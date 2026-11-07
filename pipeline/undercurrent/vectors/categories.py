"""What is a story about? Zero-shot categories from the same vectors as everything else.

Each category is described by a handful of short, headline-like examples. Their vectors are
averaged into one "prototype" per category, and a story gets the category whose prototype is
most similar to the story's centroid. No extra model and no training data, and it works for every
source and language the embedding model handles.

To improve a category, edit its examples: write them like real headlines. The demo
(uv run python -m undercurrent.demo) scores the result against hand labels.

Later, GDELT's own theme tags (ELECTION, PROTEST, NATURAL_DISASTER, ECON_*...) could add a
second vote for news-heavy stories.
"""

from functools import cache

import numpy as np

from undercurrent.contracts import Category
from undercurrent.vectors.embed import embed

CATEGORY_EXAMPLES: dict[Category, list[str]] = {
    "politics": [
        "Election results disputed as vote count is delayed",
        "Parliament passes a new law after a heated debate",
        "President names new ministers in a cabinet reshuffle",
        "Supreme court overturns the election result",
        "Coalition government collapses after party quits",
        "Opposition leader calls for a referendum",
        "Foreign ministers hold talks on a diplomatic dispute",
        "Prime minister faces a confidence vote",
    ],
    "conflict": [
        "Missile strikes hit the city overnight",
        "Soldiers killed in clashes near the border",
        "Rebels seize a town after heavy fighting",
        "Air raid destroys a residential building",
        "Ceasefire talks stall as shelling continues",
        "Army deploys troops after an armed attack",
        "Bombing kills dozens in a crowded market",
        "Drone attack targets a military base",
    ],
    "economy": [
        "Central bank raises interest rates to curb borrowing",
        "Trade war escalates as both sides raise duties",
        "Stock markets fall on recession fears",
        "Oil prices jump as producers cut output",
        "Currency slides to a record low against the dollar",
        "Fuel and food prices climb again",
        "Trade talks collapse over export quotas",
        "Company announces thousands of job cuts",
    ],
    "society": [
        "Airline staff strike over working hours",
        "Thousands march through the capital in protest",
        "Union calls a nationwide general strike",
        "Rights groups condemn the crackdown on activists",
        "Migrants and refugees stranded at the border",
        "Teachers and nurses stage a walkout",
        "Court ruling sparks demonstrations",
        "Police clash with protesters in the streets",
    ],
    "disaster": [
        "Floods force thousands to evacuate their homes",
        "Forest fires rage across the countryside",
        "Earthquake kills hundreds and flattens buildings",
        "Hurricane makes landfall with destructive winds",
        "Volcano erupts, covering towns in ash",
        "Typhoon brings torrential rain and a storm surge",
        "Landslide buries a mountain village",
        "Heatwave and drought scorch the region",
    ],
    "health": [
        "Disease outbreak spreads to new provinces",
        "Hospitals overwhelmed as virus cases surge",
        "Health officials declare an epidemic",
        "New vaccine approved for emergency use",
        "Cholera cases rise after the floods",
        "Shortage of medicines hits pharmacies",
    ],
    "technology": [
        "Cyberattack shuts down government websites",
        "New artificial intelligence model released",
        "Rocket launch puts satellites into orbit",
        "Social media platform fined over user data",
        "Tech company suffers a major data breach",
        "Undersea internet cable cut, disrupting connections",
    ],
}

# Below this similarity to every prototype, a story is "other".
MIN_SIMILARITY = 0.2


@cache
def prototypes() -> tuple[tuple[Category, ...], np.ndarray]:
    """One L2-normalised prototype vector per category (cached per process)."""
    names = tuple(CATEGORY_EXAMPLES)
    rows = []
    for name in names:
        mean = embed(CATEGORY_EXAMPLES[name]).mean(axis=0)
        rows.append(mean / np.linalg.norm(mean))
    return names, np.stack(rows)


def categorize(vectors: np.ndarray) -> tuple[list[Category], np.ndarray]:
    """Category for each (normalised) vector, e.g. story centroids, plus the similarity matrix
    (n, categories) for diagnostics."""
    names, protos = prototypes()
    sims = vectors @ protos.T
    best = sims.argmax(axis=1)
    labels: list[Category] = [
        names[b] if sims[i, b] >= MIN_SIMILARITY else "other" for i, b in enumerate(best)
    ]
    return labels, sims
