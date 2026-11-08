"""Re-decide the categories of an export that already exists, from the story titles it holds.

    uv run python -m undercurrent.stories.recategorize                  # data/export, in place
    uv run python -m undercurrent.stories.recategorize --export data/export --dry-run

`stories.export` now classifies each story from its English headlines while it exports. This does
the same to an export made before that (or without the model), without the pipeline's data: it
uses each story's titles as they are in the snapshots (up to TITLES_PER_STORY distinct ones, the
latest first), classified by `vectors/categories.py`. It has no GDELT themes, so a story whose
title the classifier is unsure about still gets the classifier's best guess. Stories whose title is
a names-and-place fallback ("Board of Directors, Exchange Web · Shanghai") have no headline to
read and keep the category they have. Only `category` in the snapshots changes; each story has
one category on every day it appears, as the exporter makes it.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from undercurrent import config
from undercurrent import contracts as c
from undercurrent.stories.export import TITLE_CONFIDENCE_MIN, TITLES_PER_STORY, write_json
from undercurrent.vectors.categories import classify_titles

FALLBACK_MARK = " · "  # export.fallback_title() joins names and place with this


def story_titles(snapshots: dict[str, c.Snapshot]) -> dict[str, list[str]]:
    """Each story's distinct real headlines, newest day first, at most TITLES_PER_STORY."""
    out: dict[str, list[str]] = defaultdict(list)
    for day in sorted(snapshots, reverse=True):
        for story in snapshots[day].stories:
            titles = out[story.id]
            if (
                FALLBACK_MARK not in story.title
                and story.title not in titles
                and len(titles) < TITLES_PER_STORY
            ):
                titles.append(story.title)
    return {sid: ts for sid, ts in out.items() if ts}


def recategorize(export: Path, dry_run: bool = False) -> Counter[str]:
    """Rewrite the categories in export/snapshots. Returns how many stories moved, by 'old -> new'."""
    files = sorted((export / "snapshots").glob("*.json"))
    if not files:
        raise SystemExit(f"No snapshots under {export}/snapshots")
    snapshots = {
        f.stem: c.Snapshot.model_validate(json.loads(f.read_text(encoding="utf-8"))) for f in files
    }
    verdicts = classify_titles(story_titles(snapshots))
    moved: Counter[str] = Counter()
    seen: set[str] = set()
    for snap in snapshots.values():
        for story in snap.stories:
            verdict = verdicts.get(story.id)
            if verdict is None or verdict[0] == story.category:
                continue
            if story.id not in seen:
                seen.add(story.id)
                moved[f"{story.category} -> {verdict[0]}"] += 1
            story.category = verdict[0]
    if not dry_run:
        for day, snap in snapshots.items():
            write_json(export, f"snapshots/{day}.json", snap)
    unsure = sum(conf < TITLE_CONFIDENCE_MIN for _, conf in verdicts.values())
    print(
        f"{len(verdicts)} stories read, {sum(moved.values())} changed category "
        f"({unsure} of the readings unsure, below {TITLE_CONFIDENCE_MIN})"
        + (" (dry run: nothing written)" if dry_run else "")
    )
    return moved


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--export", type=Path, default=config.EXPORT)
    p.add_argument("--dry-run", action="store_true", help="say what would change, write nothing")
    args = p.parse_args()
    for change, n in recategorize(args.export, args.dry_run).most_common():
        print(f"  {n:5d}  {change}")


if __name__ == "__main__":
    main()
