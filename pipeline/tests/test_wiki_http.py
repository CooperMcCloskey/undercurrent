"""Wikipedia ingest against a fake Wikimedia server (httpx.MockTransport): no network needed.

Covers the HTTP client (rate-limit etiquette, retries, 404s, User-Agent), the per-language
top-1000 downloader, the name resolver and Wikidata language links, and the per-article
pageviews/edits cache with the per-entity table.
"""

import datetime as dt
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

import httpx
import pandas as pd
import pytest

from undercurrent.config import WIKIMEDIA_USER_AGENT
from undercurrent.ingest import wiki, wiki_client, wiki_resolve, wiki_top
from undercurrent.ingest.wiki_client import MAX_IN_FLIGHT, WikiClient

_real_sleep = time.sleep  # the fixture replaces time.sleep for the client's back-off
D1, D2, D3 = dt.date(2026, 11, 1), dt.date(2026, 11, 2), dt.date(2026, 11, 3)

SITEINFO = {
    "en": ("Main Page", {-1: ("Special", "Special"), 4: ("Wikipedia", "Project")}),
    "ja": ("メインページ", {-1: ("特別", "Special"), 4: ("Wikipedia", "Project")}),
}
QIDS = {"Real News": "Q1", "United Nations": "Q1065"}
SITELINKS = {
    "Q1": {"enwiki": "Real News", "jawiki": "本当のニュース"},
    "Q1065": {"enwiki": "United Nations", "jawiki": "国際連合", "dewiki": "Vereinte Nationen"},
}


class FakeWikimedia:
    """Just enough of the Wikimedia REST, Action and Wikidata APIs. Records every request."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.user_agents: set[str] = set()
        self.fail_next: list[int] = []  # status codes to return before behaving normally
        self.delay = 0.0
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay:
                _real_sleep(self.delay)
            return self.respond(request)
        finally:
            with self._lock:
                self.in_flight -= 1

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.user_agents.add(request.headers["user-agent"])
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), headers={"retry-after": "0"})
        host, path, q = request.url.host, request.url.path, request.url.params
        if host == "www.wikidata.org":
            self.calls["wikidata"] += 1
            return httpx.Response(200, json=self.wikidata(q))
        if host.endswith(".wikipedia.org"):
            lang = host.split(".")[0]
            kind = (
                "siteinfo" if q.get("meta") else
                "revisions" if q.get("prop") == "revisions" else
                "search" if q.get("generator") else
                "qids" if q.get("ppprop") == "wikibase_item" else
                "exact"
            )  # fmt: skip
            self.calls[f"{lang}:{kind}"] += 1
            self.calls[f"{lang}:{kind}:{q.get('titles') or q.get('gsrsearch')}"] += 1
            return httpx.Response(200, json=getattr(self, kind)(lang, q))
        self.calls[unquote(path)] += 1
        return self.rest(unquote(path))

    # REST API -----------------------------------------------------------------------------------
    def rest(self, path: str) -> httpx.Response:
        parts = path.split("/")
        if "/top/" in path:
            lang, access, day = parts[-5].split(".")[0], parts[-4], "-".join(parts[-3:])
            if day == D3.isoformat():
                return httpx.Response(404)  # not published yet
            arts = {
                "en": [
                    ("Main_Page", 1000),
                    ("Bot_Target", 600),
                    ("Real_News", 500),
                    ("Special:Search", 400),
                ],
                "ja": [("メインページ", 900), ("特別:検索", 800), ("本当のニュース", 300)],
            }[lang]
            if access == "desktop":
                arts = [("Bot_Target", 590), ("Real_News", 100)] if lang == "en" else []
            items = [{"article": a, "views": v, "rank": i} for i, (a, v) in enumerate(arts)]
            return httpx.Response(200, json={"items": [{"articles": items}]})  # fmt: skip
        if "/per-article/" in path:
            lang, title = parts[-7].split(".")[0], parts[-4]
            if title == "No_Such_Page":
                return httpx.Response(404)
            views = {"en": 42, "ja": 10}.get(lang, 1)
            # views on Nov 1 only; Nov 2 has no item (zero views), like the real API
            return httpx.Response(
                200, json={"items": [{"timestamp": "2026110100", "views": views}]}
            )
        return httpx.Response(404)

    # Action API ---------------------------------------------------------------------------------
    def siteinfo(self, lang: str, q: httpx.QueryParams) -> dict:
        main, ns = SITEINFO[lang]
        namespaces = {str(i): {"id": i, "name": n, "canonical": c} for i, (n, c) in ns.items()}
        namespaces["0"] = {"id": 0, "name": ""}
        return {"query": {"general": {"mainpage": main}, "namespaces": namespaces, "namespacealiases": []}}  # fmt: skip

    def revisions(self, lang: str, q: httpx.QueryParams) -> dict:
        title = q["titles"]
        if q.get("rvcontinue") is None:  # first page, then a continuation
            revs = [{"timestamp": "2026-11-01T10:00:00Z"}, {"timestamp": "2026-11-01T11:00:00Z"}]
            return {"continue": {"rvcontinue": "x", "continue": "||"}, "query": {"pages": [{"title": title, "revisions": revs}]}}  # fmt: skip
        return {"query": {"pages": [{"title": title, "revisions": [{"timestamp": "2026-11-02T09:00:00Z"}]}]}}  # fmt: skip

    def search(self, lang: str, q: httpx.QueryParams) -> dict:
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

    def exact(self, lang: str, q: httpx.QueryParams) -> dict:
        pages, redirects = [], []
        for v in q["titles"].split("|"):
            if v == "UN":
                redirects.append({"from": "UN", "to": "United Nations"})
                pages.append({"title": "United Nations"})
            elif v == "John Smith":
                pages.append({"title": "John Smith", "pageprops": {"disambiguation": ""}})
            elif v in ("Real News", "United Nations"):
                pages.append({"title": v})
            else:
                pages.append({"title": v, "missing": True})
        return {"query": {"redirects": redirects, "pages": pages}}

    def qids(self, lang: str, q: httpx.QueryParams) -> dict:
        pages = []
        for t in q["titles"].split("|"):
            if t in QIDS:
                pages.append({"title": t, "pageprops": {"wikibase_item": QIDS[t]}})
            else:
                pages.append({"title": t})  # exists but has no Wikidata item
        return {"query": {"pages": pages}}

    def wikidata(self, q: httpx.QueryParams) -> dict:
        sites = set(q["sitefilter"].split("|"))
        entities = {}
        for qid in q["ids"].split("|"):
            links = {
                s: {"site": s, "title": t} for s, t in SITELINKS.get(qid, {}).items() if s in sites
            }
            entities[qid] = {"id": qid, "sitelinks": links}
        return {"entities": entities}


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Route every WikiClient to the fake server and every file to tmp_path."""
    server = FakeWikimedia()
    transport = httpx.MockTransport(server)

    def client(**kw) -> WikiClient:
        return WikiClient(**{"max_rps": 1e6, **kw, "transport": transport})

    for module in (wiki_top, wiki_resolve, wiki):
        monkeypatch.setattr(module, "WikiClient", client)
    monkeypatch.setattr(wiki_client.time, "sleep", lambda s: None)  # no real back-off waits
    monkeypatch.setattr(wiki_client, "SITEINFO_DIR", tmp_path / "cache" / "siteinfo")
    monkeypatch.setattr(wiki_top, "RAW_DIR", tmp_path / "raw" / "wiki" / "top")
    monkeypatch.setattr(wiki_top, "OUT", tmp_path / "processed" / "wiki_top.parquet")
    monkeypatch.setattr(wiki_resolve, "CACHE_FILE", tmp_path / "cache" / "wiki_resolve.json")
    monkeypatch.setattr(wiki_resolve, "LINKS_FILE", tmp_path / "cache" / "wiki_links.json")
    monkeypatch.setattr(wiki_resolve, "_cache", None)
    monkeypatch.setattr(wiki_resolve, "_links", None)
    monkeypatch.setattr(wiki, "RAW", tmp_path / "raw")
    monkeypatch.setattr(wiki, "PROCESSED", tmp_path / "processed")
    monkeypatch.setattr(wiki, "utc_today", lambda: dt.date(2026, 11, 10))
    server.client = client  # type: ignore[attr-defined]
    return server


# HTTP client ------------------------------------------------------------------------------------


def test_client_sends_user_agent_and_maps_404_to_none(fake) -> None:
    assert fake.client().get_json("https://wikimedia.org/api/rest_v1/nope") is None
    assert fake.user_agents == {WIKIMEDIA_USER_AGENT}


def test_client_retries_429_and_5xx_then_succeeds(fake) -> None:
    fake.fail_next = [429, 503]
    payload = fake.client().get_json(
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/X/daily/a/b"
    )
    assert payload == {"items": [{"timestamp": "2026110100", "views": 42}]}


def test_client_retries_a_404_only_when_asked(fake) -> None:
    url = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user/X/daily/a/b"
    fake.fail_next = [404]
    assert fake.client().get_json(url) is None  # default: a 404 is believed
    fake.fail_next = [404, 404]
    assert fake.client().get_json(url, retry_404=2) is not None  # one-off 404s retried


def test_one_off_404_does_not_record_a_year_of_zeros(fake) -> None:
    fake.fail_next = [404]
    wiki.update_title(fake.client(), "pageviews", "en", "Real_News", D1, D2)
    assert wiki.build_table("pageviews")["views"].tolist() == [42, 0]  # real data, not [0, 0]


def test_top_download_retries_a_one_off_404(fake) -> None:
    fake.fail_next = [404]
    assert wiki_top.download(fake.client(), "en", "all-access", D1) == "downloaded"


def test_client_gives_up_after_repeated_errors(fake) -> None:
    fake.fail_next = [500] * 10
    with pytest.raises(RuntimeError, match="giving up"):
        fake.client(retries=3).get_json("https://wikimedia.org/api/rest_v1/x")


def test_client_never_has_more_than_3_requests_in_flight(fake, monkeypatch) -> None:
    fake.delay = 0.02  # each response takes 20 ms, so requests would overlap if allowed
    c = fake.client()
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda _: c.get_json("https://wikimedia.org/api/rest_v1/x"), range(24)))
    assert fake.max_in_flight == MAX_IN_FLIGHT == 3


def test_default_rate_is_under_wikimedias_200_per_minute() -> None:
    c = WikiClient()
    assert 60 / c._interval <= 200


# Top-1000 ---------------------------------------------------------------------------------------


def test_top_download_is_resumable_and_skips_unpublished_days(fake) -> None:
    c = fake.client()
    assert wiki_top.download(c, "en", "all-access", D1) == "downloaded"
    assert wiki_top.download(c, "en", "all-access", D1) == "cached"
    assert wiki_top.download(c, "en", "all-access", D3) == "missing (404)"
    assert not (wiki_top.RAW_DIR / "en" / "all-access" / f"{D3}.json").exists()
    # D1: 1 request, then cached (0); D3 is a past day, so its 404 is tried 1 + RETRY_404 times
    assert sum(n for p, n in fake.calls.items() if "/top/" in p) == 1 + 1 + wiki_top.RETRY_404


def test_top_recent_404_is_not_retried(fake, monkeypatch) -> None:
    monkeypatch.setattr(wiki_top, "utc_today", lambda: D3)  # D3 is "today": not published yet
    assert wiki_top.download(fake.client(), "en", "all-access", D3) == "not published yet"
    assert sum(n for p, n in fake.calls.items() if "/top/" in p) == 1


def test_top_prune_keeps_a_rolling_window(fake) -> None:
    c = fake.client()
    for day in (D1, D2):
        for access in wiki_top.ACCESS:
            wiki_top.download(c, "en", access, day)
    assert wiki_top.prune(keep_from=D2) == 2  # both D1 lists
    left = sorted(p.stem for p in wiki_top.RAW_DIR.rglob("*.json"))
    assert left == [D2.isoformat(), D2.isoformat()]
    assert set(wiki_top.build_table(c)["day"]) == {D2}


def test_top_build_table_cleans_each_language(fake) -> None:
    c = fake.client()
    for lang in ("en", "ja"):
        for access in wiki_top.ACCESS:
            wiki_top.download(c, lang, access, D1)
    wiki_top.download(c, "en", "all-access", D2)  # D2 has no desktop list yet
    df = wiki_top.build_table(c)
    assert wiki_top.OUT.exists()
    en1 = df[(df["lang"] == "en") & (df["day"] == D1)]
    assert list(en1["title"]) == ["Bot_Target", "Real_News"]
    assert list(en1["suspect_bot"]) == [True, False]
    ja1 = df[df["lang"] == "ja"]
    assert list(ja1["title"]) == ["本当のニュース"]  # Japanese main page and 特別: removed
    assert not df[df["day"] == D2]["suspect_bot"].any()  # unknown, so not flagged
    assert fake.calls["ja:siteinfo"] == 1
    wiki_top.build_table(c)
    assert fake.calls["ja:siteinfo"] == 1  # site info is cached on disk


# Resolver: names -> English titles -------------------------------------------------------------


def test_resolve_exact_title_follows_redirects(fake) -> None:
    assert wiki_resolve.resolve("un") == "United_Nations"


def test_resolve_batches_exact_lookups(fake) -> None:
    out = wiki_resolve.resolve_many(["un", "Real News", "John Smith"], progress=False)
    assert out == {"un": "United_Nations", "Real News": "Real_News", "John Smith": None}
    assert fake.calls["en:exact"] == 1  # three names, one request
    assert fake.calls["en:search"] == 0


def test_resolve_ambiguous_exact_title_gives_none_without_searching(fake) -> None:
    assert wiki_resolve.resolve("John  SMITH") is None
    assert fake.calls["en:search"] == 0


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


# Resolver: English titles -> Wikidata -> other languages ----------------------------------------


def test_link_languages(fake) -> None:
    links = wiki_resolve.link_languages(["Real_News", "United_Nations", "Orphan"], ["en", "ja"])
    assert links["United_Nations"] == {
        "qid": "Q1065",
        "titles": {"en": "United_Nations", "ja": "国際連合"},
    }
    assert links["Real_News"]["titles"]["ja"] == "本当のニュース"
    assert links["Orphan"] == {"qid": None, "titles": {"en": "Orphan"}}  # no Wikidata item
    assert fake.calls["en:qids"] == 1 and fake.calls["wikidata"] == 1  # batched


def test_link_languages_cache_and_new_languages(fake) -> None:
    wiki_resolve.link_languages(["United_Nations"], ["en", "ja"])
    wiki_resolve.link_languages(["United_Nations"], ["ja"])  # subset: cached
    assert fake.calls["wikidata"] == 1
    links = wiki_resolve.link_languages(["United_Nations"], ["en", "de"])  # new language: refetch
    assert fake.calls["wikidata"] == 2
    assert links["United_Nations"]["titles"] == {"en": "United_Nations", "de": "Vereinte_Nationen"}


def test_resolve_entities(fake) -> None:
    out = wiki_resolve.resolve_entities(["un", "John Smith"], ["en", "ja"], progress=False)
    assert out["un"] == {
        "title": "United_Nations",
        "qid": "Q1065",
        "titles": {"en": "United_Nations", "ja": "国際連合"},
    }
    assert out["John Smith"] is None


# Pageviews and edits ----------------------------------------------------------------------------


def test_pageviews_fetch_cache_and_zero_fill(fake) -> None:
    c = fake.client()
    assert wiki.update_title(c, "pageviews", "en", "Real_News", D1, D2) == "fetched"
    assert wiki.update_title(c, "pageviews", "en", "Real_News", D1, D2) == "cached"
    df = wiki.build_table("pageviews")
    assert df.to_dict("records") == [
        {"lang": "en", "title": "Real_News", "day": D1, "views": 42, "complete": True},
        {"lang": "en", "title": "Real_News", "day": D2, "views": 0, "complete": True},
    ]


def test_same_title_in_two_languages_is_cached_separately(fake) -> None:
    c = fake.client()
    wiki.update_title(c, "pageviews", "en", "Knesset", D1, D1)
    wiki.update_title(c, "pageviews", "de", "Knesset", D1, D1)
    assert wiki.cache_path("pageviews", "en", "Knesset") != wiki.cache_path(
        "pageviews", "de", "Knesset"
    )
    df = wiki.build_table("pageviews")
    assert sorted(zip(df["lang"], df["views"], strict=True)) == [("de", 1), ("en", 42)]


def test_pageviews_wider_range_refetches(fake) -> None:
    c = fake.client()
    wiki.update_title(c, "pageviews", "en", "Real_News", D2, D2)
    assert wiki.update_title(c, "pageviews", "en", "Real_News", D1, D2) == "fetched"
    record = json.loads(wiki.cache_path("pageviews", "en", "Real_News").read_text())
    assert (record["start"], record["end"]) == (D1.isoformat(), D2.isoformat())


def test_pageviews_missing_page_is_all_zeros(fake) -> None:
    wiki.update_title(fake.client(), "pageviews", "en", "No_Such_Page", D1, D2)
    assert wiki.build_table("pageviews")["views"].tolist() == [0, 0]


def test_edits_use_the_languages_own_site_and_follow_continuation(fake) -> None:
    wiki.update_title(fake.client(), "edits", "ja", "国際連合", D1, D2)
    df = wiki.build_table("edits")
    assert dict(zip(df["day"], df["edits"], strict=True)) == {D1: 2, D2: 1}
    assert fake.calls["ja:revisions"] == 2  # two pages of results, on ja.wikipedia.org
    assert fake.calls["en:revisions"] == 0


def test_fetch_end_to_end_across_languages(fake) -> None:
    wiki.fetch(["Real_News", " Real News "], D1, D2, langs=["en", "ja"])  # same title twice
    views = pd.read_parquet(wiki.PROCESSED / "wiki_pageviews.parquet")
    edits = pd.read_parquet(wiki.PROCESSED / "wiki_edits.parquet")
    ent = pd.read_parquet(wiki.PROCESSED / "wiki_entity_daily.parquet")
    assert list(views.columns) == ["lang", "title", "day", "views", "complete"]
    assert sorted(set(zip(views["lang"], views["title"], strict=True))) == [
        ("en", "Real_News"),
        ("ja", "本当のニュース"),
    ]
    assert len(views) == len(edits) == 4  # 2 articles x 2 days
    d1 = ent[ent["day"] == D1].iloc[0]
    assert (d1["qid"], d1["title_en"], d1["n_langs"]) == ("Q1", "Real_News", 2)
    assert (d1["views"], d1["views_en"], d1["views_ja"]) == (52, 42, 10)  # summed over languages
    assert d1["edits"] == 4  # 2 per language


def test_fetch_with_fewer_languages_keeps_the_others_in_the_entity_table(fake) -> None:
    # tier 1: pageviews in every language, then edits in English only
    wiki.fetch(["Real_News"], D1, D2, ["pageviews"], langs=["en", "ja"])
    wiki.fetch(["Real_News"], D1, D2, ["edits"], langs=["en"])
    ent = pd.read_parquet(wiki.PROCESSED / "wiki_entity_daily.parquet")
    d1 = ent[ent["day"] == D1].iloc[0]
    assert (d1["views"], d1["views_en"], d1["views_ja"]) == (52, 42, 10)  # ja not dropped
    assert d1["edits"] == 2 and d1["edits_en"] == 2
    assert "edits_ja" not in ent.columns


def test_fetch_pageviews_only_on_a_fresh_machine(fake) -> None:
    # tier 2: English pageviews only, before any edits exist
    wiki.fetch(["Real_News"], D1, D2, ["pageviews"], langs=["en"])
    ent = pd.read_parquet(wiki.PROCESSED / "wiki_entity_daily.parquet")
    assert ent["views"].tolist() == [42, 0]
    assert ent["edits"].isna().all()  # missing, not zero
