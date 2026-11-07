"""Download GDELT GKG 2.1 15-minute files, for the English and the machine-translated feed.

The file lists come from `masterfilelist.txt` / `masterfilelist-translation.txt` (each line is
`size md5 url`). Both are >100 MB, so they are streamed once and only recent `gkg` lines are
cached in `data/raw/gdelt/index/`.

Most runs should use `undercurrent.ingest.gdelt` (download -> parse -> delete zip). This CLI
only downloads zips to `data/raw/gdelt/YYYYMMDD/`, which is useful for measuring or debugging:

    uv run python -m undercurrent.ingest.gdelt_download --days 1 --dry-run
    uv run python -m undercurrent.ingest.gdelt_download --at 20261106120000
    uv run python -m undercurrent.ingest.gdelt_download --start 2026-11-01 --end 2026-11-01 \
        --sample-per-hour 1 --translation
"""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx

from undercurrent import config

BASE = "https://data.gdeltproject.org/gdeltv2"
FEEDS = ("english", "translation")
MASTER = {
    "english": f"{BASE}/masterfilelist.txt",
    "translation": f"{BASE}/masterfilelist-translation.txt",
}
SUFFIX = {"english": ".gkg.csv.zip", "translation": ".translation.gkg.csv.zip"}

RAW_GDELT = config.RAW / "gdelt"
INDEX_DIR = RAW_GDELT / "index"
INDEX_KEEP_DAYS = 400  # how much of the master list to cache
INDEX_MAX_AGE_S = 3600  # refetch the master list if the cache is older than this and too short

USER_AGENT = "undercurrent/0.1 (https://github.com/CooperMcCloskey/undercurrent)"
MIN_INTERVAL_S = 0.0  # no gap by default; --min-interval adds one if GDELT ever rate-limits
RETRIES = 5


@dataclass(frozen=True)
class GkgFile:
    feed: str
    ts: str  # YYYYMMDDHHMMSS from the filename
    size: int  # zipped bytes, from the master list (0 if unknown)
    md5: str
    url: str

    @property
    def day(self) -> date:
        return date(int(self.ts[:4]), int(self.ts[4:6]), int(self.ts[6:8]))

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[-1]


class FileMissing(Exception):
    """GDELT returned 404 for a file listed in the index (it happens; GDELT has gaps)."""


# --- index -------------------------------------------------------------------------------


def _parse_index_line(feed: str, line: str) -> GkgFile | None:
    parts = line.split()
    if len(parts) != 3 or not parts[2].endswith(SUFFIX[feed]):
        return None
    if feed == "english" and ".translation." in parts[2]:
        return None
    name = parts[2].rsplit("/", 1)[-1]
    ts = name[:14]
    if not ts.isdigit():
        return None
    url = parts[2].replace("http://", "https://", 1)
    size = int(parts[0]) if parts[0].isdigit() else 0
    return GkgFile(feed, ts, size, parts[1], url)


def _index_path(feed: str) -> Path:
    return INDEX_DIR / f"{feed}.gkg.txt"


def fetch_index(feed: str, client: httpx.Client, since: date | None = None) -> Path:
    """Stream the master list and cache gkg lines from `since` (default: the last
    INDEX_KEEP_DAYS days, whichever is earlier)."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    default = datetime.now(UTC).date() - timedelta(days=INDEX_KEEP_DAYS)
    cutoff = min(default, since or default).strftime("%Y%m%d")
    out = _index_path(feed)
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")  # parallel workers never share a tmp
    kept = 0
    t0 = time.monotonic()
    with client.stream("GET", MASTER[feed], timeout=120) as r, tmp.open("w") as f:
        r.raise_for_status()
        for line in r.iter_lines():
            g = _parse_index_line(feed, line)
            if g and g.ts[:8] >= cutoff:
                f.write(f"{g.size} {g.md5} {g.url}\n")
                kept += 1
    os.replace(tmp, out)
    print(f"index {feed}: {kept} gkg files cached in {time.monotonic() - t0:.0f}s -> {out}")
    return out


def load_index(
    feed: str,
    need_until: date,
    client: httpx.Client | None = None,
    need_from: date | None = None,
) -> list[GkgFile]:
    """Cached index for a feed. Refetched if missing, if it doesn't go back to `need_from`,
    or if it doesn't reach `need_until` and is older than INDEX_MAX_AGE_S."""
    path = _index_path(feed)
    files = _read_index(feed, path) if path.exists() else []
    stale = time.time() - path.stat().st_mtime > INDEX_MAX_AGE_S if path.exists() else True
    too_short = bool(files) and need_from is not None and files[0].day > need_from
    if not files or too_short or (files[-1].day < need_until and stale):
        own = client is None
        client = client or make_client()
        try:
            fetch_index(feed, client, since=need_from)
        finally:
            if own:
                client.close()
        files = _read_index(feed, path)
    return files


def _read_index(feed: str, path: Path) -> list[GkgFile]:
    with path.open() as f:
        files = [g for line in f if (g := _parse_index_line(feed, line))]
    return sorted(files, key=lambda g: g.ts)


# --- selecting files ---------------------------------------------------------------------


def is_sampled(sample_per_hour: int, hour_step: int) -> bool:
    return sample_per_hour < 4 or hour_step > 1


def select_day(
    index: Iterable[GkgFile], day: date, sample_per_hour: int = 4, hour_step: int = 1
) -> list[GkgFile]:
    """Files for one UTC day. `sample_per_hour` 4 = every file, 2 = :00 and :30, 1 = :00 only.
    `hour_step` 2 = only even hours. The same rule is applied to both feeds, so sampled
    volumes stay comparable across feeds and days."""
    if sample_per_hour not in (1, 2, 4):
        raise ValueError("sample_per_hour must be 1, 2 or 4")
    slots = {1: {0}, 2: {0, 2}, 4: {0, 1, 2, 3}}[sample_per_hour]
    key = day.strftime("%Y%m%d")
    chosen: dict[tuple[int, int], GkgFile] = {}
    for g in index:
        if g.ts[:8] != key:
            continue
        hour, slot = int(g.ts[8:10]), int(g.ts[10:12]) // 15
        if hour % hour_step == 0 and slot in slots:
            chosen.setdefault((hour, slot), g)  # first file per slot, if GDELT ever doubles up
    return [chosen[k] for k in sorted(chosen)]


def expected_slots(sample_per_hour: int = 4, hour_step: int = 1) -> int:
    return (24 // hour_step) * sample_per_hour


# --- downloading -------------------------------------------------------------------------


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=httpx.Timeout(60.0)
    )


class Throttle:
    def __init__(self, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        delay = self._last + self.min_interval_s - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._last = time.monotonic()


def zip_path(g: GkgFile, root: Path = RAW_GDELT) -> Path:
    return root / g.ts[:8] / g.filename


def download(
    g: GkgFile, client: httpx.Client, throttle: Throttle, root: Path = RAW_GDELT
) -> tuple[Path, int, float]:
    """Download one zip (skipped if already on disk). Returns (path, bytes, seconds).
    Retries with exponential backoff; checks the md5 from the index. Raises FileMissing on 404."""
    dest = zip_path(g, root)
    if dest.exists() and (not g.size or dest.stat().st_size == g.size):
        return dest, 0, 0.0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    last_err: Exception | None = None
    for attempt in range(RETRIES):
        throttle.wait()
        t0 = time.monotonic()
        try:
            r = client.get(g.url)
            if r.status_code == 404:
                raise FileMissing(g.url)
            r.raise_for_status()
            body = r.content
            if g.md5 and hashlib.md5(body).hexdigest() != g.md5:
                raise ValueError(f"md5 mismatch for {g.url}")
            tmp.write_bytes(body)
            os.replace(tmp, dest)
            return dest, len(body), time.monotonic() - t0
        except FileMissing:
            raise
        except (httpx.HTTPError, ValueError) as e:
            last_err = e
            backoff = 2 ** (attempt + 1)
            print(f"  retry {attempt + 1}/{RETRIES} for {g.filename} in {backoff}s: {e}")
            time.sleep(backoff)
    raise RuntimeError(f"giving up on {g.url}: {last_err}")


# --- CLI ---------------------------------------------------------------------------------


def day_range(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def resolve_days(args: argparse.Namespace) -> list[date]:
    """--start/--end (inclusive) or --days N (the N most recent complete UTC days)."""
    today = datetime.now(UTC).date()
    if args.start:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end) if args.end else start
    else:
        end = today - timedelta(days=1)
        start = end - timedelta(days=args.days - 1)
    if end >= today:
        raise SystemExit(f"--end must be before today ({today}); today's files aren't complete")
    if start > end:
        raise SystemExit("--start is after --end")
    return day_range(start, end)


def resolve_feeds(args: argparse.Namespace) -> list[str]:
    if args.translation and args.english:
        raise SystemExit("--translation and --english are mutually exclusive (default: both)")
    if args.translation:
        return ["translation"]
    if args.english:
        return ["english"]
    return list(FEEDS)


def add_range_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", help="first UTC day, YYYY-MM-DD")
    p.add_argument("--end", help="last UTC day, inclusive (default: --start)")
    p.add_argument("--days", type=int, default=1, help="N most recent complete days (default 1)")
    p.add_argument("--sample-per-hour", type=int, default=4, choices=(1, 2, 4))
    p.add_argument("--hour-step", type=int, default=1, choices=(1, 2, 3, 4, 6))
    p.add_argument("--translation", action="store_true", help="translated feed only")
    p.add_argument("--english", action="store_true", help="English feed only")
    p.add_argument(
        "--min-interval",
        type=float,
        default=MIN_INTERVAL_S,
        help="minimum seconds between one worker's download requests (default 0)",
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    add_range_args(p)
    p.add_argument("--at", help="download just the file(s) at this YYYYMMDDHHMMSS timestamp")
    p.add_argument("--dry-run", action="store_true", help="list counts and sizes; download nothing")
    p.add_argument("--limit", type=int, help="stop after this many files per feed")
    args = p.parse_args()
    feeds = resolve_feeds(args)

    with make_client() as client:
        if args.at:
            day = date(int(args.at[:4]), int(args.at[4:6]), int(args.at[6:8]))
            plan = {f: [g for g in load_index(f, day, client) if g.ts == args.at] for f in feeds}
        else:
            days = resolve_days(args)
            plan = {}
            for f in feeds:
                index = load_index(f, days[-1], client)
                plan[f] = [
                    g
                    for d in days
                    for g in select_day(index, d, args.sample_per_hour, args.hour_step)
                ]
        throttle = Throttle(args.min_interval)
        for f, files in plan.items():
            files = files[: args.limit] if args.limit else files
            mb = sum(g.size for g in files) / 1e6
            print(f"{f}: {len(files)} files, {mb:,.1f} MB zipped (from index)")
            if args.dry_run:
                continue
            for g in files:
                try:
                    path, n, s = download(g, client, throttle)
                except FileMissing:
                    print(f"  404 {g.url}")
                    continue
                print(f"  {path.relative_to(config.ROOT)}  {n / 1e6:.2f} MB  {s:.1f}s")


if __name__ == "__main__":
    main()
