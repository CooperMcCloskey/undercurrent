"""A fake story panel with story-like dynamics, for developing and testing without real data.

Each story rises for a few days, then fades as a power law; the fade speed depends on the
category (disasters fade fast, conflicts slowly); some stories get a second surge; weekends are
quieter. Wikipedia pageviews lead the news by two days. So a good model should find real signal
(age, position relative to the peak, Wikipedia growth), and tests can check that it does.

This is NOT a model of real news. Never report numbers from it.
"""

import numpy as np
import pandas as pd

# Category -> how fast stories in it fade (the exponent of the power-law decay).
DECAY = {
    "disaster": 1.5,
    "politics": 0.9,
    "conflict": 0.5,
    "economy": 0.8,
    "society": 1.0,
    "health": 0.7,
    "technology": 1.1,
}


def _curve(t: np.ndarray, rise: int, alpha: float) -> np.ndarray:
    """0 before the story, a linear rise to 1 at day `rise`, then (days since peak + 1)^-alpha."""
    t = np.asarray(t, dtype=float)
    up = (t + 1) / (rise + 1)
    down = np.maximum(t - rise + 1, 1.0) ** -alpha
    return np.where(t < 0, 0.0, np.where(t < rise, up, down))


def make_panel(
    n_stories: int = 300, n_days: int = 420, seed: int = 0, start: str = "2025-01-01"
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(panel, totals, wiki) in the formats described in forecast/schema.py."""
    rng = np.random.default_rng(seed)
    days = pd.date_range(start, periods=n_days).as_unit("ns")
    weekday = np.where(days.day_of_week >= 5, 0.75, 1.0)
    categories = list(DECAY)
    total = np.zeros(n_days)
    panels, wikis = [], []
    for i in range(n_stories):
        category = categories[int(rng.integers(len(categories)))]
        birth = int(rng.integers(0, n_days - 14))
        peak = float(np.exp(rng.normal(3.4, 0.9)))  # busiest day, in articles
        rise = int(rng.integers(1, 4))
        alpha = DECAY[category] * rng.uniform(0.7, 1.3)
        t = np.arange(n_days - birth)
        mean = peak * _curve(t, rise, alpha)
        if rng.random() < 0.3:  # a second surge
            t0 = rise + int(rng.integers(4, 25))
            mean = mean + peak * rng.uniform(0.3, 1.2) * _curve(t - t0, rise, alpha)
        mean = mean * weekday[birth:]
        counts = np.zeros(n_days, dtype=int)
        counts[birth:] = rng.poisson(mean)
        total += counts
        active = np.flatnonzero(counts > 0)
        if len(active) == 0:
            continue
        story_id = f"synthetic-{i:04d}"
        n = counts[active]
        panels.append(
            pd.DataFrame(
                {
                    "story_id": story_id,
                    "day": days[active],
                    "n_articles": n,
                    "n_domains": np.minimum(n, np.maximum(1, np.round(n**0.7))).astype(int),
                    "n_countries": 1 + (n > 8).astype(int) + (n > 30).astype(int),
                    "tone_mean": rng.normal(-2.5, 1.0, len(n)),
                    "category": category,
                    "first_seen": days[active[0]],
                }
            )
        )
        mean_full = np.zeros(n_days)
        mean_full[birth:] = mean
        lead_mean = np.r_[mean_full[2:], 0.0, 0.0]  # Wikipedia sees the story two days early
        w_days = np.arange(max(active[0] - 3, 0), n_days)
        views = rng.poisson(30 + 60 * lead_mean[w_days])
        wikis.append(
            pd.DataFrame(
                {
                    "story_id": story_id,
                    "day": days[w_days],
                    "wiki_views": views,
                    "wiki_edits": rng.poisson(0.05 * views),
                    "wiki_views_en": rng.binomial(views, 0.6),
                }
            )
        )
    totals = pd.DataFrame({"day": days, "total_articles": total + rng.poisson(3000 * weekday)})
    return pd.concat(panels, ignore_index=True), totals, pd.concat(wikis, ignore_index=True)
