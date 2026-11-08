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


# wiki_names and wiki_bulk (pure parts) ----------------------------------------------------------


def test_count_day_weights_by_dup_count_and_normalises() -> None:
    from undercurrent.ingest.wiki_names import count_day

    articles = pd.DataFrame(
        {
            "persons": [["Barack Obama", "x"], ["barack  obama"], None],
            "orgs": [["United Nations"], [], ["united nations"]],
            "dup_count": [3, 1, 2],
        }
    )
    out = count_day(articles, dt.date(2026, 11, 2))
    rows = {(r["kind"], r["name"]): (r["mentions"], r["articles"]) for r in out.to_dict("records")}
    assert rows == {
        ("persons", "barack obama"): (4, 2),  # one-letter names like "x" are dropped
        ("orgs", "united nations"): (5, 2),
    }


def test_select_ranks_each_week_and_kind_into_tiers() -> None:
    from undercurrent.ingest.wiki_names import entities, select

    mon, sun, next_mon = dt.date(2026, 11, 2), dt.date(2026, 11, 8), dt.date(2026, 11, 9)
    counts = pd.DataFrame(
        [
            (mon, "persons", "a", 10),
            (sun, "persons", "a", 5),  # same ISO week: summed to 15
            (mon, "persons", "b", 12),
            (mon, "persons", "c", 1),  # rank 3: over --top 2
            (mon, "orgs", "o", 1),
            (next_mon, "persons", "c", 50),  # next week, where c is rank 1
        ],
        columns=["day", "kind", "name", "mentions"],
    )
    names = select(counts, top=2, tier1=1)
    got = {
        (r["week"], r["kind"], r["name"]): (r["rank"], r["tier"]) for r in names.to_dict("records")
    }
    assert got == {
        (mon, "persons", "a"): (1, 1),
        (mon, "persons", "b"): (2, 2),
        (mon, "orgs", "o"): (1, 1),
        (next_mon, "persons", "c"): (1, 1),
    }
    ents = entities(names).set_index("name")
    assert ents.loc["c", "first_week"] == next_mon  # chosen only once it was big
    assert ents.loc["b", "tier"] == 2
    # tier1_week: the first week as tier 1, which is when 8 languages and edits become known.
    assert ents.loc["a", "tier1_week"] == mon and ents.loc["c", "tier1_week"] == next_mon
    assert pd.isna(ents.loc["b", "tier1_week"])  # never tier 1

    # A name that is tier 2 first and tier 1 a week later: its tier is 1 (over all weeks), but
    # tier1_week is the later week, so its first week doesn't see the 8 languages.
    later = pd.concat(
        [names, pd.DataFrame([(next_mon, "persons", "b", 99, 1, 1)], columns=names.columns)]
    )
    ents = entities(later).set_index("name")
    assert ents.loc["b", "tier"] == 1 and ents.loc["b", "first_week"] == mon
    assert ents.loc["b", "tier1_week"] == next_mon


def test_shards_split_names_exactly_once() -> None:
    from undercurrent.ingest.wiki_bulk import in_shard, parse_shard

    names = [f"name {k}" for k in range(500)]
    owners = [[i for i in range(4) if in_shard(x, i, 4)] for x in names]
    assert all(len(o) == 1 for o in owners)
    assert {o[0] for o in owners} == {0, 1, 2, 3}
    assert parse_shard("3/4") == (3, 4)
    with pytest.raises(SystemExit):
        parse_shard("4/4")


def test_merge_caches_prefers_hits_and_more_languages() -> None:
    from undercurrent.ingest.wiki_bulk import merge_links, merge_resolve

    resolve = {"a": None, "b": "B"}
    merge_resolve(resolve, {"a": "A", "b": None, "c": None})
    assert resolve == {"a": "A", "b": "B", "c": None}
    links = {"A": {"qid": "Q1", "titles": {"en": "A"}, "checked": ["en"]}}
    more = {"qid": "Q1", "titles": {"en": "A", "ja": "あ"}, "checked": ["en", "ja"]}
    merge_links(links, {"A": more})
    assert links["A"] == more
    merge_links(links, {"A": {"qid": "Q1", "titles": {"en": "A"}, "checked": ["en"]}})
    assert links["A"] == more


def test_entity_table_counts_only_fetched_languages(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A tier-2 entity linked in 2 languages but fetched in English only has n_langs 1, and a
    run with no edits at all still builds."""
    monkeypatch.setattr(wiki, "PROCESSED", tmp_path)
    d1 = dt.date(2026, 11, 1)
    views = pd.DataFrame(
        [("en", "Tier_Two", d1, 5, True)], columns=["lang", "title", "day", "views", "complete"]
    )
    edits = pd.DataFrame(columns=["lang", "title", "day", "edits", "complete"])
    links = {"Tier_Two": {"qid": "Q2", "titles": {"en": "Tier_Two", "ja": "第二"}}}
    out = wiki.build_entity_table(views, edits, links, ["en", "ja"])
    row = out.iloc[0]
    assert (row["qid"], row["views"], row["n_langs"]) == ("Q2", 5, 1)
    assert pd.isna(row["edits"])
