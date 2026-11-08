"""stories/wiki_topics.py: naming stories after Wikipedia's current-events topics."""

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent import contracts as c
from undercurrent.stories import wiki_topics
from undercurrent.stories.export import write_json

KEYWORDS = ["quake", "fire", "war", "flood", "vote"]
D1, D2, D3 = dt.date(2026, 11, 1), dt.date(2026, 11, 2), dt.date(2026, 11, 3)


def embed(texts: list[str]) -> np.ndarray:
    """One axis per keyword: a text is the keyword it contains (and a little of axis 0 so no
    vector is zero). Same keyword: similarity 1; different: about 0."""
    out = np.full((len(texts), len(KEYWORDS) + 1), 0.0)
    for i, t in enumerate(texts):
        hits = [k for k, word in enumerate(KEYWORDS) if word in t.lower()]
        out[i, hits[0] if hits else len(KEYWORDS)] = 1.0
    return out


def story(sid: str, title: str) -> c.StorySummary:
    return c.StorySummary(
        id=sid,
        title=title,
        location=c.Location(name="Paris", country="FR", lat=48.8, lon=2.3),
        category="other",
        footprint=[],
        article_count_7d=10,
        post_count_7d=0,
        growth_7d=0.0,
        tone=None,
        first_seen=D1,
        forecast=None,
        outcome=None,
    )


def detail(sid: str, title: str) -> c.StoryDetail:
    return c.StoryDetail(
        id=sid,
        title=title,
        summary=None,
        first_seen=D1,
        locations=[],
        entities=[],
        timeline=[],
        items=[],
    )


def bullets(rows: list[tuple[dt.date, str, str]]) -> list[dict]:
    return [
        {
            "id": f"{day}:{i}",
            "day": day,
            "text": text,
            "titles": [],
            "section": "x",
            "topic": topic,
            "topic_titles": [topic.split(" > ")[0].replace(" ", "_") + "_(2026–present)"],
        }
        for i, (day, text, topic) in enumerate(rows)
    ]


def build(tmp: Path) -> tuple[Path, Path]:
    export = tmp / "export"
    stories = {
        "quake-1": "Quake hits the coast | NewsRadio 99",
        "vote-1": "Vote count begins",
        "vote-2": "Vote result disputed",
        "vote-3": "Vote observers report",
        "fire-1": "Fire at the port",  # no bullet matches it
    }
    for day in (D1, D2, D3):
        write_json(
            export,
            f"snapshots/{day}.json",
            c.Snapshot(as_of=day, stories=[story(s, t) for s, t in stories.items()]),
        )
    for sid, title in stories.items():
        write_json(export, f"stories/{sid}.json", detail(sid, title))
    wiki = tmp / "wiki_events.parquet"
    rows = bullets(
        [
            (D2, "A quake strikes the coast", "2026 Coastal earthquake"),  # first seen on D2
            (D2, "Votes are counted nationwide", "2026 General election"),
            (D3, "Election vote disputed", "2026 General election"),
            (D2, "War continues", ""),  # no topic: can't name anything
        ]
    )
    pq.write_table(pa.Table.from_pylist(rows), wiki)
    return export, wiki


def titles(export: Path, day: dt.date) -> dict[str, str]:
    snap = json.loads((export / f"snapshots/{day}.json").read_text())
    return {s["id"]: s["title"] for s in snap["stories"]}


def test_specific_topics_name_a_story_and_umbrellas_only_label_it(tmp_path: Path) -> None:
    export, wiki = build(tmp_path)
    result = wiki_topics.name_stories(export, wiki, embed)
    assert {sid: n.specific for sid, n in result.items()} == {
        "quake-1": True,
        "vote-1": False,  # three stories under "2026 General election": an umbrella
        "vote-2": False,
        "vote-3": False,
    }
    assert "fire-1" not in result
    # point in time: the topic is on Wikipedia from D2, so D1 keeps its headline
    assert titles(export, D1)["quake-1"] == "Quake hits the coast | NewsRadio 99"
    assert titles(export, D2)["quake-1"] == "2026 Coastal earthquake"
    assert titles(export, D3)["quake-1"] == "2026 Coastal earthquake"
    # umbrellas and unmatched stories keep their headlines
    assert titles(export, D3)["vote-2"] == "Vote result disputed"
    assert titles(export, D3)["fire-1"] == "Fire at the port"
    detail_of = lambda sid: json.loads((export / f"stories/{sid}.json").read_text())  # noqa: E731
    assert detail_of("quake-1")["title"] == "2026 Coastal earthquake"
    assert detail_of("quake-1")["summary"] == "Latest headline: Quake hits the coast | NewsRadio 99"
    assert detail_of("vote-1")["title"] == "Vote count begins"
    assert detail_of("vote-1")["summary"] == "Part of: 2026 General election"
    assert detail_of("fire-1")["summary"] is None


def test_running_it_again_starts_from_the_headlines(tmp_path: Path) -> None:
    export, wiki = build(tmp_path)
    wiki_topics.name_stories(export, wiki, embed)
    first = {d: titles(export, d) for d in (D1, D2, D3)}
    wiki_topics.name_stories(export, wiki, embed)
    assert {d: titles(export, d) for d in (D1, D2, D3)} == first
    # the sidecar keeps the headlines it replaced
    original = json.loads((export / wiki_topics.SIDECAR).read_text())
    assert original["quake-1"][str(D2)] == "Quake hits the coast | NewsRadio 99"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    export, wiki = build(tmp_path)
    before = (export / f"snapshots/{D3}.json").read_text()
    assert wiki_topics.name_stories(export, wiki, embed, dry_run=True)
    assert (export / f"snapshots/{D3}.json").read_text() == before
    assert not (export / wiki_topics.SIDECAR).exists()


def test_topic_names_are_the_linked_article() -> None:
    assert (
        wiki_topics.topic_name("Gen Z protests", ["Gen_Z_protests", "2026_European_unrest"])
        == "Gen Z protests"
    )
    assert (
        wiki_topics.topic_name("Yemeni civil war", ["Yemeni_civil_war_(2014–present)"])
        == "Yemeni civil war"
    )
    assert wiki_topics.topic_name("A, B", ["A_thing", "B_thing"]) == "A thing"
    assert wiki_topics.topic_name("Plain heading", []) == "Plain heading"


def test_fallback_titles_are_not_matched(tmp_path: Path) -> None:
    export, wiki = build(tmp_path)
    for day in (D1, D2, D3):
        write_json(
            export,
            f"snapshots/{day}.json",
            c.Snapshot(as_of=day, stories=[story("junk", "Quake Board, Exchange · Shanghai")]),
        )
    assert wiki_topics.name_stories(export, wiki, embed) == {}
