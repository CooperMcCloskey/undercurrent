"""Models: quantile regression on the log2 change in a story's article_count_7d.

A model predicts three numbers per row for one horizon: the low end of the range, the median and
the high end, all as log2 change (0 = stays as it is, 1 = doubles). Everything else (turning
that into article counts, the contract, the backtest) lives outside the model.

There are two kinds of model, and the backtest handles both:

- Per-horizon (like HGBQuantile): a class with fit(X, y, weight) and predict(X) -> (n, 3),
  decorated with @register("name"). One instance is trained per horizon, on the features table.
- Joint (like neural.GRUQuantile): a class with fit(rows, seq, Y, weight) and
  predict(rows, seq) -> (n, horizons, 3), decorated with @register_joint("name"). One instance
  is trained for all horizons at once, on the features table and each story's recent daily
  history (`seq`). Y is (n, horizons) with NaN wherever that target must not be trained on.

Run one or several with --model (several are averaged, then calibrated together).
"""

import importlib
from collections.abc import Callable
from typing import Protocol

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from undercurrent.forecast import settings
from undercurrent.forecast.features import CATEGORICAL, FEATURES


class QuantileModel(Protocol):
    name: str

    def fit(self, X: pd.DataFrame, y: np.ndarray, weight: np.ndarray | None = None) -> None: ...

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """(n, 3): low, median, high, in log2 change. Columns must be non-decreasing."""
        ...


class JointModel(Protocol):
    name: str

    def fit(
        self,
        rows: pd.DataFrame,
        seq: np.ndarray,
        Y: np.ndarray,
        weight: np.ndarray | None = None,
        pretrain: tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray | None] | None = None,
    ) -> None: ...

    def predict(self, rows: pd.DataFrame, seq: np.ndarray) -> np.ndarray:
        """(n, horizons, 3): low, median, high in log2 change, for the horizons it was built with."""
        ...


MODELS: dict[str, Callable[..., QuantileModel]] = {}
JOINT_MODELS: dict[str, Callable[..., JointModel]] = {}
# Models that import heavy libraries (PyTorch) are only imported when asked for.
LAZY_MODULES = {"gru": "undercurrent.forecast.neural"}


def register(name: str) -> Callable[[type], type]:
    def add(cls: type) -> type:
        MODELS[name] = cls
        return cls

    return add


def register_joint(name: str) -> Callable[[type], type]:
    def add(cls: type) -> type:
        JOINT_MODELS[name] = cls
        return cls

    return add


def all_model_names() -> list[str]:
    return sorted({*MODELS, *JOINT_MODELS, *LAZY_MODULES})


def resolve(name: str) -> tuple[str, Callable]:
    """("horizon" | "joint", constructor) for a model name."""
    if name not in MODELS and name not in JOINT_MODELS and name in LAZY_MODULES:
        importlib.import_module(LAZY_MODULES[name])
    if name in MODELS:
        return "horizon", MODELS[name]
    if name in JOINT_MODELS:
        return "joint", JOINT_MODELS[name]
    raise ValueError(f"Unknown model {name!r}. Available: {', '.join(all_model_names())}")


def interval_quantiles(interval: float) -> tuple[float, float, float]:
    return ((1 - interval) / 2, 0.5, (1 + interval) / 2)


@register("hgb")
class HGBQuantile:
    """Three gradient boosting models (low, median, high quantile) per horizon."""

    name = "hgb"

    def __init__(
        self, interval: float, params: settings.HGBParams | None = None, seed: int = 0
    ) -> None:
        self.quantiles = interval_quantiles(interval)
        self.params = params or settings.HGBParams()
        self.seed = seed
        self.columns: list[str] = []
        self.models: list[HistGradientBoostingRegressor] = []
        self.medians = pd.Series(dtype=float)

    def fit(self, X: pd.DataFrame, y: np.ndarray, weight: np.ndarray | None = None) -> None:
        self.columns = [c for c in FEATURES if c in X.columns]
        categorical = [c for c in CATEGORICAL if c in self.columns]
        p = self.params
        self.models = []
        for q in self.quantiles:
            model = HistGradientBoostingRegressor(
                loss="quantile",
                quantile=q,
                max_iter=p.max_iter,
                learning_rate=p.learning_rate,
                max_leaf_nodes=p.max_leaf_nodes,
                min_samples_leaf=p.min_samples_leaf,
                l2_regularization=p.l2_regularization,
                categorical_features=categorical or None,
                early_stopping=False,
                random_state=self.seed,
            )
            model.fit(X[self.columns], y, sample_weight=weight)
            self.models.append(model)
        self.medians = X[self.columns].median()

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        cols = X[self.columns]
        raw = np.column_stack([m.predict(cols) for m in self.models])
        return np.sort(raw, axis=1)  # quantiles can cross a little; sort them

    def explain(self, X: pd.DataFrame, top: int = settings.TOP_FACTORS) -> list[list[dict]]:
        """Per row, the features that moved the median forecast most: the prediction minus the
        prediction with that one feature set to its training median. In log2 units."""
        cols = X[self.columns].reset_index(drop=True)
        median_model = self.models[1]
        base = median_model.predict(cols)
        contribution = np.zeros((len(cols), len(self.columns)), dtype=np.float32)
        for j, name in enumerate(self.columns):
            swapped = cols.copy()
            swapped[name] = self.medians[name]
            contribution[:, j] = base - median_model.predict(swapped)
        order = np.argsort(-np.abs(contribution), axis=1)[:, :top]
        return [
            [
                {"name": self.columns[j], "contribution": float(contribution[i, j])}
                for j in row
                if abs(contribution[i, j]) > 1e-6
            ]
            for i, row in enumerate(order)
        ]


def row_weights(rows: pd.DataFrame, mode: str = "log_volume") -> np.ndarray | None:
    """Bigger stories count more, but not linearly: small stories are noisy, not useless."""
    if mode == "none":
        return None
    return np.log2(rows["a7"].to_numpy(dtype=np.float32) + 1)


def conformal_offset(pred: np.ndarray, y: np.ndarray, interval: float) -> float:
    """How far to widen (or, if negative, narrow) every [low, high] range so that `interval` of
    held-out targets fall inside it (conformalised quantile regression). Fit on a calibration
    block that comes after the training rows and before the test rows."""
    ok = np.isfinite(y)
    n = int(ok.sum())
    if n < 20:
        return 0.0
    score = np.maximum(pred[ok, 0] - y[ok], y[ok] - pred[ok, 2])
    k = min(int(np.ceil((n + 1) * interval)), n)
    return float(np.sort(score)[k - 1])


def apply_offset(pred: np.ndarray, offset: float) -> np.ndarray:
    low = np.minimum(pred[:, 0] - offset, pred[:, 1])
    high = np.maximum(pred[:, 2] + offset, pred[:, 1])
    return np.column_stack([low, pred[:, 1], high])
