"""Each story's Wikipedia entities -> data/processed/story_wiki_entities.parquet, for the forecast.

    uv run python -m undercurrent.stories.wiki_entities                       # data/processed
    uv run python -m undercurrent.stories.wiki_entities --processed data/merged

Reads, under --processed (one file `name.parquet` or a folder `name/` of partitions):

    story_assignments       article_id, story_id, day          B (stories/track.py)
    articles                id, persons, orgs                  A (docs/WORKPLAN.md 3.1)
    wiki_selection.parquet  name, first_week, title_en, qid    A (ingest/wiki_bulk.py; in the bucket
                                                                 at gs://undercurrent-data-cooper/wiki/)

Writes story_wiki_entities.parquet, at most --top rows per story, one per (story_id, qid), for
the stories that ever get a forecast row (7-day articles >= --min-articles-7d on some day):

    story_id   str    the tracker's id, without story_lineage (forecast/panel.py counts the same way)
    qid        str    Wikidata id; forecast/panel.py joins it to A's wiki_entity_daily.parquet
    title_en   str    English Wikipedia title
    name       str    the GDELT name (normalised) that named it most often in the story
    articles   int    the story's articles naming it, up to chosen_on
    rank       int    1 = named in the most articles
    chosen_on  date   the last day of articles the choice looked at
    full_from  date   from this day on, the entity's 8-language views and its edits may be used;
                      before it (or if empty), English views only. See "Languages" below.

`story_id` and `qid` are the hand-off (forecast/README.md); forecast/panel.py also reads
`full_from`. The rest is for people.

**Point in time.** forecast/panel.py joins this table without a date, so every story's entities
must already be known on the first day the story can get a forecast. They are chosen from the
story's articles up to `chosen_on`, the earlier of:

  - the last of its first --days days (counted from its first assignment, so ~3 days, PLAN.md), and
  - the first day its articles so far reach --min-articles-7d (the forecast's MIN_ARTICLES_7D).

A forecast row on day t needs a7(t) >= MIN_ARTICLES_7D (forecast/dataset.py), and a story's
articles so far are never fewer than its last 7 days, so every forecast row is on or after
chosen_on. Run the forecast with the same --min-articles as this, or that stops being true.

An entity also counts only if A had picked it by then: its `first_week` (the Monday of the first
week it made a weekly top 50, from that whole week's mentions) must end on or before chosen_on.
Otherwise "has Wikipedia data" would leak "became big later".

**Left out** (`excluded()`): countries, regions, capitals and big dateline cities, seas and
oceans, seats of government (White House), news agencies, newspapers, broadcasters and platforms
(they are named because they are the source, not the subject), and generic or wrong matches of
the name resolver (`Court`, `Secondary_school`, `Supreme_court`, ...). They are read so much
anyway that summed over a story's entities they drown out the story's own signal.

**Languages.** A's run fetches all 8 languages and English edits for an entity that was ever a
weekly top 15 (tier 1), over its whole history, and English views only for the rest. "Ever"
includes weeks after any given day, so summing 8 languages from the start would show an
entity's later prominence early. `full_from` is the end of the first week any of the entity's
names was tier 1 (`tier1_week` in wiki_selection), when that was known; forecast/panel.py uses
English views before it.
"""

import argparse
import datetime as dt
import logging
import re
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.forecast.settings import MIN_ARTICLES_7D
from undercurrent.ingest.wiki_resolve import normalise_name
from undercurrent.vectors.store import parquet_source

log = logging.getLogger(__name__)

OUT_NAME = "story_wiki_entities.parquet"
DAYS = 3  # PLAN.md, C's needs: "chosen from information available within the story's first ~3 days"
TOP = 5  # entities kept per story (ingest/WIKIPEDIA.md, "How the other workstreams should use it")
MIN_MENTIONS = 2  # a name must be in at least this many of the story's articles (as export.py)
COLUMNS = ["story_id", "qid", "title_en", "name", "articles", "rank", "chosen_on", "full_from"]

# ---- what to leave out ------------------------------------------------------------------------

# Hand-picked from wiki_selection.parquet as of 2026-11-08 (2,066 names), by English title.
PLACES = {
    "United_States", "Russia", "European_Union", "Dominican_Republic", "Confederate_States_of_America",
    "Latin_America", "Asia-Pacific", "Europe", "Asia", "Africa", "Middle_East",
    "Los_Angeles", "Las_Vegas", "Abu_Dhabi", "Madison,_Wisconsin", "Valencian_Community",
    "Aigosthena", "Pacific_Ocean", "Atlantic_Ocean", "Indian_Ocean", "Baltic_Sea",
    "White_House", "Oval_Office", "Kremlin", "Washington,_D.C.", "London", "Moscow", "Beijing",
    "Paris", "New_York_City", "Kyiv", "Tehran", "Jerusalem", "New_Delhi", "Tokyo", "Berlin",
}  # fmt: skip
SOURCES = {
    "Reuters", "Associated_Press", "Agence_France-Presse", "Xinhua_News_Agency",
    "Australian_Associated_Press", "İhlas_News_Agency", "Anadolu_Agency", "Demirören_News_Agency",
    "CNN", "ESPN", "MS_NOW", "Al_Jazeera_Media_Network", "ABS-CBN_News_and_Current_Affairs",
    "The_New_York_Times", "The_Washington_Post", "New_York_Post", "Los_Angeles_Times",
    "National_Enquirer", "El_País", "BBC", "BBC_News", "Fox_News", "Bloomberg_News",
    "Instagram", "Facebook", "X_(social_network)", "Twitter", "YouTube", "Google", "LinkedIn",
    "Yahoo", "VK_(service)", "TikTok", "WhatsApp", "Telegram_(software)", "Nasdaq",
    "Piper_Sandler_Companies", "Raymond_James_Financial", "Stifel", "Robert_W._Baird",
}  # fmt: skip
WRONG = {
    "Police", "Court", "Office", "Congress", "Parliament", "Committee", "Mosque", "Medication",
    "Zenith", "A-center", "Anthony", "Aaron", "Anna-Lena", "Lisa_Marie", "José_Antonio",
    "Giovanni_Battista", "Rama", "John_Doe", "Saint_Faith", "Europe_(band)", "Europe-Asia_Studies",
    "Stevenson–Wydler_Technology_Innovation_Act_of_1980", "C._Joseph_Vijay_ministry",
    "European_Public_Prosecutor's_Office", "Israeli_Army_Radio", "International_Men's_Day",
    "Reich_Ministry_of_Public_Enlightenment_and_Propaganda", "Brazilian_Intelligence_Agency",
    "National_Disaster_Management_Authority_(India)", "Discovery_Land_Company", "Forum_Peak",
    "Communications_and_Utilities_Regulatory_Authority", "Depp_v_News_Group_Newspapers_Ltd",
    "Hyundai_Santa_Fe", "The_Book_of_Healing", "Stanislav_Zakharov", "Mario_Machado",
    "Aleksandra_Goryachkina", "Tim_Bogert", "Norman_Daly", 'Jimmy_"Popeye"_Doyle', "Ji_Jin-hee",
    "Lee_Jong-suk", "Marie-Eugénie_de_Jésus", "Karim_Khan_Zand", "Progressive_Party_(China)",
    "Educational_policies_and_initiatives_of_the_European_Union", "National_Assembly",
}  # fmt: skip
CURATED = PLACES | SOURCES | WRONG
# Event articles that the generic-title rule below would otherwise catch.
KEEP = {"List_of_Gaza_war_hostages"}

# Words that stay lower case inside a proper name ("Ministry_of_Defence", "Bank_of_England").
_CONNECTORS = {"of", "the", "and", "for", "in", "on", "at", "to", "de", "del", "la", "le", "al",
               "el", "von", "van", "da", "di", "du", "y", "e", "a"}  # fmt: skip
# A title ending in one of these is an event ("Porepunkah_police_shootings"), not a concept.
_EVENT_WORDS = {"shooting", "shootings", "stabbing", "stabbings", "massacre", "murder", "murders",
                "killing", "killings", "attack", "attacks", "bombing", "bombings", "explosion",
                "crash", "fire", "fires", "flood", "floods", "earthquake", "protests", "riots",
                "prosecution", "trial", "case", "scandal", "family", "election", "referendum",
                "disappearance", "kidnapping", "hostages", "siege", "strike", "strikes"}  # fmt: skip


def generic(title: str) -> bool:
    """A generic concept page rather than a named thing: 'List_of_...', or a title of several
    words whose words after the first are all lower case ('Supreme_court', 'Central_bank',
    'Ministry_of_justice'). Named things capitalise ('Supreme_Court_of_the_United_States'), and
    events keep their last word ('Murdaugh_family', 'Nuseirat_rescue_and_massacre')."""
    if title in KEEP:
        return False
    if title.startswith("List_of_"):
        return True
    words = [w for w in re.split(r"[_\s]+", re.sub(r"\([^)]*\)", "", title)) if w]
    rest = [w for w in words[1:] if w.lower() not in _CONNECTORS]
    if not rest or words[-1].lower() in _EVENT_WORDS:
        return False
    return all(w == w.lower() for w in rest)


def excluded(title: str | None) -> bool:
    """Titles that are never a story's own entity (see the module docstring)."""
    return not title or title in CURATED or generic(title)


# ---- the choice ---------------------------------------------------------------------------------


def load_selection(path: Path) -> pd.DataFrame:
    """wiki_selection.parquet -> the names worth matching (see load_selection_frame)."""
    return load_selection_frame(pd.read_parquet(path))


def load_selection_frame(sel: pd.DataFrame) -> pd.DataFrame:
    """name, first_week, tier1_week, title_en, qid: resolved, not excluded, one row per name.
    A selection from before `tier1_week` existed gets None there (English views only)."""
    if "tier1_week" not in sel:
        sel = sel.assign(tier1_week=None)
    sel = sel[["name", "first_week", "tier1_week", "title_en", "qid"]].dropna(
        subset=["qid", "title_en", "first_week"]
    )
    sel = sel[~sel["title_en"].map(excluded)].copy()
    sel["first_week"] = pd.to_datetime(sel["first_week"]).dt.date
    sel["tier1_week"] = [None if pd.isna(w) else pd.Timestamp(w).date() for w in sel["tier1_week"]]
    if not sel["name"].is_unique:
        raise ValueError("wiki_selection: `name` is not unique")
    return sel.reset_index(drop=True)


def choice_days(daily: pd.DataFrame, days: int, min_articles: int) -> pd.DataFrame:
    """Per story: (story_id, start, chosen_on) from its articles per day in its first `days` days
    (story_id, day, n). chosen_on = the first day its articles so far reach min_articles, or
    start + days - 1 if they don't by then."""
    df = daily.assign(d=pd.to_datetime(daily["day"]).dt.date.map(dt.date.toordinal))
    df = df.sort_values(["story_id", "d"])
    df["so_far"] = df.groupby("story_id")["n"].cumsum()
    start = df.groupby("story_id")["d"].min()
    reached = df[df["so_far"] >= min_articles].groupby("story_id")["d"].min()
    reached = reached.reindex(start.index).to_numpy(dtype="float64")
    cap = (start + days - 1).to_numpy(dtype="float64")
    chosen = np.where(np.isnan(reached), cap, np.minimum(reached, cap)).astype("int64")
    return pd.DataFrame(
        {
            "story_id": start.index.to_numpy(),
            "start": [dt.date.fromordinal(int(d)) for d in start.to_numpy()],
            "chosen_on": [dt.date.fromordinal(int(d)) for d in chosen],
        }
    )


def build(
    processed: Path,
    selection: pd.DataFrame,
    days: int = DAYS,
    top: int = TOP,
    min_mentions: int = MIN_MENTIONS,
    min_articles: int = MIN_ARTICLES_7D,
) -> pd.DataFrame:
    """The story -> entity table (COLUMNS), from the tracker's assignments and A's articles."""
    if not 1 <= days <= 7:
        raise ValueError("days must be 1..7: the 'articles so far' rule assumes a 7-day window")
    assign = parquet_source(processed, "story_assignments")
    articles = parquet_source(processed, "articles")
    if assign is None or articles is None:
        raise SystemExit(
            f"Need story_assignments and articles under {processed} (B's tracker and A write them)."
        )
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    have = {r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {articles}").fetchall()}
    missing = {"id", "persons", "orgs"} - have
    if missing:
        raise SystemExit(f"articles has no {sorted(missing)} (docs/WORKPLAN.md 3.1)")

    # Only stories that ever get a forecast row (7-day articles >= min_articles on some day):
    # forecast/panel.py joins every story here to every day of its entities, so the others would
    # only cost memory. Counted from the assignments alone, which never count fewer than the
    # panel's join with articles, so no story the forecast uses is dropped. Leaving the others
    # out leaks nothing: a story only has forecast rows on days it is already over the line.
    # Then the articles in each eligible story's first `days` days, joined to A's articles
    # exactly as forecast/panel.py joins them, so the per-day counts are the forecast's counts.
    con.execute(
        f"""CREATE TEMP TABLE win AS
            WITH s AS (SELECT story_id, article_id, CAST(day AS DATE) AS day FROM {assign}),
                 per_day AS (SELECT story_id, day, count(*) AS n FROM s GROUP BY 1, 2),
                 eligible AS (
                     SELECT DISTINCT story_id FROM (
                         SELECT story_id, sum(n) OVER (
                             PARTITION BY story_id ORDER BY day
                             RANGE BETWEEN INTERVAL 6 DAYS PRECEDING AND CURRENT ROW) AS a7
                         FROM per_day)
                     WHERE a7 >= $min_articles),
                 f AS (SELECT story_id, min(day) AS start FROM per_day
                       WHERE story_id IN (SELECT story_id FROM eligible) GROUP BY 1)
            SELECT s.story_id, s.day, s.article_id,
                   list_distinct(list_concat(coalesce(a.persons, CAST([] AS VARCHAR[])),
                                             coalesce(a.orgs, CAST([] AS VARCHAR[])))) AS names
            FROM s JOIN f ON f.story_id = s.story_id
            JOIN (SELECT id, persons, orgs FROM {articles}) a ON a.id = s.article_id
            WHERE s.day < f.start + CAST($days AS INTEGER)""",
        {"days": days, "min_articles": min_articles},
    )
    daily = con.execute("SELECT story_id, day, count(*) AS n FROM win GROUP BY 1, 2").df()
    if daily.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})
    chosen = choice_days(daily, days, min_articles)
    con.register("chosen", chosen[["story_id", "chosen_on"]])

    # GDELT names -> the selection's names, normalised in Python exactly as wiki_names counts them.
    raw = con.execute(
        """SELECT DISTINCT raw FROM (SELECT unnest(names) AS raw FROM win)
           WHERE raw IS NOT NULL AND raw <> ''"""
    ).df()["raw"]
    by_name = selection.set_index("name")
    norm = raw.map(normalise_name)
    keep = norm.isin(by_name.index)
    rawmap = pd.DataFrame(
        {
            "raw": raw[keep].to_numpy(),
            "name": norm[keep].to_numpy(),
            "qid": by_name.loc[norm[keep], "qid"].to_numpy(),
            "first_week": by_name.loc[norm[keep], "first_week"].to_numpy(),
        }
    )
    if rawmap.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})
    con.register("rawmap", rawmap)

    # A story's articles up to its chosen_on, naming entities A had picked by then.
    con.execute(
        """CREATE TEMP TABLE named AS
           SELECT w.story_id, w.article_id, m.qid, m.name
           FROM (SELECT story_id, day, article_id, unnest(names) AS raw FROM win) w
           JOIN chosen c ON c.story_id = w.story_id AND w.day <= c.chosen_on
           JOIN rawmap m ON m.raw = w.raw
           WHERE CAST(m.first_week AS DATE) + 6 <= c.chosen_on"""
    )
    per_qid = con.execute(
        """SELECT story_id, qid, count(DISTINCT article_id) AS articles
           FROM named GROUP BY 1, 2 HAVING count(DISTINCT article_id) >= $min""",
        {"min": min_mentions},
    ).df()
    per_name = con.execute(
        "SELECT story_id, qid, name, count(DISTINCT article_id) AS k FROM named GROUP BY 1, 2, 3"
    ).df()
    if per_qid.empty:
        return pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})

    per_qid = per_qid.sort_values(["story_id", "articles", "qid"], ascending=[True, False, True])
    per_qid["rank"] = per_qid.groupby("story_id").cumcount() + 1
    per_qid = per_qid[per_qid["rank"] <= top]
    names = per_name.sort_values(
        ["story_id", "qid", "k", "name"], ascending=[True, True, False, True]
    ).drop_duplicates(["story_id", "qid"])[["story_id", "qid", "name"]]
    titles = selection.drop_duplicates("qid").set_index("qid")["title_en"]
    # Any name of the entity going tier 1 makes A fetch its 8 languages: the earliest such week.
    t1 = selection.dropna(subset=["tier1_week"]).groupby("qid")["tier1_week"].min()
    full_from = (t1 + dt.timedelta(days=6)) if len(t1) else t1
    out = per_qid.merge(names, on=["story_id", "qid"]).merge(chosen, on="story_id")
    out["title_en"] = out["qid"].map(titles)
    out["full_from"] = out["qid"].map(full_from).astype("object")
    out["full_from"] = [None if pd.isna(d) else d for d in out["full_from"]]
    out = out.astype({"articles": "int32", "rank": "int8"})
    return out[COLUMNS].sort_values(["story_id", "rank"]).reset_index(drop=True)


SCHEMA = pa.schema(
    [("story_id", pa.string()), ("qid", pa.string()), ("title_en", pa.string()),
     ("name", pa.string()), ("articles", pa.int32()), ("rank", pa.int8()),
     ("chosen_on", pa.date32()), ("full_from", pa.date32())]
)  # fmt: skip


def write(df: pd.DataFrame, path: Path) -> None:
    """Typed, so an empty table or one with no `full_from` still has date columns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    table = pa.Table.from_pandas(df[COLUMNS], schema=SCHEMA, preserve_index=False)
    pq.write_table(table, tmp)
    tmp.replace(path)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--processed", type=Path, default=config.PROCESSED)
    p.add_argument(
        "--selection", type=Path, help="default: <processed>/wiki_selection.parquet (A's)"
    )
    p.add_argument("--out", type=Path, help=f"default: <processed>/{OUT_NAME}")
    p.add_argument(
        "--days", type=int, default=DAYS, help=f"the story's first days to read (default {DAYS})"
    )
    p.add_argument("--top", type=int, default=TOP, help=f"entities per story (default {TOP})")
    p.add_argument(
        "--min-mentions", type=int, default=MIN_MENTIONS,
        help=f"articles that must name an entity (default {MIN_MENTIONS})",
    )  # fmt: skip
    p.add_argument(
        "--min-articles-7d", type=int, default=MIN_ARTICLES_7D,
        help=f"the forecast's --min-articles (default {MIN_ARTICLES_7D}); keep them equal",
    )  # fmt: skip
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    sel_path = args.selection or args.processed / "wiki_selection.parquet"
    if not sel_path.exists():
        raise SystemExit(
            f"No {sel_path}. Get it from gs://undercurrent-data-cooper/wiki/ "
            "(docs/DATA_COVERAGE.md, 'Wikipedia files for B')."
        )
    selection = load_selection(sel_path)
    out = build(
        args.processed, selection, args.days, args.top, args.min_mentions, args.min_articles_7d
    )
    path = args.out or args.processed / OUT_NAME
    write(out, path)
    n = out["story_id"].nunique()
    log.info(
        f"{path}: {len(out):,} rows, {n:,} stories with entities, {out['qid'].nunique():,} "
        f"entities ({len(selection):,} names matchable, {args.days} days, top {args.top})"
    )
    if n:
        top = out.groupby("title_en")["story_id"].nunique().sort_values(ascending=False).head(12)
        log.info("most common: " + ", ".join(f"{t} ({k})" for t, k in top.items()))


if __name__ == "__main__":
    main()
