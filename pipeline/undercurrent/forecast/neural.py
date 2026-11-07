"""A neural quantile model: a GRU over each story's recent daily history, joined with the tabular
features, predicting every horizon at once.

Why it sits next to the gradient boosting (models.HGBQuantile) instead of replacing it:

- It reads the shape of the last SEQ_DAYS days directly, not 23 hand-made summaries of them.
- It learns all horizons together, so its 1-day and 14-day forecasts agree with each other.
- It can be pretrained on cheap auxiliary series (for example theme x country counts from GDELT,
  back to 2015) and then fine-tuned on stories. Trees can't do that.

The catch is data: rows from one story are near-copies, so there are far fewer independent
examples than rows. Expect it to need the pretraining, or more stories, to beat the trees, and to
be most useful averaged with them (--model hgb gru). The backtest reports each on its own.

Layout:   seq (SEQ_DAYS x channels) -> GRU ---+
          tabular features (standardised) -> Linear +-> concat -> MLP -> 3 numbers per horizon

The 3 numbers are the median and two non-negative gaps, so low <= median <= high always holds.
Trained with the pinball (quantile) loss, which is what makes them a median and a range.
"""

import copy
import warnings

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F

from undercurrent.forecast import settings
from undercurrent.forecast.features import CATEGORICAL, FEATURES, SEQ_CHANNELS
from undercurrent.forecast.models import interval_quantiles, register_joint
from undercurrent.forecast.schema import CATEGORIES

# Rough size of each channel, so the network sees values near 1 (see features.SEQ_CHANNELS).
SEQ_SCALE = np.array([8.0, 6.0, 4.0, 5.0, 15.0, 8.0, 1.0, 1.0], dtype=np.float32)
assert len(SEQ_SCALE) == len(SEQ_CHANNELS), "update SEQ_SCALE when SEQ_CHANNELS changes"


class StaticEncoder:
    """The tabular features as a float matrix a network can read: numbers standardised on the
    training rows (and clipped), missing values zeroed with a flag column for each feature that
    had any, and the category one-hot encoded."""

    def __init__(self) -> None:
        self.numeric: list[str] = []
        self.mean = np.zeros(0, dtype=np.float32)
        self.std = np.ones(0, dtype=np.float32)
        self.has_missing = np.zeros(0, dtype=bool)
        self.use_category = False
        self.dim = 0

    def fit(self, rows: pd.DataFrame) -> "StaticEncoder":
        names = [c for c in FEATURES if c not in CATEGORICAL and c in rows.columns]
        x = rows[names].to_numpy(dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns
            mean, std = np.nanmean(x, axis=0), np.nanstd(x, axis=0)
        usable = np.isfinite(mean) & (std > 1e-6)  # drop columns that are empty or constant
        self.numeric = [n for n, ok in zip(names, usable, strict=True) if ok]
        self.mean, self.std = mean[usable], std[usable]
        self.has_missing = np.isnan(x[:, usable]).any(axis=0)
        self.use_category = any(c in rows.columns for c in CATEGORICAL)
        self.dim = (
            len(self.numeric)
            + int(self.has_missing.sum())
            + (len(CATEGORIES) + 1 if self.use_category else 0)
        )
        return self

    def transform(self, rows: pd.DataFrame) -> np.ndarray:
        z = (rows[self.numeric].to_numpy(dtype=np.float32) - self.mean) / self.std
        missing = np.isnan(z)
        parts = [
            np.clip(np.nan_to_num(z, nan=0.0), -6, 6),
            missing[:, self.has_missing].astype(np.float32),
        ]
        if self.use_category:
            code = rows[CATEGORICAL[0]].to_numpy(dtype=np.float32)
            index = np.where(np.isfinite(code), code, len(CATEGORIES)).astype(int)
            parts.append(np.eye(len(CATEGORIES) + 1, dtype=np.float32)[index])
        return np.concatenate(parts, axis=1).astype(np.float32)


class Net(nn.Module):
    def __init__(
        self, n_channels: int, n_static: int, n_horizons: int, hidden: int, dropout: float
    ) -> None:
        super().__init__()
        self.n_horizons = n_horizons
        self.gru = nn.GRU(n_channels, hidden, batch_first=True)
        self.static = nn.Sequential(nn.Linear(n_static, hidden), nn.ReLU())
        self.head = nn.Sequential(
            nn.Linear(2 * hidden, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, n_horizons * 3),
        )

    def forward(self, seq: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
        _, last = self.gru(seq)
        raw = self.head(torch.cat([last[-1], self.static(static)], dim=1))
        raw = raw.view(-1, self.n_horizons, 3)
        median = raw[..., 0]
        low = median - F.softplus(raw[..., 1])
        high = median + F.softplus(raw[..., 2])
        return torch.stack([low, median, high], dim=-1)


def pinball(
    pred: torch.Tensor, y: torch.Tensor, mask: torch.Tensor, weight: torch.Tensor, q: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    """(sum of weighted quantile losses, sum of weights) over known targets."""
    err = y.unsqueeze(-1) - pred
    loss = torch.maximum(q * err, (q - 1) * err)
    w = (mask * weight.unsqueeze(1)).unsqueeze(-1).expand_as(loss)
    return (loss * w).sum(), w.sum()


def pick_device(name: str) -> torch.device:
    if name != "auto":
        return torch.device(name)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class _Data:
    """One dataset as tensors: the sequences stay float16 on the CPU and are moved in batches."""

    def __init__(self, static, seq, y, mask, weight) -> None:
        self.static, self.seq, self.y, self.mask, self.weight = static, seq, y, mask, weight

    def __len__(self) -> int:
        return len(self.y)


@register_joint("gru")
class GRUQuantile:
    name = "gru"

    def __init__(
        self,
        interval: float,
        horizons: list[int],
        params: settings.NNParams | None = None,
        seed: int = 0,
    ) -> None:
        self.quantiles = interval_quantiles(interval)
        self.horizons = list(horizons)
        self.params = params or settings.NNParams()
        self.seed = seed
        self.device = pick_device(self.params.device)
        self.static = StaticEncoder()
        self.nets: list[Net] = []
        self.val_loss: float | None = None  # best validation loss of the first network
        self._q = torch.tensor(self.quantiles, dtype=torch.float32).view(1, 1, 3)
        self._scale = torch.from_numpy(SEQ_SCALE).view(1, 1, -1)

    # -- data -------------------------------------------------------------------------------

    def _prepare(
        self, rows: pd.DataFrame, seq: np.ndarray, Y: np.ndarray, weight: np.ndarray | None
    ) -> _Data:
        known = np.isfinite(Y)
        return _Data(
            torch.from_numpy(self.static.transform(rows)),
            torch.from_numpy(seq),
            torch.from_numpy(np.nan_to_num(Y).astype(np.float32)),
            torch.from_numpy(known.astype(np.float32)),
            torch.from_numpy(
                np.ones(len(rows), dtype=np.float32)
                if weight is None
                else weight.astype(np.float32)
            ),
        )

    def _batch(self, data: _Data, idx: np.ndarray) -> tuple[torch.Tensor, ...]:
        i = torch.from_numpy(idx)
        seq = data.seq[i].float() / self._scale
        return (
            seq.to(self.device),
            data.static[i].to(self.device),
            data.y[i].to(self.device),
            data.mask[i].to(self.device),
            data.weight[i].to(self.device),
        )

    # -- training ---------------------------------------------------------------------------

    def _validation_loss(self, net: Net, data: _Data, idx: np.ndarray) -> float:
        net.eval()
        total, weight = 0.0, 0.0
        q = self._q.to(self.device)
        with torch.no_grad():
            for start in range(0, len(idx), 4096):
                seq, static, y, mask, w = self._batch(data, idx[start : start + 4096])
                s, n = pinball(net(seq, static), y, mask, w, q)
                total, weight = total + float(s), weight + float(n)
        return total / max(weight, 1e-6)

    def _train(
        self,
        net: Net,
        data: _Data,
        train_idx: np.ndarray,
        val_idx: np.ndarray,
        epochs: int,
        lr: float,
        rng: np.random.Generator,
    ) -> float | None:
        p = self.params
        optimiser = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=p.weight_decay)
        q = self._q.to(self.device)
        best, best_state, stale = float("inf"), None, 0
        for _ in range(epochs):
            net.train()
            order = rng.permutation(train_idx)
            for start in range(0, len(order), p.batch_size):
                seq, static, y, mask, w = self._batch(data, order[start : start + p.batch_size])
                s, n = pinball(net(seq, static), y, mask, w, q)
                loss = s / n.clamp_min(1e-6)
                optimiser.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(net.parameters(), 1.0)
                optimiser.step()
            if len(val_idx):
                val = self._validation_loss(net, data, val_idx)
                if val < best:
                    best, best_state, stale = val, copy.deepcopy(net.state_dict()), 0
                else:
                    stale += 1
                    if stale >= p.patience:
                        break
        if best_state is not None:
            net.load_state_dict(best_state)
            return best
        return None

    def _split(self, days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Train on earlier days, validate on the most recent ones: a random split would put
        near-identical rows of the same story on both sides."""
        everything = np.arange(len(days))
        unique = np.unique(days)
        if len(unique) < 20:
            return everything, np.array([], dtype=int)
        cut = unique[int(len(unique) * (1 - self.params.val_fraction))]
        train, val = everything[days < cut], everything[days >= cut]
        return (train, val) if len(val) >= 100 and len(train) >= 100 else (everything, val[:0])

    def fit(
        self,
        rows: pd.DataFrame,
        seq: np.ndarray,
        Y: np.ndarray,
        weight: np.ndarray | None = None,
        pretrain: tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray | None] | None = None,
    ) -> None:
        """Y: (n, horizons), NaN where a target must not be trained on (unknown, or too recent
        to be allowed). `pretrain` = (rows, seq, Y, weight) of auxiliary data to learn from first;
        the caller must already have removed anything that overlaps the days we predict."""
        p = self.params
        self.static.fit(rows)
        data = self._prepare(rows, seq, Y, weight)
        train_idx, val_idx = self._split(rows["day"].to_numpy())
        aux = self._prepare(*pretrain) if pretrain is not None and len(pretrain[0]) else None
        self.nets = []
        for k in range(p.seeds):
            torch.manual_seed(self.seed + k)
            rng = np.random.default_rng(self.seed + k)
            net = Net(
                len(SEQ_CHANNELS), self.static.dim, len(self.horizons), p.hidden, p.dropout
            ).to(self.device)
            if aux is not None and p.pretrain_epochs > 0:
                everything = np.arange(len(aux))
                self._train(net, aux, everything, everything[:0], p.pretrain_epochs,
                            p.pretrain_learning_rate, rng)  # fmt: skip
            best = self._train(net, data, train_idx, val_idx, p.epochs, p.learning_rate, rng)
            if k == 0:
                self.val_loss = best
            self.nets.append(net.eval())

    # -- prediction -------------------------------------------------------------------------

    def predict(self, rows: pd.DataFrame, seq: np.ndarray) -> np.ndarray:
        static = torch.from_numpy(self.static.transform(rows))
        seq_t = torch.from_numpy(seq)
        outputs = []
        with torch.no_grad():
            for net in self.nets:
                net.eval()
                parts = []
                for start in range(0, len(rows), 4096):
                    part = slice(start, start + 4096)
                    batch = (seq_t[part].float() / self._scale).to(self.device)
                    parts.append(net(batch, static[part].to(self.device)).cpu())
                outputs.append(torch.cat(parts) if parts else torch.zeros(0, len(self.horizons), 3))
        return torch.stack(outputs).mean(dim=0).numpy()
