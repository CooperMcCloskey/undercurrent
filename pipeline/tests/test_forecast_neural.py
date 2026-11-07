"""The neural model: its inputs never see the future, it learns, and it fits into the backtest."""

import numpy as np
import pandas as pd
import pytest

from undercurrent import contracts
from undercurrent.forecast import settings
from undercurrent.forecast.backtest import make_folds, run_backtest
from undercurrent.forecast.dataset import build_dataset
from undercurrent.forecast.features import SEQ_CHANNELS
from undercurrent.forecast.models import JOINT_MODELS, register_joint
from undercurrent.forecast.neural import GRUQuantile
from undercurrent.forecast.run import main
from undercurrent.forecast.synthetic import make_panel

H = [1, 7]
L = 14
TINY = settings.NNParams(hidden=16, epochs=6, batch_size=256, device="cpu", patience=2)
SMALL = dict(final_test_days=60, fold_days=60, cal_days=21, min_train_days=60)


@pytest.fixture(scope="module")
def data() -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray]:
    panel, totals, wiki = make_panel(n_stories=120, n_days=300, seed=1)
    rows, seq = build_dataset(panel, H, totals=totals, wiki=wiki, min_articles=10, seq_len=L)
    return panel, rows, seq


def test_a_window_holds_the_last_days_oldest_first() -> None:
    days = pd.date_range("2026-01-01", periods=40).as_unit("ns")
    panel = pd.DataFrame({"story_id": "s", "day": days, "n_articles": np.arange(1, 41)})
    rows, seq = build_dataset(panel, [1], min_articles=0, burn_in=0, seq_len=5)
    assert seq.shape == (len(rows), 5, len(SEQ_CHANNELS)) and seq.dtype == np.float16

    count, alive = SEQ_CHANNELS.index("log_count"), SEQ_CHANNELS.index("alive")
    i = rows.index[rows["day"] == days[10]][0]  # the 11th day: counts 7..11 are the last five
    np.testing.assert_allclose(seq[i, :, count], np.log2(np.arange(7, 12) + 1), rtol=1e-3)
    first = rows.index[rows["day"] == days[0]][0]  # before the data starts: zeros, not alive
    assert (seq[first, :4] == 0).all() and seq[first, 4, alive] == 1


def _windows_at_or_before(rows: pd.DataFrame, seq: np.ndarray, day: pd.Timestamp) -> np.ndarray:
    order = rows.sort_values(["story_id", "day"]).index
    keep = [i for i in order if rows.at[i, "day"] <= day]
    return seq[keep].astype(np.float32)


def test_sequences_never_use_the_future() -> None:
    panel, totals, wiki = make_panel(n_stories=40, n_days=160, seed=3)
    cut = pd.Timestamp("2025-04-20")
    rows, seq = build_dataset(panel, H, totals=totals, wiki=wiki, min_articles=5, seq_len=L)

    altered = panel.copy()
    later = altered["day"] > cut
    altered.loc[later, "n_articles"] = altered.loc[later, "n_articles"] * 3
    altered.loc[later, "tone_mean"] = 9.0
    rows2, seq2 = build_dataset(
        altered, H, totals=totals, wiki=wiki[wiki["day"] <= cut], min_articles=5, seq_len=L
    )
    before, after = _windows_at_or_before(rows, seq, cut), _windows_at_or_before(rows2, seq2, cut)
    assert before.shape == after.shape and len(before) > 100
    np.testing.assert_array_equal(before, after)


def test_the_newest_pageviews_in_a_window_are_a_day_old() -> None:
    panel, totals, wiki = make_panel(n_stories=25, n_days=140, seed=5)
    day = pd.Timestamp("2025-04-25")
    views = SEQ_CHANNELS.index("log_wiki_views")
    spiked = wiki.copy()
    spiked.loc[spiked["day"] == day, "wiki_views"] *= 50

    def newest_view(w: pd.DataFrame, on: pd.Timestamp) -> np.ndarray:
        rows, seq = build_dataset(panel, [1], totals=totals, wiki=w, min_articles=5, seq_len=3)
        pick = rows["day"] == on
        return seq[pick.to_numpy()][np.argsort(rows.loc[pick, "story_id"].to_numpy()), -1, views]

    np.testing.assert_array_equal(
        newest_view(wiki, day), newest_view(spiked, day)
    )  # not visible on day t
    next_day = day + pd.Timedelta(days=1)
    assert not np.array_equal(
        newest_view(wiki, next_day), newest_view(spiked, next_day)
    )  # visible a day later


def test_the_network_learns_and_keeps_its_ranges_ordered(data) -> None:
    _, rows, seq = data
    cut = rows["day"].max() - pd.Timedelta(days=60)
    train = (rows["day"] + pd.Timedelta(days=7) < cut).to_numpy()
    test = ((rows["day"] > cut) & rows["y_7"].notna()).to_numpy()
    Y = np.column_stack([np.where(train, rows[f"y_{h}"], np.nan) for h in H])

    model = GRUQuantile(0.8, H, TINY, seed=0)
    model.fit(rows[train], seq[train], Y[train])
    out = model.predict(rows[test], seq[test])

    assert out.shape == (test.sum(), len(H), 3)
    assert (out[..., 0] <= out[..., 1]).all() and (out[..., 1] <= out[..., 2]).all()
    y = rows.loc[test, "y_7"].to_numpy()
    assert (
        np.abs(out[:, 1, 1] - y).mean() < 0.8 * np.abs(y).mean()
    )  # clearly beats "stays the same"
    inside = ((out[:, 1, 0] <= y) & (y <= out[:, 1, 2])).mean()
    assert 0.5 < inside < 0.97  # uncalibrated, but in the right region for an 80% range


def test_a_network_ignores_targets_it_may_not_train_on(data) -> None:
    """NaN in Y means "do not learn from this": the purge relies on it."""
    _, rows, seq = data
    Y = np.full((len(rows), len(H)), np.nan, dtype=np.float32)
    Y[:, 0] = rows["y_1"].to_numpy()  # only the 1-day target is allowed
    model = GRUQuantile(0.8, H, TINY, seed=0)
    model.fit(rows, seq, Y)
    assert model.predict(rows.head(5), seq[:5]).shape == (5, 2, 3)


def test_blend_and_pretraining_in_the_backtest(data) -> None:
    _, rows, seq = data
    aux_panel, aux_totals, aux_wiki = make_panel(n_stories=60, n_days=300, seed=9)
    aux = build_dataset(aux_panel, H, totals=aux_totals, wiki=aux_wiki, min_articles=10, seq_len=L)

    result = run_backtest(
        rows,
        H,
        models=["hgb", "gru"],
        model_params={"hgb": settings.HGBParams(max_iter=25), "gru": TINY},
        seq=seq,
        aux=aux,
        n_folds=0,
        factor_days=7,
        **SMALL,
    )
    assert result.model_version.startswith("hgb+gru-")
    assert (
        result.report["models"] == ["hgb", "gru"] and result.report["pretrained_on_auxiliary_data"]
    )
    by_method = result.report["final"]["mae_by_method"][7]
    assert {"model", "hgb", "gru", "persistence"} <= set(
        by_method
    )  # each model is scored alone too
    assert by_method["model"] < by_method["persistence"]
    metrics = contracts.ModelMetrics.model_validate(result.metrics.model_dump())
    assert 0.6 < {h.days: h for h in metrics.horizons}[7].coverage < 0.95
    assert {"med_hgb", "med_gru"} <= set(result.walkforward.columns)


def test_pretraining_and_training_only_use_targets_known_before_calibration(data) -> None:
    _, rows, seq = data
    aux_panel, aux_totals, aux_wiki = make_panel(n_stories=60, n_days=300, seed=9)
    aux_rows, aux_seq = build_dataset(
        aux_panel, H, totals=aux_totals, wiki=aux_wiki, min_articles=10, seq_len=L
    )
    seen: list[tuple] = []

    class Stub:
        name = "stub"

        def __init__(self, interval, horizons, params=None, seed=0) -> None:
            self.horizons = horizons

        def fit(self, fit_rows, fit_seq, Y, weight=None, pretrain=None) -> None:
            seen.append(("main", fit_rows["day"].to_numpy(), Y))
            if pretrain is not None:
                p_rows, _, p_Y, _ = pretrain
                seen.append(("pretrain", p_rows["day"].to_numpy(), p_Y))

        def predict(self, pred_rows, pred_seq) -> np.ndarray:
            n = len(pred_rows)
            return np.broadcast_to(np.array([-1.0, 0.0, 1.0]), (n, len(self.horizons), 3)).copy()

    JOINT_MODELS["stub"] = Stub
    try:
        run_backtest(rows, H, models=["stub"], seq=seq, aux=(aux_rows, aux_seq), n_folds=1, **SMALL)
    finally:
        del JOINT_MODELS["stub"]

    folds = [*make_folds(rows["day"].min(), rows["day"].max(), H, n_folds=1, **SMALL)]
    live_cal_start = rows["day"].max() + pd.Timedelta(days=1) - pd.Timedelta(days=21 + max(H))
    cal_starts = [f.cal_start for f in folds] + [live_cal_start]
    assert len(seen) == 2 * len(cal_starts)  # a main and a pretrain fit for each window
    for (kind, days, Y), cal_start in zip(
        seen, [c for c in cal_starts for _ in (0, 1)], strict=True
    ):
        for j, h in enumerate(H):
            used = days[np.isfinite(Y[:, j])]
            assert len(used) > 0, kind
            assert (pd.to_datetime(used) + pd.Timedelta(days=h) < cal_start).all(), (kind, h)


def test_a_model_that_reads_history_says_when_it_has_none(data) -> None:
    _, rows, _ = data

    @register_joint("needs-seq")
    class NeedsSeq:
        name = "needs-seq"

    try:
        with pytest.raises(ValueError, match="daily history"):
            run_backtest(rows, H, models=["needs-seq"], n_folds=0, **SMALL)
    finally:
        del JOINT_MODELS["needs-seq"]
    with pytest.raises(ValueError, match="Unknown model"):
        run_backtest(rows, H, models=["no-such-model"], n_folds=0, **SMALL)


def test_command_line_with_the_neural_model(tmp_path, capsys) -> None:
    main(
        [
            "--source",
            "synthetic",
            "--processed",
            str(tmp_path),
            "--synthetic-stories",
            "70",
            "--synthetic-days",
            "260",
            "--horizons",
            "1",
            "7",
            "--max-iter",
            "20",
            "--folds",
            "0",
            "--model",
            "hgb",
            "gru",
            "--nn-epochs",
            "3",
            "--nn-hidden",
            "16",
            "--device",
            "cpu",
            "--pretrain",
            "synthetic",
            "--seq-days",
            "14",
            "--final-test-days",
            "50",
            "--cal-days",
            "21",
            "--min-train-days",
            "60",
        ]  # fmt: skip
    )
    printed = capsys.readouterr().out
    assert "pretraining data (synthetic)" in printed and "gru" in printed and "blend" in printed
    assert (tmp_path / "forecast" / "synthetic" / "forecasts.parquet").exists()
