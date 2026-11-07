"""Daily pageviews and edit counts for entities, across the Wikipedia languages, cached per article.

    uv run python -m undercurrent.ingest.wiki --titles-file data/processed/wiki_titles.txt --days 365
    uv run python -m undercurrent.ingest.wiki --titles Barack_Obama NATO --days 14 --langs en de

Input is English titles (what wiki_resolve writes). Each one is linked through Wikidata to its
article in every requested language, and every (language, article) pair is fetched.

Per-article cache -> data/raw/wiki/{pageviews,edits}/<hash>.json
Tables -> data/processed/wiki_pageviews.parquet  lang, title, day, views, complete
          data/processed/wiki_edits.parquet      lang, title, day, edits, complete
          data/processed/wiki_entity_daily.parquet  one row per (entity, day), summed over
              languages: qid, title_en, day, views, edits, n_langs, views_<lang>..., edits_<lang>...

Tables are rebuilt from every cached article, so runs accumulate. Days with no views/edits are 0.
Pageviews are published with about a day's lag, so `complete` is False for the most recent
day or two; those rows are refetched on the next run. wiki_entity_daily uses complete rows only.

Point-in-time: a row for day d only describes day d (UTC), so as-of-t features filter day <= t.
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
    LANGUAGES,
    REST,
    WikiClient,
    action_url,
    add_date_args,
    add_lang_arg,
    date_range,
    days_between,
    project,
    title_path,
    utc_today,
)
from undercurrent.ingest.wiki_resolve import link_languages

# Standard history length: a year of daily numbers per article. Pageviews cost one request per
# article whatever the range; edits need about one extra request per 500 edits. The CLI default
# stays short (team rule: no months of downloads by default), so pass --days HISTORY_DAYS.
HISTORY_DAYS = 365

KINDS = {
    # kind: (value column, days to wait before a day's count is trusted as complete)
    "pageviews": ("views", 2),
    "edits": ("edits", 1),
}


def cache_path(kind: str, lang: str, title: str) -> Path:
    h = hashlib.sha1(f"{lang}:{title}".encode()).hexdigest()[:16]
    return RAW / "wiki" / kind / f"{h}.json"


def fetch_pageviews(
    client: WikiClient, lang: str, title: str, start: dt.date, end: dt.date
) -> dict[str, int]:
    url = (
        f"{REST}/metrics/pageviews/per-article/{project(lang)}/all-access/user/"
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


def fetch_edits(
    client: WikiClient, lang: str, title: str, start: dt.date, end: dt.date
) -> dict[str, int]:
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
        payload = client.get_json(action_url(lang), params=params) or {}
        for page in (payload.get("query") or {}).get("pages", []):
            timestamps += [r["timestamp"] for r in page.get("revisions", [])]
        if "continue" not in payload:
            break
        params = {**params, **payload["continue"]}
    return count_by_day(timestamps)


FETCHERS = {"pageviews": fetch_pageviews, "edits": fetch_edits}


def update_title(
    client: WikiClient, kind: str, lang: str, title: str, start: dt.date, end: dt.date
) -> str:
    """Make sure the cache for one article covers [start, end]. Returns a status word."""
    path = cache_path(kind, lang, title)
    cached = json.loads(path.read_text()) if path.exists() else None
    if cached and cached["start"] <= start.isoformat() and cached["complete_to"] >= end.isoformat():
        return "cached"
    if cached:  # refetch the union in one request rather than stitching ranges
        start = min(start, dt.date.fromisoformat(cached["start"]))
        end = max(end, dt.date.fromisoformat(cached["end"]))
    daily = FETCHERS[kind](client, lang, title, start, end)
    safe = utc_today() - dt.timedelta(days=KINDS[kind][1])
    record = {
        "lang": lang,
        "title": title,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "complete_to": min(end, safe).isoformat(),
        "daily": daily,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False))
    return "fetched"


def record_rows(record: dict, value_col: str) -> list[dict]:
    """Zero-fill [start, complete_to]; after that keep only days the API actually reported."""
    start = dt.date.fromisoformat(record["start"])
    complete_to = dt.date.fromisoformat(record["complete_to"])
    daily = record["daily"]
    days = {d.isoformat() for d in days_between(start, complete_to)} | set(daily)
    return [
        {
            "lang": record["lang"],
            "title": record["title"],
            "day": dt.date.fromisoformat(d),
            value_col: int(daily.get(d, 0)),
            "complete": d <= record["complete_to"],
        }
        for d in sorted(days)
    ]


def build_table(kind: str) -> pd.DataFrame:
    value_col = KINDS[kind][0]
    rows = []
    for p in sorted((RAW / "wiki" / kind).glob("*.json")):
        rows += record_rows(json.loads(p.read_text()), value_col)
    df = pd.DataFrame(rows, columns=["lang", "title", "day", value_col, "complete"])
    out = PROCESSED / f"wiki_{kind}.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return df


def build_entity_table(
    views: pd.DataFrame, edits: pd.DataFrame, links: dict[str, dict], langs: list[str]
) -> pd.DataFrame:
    """Sum complete per-language rows into one row per (entity, day).

    links is link_languages() output. Entities with no Wikidata item get qid "en:<title>".
    """
    owner = pd.DataFrame(
        [
            {"lang": lang, "title": title, "qid": e["qid"] or f"en:{en}", "title_en": en}
            for en, e in links.items()
            for lang, title in e["titles"].items()
            if lang in langs
        ],
        columns=["lang", "title", "qid", "title_en"],
    ).drop_duplicates(["lang", "title"])
    n_langs = owner.groupby("qid")["lang"].nunique().rename("n_langs")

    def wide(df: pd.DataFrame, col: str) -> pd.DataFrame:
        df = df[df["complete"]].merge(owner, on=["lang", "title"])
        per_lang = df.pivot_table(
            index=["qid", "title_en", "day"], columns="lang", values=col, aggfunc="sum"
        )
        per_lang.columns = [f"{col}_{lang}" for lang in per_lang.columns]
        per_lang.insert(0, col, per_lang.sum(axis=1, min_count=1))
        return per_lang

    out = wide(views, "views").join(wide(edits, "edits"), how="outer").reset_index()
    out = out.merge(n_langs, on="qid", how="left")
    lead = ["qid", "title_en", "day", "views", "edits", "n_langs"]
    tail = [f"{c}_{lang}" for c in ("views", "edits") for lang in langs if f"{c}_{lang}" in out]
    out = out[lead + tail].sort_values(["qid", "day"]).reset_index(drop=True)
    path = PROCESSED / "wiki_entity_daily.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(path, index=False)
    return out


def fetch(
    titles: list[str],
    start: dt.date,
    end: dt.date,
    kinds: list[str] | tuple[str, ...] = tuple(KINDS),
    langs: list[str] | tuple[str, ...] = LANGUAGES,
    workers: int = 3,
) -> None:
    """Python entry point: link English titles to every language, fetch what's missing, then
    rebuild the tables. Safe to re-run."""
    langs = list(dict.fromkeys(["en", *langs]))
    links = link_languages(titles, langs)
    jobs = sorted({(lang, t) for e in links.values() for lang, t in e["titles"].items()})
    print(f"{len(links)} entities -> {len(jobs)} articles in {len(langs)} languages")

    client = WikiClient()
    for kind in kinds:
        statuses: Counter[str] = Counter()

        def work(job: tuple[str, str], kind: str = kind) -> str:
            lang, title = job
            try:
                return update_title(client, kind, lang, title, start, end)
            except RuntimeError as e:
                print(f"  failed {kind} {lang}:{title}: {e}")
                return "failed"

        with ThreadPoolExecutor(workers) as pool:
            for status in tqdm(pool.map(work, jobs), total=len(jobs), desc=kind):
                statuses[status] += 1
        df = build_table(kind)
        print(f"{kind}: {dict(statuses)} -> {len(df):,} rows")
    client.close()

    all_links = link_languages(_all_cached_en_titles(), langs)
    ent = build_entity_table(_table("pageviews"), _table("edits"), all_links, langs)
    print(f"wiki_entity_daily: {len(ent):,} rows, {ent['qid'].nunique():,} entities")


def _table(kind: str) -> pd.DataFrame:
    path = PROCESSED / f"wiki_{kind}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return pd.DataFrame(columns=["lang", "title", "day", KINDS[kind][0], "complete"])


def _all_cached_en_titles() -> list[str]:
    """English titles of every article we have cached, so the entity table covers all runs."""
    titles = set()
    for kind in KINDS:
        for p in (RAW / "wiki" / kind).glob("*.json"):
            rec = json.loads(p.read_text())
            if rec["lang"] == "en":
                titles.add(rec["title"])
    return sorted(titles)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--titles", nargs="*", default=[], help="English Wikipedia titles")
    p.add_argument("--titles-file", type=Path, help="one English title per line")
    p.add_argument("--what", nargs="+", choices=list(KINDS), default=list(KINDS))
    add_lang_arg(p)
    add_date_args(p, default_days=7)
    args = p.parse_args()

    titles = list(args.titles)
    if args.titles_file:
        titles += [ln.strip() for ln in args.titles_file.read_text().splitlines() if ln.strip()]
    if not titles:
        p.error("give --titles or --titles-file")
    start, end = date_range(args)
    print(f"{len(set(titles))} titles, {start}..{end}, languages: {' '.join(args.langs)}")
    fetch(titles, start, end, args.what, args.langs)


if __name__ == "__main__":
    main()
