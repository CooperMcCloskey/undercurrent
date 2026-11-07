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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from undercurrent import contracts
from undercurrent.forecast import settings
from undercurrent.forecast.baselines import AgeCategoryMedian, damped_momentum
from undercurrent.forecast.models import (
    apply_offset,
    conformal_offset,
    resolve,
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


def _blend(parts: list[np.ndarray], weights: np.ndarray) -> np.ndarray:
    """Weighted average of (n, 3) predictions. Averaging keeps low <= median <= high."""
    return sum(w * p for w, p in zip(weights, parts, strict=True))


def _auxiliary(
    aux: tuple[pd.DataFrame, np.ndarray], usable: list[int], fold: Fold
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray | None] | None:
    """Auxiliary data to pretrain a joint model on, cut by the same rule as the real training
    rows: a target is only used if its outcome was known before calibration started. Without
    this, pretraining would quietly let the model see the days it is scored on."""
    aux_rows, aux_seq = aux
    Y = np.column_stack(
        [
            np.where(
                aux_rows[f"y_{h}"].notna()
                & (aux_rows[DAY] + pd.Timedelta(days=h) < fold.cal_start),
                aux_rows[f"y_{h}"],
                np.nan,
            )
            for h in usable
        ]
    )
    keep = np.isfinite(Y).any(axis=1)
    if not keep.any():
        return None
    chosen = aux_rows.loc[keep]
    return chosen, aux_seq[keep], Y[keep], row_weights(chosen)


def run_fold(
    rows: pd.DataFrame,
    fold: Fold,
    horizons: list[int],
    *,
    models: Sequence[str],
    interval: float,
    model_params: Mapping[str, object],
    predict_mask: pd.Series,
    source: str,
    seq: np.ndarray | None = None,
    aux: tuple[pd.DataFrame, np.ndarray] | None = None,
    blend: Sequence[float] | None = None,
    explain_from: pd.Timestamp | None = None,
    seed: int = 0,
) -> pd.DataFrame:
    """Train on rows that finished before the fold, calibrate, predict `predict_mask`.

    `models` are trained separately and averaged (equal weights unless `blend` says otherwise);
    the blend is then calibrated as one. One row per (story, day, horizon) comes out, with each
    model's own median in `med_<name>` so the report can compare them. Horizons without enough
    rows are skipped. `seq` (row for row with `rows`) is for models that read daily history, and
    `aux` is optional extra data to pretrain them on."""
    day = rows[DAY]
    masks: dict[int, tuple[pd.Series, pd.Series]] = {}
    for h in horizons:
        delta = pd.Timedelta(days=h)
        known = rows[f"y_{h}"].notna()
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
        masks[h] = (train, cal)
    if not masks or not predict_mask.any():
        return pd.DataFrame()
    usable = list(masks)
    target = rows.loc[predict_mask]
    target_seq = seq[predict_mask.to_numpy()] if seq is not None else None

    cal_preds: dict[str, dict[int, np.ndarray]] = {}
    tgt_preds: dict[str, dict[int, np.ndarray]] = {}
    fitted: dict[str, dict[int, object]] = {}
    for name in models:
        kind, factory = resolve(name)
        cal_preds[name], tgt_preds[name], fitted[name] = {}, {}, {}
        if kind == "horizon":
            for h in usable:
                train, cal = masks[h]
                model = factory(interval, model_params.get(name), seed)
                fit_rows = rows.loc[train]
                model.fit(fit_rows, rows.loc[train, f"y_{h}"].to_numpy(), row_weights(fit_rows))
                cal_preds[name][h] = model.predict(rows.loc[cal])
                tgt_preds[name][h] = model.predict(target)
                fitted[name][h] = model
            continue
        if seq is None:
            raise ValueError(
                f"Model {name!r} reads each story's daily history: build the dataset with "
                "build_dataset(..., seq_len=...) and pass seq."
            )
        in_train = np.zeros(len(rows), dtype=bool)
        in_cal = np.zeros(len(rows), dtype=bool)
        for h in usable:
            in_train |= masks[h][0].to_numpy()
            in_cal |= masks[h][1].to_numpy()
        # One network for all horizons: each target is NaN where that horizon may not train on it.
        Y = np.column_stack(
            [
                np.where(
                    masks[h][0].to_numpy()[in_train], rows[f"y_{h}"].to_numpy()[in_train], np.nan
                )
                for h in usable
            ]
        )
        fit_rows = rows.loc[in_train]
        model = factory(interval, usable, model_params.get(name), seed)
        pretrain = _auxiliary(aux, usable, fold) if aux is not None else None
        model.fit(fit_rows, seq[in_train], Y, row_weights(fit_rows), pretrain)
        cal_all = model.predict(rows.loc[in_cal], seq[in_cal])
        tgt_all = model.predict(target, target_seq)
        for j, h in enumerate(usable):
            cal_preds[name][h] = cal_all[masks[h][1].to_numpy()[in_cal], j]
            tgt_preds[name][h] = tgt_all[:, j]

    weights = np.ones(len(models)) if blend is None else np.asarray(blend, dtype=float)
    weights = weights / weights.sum()
    out: list[pd.DataFrame] = []
    for h in usable:
        train, cal = masks[h]
        cal_blend = _blend([cal_preds[n][h] for n in models], weights)
        tgt_blend = _blend([tgt_preds[n][h] for n in models], weights)
        offset = conformal_offset(cal_blend, rows.loc[cal, f"y_{h}"].to_numpy(), interval)
        pred = apply_offset(tgt_blend, offset)
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
        for n in models:
            frame[f"med_{n}"] = tgt_preds[n][h][:, 1]
        frame["factors"] = [[] for _ in range(len(frame))]
        explainer = next(
            (m for n in models if hasattr(m := fitted[n].get(h), "explain")), None
        )  # only per-horizon models can explain themselves
        if explain_from is not None and explainer is not None:
            recent = (target[DAY] >= explain_from).to_numpy()
            if recent.any():
                explained = explainer.explain(target.loc[recent])
                for i, factors in zip(np.flatnonzero(recent), explained, strict=True):
                    frame.at[i, "factors"] = factors
        out.append(frame)
    return pd.concat(out, ignore_index=True)


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
    """MAE per horizon for the model (the calibrated blend), each baseline, and each model on its
    own (uncalibrated median), side by side. This is how you tell whether a model earns its place."""
    table = {}
    for h, p in preds[preds["y"].notna()].groupby("days"):
        y = p["y"].to_numpy()
        table[int(h)] = {
            "model": float(np.abs(p["med"].to_numpy() - y).mean()),
            "persistence": float(np.abs(y).mean()),
            "momentum": float(np.abs(p["momentum"].to_numpy() - y).mean()),
            "age_category": float(np.abs(p["agecat"].to_numpy() - y).mean()),
        }
        for column in p.columns:
            if column.startswith("med_"):
                table[int(h)][column[4:]] = float(np.abs(p[column].to_numpy() - y).mean())
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
    models: Sequence[str] = ("hgb",),
    interval: float = 0.8,
    model_params: Mapping[str, object] | None = None,
    seq: np.ndarray | None = None,
    aux: tuple[pd.DataFrame, np.ndarray] | None = None,
    blend: Sequence[float] | None = None,
    n_folds: int = settings.N_FOLDS,
    final_test_days: int = settings.FINAL_TEST_DAYS,
    fold_days: int = settings.FOLD_DAYS,
    cal_days: int = settings.CALIBRATION_DAYS,
    min_train_days: int = settings.MIN_TRAIN_DAYS,
    factor_days: int = settings.FACTOR_DAYS,
    seed: int = 0,
) -> BacktestResult:
    """`models`: one or more names (see models.all_model_names()); several are averaged.
    `model_params`: {model name: its params object}. `seq`: each row's recent daily history
    (from build_dataset(seq_len=...)), for models that read it. `aux`: (rows, seq) to pretrain
    them on, cut by the same time rules as the real training rows."""
    model_params = model_params or {}
    if seq is not None and len(seq) != len(rows):
        raise ValueError("seq must have one entry per row of `rows`")
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
                models=models,
                interval=interval,
                model_params=model_params,
                predict_mask=in_window,
                source="walkforward",
                seq=seq,
                aux=aux,
                blend=blend,
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
        models=models,
        interval=interval,
        model_params=model_params,
        predict_mask=rows[DAY] == last_day,
        source="live",
        seq=seq,
        aux=aux,
        blend=blend,
        explain_from=last_day,
        seed=seed,
    )

    final = walk[walk["fold"] == "final"] if len(walk) else walk
    metrics = None
    report: dict = {
        "models": list(models),
        "blend": None if blend is None else list(blend),
        "pretrained_on_auxiliary_data": aux is not None,
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
    model_version = f"{'+'.join(models)}-{last_day:%Y%m%d}"
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
