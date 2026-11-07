"""Walk-forward backtest, calibration, and the forecast files, end to end on synthetic stories."""

import numpy as np
import pandas as pd
import pytest

from undercurrent import contracts
from undercurrent.forecast import settings
from undercurrent.forecast.backtest import live_fold, make_folds, run_backtest
from undercurrent.forecast.dataset import build_rows
from undercurrent.forecast.export import (
    forecasts_frame,
    load_forecasts,
    meta_horizons,
    write_outputs,
)
from undercurrent.forecast.models import MODELS, apply_offset, conformal_offset
from undercurrent.forecast.run import main
from undercurrent.forecast.synthetic import make_panel

H = [1, 7]
QUICK = settings.HGBParams(max_iter=25)
SMALL = dict(final_test_days=60, fold_days=60, cal_days=21, min_train_days=60)


@pytest.fixture(scope="module")
def synthetic() -> tuple[pd.DataFrame, pd.DataFrame]:
    panel, totals, wiki = make_panel(n_stories=120, n_days=300, seed=1)
    return panel, build_rows(panel, H, totals=totals, wiki=wiki, min_articles=10)


def test_folds_go_backwards_and_leave_a_gap() -> None:
    first, last = pd.Timestamp("2025-01-01"), pd.Timestamp("2026-01-01")
    folds = make_folds(first, last, [1, 14], final_test_days=90, fold_days=91, n_folds=3,
                       cal_days=28, min_train_days=120)  # fmt: skip
    assert folds[0].name == "final" and folds[0].test_end == last
    assert 1 < len(folds) < 4  # a window without 120 days of history before it is dropped
    for newer, older in zip(folds, folds[1:], strict=False):
        assert older.test_end == newer.test_start - pd.Timedelta(days=1)
    for f in folds:
        assert (f.test_start - f.cal_start).days == 28 + 14  # calibration + the longest horizon
        assert (f.cal_start - first).days >= 120
    assert make_folds(first, first + pd.Timedelta(days=100), [7]) == []


def test_training_never_overlaps_what_it_is_scored_on(synthetic) -> None:
    _, rows = synthetic
    calls: list[tuple] = []

    class Recorder:
        name = "recorder"

        def __init__(self, interval, params=None, seed=0) -> None:
            pass

        def fit(self, X, y, weight=None) -> None:
            h = next(h for h in H if np.array_equal(X[f"y_{h}"].to_numpy(), y))
            calls.append(("fit", h, X["day"].max()))

        def predict(self, X) -> np.ndarray:
            calls.append(("predict", X["day"].min(), X["day"].max()))
            return np.column_stack([-np.ones(len(X)), np.zeros(len(X)), np.ones(len(X))])

    MODELS["recorder"] = Recorder
    try:
        run_backtest(rows, H, models=["recorder"], n_folds=1, factor_days=1, **SMALL)
    finally:
        del MODELS["recorder"]

    last = rows["day"].max()
    folds = make_folds(rows["day"].min(), last, H, n_folds=1, **{k: v for k, v in SMALL.items() if k != "fold_days"}, fold_days=60)  # fmt: skip
    assert len(folds) == 2
    expected_folds = [*folds, live_fold(last, H, SMALL["cal_days"])]
    calls_iter = iter(calls)
    for fold in expected_folds:
        for h in H:
            delta = pd.Timedelta(days=h)
            kind, fit_h, last_train_day = next(calls_iter)
            assert (kind, fit_h) == ("fit", h)
            assert (
                last_train_day + delta < fold.cal_start
            )  # training targets end before calibration
            _, cal_lo, cal_hi = next(calls_iter)
            assert cal_lo >= fold.cal_start and cal_hi + delta < fold.test_start  # known by then
            _, test_lo, test_hi = next(calls_iter)
            if fold.name == "live":
                assert test_lo == test_hi == last
            else:
                assert fold.test_start <= test_lo and test_hi <= fold.test_end
    assert next(calls_iter, None) is None


def test_calibration_fixes_ranges_that_are_too_narrow_or_too_wide() -> None:
    rng = np.random.default_rng(0)
    y_cal, y_new = rng.normal(0, 1, 5000), rng.normal(0, 1, 5000)

    def ranges(half: float) -> np.ndarray:
        n = 5000
        return np.column_stack([np.full(n, -half), np.zeros(n), np.full(n, half)])

    for half, widen in [(0.3, True), (3.0, False)]:
        offset = conformal_offset(ranges(half), y_cal, 0.8)
        assert (offset > 0) == widen
        fixed = apply_offset(ranges(half), offset)
        covered = ((fixed[:, 0] <= y_new) & (y_new <= fixed[:, 2])).mean()
        assert covered == pytest.approx(0.8, abs=0.03)
        assert (fixed[:, 0] <= fixed[:, 1]).all() and (fixed[:, 1] <= fixed[:, 2]).all()
    assert conformal_offset(ranges(1.0)[:10], y_cal[:10], 0.8) == 0.0  # too little to calibrate on


def test_end_to_end_on_synthetic_data(synthetic, tmp_path) -> None:
    panel, rows = synthetic
    result = run_backtest(rows, H, model_params={"hgb": QUICK}, n_folds=0, factor_days=7, **SMALL)

    metrics = result.metrics
    assert isinstance(metrics, contracts.ModelMetrics)
    by = {h.days: h for h in metrics.horizons}
    assert list(by) == H
    assert by[7].mae_log2 < by[7].baseline_mae_log2  # the fading in this data is learnable
    assert 0.6 < by[7].coverage < 0.95  # the 80% ranges are roughly honest
    assert metrics.test_start >= result.walkforward["day"].min().date()

    forecasts = forecasts_frame(result.walkforward, result.live, result.model_version)
    assert not forecasts.duplicated(["story_id", "day", "days"]).any()
    live = forecasts[forecasts["source"] == "live"]
    assert set(live["day"]) == {rows["day"].max().date()}
    assert any(len(f) for f in live["factors"])  # the latest forecasts explain themselves

    paths = write_outputs(tmp_path, forecasts, result.metrics, result.report)
    objects = load_forecasts(paths["forecasts"])
    assert meta_horizons(paths["forecasts"]) == H
    contracts.ModelMetrics.model_validate_json(paths["metrics"].read_text())

    # The outcomes are the real thing: recompute article_count_7d from the raw panel.
    days = pd.date_range(panel["day"].min(), panel["day"].max())
    daily = (
        panel.pivot(index="day", columns="story_id", values="n_articles").reindex(days).fillna(0)
    )
    a7 = daily.rolling(7, min_periods=1).sum()
    checked = 0
    for (story, day), (forecast, outcome) in objects.items():
        assert {h.days for h in forecast.horizons} <= set(H)
        if outcome is None:
            continue
        assert {o.days for o in outcome.horizons} <= {h.days for h in forecast.horizons}
        for o in outcome.horizons:
            assert o.articles_7d == a7.loc[pd.Timestamp(day) + pd.Timedelta(days=o.days), story]
            checked += 1
    assert checked > 100


def test_not_enough_history_says_so(synthetic) -> None:
    _, rows = synthetic
    with pytest.raises(ValueError, match="Not enough history"):
        run_backtest(rows, H, model_params={"hgb": QUICK}, final_test_days=400)


def test_command_line_trial_run_stays_out_of_the_real_outputs(tmp_path, capsys) -> None:
    main(
        [
            "--source",
            "synthetic",
            "--processed",
            str(tmp_path),
            "--synthetic-stories",
            "80",
            "--synthetic-days",
            "260",
            "--horizons",
            "1",
            "7",
            "--max-iter",
            "20",
            "--folds",
            "0",
            "--final-test-days",
            "50",
            "--cal-days",
            "21",
            "--min-train-days",
            "60",
        ]  # fmt: skip
    )
    printed = capsys.readouterr().out
    assert "vs persistence" in printed and "trial run on synthetic data" in printed
    trial = tmp_path / "forecast" / "synthetic"
    assert (trial / "forecasts.parquet").exists() and (trial / "model_metrics.json").exists()
    assert not (tmp_path / "forecasts.parquet").exists()  # B's exporter reads that one
