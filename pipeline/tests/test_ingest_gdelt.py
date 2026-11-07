"""GDELT GKG -> articles: parsing, dedupe, daily totals, and the BigQuery command with a fake
client (no Google account needed). The last tests check that what it writes is what the vector
store and the story tracker read."""

import datetime as dt
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from undercurrent.ingest import gdelt_bq, gkg
from undercurrent.ingest.fips import fips_to_iso

D1, D2 = dt.date(2026, 11, 3), dt.date(2026, 11, 4)

ROTTERDAM = "4#Rotterdam, Zuid-Holland, Netherlands#NL#NL11#51.9225#4.47917#-2150203"
GERMANY = "1#Germany#GM#GM#51#9#GM"
BEIJING = "4#Beijing, Beijing, China#CH#CH22#39.9289#116.388#-1898541"


def raw_row(record_id, date_int, url, title, **extra) -> dict:
    row = {
        "record_id": record_id,
        "date_int": date_int,
        "domain": url.split("/")[2] if url else None,
        "url": url,
        "title": title,
        "locations": "",
        "persons": "",
        "orgs": "",
        "themes": "",
        "tone": "-2.5,1.2,3.7,4.9,20.1,0,300",
        "translation_info": "",
    }
    row.update(extra)
    return row


RAW = pd.DataFrame(
    [
        # English feed, the first copy of a syndicated headline
        raw_row(
            "20261103101500-1",
            20261103101500,
            "https://a.example/strike",
            "Port strike &amp; chaos  in Rotterdam",
            locations=f"{ROTTERDAM};{GERMANY};{ROTTERDAM}",
            persons="jan de vries;anna;jan de vries",
            orgs="port authority",
            themes="STRIKE;TAX_FNCACT_WORKERS;STRIKE;",
        ),
        # the same headline on another site, later: folds into the first (dup_count 2)
        raw_row(
            "20261103111500-7",
            20261103111500,
            "https://b.example/x",
            "Port strike & chaos in Rotterdam",
        ),
        # the first URL again, from the translated feed: dropped as a duplicate URL
        raw_row(
            "20261103120000-T3", 20261103120000, "https://a.example/strike", "Port strike chaos"
        ),
        # a translated-feed article
        raw_row(
            "20261103103000-T5",
            20261103103000,
            "https://c.example/fr",
            "Grève au port de Rotterdam",
            translation_info="srclc:fra;eng:GT-FRA 1.0",
            locations=BEIJING,
        ),
        # no <PAGE_TITLE>: dropped from articles, but counted in the totals
        raw_row("20261103130000-2", 20261103130000, "https://d.example/y", None, locations=GERMANY),
        # the next day
        raw_row("20261104000500-1", 20261104000500, "https://e.example/z", "Next day headline"),
    ]
)


def test_fips_codes_that_clash_with_iso() -> None:
    assert fips_to_iso("GM") == "DE"  # not Gambia
    assert fips_to_iso("CH") == "CN"  # not Switzerland
    assert fips_to_iso("SZ") == "CH"
    assert fips_to_iso("UK") == "GB"
    assert fips_to_iso("NL") == "NL"  # not Caribbean Netherlands
    assert fips_to_iso("RI") == "RS" and fips_to_iso("KV") == "XK"
    assert fips_to_iso("XX") is None and fips_to_iso("") is None and fips_to_iso(None) is None


def test_locations_v1_and_v2_formats() -> None:
    v1 = gkg.parse_locations(f"{ROTTERDAM};{GERMANY};{ROTTERDAM};1#Nowhere#XX#XX##")
    assert [(p["name"].split(",")[0], p["country"], p["type"]) for p in v1] == [
        ("Rotterdam", "NL", 4),
        ("Germany", "DE", 1),
    ]  # repeats and places without coordinates are dropped
    assert v1[0]["lat"] == pytest.approx(51.9225) and v1[0]["lon"] == pytest.approx(4.47917)
    v2 = gkg.parse_locations("4#Beijing, China#CH#CH22#12345#39.9289#116.388#-1898541#120")
    assert v2 == [
        {"name": "Beijing, China", "country": "CN", "lat": 39.9289, "lon": 116.388, "type": 4}
    ]
    assert gkg.parse_locations(None) == [] and gkg.parse_locations("") == []


def test_lists_tone_and_language() -> None:
    assert gkg.split_list("A;B;;A;") == ["A", "B"]
    assert gkg.split_list("SMITH, BARNEY INC") == ["SMITH, BARNEY INC"]  # commas in names stay
    assert gkg.split_list("KILL,120;ELECTION,45") == ["KILL", "ELECTION"]  # V2 offsets go
    assert gkg.parse_tone("-2.5,1.2,3.7") == -2.5 and gkg.parse_tone("") is None
    assert gkg.source_language("srclc:fra;eng:GT-FRA 1.0") == "fra"
    assert gkg.source_language("") is None


def test_articles_dedupe_and_schema() -> None:
    day1 = gkg.split_by_day(RAW)[D1]
    table = gkg.build_articles(day1)
    assert table.schema == gkg.ARTICLES_SCHEMA
    rows = table.to_pylist()
    assert [r["title"] for r in rows] == [
        "Port strike & chaos in Rotterdam",
        "Grève au port de Rotterdam",
    ]
    strike, greve = rows
    assert strike["dup_count"] == 2 and greve["dup_count"] == 1
    assert strike["id"] == gkg.article_id("https://a.example/strike") and len(strike["id"]) == 16
    assert strike["published_at"] == dt.datetime(2026, 11, 3, 10, 15, tzinfo=dt.UTC)
    assert [p["country"] for p in strike["locations"]] == ["NL", "DE"]
    assert strike["persons"] == ["jan de vries", "anna"] and strike["themes"] == [
        "STRIKE",
        "TAX_FNCACT_WORKERS",
    ]
    assert strike["tone"] == -2.5 and strike["domain"] == "a.example"
    assert (strike["translated"], strike["lang"]) == (False, None)
    assert (greve["translated"], greve["lang"]) == (True, "fra")
    assert greve["locations"][0]["country"] == "CN"
    assert not any(r["sampled"] for r in rows)


def test_totals_count_everything_before_filtering() -> None:
    totals = gkg.daily_totals(gkg.split_by_day(RAW)[D1]).to_pylist()
    by = {(t["feed"], t["country"]): (t["records"], t["with_title"]) for t in totals}
    assert by == {
        ("english", "NL"): (1, 1),
        ("english", "DE"): (1, 0),  # the untitled row still counts
        ("english", None): (1, 1),
        ("translated", "CN"): (1, 1),
        ("translated", None): (1, 1),
    }
    assert sum(t["records"] for t in totals) == 5


def test_empty_input() -> None:
    empty = RAW.iloc[0:0]
    assert gkg.build_articles(empty).num_rows == 0
    assert gkg.daily_totals(empty).num_rows == 0
    assert gkg.split_by_day(empty) == {}


def test_batches_and_query_window() -> None:
    days = [D1, D2, dt.date(2026, 11, 5), dt.date(2026, 11, 8), dt.date(2026, 11, 9)]
    assert [len(b) for b in gdelt_bq.batches(days, max_days=2)] == [2, 1, 2]
    params = gdelt_bq.query_parameters(D1, D2)
    assert params["first_partition"] == dt.datetime(2026, 11, 3, tzinfo=dt.UTC)
    assert params["last_partition"] == dt.datetime(2026, 11, 5, tzinfo=dt.UTC)  # one extra
    assert (params["date_from"], params["date_to"]) == (20261103000000, 20261104235959)
    for column in ("Extras", "Locations", "Themes", "V2Tone", "TranslationInfo"):
        assert column in gdelt_bq.QUERY
    assert "GCAM" not in gdelt_bq.QUERY  # the biggest column, never read


class FakeJob:
    def __init__(self, table: pa.Table, dry_run: bool) -> None:
        self.table, self.total_bytes_processed = table, 123_000_000_000 if dry_run else None
        self.total_bytes_billed = 0 if dry_run else 42_000_000_000

    def result(self):
        return self

    def to_arrow(self, create_bqstorage_client: bool = True) -> pa.Table:
        return self.table


class FakeClient:
    """Answers QUERY from RAW the way BigQuery would: rows whose DATE is in the window."""

    def __init__(self) -> None:
        self.calls: list[tuple[bool, dict]] = []

    def query(self, sql: str, job_config) -> FakeJob:
        params = {p.name: p.value for p in job_config.query_parameters}
        self.calls.append((job_config.dry_run, params))
        keep = RAW["date_int"].between(params["date_from"], params["date_to"])
        return FakeJob(pa.Table.from_pandas(RAW[keep], preserve_index=False), job_config.dry_run)


def test_run_writes_one_file_per_day_and_resumes(tmp_path: Path, capsys) -> None:
    client = FakeClient()
    gdelt_bq.run(client, D1, D2, processed=tmp_path, dry_run=True)
    dry = capsys.readouterr().out
    assert "Total 123.0 GB for 2 days" in dry  # two consecutive days: one query, one estimate
    assert not (tmp_path / "articles").exists()  # a dry run writes nothing

    client = FakeClient()
    gdelt_bq.run(client, D1, D2, processed=tmp_path)
    assert len(client.calls) == 1  # consecutive days share one query
    for day in (D1, D2):
        assert gdelt_bq.articles_path(tmp_path, day).exists()
        assert gdelt_bq.totals_path(tmp_path, day).exists()
    out = capsys.readouterr().out
    assert (
        "2026-11-03: 5 GKG rows -> 2 articles" in out
        and "2026-11-04: 1 GKG rows -> 1 articles" in out
    )

    client = FakeClient()
    gdelt_bq.run(client, D1, D2, processed=tmp_path)
    assert client.calls == []  # both days done: nothing queried
    gdelt_bq.run(client, D1, D1, processed=tmp_path, force=True)
    assert len(client.calls) == 1


def test_downstream_readers_understand_the_files(tmp_path: Path) -> None:
    """The vector store and the story tracker read exactly what this writes."""
    from undercurrent.stories.track import day_meta
    from undercurrent.vectors.store import day_inputs

    gdelt_bq.run(FakeClient(), D1, D2, processed=tmp_path)
    ids, texts = day_inputs("news", tmp_path, D1)
    assert sorted(texts) == ["Grève au port de Rotterdam", "Port strike & chaos in Rotterdam"]
    titles, places = day_meta(tmp_path, D1, ids)
    assert sorted(titles) == sorted(texts)
    assert {p[2] for row in places for p in row} == {"NL", "DE", "CN"}
    ids2, _ = day_inputs("news", tmp_path, D2)
    assert len(ids2) == 1


def test_days_not_pulled_yet_are_not_embedded_as_empty(tmp_path: Path) -> None:
    """Embedding can run alongside the pull: a day with no articles folder yet is "no input",
    not an empty day that would be marked done and never revisited."""
    from undercurrent.vectors.store import day_inputs

    gdelt_bq.run(FakeClient(), D1, D1, processed=tmp_path)
    assert day_inputs("news", tmp_path, D1) is not None
    assert day_inputs("news", tmp_path, D2) is None
