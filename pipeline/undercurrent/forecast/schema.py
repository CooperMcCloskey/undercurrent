"""The story-day panel: what the forecast model reads, and the one place that knows the other
workstreams' file schemas.

If B or A change a column name or type, the fix goes here (and in panel.py's SQL), nothing else.

The panel is one row per (story, day) on which the story had at least one article:

    story_id    str    matches contracts.STORY_ID_PATTERN
    day         date   UTC day
    n_articles  int    deduplicated articles that day (the volume convention in docs/WORKPLAN.md)

and optionally (NaN / None when unknown; the model copes with missing columns):

    n_domains   int    distinct source domains that day
    n_countries int    distinct countries mentioned that day
    tone_mean   float  mean GDELT tone that day
    category    str    one of contracts.Category, as of that day
    first_seen  date   when the story first appeared (default: its first day in the panel)

A separate "wiki panel" (story_id, day, wiki_views, wiki_edits, wiki_views_en) has one row per
day of the story's Wikipedia entities, including days with no articles. And "totals" (day,
total_articles) is the day's article count over all stories, to normalise volume.
"""

from collections.abc import Iterable
from typing import get_args

import numpy as np
import pandas as pd

from undercurrent import contracts

STORY = "story_id"
DAY = "day"
N_ARTICLES = "n_articles"
FIRST_SEEN = "first_seen"
CATEGORY = "category"
TOTAL = "total_articles"

PANEL_OPTIONAL = ["n_domains", "n_countries", "tone_mean"]
WIKI_COLUMNS = ["wiki_views", "wiki_edits", "wiki_views_en"]
CATEGORIES: list[str] = list(get_args(contracts.Category))


def require_columns(df: pd.DataFrame, required: Iterable[str], what: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"{what} is missing columns {missing} (it has {list(df.columns)}). The schema the "
            "forecast code expects is in forecast/schema.py and docs/WORKPLAN.md; if another "
            "workstream changed a file, update schema.py (and panel.py)."
        )


def _day(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series).dt.normalize().astype("datetime64[ns]")


def normalise_panel(panel: pd.DataFrame) -> pd.DataFrame:
    """Check and complete a panel: all optional columns present, days as timestamps, sorted."""
    require_columns(panel, [STORY, DAY, N_ARTICLES], "panel")
    df = panel.copy()
    df[DAY] = _day(df[DAY])
    if df.duplicated([STORY, DAY]).any():
        raise ValueError("panel has more than one row for some (story_id, day)")
    for col in PANEL_OPTIONAL:
        if col not in df:
            df[col] = np.nan
    if CATEGORY not in df:
        df[CATEGORY] = None
    df[FIRST_SEEN] = _day(df[FIRST_SEEN]) if FIRST_SEEN in df else pd.NaT
    return df.sort_values([STORY, DAY]).reset_index(drop=True)


def normalise_totals(totals: pd.DataFrame | None) -> pd.Series | None:
    if totals is None or totals.empty:
        return None
    require_columns(totals, [DAY, TOTAL], "totals")
    return pd.Series(
        totals[TOTAL].to_numpy(dtype="float64"), index=pd.DatetimeIndex(_day(totals[DAY]))
    )


def normalise_wiki(wiki: pd.DataFrame | None) -> pd.DataFrame | None:
    if wiki is None or wiki.empty:
        return None
    require_columns(wiki, [STORY, DAY], "wiki panel")
    df = wiki.copy()
    df[DAY] = _day(df[DAY])
    for col in WIKI_COLUMNS:
        if col not in df:
            df[col] = np.nan
    return df
