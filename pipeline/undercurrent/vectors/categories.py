"""What is a story about? Categories from the same vectors as everything else.

Two ways, both on the multilingual embedding model:

1. **Prototypes** (`categorize`): each category is described by a handful of short, headline-like
   examples whose vectors are averaged into one prototype; a story takes the category whose
   prototype is closest to its centroid. No training data. This is what the story tracker uses,
   every day, on centroids. On 246 hand-labelled headlines it gets 65%.
2. **A trained classifier** (`classify_titles`): logistic regression on the vectors of those 246
   labelled story headlines (`category_labels.json`) plus the examples above, with each story's
   probabilities averaged over its several headlines. 75% on a single unseen headline (5-fold
   cross-validation; `--evaluate` prints it), and a story has several. Its confidence means
   something: above 0.35 it is right 80% of the time, below it 47%. The exporter uses it.

Both are far from perfect on the real boundaries: politics against conflict ("Iran warns Bulgaria
against aiding US military operations"), society against politics (court cases about politicians),
sport against culture (a footballer's death). Add labelled headlines to `category_labels.json`
(the exported story titles are the best source) and the classifier improves without code changes:

    uv run python -m undercurrent.vectors.categories --evaluate

To improve the prototypes, edit the examples: write them like real headlines.

Later, GDELT's own theme tags (ELECTION, PROTEST, NATURAL_DISASTER, ECON_*...) could add a
second vote for news-heavy stories; the exporter already uses them where the title is unsure.
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
        "Scientists discover a new species of dinosaur",
        "Researchers publish a breakthrough study on the brain",
    ],
}

# Added 2026-11-08: on real headlines 41% of story-days were "other", much of it sport and
# entertainment, which had no category.
CATEGORY_EXAMPLES["sport"] = [
    "Striker scores twice as the champions win the derby",
    "Coach sacked after a run of defeats",
    "Team reaches the cup final after a penalty shootout",
    "Tennis star wins the title in straight sets",
    "Driver takes pole position for the Grand Prix",
    "Transfer deal agreed for the club's record signing",
    "Olympic champion breaks the world record",
    "Fans celebrate as the national team qualifies for the World Cup",
]
CATEGORY_EXAMPLES["culture"] = [
    "Actor wins best actor award at the film festival",
    "Singer announces a world tour and a new album",
    "Box office: the sequel tops the weekend charts",
    "Celebrity couple announce their divorce",
    "Popular TV series renewed for a new season",
    "Museum opens an exhibition of the artist's work",
    "Novelist wins the literary prize",
    "Famous musician dies at 76",
]

# Added 2026-11-08 after reading the 300 stories of the 2026-11-06 snapshot: the first examples
# described events (strikes, floods, fighting), so stories about what a leader said or did, a
# court case, a plane crash, a celebrity or a company's results all fell below MIN_SIMILARITY and
# became "other" (36% of stories). These are written like the headlines that were missed, not
# copied from them.
CATEGORY_EXAMPLES["politics"] += [
    "President says he is ready to meet his rival for peace talks",
    "Minister resigns after criticism of the government",
    "Senator endorses a candidate ahead of the vote",
    "Leaders sign a treaty at the summit",
    "Foreign minister denies any involvement in the coup",
    "Government says it will cut off aid to an ally",
]
CATEGORY_EXAMPLES["society"] += [
    "Gunman opens fire at a festival, leaving one dead",
    "Court sentences an activist to ten years in prison",
    "Judge dismisses a lawsuit against the newspaper",
    "Police arrest a suspect after the shooting",
    "Man found guilty of assault",
]
CATEGORY_EXAMPLES["economy"] += [
    "Company reports quarterly earnings above expectations",
    "Gold prices climb to a record high",
    "Airline warns of higher fares as fuel costs rise",
    "Bank collapses and savers lose access to their money",
]
CATEGORY_EXAMPLES["disaster"] += [
    "Plane crashes shortly after takeoff, killing passengers",
    "Train derails and dozens are dead",
]
CATEGORY_EXAMPLES["culture"] += [
    "Actor dies at 50 after a heart attack",
    "What to watch on television and streaming this weekend",
    "Pope welcomes a famous singer to the Vatican",
    "Video game guide and walkthrough",
    "Celebrities show off their Halloween costumes",
]
CATEGORY_EXAMPLES["technology"] += [
    "Carmaker winds down production of its electric truck",
    "Company unveils a new smartphone",
]

# Below this similarity to every prototype, a story is "other". Read on the 2026-11-06 stories:
# between 0.16 and 0.20 the closest prototype was right for about 3 in 4; between 0.12 and 0.16
# for fewer than 1 in 2, so those stay "other".
MIN_SIMILARITY = 0.16


def _cache_path() -> Path:
    """Where the prototypes are kept on disk. The key covers the model, the text cleaning and
    the examples, so editing any of them makes a new file instead of using stale vectors."""
    key = json.dumps([config.EMBEDDING_MODEL, TEXT_VERSION, CATEGORY_EXAMPLES], sort_keys=True)
    digest = hashlib.sha1(key.encode()).hexdigest()[:16]
    return config.CACHE / "category_prototypes" / f"{digest}.npy"


def cached() -> bool:
    """Whether prototypes() can load from disk, without loading the embedding model."""
    return _cache_path().exists()


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


# ---- The trained classifier -----------------------------------------------------------------------

LABELS_PATH = Path(__file__).with_name("category_labels.json")
CLASSIFIER_C = 10.0  # logistic regression: higher fits the labels harder; 5 to 20 were equal in CV


def labelled() -> tuple[list[str], list[Category]]:
    """The hand-labelled story headlines: (titles, categories)."""
    rows = json.loads(LABELS_PATH.read_text())
    return [r["title"] for r in rows], [r["category"] for r in rows]


def _training_data() -> tuple[np.ndarray, list[Category]]:
    """Vectors and categories to train on: the labelled headlines, then the prototype examples.
    Cached on disk, keyed like the prototypes, so only the first run loads the embedding model."""
    titles, cats = labelled()
    examples = [(text, cat) for cat, texts in CATEGORY_EXAMPLES.items() for text in texts]
    texts = titles + [t for t, _ in examples]
    labels = cats + [c for _, c in examples]
    key = json.dumps([config.EMBEDDING_MODEL, TEXT_VERSION, texts, labels])
    path = (
        config.CACHE
        / "category_prototypes"
        / f"train-{hashlib.sha1(key.encode()).hexdigest()[:16]}.npy"
    )
    if path.exists():
        return np.load(path), labels
    vectors = embed(texts)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    with open(tmp, "wb") as f:
        np.save(f, vectors)
    os.replace(tmp, path)
    return vectors, labels


@cache
def classifier():
    """The fitted classifier (cached per process)."""
    from sklearn.linear_model import LogisticRegression

    vectors, labels = _training_data()
    return LogisticRegression(C=CLASSIFIER_C, max_iter=3000, class_weight="balanced").fit(
        vectors, labels
    )


def classify_titles(titles: dict[str, list[str]]) -> dict[str, tuple[Category, float]]:
    """Category and confidence (0..1) for each key, from its headlines: the classifier's
    probabilities, averaged over the headlines. Keys with no headline are left out."""
    keys = [k for k, ts in titles.items() if ts]
    if not keys:
        return {}
    flat = [t for k in keys for t in titles[k]]
    probs = classifier().predict_proba(embed(flat))
    classes = classifier().classes_
    out: dict[str, tuple[Category, float]] = {}
    at = 0
    for k in keys:
        n = len(titles[k])
        mean = probs[at : at + n].mean(axis=0)
        at += n
        best = int(mean.argmax())
        out[k] = (classes[best], float(mean[best]))
    return out


def evaluate() -> None:
    """Accuracy on the labelled headlines: the prototypes, and the classifier by 5-fold CV."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import classification_report
    from sklearn.model_selection import StratifiedKFold

    titles, cats = labelled()
    y = np.array(cats)
    x = embed(titles)
    names, protos = prototypes()
    sims = x @ protos.T
    pred = np.array([names[b] for b in sims.argmax(axis=1)])
    print(f"{len(titles)} labelled headlines")
    print(f"prototypes (closest, no 'other'): {(pred == y).mean():.1%}")
    vectors, labels = _training_data()
    ex_x, ex_y = vectors[len(titles) :], np.array(labels[len(titles) :])
    cv_pred = np.empty(len(y), dtype=object)
    # Stratify on a class with at least 5 members: health has fewer in some label sets.
    strata = np.where(np.isin(y, ["health", "other"]), "rare", y)
    for tr, te in StratifiedKFold(5, shuffle=True, random_state=0).split(x, strata):
        model = LogisticRegression(C=CLASSIFIER_C, max_iter=3000, class_weight="balanced")
        model.fit(np.vstack([x[tr], ex_x]), np.concatenate([y[tr], ex_y]))
        cv_pred[te] = model.predict(x[te])
    print(f"classifier (5-fold cross-validation): {(cv_pred == y).mean():.1%}\n")
    print(classification_report(y, cv_pred, zero_division=0))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--evaluate", action="store_true", help="accuracy on the labelled headlines")
    ap.parse_args()
    evaluate()
