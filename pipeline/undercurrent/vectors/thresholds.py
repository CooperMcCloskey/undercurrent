"""Calibrated "same story" cosine cutoffs, per embedding model and pair of sources.

Similarity scales differ between models and between kinds of text (a short post and a headline
about the same event score lower than two headlines do), so nobody else hardcodes a number: they
call vectors.threshold(a, b). Values live here, in git, so every machine uses the same ones.

PLACEHOLDERS: until a pair is measured, its value is a guess and threshold() says so (once). To
measure, label ~50 pairs (same story / not) and run

    uv run python -m undercurrent.vectors.calibrate --pairs pairs.csv --a news --b news

then paste the line it prints below and add the pair to CALIBRATED. If the model changes
(config.EMBEDDING_MODEL), every cutoff has to be measured again.
"""

import logging

from undercurrent import config
from undercurrent.vectors.embed import Source

log = logging.getLogger(__name__)

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# model -> {(source a, source b): cutoff}. Pairs are unordered.
THRESHOLDS: dict[str, dict[tuple[Source, Source], float]] = {
    MODEL: {
        # Tuned on the hand-written demo items (stories/graph.py). Not yet checked on real titles.
        ("news", "news"): 0.42,
        # Guesses: posts and Wikipedia bullets are shorter and looser than headlines.
        ("bluesky", "news"): 0.35,
        ("wiki_events", "news"): 0.38,
        ("wiki_new", "news"): 0.38,
        ("reddit", "news"): 0.35,
    }
}

# (model, pair) entries above that were measured on labelled pairs rather than guessed.
CALIBRATED: set[tuple[str, tuple[Source, Source]]] = set()


_warned: set[tuple[str, tuple[Source, Source]]] = set()


def threshold(a: Source, b: Source) -> float:
    """The cosine similarity above which a text of source `a` and one of source `b` count as the
    same story, for the current model (config.EMBEDDING_MODEL). Order doesn't matter."""
    model = config.EMBEDDING_MODEL
    table = THRESHOLDS.get(model)
    if table is None:
        raise KeyError(
            f"No cutoffs for model {model!r}. Cutoffs differ between models, so measure them "
            "with `python -m undercurrent.vectors.calibrate` and add them to thresholds.py."
        )
    key = (a, b) if (a, b) in table else (b, a)
    if key not in table:
        raise KeyError(
            f"No cutoff for the pair ({a}, {b}) with {model!r}; add one to thresholds.py."
        )
    if (model, key) not in CALIBRATED and (model, key) not in _warned:
        _warned.add((model, key))
        log.warning(
            "Using a PLACEHOLDER cutoff of %.2f for %s vs %s: it has not been measured on "
            "labelled pairs (see vectors/thresholds.py).",
            table[key],
            a,
            b,
        )
    return table[key]
