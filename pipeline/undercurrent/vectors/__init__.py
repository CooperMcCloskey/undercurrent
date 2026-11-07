"""Workstream V (Vectors, owner: Cooper): every source's text as vectors in one shared space.

Public API (docs/WORKPLAN.md section 3.4):

    embed(texts, source)           clean + embed (float32, unit length); also used live
    load(source, days)             (ids, vectors) from the store, float32, unit length
    threshold(a, b)                the calibrated "same story" cosine cutoff for two sources
    categorize(vectors)            story category for each vector

The store (store.py) holds one file of vectors per source and day; run it with
`python -m undercurrent.vectors.store`. Cutoffs live in thresholds.py; calibrate.py measures them.
"""

from undercurrent.vectors.categories import categorize
from undercurrent.vectors.clean import is_usable
from undercurrent.vectors.embed import TEXT_VERSION, Source, clean_text, embed, embed_cleaned
from undercurrent.vectors.store import embed_day, is_done, load, load_day, vectors_dir
from undercurrent.vectors.thresholds import threshold

__all__ = [
    "TEXT_VERSION",
    "Source",
    "categorize",
    "clean_text",
    "embed",
    "embed_cleaned",
    "embed_day",
    "is_done",
    "is_usable",
    "load",
    "load_day",
    "threshold",
    "vectors_dir",
]
