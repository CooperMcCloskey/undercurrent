"""Daily pageviews and edit counts for given English Wikipedia titles, cached per title.

    uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_titles.txt --days 60
    uv run python -m undercurrent.ingest.wiki --titles Barack_Obama NATO --days 14 --what pageviews

Per-title cache -> data/raw/wiki/{pageviews,edits}/<hash>.json
Tables          -> data/processed/wiki_pageviews.parquet  (title, day, views)
                   data/processed/wiki_edits.parquet      (title, day, edits)

Tables are rebuilt from every cached title, so runs accumulate. Days with no views/edits are
filled with 0 within the range we know is complete; pageviews are published with about a day's
lag, so the last day or two are refetched on the next run instead of being trusted.

Point-in-time: a row for day d only describes day d (UTC), so as-of-t features just filter
day <= t.
"""

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from tqdm import tqdm

from undercurrent.config import PROCESSED, RAW
from undercurrent.ingest.wiki_client import (
    ACTION,
    REST,
    WikiClient,
    add_date_args,
    date_range,
    days_between,
    normalise_title,
    title_path,
    utc_today,
)

KINDS = {
    # kind: (value column, days to wait before a day's count is trusted as complete)
    "pageviews": ("views", 2),
    "edits": ("edits", 1),
}


def cache_path(kind: str, title: str) -> Path:
    h = hashlib.sha1(title.encode()).hexdigest()[:16]
    return RAW / "wiki" / kind / f"{h}.json"


def fetch_pageviews(client: WikiClient, title: str, start: dt.date, end: dt.date) -> dict[str, int]:
    url = (
        f"{REST}/metrics/pageviews/per-article/en.wikipedia/all-access/user/"
        f"{title_path(title)}/daily/{start:%Y%m%d}00/{end:%Y%m%d}00"
    )
    payload = client.get_json(url)  # 404: no such page, or no views in range
    items = (payload or {}).get("items", [])
    return {
        f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}-{i['timestamp'][6:8]}": i["views"]
        for i in items
    }


def count_by_day(timestamps: list[str]) -> dict[str, int]:
    """['2026-11-01T10:00:00Z', ...] -> {'2026-11-01': n, ...}"""
    return dict(Counter(ts[:10] for ts in timestamps))


def fetch_edits(client: WikiClient, title: str, start: dt.date, end: dt.date) -> dict[str, int]:
    params: dict = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "prop": "revisions",
        "titles": title.replace("_", " "),
        "redirects": 1,
        "rvprop": "timestamp",
        "rvlimit": "max",
        "rvdir": "newer",
        "rvstart": f"{start}T00:00:00Z",
        "rvend": f"{end}T23:59:59Z",
    }
    timestamps: list[str] = []
    while True:
        payload = client.get_json(ACTION, params=params) or {}
        for page in (payload.get("query") or {}).get("pages", []):
            timestamps += [r["timestamp"] for r in page.get("revisions", [])]
        if "continue" not in payload:
            break
        params = {**params, **payload["continue"]}
    return count_by_day(timestamps)


FETCHERS = {"pageviews": fetch_pageviews, "edits": fetch_edits}


def update_title(client: WikiClient, kind: str, title: str, start: dt.date, end: dt.date) -> str:
    """Make sure the cache for one title covers [start, end]. Returns a status word."""
    path = cache_path(kind, title)
    cached = json.loads(path.read_text()) if path.exists() else None
    if cached and cached["start"] <= start.isoformat() and cached["complete_to"] >= end.isoformat():
        return "cached"
    if cached:  # refetch the union in one request rather than stitching ranges
        start = min(start, dt.date.fromisoformat(cached["start"]))
        end = max(end, dt.date.fromisoformat(cached["end"]))
    daily = FETCHERS[kind](client, title, start, end)
    safe = utc_today() - dt.timedelta(days=KINDS[kind][1])
    record = {
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "complete_to": min(end, safe).isoformat(),
        "daily": daily,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record))
    return "fetched"


def record_rows(record: dict, value_col: str) -> list[dict]:
    """Zero-fill [start, complete_to]; after that keep only days the API actually reported."""
    start = dt.date.fromisoformat(record["start"])
    complete_to = dt.date.fromisoformat(record["complete_to"])
    daily = record["daily"]
    days = {d.isoformat() for d in days_between(start, complete_to)} | set(daily)
    return [
        {"title": record["title"], "day": dt.date.fromisoformat(d), value_col: int(daily.get(d, 0))}
        for d in sorted(days)
    ]


def build_table(kind: str) -> pd.DataFrame:
    value_col = KINDS[kind][0]
    rows = []
    for p in sorted((RAW / "wiki" / kind).glob("*.json")):
        rows += record_rows(json.loads(p.read_text()), value_col)
    df = pd.DataFrame(rows, columns=["title", "day", value_col])
    out = PROCESSED / f"wiki_{kind}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return df


def fetch(
    titles: list[str], start: dt.date, end: dt.date, kinds: list[str], workers: int = 6
) -> None:
    """Python entry point: fetch what's missing for these titles, then rebuild the tables."""
    titles = sorted({normalise_title(t) for t in titles if t and t.strip()})
    client = WikiClient(max_rps=10)
    for kind in kinds:
        statuses: Counter[str] = Counter()

        def work(title: str, kind: str = kind) -> str:
            try:
                return update_title(client, kind, title, start, end)
            except RuntimeError as e:
                print(f"  failed {kind} {title!r}: {e}")
                return "failed"

        with ThreadPoolExecutor(workers) as pool:
            for status in tqdm(pool.map(work, titles), total=len(titles), desc=kind):
                statuses[status] += 1
        df = build_table(kind)
        print(f"{kind}: {dict(statuses)} -> {len(df):,} rows, {df['title'].nunique():,} titles")
    client.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--titles", nargs="*", default=[], help="Wikipedia titles")
    p.add_argument("--titles-file", type=Path, help="one title per line")
    p.add_argument("--what", nargs="+", choices=list(KINDS), default=list(KINDS))
    add_date_args(p, default_days=7)
    args = p.parse_args()

    titles = list(args.titles)
    if args.titles_file:
        titles += [ln.strip() for ln in args.titles_file.read_text().splitlines() if ln.strip()]
    if not titles:
        p.error("give --titles or --titles-file")
    start, end = date_range(args)
    print(f"{len(set(titles))} titles, {start}..{end}")
    fetch(titles, start, end, args.what)


if __name__ == "__main__":
    main()
