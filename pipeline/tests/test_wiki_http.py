"""Wikipedia ingest against a fake Wikimedia server (httpx.MockTransport): no network needed.

Covers the HTTP client (retries, 404s, User-Agent), the top-1000 downloader, the name resolver
and the per-title pageviews/edits cache.
"""

import datetime as dt
import json
from collections import Counter

import httpx
import pandas as pd
import pytest

from undercurrent.config import WIKIMEDIA_USER_AGENT
from undercurrent.ingest import wiki, wiki_client, wiki_resolve, wiki_top
from undercurrent.ingest.wiki_client import WikiClient

D1, D2, D3 = dt.date(2026, 11, 1), dt.date(2026, 11, 2), dt.date(2026, 11, 3)


class FakeWikimedia:
    """Just enough of the Wikimedia REST and Action APIs. Records every request path."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.user_agents: set[str] = set()
        self.fail_next: list[int] = []  # status codes to return before behaving normally

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.user_agents.add(request.headers["user-agent"])
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"retry-after": "0"})
        path, q = request.url.path, request.url.params
        if request.url.host == "en.wikipedia.org":
            key = q.get("gsrsearch") or q.get("titles")
            self.calls[f"action:{q.get('prop')}:{key}"] += 1
            return httpx.Response(200, json=self.action(q))
        self.calls[path] += 1
        return self.rest(path)

    # REST API ---------------------------------------------------------------------------------
    def rest(self, path: str) -> httpx.Response:
        parts = path.split("/")
        if "/top/" in path:
            access, day = parts[-4], "-".join(parts[-3:])
            if day == D3.isoformat():
                return httpx.Response(404)  # not published yet
            arts = [
                ("Main_Page", 1000),
                ("Bot_Target", 600),
                ("Real_News", 500),
                ("Special:Search", 400),
            ]
            if access == "desktop":
                arts = [("Bot_Target", 590), ("Real_News", 100)]
            return httpx.Response(
                200,
                json={"items": [{"articles": [{"article": a, "views": v, "rank": i} for i, (a, v) in enumerate(arts)]}]},
            )  # fmt: skip
        if "/per-article/" in path:
            title = parts[-4]
            if title == "No_Such_Page":
                return httpx.Response(404)
            # views on Nov 1 only; Nov 2 has no item (zero views) like the real API
            return httpx.Response(200, json={"items": [{"timestamp": "2026110100", "views": 42}]})
        return httpx.Response(404)

    # Action API -------------------------------------------------------------------------------
    def action(self, q: httpx.QueryParams) -> dict:
        if q.get("prop") == "revisions":
            if q.get("rvcontinue") is None:  # first page, then a continuation
                revs = [
                    {"timestamp": "2026-11-01T10:00:00Z"},
                    {"timestamp": "2026-11-01T11:00:00Z"},
                ]
                return {"continue": {"rvcontinue": "x", "continue": "||"}, "query": {"pages": [{"title": q["titles"], "revisions": revs}]}}  # fmt: skip
            return {"query": {"pages": [{"title": q["titles"], "revisions": [{"timestamp": "2026-11-02T09:00:00Z"}]}]}}  # fmt: skip
        if q.get("generator") == "search":
            hits = {
                "zelensky": [
                    {
                        "title": "Zelensky (disambiguation)",
                        "index": 1,
                        "pageprops": {"disambiguation": ""},
                    },
                    {"title": "Volodymyr Zelenskyy", "index": 2},
                ],
                "qwerty nonsense": [{"title": "Typewriter", "index": 1}],
            }
            return {"query": {"pages": hits.get(q["gsrsearch"], [])}}
        # exact title lookup with redirects
        variants = q["titles"].split("|")
        pages, redirects = [], []
        for v in variants:
            if v == "UN":
                redirects.append({"from": "UN", "to": "United Nations"})
                pages.append({"title": "United Nations"})
            elif v == "John Smith":
                pages.append({"title": "John Smith", "pageprops": {"disambiguation": ""}})
            else:
                pages.append({"title": v, "missing": True})
        return {"query": {"redirects": redirects, "pages": pages}}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Route every WikiClient to the fake server and every file to tmp_path."""
    server = FakeWikimedia()
    transport = httpx.MockTransport(server)

    def client(**kw) -> WikiClient:
        return WikiClient(**{**kw, "max_rps": 1e6, "transport": transport})

    for module in (wiki_top, wiki_resolve, wiki):
        monkeypatch.setattr(module, "WikiClient", client)
    monkeypatch.setattr(wiki_client.time, "sleep", lambda s: None)  # no real back-off waits
    monkeypatch.setattr(wiki_top, "RAW_DIR", tmp_path / "raw" / "wiki" / "top")
    monkeypatch.setattr(wiki_top, "OUT", tmp_path / "processed" / "wiki_top.parquet")
    monkeypatch.setattr(wiki_resolve, "CACHE_FILE", tmp_path / "cache" / "wiki_resolve.json")
    monkeypatch.setattr(wiki_resolve, "_cache", None)
    monkeypatch.setattr(wiki, "RAW", tmp_path / "raw")
    monkeypatch.setattr(wiki, "PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(wiki, "utc_today", lambda: dt.date(2026, 11, 10))
    server.client = client  # type: ignore[attr-defined]
    return server


# HTTP client ----------------------------------------------------------------------------------


def test_client_sends_user_agent_and_maps_404_to_none(fake) -> None:
    c = fake.client()
    assert c.get_json("https://wikimedia.org/api/rest_v1/nope") is None
    assert fake.user_agents == {WIKIMEDIA_USER_AGENT}


def test_client_retries_429_and_5xx_then_succeeds(fake) -> None:
    fake.fail_next = [429, 503]
    payload = fake.client().get_json(
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/X/daily/a/b"
    )
    assert payload == {"items": [{"timestamp": "2026110100", "views": 42}]}


def test_client_gives_up_after_repeated_errors(fake) -> None:
    fake.fail_next = [500] * 10
    with pytest.raises(RuntimeError, match="giving up"):
        fake.client(retries=3).get_json("https://wikimedia.org/api/rest_v1/x")


# Top-1000 -------------------------------------------------------------------------------------


def test_top_download_is_resumable_and_skips_unpublished_days(fake) -> None:
    c = fake.client()
    assert wiki_top.download(c, "all-access", D1) == "downloaded"
    assert wiki_top.download(c, "all-access", D1) == "cached"
    assert wiki_top.download(c, "all-access", D3) == "not published yet"
    assert not (wiki_top.RAW_DIR / "all-access" / f"{D3}.json").exists()
    assert (
        sum(n for p, n in fake.calls.items() if "/top/" in p) == 2
    )  # the cached call made no request


def test_top_build_table(fake) -> None:
    c = fake.client()
    for access in wiki_top.ACCESS:
        wiki_top.download(c, access, D1)
    wiki_top.download(c, "all-access", D2)  # D2 has no desktop list yet
    df = wiki_top.build_table()
    assert wiki_top.OUT.exists()
    d1 = df[df["day"] == D1]
    assert list(d1["title"]) == ["Bot_Target", "Real_News"]
    assert list(d1["suspect_bot"]) == [True, False]
    assert not df[df["day"] == D2]["suspect_bot"].any()  # unknown, so not flagged


# Resolver -------------------------------------------------------------------------------------


def test_resolve_exact_title_follows_redirects(fake) -> None:
    assert wiki_resolve.resolve("un") == "United_Nations"


def test_resolve_ambiguous_exact_title_gives_none_without_searching(fake) -> None:
    assert wiki_resolve.resolve("John  SMITH") is None
    assert "action:pageprops:john smith" not in fake.calls  # no full-text search was made


def test_resolve_falls_back_to_search_skipping_disambiguation(fake) -> None:
    assert wiki_resolve.resolve("zelensky") == "Volodymyr_Zelenskyy"
    assert wiki_resolve.resolve("qwerty nonsense") is None  # implausible top hit is rejected


def test_resolve_cache_is_persisted_and_reused(fake) -> None:
    wiki_resolve.resolve_many(["un", "zelensky", "John Smith"], progress=False)
    on_disk = json.loads(wiki_resolve.CACHE_FILE.read_text())
    assert on_disk == {
        "un": "United_Nations",
        "zelensky": "Volodymyr_Zelenskyy",
        "john smith": None,
    }
    before = sum(fake.calls.values())
    out = wiki_resolve.resolve_many(["UN", "Zelensky"], progress=False)
    assert out == {"UN": "United_Nations", "Zelensky": "Volodymyr_Zelenskyy"}
    assert sum(fake.calls.values()) == before  # all from cache


def test_resolve_network_failure_is_not_cached(fake) -> None:
    fake.fail_next = [500] * 100
    assert wiki_resolve.resolve("un") is None
    fake.fail_next = []
    assert "un" not in json.loads(wiki_resolve.CACHE_FILE.read_text())
    assert wiki_resolve.resolve("un") == "United_Nations"  # retried on the next call


# Pageviews and edits ----------------------------------------------------------------------------


def test_pageviews_fetch_cache_and_zero_fill(fake) -> None:
    c = fake.client()
    assert wiki.update_title(c, "pageviews", "Real_News", D1, D2) == "fetched"
    assert wiki.update_title(c, "pageviews", "Real_News", D1, D2) == "cached"
    df = wiki.build_table("pageviews")
    assert df.to_dict("records") == [
        {"title": "Real_News", "day": D1, "views": 42},
        {"title": "Real_News", "day": D2, "views": 0},  # absent from the API = zero views
    ]


def test_pageviews_wider_range_refetches(fake) -> None:
    c = fake.client()
    wiki.update_title(c, "pageviews", "Real_News", D2, D2)
    assert wiki.update_title(c, "pageviews", "Real_News", D1, D2) == "fetched"
    record = json.loads(wiki.cache_path("pageviews", "Real_News").read_text())
    assert (record["start"], record["end"]) == (D1.isoformat(), D2.isoformat())


def test_pageviews_missing_page_is_all_zeros(fake) -> None:
    wiki.update_title(fake.client(), "pageviews", "No_Such_Page", D1, D2)
    assert wiki.build_table("pageviews")["views"].tolist() == [0, 0]


def test_edits_follow_continuation(fake) -> None:
    wiki.update_title(fake.client(), "edits", "Real_News", D1, D2)
    df = wiki.build_table("edits")
    assert dict(zip(df["day"], df["edits"], strict=True)) == {D1: 2, D2: 1}
    assert fake.calls["action:revisions:Real News"] == 2  # two pages of results


def test_fetch_end_to_end_writes_both_tables(fake) -> None:
    wiki.fetch(["Real_News", " Real News "], D1, D2, ["pageviews", "edits"])  # same title twice
    views = pd.read_parquet(wiki.PROCESSED / "wiki_pageviews.parquet")
    edits = pd.read_parquet(wiki.PROCESSED / "wiki_edits.parquet")
    assert list(views.columns) == ["title", "day", "views"]
    assert list(edits.columns) == ["title", "day", "edits"]
    assert views["title"].unique().tolist() == ["Real_News"]
    assert len(views) == len(edits) == 2  # one row per day
