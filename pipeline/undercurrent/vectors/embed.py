"""Text -> vectors. Every source goes through embed(), so all vectors share one space.

The model is a small local embedding model (sentence-transformers), not a generative LLM: it maps
a text to `dim` numbers so that texts with similar meaning point in similar directions.
Vectors are L2-normalised, so cosine similarity is a plain dot product.
"""

import html
import logging
import os
import re
import sys
from functools import cache
from typing import Literal

import numpy as np

from undercurrent import config  # sets HF_HOME before any model download

log = logging.getLogger(__name__)

Source = Literal["news", "bluesky", "wiki_events", "wiki_new", "reddit"]

# Bump when cleaning changes: stored vectors made with another version must be rebuilt.
TEXT_VERSION = 1

_SITE_SUFFIX = re.compile(r"\s+[|\-–—]\s+[^|\-–—]{2,40}$")  # "Headline - BBC News"
_URL = re.compile(r"https?://\S+")
_MENTION = re.compile(r"@[\w.\-]+")


def clean_text(text: str, source: Source) -> str:
    """The exact text that gets embedded. Keep this deterministic: it's part of the cache key."""
    t = html.unescape(text).strip()
    if source == "news":
        t = _SITE_SUFFIX.sub("", t)
    elif source in ("bluesky", "reddit"):
        t = _URL.sub(" ", t)
        t = _MENTION.sub(" ", t)
        t = t.replace("#", "")
    return " ".join(t.split())


def pick_device() -> str:
    """UC_DEVICE if set, else CUDA, then Apple's MPS, then the CPU."""
    device = os.getenv("UC_DEVICE")
    if device:
        return device
    import torch

    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


@cache
def _model():
    if "faiss" in sys.modules:
        log.warning(
            "FAISS is already loaded in this process. Loading PyTorch after it can crash on "
            "macOS (segmentation fault). Run embedding and story tracking as separate processes."
        )
    from sentence_transformers import SentenceTransformer  # slow import, so only when needed

    device = pick_device()
    model = SentenceTransformer(config.EMBEDDING_MODEL, device=device)
    if device.startswith("cuda") and os.getenv("UC_HALF") == "1":
        model.half()  # about twice as fast on a GPU; vectors differ from the CPU's in the 3rd decimal
    return model


def embed_cleaned(cleaned: list[str], batch_size: int = 256) -> np.ndarray:
    """Embed texts that clean_text() has already cleaned. Returns float32 (n, dim), L2-normalised.

    Cleaning is not idempotent (a second pass can strip real words from "A - B debate - CNN"), so
    code that stores the cleaned text, like the vector store, must call this and not embed()."""
    texts = list(cleaned)
    if "e5" in config.EMBEDDING_MODEL.lower():  # e5 models expect a task prefix
        texts = [f"query: {t}" for t in texts]
    vectors = _model().encode(
        texts, batch_size=batch_size, normalize_embeddings=True, convert_to_numpy=True
    )
    return np.ascontiguousarray(vectors, dtype=np.float32)


def embed(texts: list[str], source: Source = "news", batch_size: int = 256) -> np.ndarray:
    """Clean and embed texts. Returns float32 (n, dim), L2-normalised."""
    return embed_cleaned([clean_text(t, source) for t in texts], batch_size)
