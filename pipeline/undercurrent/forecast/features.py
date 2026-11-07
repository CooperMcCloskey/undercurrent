"""Point-in-time features on (stories x days) matrices.

Every feature at day t uses only data from day t or earlier (a Wikipedia pageview feature only
up to t - settings.WIKI_VIEWS_LAG_DAYS). tests/test_forecast_core.py checks this by changing the
data after t and asserting that the features at t do not move.

To add a feature, write a function that takes a Context and returns a (stories x days) float32
array, and decorate it with @feature("name") in this file, above the FEATURES line at the bottom.
Nothing else needs to change: the dataset builder, the models and the backtest pick it up through
FEATURES. Use NaN for "unknown"; gradient boosting handles it. Add a test next to the others in
tests/test_forecast_core.py (the point-in-time test covers every registered feature).
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from undercurrent.forecast import settings


@dataclass
class Dense:
    """A chunk of stories as (stories x days) matrices over consecutive calendar days."""

    story_ids: np.ndarray  # (S,)
    days: pd.DatetimeIndex  # (D,)
    first_idx: np.ndarray  # (S,) column of the day each story first appeared
    counts: np.ndarray  # (S, D) articles per day, 0 when none
    metrics: dict[str, np.ndarray]  # (S, D) per-day extras, NaN = unknown
    cat_code: np.ndarray  # (S, D) category index carried forward in time, NaN = unknown
    totals: np.ndarray  # (D,) articles per day over all stories, NaN = unknown


# ---- array helpers (all work along the last axis, which is time) -------------------------------


def rolling_sum(x: np.ndarray, w: int) -> np.ndarray:
    """Sum over the w days ending at each day (fewer at the start). `x` must not contain NaN."""
    cs = np.cumsum(x, axis=-1, dtype=np.float64)
    out = cs.copy()
    out[..., w:] -= cs[..., :-w]
    return out.astype(np.float32)


def shift(x: np.ndarray, k: int) -> np.ndarray:
    """The value from k days earlier (NaN where there is none)."""
    out = np.full(x.shape, np.nan, dtype=np.float32)
    if k == 0:
        return x.astype(np.float32)
    if k < x.shape[-1]:
        out[..., k:] = x[..., :-k]
    return out


def lead(x: np.ndarray, h: int) -> np.ndarray:
    """The value h days later (NaN where it is not known yet). Used only for targets."""
    out = np.full(x.shape, np.nan, dtype=np.float32)
    if h < x.shape[-1]:
        out[..., :-h] = x[..., h:]
    return out


def ffill(x: np.ndarray) -> np.ndarray:
    """Carry the last known value forward in time."""
    idx = np.where(np.isfinite(x), np.arange(x.shape[-1]), 0)
    np.maximum.accumulate(idx, axis=-1, out=idx)
    return np.take_along_axis(x, idx, axis=-1)


def rolling_mean(x: np.ndarray, w: int, weights: np.ndarray | None = None) -> np.ndarray:
    """Mean of the known (non-NaN) values over the w days ending at each day, optionally weighted.
    NaN where there are none."""
    valid = np.isfinite(x)
    wt = np.ones_like(x, dtype=np.float32) if weights is None else weights
    num = rolling_sum(np.where(valid, x * wt, 0.0), w)
    den = rolling_sum(np.where(valid, wt, 0.0), w)
    return np.divide(num, den, out=np.full(num.shape, np.nan, dtype=np.float32), where=den > 0)


# ---- the feature registry ----------------------------------------------------------------------


class Context:
    """Shared arrays for one chunk of stories, so features don't recompute them."""

    def __init__(self, d: Dense) -> None:
        self.d = d
        self.shape = d.counts.shape
        self.t = np.arange(self.shape[1])[None, :]
        self.age = (self.t - d.first_idx[:, None]).astype(np.float32)
        self.a1 = d.counts
        self.a7 = rolling_sum(d.counts, 7)
        self._memo: dict[str, np.ndarray] = {}

    def memo(self, key: str, fn: Callable[[], np.ndarray]) -> np.ndarray:
        if key not in self._memo:
            self._memo[key] = fn()
        return self._memo[key]

    def nan(self) -> np.ndarray:
        return np.full(self.shape, np.nan, dtype=np.float32)


_REGISTRY: dict[str, Callable[[Context], np.ndarray]] = {}


def feature(
    name: str,
) -> Callable[[Callable[[Context], np.ndarray]], Callable[[Context], np.ndarray]]:
    def register(fn: Callable[[Context], np.ndarray]) -> Callable[[Context], np.ndarray]:
        _REGISTRY[name] = fn
        return fn

    return register


def _growth(a7: np.ndarray) -> np.ndarray:
    return np.log2((a7 + 1) / (shift(a7, 7) + 1))


@feature("log_a7")
def _log_a7(c: Context) -> np.ndarray:
    return np.log2(c.a7 + 1)


@feature("log_a1")
def _log_a1(c: Context) -> np.ndarray:
    return np.log2(c.a1 + 1)


@feature("today_vs_week")
def _today_vs_week(c: Context) -> np.ndarray:
    return np.log2((c.a1 + 1) / (c.a7 / 7 + 1))


@feature("growth_7d")
def _growth_7d(c: Context) -> np.ndarray:
    return _growth(c.a7)


@feature("accel")
def _accel(c: Context) -> np.ndarray:
    g = _growth(c.a7)
    return g - shift(g, 7)


@feature("age_days")
def _age(c: Context) -> np.ndarray:
    return c.age


def _cummax(c: Context) -> np.ndarray:
    return c.memo("cummax", lambda: np.maximum.accumulate(c.a7, axis=1))


@feature("log_peak")
def _log_peak(c: Context) -> np.ndarray:
    return np.log2(_cummax(c) + 1)


@feature("peak_ratio")
def _peak_ratio(c: Context) -> np.ndarray:
    return c.a7 / np.maximum(_cummax(c), 1)


@feature("days_since_peak")
def _days_since_peak(c: Context) -> np.ndarray:
    last_peak = np.where(c.a7 >= _cummax(c), c.t, -1)
    np.maximum.accumulate(last_peak, axis=1, out=last_peak)
    return (c.t - last_peak).astype(np.float32)


@feature("active_share")
def _active_share(c: Context) -> np.ndarray:
    active_days = np.cumsum(c.a1 > 0, axis=1)
    return np.minimum(active_days / np.maximum(c.age + 1, 1), 1.0).astype(np.float32)


@feature("domains_mean7")
def _domains(c: Context) -> np.ndarray:
    return rolling_mean(c.d.metrics["n_domains"], 7)


@feature("countries_mean7")
def _countries(c: Context) -> np.ndarray:
    return rolling_mean(c.d.metrics["n_countries"], 7)


def _tone7(c: Context) -> np.ndarray:
    return c.memo("tone7", lambda: rolling_mean(c.d.metrics["tone_mean"], 7, weights=c.a1))


@feature("tone_mean7")
def _tone_mean7(c: Context) -> np.ndarray:
    return _tone7(c)


@feature("tone_delta")
def _tone_delta(c: Context) -> np.ndarray:
    return _tone7(c) - shift(_tone7(c), 7)


def _totals7(c: Context) -> np.ndarray | None:
    tot = c.d.totals
    if not np.isfinite(tot).any():
        return None
    return rolling_sum(np.nan_to_num(tot), 7)


@feature("share_of_day")
def _share_of_day(c: Context) -> np.ndarray:
    tot7 = _totals7(c)
    if tot7 is None:
        return c.nan()
    return c.a7 / np.where(tot7 > 0, tot7, np.nan)[None, :]


@feature("log_total7")
def _log_total7(c: Context) -> np.ndarray:
    tot7 = _totals7(c)
    if tot7 is None:
        return c.nan()
    return np.broadcast_to(np.log2(tot7 + 1)[None, :], c.shape)


@feature("dow")
def _dow(c: Context) -> np.ndarray:
    dow = c.d.days.day_of_week.to_numpy().astype(np.float32)
    return np.broadcast_to(dow[None, :], c.shape)


def _wiki_week(c: Context, col: str, lag: int) -> np.ndarray:
    """Seven-day total of a Wikipedia series, lagged; NaN unless all seven days are known."""
    x = shift(c.d.metrics[col], lag)
    known = np.isfinite(x)
    total = rolling_sum(np.where(known, x, 0.0), 7)
    full = rolling_sum(known.astype(np.float32), 7) >= 7
    return np.where(full, total, np.nan).astype(np.float32)


def _views7(c: Context) -> np.ndarray:
    return c.memo("views7", lambda: _wiki_week(c, "wiki_views", settings.WIKI_VIEWS_LAG_DAYS))


@feature("wiki_log_views7")
def _wiki_log_views7(c: Context) -> np.ndarray:
    return np.log2(_views7(c) + 1)


@feature("wiki_growth_7d")
def _wiki_growth(c: Context) -> np.ndarray:
    return _growth(_views7(c))


@feature("wiki_edit_growth_7d")
def _wiki_edit_growth(c: Context) -> np.ndarray:
    return _growth(_wiki_week(c, "wiki_edits", settings.WIKI_EDITS_LAG_DAYS))


@feature("wiki_non_en_share")
def _wiki_non_en(c: Context) -> np.ndarray:
    en = _wiki_week(c, "wiki_views_en", settings.WIKI_VIEWS_LAG_DAYS)
    views = _views7(c)
    return 1.0 - np.divide(
        en, views, out=np.full(views.shape, np.nan, dtype=np.float32), where=views > 0
    )


@feature("wiki_views_per_article")
def _wiki_per_article(c: Context) -> np.ndarray:
    return np.log2((_views7(c) + 1) / (c.a7 + 1))


@feature("category")
def _category(c: Context) -> np.ndarray:
    return c.d.cat_code


FEATURES: list[str] = list(_REGISTRY)
CATEGORICAL: list[str] = ["category"]


def compute_features(d: Dense) -> dict[str, np.ndarray]:
    """All registered features for a chunk of stories: name -> (stories x days) float32."""
    ctx = Context(d)
    out = {name: np.asarray(fn(ctx), dtype=np.float32) for name, fn in _REGISTRY.items()}
    out["_a7"] = ctx.a7
    return out
