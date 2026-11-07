"""Offline tests for the pure parts of the Wikipedia ingest (no network)."""

import datetime as dt

from undercurrent.ingest.wiki import count_by_day, record_rows
from undercurrent.ingest.wiki_client import normalise_title, title_path
from undercurrent.ingest.wiki_resolve import normalise_name, plausible, title_variants
from undercurrent.ingest.wiki_top import clean_top, is_article


def _top(articles: list[tuple[str, int]]) -> dict:
    return {"items": [{"articles": [{"article": a, "views": v, "rank": 0} for a, v in articles]}]}


def test_titles() -> None:
    assert normalise_title(" barack  obama ") == "Barack_obama"
    assert title_path("AC/DC") == "AC%2FDC"
    assert normalise_name("  Barack   OBAMA ") == "barack obama"
    assert title_variants("un") == ["Un", "UN"]
    assert title_variants("barack obama")[0] == "Barack Obama"


def test_is_article() -> None:
    assert is_article("Star_Wars:_Andor")
    assert is_article("Barack_Obama")
    assert not is_article("Main_Page")
    assert not is_article("Special:Search")
    assert not is_article("Wikipedia:Featured_pictures")


def test_clean_top_reranks_and_flags_bots() -> None:
    day = dt.date(2026, 11, 1)
    all_access = _top(
        [("Main_Page", 900), ("Bot_Target", 500), ("Real_News", 400), ("Special:Search", 300)]
    )
    desktop = _top([("Bot_Target", 495), ("Real_News", 100)])
    df = clean_top(day, all_access, desktop)
    assert list(df["title"]) == ["Bot_Target", "Real_News"]
    assert list(df["rank"]) == [1, 2]
    assert list(df["suspect_bot"]) == [True, False]
    assert clean_top(day, all_access, None)["suspect_bot"].sum() == 0


def test_plausible() -> None:
    assert plausible("vladimir putin", "Vladimir_Putin")
    assert plausible("volodymyr zelensky", "Volodymyr_Zelenskyy")
    assert plausible("putin", "Vladimir_Putin")
    assert not plausible("john smith", "Pocahontas")


def test_count_by_day() -> None:
    ts = ["2026-11-01T10:00:00Z", "2026-11-01T23:59:59Z", "2026-11-02T00:00:00Z"]
    assert count_by_day(ts) == {"2026-11-01": 2, "2026-11-02": 1}


def test_record_rows_zero_fills_only_complete_days() -> None:
    record = {
        "title": "X",
        "start": "2026-11-01",
        "end": "2026-11-05",
        "complete_to": "2026-11-03",
        "daily": {"2026-11-01": 5, "2026-11-04": 7},
    }
    rows = {r["day"].isoformat(): r["views"] for r in record_rows(record, "views")}
    # 11-02 and 11-03 are known zeros; 11-05 isn't published yet so it's absent, not zero
    assert rows == {"2026-11-01": 5, "2026-11-02": 0, "2026-11-03": 0, "2026-11-04": 7}
