"""Offline tests for the pure parts of the Wikipedia ingest (no network, no fake server)."""

import datetime as dt

import pandas as pd
import pytest

from undercurrent.ingest import wiki
from undercurrent.ingest.wiki import count_by_day, record_rows
from undercurrent.ingest.wiki_client import normalise_title, title_path
from undercurrent.ingest.wiki_resolve import (
    _chunks_by_variants,
    normalise_name,
    plausible,
    title_variants,
)
from undercurrent.ingest.wiki_top import clean_top, is_article

EN = {"main_page": "Main_Page", "namespaces": ["Special", "Wikipedia", "Talk", "File"]}
JA = {"main_page": "メインページ", "namespaces": ["特別", "Special", "ノート", "Wikipedia"]}


def _top(articles: list[tuple[str, int]]) -> dict:
    return {"items": [{"articles": [{"article": a, "views": v, "rank": 0} for a, v in articles]}]}


def test_titles() -> None:
    assert normalise_title(" barack  obama ") == "Barack_obama"
    assert normalise_title("バラク・オバマ") == "バラク・オバマ"
    assert title_path("AC/DC") == "AC%2FDC"
    assert normalise_name("  Barack   OBAMA ") == "barack obama"
    assert title_variants("un") == ["Un", "UN"]
    assert title_variants("barack obama")[0] == "Barack Obama"


def test_is_article_uses_each_languages_namespaces() -> None:
    assert is_article("Star_Wars:_Andor", EN)
    assert not is_article("Main_Page", EN)
    assert not is_article("Special:Search", EN)
    assert not is_article("特別:検索", JA)
    assert not is_article("メインページ", JA)
    assert is_article("Re:ゼロから始める異世界生活", JA)  # "Re" isn't a namespace


def test_clean_top_reranks_and_flags_bots() -> None:
    day = dt.date(2026, 11, 1)
    all_access = _top(
        [("Main_Page", 900), ("Bot_Target", 500), ("Real_News", 400), ("Special:Search", 300)]
    )
    desktop = _top([("Bot_Target", 495), ("Real_News", 100)])
    df = clean_top("en", day, all_access, desktop, EN)
    assert list(df.columns) == [
        "lang",
        "day",
        "rank",
        "title",
        "views",
        "desktop_share",
        "suspect_bot",
    ]
    assert list(df["title"]) == ["Bot_Target", "Real_News"]
    assert list(df["rank"]) == [1, 2]
    assert list(df["suspect_bot"]) == [True, False]
    assert set(df["lang"]) == {"en"}
    assert clean_top("en", day, all_access, None, EN)["suspect_bot"].sum() == 0


def test_plausible() -> None:
    assert plausible("vladimir putin", "Vladimir_Putin")
    assert plausible("volodymyr zelensky", "Volodymyr_Zelenskyy")
    assert plausible("putin", "Vladimir_Putin")
    assert not plausible("john smith", "Pocahontas")


def test_chunks_respect_the_50_title_limit() -> None:
    names = [f"name {i}" for i in range(100)] + ["un", "eu", "nato"]
    chunks = _chunks_by_variants(names)
    assert sum(len(c) for c in chunks) == len(names)
    assert all(sum(len(title_variants(n)) for n in c) <= 50 for c in chunks)
    assert len(chunks) < len(names) / 10  # batching actually happens


def test_count_by_day() -> None:
    ts = ["2026-11-01T10:00:00Z", "2026-11-01T23:59:59Z", "2026-11-02T00:00:00Z"]
    assert count_by_day(ts) == {"2026-11-01": 2, "2026-11-02": 1}


def test_record_rows_zero_fills_only_complete_days() -> None:
    record = {
        "lang": "de",
        "title": "X",
        "start": "2026-11-01",
        "end": "2026-11-05",
        "complete_to": "2026-11-03",
        "daily": {"2026-11-01": 5, "2026-11-04": 7},
    }
    rows = {r["day"].isoformat(): (r["views"], r["complete"]) for r in record_rows(record, "views")}
    # 11-02 and 11-03 are known zeros; 11-04 was reported but may still grow; 11-05 isn't
    # published yet, so it's absent rather than zero
    assert rows == {
        "2026-11-01": (5, True),
        "2026-11-02": (0, True),
        "2026-11-03": (0, True),
        "2026-11-04": (7, False),
    }
    assert {r["lang"] for r in record_rows(record, "views")} == {"de"}


def test_build_entity_table_sums_languages(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(wiki, "PROCESSED", tmp_path)
    d1, d2 = dt.date(2026, 11, 1), dt.date(2026, 11, 2)
    views = pd.DataFrame(
        [
            ("en", "Barack_Obama", d1, 100, True),
            ("ja", "バラク・オバマ", d1, 20, True),
            ("en", "Barack_Obama", d2, 50, False),  # incomplete: must be ignored
            ("en", "Orphan", d1, 7, True),  # no Wikidata item
            ("en", "Not_Linked", d1, 999, True),  # not in links: ignored
        ],
        columns=["lang", "title", "day", "views", "complete"],
    )
    edits = pd.DataFrame(
        [("ja", "バラク・オバマ", d1, 3, True)],
        columns=["lang", "title", "day", "edits", "complete"],
    )
    links = {
        "Barack_Obama": {"qid": "Q76", "titles": {"en": "Barack_Obama", "ja": "バラク・オバマ"}},
        "Orphan": {"qid": None, "titles": {"en": "Orphan"}},
    }
    out = wiki.build_entity_table(views, edits, links, ["en", "ja"])
    assert (tmp_path / "wiki_entity_daily.parquet").exists()
    rows = {r["qid"]: r for r in out.to_dict("records")}
    assert set(rows) == {"Q76", "en:Orphan"}
    obama = rows["Q76"]
    assert (obama["day"], obama["views"], obama["edits"], obama["n_langs"]) == (d1, 120, 3, 2)
    assert (obama["views_en"], obama["views_ja"], obama["edits_ja"]) == (100, 20, 3)
    assert rows["en:Orphan"]["views"] == 7 and rows["en:Orphan"]["n_langs"] == 1
