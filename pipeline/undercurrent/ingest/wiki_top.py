"""Daily top-1000 most-viewed English Wikipedia articles: a cheap bulk attention signal.

    uv run python -m undercurrent.ingest.wiki_top --days 60

Raw JSON -> data/raw/wiki/top/<access>/YYYY-MM-DD.json for access in all-access and desktop
            (skipped if already downloaded)
Table    -> data/processed/wiki_top.parquet
            day, rank, title, views, desktop_share, suspect_bot

Cleaning:
- drops the Main Page, non-article namespaces (Special:, File:, ...) and the '-' bucket, then
  re-ranks;
- desktop_share = desktop views / all views (NaN when the title isn't in the desktop top-1000,
  which means its desktop share is low). Human traffic is mostly mobile; automated traffic that
  slips past Wikimedia's bot filter is almost all desktop, so suspect_bot = desktop_share >= 0.9.
  Rows are flagged, not dropped: filter with `~suspect_bot`.
The table is rebuilt from every raw file on disk, so runs accumulate.
"""

import argparse
import datetime as dt
import json

import pandas as pd

from undercurrent.config import PROCESSED, RAW
from undercurrent.ingest.wiki_client import (
    REST,
    WikiClient,
    add_date_args,
    date_range,
    days_between,
)

RAW_DIR = RAW / "wiki" / "top"
ACCESS = ("all-access", "desktop")
OUT = PROCESSED / "wiki_top.parquet"
BOT_DESKTOP_SHARE = 0.9

NON_ARTICLE_NAMESPACES = {
    "Special", "Wikipedia", "File", "Portal", "Help", "Talk", "User", "Template", "Category",
    "Draft", "Module", "MediaWiki", "Book", "TimedText", "Media", "Image",
    "User_talk", "Wikipedia_talk", "File_talk", "Template_talk", "Help_talk", "Category_talk",
    "Portal_talk", "Draft_talk", "Module_talk", "MediaWiki_talk",
}  # fmt: skip
EXCLUDED_TITLES = {"Main_Page", "-", "Undefined", "Wikipedia"}


def is_article(title: str) -> bool:
    if title in EXCLUDED_TITLES:
        return False
    ns, sep, _ = title.partition(":")
    return not (sep and ns in NON_ARTICLE_NAMESPACES)


def _views(payload: dict) -> dict[str, int]:
    return {a["article"]: int(a["views"]) for a in payload["items"][0]["articles"]}


def clean_top(day: dt.date, all_access: dict, desktop: dict | None) -> pd.DataFrame:
    """Raw API payloads for one day -> cleaned, re-ranked, bot-flagged rows."""
    views = {t: v for t, v in _views(all_access).items() if is_article(t)}
    desk = _views(desktop) if desktop else {}
    df = pd.DataFrame({"title": list(views), "views": list(views.values())})
    df = df.sort_values("views", ascending=False, kind="stable").reset_index(drop=True)
    df.insert(0, "day", day)
    df.insert(1, "rank", range(1, len(df) + 1))
    df["desktop_share"] = (df["title"].map(desk) / df["views"]).astype("float64")
    df["suspect_bot"] = (df["desktop_share"] >= BOT_DESKTOP_SHARE).fillna(False).astype(bool)
    return df


def download(client: WikiClient, access: str, day: dt.date) -> str:
    path = RAW_DIR / access / f"{day.isoformat()}.json"
    if path.exists():
        return "cached"
    url = f"{REST}/metrics/pageviews/top/en.wikipedia/{access}/{day:%Y/%m/%d}"
    payload = client.get_json(url)
    if payload is None:
        return "not published yet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return "downloaded"


def build_table() -> pd.DataFrame:
    frames = []
    for p in sorted((RAW_DIR / "all-access").glob("*.json")):
        desk = RAW_DIR / "desktop" / p.name
        frames.append(
            clean_top(
                dt.date.fromisoformat(p.stem),
                json.loads(p.read_text()),
                json.loads(desk.read_text()) if desk.exists() else None,
            )
        )
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    return df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_date_args(p, default_days=7)
    args = p.parse_args()
    start, end = date_range(args)

    client = WikiClient()
    counts: dict[str, int] = {}
    for day in days_between(start, end):
        for access in ACCESS:
            status = download(client, access, day)
            counts[status] = counts.get(status, 0) + 1
    client.close()

    df = build_table()
    print(f"{start}..{end}: {counts}")
    if len(df):
        print(
            f"{OUT}: {len(df):,} rows, {df['day'].nunique()} days, "
            f"{df['title'].nunique():,} titles, {df['suspect_bot'].mean():.1%} flagged as bots"
        )


if __name__ == "__main__":
    main()
