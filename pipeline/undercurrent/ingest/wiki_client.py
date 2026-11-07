"""Shared plumbing for the Wikimedia APIs: languages, polite HTTP client, title/date helpers and
per-language site info. Used by wiki_top, wiki_resolve and wiki.

Titles are always stored in underscore form ("Barack_Obama"), which is what the pageviews API
uses and returns.
"""

import argparse
import datetime as dt
import json
import os
import threading
import time
from urllib.parse import quote

import httpx

from undercurrent.config import CACHE, WIKIMEDIA_USER_AGENT

# The 8 most-viewed Wikipedias (human pageviews, October 2026). English first.
LANGUAGES: tuple[str, ...] = ("en", "ja", "de", "fr", "ru", "es", "it", "zh")

REST = "https://wikimedia.org/api/rest_v1"
WIKIDATA = "https://www.wikidata.org/w/api.php"
ACTION = "https://en.wikipedia.org/w/api.php"  # English; use action_url(lang) for others

# Wikimedia's API rate limit (2026, https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits):
# clients identified only by User-Agent get 200 requests/minute in total, across every site and
# both the Action and REST APIs, and should keep at most 3 requests in flight. We stay just
# under. Authenticated established editors get 2000/min: override with UC_WIKI_REQUESTS_PER_MINUTE.
REQUESTS_PER_MINUTE = float(os.getenv("UC_WIKI_REQUESTS_PER_MINUTE", "190"))
MAX_IN_FLIGHT = 3
MIN_BACKOFF_S = 5.0  # wait at least this long after a 429/503 without a Retry-After header


def action_url(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def project(lang: str) -> str:
    return f"{lang}.wikipedia"


class WikiClient:
    """Thread-safe GET that respects Wikimedia's limits: one shared request rate for all hosts
    (REQUESTS_PER_MINUTE), at most MAX_IN_FLIGHT concurrent requests, and retries on
    429/5xx/network errors honouring Retry-After.

    max_rps, if given, overrides the rate (tests use a huge value).
    """

    def __init__(
        self,
        max_rps: float | None = None,
        retries: int = 6,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,  # tests pass an httpx.MockTransport
    ) -> None:
        self._http = httpx.Client(
            headers={"User-Agent": WIKIMEDIA_USER_AGENT, "Accept": "application/json"},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._interval = 1.0 / (max_rps or REQUESTS_PER_MINUTE / 60.0)
        self._lock = threading.Lock()
        self._in_flight = threading.Semaphore(MAX_IN_FLIGHT)
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
                with self._in_flight:
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
                    delay = max(MIN_BACKOFF_S, 2.0**attempt)
                with self._lock:  # everyone pauses: the limit is shared
                    self._next = max(self._next, time.monotonic() + delay)
                time.sleep(min(delay, 120))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"giving up on {url} {params or ''}: {last}")

    def close(self) -> None:
        self._http.close()


# Titles -----------------------------------------------------------------------------------------


def normalise_title(title: str) -> str:
    """'barack obama ' -> 'Barack_obama'. MediaWiki only capitalises the first letter."""
    t = "_".join(title.strip().split())
    return t[:1].upper() + t[1:]


def title_path(title: str) -> str:
    """URL path segment for the REST API ('/' and other reserved characters escaped)."""
    return quote(normalise_title(title), safe="")


# Site info (namespaces and main page differ per language) ---------------------------------------

SITEINFO_DIR = CACHE / "wiki_siteinfo"


def site_info(client: WikiClient, lang: str) -> dict:
    """{'main_page': 'メインページ', 'namespaces': ['特別', 'Special', ...]} for one language.

    'namespaces' holds every local name, canonical English name and alias of every namespace
    except articles (0), so `prefix in namespaces` means "not an article". Cached on disk.
    """
    path = SITEINFO_DIR / f"{lang}.json"
    if path.exists():
        return json.loads(path.read_text())
    payload = client.get_json(
        action_url(lang),
        params={
            "action": "query",
            "meta": "siteinfo",
            "siprop": "general|namespaces|namespacealiases",
            "format": "json",
            "formatversion": 2,
        },
    )
    if payload is None:
        raise RuntimeError(f"no siteinfo for {lang}")
    q = payload["query"]
    names: set[str] = set()
    for ns in q["namespaces"].values():
        if ns["id"] != 0:
            names |= {ns.get("name", ""), ns.get("canonical", "")}
    names |= {a["alias"] for a in q.get("namespacealiases", []) if a["id"] != 0}
    info = {
        "main_page": normalise_title(q["general"]["mainpage"]),
        "namespaces": sorted("_".join(n.split()) for n in names if n),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(info, ensure_ascii=False, indent=1))
    return info


# Dates and CLI helpers ----------------------------------------------------------------------------


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


def add_lang_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--langs",
        nargs="+",
        default=list(LANGUAGES),
        help=f"Wikipedia language codes (default: {' '.join(LANGUAGES)})",
    )


def date_range(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    end = args.end or utc_today() - dt.timedelta(days=1)
    start = args.start or end - dt.timedelta(days=args.days - 1)
    if start > end:
        raise SystemExit(f"--start {start} is after --end {end}")
    return start, end


def days_between(start: dt.date, end: dt.date) -> list[dt.date]:
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
