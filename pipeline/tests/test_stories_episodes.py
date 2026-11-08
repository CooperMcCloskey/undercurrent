"""stories/export.py episodes: a story that starts up again after a quiet spell becomes a new story."""

import datetime as dt
import json
from pathlib import Path

import pytest
import test_stories_export as base

from undercurrent.ingest.gkg import ARTICLES_SCHEMA
from undercurrent.stories import export
from undercurrent.stories.track import SCHEMAS

D0 = dt.date(2026, 10, 1)
DAYS = [D0 + dt.timedelta(i) for i in range(30)]
BURSTY, STEADY = "bursty-story-aaaaaa", "steady-story-bbbbbb"


def per_day(story: str, i: int) -> int:
    if story == BURSTY:
        return 20 if i in (0, 1, 2, 13, 14, 15) else 0  # a 10-day gap between two bursts
    return 6  # steady, and never "active": under QUIET_FLOOR every day


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    root = tmp_path / "processed"
    first_seen: dict[str, dt.date] = {}
    for i, day in enumerate(DAYS):
        arts, assigns, counts = [], [], {}
        for story in (BURSTY, STEADY):
            for k in range(per_day(story, i)):
                aid = f"{story[:3]}{i:02d}{k:02d}".ljust(16, "0")
                arts.append(
                    {
                        "id": aid,
                        "url": f"https://news.example/{aid}",
                        "domain": "news.example",
                        "published_at": dt.datetime.combine(day, dt.time(8, k % 60), tzinfo=dt.UTC),
                        "title": f"{story} headline {i}-{k}",
                        "locations": [base.ROTTERDAM],
                        "persons": ["jan de vries"],
                        "orgs": [],
                        "themes": [],
                        "tone": -2.0,
                        "dup_count": 1,
                        "sampled": False,
                        "translated": False,
                        "lang": None,
                    }
                )
                assigns.append(
                    {"article_id": aid, "story_id": story, "day": day, "similarity": 0.9}
                )
                counts[story] = counts.get(story, 0) + 1
                first_seen.setdefault(story, day)
        base.write(root / "articles" / f"day={day}" / "part.parquet", arts, ARTICLES_SCHEMA)
        base.write(
            root / "story_assignments" / f"{day}.parquet", assigns, SCHEMAS["story_assignments"]
        )
        daily = [
            {
                "story_id": s,
                "day": day,
                "n_articles_day": n,
                "first_seen": first_seen[s],
                "label": s,
                "category": "politics",
            }
            for s, n in counts.items()
        ]
        base.write(root / "stories_daily" / f"{day}.parquet", daily, SCHEMAS["stories_daily"])
    return root


def snapshot(out: Path, day: dt.date) -> dict[str, dict]:
    return {s["id"]: s for s in json.loads((out / f"snapshots/{day}.json").read_text())["stories"]}


def test_a_story_that_starts_up_again_is_a_new_story(processed: Path, tmp_path: Path) -> None:
    out = export.run(processed, tmp_path / "out", min_articles=3)
    second = f"{BURSTY}-e2"
    assert set(snapshot(out, DAYS[2])) == {BURSTY, STEADY}  # the first burst
    # on day 14 only the second episode is alive, and it is new: its own id and first_seen
    day14 = snapshot(out, DAYS[14])
    assert set(day14) == {second, STEADY}
    assert day14[second]["first_seen"] == str(DAYS[13])
    assert day14[second]["article_count_7d"] == 40  # days 13 and 14: not 100 from the first burst
    # the first episode keeps the tracker's id, and its detail stops at its own last day
    first = json.loads((out / f"stories/{BURSTY}.json").read_text())
    later = json.loads((out / f"stories/{second}.json").read_text())
    assert [t["day"] for t in first["timeline"]] == [str(d) for d in DAYS[:3]]
    assert [t["day"] for t in later["timeline"]] == [str(d) for d in DAYS[13:16]]
    assert first["first_seen"] == str(D0) and later["first_seen"] == str(DAYS[13])
    # a story that is never active (6 a day, under the floor) is not split
    assert not any(
        sid.startswith(STEADY) and sid != STEADY for d in DAYS for sid in snapshot(out, d)
    )


def test_episodes_can_be_turned_off(processed: Path, tmp_path: Path) -> None:
    out = export.run(processed, tmp_path / "out", min_articles=3, split_episodes=False)
    assert BURSTY in snapshot(out, DAYS[14])
    assert not any(sid.endswith("-e2") for d in DAYS for sid in snapshot(out, d))


def test_a_forecast_for_the_story_goes_to_the_episode_alive_that_day(
    processed: Path, tmp_path: Path
) -> None:
    import pandas as pd

    rows = [
        {
            "story_id": BURSTY,
            "day": DAYS[14],
            "days": h,
            "articles_7d": 50.0,
            "low": 30.0,
            "high": 70.0,
            "model_version": "t",
            "factors": [],
            "actual_articles_7d": None,
            "source": "walkforward",
            "fold": 0,
        }
        for h in (1, 3)
    ]
    pd.DataFrame(rows).to_parquet(processed / "forecasts.parquet", index=False)
    out = export.run(processed, tmp_path / "out", min_articles=3)
    assert snapshot(out, DAYS[14])[f"{BURSTY}-e2"]["forecast"] is not None


def test_episode_starts_uses_only_the_past() -> None:
    days = list(range(100, 130))
    counts = [30 if d in (100, 101, 120, 121) else 0 for d in days]
    assert export.episode_starts(days, counts) == [100, 120]
    # cutting the data off before the second burst changes nothing about the first episode's start,
    # and cutting it right at the burst's first day already starts the episode there
    assert export.episode_starts(days[:20], counts[:20]) == [100]
    assert export.episode_starts(days[:21], counts[:21]) == [100, 120]
    # a gap shorter than GAP_DAYS does not split; neither does a story that is always small
    short = [30 if d in (100, 101, 108) else 0 for d in days]
    assert export.episode_starts(days, short) == [100]
    assert export.episode_starts(days, [5] * 30) == [100]


def test_episode_ids_fit_the_contract() -> None:
    long = "x" * 81
    assert len(export.episode_id(long, 12)) == 81
    assert export.episode_id("abc", 2) == "abc-e2"
