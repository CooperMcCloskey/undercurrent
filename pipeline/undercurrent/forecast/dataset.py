"""From a story-day panel to training rows: features at day t, and the targets to learn.

The target for horizon h is the change in a story's article_count_7d (articles in the 7 days
ending that day), on a log scale:

    y_h = log2((a7(t + h) + 1) / (a7(t) + 1))

so a forecast is "this story will be 2^y times as big, give or take". a7 is exactly the number
the globe draws a story's heat from, which is why the model predicts it.

A row exists for every (story, day t) where the story had at least MIN_ARTICLES_7D articles in
the last 7 days. Rows near the end of the data have y_h = NaN: they can't be scored yet, but they
are the rows we forecast from.
"""

import numpy as np
import pandas as pd

from undercurrent.forecast import settings
from undercurrent.forecast.features import (
    CATEGORICAL,
    FEATURES,
    SEQ_CHANNELS,
    Dense,
    compute_features,
    ffill,
    gather_windows,
    lead,
    sequence_channels,
)
from undercurrent.forecast.schema import (
    CATEGORIES,
    CATEGORY,
    DAY,
    FIRST_SEEN,
    N_ARTICLES,
    PANEL_OPTIONAL,
    STORY,
    WIKI_COLUMNS,
    normalise_panel,
    normalise_totals,
    normalise_wiki,
)

__all__ = ["CATEGORICAL", "FEATURES", "build_dataset", "build_rows", "target_columns", "to_dense"]


def target_columns(horizons: list[int]) -> list[str]:
    return [f"y_{h}" for h in horizons] + [f"actual_{h}" for h in horizons]


def to_dense(
    panel: pd.DataFrame,
    days: pd.DatetimeIndex,
    totals: pd.Series | None,
    wiki: pd.DataFrame | None,
) -> Dense:
    """Turn a (normalised) chunk of the panel into matrices. `panel` must be sorted by story."""
    codes, uniques = pd.factorize(panel[STORY], sort=True)
    n_stories, n_days = len(uniques), len(days)
    d_idx = (panel[DAY] - days[0]).dt.days.to_numpy()

    counts = np.zeros((n_stories, n_days), dtype=np.float32)
    counts[codes, d_idx] = panel[N_ARTICLES].to_numpy(dtype=np.float32)

    metrics: dict[str, np.ndarray] = {}
    for col in PANEL_OPTIONAL:
        arr = np.full((n_stories, n_days), np.nan, dtype=np.float32)
        arr[codes, d_idx] = panel[col].to_numpy(dtype=np.float32, na_value=np.nan)
        metrics[col] = arr

    cat = np.full((n_stories, n_days), np.nan, dtype=np.float32)
    code_of_category = {name: float(i) for i, name in enumerate(CATEGORIES)}
    cat[codes, d_idx] = (
        panel[CATEGORY].map(code_of_category).to_numpy(dtype=np.float32, na_value=np.nan)
    )
    cat = ffill(cat)

    first_with_articles = np.full(n_stories, n_days, dtype=np.int64)
    np.minimum.at(first_with_articles, codes, d_idx)
    first_idx = first_with_articles
    if panel[FIRST_SEEN].notna().any():
        seen = (panel[FIRST_SEEN] - days[0]).dt.days.clip(lower=0)
        seen_by_story = seen.groupby(codes).min().reindex(range(n_stories))
        first_idx = np.where(
            seen_by_story.isna(),
            first_with_articles,
            np.minimum(seen_by_story.fillna(n_days).to_numpy(), first_with_articles),
        ).astype(np.int64)

    for col in WIKI_COLUMNS:
        metrics[col] = np.full((n_stories, n_days), np.nan, dtype=np.float32)
    if wiki is not None:
        w_code = wiki[STORY].map(pd.Series(range(n_stories), index=uniques))
        w_day = (wiki[DAY] - days[0]).dt.days
        keep = w_code.notna() & (w_day >= 0) & (w_day < n_days)
        for col in WIKI_COLUMNS:
            metrics[col][w_code[keep].to_numpy(dtype=int), w_day[keep].to_numpy()] = wiki.loc[
                keep, col
            ].to_numpy(dtype=np.float32, na_value=np.nan)

    total_arr = np.full(n_days, np.nan, dtype=np.float32)
    if totals is not None:
        total_arr = totals.reindex(days).to_numpy(dtype=np.float32, na_value=np.nan)

    return Dense(
        story_ids=np.asarray(uniques, dtype=object),
        days=days,
        first_idx=first_idx,
        counts=counts,
        metrics=metrics,
        cat_code=cat,
        totals=total_arr,
    )


def build_dataset(
    panel: pd.DataFrame,
    horizons: list[int],
    *,
    totals: pd.DataFrame | None = None,
    wiki: pd.DataFrame | None = None,
    min_articles: int = settings.MIN_ARTICLES_7D,
    burn_in: int = settings.BURN_IN_DAYS,
    chunk: int = settings.STORY_CHUNK,
    seq_len: int = 0,
) -> tuple[pd.DataFrame, np.ndarray | None]:
    """One row per (story, day t) with FEATURES, a7, and for each horizon h the target y_h and
    the actual a7 at t + h (NaN when t + h is beyond the data).

    With seq_len > 0 also returns, row for row, each story's last `seq_len` days of history as a
    (rows, seq_len, len(SEQ_CHANNELS)) float16 array, for the neural model. Same no-lookahead rule
    as the features."""
    panel = normalise_panel(panel)
    totals_s = normalise_totals(totals)
    wiki_df = normalise_wiki(wiki)
    # One datetime unit everywhere (pandas 3 mixes ns and us, which would misalign day lookups).
    days = pd.date_range(panel[DAY].min(), panel[DAY].max(), freq="D").as_unit("ns")

    story = panel[STORY].to_numpy()
    starts = np.flatnonzero(np.r_[True, story[1:] != story[:-1]])
    bounds = np.r_[starts, len(panel)]
    parts, seq_parts = [], []
    for lo in range(0, len(starts), chunk):
        sub = panel.iloc[bounds[lo] : bounds[min(lo + chunk, len(starts))]]
        sub_wiki = None
        if wiki_df is not None:
            sub_wiki = wiki_df[wiki_df[STORY].isin(set(sub[STORY].unique()))]
        dense = to_dense(sub, days, totals_s, sub_wiki)
        feats = compute_features(dense)
        a7 = feats["_a7"]
        t = np.arange(len(days))[None, :]
        eligible = (t >= dense.first_idx[:, None]) & (a7 >= min_articles) & (t >= burn_in)
        s_idx, t_idx = np.nonzero(eligible)
        if len(s_idx) == 0:
            continue
        data: dict[str, np.ndarray] = {
            STORY: dense.story_ids[s_idx],
            DAY: days.to_numpy()[t_idx],
            "a7": a7[s_idx, t_idx],
        }
        for name in FEATURES:
            data[name] = feats[name][s_idx, t_idx]
        for h in horizons:
            future = lead(a7, h)
            data[f"actual_{h}"] = future[s_idx, t_idx]
            data[f"y_{h}"] = np.log2((future[s_idx, t_idx] + 1) / (a7[s_idx, t_idx] + 1))
        parts.append(pd.DataFrame(data))
        if seq_len:
            seq_parts.append(gather_windows(sequence_channels(dense), s_idx, t_idx, seq_len))
    if not parts:
        empty = {c: [] for c in [STORY, DAY, "a7", *FEATURES, *target_columns(horizons)]}
        no_seq = np.zeros((0, seq_len, len(SEQ_CHANNELS)), dtype=np.float16) if seq_len else None
        return pd.DataFrame(empty), no_seq
    rows = pd.concat(parts, ignore_index=True)
    rows[DAY] = pd.to_datetime(rows[DAY])
    order = rows.sort_values([DAY, STORY]).index.to_numpy()
    seq = np.concatenate(seq_parts)[order] if seq_len else None
    return rows.iloc[order].reset_index(drop=True), seq


def build_rows(panel: pd.DataFrame, horizons: list[int], **kwargs) -> pd.DataFrame:
    """build_dataset without the sequences (what the tree model needs)."""
    return build_dataset(panel, horizons, **kwargs)[0]
