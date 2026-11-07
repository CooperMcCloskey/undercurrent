"""Proxy stories for developing and testing the model before real stories exist.

A proxy story is a (topic group x country) pair: for example "protest-fr" is every article about
protests or strikes whose main place is France. The groups come from GDELT theme tags, so this
needs only A's articles.parquet: no embeddings, no clustering.

It is also the cheapest feasibility check we have. If the model can't beat "it stays as it is"
on these series, real stories won't rescue it; if it can, we know the pipeline works end to end.
Proxy stories are broader and slower-moving than real ones, so don't quote their numbers.
"""

from pathlib import Path

import duckdb
import pandas as pd

from undercurrent.forecast.panel import columns, source, timestamps
from undercurrent.forecast.schema import require_columns

# group -> (GKG theme prefixes, contracts.Category)
GROUPS: dict[str, tuple[tuple[str, ...], str]] = {
    "election": (("ELECTION",), "politics"),
    "protest": (("PROTEST", "STRIKE"), "society"),
    "conflict": (("ARMEDCONFLICT", "TERROR", "MILITARY"), "conflict"),
    "disaster": (("NATURAL_DISASTER",), "disaster"),
    "health": (("HEALTH_", "EPIDEMIC"), "health"),
    "economy": (("ECON_",), "economy"),
    "cyber": (("CYBER_ATTACK",), "technology"),
    "migration": (("REFUGEES", "MIGRATION"), "society"),
    "sanctions": (("SANCTION",), "politics"),
    "corruption": (("CORRUPTION",), "politics"),
}


def _case() -> str:
    arms = []
    for group, (prefixes, _) in GROUPS.items():
        test = " OR ".join(f"starts_with(theme, '{p}')" for p in prefixes)
        arms.append(f"WHEN {test} THEN '{group}'")
    return "CASE " + " ".join(arms) + " END"


def build_panel(processed: Path) -> tuple[pd.DataFrame, pd.DataFrame | None, None]:
    """(panel, totals, None) of proxy stories from articles.parquet."""
    con = duckdb.connect()
    articles = source(processed, "articles")
    if articles is None:
        raise FileNotFoundError(f"No articles under {processed}: A writes articles.parquet.")
    cols = columns(con, articles)
    require_columns(
        pd.DataFrame(columns=cols), ["domain", "tone", "themes", "locations"], "articles"
    )
    when = "day" if "day" in cols else "published_at"
    panel = con.execute(
        f"""WITH t AS (
              SELECT CAST({when} AS DATE) AS day, domain, tone, locations[1].country AS country,
                     unnest(themes) AS theme, id
              FROM {articles}
              WHERE len(themes) > 0 AND locations[1].country IS NOT NULL
            ),
            g AS (SELECT DISTINCT id, day, domain, tone, country, {_case()} AS grp FROM t)
            SELECT lower(grp) || '-' || lower(country) AS story_id, day, grp,
                   count(*) AS n_articles, count(DISTINCT domain) AS n_domains,
                   avg(tone) AS tone_mean
            FROM g WHERE grp IS NOT NULL GROUP BY 1, 2, 3"""
    ).df()
    panel["category"] = panel.pop("grp").map({g: cat for g, (_, cat) in GROUPS.items()})
    totals = con.execute(
        f"SELECT CAST({when} AS DATE) AS day, count(*) AS total_articles FROM {articles} GROUP BY 1"
    ).df()
    return timestamps(panel), timestamps(totals), None
