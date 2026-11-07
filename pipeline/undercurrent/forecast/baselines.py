"""Baselines the model has to beat. All predict the same thing as the model: the log2 change in
a story's article_count_7d after h days (0 = stays as it is).

- persistence: no change. Hard to beat for 1-3 days.
- damped momentum: keep going at half of last week's rate.
- age x category median: the typical change for a story of this age and category. This is the
  one that shows whether the model learned more than "stories fade as they age".
"""

import numpy as np
import pandas as pd

from undercurrent.forecast.schema import CATEGORY


def persistence(rows: pd.DataFrame, h: int) -> np.ndarray:
    return np.zeros(len(rows), dtype=np.float32)


def damped_momentum(rows: pd.DataFrame, h: int, damping: float = 0.5) -> np.ndarray:
    growth = np.nan_to_num(rows["growth_7d"].to_numpy(dtype=np.float32))
    return (damping * growth * h / 7).astype(np.float32)


def _bucket(age: pd.Series) -> np.ndarray:
    return np.minimum(np.nan_to_num(age.to_numpy(dtype=np.float32)) // 3, 20).astype(int)


class AgeCategoryMedian:
    """Median target by (category, age in 3-day buckets), falling back to age alone, then to all."""

    def __init__(self) -> None:
        self.by_both = pd.Series(dtype=float)
        self.by_age = pd.Series(dtype=float)
        self.default = 0.0

    def _category(self, rows: pd.DataFrame) -> np.ndarray:
        return (
            rows[CATEGORY].fillna(-1).to_numpy(dtype=np.float32)
            if CATEGORY in rows
            else -np.ones(len(rows))
        )

    def fit(self, rows: pd.DataFrame, h: int) -> "AgeCategoryMedian":
        frame = pd.DataFrame(
            {
                "cat": self._category(rows),
                "age": _bucket(rows["age_days"]),
                "y": rows[f"y_{h}"].to_numpy(),
            }
        ).dropna(subset=["y"])
        if len(frame):
            self.by_both = frame.groupby(["cat", "age"])["y"].median()
            self.by_age = frame.groupby("age")["y"].median()
            self.default = float(frame["y"].median())
        return self

    def predict(self, rows: pd.DataFrame) -> np.ndarray:
        age = _bucket(rows["age_days"])
        both = self.by_both.reindex(pd.MultiIndex.from_arrays([self._category(rows), age]))
        by_age = self.by_age.reindex(age)
        out = both.to_numpy()
        out = np.where(np.isnan(out), by_age.to_numpy(), out)
        return np.where(np.isnan(out), self.default, out).astype(np.float32)
