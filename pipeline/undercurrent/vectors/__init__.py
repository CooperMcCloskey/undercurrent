"""Workstream V (Vectors, owner: Cooper): every source's text as vectors in one shared space.

Public API (see docs/WORKPLAN.md section 3.4): embed(), load(), threshold().
So far: embed(), clean_text() and categorize(). load() and threshold() come with the vector store.
Writes data/processed/vectors/ and the Wikipedia text tables.
"""

from undercurrent.vectors.categories import categorize
from undercurrent.vectors.embed import TEXT_VERSION, Source, clean_text, embed

__all__ = ["TEXT_VERSION", "Source", "categorize", "clean_text", "embed"]
