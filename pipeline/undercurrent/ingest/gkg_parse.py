"""Parse GDELT GKG 2.1 files (English + translated feed) into `data/processed/articles/`.

Streams one file at a time: download -> parse -> staging part -> delete the zip. When every
expected file of a day is staged for **both** feeds, the day is deduplicated across both feeds
and written atomically to `processed/articles/day=YYYY-MM-DD/articles.parquet` (schema:
docs/WORKPLAN.md section 3.1). Read it with:

    duckdb.read_parquet("data/processed/articles/**/*.parquet", hive_partitioning=True)
    # or: from undercurrent.ingest.gkg_parse import read_articles

Resumable: each feed's per-day staging under `processed/gdelt_staging/<feed>/<day>/` records
which 15-minute files are done, so a rerun only fetches what's missing, and a day with only
one feed done finishes correctly. Days run newest first.

    uv run python -m undercurrent.ingest.gkg_parse --days 1                       # yesterday, full
    uv run python -m undercurrent.ingest.gkg_parse --days 7 --max-disk-gb 10      # last 7, full
    uv run python -m undercurrent.ingest.gkg_parse --start 2026-09-08 --end 2026-10-30 \
        --sample-per-hour 1 --max-disk-gb 10                                     # hourly backfill
    uv run python -m undercurrent.ingest.gkg_parse --start 2026-05-11 --end 2026-11-06 \
        --workers 4 --max-disk-gb 30                         # 180 full days, 4 processes (VPS)

Disk holds only parquet (~75 MB per full day for both feeds; zips are deleted), so on a big
machine the limit is download bandwidth: a full day is ~1.3 GB of zips.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import time
import unicodedata
import zipfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.ingest import gdelt_download as dl
from undercurrent.ingest.fips import to_iso2

ARTICLES = config.PROCESSED / "articles"
STAGING = config.PROCESSED / "gdelt_staging"
LOGS = config.PROCESSED / "gdelt_logs"

log = logging.getLogger("gdelt")

# GKG 2.1 column positions (tab-separated, no header, 27 columns)
C_DATE, C_COLLECTION, C_DOMAIN, C_URL = 1, 2, 3, 4
C_COUNTS, C_THEMES, C_LOC_V1, C_LOC_V2 = 5, 7, 9, 10
C_PERSONS, C_ORGS, C_TONE = 11, 13, 15
C_ALL_NAMES, C_TRANSLATION, C_EXTRAS = 23, 25, 26
N_COLS = 27
USECOLS = [
    C_DATE,
    C_COLLECTION,
    C_DOMAIN,
    C_URL,
    C_COUNTS,
    C_THEMES,
    C_LOC_V1,
    C_LOC_V2,
    C_PERSONS,
    C_ORGS,
    C_TONE,
    C_ALL_NAMES,
    C_TRANSLATION,
    C_EXTRAS,
]

LOCATION = pa.struct(
    [
        ("name", pa.string()),
        ("country", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("type", pa.int32()),
    ]
)
_COMMON = [
    ("id", pa.string()),
    ("url", pa.string()),
    ("domain", pa.string()),
    ("published_at", pa.timestamp("us", tz="UTC")),
    ("day", pa.date32()),
    ("title", pa.string()),
    ("locations", pa.list_(LOCATION)),
    ("persons", pa.list_(pa.string())),
    ("orgs", pa.list_(pa.string())),
    ("themes", pa.list_(pa.string())),
    ("tone", pa.float64()),
]
_FEED_COLS = [("translated", pa.bool_()), ("lang", pa.string())]
# The rest of V1.5Tone (`tone` above is its first value). For translated-feed rows GDELT
# scores the machine translation, so compare tone within a feed, not across feeds.
_TONE_EXTRA = [
    ("tone_pos", pa.float64()),  # positive score (% of words)
    ("tone_neg", pa.float64()),  # negative score (% of words)
    ("tone_polarity", pa.float64()),  # how emotionally charged, in either direction
    ("tone_activity", pa.float64()),  # activity reference density
    ("tone_selfref", pa.float64()),  # self/group reference density
    ("word_count", pa.int32()),
]
TONE_FIELDS = ["tone"] + [name for name, _ in _TONE_EXTRA]
# V1Counts: `TYPE#NUMBER#OBJECT#LOCTYPE#LOCNAME#COUNTRY#ADM1#LAT#LON#FEATUREID`,
# e.g. PROTEST#20##1#Paraguay#PA#PA#-22.99#-57.99#PA. Location fields are null if absent.
COUNT = pa.struct(
    [
        ("type", pa.string()),  # KILL, ARREST, PROTEST, AFFECT, WOUND, ...
        ("number", pa.int64()),
        ("object", pa.string()),  # what was counted, e.g. "people"; often empty
        ("location_type", pa.int32()),
        ("location", pa.string()),
        ("country", pa.string()),  # ISO alpha-2
        ("lat", pa.float64()),
        ("lon", pa.float64()),
    ]
)
_EXTRA_ENTITY_COLS = [
    # V2.1AllNames: every proper name GDELT found (people, orgs, places, events), deduped.
    # Noisier than persons/orgs but much better coverage on the translated feed.
    ("all_names", pa.list_(pa.string())),
    ("counts", pa.list_(COUNT)),
]
# Final output: docs/WORKPLAN.md section 3.1, plus the added columns at the end
ARTICLE_SCHEMA = pa.schema(
    _COMMON
    + [("dup_count", pa.int32()), ("sampled", pa.bool_())]
    + _FEED_COLS
    + _TONE_EXTRA
    + _EXTRA_ENTITY_COLS
)
# Staging: one row per parsed GKG record, before cross-feed dedup
STAGING_SCHEMA = pa.schema(
    _COMMON + _FEED_COLS + _TONE_EXTRA + _EXTRA_ENTITY_COLS + [("gdelt_file", pa.string())]
)

_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_SRCLC_RE = re.compile(r"srclc:([A-Za-z]+)")
_NON_WORD = re.compile(r"[\W_]+")


# --- parsing one file (pure) ---------------------------------------------------------------


def article_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def extract_title(extras: str) -> str:
    m = _TITLE_RE.search(extras)
    return " ".join(html.unescape(m.group(1)).split()) if m else ""


def normalise_title(title: str) -> str:
    """Key for folding syndicated copies: NFKC, casefolded, punctuation collapsed."""
    t = unicodedata.normalize("NFKC", title).casefold()
    return _NON_WORD.sub(" ", t).strip()


def _split(field: str) -> list[str]:
    return list(dict.fromkeys(x for x in field.split(";") if x)) if field else []


def parse_locations(v1: str, v2: str) -> list[dict]:
    """V1Locations (`type#name#cc#adm1#lat#lon#fid`), else V2EnhancedLocations
    (`type#name#cc#adm1#adm2#lat#lon#fid#offset`). Entries without coordinates are dropped;
    FIPS country codes become ISO alpha-2. One entry per (type, name)."""
    if v1:
        entries, lat_i = v1, 4
    elif v2:
        entries, lat_i = v2, 5
    else:
        return []
    out: dict[tuple[int, str], dict] = {}
    for entry in entries.split(";"):
        f = entry.split("#")
        if len(f) < lat_i + 2:
            continue
        try:
            typ, lat, lon = int(f[0]), float(f[lat_i]), float(f[lat_i + 1])
        except ValueError:
            continue
        out.setdefault(
            (typ, f[1]),
            {"name": f[1], "country": to_iso2(f[2]), "lat": lat, "lon": lon, "type": typ},
        )
    return list(out.values())


def parse_all_names(field: str) -> list[str]:
    """V2.1AllNames `Name,offset;Name,offset` -> distinct names in first-seen order."""
    if not field:
        return []
    names = (entry.rsplit(",", 1)[0].strip() for entry in field.split(";"))
    return list(dict.fromkeys(n for n in names if n))


def _num(s: str, kind: type) -> float | int | None:
    try:
        return kind(float(s)) if kind is int else float(s)
    except ValueError:
        return None


def parse_counts(field: str) -> list[dict]:
    """V1Counts -> list of COUNT structs (identical entries collapsed)."""
    out: dict[str, dict] = {}
    for entry in field.split(";") if field else []:
        f = entry.split("#")
        if len(f) < 9 or not f[0]:
            continue
        number = _num(f[1], int)
        if number is None:
            continue
        out.setdefault(
            entry,
            {
                "type": f[0],
                "number": number,
                "object": f[2] or None,
                "location_type": _num(f[3], int) if f[3] else None,
                "location": f[4] or None,
                "country": to_iso2(f[5]),
                "lat": _num(f[7], float) if f[7] else None,
                "lon": _num(f[8], float) if f[8] else None,
            },
        )
    return list(out.values())


def parse_tone(field: str) -> list[float | int | None]:
    """V1.5Tone -> [tone, pos, neg, polarity, activity, selfref, word_count]; None if missing."""
    parts = field.split(",") if field else []
    out: list[float | int | None] = []
    for i in range(len(TONE_FIELDS)):
        try:
            v = float(parts[i])
            out.append(int(v) if i == len(TONE_FIELDS) - 1 else v)
        except (IndexError, ValueError):
            out.append(None)
    return out


def parse_gkg_bytes(raw: bytes, feed: str, gdelt_file: str) -> tuple[pa.Table, dict]:
    """Unzipped GKG bytes -> (staging table, stats). Drops non-web records and rows with no
    title."""
    df = pd.read_csv(
        io.BytesIO(raw),
        sep="\t",
        header=None,
        names=range(N_COLS),
        usecols=USECOLS,
        dtype=str,
        quoting=csv.QUOTE_NONE,
        keep_default_na=False,
        encoding="utf-8",
        encoding_errors="replace",
        on_bad_lines="skip",
        engine="c",
    )
    translated = feed == "translation"
    stats = Counter(lines=raw.count(b"\n"), records=len(df))
    cols: dict[str, list] = {name: [] for name in STAGING_SCHEMA.names}
    for r in df.itertuples(index=False, name=None):
        rec = dict(zip(USECOLS, r, strict=True))
        url = rec[C_URL]
        if rec[C_COLLECTION] != "1" or not url.startswith("http"):
            stats["non_web"] += 1
            continue
        title = extract_title(rec[C_EXTRAS])
        if not title:
            stats["no_title"] += 1
            continue
        try:
            ts = datetime.strptime(rec[C_DATE], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            stats["bad_date"] += 1
            continue
        m = _SRCLC_RE.search(rec[C_TRANSLATION]) if translated else None
        cols["id"].append(article_id(url))
        cols["url"].append(url)
        cols["domain"].append(rec[C_DOMAIN])
        cols["published_at"].append(ts)
        cols["day"].append(ts.date())
        cols["title"].append(title)
        cols["locations"].append(parse_locations(rec[C_LOC_V1], rec[C_LOC_V2]))
        cols["persons"].append(_split(rec[C_PERSONS]))
        cols["orgs"].append(_split(rec[C_ORGS]))
        cols["themes"].append(_split(rec[C_THEMES]))
        cols["all_names"].append(parse_all_names(rec[C_ALL_NAMES]))
        cols["counts"].append(parse_counts(rec[C_COUNTS]))
        for name, value in zip(TONE_FIELDS, parse_tone(rec[C_TONE]), strict=True):
            cols[name].append(value)
        cols["translated"].append(translated)
        cols["lang"].append(m.group(1).lower() if m else None)
        cols["gdelt_file"].append(gdelt_file)
    stats["kept"] = len(cols["id"])
    return pa.Table.from_pydict(cols, schema=STAGING_SCHEMA), dict(stats)


def read_zip(path: Path) -> bytes:
    with zipfile.ZipFile(path) as z:
        return z.read(z.namelist()[0])


# --- dedup across both feeds (pure) ----------------------------------------------------------


def finalize_rows(staged: pa.Table, sampled: bool) -> tuple[pa.Table, dict]:
    """Dedup one day's staged rows from both feeds: same URL first, then same normalised title.
    The earliest copy (English before translated on ties) is kept; `dup_count` counts the
    distinct-URL rows folded into it, including itself."""
    meta = pd.DataFrame(
        {
            "url": staged.column("url").to_pylist(),
            "title": staged.column("title").to_pylist(),
            "ts": staged.column("published_at").to_pandas(),
            "translated": staged.column("translated").to_pylist(),
        }
    )
    meta = meta.sort_values(["ts", "translated"], kind="stable")
    by_url = meta[~meta.duplicated("url")]
    key = by_url["title"].map(normalise_title)
    key = key.where(key != "", by_url["url"])
    first = ~key.duplicated()
    counts = key.map(key.value_counts())
    keep = by_url.index[first.to_numpy()]
    out = staged.take(pa.array(keep.to_numpy())).drop_columns(["gdelt_file"])
    out = out.append_column("dup_count", pa.array(counts[first].to_numpy(), pa.int32()))
    out = out.append_column("sampled", pa.array([sampled] * out.num_rows, pa.bool_()))
    out = out.select(ARTICLE_SCHEMA.names).cast(ARTICLE_SCHEMA)
    out = out.sort_by("published_at")
    stats = {
        "rows_in": staged.num_rows,
        "url_duplicates": int(len(meta) - len(by_url)),
        "title_folded": int(len(by_url) - first.sum()),
        "rows_out": out.num_rows,
    }
    return out, stats


# --- disk layout and state -------------------------------------------------------------------


def _atomic_write_bytes(data: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def write_parquet_atomic(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def write_json_atomic(obj: dict, path: Path) -> None:
    _atomic_write_bytes(json.dumps(obj, indent=1, default=str).encode(), path)


def staging_dir(feed: str, day: date) -> Path:
    return STAGING / feed / day.isoformat()


def final_path(day: date) -> Path:
    return ARTICLES / f"day={day.isoformat()}" / "articles.parquet"


def day_summary_path(day: date) -> Path:
    return STAGING / "_days" / f"{day.isoformat()}.json"


def load_done(feed: str, day: date) -> dict[str, dict]:
    """{gdelt_file ts: per-file stats} for files already staged (consolidated or as parts)."""
    d = staging_dir(feed, day)
    done: dict[str, dict] = {}
    manifest = d / "manifest.json"
    if manifest.exists():
        done.update(json.loads(manifest.read_text())["files"])
    parts = d / "parts"
    if parts.exists():
        for p in parts.glob("*.json"):
            done[p.stem] = json.loads(p.read_text())
    return done


def stage_file(feed: str, day: date, ts: str, table: pa.Table | None, stats: dict) -> None:
    """Write one file's parsed rows (part parquet first, then its stats json = 'done')."""
    parts = staging_dir(feed, day) / "parts"
    if table is not None:
        write_parquet_atomic(table, parts / f"{ts}.parquet")
    write_json_atomic(stats, parts / f"{ts}.json")


def consolidate(feed: str, day: date) -> None:
    """Merge parts into the feed-day's rows.parquet + manifest.json, then delete the parts.
    Crash-safe: rows are filtered to the manifest before parts are re-added."""
    d = staging_dir(feed, day)
    parts = d / "parts"
    if not parts.exists():
        return
    manifest = d / "manifest.json"
    files = json.loads(manifest.read_text())["files"] if manifest.exists() else {}
    tables = []
    if (d / "rows.parquet").exists() and files:
        old = pq.read_table(d / "rows.parquet")
        mask = pc.is_in(old.column("gdelt_file"), pa.array(list(files)))
        tables.append(old.filter(mask))
    for p in sorted(parts.glob("*.json")):
        files[p.stem] = json.loads(p.read_text())
        part = parts / f"{p.stem}.parquet"
        if part.exists():
            tables.append(pq.read_table(part))
    table = pa.concat_tables(tables) if tables else STAGING_SCHEMA.empty_table()
    write_parquet_atomic(table, d / "rows.parquet")
    write_json_atomic({"feed": feed, "day": day.isoformat(), "files": files}, manifest)
    for p in parts.iterdir():
        p.unlink()
    parts.rmdir()


def _read_manifest(feed: str, day: date) -> dict:
    p = staging_dir(feed, day) / "manifest.json"
    return json.loads(p.read_text()) if p.exists() else {}


def prune_staging(day: date) -> None:
    """After a day is finalized, drop the staged rows (they duplicate the final parquet) but
    keep manifest.json, so reruns still know the day's files are done."""
    for feed in dl.FEEDS:
        m = _read_manifest(feed, day)
        rows = staging_dir(feed, day) / "rows.parquet"
        if m:
            m["rows_pruned"] = True
            write_json_atomic(m, staging_dir(feed, day) / "manifest.json")
        if rows.exists():
            rows.unlink()


def wipe_day(day: date) -> None:
    """Forget all staging state for a day (used by --force-mode)."""
    for feed in dl.FEEDS:
        d = staging_dir(feed, day)
        if d.exists():
            for root, dirs, files in os.walk(d, topdown=False):
                for f in files:
                    (Path(root) / f).unlink()
                for sub in dirs:
                    (Path(root) / sub).rmdir()
            d.rmdir()
    day_summary_path(day).unlink(missing_ok=True)


def finalize_day(day: date, expected: dict[str, list[str]], sampled: bool) -> dict | None:
    """If every expected file of both feeds is staged, write the day's articles.parquet."""
    tables, per_feed = [], {}
    for feed in dl.FEEDS:
        done = load_done(feed, day)
        missing = [ts for ts in expected[feed] if ts not in done]
        if missing:
            return None
        if _read_manifest(feed, day).get("rows_pruned"):
            raise RuntimeError(
                f"{day}: staged rows were pruned after an earlier finalize; "
                "rerun this day with --force-mode to rebuild it"
            )
        rows = staging_dir(feed, day) / "rows.parquet"
        t = pq.read_table(rows) if rows.exists() else STAGING_SCHEMA.empty_table()
        t = t.filter(pc.is_in(t.column("gdelt_file"), pa.array(expected[feed] or [""])))
        tables.append(t)
        files = [done[ts] for ts in expected[feed]]
        per_feed[feed] = {
            "files": len(files),
            "files_missing_upstream": sum(f.get("status") == "missing" for f in files),
            "zip_bytes": sum(f.get("zip_bytes", 0) for f in files),
            "unzipped_bytes": sum(f.get("unzipped_bytes", 0) for f in files),
            "records": sum(f.get("records", 0) for f in files),
            "no_title": sum(f.get("no_title", 0) for f in files),
            "non_web": sum(f.get("non_web", 0) for f in files),
            "staged_rows": t.num_rows,
        }
    staged = pa.concat_tables(tables)
    off_day = pc.not_equal(staged.column("day"), pa.scalar(day, pa.date32()))
    n_off = pc.sum(off_day).as_py() or 0
    if n_off:
        staged = staged.filter(pc.invert(off_day))
    out, stats = finalize_rows(staged, sampled)
    write_parquet_atomic(out, final_path(day))
    translated = out.column("translated").to_pylist()
    langs = Counter(lang or "eng(english feed)" for lang in out.column("lang").to_pylist())
    summary = {
        "day": day.isoformat(),
        "sampled": sampled,
        "feeds": per_feed,
        **stats,
        "off_day_rows_dropped": n_off,
        "rows_out_english": translated.count(False),
        "rows_out_translated": translated.count(True),
        "lang_top": langs.most_common(25),
        "parquet_bytes": final_path(day).stat().st_size,
        "written_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    write_json_atomic(summary, day_summary_path(day))
    return summary


# --- orchestration ---------------------------------------------------------------------------


class BudgetExceeded(Exception):
    pass


def dir_bytes(path: Path) -> int:
    total = 0
    if path.exists():
        for root, _dirs, files in os.walk(path):
            total += sum((Path(root) / f).stat().st_size for f in files)
    return total


def gdelt_disk_bytes() -> int:
    """Everything this module puts on disk: raw zips + index, staging, final articles."""
    return dir_bytes(dl.RAW_GDELT) + dir_bytes(STAGING) + dir_bytes(ARTICLES)


def zips_on_disk(day: date | None = None) -> int:
    """Zips on disk, for one day's folder (what a single worker owns) or in total."""
    pattern = f"{day:%Y%m%d}/*.zip" if day else "*/*.zip"
    return sum(1 for _ in dl.RAW_GDELT.glob(pattern)) if dl.RAW_GDELT.exists() else 0


def shard_days(days: list[date], shard: int, n_shards: int) -> list[date]:
    """Every n-th day, so each parallel worker still goes newest first and no two workers
    ever touch the same day (days are the unit of all staging/output files)."""
    return days[shard::n_shards]


def ingest_day(
    day: date,
    feeds: list[str],
    indexes: dict[str, list[dl.GkgFile]],
    sample_per_hour: int,
    hour_step: int,
    client,
    throttle: dl.Throttle,
    *,
    keep_zips: bool = False,
    keep_staging: bool = False,
    max_disk_bytes: int,
    max_zips: int,
) -> dict:
    sampled = dl.is_sampled(sample_per_hour, hour_step)
    selected = {f: dl.select_day(indexes[f], day, sample_per_hour, hour_step) for f in dl.FEEDS}
    expected = {f: [g.ts for g in files] for f, files in selected.items()}
    result = {"day": day.isoformat(), "failed": []}
    complete = all(set(expected[f]) <= set(load_done(f, day)) for f in dl.FEEDS)
    if complete and day_summary_path(day).exists() and final_path(day).exists():
        log.info("day=%s already finalized; skipping", day)
        result["summary"] = json.loads(day_summary_path(day).read_text())
        return result
    for feed in feeds:
        done = load_done(feed, day)
        todo = [g for g in selected[feed] if g.ts not in done]
        n_slots = dl.expected_slots(sample_per_hour, hour_step)
        if len(selected[feed]) < n_slots:
            log.warning(
                "day=%s feed=%s index lists %d of %d slots", day, feed, len(selected[feed]), n_slots
            )
        t0, fetched, nbytes, kept = time.monotonic(), 0, 0, 0
        for g in todo:
            if gdelt_disk_bytes() + g.size > max_disk_bytes:
                consolidate(feed, day)
                raise BudgetExceeded(
                    f"next file would exceed --max-disk-gb ({max_disk_bytes / 1e9:.1f} GB)"
                )
            if zips_on_disk(day) >= max_zips:
                consolidate(feed, day)
                raise BudgetExceeded(f"{max_zips} zips already on disk (--max-zips)")
            try:
                path, n, _secs = dl.download(g, client, throttle)
            except dl.FileMissing:
                log.warning("day=%s feed=%s 404 %s (recorded as missing)", day, feed, g.filename)
                stage_file(feed, day, g.ts, None, {"status": "missing"})
                continue
            except RuntimeError as e:
                log.error("day=%s feed=%s download failed: %s", day, feed, e)
                result["failed"].append(g.filename)
                continue
            try:
                raw = read_zip(path)
            except zipfile.BadZipFile:
                log.error("day=%s feed=%s corrupt zip %s; deleted", day, feed, g.filename)
                path.unlink()
                result["failed"].append(g.filename)
                continue
            table, stats = parse_gkg_bytes(raw, feed, g.ts)
            stats.update(status="ok", zip_bytes=path.stat().st_size, unzipped_bytes=len(raw))
            stage_file(feed, day, g.ts, table, stats)
            if not keep_zips:
                path.unlink()
            fetched, nbytes, kept = fetched + 1, nbytes + n, kept + stats["kept"]
        consolidate(feed, day)
        log.info(
            "day=%s feed=%s files_fetched=%d already_done=%d bytes=%.1fMB rows_kept=%d secs=%.0f",
            day,
            feed,
            fetched,
            len(selected[feed]) - len(todo),
            nbytes / 1e6,
            kept,
            time.monotonic() - t0,
        )
    summary = finalize_day(day, expected, sampled)
    if summary:
        log.info(
            "day=%s finalized sampled=%s rows=%d (english=%d translated=%d) url_dups=%d "
            "title_folded=%d parquet=%.1fMB",
            day,
            sampled,
            summary["rows_out"],
            summary["rows_out_english"],
            summary["rows_out_translated"],
            summary["url_duplicates"],
            summary["title_folded"],
            summary["parquet_bytes"] / 1e6,
        )
        result["summary"] = summary
        if not keep_staging:
            prune_staging(day)
    else:
        log.info("day=%s not finalized yet (a feed is incomplete)", day)
        result["failed"] = result["failed"] or ["incomplete"]
    return result


def read_articles(start: date | None = None, end: date | None = None) -> pd.DataFrame:
    """All parsed articles (optionally a day range) as a DataFrame, via DuckDB."""
    import duckdb

    where = []
    if start:
        where.append(f"day >= DATE '{start.isoformat()}'")
    if end:
        where.append(f"day <= DATE '{end.isoformat()}'")
    glob = (ARTICLES / "**" / "*.parquet").as_posix()
    sql = f"SELECT * FROM read_parquet('{glob}', hive_partitioning=false)"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return duckdb.sql(sql).df()


def _existing_mode(day: date) -> bool | None:
    p = day_summary_path(day)
    return json.loads(p.read_text())["sampled"] if p.exists() else None


def run_workers(args: argparse.Namespace, days: list[date]) -> int:
    """Fetch the index once, then run `args.workers` copies of this CLI on interleaved days.
    Each child writes its own log (`run-...-shardKofN.log`). Returns the worst exit code."""
    import subprocess
    import sys

    n = args.workers
    if n > args.max_zips:
        raise SystemExit(f"--workers {n} would allow more than --max-zips {args.max_zips} zips")
    with dl.make_client() as client:  # warm the cache so the children never refetch at once
        for f in dl.FEEDS:
            dl.load_index(f, max(days), client, need_from=min(days))
    argv, skip = [], False
    for a in sys.argv[1:]:
        if skip:
            skip = False
        elif a == "--workers":
            skip = True
        elif not a.startswith("--workers="):
            argv.append(a)
    procs = []
    for k in range(n):
        cmd = [sys.executable, "-m", "undercurrent.ingest.gkg_parse", *argv, "--shard", f"{k}/{n}"]
        procs.append(subprocess.Popen(cmd))
        time.sleep(1.0)  # stagger starts so log names and first requests don't collide
    codes = [proc.wait() for proc in procs]
    print(f"workers finished, exit codes {codes}; per-worker logs in {LOGS}")
    return max(codes)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    dl.add_range_args(p)
    p.add_argument(
        "--max-disk-gb",
        type=float,
        default=2.0,
        help="stop before GDELT data on disk (raw+staging+articles) exceeds this (default 2)",
    )
    p.add_argument("--max-zips", type=int, default=10, help="max zips on disk at once")
    p.add_argument("--keep-zips", action="store_true", help="don't delete zips after parsing")
    p.add_argument("--oldest-first", action="store_true", help="default is newest day first")
    p.add_argument(
        "--force-mode",
        action="store_true",
        help="rebuild a finished day from scratch (e.g. to switch it between sampled and full)",
    )
    p.add_argument(
        "--keep-staging",
        action="store_true",
        help="keep per-feed staged rows after a day is finalized (default: prune them)",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="run N worker processes on interleaved days (default 1). Each worker downloads "
        "one file at a time, so N workers have up to N downloads in flight.",
    )
    p.add_argument("--shard", help=argparse.SUPPRESS)  # "K/N", set by --workers for each child
    args = p.parse_args()
    feeds = dl.resolve_feeds(args)
    days = sorted(dl.resolve_days(args), reverse=not args.oldest_first)
    sampled = dl.is_sampled(args.sample_per_hour, args.hour_step)
    if args.workers > 1:
        raise SystemExit(run_workers(args, days))
    shard_tag = ""
    if args.shard:
        k, n = (int(x) for x in args.shard.split("/"))
        days = shard_days(days, k, n)
        shard_tag = f"-shard{k}of{n}"
        if not days:
            return

    LOGS.mkdir(parents=True, exist_ok=True)
    log_file = LOGS / f"run-{datetime.now(UTC):%Y%m%dT%H%M%SZ}{shard_tag}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is too noisy
    log.info(
        "run days=%s..%s (%d) feeds=%s sample_per_hour=%d hour_step=%d sampled=%s "
        "max_disk_gb=%.1f log=%s",
        min(days),
        max(days),
        len(days),
        ",".join(feeds),
        args.sample_per_hour,
        args.hour_step,
        sampled,
        args.max_disk_gb,
        log_file,
    )
    failed: dict[str, list[str]] = {}
    with dl.make_client() as client:
        indexes = {f: dl.load_index(f, max(days), client, need_from=min(days)) for f in dl.FEEDS}
        throttle = dl.Throttle(args.min_interval)
        for day in days:
            mode = _existing_mode(day)
            if args.force_mode:
                wipe_day(day)
            elif mode is not None and mode != sampled:
                log.warning("day=%s already done with sampled=%s; skipping", day, mode)
                continue
            try:
                r = ingest_day(
                    day,
                    feeds,
                    indexes,
                    args.sample_per_hour,
                    args.hour_step,
                    client,
                    throttle,
                    keep_zips=args.keep_zips,
                    keep_staging=args.keep_staging,
                    max_disk_bytes=int(args.max_disk_gb * 1e9),
                    max_zips=args.max_zips,
                )
            except BudgetExceeded as e:
                log.error("stopping: %s (day=%s)", e, day)
                failed[day.isoformat()] = ["stopped: " + str(e)]
                break
            except RuntimeError as e:
                log.error("day=%s failed: %s", day, e)
                failed[day.isoformat()] = [str(e)]
                continue
            if r["failed"]:
                failed[day.isoformat()] = r["failed"]
    log.info("done. disk=%.2f GB failed_days=%s", gdelt_disk_bytes() / 1e9, failed or "none")


if __name__ == "__main__":
    main()
