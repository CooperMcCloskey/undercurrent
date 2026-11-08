"""Read what the story tracker made: how big the stories are, and what is inside some of them.

    uv run python -m undercurrent.stories.preview --processed ~/preview --day 2026-11-02

Sizes can't say whether a story is a story. For one tracked day this prints how many headlines
joined a story and how the sizes fall, then the largest stories and some random ones, each with a
few of its headlines. Read them. A story should be one event, not a type of headline ("weather
for <city>", "<company> stock price up <n>%"), and the largest ones are where a cutoff that is
too low shows first.
"""

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.stories.track import ASSIGNMENTS, DAILY
from undercurrent.vectors import store


def last_day(processed: Path) -> dt.date:
    days = sorted(p.stem for p in (processed / ASSIGNMENTS).glob("*.parquet"))
    if not days:
        raise SystemExit(f"No tracked days in {processed / ASSIGNMENTS}: run stories.track first.")
    return dt.date.fromisoformat(days[-1])


def read_day(processed: Path, day: dt.date) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """(assignments, stories, headline text by id) of one tracked day."""
    name = f"{day:%Y-%m-%d}.parquet"
    path = processed / ASSIGNMENTS / name
    if not path.exists():
        raise SystemExit(f"{day} has not been tracked: {path} is missing.")
    assignments = pq.read_table(path).to_pandas()
    stories = pq.read_table(processed / DAILY / name).to_pandas().set_index("story_id")
    _, parquet = store.day_files(store.vectors_dir(processed), "news", day)
    table = pq.read_table(parquet).to_pydict()
    return assignments, stories, dict(zip(table["id"], table["text"], strict=True))


def size_summary(day: dt.date, total: int, sizes: pd.Series) -> list[str]:
    joined = int(sizes.sum())
    if joined == 0:
        return [f"{day}: {total:,} headlines, none in a story"]
    lo, hi = np.percentile(sizes, [50, 90])
    top = int(sizes.iloc[:10].sum())
    return [
        f"{day}: {total:,} headlines, {joined:,} ({joined / max(total, 1):.0%}) in {len(sizes):,} stories today",
        f"story size: median {lo:.0f}, 90% are {hi:.0f} or fewer, largest {int(sizes.iloc[0]):,}; "
        f"the 10 largest hold {top / joined:.0%} of the headlines in stories",
    ]


def pick(sizes: pd.Series, largest: int, random: int, min_size: int, rng) -> list[str]:
    """The `largest` stories, then `random` others of at least `min_size` headlines."""
    chosen = [str(s) for s in sizes.index[:largest]]
    pool = [str(s) for s in sizes.index[largest:] if sizes[s] >= min_size]
    if pool:
        chosen += [
            pool[i] for i in sorted(rng.choice(len(pool), min(random, len(pool)), replace=False))
        ]
    return chosen


def show(
    story_id: str, members: pd.DataFrame, info: pd.Series | None, texts: dict[str, str],
    day: dt.date, sample: int, rng,
) -> None:  # fmt: skip
    known = info is not None
    age = (day - info["first_seen"]).days if known and not pd.isna(info["first_seen"]) else 0
    category = info["category"] if known and not pd.isna(info["category"]) else "no category"
    print(
        f"\n[{len(members):,} headlines today, {age} days old, {category}, "
        f"mean similarity {members['similarity'].mean():.2f}]"
    )
    print(f"  {(info['label'] if info is not None else story_id)[:120]}")
    ids = members["article_id"].tolist()
    for i in sorted(rng.choice(len(ids), min(sample, len(ids)), replace=False)):
        print(f"   - {texts.get(ids[i], ids[i])[:110]}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--processed", type=Path, default=config.PROCESSED)
    p.add_argument("--day", type=dt.date.fromisoformat, help="default: the last tracked day")
    p.add_argument("--largest", type=int, default=8, help="how many of the largest stories to show")
    p.add_argument("--random", type=int, default=12, help="how many other stories to show")
    p.add_argument("--sample", type=int, default=5, help="headlines shown per story")
    p.add_argument("--min-size", type=int, default=3, help="smallest story picked at random")
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    day = args.day or last_day(args.processed)
    assignments, stories, texts = read_day(args.processed, day)
    sizes = assignments.groupby("story_id").size().sort_values(ascending=False, kind="stable")
    print("\n".join(size_summary(day, len(texts), sizes)))
    rng = np.random.default_rng(args.seed)
    by_story = dict(tuple(assignments.groupby("story_id")))
    for story_id in pick(sizes, args.largest, args.random, args.min_size, rng):
        info = stories.loc[story_id] if story_id in stories.index else None
        show(story_id, by_story[story_id], info, texts, day, args.sample, rng)


if __name__ == "__main__":
    main()
