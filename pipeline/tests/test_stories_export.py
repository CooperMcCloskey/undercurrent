"""stories.export: synthetic tracker output, articles and forecasts -> data/export/, checked
against the contract tests and by hand (counts, footprints, point in time, outcomes)."""

import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import test_contracts as contract

from undercurrent import contracts as c
from undercurrent.ingest.gkg import ARTICLES_SCHEMA
from undercurrent.stories import export
from undercurrent.stories.footprint import add_article, footprint
from undercurrent.stories.track import SCHEMAS

D0 = dt.date(2026, 10, 1)
DAYS = [D0 + dt.timedelta(i) for i in range(10)]
END = DAYS[-1]


def loc(name, country, lat, lon, type_) -> dict:
    return {"name": name, "country": country, "lat": lat, "lon": lon, "type": type_}


ROTTERDAM = loc("Rotterdam, Zuid-Holland, Netherlands", "NL", 51.92, 4.48, 4)
NETHERLANDS = loc("Netherlands", "NL", 52.5, 5.75, 1)
JAPAN = loc("Japan", "JP", 36.0, 138.0, 1)
PARIS = loc("Paris, Ile-de-France, France", "FR", 48.86, 2.35, 4)
FRANCE = loc("France", "FR", 46.0, 2.0, 1)
GERMANY = loc("Germany", "DE", 51.0, 9.0, 1)
BERLIN = loc("Berlin, Berlin, Germany", "DE", 52.52, 13.4, 4)

STRIKE, QUAKE, TINY, NOWHERE, SUMMIT, JUNK, FRENCH = (
    "port-strike-in-rotterdam-aaaaaa",
    "earthquake-in-japan-bbbbbb",
    "tiny-story-cccccc",
    "no-places-dddddd",
    "summit-eeeeee",
    "story-ffffff",
    "zz-only-french-gggggg",
)
CATEGORY = {
    STRIKE: "economy",
    QUAKE: "disaster",
    TINY: "other",
    NOWHERE: "politics",
    SUMMIT: "politics",
    JUNK: "other",
    FRENCH: "politics",
}
SUMMIT_PLACES = [[PARIS, FRANCE, GERMANY], [BERLIN, GERMANY], [GERMANY]]


def articles_for(day: dt.date) -> list[tuple[str, dict]]:
    """(story_id, article) pairs published on `day`."""
    i = (day - D0).days
    out = []

    def art(story: str, k: int, locations: list[dict], **extra) -> None:
        aid = f"{story[:4]}{i:02d}{k:02d}".ljust(16, "0")
        row = {
            "id": aid,
            "url": f"https://news.example/{aid}",
            "domain": "news.example",
            "published_at": dt.datetime.combine(day, dt.time(8, k), tzinfo=dt.UTC),
            "title": f"{story} headline {i}-{k}",
            "locations": locations,
            "persons": ["jan de vries"] if story == STRIKE else [],
            "orgs": [],
            "themes": ["STRIKE", "PROTEST", "LEADER"] if story == STRIKE else [],
            "tone": -2.0 if story == STRIKE else -6.0,
            "dup_count": 1,
            "sampled": False,
            "translated": k == 0,
            "lang": "fra" if k == 0 else None,
            **extra,
        }
        out.append((story, row))

    for k in range(5):
        art(STRIKE, k, [ROTTERDAM, NETHERLANDS])
    if i >= 3:
        for k in range(2):
            art(QUAKE, k, [JAPAN])
    if i <= 1:
        art(TINY, 0, [JAPAN])
    for k in range(4):
        art(NOWHERE, k, [])
    if day == END:
        for k, places in enumerate(SUMMIT_PLACES):
            art(SUMMIT, k, places)
    # A "story" of unrelated generic headlines: no shared names, a different country each time.
    for k, place in enumerate([JAPAN, PARIS, BERLIN, ROTTERDAM]):
        art(JUNK, k, [place])
    # A story with no English headline at all: its title falls back to names and place.
    if day == END:
        for k in range(3):
            art(FRENCH, k, [PARIS], lang="fra", translated=True, persons=["emmanuel macron"])
    return out


def write(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    root = tmp_path / "processed"
    first_seen: dict[str, dt.date] = {}
    for day in DAYS:
        pairs = articles_for(day)
        write(
            root / "articles" / f"day={day}" / "part.parquet",
            [a for _, a in pairs],
            ARTICLES_SCHEMA,
        )
        assignments, per_story = [], {}
        for k, (story, a) in enumerate(pairs):
            sim = 0.9 - 0.01 * k
            assignments.append(
                {"article_id": a["id"], "story_id": story, "day": day, "similarity": sim}
            )
            per_story[story] = per_story.get(story, 0) + 1
            first_seen.setdefault(story, day)
        daily = [
            {
                "story_id": s,
                "day": day,
                "n_articles_day": n,
                "first_seen": first_seen[s],
                "label": s.rsplit("-", 1)[0].replace("-", " "),
                "category": CATEGORY[s],
            }
            for s, n in per_story.items()
        ]
        write(
            root / "story_assignments" / f"{day}.parquet", assignments, SCHEMAS["story_assignments"]
        )
        write(root / "stories_daily" / f"{day}.parquet", daily, SCHEMAS["stories_daily"])
    return root


def add_forecasts(root: Path, horizons=(1, 3)) -> None:
    rows = []
    for story, day in [(STRIKE, DAYS[2]), (QUAKE, DAYS[8])]:
        for h in horizons:
            rows.append(
                {
                    "story_id": story,
                    "day": day,
                    "days": h,
                    "articles_7d": 20.0,
                    "low": 10.0,
                    "high": 30.0,
                    "model_version": "test-1",
                    "factors": [{"name": "momentum", "contribution": 0.2}],
                    "actual_articles_7d": None,  # the exporter grades against its own snapshots
                    "source": "walkforward",
                    "fold": 0,
                }
            )
    pd.DataFrame(rows).to_parquet(root / "forecasts.parquet", index=False)
    metrics = c.ModelMetrics(
        model_version="test-1",
        test_start=DAYS[0],
        test_end=END,
        baseline_name="persistence",
        horizons=[
            c.HorizonMetrics(days=h, n_test=10, mae_log2=0.3, baseline_mae_log2=0.4, coverage=0.8)
            for h in horizons
        ],
    )
    (root / "model_metrics.json").write_text(metrics.model_dump_json())


def load(out: Path, rel: str) -> dict:
    return json.loads((out / rel).read_text())


def snapshot(out: Path, day: dt.date) -> dict[str, dict]:
    return {s["id"]: s for s in load(out, f"snapshots/{day}.json")["stories"]}


def test_export_end_to_end(processed: Path, tmp_path: Path) -> None:
    add_forecasts(processed)
    out = export.run(processed, tmp_path / "export")
    contract.test_export_matches_contract(out)
    contract.test_forecasts_are_consistent(out)

    meta = c.Meta.model_validate(load(out, "meta.json"))
    assert meta.dates == DAYS and meta.latest == END
    assert meta.horizons == [1, 3] and meta.model and meta.model.model_version == "test-1"

    last = snapshot(out, END)
    assert list(last) == [STRIKE, QUAKE, SUMMIT, FRENCH]  # biggest first; NOWHERE has no places
    strike, quake = last[STRIKE], last[QUAKE]
    assert strike["article_count_7d"] == 35 and quake["article_count_7d"] == 14
    # 35 articles vs 15 in days 0-2 (the data starts there): log2(36 / 16)
    assert strike["growth_7d"] == 1.17 and strike["tone"] == -2.0
    # The tracker said "economy"; every article's GDELT themes say strike/protest: society
    assert strike["category"] == "society" and strike["first_seen"] == str(D0)
    assert quake["category"] == "disaster"  # no themes: keeps the tracker's category
    # The most central English headline of the week: k = 0 is French, so k = 1 (similarity 0.89)
    assert re.fullmatch(rf"{STRIKE} headline \d-1", strike["title"]), strike["title"]
    assert last[FRENCH]["title"] == "Emmanuel Macron · Paris"
    # "Netherlands" is covered by Rotterdam: all the heat is on the city
    assert strike["footprint"] == [{"lat": 51.92, "lon": 4.48, "weight": 1.0, "radius_km": 75}]
    assert strike["location"]["name"].startswith("Rotterdam")
    assert quake["footprint"][0]["radius_km"] == 500 and quake["location"]["country"] == "JP"


def test_point_in_time_and_cutoffs(processed: Path, tmp_path: Path) -> None:
    out = export.run(processed, tmp_path / "export")
    first, third = snapshot(out, DAYS[0]), snapshot(out, DAYS[2])
    assert list(first) == [STRIKE]  # TINY is below --min-articles, QUAKE hasn't started
    assert third[STRIKE]["article_count_7d"] == 15
    # QUAKE starts on day 3 with 2 articles a day: below --min-articles 3 until day 4
    assert QUAKE not in snapshot(out, DAYS[3]) and QUAKE in snapshot(out, DAYS[4])
    assert snapshot(out, DAYS[4])[QUAKE]["growth_7d"] == pytest.approx(2.322, abs=1e-3)
    assert all("forecast" in s and s["forecast"] is None for s in third.values())

    small = export.run(processed, tmp_path / "small", top=1, start=DAYS[5])
    assert load(small, "meta.json")["dates"] == [str(d) for d in DAYS[5:]]
    assert all(list(snapshot(small, d)) == [STRIKE] for d in DAYS[5:])
    assert sorted(p.name for p in (small / "stories").iterdir()) == [f"{STRIKE}.json"]


def test_outcomes_come_from_later_snapshots(processed: Path, tmp_path: Path) -> None:
    add_forecasts(processed)
    out = export.run(processed, tmp_path / "export")
    strike = snapshot(out, DAYS[2])[STRIKE]
    assert [h["days"] for h in strike["forecast"]["horizons"]] == [1, 3]
    assert strike["outcome"]["horizons"] == [
        {"days": 1, "articles_7d": 20},
        {"days": 3, "articles_7d": 30},
    ]
    quake = snapshot(out, DAYS[8])[QUAKE]
    assert quake["outcome"]["horizons"] == [{"days": 1, "articles_7d": 14}]  # day 11 is the future
    assert snapshot(out, DAYS[3])[STRIKE]["forecast"] is None


def test_sql_footprint_matches_python(processed: Path, tmp_path: Path) -> None:
    out = export.run(processed, tmp_path / "export")
    weights: dict = {}
    for places in SUMMIT_PLACES:
        add_article(weights, places)
    points, location = footprint(weights)
    summit = snapshot(out, END)[SUMMIT]
    assert summit["footprint"] == [p.model_dump(mode="json") for p in points]
    assert [p["weight"] for p in summit["footprint"]] == [0.5, 0.3333, 0.1667]  # DE, Berlin, Paris
    assert summit["location"]["name"].startswith("Berlin")  # Germany's top city, not its centre


def test_story_detail(processed: Path, tmp_path: Path) -> None:
    out = export.run(processed, tmp_path / "export", items=4)
    detail = c.StoryDetail.model_validate(load(out, f"stories/{STRIKE}.json"))
    assert len(detail.timeline) == 10 and {p.articles for p in detail.timeline} == {5}
    assert len(detail.items) == 4
    sims = [i.similarity for i in detail.items]
    assert sims == sorted(sims, reverse=True)
    assert all([loc.country for loc in i.locations] == ["NL"] for i in detail.items)
    assert {i.language for i in detail.items} == {
        "fr"
    }  # k = 0 articles are the top 4 by similarity
    assert [(e.name, e.kind) for e in detail.entities] == [("Jan de Vries", "person")]
    assert detail.locations[0].name.startswith("Rotterdam")

    summit = c.StoryDetail.model_validate(load(out, f"stories/{SUMMIT}.json"))
    assert [i.locations[0].name.split(",")[0] for i in summit.items] == [
        "Paris",
        "Berlin",
        "Germany",
    ]
    assert [loc.name.split(",")[0] for loc in summit.items[0].locations] == ["Paris", "Germany"]


def test_only_replaces_its_own_exports(processed: Path, tmp_path: Path) -> None:
    out = tmp_path / "export"
    out.mkdir()
    (out / "meta.json").write_text("{}")
    with pytest.raises(SystemExit, match="didn't write"):
        export.run(processed, out)
    (out / ".demo-export").write_text("demo")
    export.run(processed, out)
    assert (out / export.MARKER).exists() and not (out / ".demo-export").exists()
    export.run(processed, out, top=1)  # its own export: replaced without --force
    assert len(load(out, f"snapshots/{END}.json")["stories"]) == 1
    assert not out.with_name("export.tmp").exists() and not out.with_name("export.old").exists()


def test_stale_model_card_is_left_out(processed: Path, tmp_path: Path) -> None:
    add_forecasts(processed)
    metrics = json.loads((processed / "model_metrics.json").read_text())
    metrics["horizons"] = metrics["horizons"][:1]
    (processed / "model_metrics.json").write_text(json.dumps(metrics))
    out = export.run(processed, tmp_path / "export")
    meta = load(out, "meta.json")
    assert meta["horizons"] == [1, 3] and meta["model"] is None


def test_junk_stories_are_left_off_the_globe(processed: Path, tmp_path: Path) -> None:
    """JUNK is big enough to rank second (28 articles a week) but its articles share no name and
    no country, so it stays out. QUAKE names nobody either, but is all in Japan: kept."""
    out = export.run(processed, tmp_path / "export")
    assert JUNK not in snapshot(out, END) and QUAKE in snapshot(out, END)
    assert not (out / "stories" / f"{JUNK}.json").exists()
    kept = export.run(processed, tmp_path / "all", keep_all=True)
    assert list(snapshot(kept, END))[:2] == [STRIKE, JUNK]


def test_titles_are_english_and_point_in_time(processed: Path, tmp_path: Path) -> None:
    out = export.run(processed, tmp_path / "export")
    for day in DAYS:
        for story in snapshot(out, day).values():
            title = story["title"]
            if story["id"] == FRENCH:
                continue
            assert "headline" in title, title  # a real (synthetic) headline, not a label
            assert int(title.split()[-1].split("-")[0]) <= (day - D0).days, "a headline from later"
    detail = c.StoryDetail.model_validate(load(out, f"stories/{FRENCH}.json"))
    assert detail.title == "Emmanuel Macron · Paris"


def test_theme_categories_can_be_turned_off_by_rules(processed: Path, tmp_path: Path) -> None:
    """A category only wins with enough of the story's articles (THEME_SHARE_MIN) and well above
    its base rate (THEME_LIFT_MIN)."""
    out = export.run(processed, tmp_path / "export")
    strike = snapshot(out, END)[STRIKE]
    assert strike["category"] == "society"
    old = export.THEME_SHARE_MIN
    try:
        export.THEME_SHARE_MIN = 1.01  # nothing can reach it
        out2 = export.run(processed, tmp_path / "export2")
    finally:
        export.THEME_SHARE_MIN = old
    assert snapshot(out2, END)[STRIKE]["category"] == "economy"


def stub_classifier(answers: dict[str, tuple[str, float]], seen: dict | None = None):
    def classify(headlines: dict[str, list[str]]) -> dict[str, tuple[str, float]]:
        if seen is not None:
            seen.update(headlines)
        return {sid: answers[sid] for sid in headlines if sid in answers}

    return classify


def test_categories_from_headlines(processed: Path, tmp_path: Path) -> None:
    """A sure headline classifier beats the themes; an unsure one gives way to clear themes, and
    where there are none its guess stands; a story with no English headline keeps the tracker's."""
    seen: dict[str, list[str]] = {}
    answers = {
        STRIKE: ("sport", 0.9),  # themes say society: the headlines win
        QUAKE: ("health", 0.2),  # unsure, and no themes: the guess stands
        SUMMIT: ("conflict", 0.5),
        FRENCH: ("sport", 0.99),  # never asked: it has no English headline
    }
    out = export.run(processed, tmp_path / "export", classify=stub_classifier(answers, seen))
    last = snapshot(out, END)
    assert last[STRIKE]["category"] == "sport"
    assert last[QUAKE]["category"] == "health"
    assert last[SUMMIT]["category"] == "conflict"
    assert last[FRENCH]["category"] == "politics"
    assert FRENCH not in seen
    assert all(0 < len(titles) <= export.TITLES_PER_STORY for titles in seen.values())
    assert all("headline" in t for titles in seen.values() for t in titles)  # English ones only
    # one category per story, on every day it appears
    for day in DAYS:
        for sid, story in snapshot(out, day).items():
            assert story["category"] == last.get(sid, story)["category"], (sid, day)


def test_unsure_headlines_give_way_to_themes(processed: Path, tmp_path: Path) -> None:
    out = export.run(
        processed,
        tmp_path / "export",
        classify=stub_classifier({STRIKE: ("sport", export.TITLE_CONFIDENCE_MIN - 0.01)}),
    )
    assert snapshot(out, END)[STRIKE]["category"] == "society"  # the themes
