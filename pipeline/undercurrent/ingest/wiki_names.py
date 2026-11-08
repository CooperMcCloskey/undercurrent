"""Which GDELT names to fetch Wikipedia data for: each week's most-mentioned people and orgs.

    uv run python -m undercurrent.ingest.wiki_names                                   # local articles
    uv run python -m undercurrent.ingest.wiki_names --bucket gs://undercurrent-data-cooper

Step 1, count (resumable, one file per day). For every day of articles, count how often each
person and organisation is mentioned, weighting each article by `dup_count` (the copies it stands
for). Days already counted are skipped, so run it again as more days land in the bucket.
    -> data/processed/wiki_name_counts/YYYY-MM-DD.parquet   day, kind, name, mentions, articles
With --bucket, each day's articles file is copied from the bucket with `gcloud storage cp`,
counted and deleted again, so the laptop never holds more than one day (about 55 MB). Downloads
from the bucket to a machine outside Google Cloud are billed (about $0.12/GB, so about $7 for two
years). On a Google Cloud VM in us-central1 they're free.

Step 2, select (always rebuilt from every count file). Per ISO week (Monday to Sunday) and kind,
rank names by mentions and keep the top --top. The top --tier1 of them are tier 1, the rest
tier 2 (README.md, "Wikipedia: what the data agent (A) should do now"):
    tier 1: pageviews in all 8 languages, plus English edits
    tier 2: English pageviews only
    -> data/processed/wiki_names.parquet   week, kind, name, mentions, rank, tier

Point-in-time: a name is chosen by its mentions in one week only, never by what happened later.
But its pageviews are then fetched for the whole range, including weeks before it was chosen.
`first_week` in wiki_selection.parquet (wiki_bulk build) says when it was first chosen; a
forecast made on day t should only use entities whose first_week ended by t.

Then hand wiki_names.parquet to every machine and run `wiki_bulk fetch --shard i/n` on each.
"""

import argparse
import datetime as dt
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from tqdm import tqdm

from undercurrent.config import PROCESSED
from undercurrent.ingest.wiki_resolve import normalise_name

ARTICLES = PROCESSED / "articles"
COUNTS = PROCESSED / "wiki_name_counts"
OUT = PROCESSED / "wiki_names.parquet"
KINDS = ("persons", "orgs")
KEEP_PER_DAY = 5000  # per kind; a weekly top-50 name is always far inside a day's top 5,000
DAY_DIR = re.compile(r"day=(\d{4}-\d{2}-\d{2})/?$")


# Step 1: count ---------------------------------------------------------------------------------


def count_day(articles: pd.DataFrame, day: dt.date) -> pd.DataFrame:
    """One day's articles (persons, orgs, dup_count) -> mentions per (kind, name)."""
    frames = []
    for kind in KINDS:
        df = articles[[kind, "dup_count"]].explode(kind).dropna(subset=[kind])
        df = df.assign(name=df[kind].map(normalise_name))
        df = df[df["name"].str.len() > 1]
        g = (
            df.groupby("name")
            .agg(mentions=("dup_count", "sum"), articles=("dup_count", "size"))
            .sort_values(["mentions", "articles"], ascending=False)
            .head(KEEP_PER_DAY)
            .reset_index()
        )
        g.insert(0, "kind", kind)
        frames.append(g)
    out = pd.concat(frames, ignore_index=True)
    out.insert(0, "day", day)
    return out.astype({"mentions": "int64", "articles": "int64"})


def count_path(day: dt.date) -> Path:
    return COUNTS / f"{day.isoformat()}.parquet"


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(path)


def _read_articles(path: Path) -> pd.DataFrame:
    return pq.read_table(path, columns=["persons", "orgs", "dup_count"]).to_pandas()


def local_days(root: Path) -> dict[dt.date, Path]:
    out = {}
    for d in root.glob("day=*"):
        m = DAY_DIR.search(d.as_posix())
        if m and (d / "part.parquet").exists():
            out[dt.date.fromisoformat(m.group(1))] = d / "part.parquet"
    return out


def bucket_days(bucket: str) -> dict[dt.date, str]:
    """gs://BUCKET -> {day: gs://BUCKET/processed/articles/day=.../part.parquet}."""
    if not shutil.which("gcloud"):
        raise SystemExit(
            "--bucket needs the gcloud CLI (https://cloud.google.com/sdk/docs/install)"
        )
    root = f"{bucket.rstrip('/')}/processed/articles/"
    ls = subprocess.run(["gcloud", "storage", "ls", root], capture_output=True, text=True)
    if ls.returncode != 0:
        raise SystemExit(f"gcloud storage ls {root} failed:\n{ls.stderr.strip()}")
    out = {}
    for line in ls.stdout.split():
        m = DAY_DIR.search(line)
        if m:
            out[dt.date.fromisoformat(m.group(1))] = f"{line.rstrip('/')}/part.parquet"
    return out


def count_days(
    sources: dict[dt.date, Path | str], start: dt.date | None, end: dt.date | None
) -> list[str]:
    """Count every day in range that has no count file yet. Returns the days that failed."""
    todo = sorted(
        d
        for d in sources
        if (start is None or d >= start)
        and (end is None or d <= end)
        and not count_path(d).exists()
    )
    print(f"{len(sources)} days of articles found, {len(todo)} still to count")
    failed = []
    with tempfile.TemporaryDirectory() as tmp:
        for day in tqdm(todo, desc="count"):
            src = sources[day]
            if isinstance(src, str):  # gs:// URL: copy one day, count it, delete it
                local = Path(tmp) / "part.parquet"
                cp = subprocess.run(
                    ["gcloud", "storage", "cp", src, str(local)], capture_output=True, text=True
                )
                if cp.returncode != 0:
                    print(f"  {day}: copy failed: {cp.stderr.strip()[-200:]}")
                    failed.append(day.isoformat())
                    continue
                df = _read_articles(local)
                local.unlink()
            else:
                df = _read_articles(src)
            _write(count_day(df, day), count_path(day))
    return failed


# Step 2: select --------------------------------------------------------------------------------


def week_of(day: pd.Series) -> pd.Series:
    """The Monday that starts each day's ISO week."""
    d = pd.to_datetime(day)
    return (d - pd.to_timedelta(d.dt.day_of_week, unit="D")).dt.date


def select(counts: pd.DataFrame, top: int, tier1: int) -> pd.DataFrame:
    """Daily counts -> each week's top `top` names per kind, with rank and tier."""
    df = counts.assign(week=week_of(counts["day"]))
    weekly = df.groupby(["week", "kind", "name"], as_index=False).agg(mentions=("mentions", "sum"))
    weekly = weekly.sort_values(
        ["week", "kind", "mentions", "name"], ascending=[True, True, False, True]
    )
    weekly["rank"] = weekly.groupby(["week", "kind"]).cumcount() + 1
    weekly = weekly[weekly["rank"] <= top].copy()
    weekly["tier"] = (weekly["rank"] > tier1).astype("int8") + 1
    return weekly.reset_index(drop=True)[["week", "kind", "name", "mentions", "rank", "tier"]]


def entities(names: pd.DataFrame) -> pd.DataFrame:
    """wiki_names rows -> one row per name: best tier, first week chosen, total mentions."""
    return (
        names.groupby("name", as_index=False)
        .agg(
            kind=("kind", "first"),
            tier=("tier", "min"),
            first_week=("week", "min"),
            mentions=("mentions", "sum"),
        )
        .sort_values(["tier", "mentions"], ascending=[True, False])
        .reset_index(drop=True)
    )


def estimate_requests(ents: pd.DataFrame, days: int, langs: int = 8) -> dict[str, int]:
    """Rough Wikimedia request counts (the rate limit is about 190 a minute per IP address)."""
    n1 = int((ents["tier"] == 1).sum())
    n2 = len(ents) - n1
    resolve = int(len(ents) * 0.45)  # batched exact lookups plus a search for ~40% of names
    link = 2 * (len(ents) + 49) // 50
    views = int(n1 * langs * 0.8) + n2  # not every entity has an article in every language
    edits = int(n1 * max(1, days / 300))  # busy articles need several pages of revisions
    top = 8 * 2 * days
    return {"resolve": resolve, "link": link, "pageviews": views, "edits": edits, "top1000": top}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--articles", type=Path, default=ARTICLES, help="local articles/ folder")
    p.add_argument("--bucket", help="read articles from gs://BUCKET/processed/articles instead")
    p.add_argument("--start", type=dt.date.fromisoformat, help="first day to count")
    p.add_argument("--end", type=dt.date.fromisoformat, help="last day to count")
    p.add_argument("--top", type=int, default=50, help="names kept per kind per week (default 50)")
    p.add_argument("--tier1", type=int, default=15, help="of those, tier 1 (default 15)")
    p.add_argument("--select-only", action="store_true", help="skip counting; just re-select")
    args = p.parse_args()

    failed: list[str] = []
    if not args.select_only:
        sources: dict[dt.date, Path | str]
        sources = dict(bucket_days(args.bucket)) if args.bucket else dict(local_days(args.articles))
        failed = count_days(sources, args.start, args.end)

    files = sorted(COUNTS.glob("*.parquet"))
    if not files:
        raise SystemExit(f"no count files in {COUNTS}: nothing to select")
    counts = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    names = select(counts, args.top, args.tier1)
    _write(names, OUT)

    ents = entities(names)
    days = counts["day"].nunique()
    est = estimate_requests(ents, days=(dt.date.today() - dt.date(2024, 10, 10)).days)
    print(
        f"{OUT}: {days} days ({counts['day'].min()}..{counts['day'].max()}), "
        f"{names['week'].nunique()} weeks, {len(ents):,} distinct names "
        f"({(ents['tier'] == 1).sum():,} tier 1, {(ents['tier'] == 2).sum():,} tier 2)"
    )
    total = sum(est.values())
    print(
        f"Estimated Wikimedia requests for the whole range: {est}, total {total:,}: "
        f"about {total / 190 / 60:.1f} h from one IP address"
    )
    print("Top tier-1 names:", ", ".join(ents["name"].head(15)))
    if failed:
        raise SystemExit(f"{len(failed)} day(s) failed to copy; run again to retry: {failed[:5]}")


if __name__ == "__main__":
    main()
