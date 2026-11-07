"""Shared plumbing for the Wikimedia APIs: polite User-Agent, rate limiting, retries, title and
date helpers. Used by wiki_top, wiki_resolve and wiki.

Titles are always stored in canonical underscore form ("Barack_Obama"), which is what the
pageviews API uses and returns.
"""

import argparse
import datetime as dt
import threading
import time
from urllib.parse import quote

import httpx

from undercurrent.config import WIKIMEDIA_USER_AGENT

REST = "https://wikimedia.org/api/rest_v1"
ACTION = "https://en.wikipedia.org/w/api.php"


class WikiClient:
    """Thread-safe GET with a shared request-rate limit and retry on 429/5xx/network errors."""

    def __init__(self, max_rps: float = 10.0, retries: int = 6, timeout: float = 30.0) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": WIKIMEDIA_USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
        )
        self._interval = 1.0 / max_rps
        self._lock = threading.Lock()
        self._next = 0.0
        self._retries = retries

    def _wait_turn(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            time.sleep(slot - now)

    def get_json(self, url: str, params: dict | None = None) -> dict | None:
        """Parsed JSON, or None on 404. Raises RuntimeError after repeated failures."""
        last = ""
        for attempt in range(self._retries):
            self._wait_turn()
            try:
                r = self._http.get(url, params=params)
            except httpx.TransportError as e:
                last = repr(e)
                time.sleep(min(2**attempt, 30))
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                try:
                    delay = float(r.headers.get("retry-after", ""))
                except ValueError:
                    delay = 2**attempt
                time.sleep(min(delay, 60))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"giving up on {url} {params or ''}: {last}")

    def close(self) -> None:
        self._http.close()


def normalise_title(title: str) -> str:
    """'barack obama ' -> 'Barack_obama'. MediaWiki only capitalises the first letter."""
    t = "_".join(title.strip().split())
    return t[:1].upper() + t[1:]


def title_path(title: str) -> str:
    """URL path segment for the REST API ('/' and other reserved characters escaped)."""
    return quote(normalise_title(title), safe="")


def utc_today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def add_date_args(p: argparse.ArgumentParser, default_days: int) -> None:
    p.add_argument("--start", type=dt.date.fromisoformat, help="first day (UTC), YYYY-MM-DD")
    p.add_argument("--end", type=dt.date.fromisoformat, help="last day (UTC); default yesterday")
    p.add_argument(
        "--days",
        type=int,
        default=default_days,
        help=f"if --start is not given: this many days ending at --end (default {default_days})",
    )


def date_range(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    end = args.end or utc_today() - dt.timedelta(days=1)
    start = args.start or end - dt.timedelta(days=args.days - 1)
    if start > end:
        raise SystemExit(f"--start {start} is after --end {end}")
    return start, end


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
