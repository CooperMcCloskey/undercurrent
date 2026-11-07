"""The forecast targets and features: correct, and point-in-time (nothing from the future)."""

import numpy as np
import pandas as pd
import pytest

from undercurrent.forecast import settings
from undercurrent.forecast.dataset import build_rows
from undercurrent.forecast.features import FEATURES, lead, rolling_mean, rolling_sum, shift
from undercurrent.forecast.synthetic import make_panel

H = [1, 3, 7]


def test_array_helpers() -> None:
    x = np.array([[1.0, 2.0, 3.0, 4.0, 5.0]])
    assert rolling_sum(x, 3).tolist() == [[1.0, 3.0, 6.0, 9.0, 12.0]]  # fewer days at the start
    assert np.isnan(shift(x, 2)[0, :2]).all() and shift(x, 2)[0, 2] == 1.0
    assert lead(x, 2)[0, 0] == 3.0 and np.isnan(lead(x, 2)[0, 3:]).all()
    y = np.array([[np.nan, 2.0, np.nan, 4.0]])
    assert rolling_mean(y, 2)[0, 1] == 2.0  # NaN days are skipped, not counted as zero
    assert np.isnan(rolling_mean(y, 1)[0, 0])


def test_targets_match_an_independent_calculation() -> None:
    days = pd.date_range("2026-01-01", periods=40).as_unit("ns")
    counts = np.arange(1, 41)  # 1, 2, 3, ... articles a day
    panel = pd.DataFrame({"story_id": "s", "day": days, "n_articles": counts})
    rows = build_rows(panel, H, min_articles=0, burn_in=0)

    a7 = pd.Series(counts, index=days).rolling(7, min_periods=1).sum()
    for h in H:
        expected = np.log2((a7.shift(-h) + 1) / (a7 + 1))
        got = rows.set_index("day")[f"y_{h}"]
        pd.testing.assert_series_equal(
            got,
            expected.loc[got.index].astype("float32"),
            check_names=False,
            check_freq=False,
            rtol=1e-5,
        )
        actual = rows.set_index("day")[f"actual_{h}"]
        pd.testing.assert_series_equal(
            actual,
            a7.shift(-h).loc[actual.index].astype("float32"),
            check_names=False,
            check_freq=False,
        )
    assert rows["a7"].iloc[-1] == a7.iloc[-1]
    # The last rows can't be scored yet, but they are still there to forecast from.
    assert rows["y_7"].tail(7).isna().all() and rows["y_1"].tail(1).isna().all()


def test_a_story_that_ends_is_scored_as_shrinking() -> None:
    days = pd.date_range("2026-01-01", periods=40)
    counts = np.r_[np.full(20, 50), np.zeros(20)]
    panel = pd.DataFrame({"story_id": "s", "day": days[:20], "n_articles": counts[:20]})
    # Days after the last article are not in the panel (no row), but the story keeps its history:
    # add a second story that runs to the end so the day range covers all 40 days.
    other = pd.DataFrame({"story_id": "t", "day": days, "n_articles": np.full(40, 30)})
    rows = build_rows(pd.concat([panel, other]), [7], min_articles=0, burn_in=0)
    s = rows[rows["story_id"] == "s"].set_index("day")
    # On the story's last day a7 = 350, a week later it is 7 * 0 -> log2(1 / 351).
    assert s.loc["2026-01-20", "y_7"] == pytest.approx(np.log2(1 / 351), rel=1e-5)


def test_burn_in_and_minimum_volume_filters() -> None:
    days = pd.date_range("2026-01-01", periods=60)
    panel = pd.DataFrame({"story_id": "s", "day": days, "n_articles": 5})  # a7 = 35 once warm
    rows = build_rows(panel, [1], min_articles=30, burn_in=21)
    assert rows["day"].min() == days[21]
    rows = build_rows(panel, [1], min_articles=40, burn_in=0)
    assert rows.empty


def _features_at_or_before(rows: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    keep = rows[rows["day"] <= day].sort_values(["story_id", "day"]).reset_index(drop=True)
    return keep[["story_id", "day", "a7", *FEATURES]]


def test_features_never_use_the_future() -> None:
    panel, totals, wiki = make_panel(n_stories=40, n_days=160, seed=3)
    cut = pd.Timestamp("2025-04-20")
    original = build_rows(panel, H, totals=totals, wiki=wiki, min_articles=5)

    # Rewrite everything after the cut: 3x the articles, no Wikipedia, different totals.
    altered = panel.copy()
    later = altered["day"] > cut
    altered.loc[later, "n_articles"] = altered.loc[later, "n_articles"] * 3
    altered.loc[later, "tone_mean"] = 9.0
    changed = build_rows(
        altered,
        H,
        totals=totals.assign(
            total_articles=np.where(totals["day"] > cut, 1.0, totals["total_articles"])
        ),
        wiki=wiki[wiki["day"] <= cut],
        min_articles=5,
    )
    pd.testing.assert_frame_equal(
        _features_at_or_before(original, cut),
        _features_at_or_before(changed, cut),
        check_exact=False,
    )


def test_wikipedia_pageviews_are_lagged_one_day() -> None:
    """A forecast on day t may only use pageviews up to t - 1: a spike on day t must not show."""
    panel, totals, wiki = make_panel(n_stories=25, n_days=140, seed=5)
    day = pd.Timestamp("2025-04-25")
    base = build_rows(panel, [1], totals=totals, wiki=wiki, min_articles=5)
    spiked = wiki.copy()
    spiked.loc[spiked["day"] == day, ["wiki_views", "wiki_views_en"]] *= 50
    after = build_rows(panel, [1], totals=totals, wiki=spiked, min_articles=5)

    wiki_cols = [c for c in FEATURES if c.startswith("wiki_")]

    def at_day(rows: pd.DataFrame, d: pd.Timestamp) -> pd.DataFrame:
        return rows[rows["day"] == d].sort_values("story_id")[wiki_cols].reset_index(drop=True)

    pd.testing.assert_frame_equal(at_day(base, day), at_day(after, day))  # unchanged that day
    assert settings.WIKI_VIEWS_LAG_DAYS >= 1
    next_day = day + pd.Timedelta(days=1)
    assert not at_day(base, next_day)["wiki_log_views7"].equals(
        at_day(after, next_day)["wiki_log_views7"]
    )


def test_missing_optional_inputs_are_just_nan() -> None:
    days = pd.date_range("2026-01-01", periods=50)
    panel = pd.DataFrame({"story_id": "s", "day": days, "n_articles": 30})  # nothing optional
    rows = build_rows(panel, [1], min_articles=1, burn_in=21)
    assert rows["wiki_growth_7d"].isna().all() and rows["share_of_day"].isna().all()
    assert rows["log_a7"].notna().all() and rows["age_days"].notna().all()


def test_a_bad_panel_says_what_is_wrong() -> None:
    with pytest.raises(ValueError, match="schema.py"):
        build_rows(pd.DataFrame({"story_id": ["s"], "day": ["2026-01-01"]}), [1])
    twice = pd.DataFrame({"story_id": ["s", "s"], "day": ["2026-01-01"] * 2, "n_articles": [1, 2]})
    with pytest.raises(ValueError, match="more than one row"):
        build_rows(twice, [1])
