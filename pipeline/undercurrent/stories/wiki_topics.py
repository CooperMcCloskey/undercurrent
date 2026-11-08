"""Name stories after Wikipedia's own topics ("2026 Iran war"), where a story clearly belongs to one.

    uv run python -m undercurrent.stories.wiki_topics                 # data/export, in place
    uv run python -m undercurrent.stories.wiki_topics --dry-run

A story's title is a publisher's headline: often about one corner of the story ("Vance Blames Iran
War For GOP Midterm Losses" for the Iran war), and often with the site's name on the end. The
Wikipedia "Current events" pages (vectors/wiki_events.py) group each day's events under named
topics: "2026 Iran war", "Gen Z protests", "2026 Kolkata Durga Puja crowd crush". When a story's
headlines match one of those events closely, it is that topic's story, and the topic's name is a
better name for it.

How a story is matched: its headlines (up to HEADLINES_PER_STORY, as they are in the snapshots) are
embedded and averaged; the match is the one Wikipedia bullet, from a day within WINDOW_DAYS of the
days the story is in the export, with the highest cosine similarity, if that is at least MIN_SIM.
Read by eye on the 2026-11-06 export: from 0.65 up every match was right; at 0.60-0.65 about 3 in 4;
at 0.55-0.60 about half. So 0.62 is right about 85% of the time, and names the biggest stories
(around 8% of those the wiki days cover) and leaves the rest alone.

Specific topic or umbrella. Some topics are one event ("2026 Kolkata Durga Puja crowd crush": one
story). Others are umbrellas that many stories sit under ("Middle Eastern crisis": ten stories;
"2026 European unrest": twelve). Naming all of those the same would make them indistinguishable. So
a topic that at most MAX_STORIES_PER_NAME stories match is their name; for an umbrella, each story
keeps its own headline, and the topic goes in its summary ("Part of: 2026 European unrest").

What changes in the export. A story with a specific topic: its title in each snapshot from the day
the topic first appears on Wikipedia (point in time: before that it keeps its headline), and its
title plus a "Latest headline: ..." summary in stories/<id>.json. A story under an umbrella: only
the summary. The headlines it replaced are kept in `.wiki_topics_original.json`, so running it
again starts from them, not from the names.
"""

import argparse
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent import contracts as c
from undercurrent.stories.export import write_json

MIN_SIM = 0.62
MAX_STORIES_PER_NAME = 2  # more stories than this under one topic: it is an umbrella, not a name
WINDOW_DAYS = 3  # a bullet can name a story that is in the export within this many days of it
HEADLINES_PER_STORY = 6
SIDECAR = ".wiki_topics_original.json"
FALLBACK_MARK = " · "  # export.fallback_title(): names and place, no headline to match

Embed = Callable[[list[str]], np.ndarray]


def topic_name(heading: str, titles: list[str]) -> str:
    """The name of a topic: its first linked Wikipedia article, as words ("Gen_Z_protests" ->
    "Gen Z protests", "Yemeni_civil_war_(2014-present)" -> "Yemeni civil war"), else its heading.
    A heading that links several articles ("A, B") is named after the first."""
    name = (titles[0] if titles else heading).replace("_", " ")
    return re.sub(r"\s*\([^)]*\)\s*$", "", name).strip() or heading


@dataclass
class Naming:
    topic: str
    similarity: float
    headline: str
    #: True if the topic names the story; False if it is an umbrella over many stories
    specific: bool


class Topics:
    """The Wikipedia bullets that sit under a named topic, with their vectors."""

    def __init__(self, rows: list[dict], embed: Embed) -> None:
        self.rows = [r for r in rows if r["topic"]]
        self.days = np.array([r["day"].isoformat() for r in self.rows])
        self.vectors = embed([r["text"] for r in self.rows]) if self.rows else np.zeros((0, 1))
        self.heading = [r["topic"].split(" > ")[0] for r in self.rows]
        self.name = [
            topic_name(h, r["topic_titles"]) for h, r in zip(self.heading, self.rows, strict=True)
        ]
        self.first_day: dict[str, str] = {}
        for h, day in zip(self.heading, self.days, strict=True):
            self.first_day[h] = min(day, self.first_day.get(h, day))

    def best(self, vector: np.ndarray, first: str, last: str) -> tuple[float, int] | None:
        """The most similar bullet within WINDOW_DAYS of [first, last], and its similarity."""
        pad = dt.timedelta(WINDOW_DAYS)
        lo = (dt.date.fromisoformat(first) - pad).isoformat()
        hi = (dt.date.fromisoformat(last) + pad).isoformat()
        ok = (self.days >= lo) & (self.days <= hi)
        if not ok.any():
            return None
        sims = np.where(ok, self.vectors @ vector, -1.0)
        j = int(sims.argmax())
        return float(sims[j]), j


def read_snapshots(export: Path) -> dict[str, c.Snapshot]:
    files = sorted((export / "snapshots").glob("*.json"))
    if not files:
        raise SystemExit(f"No snapshots under {export}/snapshots")
    return {f.stem: c.Snapshot.model_validate(json.loads(f.read_text())) for f in files}


def name_stories(
    export: Path, wiki_events: Path, embed: Embed | None = None, dry_run: bool = False
) -> dict[str, Naming]:
    """Name the stories of `export` that match a Wikipedia topic. Returns, by story id, what each
    matched."""
    if embed is None:
        from undercurrent.vectors.embed import embed as embed_texts

        embed = embed_texts
    snapshots = read_snapshots(export)

    # Start from the headlines, not from names this wrote on an earlier run.
    sidecar = export / SIDECAR
    original: dict[str, dict[str, str]] = (
        json.loads(sidecar.read_text()) if sidecar.exists() else {}
    )
    for day, snap in snapshots.items():
        for story in snap.stories:
            story.title = original.get(story.id, {}).get(day, story.title)

    headlines: dict[str, list[str]] = defaultdict(list)
    active: dict[str, list[str]] = defaultdict(list)
    for day in sorted(snapshots, reverse=True):  # newest headlines first
        for story in snapshots[day].stories:
            active[story.id].append(day)
            if FALLBACK_MARK not in story.title and len(headlines[story.id]) < HEADLINES_PER_STORY:
                if story.title not in headlines[story.id]:
                    headlines[story.id].append(story.title)
    ids = [sid for sid, ts in headlines.items() if ts]

    topics = Topics(pq.read_table(wiki_events).to_pylist(), embed)
    if not len(topics.rows) or not ids:
        return {}
    flat = embed([t for sid in ids for t in headlines[sid]])
    named: dict[str, tuple[str, str, float]] = {}  # story id -> (name, heading, similarity)
    at = 0
    for sid in ids:
        mean = flat[at : at + len(headlines[sid])].mean(axis=0)
        at += len(headlines[sid])
        found = topics.best(mean / np.linalg.norm(mean), min(active[sid]), max(active[sid]))
        if found and found[0] >= MIN_SIM:
            named[sid] = (topics.name[found[1]], topics.heading[found[1]], found[0])

    per_topic = Counter(heading for _, heading, _ in named.values())
    result = {
        sid: Naming(name, sim, headlines[sid][0], per_topic[heading] <= MAX_STORIES_PER_NAME)
        for sid, (name, heading, sim) in named.items()
    }
    if dry_run or not named:
        return result

    kept: dict[str, dict[str, str]] = {}
    for day, snap in snapshots.items():
        for story in snap.stories:
            if story.id in result and result[story.id].specific:
                if day >= topics.first_day[named[story.id][1]]:
                    kept.setdefault(story.id, {})[day] = story.title
                    story.title = result[story.id].topic
        write_json(export, f"snapshots/{day}.json", snap)
    sidecar.write_text(json.dumps({**original, **kept}, ensure_ascii=False) + "\n")
    for sid, naming in result.items():
        path = export / "stories" / f"{sid}.json"
        if not path.exists():
            continue
        detail = c.StoryDetail.model_validate(json.loads(path.read_text()))
        if naming.specific:
            detail.title = naming.topic
            detail.summary = f"Latest headline: {naming.headline}"
        else:
            detail.summary = f"Part of: {naming.topic}"
        write_json(export, f"stories/{sid}.json", detail)
    return result


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--export", type=Path, default=config.EXPORT)
    p.add_argument("--wiki-events", type=Path, default=config.PROCESSED / "wiki_events.parquet")
    p.add_argument("--dry-run", action="store_true", help="say what would be named, write nothing")
    args = p.parse_args()
    result = name_stories(args.export, args.wiki_events, dry_run=args.dry_run)
    specific = [n for n in result.values() if n.specific]
    print(
        f"{len(specific)} stories named after a Wikipedia topic, {len(result) - len(specific)} "
        "more filed under an umbrella topic" + (" (dry run)" if args.dry_run else "")
    )
    for n in sorted(specific, key=lambda n: n.topic):
        print(f"  {n.similarity:.2f}  {n.topic}  <-  {n.headline[:80]}")


if __name__ == "__main__":
    main()
