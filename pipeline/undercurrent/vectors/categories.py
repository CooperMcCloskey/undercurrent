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

import hashlib
import json
import os
from functools import cache
from pathlib import Path

import numpy as np

from undercurrent import config
from undercurrent.contracts import Category
from undercurrent.vectors.embed import TEXT_VERSION, embed

CATEGORY_EXAMPLES: dict[Category, list[str]] = {
    "politics": [
        "Election results disputed as vote count is delayed",
        "Parliament passes a new law after a heated debate",
        "President names new ministers in a cabinet reshuffle",
        "Supreme court overturns the election result",
        "Parliament is dissolved ahead of early elections",
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
        "Government cuts fuel subsidies to save money",
        "Fuel and food prices climb again",
        "Trade talks collapse over export quotas",
        "Unemployment rises for the third month in a row",
    ],
    "society": [
        "Airline staff strike over working hours",
        "Thousands march through the capital in protest",
        "Union calls a nationwide general strike",
        "Rights groups condemn the crackdown on activists",
        "Migrants and refugees stranded at the border",
        "Teachers stage a walkout over class sizes",
        "Court ruling sparks demonstrations",
        "Police clash with protesters in the streets",
    ],
    "disaster": [
        "Floods force thousands to evacuate their homes",
        "Forest fires rage across the countryside",
        "Earthquake kills hundreds and flattens buildings",
        "Hurricane makes landfall with destructive winds",
        "Avalanche buries part of a ski resort",
        "Tsunami warning issued after an undersea quake",
        "Landslide buries a mountain village",
        "Heatwave and drought scorch the region",
    ],
    "health": [
        "Disease outbreak spreads to new provinces",
        "Hospitals overwhelmed as virus cases surge",
        "Health officials declare an epidemic",
        "New vaccine approved for emergency use",
        "Measles spreads among unvaccinated children",
        "Shortage of medicines hits pharmacies",
    ],
    "technology": [
        "Hackers leak millions of customer passwords",
        "New artificial intelligence model released",
        "Space agency tests a reusable booster",
        "Astronauts return to Earth after six months on the space station",
        "Space telescope captures images of a distant galaxy",
        "Social media platform fined over user data",
        "Tech company suffers a major data breach",
        "Streaming service suffers a worldwide outage",
    ],
}

# Below this similarity to every prototype, a story is "other".
MIN_SIMILARITY = 0.2


def _cache_path() -> Path:
    """Where the prototypes are kept on disk. The key covers the model, the text cleaning and
    the examples, so editing any of them makes a new file instead of using stale vectors."""
    key = json.dumps([config.EMBEDDING_MODEL, TEXT_VERSION, CATEGORY_EXAMPLES], sort_keys=True)
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return config.CACHE / "category_prototypes" / f"{digest}.npy"


@cache
def prototypes() -> tuple[tuple[Category, ...], np.ndarray]:
    """One L2-normalised prototype vector per category (cached per process, and on disk).

    The disk cache means a process that only needs categories (the story tracker) never has to
    load the embedding model, and PyTorch and FAISS can crash each other in one process."""
    names = tuple(CATEGORY_EXAMPLES)
    path = _cache_path()
    if path.exists():
        return names, np.load(path)
    rows = []
    for name in names:
        mean = embed(CATEGORY_EXAMPLES[name]).mean(axis=0)
        rows.append(mean / np.linalg.norm(mean))
    stacked = np.stack(rows).astype(np.float32)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "wb") as f:
        np.save(f, stacked)
    os.replace(tmp, path)
    return names, stacked


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
