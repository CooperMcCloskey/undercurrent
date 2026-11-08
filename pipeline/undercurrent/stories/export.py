"""Tracker output + articles + forecasts -> data/export/: the JSON the API serves and the globe draws.

    uv run python -m undercurrent.stories.export                                 # every tracked day
    uv run python -m undercurrent.stories.export --start 2026-10-01 --end 2026-11-06 --top 750

Reads, under data/processed/ (docs/WORKPLAN.md section 3):

    story_assignments   article_id, story_id, day, similarity                    B (3.6)
    stories_daily       story_id, day, first_seen, label, category               B (3.5)
    articles            id, url, domain, published_at, title, locations, tone,   A (3.1)
                        and optionally persons, orgs, lang
    forecasts.parquet   + model_metrics.json, optional                           C (3.7)

Writes meta.json, snapshots/<day>.json (each day's --top biggest stories) and stories/<id>.json
(every story that is in any snapshot), in the contracts.py shapes and validated on the way out.
The new export is written next to the old one and swapped in at the end, so the API never
serves half of it. It only replaces an export this command or the demo wrote (--force otherwise).

- **Point in time.** A snapshot for day t only uses articles assigned on day t or earlier, so
  replaying the globe never shows the future. Story details are one file per story, written with
  everything up to --end.
- **article_count_7d** counts a story id's story_assignments rows over the 7 days ending as_of,
  without story_lineage remapping: exactly how forecast/panel.py counts, so a forecast and the
  later snapshot it is graded against measure the same thing. Outcomes are taken from those later
  snapshots (pipeline/tests/test_contracts.py checks they agree).
- **Footprint** (`stories/footprint.py`): each article spreads one unit of attention over the
  places it names, over the same 7 days. Items get the same places as `Item.locations`.
- **Titles are in English** where the story has any English headline: on each day's globe, the
  most central English-feed headline of that week (else of any earlier day; never a later one),
  and in the story panel the most central one overall. A story with no English headline gets
  its top GDELT names and main place ("Emmanuel Macron · Paris"), which GDELT gives in English.
  The tracker's own label is the headline the story started with, in any language.
- **Categories** (`recategorize`): one per story, for all its days. From the story's own English
  headlines (up to TITLES_PER_STORY of the most central), classified by `vectors/categories.py`,
  when the classifier is sure (TITLE_CONFIDENCE_MIN); else from GDELT's theme tags when they are
  clear (THEME_CATEGORIES); else the classifier's best guess; a story with no English headline
  keeps the tracker's category. `--no-title-categories` skips the classifier (it loads the
  embedding model). The tracker's own category (its centroid against hand-written prototypes) was
  far too often "other", and had no sport or culture before 2026-11-08.
- **Wikipedia topics** (`stories/wiki_topics.py`, run at the end of the command when
  `wiki_events.parquet` is in --processed; `--no-wiki-topics` skips it): a story whose headlines
  match one Wikipedia current-events topic closely is named after it ("2026 Kolkata Durga Puja
  crowd crush"); one under a topic many stories share ("Middle Eastern crisis") keeps its headline
  and gets "Part of: <topic>" as its summary.
- **Junk stories are left off the globe** (`--keep-all-stories` to keep them): a story is shown
  only if at least NAME_SHARE_MIN of its articles share a person or organisation, or at least
  COUNTRY_SHARE_MIN have the same first-named country. The tracker groups by embedding alone,
  and some groups are not news: generic headlines that all sit near the average vector, daily
  horoscopes, headlines that are mostly a date. They share no names and no country. The rule is
  measured over each story's articles up to --end. The tracker's output is untouched.
"""

import argparse
import datetime as dt
import json
import logging
import math
import shutil
import typing
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from pydantic import BaseModel

from undercurrent import config
from undercurrent import contracts as c
from undercurrent.stories.footprint import Place, article_places, footprint, top_locations
from undercurrent.vectors.store import parquet_source

log = logging.getLogger(__name__)

WINDOW = 7  # days in article_count_7d
MARKER = ".stories-export"
REPLACEABLE = (MARKER, ".demo-export")  # exports this may overwrite without --force
CATEGORIES = set(typing.get_args(c.Category))

# Junk-story rule (see the docstring). Measured on block 1's 25 biggest stories on 2026-04-29: the
# two junk ones had 4-5% of articles sharing their top name and 11-34% their top country; the
# weakest real story had 13% / 73%, all others 15%+ on names. GDELT extracts names from an English
# translation for the translated feed, so they are comparable across languages.
NAME_SHARE_MIN = 0.10
COUNTRY_SHARE_MIN = 0.50

# Category from GDELT's theme tags. GDELT tags each article from an English translation, so this
# works the same in every language, unlike the tracker's embedding category (which is matched to
# English example headlines and drops many non-English stories to "other"). Each article votes
# for every category its themes name (first matching prefix per theme); a story takes the
# category whose share of its articles is highest relative to how common that category is over
# all assigned articles (its lift), if at least THEME_SHARE_MIN of its articles name it.
# Otherwise it keeps the tracker's category.
THEME_CATEGORIES: dict[str, tuple[str, ...]] = {
    "conflict": ("ARMEDCONFLICT", "TERROR", "MILITARY", "WB_2432_FRAGILITY_CONFLICT_AND_VIOLENCE",
                 "REBELLION", "TAX_WEAPONS", "CEASEFIRE", "SEIGE", "BLOCKADE"),
    "disaster": ("NATURAL_DISASTER", "MANMADE_DISASTER", "ENV_CLIMATECHANGE", "CRISISLEX_C07"),
    "health": ("HEALTH_", "EPIDEMIC", "MEDICAL", "TAX_DISEASE", "WB_621_HEALTH", "DISEASE"),
    "economy": ("ECON_", "EPU_ECONOMY", "TAX_ECON_PRICE", "WB_1104_MACROECONOMIC",
                "WB_332_CAPITAL_MARKETS", "WB_698_TRADE"),
    "politics": ("ELECTION", "LEGISLATION", "IMPEACHMENT", "CORRUPTION", "SANCTION", "VETO",
                 "DEMOCRACY", "USPEC_POLITICS", "NEGOTIATIONS", "TAX_FNCACT_PRESIDENT",
                 "TAX_FNCACT_PRIME_MINISTER", "TAX_FNCACT_MINISTER", "GOV_"),
    "society": ("PROTEST", "STRIKE", "REFUGEES", "IMMIGRATION", "HUMAN_RIGHTS", "DISCRIMINATION",
                "CRIME", "ARREST", "KIDNAP", "TRIAL", "EDUCATION", "SOC_GENERALCRIME", "LGBT"),
    "technology": ("CYBER_ATTACK", "SCIENCE", "MEDIA_SOCIAL", "WB_133_INFORMATION_AND_COMM",
                   "SOC_INNOVATION", "TAX_FNCACT_ASTRONAUT"),
    "sport": ("SOC_SPORTS", "SPORTS", "TAX_FNCACT_COACH", "TAX_FNCACT_ATHLETE", "TAX_FNCACT_PLAYER",
              "TAX_FNCACT_FOOTBALLER"),
    "culture": ("TAX_FNCACT_ACTOR", "TAX_FNCACT_SINGER", "TAX_FNCACT_ARTIST", "TAX_FNCACT_MUSICIAN",
                "MOVIE", "MUSIC", "ARTS", "ENTERTAINMENT", "CELEBRITY"),
}  # fmt: skip
THEME_SHARE_MIN = 0.25
THEME_LIFT_MIN = 1.5

# Category from the story's own English headlines (vectors/categories.py, classify_titles): the
# most central ones, each classified, their probabilities averaged. On 246 hand-labelled headlines
# the classifier is right 80% of the time when its confidence is at least TITLE_CONFIDENCE_MIN
# and 47% of the time below it. It decides the category when it is sure; otherwise a clear theme
# category decides; otherwise its best guess stands (better than "other", which was right for 1 in
# 20 of the labelled stories); and a story with no English headline keeps the tracker's.
TITLE_CONFIDENCE_MIN = 0.35
TITLES_PER_STORY = 6
Classifier = typing.Callable[[dict[str, list[str]]], dict[str, tuple[str, float]]]


def _theme_case(var: str) -> str:
    arms = []
    for category, prefixes in THEME_CATEGORIES.items():
        test = " OR ".join(f"starts_with({var}, '{p}')" for p in prefixes)
        arms.append(f"WHEN {test} THEN '{category}'")
    return "CASE " + " ".join(arms) + " END"


# Columns read from articles, with the type to fill in when a column is missing.
ARTICLE_COLUMNS = {
    "title": None,  # required
    "url": None,
    "locations": None,
    "domain": "VARCHAR",
    "published_at": "TIMESTAMPTZ",
    "tone": "DOUBLE",
    "lang": "VARCHAR",
    "persons": "VARCHAR[]",
    "orgs": "VARCHAR[]",
    "themes": "VARCHAR[]",
}

# The weights footprint.add_article gives, summed per story, day and place.
PLACE_WEIGHTS_SQL = """
CREATE TEMP TABLE place_daily AS
WITH flat AS (
    SELECT story_id, d, article_id, l.name AS name, coalesce(l.type, 0) AS type,
           l.country AS country, l.lat AS lat, l.lon AS lon
    FROM (SELECT story_id, d, article_id, unnest(locations) AS l FROM joined WHERE d <= $end)
    WHERE l.lat IS NOT NULL AND l.lon IS NOT NULL
), marked AS (
    SELECT *, bool_or(type <> 1) OVER (PARTITION BY story_id, article_id, country) AS covered
    FROM flat
), kept AS (
    SELECT *, count(*) OVER (PARTITION BY story_id, article_id) AS n
    FROM marked WHERE NOT (type = 1 AND country IS NOT NULL AND covered)
)
SELECT story_id, d, name, type, country, lat, lon, sum(1.0 / n) AS w
FROM kept GROUP BY ALL ORDER BY d, story_id
"""

# One English-feed headline per story and day: the one closest to the story's centroid. "English"
# means the English feed (no `lang`) and a title written (90%+) in plain Latin letters.
ENGLISH_DAILY_SQL = r"""
CREATE TEMP TABLE english_daily AS
SELECT story_id, d, arg_max(title, similarity) AS title, max(similarity) AS sim
FROM joined
WHERE d <= $end AND lang IS NULL AND title IS NOT NULL
  AND length(regexp_replace(title, '[^\pL]', '', 'g')) > 0
  AND length(regexp_replace(title, '[^A-Za-z]', '', 'g'))
      >= 0.9 * length(regexp_replace(title, '[^\pL]', '', 'g'))
GROUP BY 1, 2
"""

# GDELT names per story and day, only for stories with no English headline at all (they get a
# names-and-place title). Built once, so each day's titles are a quick lookup.
NAMES_DAILY_SQL = """
CREATE TEMP TABLE names_daily AS
WITH s AS (
    SELECT DISTINCT story_id FROM joined WHERE d <= $end
    EXCEPT SELECT story_id FROM english_daily
), m AS (
    SELECT story_id, d, article_id, persons, orgs FROM joined
    WHERE d <= $end AND story_id IN (SELECT story_id FROM s)
)
SELECT story_id, d, name, count(DISTINCT article_id) AS k FROM (
    SELECT story_id, d, article_id, unnest(list_distinct(list_concat(
        coalesce(persons, CAST([] AS VARCHAR[])), coalesce(orgs, CAST([] AS VARCHAR[]))))) AS name
    FROM m
) WHERE name IS NOT NULL AND name <> '' GROUP BY 1, 2, 3
"""

# GDELT's translated feed gives ISO 639-2 codes; the web app expects two letters ("en", "fr").
LANG = {
    "afr": "af", "ara": "ar", "aze": "az", "bel": "be", "ben": "bn", "bos": "bs", "bul": "bg",
    "cat": "ca", "ces": "cs", "cym": "cy", "dan": "da", "deu": "de", "ell": "el", "eng": "en",
    "est": "et", "eus": "eu", "fas": "fa", "fin": "fi", "fra": "fr", "gle": "ga", "glg": "gl",
    "guj": "gu", "heb": "he", "hin": "hi", "hrv": "hr", "hun": "hu", "hye": "hy", "ind": "id",
    "isl": "is", "ita": "it", "jpn": "ja", "kan": "kn", "kat": "ka", "kaz": "kk", "khm": "km",
    "kor": "ko", "lav": "lv", "lit": "lt", "mal": "ml", "mar": "mr", "mkd": "mk", "mon": "mn",
    "msa": "ms", "mya": "my", "nep": "ne", "nld": "nl", "nor": "no", "pan": "pa", "pol": "pl",
    "por": "pt", "ron": "ro", "rus": "ru", "sin": "si", "slk": "sk", "slv": "sl", "som": "so",
    "spa": "es", "sqi": "sq", "srp": "sr", "swa": "sw", "swe": "sv", "tam": "ta", "tel": "te",
    "tgl": "tl", "tha": "th", "tur": "tr", "ukr": "uk", "urd": "ur", "uzb": "uz", "vie": "vi",
    "zho": "zh",
}  # fmt: skip
LOWER_WORDS = {"al", "and", "bin", "da", "de", "del", "der", "di", "du", "el", "la", "le", "of",
               "the", "van", "von"}  # fmt: skip


def language(code: str | None) -> str:
    """GDELT's source language as two letters; null (the English feed) is English."""
    return "en" if not code else LANG.get(code, code)


def display_name(name: str) -> str:
    """GDELT lower-cases names: 'jan de vries' -> 'Jan de Vries'."""
    words = name.split()
    return " ".join(
        w if i and w in LOWER_WORDS else w[:1].upper() + w[1:] for i, w in enumerate(words)
    )


def ordinal(day: dt.date) -> int:
    return day.toordinal()


def from_ordinal(n: int) -> dt.date:
    return dt.date.fromordinal(int(n))


def window(df: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    """Rows with lo <= d <= hi, from a frame sorted by `d` (a day as date.toordinal())."""
    days = df["d"].to_numpy()
    return df.iloc[np.searchsorted(days, lo, "left") : np.searchsorted(days, hi, "right")]


# ---- reading ---------------------------------------------------------------------------------


class Source:
    """The pipeline tables, joined in DuckDB. `joined` has one row per assigned article."""

    def __init__(self, processed: Path) -> None:
        tables = {
            name: parquet_source(processed, name)
            for name in ("story_assignments", "stories_daily", "articles")
        }
        missing = [name for name, src in tables.items() if src is None]
        if missing:
            raise SystemExit(
                f"Missing {', '.join(missing)} under {processed}. Run undercurrent.stories.track "
                "first (and the ingest before it)."
            )
        self.con = duckdb.connect()
        self.con.execute("SET TimeZone = 'UTC'")
        self.daily_src = tables["stories_daily"]
        have = {
            r[0]
            for r in self.con.execute(f"DESCRIBE SELECT * FROM {tables['articles']}").fetchall()
        }
        cols = []
        for col, fill in ARTICLE_COLUMNS.items():
            if col in have:
                cols.append(f"a.{col} AS {col}")
            elif fill is None:
                raise SystemExit(f"articles has no `{col}` column (docs/WORKPLAN.md 3.1)")
            else:
                cols.append(f"CAST(NULL AS {fill}) AS {col}")
        self.con.execute(
            f"""CREATE TEMP VIEW joined AS
                SELECT s.story_id AS story_id, s.article_id AS article_id,
                       CAST(s.similarity AS DOUBLE) AS similarity,
                       CAST(s.day AS DATE) AS day,
                       (CAST(s.day AS DATE) - DATE '0001-01-01') + 1 AS d,
                       {", ".join(cols)}
                FROM {tables["story_assignments"]} s JOIN {tables["articles"]} a
                  ON a.id = s.article_id"""
        )

    def tracked_days(self) -> list[dt.date]:
        rows = self.con.execute(
            f"SELECT DISTINCT CAST(day AS DATE) FROM {self.daily_src} ORDER BY 1"
        ).fetchall()
        return [r[0] for r in rows]

    def counts(self, end: dt.date) -> pd.DataFrame:
        """Articles (and their tone) per story and day: story_id, d, n, tone_sum, tone_n."""
        return self.con.execute(
            """SELECT story_id, d, count(*) AS n, coalesce(sum(tone), 0) AS tone_sum,
                      count(tone) AS tone_n
               FROM joined WHERE d <= $end GROUP BY ALL ORDER BY d, story_id""",
            {"end": ordinal(end)},
        ).df()

    def story_days(self, end: dt.date) -> pd.DataFrame:
        """stories_daily: story_id, d, first_seen, label, category, sorted by d."""
        df = self.con.execute(
            f"""SELECT story_id, (CAST(day AS DATE) - DATE '0001-01-01') + 1 AS d,
                       CAST(first_seen AS DATE) AS first_seen, label, category
                FROM {self.daily_src} WHERE CAST(day AS DATE) <= $end ORDER BY d, story_id""",
            {"end": end},
        ).df()
        df["first_seen"] = pd.to_datetime(df["first_seen"]).dt.date
        return df

    def build_places(self, end: dt.date) -> None:
        self.con.execute(PLACE_WEIGHTS_SQL, {"end": ordinal(end)})

    def build_english(self, end: dt.date) -> None:
        self.con.execute(ENGLISH_DAILY_SQL, {"end": ordinal(end)})
        self.con.execute(NAMES_DAILY_SQL, {"end": ordinal(end)})

    def names_as_of(self, story_ids: list[str], hi: int, limit: int = 2) -> dict[str, list[str]]:
        """Top GDELT names (named in 2+ articles) of each story up to day hi, for stories with no
        English headline at all (names_daily holds only those)."""
        if not story_ids:
            return {}
        self._pick(story_ids)
        rows = self.con.execute(
            """SELECT story_id, name FROM (
                   SELECT story_id, name, sum(k) AS n FROM names_daily
                   WHERE d <= $hi AND story_id IN (SELECT story_id FROM picked) GROUP BY 1, 2
               ) WHERE n >= 2
               QUALIFY row_number() OVER (PARTITION BY story_id ORDER BY n DESC, name) <= $limit
               ORDER BY story_id, n DESC, name""",
            {"hi": hi, "limit": limit},
        ).fetchall()
        out: dict[str, list[str]] = defaultdict(list)
        for sid, name in rows:
            out[sid].append(display_name(name))
        return out

    def english_titles(self, story_ids: list[str], lo: int, hi: int) -> dict[str, str]:
        """Each story's most central English headline over days lo..hi (ordinals)."""
        if not story_ids:
            return {}
        self._pick(story_ids)
        rows = self.con.execute(
            """SELECT story_id, arg_max(title, sim) FROM english_daily
               WHERE d BETWEEN $lo AND $hi AND story_id IN (SELECT story_id FROM picked)
               GROUP BY 1""",
            {"lo": lo, "hi": hi},
        ).fetchall()
        return {sid: title for sid, title in rows}

    def category_titles(self, story_ids: list[str], end: int, n: int) -> dict[str, list[str]]:
        """Up to `n` English headlines per story, the most central of its days up to `end`."""
        if not story_ids:
            return {}
        self._pick(story_ids)
        rows = self.con.execute(
            """SELECT story_id, title FROM english_daily
               WHERE d <= $end AND story_id IN (SELECT story_id FROM picked)
               QUALIFY row_number() OVER (PARTITION BY story_id ORDER BY sim DESC, d DESC) <= $n
               ORDER BY story_id, sim DESC, d DESC""",
            {"end": end, "n": n},
        ).fetchall()
        out: dict[str, list[str]] = defaultdict(list)
        for sid, title in rows:
            out[sid].append(title)
        return out

    def _pick(self, story_ids: list[str]) -> None:
        self.con.register("picked", pd.DataFrame({"story_id": pd.Series(story_ids, dtype=str)}))

    def place_weights(
        self, story_ids: list[str], lo: int | None, hi: int
    ) -> dict[str, dict[Place, float]]:
        """Each story's footprint weights over days lo..hi (lo None: from the start)."""
        self._pick(story_ids)
        rows = self.con.execute(
            """SELECT story_id, name, type, country, lat, lon, sum(w)
               FROM place_daily
               WHERE d BETWEEN $lo AND $hi AND story_id IN (SELECT story_id FROM picked)
               GROUP BY ALL""",
            {"lo": lo if lo is not None else 0, "hi": hi},
        ).fetchall()
        out: dict[str, dict[Place, float]] = defaultdict(dict)
        for sid, name, type_, country, lat, lon, w in rows:
            out[sid][Place(name, int(type_), country, lat, lon)] = float(w)
        return out

    def top_items(self, story_ids: list[str], end: dt.date, limit: int) -> pd.DataFrame:
        """The `limit` articles closest to each story's centroid."""
        self._pick(story_ids)
        return self.con.execute(
            """SELECT story_id, article_id, similarity, title, url, domain, published_at, lang,
                      locations, day
               FROM joined
               WHERE d <= $end AND story_id IN (SELECT story_id FROM picked)
               QUALIFY row_number() OVER (
                   PARTITION BY story_id ORDER BY similarity DESC, published_at, article_id
               ) <= $limit
               ORDER BY story_id, similarity DESC, published_at, article_id""",
            {"end": ordinal(end), "limit": limit},
        ).df()

    def theme_categories(self, story_ids: list[str], end: dt.date) -> pd.DataFrame:
        """Per story and category (THEME_CATEGORIES): the share of the story's articles up to
        `end` whose themes name it, and that share divided by the category's share of all
        assigned articles (its lift). Columns story_id, category, share, lift."""
        self._pick(story_ids)
        cats = (
            "list_distinct(list_filter(list_transform(coalesce(themes, CAST([] AS VARCHAR[])), "
            f"lambda x: {_theme_case('x')}), lambda y: y IS NOT NULL))"
        )
        return self.con.execute(
            f"""WITH a AS (
                    SELECT story_id, {cats} AS cats FROM joined WHERE d <= $end
                ), total AS (
                    SELECT count(*) AS n FROM a
                ), base AS (
                    SELECT c AS category, count(*) / (SELECT n FROM total) AS p
                    FROM (SELECT unnest(cats) AS c FROM a) GROUP BY 1
                ), mine AS (
                    SELECT story_id, cats FROM a WHERE story_id IN (SELECT story_id FROM picked)
                ), size AS (
                    SELECT story_id, count(*) AS n FROM mine GROUP BY 1
                ), votes AS (
                    SELECT story_id, c AS category, count(*) AS k
                    FROM (SELECT story_id, unnest(cats) AS c FROM mine) GROUP BY 1, 2
                )
                SELECT v.story_id, v.category, v.k / s.n AS share, (v.k / s.n) / b.p AS lift
                FROM votes v JOIN size s USING (story_id) JOIN base b USING (category)""",
            {"end": ordinal(end)},
        ).df()

    def story_quality(self, end: dt.date, min_articles: int) -> pd.DataFrame:
        """Per story with at least `min_articles` articles up to `end`: n, name_share (the share of
        its articles naming its most-named person or organisation) and country_share (the share
        whose first location is in its most common country). Indexed by story_id."""
        return (
            self.con.execute(
                """WITH m AS (
                       SELECT story_id, article_id, persons, orgs, locations FROM joined
                       WHERE d <= $end
                   ), n AS (
                       SELECT story_id, count(*) AS n FROM m GROUP BY 1 HAVING count(*) >= $min
                   ), names AS (
                       SELECT story_id, name, count(DISTINCT article_id) AS k FROM (
                           SELECT story_id, article_id, unnest(list_distinct(list_concat(
                               coalesce(persons, CAST([] AS VARCHAR[])),
                               coalesce(orgs, CAST([] AS VARCHAR[]))))) AS name
                           FROM m WHERE story_id IN (SELECT story_id FROM n)
                       ) WHERE name IS NOT NULL AND name <> '' GROUP BY 1, 2
                   ), top_name AS (
                       SELECT story_id, max(k) AS k FROM names GROUP BY 1
                   ), countries AS (
                       SELECT story_id, locations[1].country AS country, count(*) AS k FROM m
                       WHERE story_id IN (SELECT story_id FROM n) AND len(locations) > 0
                         AND locations[1].country IS NOT NULL
                       GROUP BY 1, 2
                   ), top_country AS (
                       SELECT story_id, max(k) AS k FROM countries GROUP BY 1
                   )
                   SELECT n.story_id, n.n,
                          coalesce(top_name.k, 0) / n.n AS name_share,
                          coalesce(top_country.k, 0) / n.n AS country_share
                   FROM n LEFT JOIN top_name USING (story_id) LEFT JOIN top_country USING (story_id)""",
                {"end": ordinal(end), "min": min_articles},
            )
            .df()
            .set_index("story_id")
        )

    def entities(self, story_ids: list[str], end: dt.date, limit: int) -> pd.DataFrame:
        """The people and organisations each story's articles name most (at least twice)."""
        self._pick(story_ids)
        return self.con.execute(
            """WITH m AS (
                   SELECT story_id, persons, orgs FROM joined
                   WHERE d <= $end AND story_id IN (SELECT story_id FROM picked)
               ), e AS (
                   SELECT story_id, 'person' AS kind, unnest(persons) AS name FROM m
                   UNION ALL
                   SELECT story_id, 'organization' AS kind, unnest(orgs) AS name FROM m
               ), n AS (
                   SELECT story_id, kind, name, count(*) AS n FROM e
                   WHERE name IS NOT NULL AND name <> '' GROUP BY ALL
               )
               SELECT story_id, kind, name, n FROM n WHERE n >= 2
               QUALIFY row_number() OVER (PARTITION BY story_id ORDER BY n DESC, name) <= $limit
               ORDER BY story_id, n DESC, name""",
            {"end": ordinal(end), "limit": limit},
        ).df()


def load_forecast_inputs(
    processed: Path,
) -> tuple[
    dict[tuple[str, dt.date], tuple[c.Forecast, c.Outcome | None]], list[int], c.ModelMetrics | None
]:
    """C's forecasts (via forecast.export, so the grouping lives in one place), horizons, metrics."""
    path = processed / "forecasts.parquet"
    if not path.exists():
        log.info("no %s: exporting without forecasts", path)
        return {}, [], None
    from undercurrent.forecast.export import load_forecasts, meta_horizons

    forecasts = {
        (sid, pd.Timestamp(day).date()): v for (sid, day), v in load_forecasts(path).items()
    }
    horizons = meta_horizons(path)
    metrics = None
    metrics_path = processed / "model_metrics.json"
    if metrics_path.exists():
        metrics = c.ModelMetrics.model_validate_json(metrics_path.read_text())
        if [h.days for h in metrics.horizons] != horizons:
            log.warning(
                "model_metrics.json horizons %s differ from forecasts.parquet %s: leaving the "
                "model card out of meta.json (re-run the forecast)",
                [h.days for h in metrics.horizons],
                horizons,
            )
            metrics = None
    return forecasts, horizons, metrics


# ---- building --------------------------------------------------------------------------------


def seven_day(counts: pd.DataFrame, as_of: dt.date) -> pd.DataFrame:
    """Per story active in the window: a7 (articles in the 7 days ending as_of), prev7 (the 7
    before), tone (mean over the 7 days)."""
    t = ordinal(as_of)
    win = window(counts, t - 2 * WINDOW + 1, t)
    recent = win[win["d"] > t - WINDOW]
    agg = recent.groupby("story_id").agg(
        a7=("n", "sum"), tone_sum=("tone_sum", "sum"), tone_n=("tone_n", "sum")
    )
    prev = win[win["d"] <= t - WINDOW].groupby("story_id")["n"].sum()
    agg["prev7"] = prev.reindex(agg.index, fill_value=0)
    return agg


def latest_rows(story_days: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    """Each story's last stories_daily row with lo <= d <= hi, indexed by story_id."""
    return window(story_days, lo, hi).drop_duplicates("story_id", keep="last").set_index("story_id")


def category_of(value: object) -> str:
    return value if isinstance(value, str) and value in CATEGORIES else "other"


def outcome_for(
    sid: str,
    as_of: dt.date,
    forecast: c.Forecast,
    a7_on: dict[dt.date, dict[str, int]],
    end: dt.date,
) -> c.Outcome | None:
    """The real article_count_7d on each forecast horizon that has happened by `end`."""
    known = [
        c.HorizonOutcome(days=h.days, articles_7d=a7_on[day].get(sid, 0))
        for h in forecast.horizons
        if (day := as_of + dt.timedelta(h.days)) <= end and day in a7_on
    ]
    return c.Outcome(horizons=known) if known else None


def fallback_title(names: list[str], location: c.Location | None, meta_row) -> str:
    """For a story with no English headline: its top names and main place, which GDELT gives in
    English ("Emmanuel Macron, Elysee Palace · Paris"). Without either, the tracker's label."""
    place = location.name.split(",")[0].strip() if location is not None else ""
    parts = [", ".join(names[:2]), place]
    title = " · ".join(p for p in parts if p)
    return title or str(meta_row["label"])


def story_item(row) -> c.Item:
    published = row.published_at
    if published is None or pd.isna(published):
        published = dt.datetime.combine(row.day, dt.time(12), tzinfo=dt.UTC)
    else:
        published = pd.Timestamp(published).tz_convert("UTC").to_pydatetime()
    return c.Item(
        id=row.article_id,
        kind="news",
        title=row.title,
        url=row.url,
        published_at=published,
        source=row.domain if isinstance(row.domain, str) else None,
        language=language(row.lang if isinstance(row.lang, str) else None),
        similarity=round(min(1.0, max(-1.0, float(row.similarity))), 3),
        locations=[p.location() for p in article_places(_as_list(row.locations))],
    )


def _as_list(value: object) -> list[dict]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    return list(value)


def timeline(story_counts: pd.DataFrame) -> list[c.TimelinePoint]:
    """Articles per day from the story's first article to its last, with the quiet days as 0."""
    by_day = dict(zip(story_counts["d"].astype(int), story_counts["n"].astype(int), strict=True))
    first, last = min(by_day), max(by_day)
    return [
        c.TimelinePoint(day=from_ordinal(d), articles=by_day.get(d, 0))
        for d in range(first, last + 1)
    ]


class Exporter:
    def __init__(
        self,
        source: Source,
        start: dt.date,
        end: dt.date,
        top: int,
        items: int,
        min_articles: int,
        forecasts: dict,
        horizons: list[int],
        metrics: c.ModelMetrics | None,
        keep_all: bool = False,
        classify: Classifier | None = None,
    ) -> None:
        self.classify = classify
        self.src, self.start, self.end = source, start, end
        self.top, self.n_items, self.min_articles = top, items, min_articles
        self.forecasts, self.horizons, self.metrics = forecasts, horizons, metrics
        self.counts = source.counts(end)
        self.story_days = source.story_days(end)
        source.build_places(end)
        source.build_english(end)
        tracked = {from_ordinal(d) for d in self.story_days["d"].unique()} | {
            from_ordinal(d) for d in self.counts["d"].unique()
        }
        self.days = sorted(d for d in tracked if start <= d <= end)
        self.stats = defaultdict(int)
        self.quality = None if keep_all else source.story_quality(end, min_articles)
        self.dropped: set[str] = set()
        self.names: dict[str, list[str]] = {}  # this day's GDELT names, for stories with no English

    def snapshots(self) -> dict[dt.date, c.Snapshot]:
        windows = {day: seven_day(self.counts, day) for day in self.days}
        a7_on = {day: agg["a7"].astype(int).to_dict() for day, agg in windows.items()}
        return {day: self.snapshot(day, windows[day], a7_on) for day in self.days}

    def snapshot(
        self, as_of: dt.date, agg: pd.DataFrame, a7_on: dict[dt.date, dict[str, int]]
    ) -> c.Snapshot:
        t = ordinal(as_of)
        meta = latest_rows(self.story_days, t - 2 * WINDOW + 1, t)
        ranked = (
            agg[agg["a7"] >= self.min_articles]
            .rename_axis("story_id")
            .reset_index()
            .sort_values(["a7", "story_id"], ascending=[False, True])
            .set_index("story_id")
        )
        stories: list[c.StorySummary] = []
        batch = max(self.top + self.top // 2, 50)
        for at in range(0, len(ranked), batch):
            chunk = ranked.iloc[at : at + batch]
            known = [s for s in chunk.index if s in meta.index]
            self.stats["skipped: no stories_daily row"] += len(chunk) - len(known)
            weights = self.src.place_weights(known, t - WINDOW + 1, t)
            titles = self.titles(known, t)
            for sid in known:
                if not self.looks_like_news(sid):
                    self.dropped.add(sid)
                    self.stats["story-days left out: no shared name or country"] += 1
                    continue
                made = footprint(weights.get(sid, {}))
                if made is None:
                    self.stats["skipped: no places"] += 1
                    continue
                title = titles.get(sid) or fallback_title(
                    self.names.get(sid, []), made[1], meta.loc[sid]
                )
                stories.append(
                    self.summary(sid, as_of, chunk.loc[sid], meta.loc[sid], made, a7_on, title)
                )
                if len(stories) == self.top:
                    return c.Snapshot(as_of=as_of, stories=stories)
        return c.Snapshot(as_of=as_of, stories=stories)

    def titles(self, story_ids: list[str], t: int) -> dict[str, str]:
        """English titles as of day t: this week's most central English headline, else the most
        central one of any earlier day. Stories with neither get names for fallback_title()."""
        week = self.src.english_titles(story_ids, t - WINDOW + 1, t)
        rest = [s for s in story_ids if s not in week]
        earlier = self.src.english_titles(rest, 0, t)
        none = [s for s in rest if s not in earlier]
        self.names = self.src.names_as_of(none, t)
        self.stats["titles: this week's English headline"] += len(week)
        self.stats["titles: an earlier English headline"] += len(earlier)
        self.stats["titles: no English headline (names and place)"] += len(rest) - len(earlier)
        return {**earlier, **week}

    def recategorize(self, snaps: dict[dt.date, c.Snapshot]) -> None:
        """Give each exported story one category for all its days: from its English headlines when
        the classifier is sure (TITLE_CONFIDENCE_MIN), else from GDELT's themes where they are
        clear (THEME_CATEGORIES), else the classifier's best guess; a story with no English
        headline keeps the tracker's category."""
        story_ids = sorted({s.id for snap in snaps.values() for s in snap.stories})
        if not story_ids:
            return
        votes = self.src.theme_categories(story_ids, self.end)
        votes = votes[(votes["share"] >= THEME_SHARE_MIN) & (votes["lift"] >= THEME_LIFT_MIN)]
        by_theme = (
            votes.sort_values(["story_id", "lift", "category"], ascending=[True, False, True])
            .drop_duplicates("story_id")
            .set_index("story_id")["category"]
            .to_dict()
        )
        by_title: dict[str, tuple[str, float]] = {}
        if self.classify is not None:
            headlines = self.src.category_titles(story_ids, ordinal(self.end), TITLES_PER_STORY)
            by_title = self.classify(headlines)
        best: dict[str, str] = {}
        for sid in story_ids:
            title, conf = by_title.get(sid, (None, 0.0))
            theme = by_theme.get(sid)
            if title and conf >= TITLE_CONFIDENCE_MIN:
                best[sid] = title
                how = "titles"
            elif theme:
                best[sid] = theme
                how = "themes"
            elif title:
                best[sid] = title
                how = "titles, unsure"
            else:
                continue
            self.stats[f"stories categorised by {how}"] += 1
            if title and theme:
                agree = "agree" if title == theme else "disagree"
                self.stats[f"stories where titles and themes {agree}"] += 1
        for snap in snaps.values():
            for s in snap.stories:
                new = best.get(s.id)
                if new and new != s.category:
                    self.stats[f"story-days recategorised: {s.category} -> {new}"] += 1
                    s.category = new
        self.stats["stories keeping the tracker's category"] = len(story_ids) - len(best)

    def looks_like_news(self, sid: str) -> bool:
        if self.quality is None:
            return True
        if sid not in self.quality.index:
            return False
        q = self.quality.loc[sid]
        return bool(q["name_share"] >= NAME_SHARE_MIN or q["country_share"] >= COUNTRY_SHARE_MIN)

    def summary(self, sid, as_of, agg_row, meta_row, made, a7_on, title) -> c.StorySummary:
        points, location = made
        a7, prev7 = int(agg_row["a7"]), int(agg_row["prev7"])
        forecast, outcome = None, None
        if (sid, as_of) in self.forecasts:
            forecast, given = self.forecasts[(sid, as_of)]
            outcome = outcome_for(sid, as_of, forecast, a7_on, self.end)
            self.stats["story-days with a forecast"] += 1
            # C's file has its own actuals; they should be the same numbers. If not, the
            # forecast was made from an older tracker run.
            theirs = {h.days: h.articles_7d for h in given.horizons} if given else {}
            if outcome and any(
                theirs.get(h.days, h.articles_7d) != h.articles_7d for h in outcome.horizons
            ):
                self.stats["forecast actuals that differ from the tracker (stale forecast?)"] += 1
        return c.StorySummary(
            id=sid,
            title=title,
            location=location,
            category=category_of(meta_row["category"]),
            footprint=points,
            article_count_7d=a7,
            post_count_7d=0,  # posts are attached by stories/attach.py (not built yet)
            growth_7d=round(math.log2((a7 + 1) / (prev7 + 1)), 3),
            tone=round(agg_row["tone_sum"] / agg_row["tone_n"], 2) if agg_row["tone_n"] else None,
            first_seen=meta_row["first_seen"],
            forecast=forecast,
            outcome=outcome,
        )

    def details(self, story_ids: list[str]) -> list[c.StoryDetail]:
        end = ordinal(self.end)
        meta = latest_rows(self.story_days, 0, end)
        weights = self.src.place_weights(story_ids, None, end)
        items = self.src.top_items(story_ids, self.end, self.n_items)
        items["day"] = pd.to_datetime(items["day"]).dt.date
        entities = self.src.entities(story_ids, self.end, 12)
        english = self.src.english_titles(story_ids, 0, end)
        counts = self.counts[self.counts["story_id"].isin(story_ids)]
        items_by = {sid: g for sid, g in items.groupby("story_id")}
        entities_by = {sid: g for sid, g in entities.groupby("story_id")}
        counts_by = {sid: g for sid, g in counts.groupby("story_id")}
        out = []
        for sid in story_ids:
            row = meta.loc[sid]
            its = items_by.get(sid)
            ents = entities_by.get(sid)
            places = top_locations(weights.get(sid, {}))
            names = [] if ents is None else [display_name(n) for n in ents["name"][:2]]
            title = english.get(sid) or fallback_title(names, places[0] if places else None, row)
            out.append(
                c.StoryDetail(
                    id=sid,
                    title=title,
                    summary=None,
                    first_seen=row["first_seen"],
                    locations=places,
                    entities=[]
                    if ents is None
                    else [
                        c.Entity(name=display_name(e.name), kind=e.kind) for e in ents.itertuples()
                    ],
                    timeline=timeline(counts_by[sid]),
                    items=[] if its is None else [story_item(r) for r in its.itertuples()],
                )
            )
        return out

    def meta(self) -> c.Meta:
        return c.Meta(
            dates=self.days, latest=self.days[-1], horizons=self.horizons, model=self.metrics
        )


# ---- writing ---------------------------------------------------------------------------------


def check_target(out: Path, force: bool) -> None:
    if not out.exists() or not any(out.iterdir()) or force:
        return
    if not any((out / m).exists() for m in REPLACEABLE):
        raise SystemExit(
            f"{out} holds an export this command didn't write (no {' or '.join(REPLACEABLE)}). "
            "Use --out somewhere else, or --force to replace it."
        )


def write_json(root: Path, rel: str, model: BaseModel) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = model.model_dump(mode="json")
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")


def swap_in(tmp: Path, out: Path) -> None:
    """Replace `out` with `tmp` in two renames, so readers see the old export or the new one."""
    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        out.rename(old)
    tmp.rename(out)
    shutil.rmtree(old, ignore_errors=True)


def run(
    processed: Path = config.PROCESSED,
    out: Path = config.EXPORT,
    start: dt.date | None = None,
    end: dt.date | None = None,
    top: int = 750,
    items: int = 25,
    min_articles: int = 3,
    force: bool = False,
    keep_all: bool = False,
    classify: Classifier | None = None,
) -> Path:
    check_target(out, force)
    source = Source(processed)
    tracked = source.tracked_days()
    if not tracked:
        raise SystemExit(f"stories_daily under {processed} is empty: nothing to export")
    start, end = max(start or tracked[0], tracked[0]), min(end or tracked[-1], tracked[-1])
    if start > end:
        raise SystemExit(
            f"No tracked days between {start} and {end} (tracked: {tracked[0]}..{tracked[-1]})"
        )
    forecasts, horizons, metrics = load_forecast_inputs(processed)
    ex = Exporter(
        source,
        start,
        end,
        top,
        items,
        min_articles,
        forecasts,
        horizons,
        metrics,
        keep_all,
        classify,
    )

    tmp = out.with_name(out.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    snaps = ex.snapshots()
    ex.recategorize(snaps)
    story_ids: list[str] = []
    seen: set[str] = set()
    for day, snap in snaps.items():
        write_json(tmp, f"snapshots/{day.isoformat()}.json", snap)
        for s in snap.stories:
            if s.id not in seen:
                seen.add(s.id)
                story_ids.append(s.id)
    for detail in ex.details(story_ids):
        write_json(tmp, f"stories/{detail.id}.json", detail)
    write_json(tmp, "meta.json", ex.meta())
    (tmp / MARKER).write_text("written by: uv run python -m undercurrent.stories.export\n")
    swap_in(tmp, out)

    sizes = [len(s.stories) for s in snaps.values()]
    print(
        f"Exported {len(snaps)} days ({start}..{end}) to {out}: {len(story_ids)} stories, "
        f"{min(sizes)}-{max(sizes)} a day; horizons {horizons or 'none'}"
    )
    for what, n in sorted(ex.stats.items()):
        print(f"  {what}: {n}")
    if ex.dropped:
        labels = latest_rows(ex.story_days, 0, ordinal(end))["label"]
        biggest = sorted(ex.dropped, key=lambda s: -int(ex.quality.at[s, "n"]))[:5]
        print(f"  left off the globe as junk: {len(ex.dropped)} stories, biggest:")
        for sid in biggest:
            q = ex.quality.loc[sid]
            print(
                f"    {int(q['n']):>7,} articles, names {q['name_share']:.2f}, country "
                f"{q['country_share']:.2f}: {str(labels.get(sid, sid))[:80]}"
            )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--start", type=dt.date.fromisoformat, help="first day (default: first tracked)"
    )
    parser.add_argument(
        "--end", type=dt.date.fromisoformat, help="last day (default: last tracked)"
    )
    parser.add_argument("--top", type=int, default=750, help="stories per snapshot, biggest first")
    parser.add_argument("--items", type=int, default=25, help="articles per story detail")
    parser.add_argument(
        "--min-articles", type=int, default=3, help="smallest article_count_7d shown"
    )
    parser.add_argument("--processed", type=Path, default=config.PROCESSED)
    parser.add_argument("--out", type=Path, default=config.EXPORT)
    parser.add_argument("--force", action="store_true", help="replace an export this didn't write")
    parser.add_argument(
        "--keep-all-stories",
        action="store_true",
        help="don't leave out stories whose articles share no name or country",
    )
    parser.add_argument(
        "--no-title-categories",
        action="store_true",
        help="don't classify stories from their headlines (loads the embedding model): use GDELT "
        "themes and the tracker's category only",
    )
    parser.add_argument(
        "--no-wiki-topics",
        action="store_true",
        help="don't name stories after Wikipedia topics (needs wiki_events.parquet in --processed)",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    classify = None
    if not args.no_title_categories:
        from undercurrent.vectors.categories import classify_titles

        classify = classify_titles
    out = run(
        args.processed,
        args.out,
        args.start,
        args.end,
        args.top,
        args.items,
        args.min_articles,
        args.force,
        args.keep_all_stories,
        classify,
    )

    wiki = args.processed / "wiki_events.parquet"
    if not args.no_wiki_topics and wiki.exists():
        from undercurrent.stories.wiki_topics import name_stories

        named = name_stories(out, wiki)
        specific = sum(n.specific for n in named.values())
        print(
            f"  Wikipedia topics: {specific} stories named after one, "
            f"{len(named) - specific} filed under an umbrella topic"
        )
    elif not args.no_wiki_topics:
        print(f"  Wikipedia topics: skipped, no {wiki}")


if __name__ == "__main__":
    main()
