"""Run the story tracker over a range of days, resumably.

    uv run python -m undercurrent.stories.track --start 2026-01-01 --end 2026-03-31

Reads, for each day (all under data/processed/):
    vectors/news/<day>.npy + .parquet   the day's headlines as vectors (python -m undercurrent.vectors.store)
    articles                            titles and places, to name stories and apply the place rule

Writes one file per day (and the state needed to carry on):
    story_assignments/<day>.parquet     article_id, story_id, day, similarity
    stories_daily/<day>.parquet         story_id, day, n_articles_day, first_seen, label, category
    story_lineage/<day>.parquet         old_story_id, new_story_id, day (only on days with a merge)
    tracker_state/state.pkl             the stories alive after the last day, rewritten every day

The tracker is sequential: every day must be processed, in order. A killed run resumes after the
last day it finished; days are deterministic, so redoing one gives the same files. Start with
--fresh to begin a new timeline (it forgets the state, but doesn't delete any output).

Run it in its own process, not the one that embeds: PyTorch and FAISS crash each other on macOS.
"""

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.stories.graph import Place
from undercurrent.stories.tracker import DayResult, Tracker, TrackerConfig
from undercurrent.vectors.store import is_done, load_day, parquet_source, read_manifest, vectors_dir

log = logging.getLogger(__name__)

ASSIGNMENTS, DAILY, LINEAGE = "story_assignments", "stories_daily", "story_lineage"
STATE = Path("tracker_state") / "state.pkl"

SCHEMAS = {
    ASSIGNMENTS: pa.schema(
        [
            ("article_id", pa.string()),
            ("story_id", pa.string()),
            ("day", pa.date32()),
            ("similarity", pa.float32()),
        ]
    ),
    DAILY: pa.schema(
        [
            ("story_id", pa.string()),
            ("day", pa.date32()),
            ("n_articles_day", pa.int32()),
            ("first_seen", pa.date32()),
            ("label", pa.string()),
            ("category", pa.string()),
        ]
    ),
    LINEAGE: pa.schema(
        [("old_story_id", pa.string()), ("new_story_id", pa.string()), ("day", pa.date32())]
    ),
}


def _places(locations: list[dict] | None, limit: int = 8) -> list[Place]:
    out: list[Place] = []
    for loc in locations or []:
        if loc.get("lat") is not None and loc.get("lon") is not None:
            out.append((float(loc["lat"]), float(loc["lon"]), loc.get("country")))
    return out[:limit]


def day_meta(
    processed: Path, day: dt.date, ids: list[str]
) -> tuple[list[str] | None, list[list[Place]] | None]:
    """(titles, places) for these article ids on this day, or (None, None) if there is no articles
    table. This is the tracker's only knowledge of A's schema (docs/WORKPLAN.md 3.1)."""
    articles = parquet_source(processed, "articles")
    if articles is None:
        return None, None
    con = duckdb.connect()
    columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {articles}").fetchall()]
    when = (
        "day"
        if "day" in columns
        else "CAST(published_at AS DATE)"
        if "published_at" in columns
        else None
    )
    if "id" not in columns or "title" not in columns or when is None:
        raise ValueError(
            f"articles needs id, title and day (or published_at); it has {columns}. "
            "If A's schema changed, update day_meta() in stories/track.py."
        )
    with_places = "locations" in columns
    rows = con.execute(
        f"SELECT id, title{', locations' if with_places else ''} FROM {articles} WHERE {when} = ?",
        [day],
    ).fetchall()
    meta = {str(r[0]): (r[1] or "", _places(r[2]) if with_places else []) for r in rows}
    titles = [meta.get(i, ("", []))[0] for i in ids]
    places = [meta.get(i, ("", []))[1] for i in ids] if with_places else None
    return titles, places


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        pq.write_table(table, tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_day(processed: Path, result: DayResult) -> None:
    name = f"{result.day:%Y-%m-%d}.parquet"
    for folder, frame in (
        (ASSIGNMENTS, result.assignments),
        (DAILY, result.stories),
        (LINEAGE, result.lineage),
    ):
        path = processed / folder / name
        if folder == LINEAGE and frame.empty:
            path.unlink(missing_ok=True)  # a rerun of this day found no merge: no stale file
            continue
        schema = SCHEMAS[folder]
        # Build from the schema, not by converting the frame: an empty day (nothing joined, no
        # story started) has empty columns that pandas would type as numbers, not dates.
        table = pa.Table.from_pydict({c: frame[c].tolist() for c in schema.names}, schema=schema)
        _write(path, table)


def make_prototypes() -> None:
    """Make the category prototypes in a separate process, once. Making them loads PyTorch, and a
    process that has loaded PyTorch runs FAISS on one thread (search.get_faiss): on the first real
    run that made every day of tracking several times slower."""
    code = "from undercurrent.vectors.categories import prototypes; prototypes()"
    if subprocess.run([sys.executable, "-c", code], check=False).returncode != 0:
        log.warning("Couldn't make the category prototypes separately; making them here instead.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--start", type=dt.date.fromisoformat, required=True)
    p.add_argument("--end", type=dt.date.fromisoformat, required=True, help="inclusive")
    p.add_argument("--processed", type=Path, default=config.PROCESSED)
    p.add_argument(
        "--fresh", action="store_true", help="ignore the saved state and start a new timeline"
    )
    p.add_argument(
        "--no-categories", action="store_true", help="don't label stories with a category"
    )
    p.add_argument("--link-sim", type=float, help="item-item cutoff to start a story")
    p.add_argument("--assign-sim", type=float, help="item-to-story cutoff to join one")
    p.add_argument("--min-size", type=int)
    p.add_argument(
        "--merge-sim", type=float, help="story-story cutoff to merge; 0 turns merging off"
    )
    p.add_argument("--half-life-days", type=float)
    p.add_argument("--idle-days", type=int)
    return p.parse_args(argv)


def _config(args: argparse.Namespace) -> TrackerConfig:
    given = {
        "link_sim": args.link_sim,
        "assign_sim": args.assign_sim,
        "min_size": args.min_size,
        "half_life_days": args.half_life_days,
        "idle_days": args.idle_days,
    }
    settings = {k: v for k, v in given.items() if v is not None}
    if args.merge_sim is not None:  # 0 means "never merge", which the config spells None
        settings["merge_sim"] = args.merge_sim or None
    return TrackerConfig(**settings)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    processed, root = args.processed, vectors_dir(args.processed)

    categorize = None
    if not args.no_categories:
        # Load the category prototypes now, before FAISS is used. They come from a disk cache; if
        # that is empty they are made in another process, so this one never loads PyTorch.
        from undercurrent.vectors.categories import cached, categorize, prototypes

        if not cached():
            make_prototypes()
        prototypes()

    state_path = processed / STATE
    tracker: Tracker | None = None
    if not args.fresh and state_path.exists():
        tracker = Tracker.load(state_path, categorize=categorize)
        first_needed = tracker.day + dt.timedelta(days=1)
        if args.start > first_needed:
            raise SystemExit(
                f"The tracker has processed up to {tracker.day}, so --start {args.start} would skip "
                f"{first_needed} to {args.start - dt.timedelta(days=1)}. Every day has to be processed, in order. "
                f"Start at {first_needed}, or use --fresh to begin a new timeline."
            )
        print(
            f"resuming after {tracker.day} ({len(tracker):,} active stories), "
            "with the settings saved in the state"
        )

    totals = dict.fromkeys(
        (
            "items",
            "joined_existing",
            "in_new_stories",
            "no_story",
            "new_stories",
            "merged",
            "retired",
        ),
        0,
    )
    day = args.start
    while day <= args.end:
        if tracker is not None and tracker.day is not None and day <= tracker.day:
            day += dt.timedelta(days=1)
            continue
        if not is_done(root, "news", day):
            raise SystemExit(
                f"No news vectors for {day}. Embed it first: "
                f"python -m undercurrent.vectors.store --source news --start {day} --end {args.end}"
            )
        started = time.time()
        ids, vectors = load_day("news", day, root=root)
        if tracker is None:
            manifest = read_manifest(root)
            tracker = Tracker(
                manifest.dim if manifest else vectors.shape[1], _config(args), categorize=categorize
            )
        titles, places = day_meta(processed, day, ids)
        result = tracker.step(day, ids, vectors, titles, places)
        write_day(processed, result)
        tracker.save(state_path)  # after the outputs: a crash in between just redoes this day
        s = result.stats
        for key in totals:
            totals[key] += s[key]
        print(
            f"{day}  {s['items']:>8,} items  {s['joined_existing']:>8,} joined  {s['new_stories']:>6,} new "
            f"({s['in_new_stories']:,} items)  {s['no_story']:>8,} no story  {s['merged']:>4} merged  "
            f"{s['retired']:>5} retired  {s['active']:>7,} active  {time.time() - started:6.1f}s "
            f"(match {s['t_match']:.0f}s, cluster {s['t_cluster']:.0f}s, fold+merge {s['t_merge']:.0f}s)",
            flush=True,
        )
        day += dt.timedelta(days=1)
    print(
        f"done: {totals['items']:,} items, {totals['new_stories']:,} stories started, "
        f"{totals['merged']:,} merged, {totals['retired']:,} retired"
    )


if __name__ == "__main__":
    main()
