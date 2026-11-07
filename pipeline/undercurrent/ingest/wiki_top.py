"""Daily top-1000 most-viewed articles on each Wikipedia language: a cheap bulk attention signal.

    uv run python -m undercurrent.ingest.wiki_top --days 60            # all 8 languages
    uv run python -m undercurrent.ingest.wiki_top --days 60 --langs en de

Raw JSON -> data/raw/wiki/top/<lang>/<access>/YYYY-MM-DD.json, access = all-access and desktop
            (skipped if already downloaded)
Table    -> data/processed/wiki_top.parquet
            lang, day, rank, title, views, desktop_share, suspect_bot

Cleaning, per language:
- drops the main page, non-article pages (Special:, 特別:, Spezial:, ... read from each site's
  namespace list) and the '-' bucket, then re-ranks;
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
    add_lang_arg,
    date_range,
    days_between,
    project,
    site_info,
)

RAW_DIR = RAW / "wiki" / "top"
ACCESS = ("all-access", "desktop")
OUT = PROCESSED / "wiki_top.parquet"
BOT_DESKTOP_SHARE = 0.9
ALWAYS_EXCLUDED = {"-", "Undefined"}


def is_article(title: str, info: dict) -> bool:
    """info is wiki_client.site_info(): {'main_page': ..., 'namespaces': [...]}."""
    if title in ALWAYS_EXCLUDED or title == info["main_page"]:
        return False
    ns, sep, _ = title.partition(":")
    return not (sep and ns in info["namespaces"])


def _views(payload: dict) -> dict[str, int]:
    return {a["article"]: int(a["views"]) for a in payload["items"][0]["articles"]}


def clean_top(
    lang: str, day: dt.date, all_access: dict, desktop: dict | None, info: dict
) -> pd.DataFrame:
    """Raw API payloads for one language and day -> cleaned, re-ranked, bot-flagged rows."""
    views = {t: v for t, v in _views(all_access).items() if is_article(t, info)}
    desk = _views(desktop) if desktop else {}
    df = pd.DataFrame({"title": list(views), "views": list(views.values())})
    df = df.sort_values("views", ascending=False, kind="stable").reset_index(drop=True)
    df.insert(0, "lang", lang)
    df.insert(1, "day", day)
    df.insert(2, "rank", range(1, len(df) + 1))
    df["desktop_share"] = (df["title"].map(desk) / df["views"]).astype("float64")
    df["suspect_bot"] = (df["desktop_share"] >= BOT_DESKTOP_SHARE).fillna(False).astype(bool)
    return df


def download(client: WikiClient, lang: str, access: str, day: dt.date) -> str:
    path = RAW_DIR / lang / access / f"{day.isoformat()}.json"
    if path.exists():
        return "cached"
    url = f"{REST}/metrics/pageviews/top/{project(lang)}/{access}/{day:%Y/%m/%d}"
    payload = client.get_json(url)
    if payload is None:
        return "not published yet"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return "downloaded"


def build_table(client: WikiClient) -> pd.DataFrame:
    frames = []
    for lang_dir in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()) if RAW_DIR.exists() else []:
        lang = lang_dir.name
        info = site_info(client, lang)
        for p in sorted((lang_dir / "all-access").glob("*.json")):
            desk = lang_dir / "desktop" / p.name
            frames.append(
                clean_top(
                    lang,
                    dt.date.fromisoformat(p.stem),
                    json.loads(p.read_text()),
                    json.loads(desk.read_text()) if desk.exists() else None,
                    info,
                )
            )
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    return df


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_date_args(p, default_days=7)
    add_lang_arg(p)
    args = p.parse_args()
    start, end = date_range(args)

    client = WikiClient()
    for lang in args.langs:
        counts: dict[str, int] = {}
        for day in days_between(start, end):
            for access in ACCESS:
                status = download(client, lang, access, day)
                counts[status] = counts.get(status, 0) + 1
        print(f"{lang} {start}..{end}: {counts}")

    df = build_table(client)
    client.close()
    if len(df):
        summary = df.groupby("lang").agg(
            days=("day", "nunique"), titles=("title", "nunique"), bots=("suspect_bot", "mean")
        )
        print(f"{OUT}: {len(df):,} rows\n{summary.to_string(float_format='{:.1%}'.format)}")


if __name__ == "__main__":
    main()
