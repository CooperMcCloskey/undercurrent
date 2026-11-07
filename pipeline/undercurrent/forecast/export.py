"""Predictions -> the hand-off files (docs/WORKPLAN.md section 3.7) and the contract objects.

forecasts.parquet has one row per (story, day, horizon):

    story_id, day, days, articles_7d, low, high, model_version, factors, actual_articles_7d,
    source ("walkforward" = out-of-sample, safe to show on a replay; "live" = latest day), fold

B's exporter should not re-implement the grouping: call `load_forecasts(path)` and put the
objects on StorySummary.forecast / StorySummary.outcome, and `meta_horizons(path)` on Meta.horizons.
"""

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from undercurrent import contracts
from undercurrent.forecast.schema import DAY, STORY

COLUMNS = [
    STORY,
    DAY,
    "days",
    "articles_7d",
    "low",
    "high",
    "model_version",
    "factors",
    "actual_articles_7d",
    "source",
    "fold",
]


def _articles(a7: np.ndarray, log_change: np.ndarray) -> np.ndarray:
    """Back from log2 change to a count: a7 -> (a7 + 1) * 2^change - 1, never below zero."""
    return np.round(np.maximum((a7 + 1) * np.exp2(log_change) - 1, 0.0), 2)


def forecasts_frame(
    walkforward: pd.DataFrame, live: pd.DataFrame, model_version: str
) -> pd.DataFrame:
    """Combine walk-forward and live predictions (live wins for the latest day)."""
    parts = [p for p in (walkforward, live) if len(p)]
    if not parts:
        return pd.DataFrame(columns=COLUMNS)
    preds = pd.concat(parts, ignore_index=True)
    preds = preds.drop_duplicates([STORY, DAY, "days"], keep="last")
    a7 = preds["a7"].to_numpy(dtype=np.float64)
    out = pd.DataFrame(
        {
            STORY: preds[STORY].to_numpy(),
            DAY: preds[DAY].dt.date,
            "days": preds["days"].astype("int16"),
            "articles_7d": _articles(a7, preds["med"].to_numpy()),
            "low": _articles(a7, preds["lo"].to_numpy()),
            "high": _articles(a7, preds["hi"].to_numpy()),
            "model_version": model_version,
            "factors": preds["factors"].to_numpy(),
            "actual_articles_7d": preds["actual"].round().astype("Int64"),
            "source": preds["source"].to_numpy(),
            "fold": preds["fold"].to_numpy(),
        }
    )
    return out.sort_values([DAY, STORY, "days"]).reset_index(drop=True)[COLUMNS]


def write_outputs(
    out_dir: Path,
    forecasts: pd.DataFrame,
    metrics: contracts.ModelMetrics | None,
    report: dict,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "forecasts": out_dir / "forecasts.parquet",
        "metrics": out_dir / "model_metrics.json",
        "report": out_dir / "forecast_report.json",
    }
    forecasts.to_parquet(paths["forecasts"], index=False)
    if metrics is not None:
        paths["metrics"].write_text(metrics.model_dump_json(indent=2) + "\n")
    paths["report"].write_text(json.dumps(report, indent=2, default=str) + "\n")
    return paths


def load_forecasts(
    path: Path,
) -> dict[tuple[str, dt.date], tuple[contracts.Forecast, contracts.Outcome | None]]:
    """What B's exporter needs: for each (story_id, day), the Forecast and, for horizons that
    have already happened, the Outcome. Validated against contracts.py."""
    df = pd.read_parquet(path)
    result = {}
    for (story, day), group in df.groupby([STORY, DAY], sort=False):
        group = group.sort_values("days")
        horizons = [
            contracts.HorizonForecast(
                days=int(r.days),
                articles_7d=float(r.articles_7d),
                low=float(r.low),
                high=float(r.high),
                factors=[
                    contracts.Factor(**f) for f in (r.factors if r.factors is not None else [])
                ],
            )
            for r in group.itertuples()
        ]
        known = group[group["actual_articles_7d"].notna()]
        outcome = (
            contracts.Outcome(
                horizons=[
                    contracts.HorizonOutcome(
                        days=int(r.days), articles_7d=int(r.actual_articles_7d)
                    )
                    for r in known.itertuples()
                ]
            )
            if len(known)
            else None
        )
        forecast = contracts.Forecast(
            model_version=str(group["model_version"].iloc[0]), horizons=horizons
        )
        result[(str(story), day)] = (forecast, outcome)
    return result


def meta_horizons(path: Path) -> list[int]:
    """The horizons to put in Meta.horizons."""
    return sorted(int(h) for h in pd.read_parquet(path, columns=["days"])["days"].unique())
