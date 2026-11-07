"""The loaders that read the other workstreams' hand-off files (docs/WORKPLAN.md section 3).

The fake files below follow the documented schemas. If A or B change a schema, these tests
fail with a message pointing at forecast/schema.py and forecast/panel.py, which is the point.
"""

import datetime as dt
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent.forecast import panel, proxy
from undercurrent.forecast.dataset import build_rows

LOCATION = pa.struct(
    [
        ("name", pa.string()),
        ("country", pa.string()),
        ("lat", pa.float64()),
        ("lon", pa.float64()),
        ("type", pa.int32()),
    ]
)


def _place(country: str | None) -> dict:
    return {"name": "x", "country": country, "lat": 0.0, "lon": 0.0, "type": 3}


ARTICLES = [
    # id, day, domain, tone, countries, themes
    ("a1", "2026-01-01", "bbc.com", -2.0, ["FR"], ["PROTEST", "ECON_INFLATION"]),
    ("a2", "2026-01-01", "lemonde.fr", -4.0, ["FR", "DE"], ["PROTEST"]),
    ("a3", "2026-01-01", "bbc.com", 0.0, [], ["ELECTION"]),
    ("a4", "2026-01-02", "dw.com", -1.0, ["DE"], ["ARMEDCONFLICT"]),
    ("a5", "2026-01-02", "bbc.com", -3.0, ["FR"], ["STRIKE"]),
]


def _articles_table(with_day: bool = True) -> pa.Table:
    cols = {
        "id": [a[0] for a in ARTICLES],
        "domain": [a[2] for a in ARTICLES],
        "tone": [a[3] for a in ARTICLES],
        "locations": [[_place(c) for c in a[4]] for a in ARTICLES],
        "themes": [a[5] for a in ARTICLES],
    }
    if with_day:
        cols["day"] = [dt.date.fromisoformat(a[1]) for a in ARTICLES]
    schema = pa.schema(
        [("id", pa.string()), ("domain", pa.string()), ("tone", pa.float64()),
         ("locations", pa.list_(LOCATION)), ("themes", pa.list_(pa.string()))]
        + ([("day", pa.date32())] if with_day else [])
    )  # fmt: skip
    return pa.Table.from_pydict(cols, schema=schema)


def _write(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path)


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    _write(tmp_path / "articles.parquet", _articles_table())
    _write(
        tmp_path / "story_assignments.parquet",
        pa.table(
            {
                "article_id": ["a1", "a2", "a3", "a4", "a5"],
                "story_id": ["paris-protests"] * 2 + ["vote"] + ["border-clash", "paris-protests"],
                "day": [dt.date.fromisoformat(a[1]) for a in ARTICLES],
                "similarity": [0.9] * 5,
            }
        ),
    )
    return tmp_path


def test_panel_from_the_real_files(processed: Path) -> None:
    result, totals, wiki = panel.build_panel(processed)
    assert wiki is None  # no Wikipedia files yet: that is fine
    protests = result[(result["story_id"] == "paris-protests")].set_index("day")
    day1, day2 = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")
    assert protests.loc[day1, "n_articles"] == 2
    assert protests.loc[day1, "n_domains"] == 2  # bbc.com and lemonde.fr
    assert protests.loc[day1, "n_countries"] == 2  # FR and DE
    assert protests.loc[day1, "tone_mean"] == pytest.approx(-3.0)
    assert protests.loc[day2, "n_articles"] == 1
    vote = result[result["story_id"] == "vote"].iloc[0]
    assert vote["n_countries"] == 0  # an article with no places

    # Totals are every article that day, before any story filtering.
    assert totals.set_index("day")["total_articles"].to_dict() == {day1: 3, day2: 2}
    # And the panel goes straight into the model's rows.
    assert not build_rows(result, [1], totals=totals, min_articles=1, burn_in=0).empty


def test_optional_files_add_category_first_seen_and_wikipedia(processed: Path) -> None:
    _write(
        processed / "stories_daily.parquet",
        pa.table(
            {
                "story_id": ["paris-protests", "paris-protests", "vote", "border-clash"],
                "day": [
                    dt.date(2026, 1, 1),
                    dt.date(2026, 1, 2),
                    dt.date(2026, 1, 1),
                    dt.date(2026, 1, 2),
                ],
                "category": ["society", "society", "politics", "conflict"],
                "first_seen": [
                    dt.date(2026, 1, 1),
                    dt.date(2026, 1, 1),
                    dt.date(2026, 1, 1),
                    dt.date(2026, 1, 2),
                ],
            }
        ),
    )
    _write(
        processed / "story_wiki_entities.parquet",
        pa.table({"story_id": ["vote", "vote"], "qid": ["Q1", "Q2"]}),
    )
    _write(
        processed / "wiki_entity_daily.parquet",
        pa.table(
            {
                "qid": ["Q1", "Q2", "Q1"],
                "day": [dt.date(2026, 1, 1)] * 2 + [dt.date(2026, 1, 2)],
                "views": [100, 50, 70],
                "edits": [3, 1, 2],
                "views_en": [60, 20, 40],
            }
        ),
    )
    result, _, wiki = panel.build_panel(processed)
    assert set(result["category"]) == {"society", "politics", "conflict"}
    assert result.loc[result["story_id"] == "border-clash", "first_seen"].iloc[0] == pd.Timestamp(
        "2026-01-02"
    )
    wiki = wiki.set_index(["story_id", "day"])
    assert (
        wiki.loc[("vote", pd.Timestamp("2026-01-01")), "wiki_views"] == 150
    )  # two entities summed
    assert wiki.loc[("vote", pd.Timestamp("2026-01-02")), "wiki_views_en"] == 40


def test_partitioned_articles_work_too(tmp_path: Path) -> None:
    """docs/WORKPLAN.md allows articles/day=YYYY-MM-DD/*.parquet instead of one file."""
    table = _articles_table(with_day=False)
    for d in ("2026-01-01", "2026-01-02"):
        keep = [i for i, a in enumerate(ARTICLES) if a[1] == d]
        _write(tmp_path / "articles" / f"day={d}" / "part.parquet", table.take(keep))
    _write(
        tmp_path / "story_assignments" / "part.parquet",
        pa.table(
            {
                "article_id": [a[0] for a in ARTICLES],
                "story_id": ["s"] * 5,
                "day": [dt.date.fromisoformat(a[1]) for a in ARTICLES],
            }
        ),
    )
    result, totals, _ = panel.build_panel(tmp_path)
    assert result["n_articles"].tolist() == [3, 2]
    assert totals["total_articles"].sum() == 5


def test_a_changed_schema_names_the_file_to_fix(tmp_path: Path) -> None:
    _write(tmp_path / "articles.parquet", _articles_table().drop(["domain"]))
    _write(
        tmp_path / "story_assignments.parquet",
        pa.table({"article_id": ["a1"], "story_id": ["s"], "day": [dt.date(2026, 1, 1)]}),
    )
    with pytest.raises(ValueError, match=r"articles is missing columns \['domain'\].*schema\.py"):
        panel.build_panel(tmp_path)
    with pytest.raises(FileNotFoundError, match="story_assignments and articles"):
        panel.build_panel(tmp_path / "nothing-here")


def test_proxy_stories_from_themes_and_countries(tmp_path: Path) -> None:
    _write(tmp_path / "articles.parquet", _articles_table())
    result, totals, _ = proxy.build_panel(tmp_path)
    by = result.set_index(["story_id", "day"])
    day1, day2 = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-02")
    assert by.loc[("protest-fr", day1), "n_articles"] == 2  # a1 and a2: PROTEST, main place FR
    assert by.loc[("protest-fr", day2), "n_articles"] == 1  # a5: STRIKE counts as protest
    assert by.loc[("economy-fr", day1), "n_articles"] == 1  # a1 also has ECON_INFLATION
    assert by.loc[("conflict-de", day2), "n_articles"] == 1
    assert ("election-fr", day1) not in by.index and not any(
        s.startswith("election") for s in result["story_id"]
    )  # a3 has no place
    assert result.loc[result["story_id"] == "protest-fr", "category"].iloc[0] == "society"
    assert totals["total_articles"].sum() == 5
    # Story ids must satisfy the contract's pattern, since they end up in the exports.
    import re

    from undercurrent import contracts

    assert all(re.match(contracts.STORY_ID_PATTERN, s) for s in result["story_id"])
