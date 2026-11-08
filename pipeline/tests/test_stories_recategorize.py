"""stories/recategorize.py: new categories for an export that already exists."""

import datetime as dt
import json
from pathlib import Path

from undercurrent import contracts as c
from undercurrent.stories import recategorize
from undercurrent.stories.export import write_json


def story(sid: str, title: str, category: str) -> c.StorySummary:
    return c.StorySummary(
        id=sid,
        title=title,
        location=c.Location(name="Paris", country="FR", lat=48.8, lon=2.3),
        category=category,
        footprint=[],
        article_count_7d=10,
        post_count_7d=0,
        growth_7d=0.0,
        tone=None,
        first_seen=dt.date(2026, 11, 1),
        forecast=None,
        outcome=None,
    )


def test_recategorize_rewrites_snapshots(tmp_path: Path, monkeypatch) -> None:
    days = [dt.date(2026, 11, 5), dt.date(2026, 11, 6)]
    for day, title in zip(days, ["Striker scores twice", "Striker scores again"], strict=True):
        write_json(
            tmp_path,
            f"snapshots/{day}.json",
            c.Snapshot(
                as_of=day,
                stories=[
                    story("match", title, "other"),
                    story("junk", "Board of Directors, Exchange Web · Shanghai", "technology"),
                    story("same", "Election results", "politics"),
                ],
            ),
        )
    asked: dict[str, list[str]] = {}

    def fake(titles: dict[str, list[str]]) -> dict[str, tuple[str, float]]:
        asked.update(titles)
        return {sid: ("sport" if sid == "match" else "politics", 0.9) for sid in titles}

    monkeypatch.setattr(recategorize, "classify_titles", fake)
    moved = recategorize.recategorize(tmp_path)
    assert moved == {"other -> sport": 1}
    assert asked["match"] == ["Striker scores again", "Striker scores twice"]  # newest first
    assert "junk" not in asked, "a names-and-place title has nothing to read"
    for day in days:
        stories = {
            s["id"]: s["category"]
            for s in json.loads((tmp_path / f"snapshots/{day}.json").read_text(encoding="utf-8"))[
                "stories"
            ]
        }
        assert stories == {"match": "sport", "junk": "technology", "same": "politics"}


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    day = dt.date(2026, 11, 6)
    write_json(
        tmp_path,
        f"snapshots/{day}.json",
        c.Snapshot(as_of=day, stories=[story("a", "Goal", "other")]),
    )
    monkeypatch.setattr(recategorize, "classify_titles", lambda t: {k: ("sport", 0.9) for k in t})
    before = (tmp_path / f"snapshots/{day}.json").read_text(encoding="utf-8")
    assert recategorize.recategorize(tmp_path, dry_run=True) == {"other -> sport": 1}
    assert (tmp_path / f"snapshots/{day}.json").read_text(encoding="utf-8") == before
