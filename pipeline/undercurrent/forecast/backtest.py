"""Walk-forward backtest, and the forecasts it produces.

Why walk-forward: the globe lets you scrub back to day t and see what the model said then. That
is only honest if the prediction for day t came from a model that had not seen anything after t.
So the data is cut into time windows, and the predictions for each window come from a model
trained only on rows that finished before it. Predictions for the latest day come from a
"live" model trained on everything that has a known outcome.

For one window and one horizon h (day = the day t a row is made on; its target is known at t + h):

    train        t + h < cal_start                      (the targets end before calibration)
    calibration  cal_start <= t,  t + h < test_start    (widens or narrows the ranges)
    test         test_start <= t <= test_end

so nothing the model trains or calibrates on overlaps the days it is scored on. The last window
("final", FINAL_TEST_DAYS long) is the one reported in ModelMetrics: never tune on it.
"""

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from undercurrent import contracts
from undercurrent.forecast import settings
from undercurrent.forecast.baselines import AgeCategoryMedian, damped_momentum
from undercurrent.forecast.models import (
    MODELS,
    apply_offset,
    conformal_offset,
    row_weights,
)
from undercurrent.forecast.schema import CATEGORIES, DAY, STORY

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fold:
    name: str
    cal_start: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def make_folds(
    first_day: pd.Timestamp,
    last_day: pd.Timestamp,
    horizons: list[int],
    *,
    final_test_days: int = settings.FINAL_TEST_DAYS,
    fold_days: int = settings.FOLD_DAYS,
    n_folds: int = settings.N_FOLDS,
    cal_days: int = settings.CALIBRATION_DAYS,
    min_train_days: int = settings.MIN_TRAIN_DAYS,
) -> list[Fold]:
    """Test windows going backwards from the end: the final one first, then earlier quarters.
    Windows without enough earlier history to train on are dropped."""
    folds: list[Fold] = []
    test_end, length = last_day, final_test_days
    for k in range(n_folds + 1):
        test_start = test_end - pd.Timedelta(days=length - 1)
        cal_start = test_start - pd.Timedelta(days=cal_days + max(horizons))
        if (cal_start - first_day).days < min_train_days:
            break
        folds.append(Fold("final" if k == 0 else f"fold-{k}", cal_start, test_start, test_end))
        test_end, length = test_start - pd.Timedelta(days=1), fold_days
    return folds


def live_fold(last_day: pd.Timestamp, horizons: list[int], cal_days: int) -> Fold:
    """A window that starts the day after the data ends: trains on everything with a known outcome."""
    test_start = last_day + pd.Timedelta(days=1)
    cal_start = test_start - pd.Timedelta(days=cal_days + max(horizons))
    return Fold("live", cal_start, test_start, test_start)


def run_fold(
    rows: pd.DataFrame,
    fold: Fold,
    horizons: list[int],
    *,
    model_name: str,
    interval: float,
    params: settings.HGBParams,
    predict_mask: pd.Series,
    source: str,
    explain_from: pd.Timestamp | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Train per horizon on rows that finished before the fold, calibrate, predict `predict_mask`.
    One row per (story, day, horizon); horizons without enough data are skipped."""
    out: list[pd.DataFrame] = []
    day = rows[DAY]
    for h in horizons:
        delta = pd.Timedelta(days=h)
        y = rows[f"y_{h}"]
        known = y.notna()
        train = known & (day + delta < fold.cal_start)
        cal = known & (day >= fold.cal_start) & (day + delta < fold.test_start)
        if train.sum() < settings.MIN_TRAIN_ROWS or cal.sum() < settings.MIN_CAL_ROWS:
            log.warning(
                "%s h=%d: too few rows (train %d, cal %d); skipped",
                fold.name,
                h,
                train.sum(),
                cal.sum(),
            )
            continue
        if not predict_mask.any():
            continue
        model = MODELS[model_name](interval, params, seed)
        model.fit(rows.loc[train], y[train].to_numpy(), row_weights(rows.loc[train]))
        cal_pred = model.predict(rows.loc[cal])
        offset = conformal_offset(cal_pred, y[cal].to_numpy(), interval)

        target = rows.loc[predict_mask]
        pred = apply_offset(model.predict(target), offset)
        age_cat = AgeCategoryMedian().fit(rows.loc[train], h)
        frame = pd.DataFrame(
            {
                STORY: target[STORY].to_numpy(),
                DAY: target[DAY].to_numpy(),
                "days": h,
                "lo": pred[:, 0],
                "med": pred[:, 1],
                "hi": pred[:, 2],
                "y": target[f"y_{h}"].to_numpy(),
                "a7": target["a7"].to_numpy(),
                "actual": target[f"actual_{h}"].to_numpy(),
                "momentum": damped_momentum(target, h),
                "agecat": age_cat.predict(target),
                "category": target["category"].to_numpy(),
                "age_days": target["age_days"].to_numpy(),
                "offset": offset,
                "fold": fold.name,
                "source": source,
            }
        )
        frame["factors"] = [[] for _ in range(len(frame))]
        if explain_from is not None and hasattr(model, "explain"):
            recent = (target[DAY] >= explain_from).to_numpy()
            if recent.any():
                explained = model.explain(target.loc[recent])
                idx = np.flatnonzero(recent)
                for i, factors in zip(idx, explained, strict=True):
                    frame.at[i, "factors"] = factors
        out.append(frame)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ---- scoring -----------------------------------------------------------------------------------


def horizon_metrics(preds: pd.DataFrame) -> list[contracts.HorizonMetrics]:
    """Typical miss (mean |log2 error|) for the model and for persistence, and range coverage,
    on rows whose outcome is known. Same definitions as contracts.HorizonMetrics."""
    scored = preds[preds["y"].notna()]
    metrics = []
    for h, p in scored.groupby("days"):
        y = p["y"].to_numpy()
        metrics.append(
            contracts.HorizonMetrics(
                days=int(h),
                n_test=len(p),
                mae_log2=float(np.abs(p["med"].to_numpy() - y).mean()),
                baseline_mae_log2=float(np.abs(y).mean()),
                coverage=float(((p["lo"].to_numpy() <= y) & (y <= p["hi"].to_numpy())).mean()),
            )
        )
    return sorted(metrics, key=lambda m: m.days)


def baseline_table(preds: pd.DataFrame) -> dict[int, dict[str, float]]:
    """MAE per horizon for the model and each baseline, side by side."""
    table = {}
    for h, p in preds[preds["y"].notna()].groupby("days"):
        y = p["y"].to_numpy()
        table[int(h)] = {
            "model": float(np.abs(p["med"].to_numpy() - y).mean()),
            "persistence": float(np.abs(y).mean()),
            "momentum": float(np.abs(p["momentum"].to_numpy() - y).mean()),
            "age_category": float(np.abs(p["agecat"].to_numpy() - y).mean()),
        }
    return table


def bootstrap_vs_persistence(preds: pd.DataFrame, n_boot: int = 200, seed: int = 0) -> dict:
    """Model MAE minus persistence MAE (negative = the model is better), with a 95% interval
    from resampling whole stories, because rows of one story are near-copies of each other."""
    rng = np.random.default_rng(seed)
    result = {}
    for h, p in preds[preds["y"].notna()].groupby("days"):
        diff = np.abs(p["med"].to_numpy() - p["y"].to_numpy()) - np.abs(p["y"].to_numpy())
        grouped = (
            pd.DataFrame({"s": p[STORY].to_numpy(), "d": diff})
            .groupby("s")["d"]
            .agg(["sum", "count"])
        )
        total, count = grouped["sum"].to_numpy(), grouped["count"].to_numpy()
        pick = rng.integers(0, len(grouped), size=(n_boot, len(grouped)))
        means = total[pick].sum(axis=1) / count[pick].sum(axis=1)
        result[int(h)] = {
            "mean": float(total.sum() / count.sum()),
            "ci_low": float(np.percentile(means, 2.5)),
            "ci_high": float(np.percentile(means, 97.5)),
            "stories": int(len(grouped)),
        }
    return result


def slice_report(preds: pd.DataFrame) -> dict:
    """Model vs persistence MAE by category, by story size and by story age, per horizon."""
    scored = preds[preds["y"].notna()].copy()
    if scored.empty:
        return {}
    names = {float(i): c for i, c in enumerate(CATEGORIES)}
    scored["category_name"] = scored["category"].map(names).fillna("unknown")
    # Rank first so ties can't collapse the three size bands.
    scored["size"] = pd.qcut(
        scored["a7"].rank(method="first"), 3, labels=["small", "medium", "large"]
    )
    scored["age"] = pd.cut(
        scored["age_days"], [-1, 7, 21, np.inf], labels=["<1 week", "1-3 weeks", ">3 weeks"]
    )
    scored["err_model"] = (scored["med"] - scored["y"]).abs()
    scored["err_persistence"] = scored["y"].abs()
    report: dict = {}
    for key, column in [("category", "category_name"), ("size", "size"), ("age", "age")]:
        grouped = scored.groupby(["days", column], observed=True)[["err_model", "err_persistence"]]
        mean, count = grouped.mean(), grouped.size()
        report[key] = {
            f"h={h}": {
                str(value): {
                    "n": int(count[(h, value)]),
                    "model": float(mean.loc[(h, value), "err_model"]),
                    "persistence": float(mean.loc[(h, value), "err_persistence"]),
                }
                for (hh, value) in mean.index
                if hh == h
            }
            for h in sorted({h for h, _ in mean.index})
        }
    return report


# ---- orchestration -----------------------------------------------------------------------------


@dataclass
class BacktestResult:
    folds: list[Fold]
    walkforward: pd.DataFrame  # out-of-sample predictions for every test window
    live: pd.DataFrame  # predictions for the latest day, from a model trained on everything
    metrics: contracts.ModelMetrics | None  # from the final window only
    report: dict
    model_version: str


def run_backtest(
    rows: pd.DataFrame,
    horizons: list[int],
    *,
    model_name: str = "hgb",
    interval: float = 0.8,
    params: settings.HGBParams | None = None,
    n_folds: int = settings.N_FOLDS,
    final_test_days: int = settings.FINAL_TEST_DAYS,
    fold_days: int = settings.FOLD_DAYS,
    cal_days: int = settings.CALIBRATION_DAYS,
    min_train_days: int = settings.MIN_TRAIN_DAYS,
    factor_days: int = settings.FACTOR_DAYS,
    seed: int = 0,
) -> BacktestResult:
    params = params or settings.HGBParams()
    first_day, last_day = rows[DAY].min(), rows[DAY].max()
    folds = make_folds(
        first_day,
        last_day,
        horizons,
        final_test_days=final_test_days,
        fold_days=fold_days,
        n_folds=n_folds,
        cal_days=cal_days,
        min_train_days=min_train_days,
    )
    if not folds:
        raise ValueError(
            f"Not enough history to backtest: {(last_day - first_day).days} days of rows, but the "
            f"final test period ({final_test_days} days) plus calibration and {min_train_days} "
            "days of training are needed. Use more data or lower --final-test-days."
        )
    explain_from = last_day - pd.Timedelta(days=factor_days - 1)
    frames = []
    for fold in folds:
        in_window = (rows[DAY] >= fold.test_start) & (rows[DAY] <= fold.test_end)
        log.info("fold %s: test %s..%s", fold.name, fold.test_start.date(), fold.test_end.date())
        frames.append(
            run_fold(
                rows,
                fold,
                horizons,
                model_name=model_name,
                interval=interval,
                params=params,
                predict_mask=in_window,
                source="walkforward",
                explain_from=explain_from if fold.name == "final" else None,
                seed=seed,
            )
        )
    walk = (
        pd.concat([f for f in frames if len(f)], ignore_index=True)
        if any(len(f) for f in frames)
        else pd.DataFrame()
    )

    live = run_fold(
        rows,
        live_fold(last_day, horizons, cal_days),
        horizons,
        model_name=model_name,
        interval=interval,
        params=params,
        predict_mask=rows[DAY] == last_day,
        source="live",
        explain_from=last_day,
        seed=seed,
    )

    final = walk[walk["fold"] == "final"] if len(walk) else walk
    metrics = None
    report: dict = {
        "model": model_name,
        "interval": interval,
        "horizons": horizons,
        "folds": [],
    }
    for fold in folds:
        part = walk[walk["fold"] == fold.name] if len(walk) else walk
        report["folds"].append(
            {
                "name": fold.name,
                "test_start": str(fold.test_start.date()),
                "test_end": str(fold.test_end.date()),
                "metrics": [m.model_dump() for m in horizon_metrics(part)] if len(part) else [],
            }
        )
    model_version = f"{model_name}-{last_day:%Y%m%d}"
    if len(final) and final["y"].notna().any():
        scored = final[final["y"].notna()]
        metrics = contracts.ModelMetrics(
            model_version=model_version,
            test_start=scored[DAY].min().date(),
            test_end=scored[DAY].max().date(),
            horizons=horizon_metrics(final),
        )
        table = baseline_table(final)
        boot = bootstrap_vs_persistence(final, seed=seed)
        report["final"] = {
            "metrics": [m.model_dump(mode="json") for m in metrics.horizons],
            "mae_by_method": table,
            "model_minus_persistence": boot,
            "beats_persistence": {h: bool(b["ci_high"] < 0) for h, b in boot.items()},
            "slices": slice_report(final),
        }
    return BacktestResult(folds, walk, live, metrics, report, model_version)
