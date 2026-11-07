"""The zip-file front end (ingest/gdelt_zip.py): reading raw GKG lines, day selection, and a day
built end to end from fake downloads. No network."""

import datetime as dt
import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from undercurrent.ingest import gdelt_bq, gdelt_zip, gkg
from undercurrent.ingest.gdelt_zip import GkgFile, parse_index, read_raw, select_files, shard_days

DAY = dt.date(2026, 11, 6)


def line(record: str = "20261106120000-3", url: str = "https://a.co.uk/1", **over: str) -> str:
    f = [""] * gdelt_zip.N_FIELDS
    f[0], f[1], f[2], f[3], f[4] = record, record[:14], "1", url.split("/")[2], url
    f[7] = "MEDIA_MSM;TAX_FNCACT;"
    f[9] = "1#United Kingdom#UK#UK#54#-4#UK;4#Berlin, Germany#GM#GM16#52.5#13.4#-1746443"
    f[11] = "katy perry;russell brand"
    f[15] = "-4.03,3.61,7.64,11.25,25.28,0.69,668"
    f[26] = "<PAGE_TITLE>Producer &amp; star asked to step back</PAGE_TITLE>"
    for k, v in over.items():
        f[int(k[1:])] = v
    return "\t".join(f)


def test_read_raw_gives_the_bigquery_columns() -> None:
    tr = line(
        "20261106120000-T7",
        "https://b.no/2",
        f25="srclc:nor;eng:GT-NOR 1.0",
        f26="<PAGE_TITLE>Kriminelle driver som bedrifter &#x2013; og bruker KI</PAGE_TITLE>",
    )
    table, bad = read_raw("\n".join([line(), tr, "20261106120000-9\tshort"]).encode())
    assert bad == 1 and table.column_names == list(gkg.RAW_COLUMNS)
    rows = table.to_pylist()
    assert rows[0]["date_int"] == 20261106120000 and rows[0]["translation_info"] is None
    assert rows[0]["title"] == "Producer &amp; star asked to step back"  # unescaped by gkg
    articles = gkg.build_articles(table.to_pandas()).to_pylist()
    assert [a["translated"] for a in articles] == [False, True]
    assert articles[1]["lang"] == "nor" and "–" in articles[1]["title"]
    assert articles[0]["title"] == "Producer & star asked to step back"
    assert [loc["country"] for loc in articles[0]["locations"]] == ["GB", "DE"]
    assert articles[0]["themes"] == ["MEDIA_MSM", "TAX_FNCACT"]


def test_newlines_inside_extras_rejoin_the_record() -> None:
    head, tail = line().split("<PAGE_TITLE>")
    split = f"{head}<PAGE_LINKS>https://x.sk/a\nhttps://x.sk/b</PAGE_LINKS>\n<PAGE_TITLE>{tail}"
    table, bad = read_raw(f"{split}\n{line(url='https://a.co.uk/2')}\n".encode())
    assert (table.num_rows, bad) == (2, 0)
    assert table.column("title").to_pylist()[0].startswith("Producer")


def test_index_sampling_and_shards() -> None:
    text = "\n".join(
        [
            "10 aa http://data.gdeltproject.org/gdeltv2/20261106120000.gkg.csv.zip",
            "11 bb http://data.gdeltproject.org/gdeltv2/20261106121500.gkg.csv.zip",
            "12 cc http://data.gdeltproject.org/gdeltv2/20261106120000.export.CSV.zip",
            "broken line",
        ]
    )
    files = parse_index(text, "en")
    assert [f.name for f in files] == ["20261106120000.gkg", "20261106121500.gkg"]
    assert files[0].url.startswith("https://") and files[0].day == DAY
    tr = GkgFile("https://x/20261106120000.translation.gkg.csv.zip", 1, "", "tr", "20261106120000")
    assert len(select_files([*files, tr], 1)) == 2 and len(select_files([*files, tr], 4)) == 3

    start, end = dt.date(2025, 1, 1), dt.date(2026, 12, 31)
    shards = [shard_days(start, end, k, 3) for k in range(3)]
    every = [d for s in shards for d in s]
    assert len(every) == len(set(every)) == (end - start).days + 1
    assert shards[0] == sorted(shards[0], reverse=True)  # newest first
    assert shard_days(start, end, 0, 3, oldest_first=True) == sorted(shards[0])


def test_sync_commands(tmp_path: Path) -> None:
    (tmp_path / "articles").mkdir()
    (tmp_path / "gdelt_files").mkdir()
    gs = gdelt_zip.sync_commands(tmp_path, "gs://bkt/processed/")
    assert [c[-1] for c in gs] == ["gs://bkt/processed/articles", "gs://bkt/processed/gdelt_files"]
    assert all(c[:3] == ["gcloud", "storage", "rsync"] for c in gs)
    assert gdelt_zip.sync_commands(tmp_path, "hub:/data/")[0][0] == "rsync"


def test_a_day_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gdelt_zip, "STAGE", tmp_path / "stage")
    files = [
        GkgFile("https://x/20261106120000.gkg.csv.zip", 5, "", "en", "20261106120000"),
        GkgFile("https://x/20261106120000.translation.gkg.csv.zip", 5, "", "tr", "20261106120000"),
        GkgFile("https://x/20261106121500.gkg.csv.zip", 5, "", "en", "20261106121500"),
    ]
    bodies = {
        files[0].name: line(),
        files[1].name: line("20261106120000-T1", "https://a.co.uk/1")  # same URL, other feed
        + "\n"
        + line("20261106120000-T2", "https://b.no/2", f25="srclc:nor;eng:x"),
        files[2].name: None,  # listed but 404
    }
    monkeypatch.setattr(
        gdelt_zip, "fetch", lambda f: None if bodies[f.name] is None else bodies[f.name].encode()
    )
    for f in files:
        gdelt_zip.process_file(f)
    assert gdelt_zip.process_file(files[0])["rows"] == 1  # staged: not fetched again

    processed = tmp_path / "processed"
    report = gdelt_zip.finalize_day(processed, DAY, files, 0.0, False)
    assert report["articles"] == 1  # same URL in both feeds, then the same title
    assert report["feeds"]["en"]["files_missing"] == ["20261106121500.gkg"]
    assert report["feeds"]["tr"]["rows"] == 2

    articles = pq.read_table(gdelt_bq.articles_path(processed, DAY))
    assert articles.schema == gkg.ARTICLES_SCHEMA
    totals = pq.read_table(gdelt_bq.totals_path(processed, DAY)).to_pylist()
    assert sum(t["records"] for t in totals) == 3
    assert json.loads((processed / "gdelt_files" / f"{DAY}.json").read_text())["articles"] == 1
    # nothing temporary is left next to the real files, and the stage is cleared
    assert sorted(p.name for p in gdelt_bq.articles_path(processed, DAY).parent.iterdir()) == [
        "part.parquet"
    ]
    assert not (tmp_path / "stage" / DAY.isoformat()).exists()


def test_a_missing_domain_does_not_break_the_day() -> None:
    table, _ = read_raw(
        "\n".join(
            [
                line(),
                line(
                    url="https://c.org/3",
                    f3="",
                    f26="<PAGE_TITLE>Another headline entirely here</PAGE_TITLE>",
                ),
            ]
        ).encode()
    )
    articles = gkg.build_articles(table.to_pandas()).to_pylist()
    assert [a["domain"] for a in articles] == ["a.co.uk", None]
