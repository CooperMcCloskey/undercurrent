"""stories/wiki_entities.py: each story's Wikipedia entities, chosen point in time, for C's panel."""

import datetime as dt
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent.forecast import panel
from undercurrent.stories import wiki_entities as we

D = dt.date
MON = D(2026, 3, 2)  # a Monday

SELECTION = pd.DataFrame(
    [
        # name, first_week (a Monday), tier1_week (None = never tier 1), title_en, qid
        ("joe biden", D(2026, 2, 23), D(2026, 3, 9), "Joe_Biden", "Q6279"),
        ("jo biden", D(2026, 2, 23), D(2026, 2, 23), "Joe_Biden", "Q6279"),  # alias, tier 1 first
        ("emmanuel macron", D(2026, 1, 5), None, "Emmanuel_Macron", "Q3052772"),
        ("olaf scholz", D(2026, 1, 5), D(2026, 3, 9), "Olaf_Scholz", "Q61053"),
        ("kamala harris", MON, MON, "Kamala_Harris", "Q10853588"),  # picked in the week of MON
        ("reuters", D(2026, 1, 5), None, "Reuters", "Q130879"),  # a source, not a subject
        ("supreme court", D(2026, 1, 5), None, "Supreme_court", "Q190752"),  # a generic match
        ("united states", D(2026, 1, 5), None, "United_States", "Q30"),  # a country
        ("nobody", D(2026, 1, 5), None, None, None),  # unresolved
    ],
    columns=["name", "first_week", "tier1_week", "title_en", "qid"],
).assign(kind="persons", tier=1, mentions=1)

# article id, story, day, persons, orgs
ARTICLES = [
    # quick: 3 articles on its first day, so (min_articles 3) its entities are chosen that day
    ("q1", "quick", MON, ["Joe  Biden", "Kamala Harris"], ["Reuters"]),
    ("q2", "quick", MON, ["jo biden", "kamala harris"], ["supreme court"]),
    ("q3", "quick", MON, ["emmanuel macron"], ["united states"]),
    ("q4", "quick", MON + dt.timedelta(1), ["olaf scholz"], []),
    ("q5", "quick", MON + dt.timedelta(1), ["olaf scholz"], None),
    # slow: 2 articles in its first 3 days, so its entities are chosen on its 3rd day
    ("s1", "slow", MON, ["emmanuel macron", "olaf scholz"], []),
    ("s2", "slow", MON + dt.timedelta(2), ["olaf scholz", "emmanuel macron", "kamala harris"], []),
    ("s3", "slow", MON + dt.timedelta(3), ["kamala harris", "joe biden"], []),
    ("s4", "slow", MON + dt.timedelta(3), ["kamala harris", "joe biden"], []),
    # noise: names nothing in the selection
    ("n1", "noise", MON, ["someone else"], ["nobody"]),
    ("n2", "noise", MON, ["someone else"], ["nobody"]),
]

LOCATION = pa.struct([("name", pa.string()), ("country", pa.string()), ("lat", pa.float64()),
                      ("lon", pa.float64()), ("type", pa.int32())])  # fmt: skip


def _write(processed: Path, rows: list[tuple]) -> None:
    processed.mkdir(parents=True, exist_ok=True)
    arts = pa.Table.from_pydict(
        {
            "id": [r[0] for r in rows],
            "day": [r[2] for r in rows],
            "domain": ["example.com"] * len(rows),
            "tone": [0.0] * len(rows),
            "locations": [[] for _ in rows],
            "persons": [r[3] for r in rows],
            "orgs": [r[4] for r in rows],
        },
        schema=pa.schema(
            [
                ("id", pa.string()),
                ("day", pa.date32()),
                ("domain", pa.string()),
                ("tone", pa.float64()),
                ("locations", pa.list_(LOCATION)),
                ("persons", pa.list_(pa.string())),
                ("orgs", pa.list_(pa.string())),
            ]
        ),  # fmt: skip
    )
    pq.write_table(arts, processed / "articles.parquet")
    assign = pa.table(
        {
            "article_id": pa.array([r[0] for r in rows], pa.string()),
            "story_id": pa.array([r[1] for r in rows], pa.string()),
            "day": pa.array([r[2] for r in rows], pa.date32()),
            "similarity": pa.array([0.9] * len(rows), pa.float32()),
        }
    )
    pq.write_table(assign, processed / "story_assignments.parquet")


@pytest.fixture
def selection() -> pd.DataFrame:
    return we.load_selection_frame(SELECTION)


def _build(processed: Path, selection: pd.DataFrame, **kw) -> pd.DataFrame:
    kw = {"days": 3, "top": 2, "min_mentions": 2, "min_articles": 3} | kw
    return we.build(processed, selection, **kw)


def test_entities_are_chosen_from_the_first_days_point_in_time(
    tmp_path: Path, selection: pd.DataFrame
) -> None:
    _write(tmp_path, ARTICLES)
    out = _build(tmp_path, selection)
    assert list(out.columns) == we.COLUMNS
    got = {sid: g for sid, g in out.groupby("story_id")}
    assert set(got) == {"quick", "slow"}  # noise names nothing matchable

    quick = got["quick"]
    # Biden counted once per article across both spellings ("Joe  Biden" normalises to "joe biden").
    # Harris: first picked in the week of MON, which hadn't ended on MON. Reuters, the US and the
    # generic Supreme_court are left out. Macron is in only one article. Scholz comes after day 1.
    assert quick["qid"].tolist() == ["Q6279"]
    assert quick["articles"].tolist() == [2]
    assert quick["chosen_on"].tolist() == [MON]
    assert quick["name"].tolist() == ["jo biden"]  # tie on articles -> alphabetical
    assert quick["title_en"].tolist() == ["Joe_Biden"]
    # 8 languages from the end of the first week any of its names was tier 1 (the alias's).
    assert quick["full_from"].tolist() == [D(2026, 3, 1)]

    slow = got["slow"]
    # Chosen on its 3rd day (2 articles never reach 3); day 4's Biden and Harris don't count.
    assert slow["chosen_on"].unique().tolist() == [MON + dt.timedelta(2)]
    assert slow["qid"].tolist() == ["Q3052772", "Q61053"]  # tie on 2 articles -> by qid
    assert slow["rank"].tolist() == [1, 2]
    assert slow["full_from"].tolist() == [None, D(2026, 3, 15)]  # Macron never tier 1
    assert not out.duplicated(["story_id", "qid"]).any()


def test_later_days_never_change_the_choice(tmp_path: Path, selection: pd.DataFrame) -> None:
    _write(tmp_path / "a", ARTICLES)
    later = [
        (f"x{i}", sid, MON + dt.timedelta(day), ["kamala harris", "olaf scholz"], ["united states"])
        for i, (sid, day) in enumerate([("quick", 1), ("quick", 2), ("quick", 9), ("slow", 3)] * 3)
    ]
    _write(tmp_path / "b", ARTICLES + later)
    pd.testing.assert_frame_equal(
        _build(tmp_path / "a", selection), _build(tmp_path / "b", selection)
    )


def test_every_forecast_row_comes_on_or_after_chosen_on(
    tmp_path: Path, selection: pd.DataFrame
) -> None:
    """The panel join has no date, so a story's entities must be known by its first forecast
    row: the first day its 7-day article count reaches min_articles (forecast/dataset.py)."""
    rows = [(f"r{i}", "ramp", MON + dt.timedelta(i // 2), ["olaf scholz"], []) for i in range(12)]
    _write(tmp_path, rows)
    for days in (1, 3, 7):
        for min_articles in (1, 3, 4, 50):
            out = we.build(tmp_path, selection, days, 5, 1, min_articles)
            per_day = pd.Series(1, index=[r[2] for r in rows]).groupby(level=0).sum()
            per_day = per_day.reindex(pd.date_range(MON, periods=14).date, fill_value=0)
            a7 = per_day.rolling(7, min_periods=1).sum()
            eligible = a7[a7 >= min_articles]
            if not len(eligible):  # never gets a forecast row, so it isn't written at all
                assert out.empty
                continue
            chosen = out["chosen_on"].iloc[0]
            assert eligible.index[0] >= chosen
            assert chosen <= MON + dt.timedelta(days - 1)


def test_the_forecast_panel_picks_it_up(tmp_path: Path, selection: pd.DataFrame) -> None:
    _write(tmp_path, ARTICLES)
    we.write(_build(tmp_path, selection), tmp_path / we.OUT_NAME)
    days = pd.date_range(MON - dt.timedelta(10), periods=20).date
    pq.write_table(
        pa.table(
            {
                "qid": ["Q6279"] * 20 + ["Q3052772"] * 20,
                "day": pa.array(list(days) * 2, pa.date32()),
                "views": [100] * 20 + [10] * 20,
                "edits": [1] * 40,
                "views_en": [60] * 20 + [5] * 20,
            }
        ),
        tmp_path / "wiki_entity_daily.parquet",
    )
    _, _, wiki = panel.build_panel(tmp_path)
    assert wiki is not None
    by_story = wiki.groupby("story_id")["wiki_views"].sum()
    # quick = Biden: English views (60) until his full_from (03-01), then 8 languages (100); the
    # panel joins days up to 7 after the story's last article (03-03), so 02-20 .. 03-10.
    assert by_story["quick"] == 60 * 9 + 100 * 10
    # slow = Macron (never tier 1: English, 5 a day) and Scholz (no Wikipedia rows here).
    assert by_story["slow"] == 5 * 20


def test_stories_that_never_get_a_forecast_are_left_out(
    tmp_path: Path, selection: pd.DataFrame
) -> None:
    # slow has 4 articles in its busiest 7 days, quick 5: at min_articles 5 only quick is kept.
    _write(tmp_path, ARTICLES)
    assert set(_build(tmp_path, selection, min_articles=5)["story_id"]) == {"quick"}
    assert _build(tmp_path, selection, min_articles=6).empty


def test_no_matches_gives_an_empty_table_with_the_columns(
    tmp_path: Path, selection: pd.DataFrame
) -> None:
    _write(tmp_path, [r for r in ARTICLES if r[1] == "noise"])
    out = _build(tmp_path, selection)
    assert out.empty and list(out.columns) == we.COLUMNS
    we.write(out, tmp_path / we.OUT_NAME)
    schema = pq.read_schema(tmp_path / we.OUT_NAME)
    assert schema.field("full_from").type == pa.date32() and schema.names == we.COLUMNS


def test_a_selection_without_tier1_week_means_english_only(tmp_path: Path) -> None:
    _write(tmp_path, ARTICLES)
    old = we.load_selection_frame(SELECTION.drop(columns="tier1_week"))
    out = _build(tmp_path, old)
    assert out["full_from"].isna().all() and len(out) == 3


@pytest.mark.parametrize(
    "title",
    ["Supreme_court", "Central_bank", "Ministry_of_justice", "Secondary_school", "Price_of_oil",
     "List_of_diplomatic_missions_of_the_United_States", "Reuters", "United_States", "Court",
     "X_(social_network)", "White_House", "National_Assembly", "", None],
)  # fmt: skip
def test_excluded(title: str | None) -> None:
    assert we.excluded(title)


@pytest.mark.parametrize(
    "title",
    ["Joe_Biden", "Supreme_Court_of_the_United_States", "Death_of_Mahsa_Amini", "Eid_al-Adha",
     "Bank_of_England", "Prince_Harry,_Duke_of_Sussex", "Tricia_McLaughlin_(spokeswoman)",
     "2022_Idaho_student_murders", "Khan_Yunis", "Murdaugh_family", "Boeing", "Lyle_and_Erik_Menendez"],
)  # fmt: skip
def test_kept(title: str) -> None:
    assert not we.excluded(title)
