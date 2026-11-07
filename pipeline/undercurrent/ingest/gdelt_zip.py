"""Pull GDELT GKG from the 15-minute zip files (both feeds) into the `articles` table, one day at
a time. The zip-file twin of gdelt_bq.py: no BigQuery, no Google account, all CPU.

    uv run python -m undercurrent.ingest.gdelt_zip --start 2026-11-05 --end 2026-11-05   # one day
    uv run python -m undercurrent.ingest.gdelt_zip --start 2025-10-10 --end 2026-11-06 \
        --sync-to gs://BUCKET/processed                                                # a year
    uv run python -m undercurrent.ingest.gdelt_zip --status

Writes exactly what gdelt_bq.py writes, through the same gkg.py, so everything downstream (the
vector store, the tracker, the forecast proxy) can't tell the two apart:
    data/processed/articles/day=YYYY-MM-DD/part.parquet       docs/WORKPLAN.md 3.1
    data/processed/gdelt_totals/day=YYYY-MM-DD/part.parquet   counts before filtering
plus, for the zip files only:
    data/processed/gdelt_files/YYYY-MM-DD.json                files listed/missing per feed, timings
Scratch: data/raw/gdelt/masterfilelist*.txt (GDELT's indexes), data/raw/gdelt_stage/<day>/.

How it runs (measurements and the full story: docs/ONE_YEAR_EACH.md): a process pool downloads and
reads one 15-minute file per task; when all of a day's files (96 per feed) are staged, one task
builds the day with gkg.build_articles, writes it and clears the stage. Days go newest first, or
oldest first with --oldest-first (so embedding and tracking can follow right behind the pull).
A day is done when its articles file exists; it is moved into place last, complete, so a sync to
the bucket never sees half a day. Kill it at any point and run the same command again.
"""

import argparse
import datetime as dt
import hashlib
import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import zipfile
from collections import defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent.config import PROCESSED, RAW
from undercurrent.ingest import gkg
from undercurrent.ingest.gdelt_bq import articles_path, totals_path

BASE = "https://data.gdeltproject.org/gdeltv2/"
FEEDS = {"en": "masterfilelist.txt", "tr": "masterfilelist-translation.txt"}
INDEX_DIR = RAW / "gdelt"
STAGE = RAW / "gdelt_stage"
FILES_PER_DAY = 96  # one every 15 minutes, per feed
INDEX_MAX_AGE_H = 6
USER_AGENT = "undercurrent-ingest/0.1 (hackathon research project)"
SYNCED = ("articles", "gdelt_totals", "gdelt_files")

# GKG 2.1: tab-separated, 27 columns, no header. The columns gkg.RAW_COLUMNS needs:
N_FIELDS = 27
RAW_FIELDS = {
    "record_id": 0,  # GKGRECORDID
    "date_int": 1,  # DATE
    "domain": 3,  # SourceCommonName
    "url": 4,  # DocumentIdentifier
    "themes": 7,  # V1Themes
    "locations": 9,  # V1Locations (the BigQuery table's `Locations`)
    "persons": 11,  # V1Persons
    "orgs": 13,  # V1Organizations
    "tone": 15,  # V1.5Tone (BigQuery: `V2Tone`)
    "translation_info": 25,  # V2.1TranslationInfo
}
F_EXTRAS = 26
_TITLE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_RECORD_START = re.compile(r"\d{14}-T?\d+\t")
RAW_SCHEMA = pa.schema(
    [(c, pa.int64() if c == "date_int" else pa.string()) for c in gkg.RAW_COLUMNS]
)

log = logging.getLogger("gdelt_zip")


# --- reading one file ----------------------------------------------------------------------------


def records(text: str) -> list[str]:
    """Split a GKG file into records. A few Extras fields contain raw newlines, so a line that
    doesn't start with a record id (YYYYMMDDHHMMSS-N or -TN) continues the previous record."""
    out: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if _RECORD_START.match(line) or not out:
            out.append(line)
        else:
            out[-1] += " " + line
    return [r for r in out if r]


def read_raw(data: bytes) -> tuple[pa.Table, int]:
    """One unzipped GKG file -> (rows with gkg.RAW_COLUMNS, malformed records skipped).

    Values are what BigQuery would return for the same columns: empty fields become null, the
    title is the raw <PAGE_TITLE> text (gkg.build_articles unescapes it).
    """
    cols: dict[str, list] = {c: [] for c in gkg.RAW_COLUMNS}
    bad = 0
    for rec in records(data.decode("utf-8", errors="replace")):
        f = rec.split("\t")
        if len(f) != N_FIELDS or not (f[1].isdigit() and len(f[1]) == 14):
            bad += 1
            continue
        for name, i in RAW_FIELDS.items():
            if name == "date_int":
                cols[name].append(int(f[i]))
            elif name == "domain":  # "" not null: a null domain can break gkg.build_articles
                cols[name].append(f[i])
            else:
                cols[name].append(f[i] or None)
        m = _TITLE.search(f[F_EXTRAS])
        cols["title"].append(m.group(1) if m else None)
    return pa.table(cols, schema=RAW_SCHEMA), bad


# --- index and day selection ---------------------------------------------------------------------


@dataclass(frozen=True)
class GkgFile:
    url: str
    size: int
    md5: str
    feed: str  # "en" or "tr"
    stamp: str  # YYYYMMDDHHMMSS of the 15-minute batch

    @property
    def day(self) -> dt.date:
        return dt.date(int(self.stamp[:4]), int(self.stamp[4:6]), int(self.stamp[6:8]))

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[1].removesuffix(".csv.zip")


def parse_index(text: str, feed: str) -> list[GkgFile]:
    """GDELT's master file list: lines of 'size md5 url'. Keeps only the GKG files."""
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 3 or not parts[2].endswith(".gkg.csv.zip"):
            continue
        stamp = parts[2].rsplit("/", 1)[1][:14]
        if not stamp.isdigit():
            continue
        url = parts[2].replace("http://", "https://", 1)
        out.append(GkgFile(url, int(parts[0]) if parts[0].isdigit() else 0, parts[1], feed, stamp))
    return out


def load_index(feeds: list[str], need_until: dt.date) -> dict[dt.date, list[GkgFile]]:
    """GKG files per day, from the master file lists (cached; refreshed if stale or too short)."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    by_day: dict[dt.date, list[GkgFile]] = defaultdict(list)
    for feed in feeds:
        path = INDEX_DIR / FEEDS[feed]
        stale = not path.exists() or time.time() - path.stat().st_mtime > INDEX_MAX_AGE_H * 3600
        files = [] if stale else parse_index(path.read_text(), feed)
        if not files or files[-1].day < need_until:
            log.info("downloading %s", FEEDS[feed])
            with httpx.Client(timeout=300, follow_redirects=True) as client:
                r = client.get(BASE + FEEDS[feed], headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(r.content)
            tmp.replace(path)
            files = parse_index(path.read_text(), feed)
        for f in files:
            by_day[f.day].append(f)
    return by_day


def select_files(files: list[GkgFile], sample_per_hour: int) -> list[GkgFile]:
    """Every file (4 an hour), or the first N of each hour on both feeds alike (fallback only)."""
    keep = files if sample_per_hour >= 4 else [
        f for f in files if int(f.stamp[10:12]) // 15 < sample_per_hour
    ]  # fmt: skip
    return sorted(keep, key=lambda f: (f.stamp, f.feed))


def shard_days(
    start: dt.date, end: dt.date, k: int, n: int, oldest_first: bool = False
) -> list[dt.date]:
    """Machine k of n's days, newest first (or oldest first). Keyed on the date (the same rule as vectors.store
    --shard), so widening the range later never moves a day to another machine."""
    days = [end - dt.timedelta(days=i) for i in range((end - start).days + 1)]
    mine = [d for d in days if d.toordinal() % n == k]
    return mine[::-1] if oldest_first else mine


# --- per-file work (runs in a worker process) ----------------------------------------------------

_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


def fetch(f: GkgFile, attempts: int = 6) -> bytes | None:
    """The unzipped file, or None if GDELT keeps answering 404 (listed but never published)."""
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            r = _http().get(f.url)
            if r.status_code == 404:
                if attempt >= 2:
                    return None
                raise httpx.HTTPStatusError("404", request=r.request, response=r)
            r.raise_for_status()
            data = r.content
            if f.md5 and hashlib.md5(data).hexdigest() != f.md5:
                raise OSError(f"md5 mismatch for {f.name}")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                return z.read(z.namelist()[0])
        except (httpx.HTTPError, OSError, zipfile.BadZipFile, IndexError) as e:
            error = e
            time.sleep(min(60, 3 * 2**attempt))
    raise RuntimeError(f"{f.name}: giving up after {attempts} attempts: {error}")


def _replace_atomically(text_or_table: str | pa.Table, path: Path, scratch: Path) -> None:
    """Write in `scratch` (same disk), then rename into place: readers and syncs never see a
    partial file, and nothing temporary ever appears next to the real one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    tmp = scratch / f".{path.parent.name}-{path.name}.{os.getpid()}.tmp"
    if isinstance(text_or_table, str):
        tmp.write_text(text_or_table)
    else:
        pq.write_table(text_or_table, tmp, compression="zstd")
    os.replace(tmp, path)


def process_file(f: GkgFile) -> dict:
    """Download one 15-minute file and stage its raw rows. Returns (and stages) its stats."""
    stage = STAGE / f.day.isoformat()
    meta_path = stage / f"{f.name}.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text())
    started = time.time()
    data = fetch(f)
    meta = {"name": f.name, "feed": f.feed, "zip_bytes": f.size, "missing": data is None}
    if data is not None:
        table, bad = read_raw(data)
        _replace_atomically(table, stage / f"{f.name}.parquet", stage)
        meta.update(rows=table.num_rows, bad=bad)
    meta["seconds"] = round(time.time() - started, 2)
    _replace_atomically(json.dumps(meta), meta_path, stage)
    return meta


def finalize_day(
    processed: Path, day: dt.date, files: list[GkgFile], started: float, sampled: bool
) -> dict:
    """Build one day from its staged files (both feeds) with gkg.py, write it, clear the stage."""
    stage = STAGE / day.isoformat()
    metas = [json.loads((stage / f"{f.name}.json").read_text()) for f in files]
    tables = [pq.read_table(stage / f"{m['name']}.parquet") for m in metas if not m["missing"]]
    raw = pa.concat_tables(tables).to_pandas() if tables else RAW_SCHEMA.empty_table().to_pandas()
    raw = gkg.split_by_day(raw).get(day, raw.iloc[0:0])  # by each row's own DATE
    articles = gkg.build_articles(raw, sampled=sampled)
    _replace_atomically(gkg.daily_totals(raw), totals_path(processed, day), stage)

    report = {
        "day": day.isoformat(),
        "host": socket.gethostname(),
        "sampled": sampled,
        "feeds": {
            feed: {
                "files_listed": sum(m["feed"] == feed for m in metas),
                "files_missing": [m["name"] for m in metas if m["feed"] == feed and m["missing"]],
                "zip_bytes": sum(m["zip_bytes"] for m in metas if m["feed"] == feed),
                "rows": sum(m.get("rows", 0) for m in metas if m["feed"] == feed),
                "bad_rows": sum(m.get("bad", 0) for m in metas if m["feed"] == feed),
            }
            for feed in FEEDS
        },
        "expected_files_per_feed": FILES_PER_DAY,
        "articles": articles.num_rows,
        "seconds": round(time.time() - started, 1),
        "finished_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    out = articles_path(processed, day)
    files_json = processed / "gdelt_files" / f"{day.isoformat()}.json"
    _replace_atomically(json.dumps(report, indent=1), files_json, stage)
    _replace_atomically(articles, out, stage)  # last: its presence marks the day done
    report["parquet_bytes"] = out.stat().st_size
    for p in stage.iterdir():
        p.unlink()
    stage.rmdir()
    return report


# --- syncing finished days -----------------------------------------------------------------------


def sync_commands(processed: Path, dest: str) -> list[list[str]]:
    """gs://bucket/processed -> `gcloud storage rsync`; anything else (host:/path/) -> rsync."""
    dirs = [processed / d for d in SYNCED if (processed / d).is_dir()]
    if dest.startswith("gs://"):
        return [
            ["gcloud", "storage", "rsync", "-r", "-q", str(d), f"{dest.rstrip('/')}/{d.name}"]
            for d in dirs
        ]
    return [["rsync", "-a", "--partial", *map(str, dirs), dest]] if dirs else []


class Syncer(threading.Thread):
    """Copies finished days to the bucket after each day (coalesced) and every 10 minutes. Never
    deletes anything at the destination. A failed sync is retried the next time round."""

    def __init__(self, processed: Path, dest: str):
        super().__init__(daemon=True)
        self.processed, self.dest = processed, dest
        self.wake, self.lock = threading.Event(), threading.Lock()
        self.stopping = False

    def run(self) -> None:
        while not self.stopping:
            self.wake.wait(timeout=600)
            self.wake.clear()
            self.sync()

    def sync(self) -> bool:
        ok = True
        with self.lock:
            for cmd in sync_commands(self.processed, self.dest):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
                    if r.returncode != 0:
                        ok = False
                        log.warning("sync failed (%s): %s", r.returncode, r.stderr.strip()[-500:])
                except (OSError, subprocess.TimeoutExpired) as e:
                    ok = False
                    log.warning("sync failed: %s", e)
        return ok

    def finish(self) -> bool:
        self.stopping = True
        ok = self.sync()
        log.info("final sync to %s: %s", self.dest, "ok" if ok else "FAILED (run it again)")
        return ok


# --- run and status ------------------------------------------------------------------------------


def run(args: argparse.Namespace, start: dt.date, end: dt.date) -> int:
    k, n = (int(x) for x in args.shard.split("/"))
    if not 0 <= k < n:
        sys.exit("--shard must be k/n with 0 <= k < n")
    feeds = args.feeds.split(",")
    order = shard_days(start, end, k, n, oldest_first=args.oldest_first)
    days = [d for d in order if not articles_path(args.out, d).exists()]
    log.info("%d day(s) to do between %s and %s, %d workers", len(days), start, end, args.workers)
    syncer = Syncer(args.out, args.sync_to) if args.sync_to else None
    if syncer:
        syncer.start()
    if not days:
        return 0 if not syncer or syncer.finish() else 1

    index = load_index(feeds, need_until=max(days))
    sampled = args.sample_per_hour < 4
    files_of = {d: select_files(index.get(d, []), args.sample_per_hour) for d in days}
    queue = deque((d, f) for d in days for f in files_of[d])
    remaining = {d: len(files_of[d]) for d in days}
    started: dict[dt.date, float] = {}
    failed: set[dt.date] = set()
    done, t0 = 0, time.time()

    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        inflight: dict[Future, tuple[str, dt.date]] = {}

        def finalize(d: dt.date) -> None:
            t = started.get(d, time.time())
            inflight[pool.submit(finalize_day, args.out, d, files_of[d], t, sampled)] = ("day", d)

        for d in days:  # days GDELT has no files for are still written (empty), so they count
            if remaining[d] == 0:
                finalize(d)
        while queue or inflight:
            while queue and len(inflight) < args.workers * 2:
                d, f = queue.popleft()
                started.setdefault(d, time.time())
                inflight[pool.submit(process_file, f)] = ("file", d)
            finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for fut in finished:
                kind, d = inflight.pop(fut)
                try:
                    result = fut.result()
                except Exception as e:  # log, give up on this day for now, keep the run going
                    log.error("%s: %s failed: %s", d, kind, e)
                    failed.add(d)
                    continue
                if kind == "file":
                    remaining[d] -= 1
                    if remaining[d] == 0 and d not in failed:
                        finalize(d)
                    continue
                done += 1
                rate = done / (time.time() - t0)
                en, tr = result["feeds"]["en"], result["feeds"]["tr"]
                log.info(
                    "%s: %s articles (en %d/%d files, tr %d/%d), %.0f MB, %.0fs | "
                    "%d/%d days, %.0f days/h, ETA %.1f h",
                    d, f"{result['articles']:,}",
                    en["files_listed"] - len(en["files_missing"]), en["files_listed"],
                    tr["files_listed"] - len(tr["files_missing"]), tr["files_listed"],
                    result["parquet_bytes"] / 1e6, result["seconds"], done, len(days),
                    rate * 3600, (len(days) - done - len(failed)) / rate / 3600,
                )  # fmt: skip
                if syncer:
                    syncer.wake.set()
    synced = syncer.finish() if syncer else True
    if failed:
        log.error("%d day(s) failed; run the same command again to retry: %s", len(failed),
                  ", ".join(sorted(d.isoformat() for d in failed)))  # fmt: skip
        return 1
    return 0 if synced else 1


def status(processed: Path, start: dt.date | None, end: dt.date | None) -> int:
    """Progress and gaps, from the per-day reports on this machine."""
    reports = [
        json.loads(p.read_text()) for p in sorted((processed / "gdelt_files").glob("*.json"))
    ]
    if start and end:
        reports = [r for r in reports if start.isoformat() <= r["day"] <= end.isoformat()]
    if not reports:
        print("no finished days yet")
        return 0
    first = start or dt.date.fromisoformat(reports[0]["day"])
    last = end or dt.date.fromisoformat(reports[-1]["day"])
    articles = sum(r["articles"] for r in reports)
    paths = [articles_path(processed, dt.date.fromisoformat(r["day"])) for r in reports]
    size = sum(p.stat().st_size for p in paths if p.exists())
    print(f"{len(reports)}/{(last - first).days + 1} days done between {first} and {last}")
    print(f"{articles:,} articles ({articles / len(reports):,.0f} a day), {size / 1e9:.1f} GB")
    gaps = [
        (r["day"], feed, len(f["files_missing"]), f["files_listed"])
        for r in reports
        for feed, f in r["feeds"].items()
        if f["files_missing"] or f["files_listed"] < r["expected_files_per_feed"]
    ]
    if gaps:
        print(f"{len(gaps)} day/feed pairs with gaps (GDELT outages), newest first:")
        for day, feed, missing, listed in sorted(gaps, reverse=True)[:20]:
            print(f"  {day} {feed}: {listed} files listed of 96, {missing} of them missing")
    recent = sorted(reports, key=lambda r: r["finished_at"])[-1]
    print(f"last finished: {recent['day']} at {recent['finished_at']} on {recent['host']}")
    return 0


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--start", type=_date, help="first UTC day, YYYY-MM-DD")
    p.add_argument("--end", type=_date, help="last UTC day (inclusive); default --start")
    p.add_argument("--status", action="store_true", help="show progress and gaps, then exit")
    p.add_argument("--workers", type=int, default=os.cpu_count() or 4, help="default: all CPUs")
    p.add_argument(
        "--sync-to", help="copy finished days here: gs://BUCKET/processed or host:/path/"
    )
    p.add_argument("--shard", default="0/1", help="k/n: only every n-th day (several machines)")
    p.add_argument("--oldest-first", action="store_true", help="pull the oldest day first")
    p.add_argument("--feeds", default="en,tr", help="en = English feed, tr = translated feed")
    p.add_argument("--sample-per-hour", type=int, default=4, choices=(1, 2, 3, 4),
                   help="files per hour per feed; 4 = full resolution (sampling is a fallback)")  # fmt: skip
    p.add_argument("--out", type=Path, default=PROCESSED, help="processed data folder")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.status:
        sys.exit(status(args.out, args.start, args.end))
    if not args.start:
        p.error("--start is required")
    end = args.end or args.start
    yesterday = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
    if end > yesterday:
        print(f"{end} isn't complete yet; stopping at {yesterday}.", file=sys.stderr)
        end = yesterday
    if end < args.start:
        p.error("--end is before --start")
    sys.exit(run(args, args.start, end))


if __name__ == "__main__":
    main()
