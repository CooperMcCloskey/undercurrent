"""Offline tests for the GDELT ingest (no network)."""

from datetime import date

import duckdb
import pyarrow as pa
import pytest

from undercurrent.ingest import gdelt_download as dl
from undercurrent.ingest import gkg_parse as gp
from undercurrent.ingest.fips import to_iso2


def test_fips_clashes() -> None:
    assert to_iso2("UK") == "GB"
    assert to_iso2("GM") == "DE"
    assert to_iso2("CH") == "CN"
    assert to_iso2("SZ") == "CH"
    assert to_iso2("AS") == "AU"
    assert to_iso2("AU") == "AT"
    assert to_iso2("RS") == "RU"
    assert to_iso2("") is None
    assert to_iso2("ZZ") is None  # unknown codes never pass through


def _row(
    ts: str,
    url: str,
    title: str,
    *,
    domain: str = "example.com",
    srclc: str = "",
    loc: str = "1#United Kingdom#UK#UK#54#-4#UK",
    collection: str = "1",
) -> str:
    cols = [""] * gp.N_COLS
    cols[0] = f"{ts}-0"
    cols[gp.C_DATE] = ts
    cols[gp.C_COLLECTION] = collection
    cols[gp.C_DOMAIN] = domain
    cols[gp.C_URL] = url
    cols[gp.C_THEMES] = "ARMEDCONFLICT;TAX_FNCACT;ARMEDCONFLICT"
    cols[gp.C_LOC_V1] = loc
    cols[gp.C_PERSONS] = "barack obama;joe biden"
    cols[gp.C_ORGS] = "united nations"
    cols[gp.C_TONE] = "-2.5,1,3.5,4.5,20,0,400"
    cols[gp.C_TRANSLATION] = f"srclc:{srclc};eng:GT-X 1.0" if srclc else ""
    cols[gp.C_EXTRAS] = (
        f"<PAGE_AUTHORS>x</PAGE_AUTHORS><PAGE_TITLE>{title}</PAGE_TITLE>" if title else ""
    )
    return "\t".join(cols)


def _gkg(*rows: str) -> bytes:
    return ("\n".join(rows) + "\n").encode()


TS = "20261106120000"


def test_parse_english_row() -> None:
    raw = _gkg(
        _row(TS, "https://a.com/1", "DWP confirms &#xA3;429  extra"),
        _row(TS, "https://a.com/2", ""),  # no title -> dropped
        _row(TS, "not-a-url", "Citation", collection="2"),  # non-web -> dropped
        _row(
            TS,
            "https://a.com/3",
            "Two places",
            loc="4#Berlin, Berlin, Germany#GM#GM16#52.5#13.4#-1;1#China#CH#CH#35#105#CH",
        ),
    )
    t, stats = gp.parse_gkg_bytes(raw, "english", TS)
    assert t.schema == gp.STAGING_SCHEMA
    assert stats["kept"] == 2 and stats["no_title"] == 1 and stats["non_web"] == 1
    r = t.to_pylist()
    assert r[0]["title"] == "DWP confirms £429 extra"
    assert r[0]["translated"] is False and r[0]["lang"] is None
    assert r[0]["themes"] == ["ARMEDCONFLICT", "TAX_FNCACT"]
    assert r[0]["tone"] == -2.5
    assert (r[0]["tone_pos"], r[0]["tone_neg"], r[0]["tone_polarity"]) == (1.0, 3.5, 4.5)
    assert (r[0]["tone_activity"], r[0]["tone_selfref"], r[0]["word_count"]) == (20.0, 0.0, 400)
    assert gp.parse_tone("") == [None] * 7
    assert gp.parse_tone("1.5,2") == [1.5, 2.0, None, None, None, None, None]
    assert r[0]["locations"][0]["country"] == "GB"
    assert [loc["country"] for loc in r[1]["locations"]] == ["DE", "CN"]
    assert r[0]["day"] == date(2026, 11, 6)
    assert r[0]["id"] == gp.article_id("https://a.com/1") and len(r[0]["id"]) == 16


def test_parse_all_names_and_counts() -> None:
    assert gp.parse_all_names("Santiago Pena,20;Santiago Pena,184;Central Bank,984") == [
        "Santiago Pena",
        "Central Bank",
    ]
    assert gp.parse_all_names("") == []
    counts = gp.parse_counts(
        "PROTEST#20##1#Paraguay#PA#PA#-22.993333#-57.996389#PA;"
        "PROTEST#20##1#Paraguay#PA#PA#-22.993333#-57.996389#PA;"
        "KILL#12#people#0######;"
        "bad#entry"
    )
    assert counts == [
        {
            "type": "PROTEST",
            "number": 20,
            "object": None,
            "location_type": 1,
            "location": "Paraguay",
            "country": "PY",
            "lat": -22.993333,
            "lon": -57.996389,
        },
        {
            "type": "KILL",
            "number": 12,
            "object": "people",
            "location_type": 0,
            "location": None,
            "country": None,
            "lat": None,
            "lon": None,
        },
    ]


def test_parse_translated_row() -> None:
    t, _ = gp.parse_gkg_bytes(
        _gkg(_row(TS, "https://b.no/x", "Hva om det er vi voksne", srclc="nor")),
        "translation",
        TS,
    )
    r = t.to_pylist()[0]
    assert r["translated"] is True and r["lang"] == "nor"
    assert r["title"] == "Hva om det er vi voksne"


def test_finalize_dedups_across_feeds() -> None:
    en, _ = gp.parse_gkg_bytes(
        _gkg(
            _row(TS, "https://a.com/1", "Big Story: Ceasefire agreed"),
            _row(TS, "https://b.com/1", "big story — ceasefire AGREED", domain="b.com"),
            _row(TS, "https://a.com/1", "Big Story: Ceasefire agreed"),  # same URL again
            _row(TS, "https://c.com/1", "Other story", domain="c.com"),
        ),
        "english",
        TS,
    )
    tr, _ = gp.parse_gkg_bytes(
        _gkg(_row(TS, "https://d.de/1", "Big story: ceasefire agreed", srclc="deu")),
        "translation",
        TS,
    )
    out, stats = gp.finalize_rows(pa.concat_tables([tr, en]), sampled=True)
    assert out.schema == gp.ARTICLE_SCHEMA
    assert stats["url_duplicates"] == 1 and stats["title_folded"] == 2
    rows = {r["url"]: r for r in out.to_pylist()}
    assert set(rows) == {"https://a.com/1", "https://c.com/1"}  # English kept on ties
    assert rows["https://a.com/1"]["dup_count"] == 3
    assert rows["https://c.com/1"]["dup_count"] == 1
    assert all(r["sampled"] for r in rows.values())


def test_select_day_sampling() -> None:
    index = [
        dl.GkgFile("english", f"20261106{h:02}{m:02}00", 1, "", f"https://x/{h}{m}.gkg.csv.zip")
        for h in range(24)
        for m in (0, 15, 30, 45)
    ]
    assert len(dl.select_day(index, date(2026, 11, 6))) == 96
    hourly = dl.select_day(index, date(2026, 11, 6), sample_per_hour=1)
    assert len(hourly) == 24 and all(g.ts[10:12] == "00" for g in hourly)
    assert len(dl.select_day(index, date(2026, 11, 6), sample_per_hour=1, hour_step=2)) == 12
    assert dl.select_day(index, date(2026, 11, 7)) == []
    assert dl.is_sampled(1, 1) and not dl.is_sampled(4, 1)


def test_shard_days_partition() -> None:
    days = [date(2026, 11, 6 - i) for i in range(6)]  # newest first
    shards = [gp.shard_days(days, k, 4) for k in range(4)]
    assert sorted(d for s in shards for d in s) == sorted(days)  # every day exactly once
    assert shards[0] == [date(2026, 11, 6), date(2026, 11, 2)]  # each shard newest first
    assert shards[3] == [date(2026, 11, 3)]


def test_index_line_parsing() -> None:
    line = "3837983 1357e9c3 http://data.gdeltproject.org/gdeltv2/20261107164500.gkg.csv.zip"
    g = dl._parse_index_line("english", line)
    assert g and g.ts == "20261107164500" and g.size == 3837983 and g.url.startswith("https://")
    tr = "7116267 2032ab57 http://data.gdeltproject.org/gdeltv2/20261107163000.translation.gkg.csv.zip"
    assert dl._parse_index_line("english", tr) is None
    assert dl._parse_index_line("translation", tr) is not None
    assert dl._parse_index_line("english", "1 2 http://x/20261107164500.export.CSV.zip") is None


def test_stage_finalize_resume(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gp, "STAGING", tmp_path / "staging")
    monkeypatch.setattr(gp, "ARTICLES", tmp_path / "articles")
    day = date(2026, 11, 6)
    ts2 = "20261106121500"
    expected = {"english": [TS, ts2], "translation": [TS, ts2]}

    def stage(feed: str, ts: str, url: str, srclc: str = "") -> None:
        t, s = gp.parse_gkg_bytes(_gkg(_row(ts, url, f"T {url}", srclc=srclc)), feed, ts)
        gp.stage_file(feed, day, ts, t, {**s, "status": "ok"})

    stage("english", TS, "https://a.com/1")
    stage("english", ts2, "https://a.com/2")
    gp.consolidate("english", day)
    assert gp.finalize_day(day, expected, sampled=False) is None  # translated feed not done
    stage("translation", TS, "https://b.it/1", "ita")
    gp.consolidate("translation", day)
    gp.stage_file("translation", day, ts2, None, {"status": "missing"})  # upstream 404
    assert set(gp.load_done("translation", day)) == {TS, ts2}
    gp.consolidate("translation", day)

    summary = gp.finalize_day(day, expected, sampled=False)
    assert summary and summary["rows_out"] == 3
    assert summary["feeds"]["translation"]["files_missing_upstream"] == 1
    glob = (tmp_path / "articles" / "**" / "*.parquet").as_posix()
    rel = duckdb.read_parquet(glob, hive_partitioning=True)
    assert rel.count("*").fetchall()[0][0] == 3
    assert {c for c in rel.columns} >= set(gp.ARTICLE_SCHEMA.names)
    langs = sorted(
        str(x[0]) for x in duckdb.sql(f"SELECT lang FROM read_parquet('{glob}')").fetchall()
    )
    assert langs == ["None", "None", "ita"]

    gp.prune_staging(day)
    assert set(gp.load_done("english", day)) == {TS, ts2}  # still resumable after pruning
    with pytest.raises(RuntimeError):
        gp.finalize_day(day, expected, sampled=False)
