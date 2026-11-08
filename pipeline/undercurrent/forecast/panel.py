"""Build the story-day panel from the other workstreams' hand-off files, with DuckDB.

DuckDB streams the join, so a year of articles doesn't have to fit in memory. Reads (all under
data/processed/, either one file `name.parquet` or a folder `name/` of partitions):

    story_assignments  article_id, story_id, day              B  (docs/WORKPLAN.md 3.6)
    articles           id, domain, tone, locations[].country  A  (3.1)
    stories_daily      story_id, day, category, first_seen    B  (3.5)   optional
    daily_totals       day, total_articles                    A         optional
    story_wiki_entities  story_id, qid (+ full_from)          B         optional (see README)
    wiki_entity_daily  qid, day, views, edits, views_en       A  (ingest/WIKIPEDIA.md)  optional

If one of those changes shape, change it here and in schema.py. Missing optional files just mean
those features are NaN.
"""

from pathlib import Path

import duckdb
import pandas as pd

from undercurrent.forecast import settings
from undercurrent.forecast.schema import require_columns

# The Wikipedia days joined around each story (see wiki_sql): enough for every feature's lookback
# (the neural model's SEQ_DAYS, lagged views, and the 7 + 7 days of the growth features).
WIKI_DAYS_BEFORE = (
    settings.SEQ_DAYS + 14 + max(settings.WIKI_VIEWS_LAG_DAYS, settings.WIKI_EDITS_LAG_DAYS)
)
WIKI_DAYS_AFTER = 7


def source(processed: Path, name: str) -> str | None:
    """A read_parquet(...) expression for a table that is one file or a folder of partitions."""
    single, folder = processed / f"{name}.parquet", processed / name
    if single.exists():
        return f"read_parquet('{str(single).replace(chr(39), chr(39) * 2)}')"
    if folder.is_dir():
        path = str(folder / "**" / "*.parquet").replace("'", "''")
        return f"read_parquet('{path}', hive_partitioning = true, union_by_name = true)"
    return None


def timestamps(df: pd.DataFrame, *also: str | None) -> pd.DataFrame:
    """DuckDB hands back dates in whatever unit it likes; use nanosecond timestamps everywhere."""
    for col in ("day", *[a for a in also if a]):
        if col in df:
            df[col] = pd.to_datetime(df[col]).dt.as_unit("ns")
    return df


def columns(con: duckdb.DuckDBPyConnection, src: str) -> list[str]:
    return [row[0] for row in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]


def build_panel(
    processed: Path,
) -> tuple[pd.DataFrame, pd.DataFrame | None, pd.DataFrame | None]:
    """(panel, totals, wiki) from the real pipeline outputs."""
    con = duckdb.connect()
    assign, articles = source(processed, "story_assignments"), source(processed, "articles")
    if assign is None or articles is None:
        raise FileNotFoundError(
            f"Need story_assignments and articles under {processed} (B and A write them). "
            "Use --source synthetic to try the pipeline without them."
        )
    require_columns(
        pd.DataFrame(columns=columns(con, assign)),
        ["article_id", "story_id", "day"],
        "story_assignments",
    )
    art_cols = columns(con, articles)
    require_columns(
        pd.DataFrame(columns=art_cols), ["id", "domain", "tone", "locations"], "articles"
    )

    con.execute(
        f"""CREATE TEMP VIEW joined AS
            SELECT s.story_id AS story_id, CAST(s.day AS DATE) AS day,
                   a.domain AS domain, a.tone AS tone, a.locations AS locations
            FROM {assign} s JOIN {articles} a ON a.id = s.article_id"""
    )
    panel = con.execute(
        """SELECT story_id, day, count(*) AS n_articles,
                  count(DISTINCT domain) AS n_domains, avg(tone) AS tone_mean
           FROM joined GROUP BY 1, 2"""
    ).df()
    countries = con.execute(
        """SELECT story_id, day, count(DISTINCT country) AS n_countries
           FROM (SELECT story_id, day,
                        unnest(list_transform(locations, lambda l: l.country)) AS country
                 FROM joined)
           GROUP BY 1, 2"""
    ).df()
    panel = timestamps(panel).merge(timestamps(countries), on=["story_id", "day"], how="left")
    panel["n_countries"] = panel["n_countries"].fillna(0)

    daily = source(processed, "stories_daily")
    if daily is not None:
        have = columns(con, daily)
        wanted = [c for c in ("category", "first_seen") if c in have]
        if wanted:
            extra = con.execute(
                f"SELECT story_id, CAST(day AS DATE) AS day, {', '.join(wanted)} FROM {daily}"
            ).df()
            extra = timestamps(extra, "first_seen" if "first_seen" in wanted else None)
            panel = panel.merge(extra, on=["story_id", "day"], how="left")

    totals = None
    given = source(processed, "daily_totals")
    if given is not None:
        totals = con.execute(f"SELECT CAST(day AS DATE) AS day, total_articles FROM {given}").df()
    else:
        when = "day" if "day" in art_cols else "published_at"
        totals = con.execute(
            f"SELECT CAST({when} AS DATE) AS day, count(*) AS total_articles FROM {articles} GROUP BY 1"
        ).df()

    wiki = None
    mapping, entity_daily = (
        source(processed, "story_wiki_entities"),
        source(processed, "wiki_entity_daily"),
    )
    if mapping is not None and entity_daily is not None:
        wiki = con.execute(wiki_sql(mapping, entity_daily, columns(con, mapping))).df()
    return panel, timestamps(totals), None if wiki is None else timestamps(wiki)


def wiki_sql(mapping: str, entity_daily: str, mapping_columns: list[str]) -> str:
    """Each story's Wikipedia series: its entities (B's story_wiki_entities) summed per day.

    - **Only the days around the story** (WIKI_DAYS_BEFORE before its first article to
      WIKI_DAYS_AFTER after its last). Every feature looks back at most SEQ_DAYS plus the views
      lag, and a story has no forecast rows more than 6 days after its last article, so this
      changes no feature; without it, every story gets every day since 2021 (hundreds of
      millions of rows on the full merge).
    - **8 languages and edits only from `full_from`** (B's column: the end of the first week
      A's run picked the entity as tier 1). A fetches a tier-1 entity's 8 languages and edits
      for its whole history, so using them before that week would show the entity's later
      prominence early. Before it, `wiki_views` is English views and `wiki_edits` is unknown.
      A mapping without `full_from` gets English views only, everywhere.
    """
    full_from = "CAST(full_from AS DATE)" if "full_from" in mapping_columns else "NULL"
    return f"""
        WITH span AS (
            SELECT story_id, min(day) - {WIKI_DAYS_BEFORE} AS lo, max(day) + {WIKI_DAYS_AFTER} AS hi
            FROM joined GROUP BY 1
        ), m AS (
            SELECT DISTINCT story_id, qid, CAST({full_from} AS DATE) AS full_from FROM {mapping}
        ), w AS (
            SELECT qid, CAST(day AS DATE) AS day, views, edits, views_en FROM {entity_daily}
        )
        SELECT m.story_id AS story_id, w.day AS day,
               sum(CASE WHEN w.day >= m.full_from THEN w.views ELSE w.views_en END) AS wiki_views,
               sum(CASE WHEN w.day >= m.full_from THEN w.edits END) AS wiki_edits,
               sum(w.views_en) AS wiki_views_en
        FROM m JOIN span s ON s.story_id = m.story_id
        JOIN w ON w.qid = m.qid AND w.day BETWEEN s.lo AND s.hi
        GROUP BY 1, 2"""
