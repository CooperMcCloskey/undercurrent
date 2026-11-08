"""Exact nearest-neighbour search by inner product, on the GPU when there is one.

The tracker's two heavy steps are searches: every leftover headline of the day against every
other (a day of 200,000 headlines is 200,000 x 200,000 x 384 multiplications), and every headline
against every live story. FAISS does them exactly on the CPU, which takes minutes a day on a
4-core VM; the same matrix products on an L4 take seconds, and that GPU is idle once the
embedding is done.

top_k() is the one entry point. It uses CUDA when PyTorch can see a GPU (and the job is big
enough to be worth it), and FAISS otherwise, and if the GPU fails (say, out of memory because
embedders are running) it falls back to FAISS for that call. Both are exact; they can differ in
the last bit of a similarity, or in the order of exact ties.

UC_SEARCH=cpu turns the GPU off.
"""

import logging
import os
import shutil
import sys
from functools import cache

import numpy as np

log = logging.getLogger(__name__)

# Below this many query x key pairs the GPU's start-up costs more than it saves.
GPU_MIN_WORK = 50_000_000
# At most this many scores (4 bytes each) are held on the GPU at once: 1 GB.
CHUNK_SCORES = 1 << 28


def get_faiss():
    """Import FAISS, safely.

    PyTorch and FAISS each bring their own OpenMP runtime, and on macOS they crash each other
    (segmentation fault) in one process unless PyTorch is loaded FIRST and FAISS then runs on one
    thread. So FAISS is imported here, on first use, never at module level; and if PyTorch is
    already loaded it gets one thread. The normal setup, embedding and tracking as separate
    processes, never has both and FAISS keeps every core."""
    import faiss

    if "torch" in sys.modules:
        faiss.omp_set_num_threads(1)
    return faiss


@cache
def gpu_available() -> bool:
    """Whether top_k may use a CUDA GPU. nvidia-smi is checked first, so a machine without an
    NVIDIA GPU never imports PyTorch here (which would also slow FAISS down; see get_faiss)."""
    if os.environ.get("UC_SEARCH", "auto") == "cpu" or shutil.which("nvidia-smi") is None:
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # no usable PyTorch or driver: just use the CPU
        return False


def _faiss_search(queries: np.ndarray, keys: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    index = get_faiss().IndexFlatIP(keys.shape[1])
    index.add(keys)
    sims, idx = index.search(queries, k)
    return sims, idx.astype(np.int64, copy=False)


def _torch_search(
    queries: np.ndarray, keys: np.ndarray, k: int, device: str = "cuda"
) -> tuple[np.ndarray, np.ndarray]:
    """The same search with PyTorch, in float32 (not TF32, so similarities match the CPU's to
    about 1e-6), a block of queries at a time so the score matrix stays small."""
    import torch

    on = torch.device(device)
    keys_t = torch.from_numpy(keys).to(on)
    block = max(256, min(8192, CHUNK_SCORES // max(len(keys), 1)))
    sims, idx = [], []
    with torch.no_grad():
        for start in range(0, len(queries), block):
            scores = torch.from_numpy(queries[start : start + block]).to(on) @ keys_t.T
            best, where = torch.topk(scores, k, dim=1)
            sims.append(best.cpu().numpy())
            idx.append(where.cpu().numpy())
    return np.concatenate(sims), np.concatenate(idx).astype(np.int64, copy=False)


def top_k(queries: np.ndarray, keys: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """The `k` keys with the highest inner product to each query: (similarities, indices), both
    (len(queries), k), best first. `k` is cut down to the number of keys."""
    k = min(k, len(keys))
    queries = np.ascontiguousarray(queries, dtype=np.float32)
    keys = np.ascontiguousarray(keys, dtype=np.float32)
    if k <= 0 or len(queries) == 0:
        return np.zeros((len(queries), max(k, 0)), np.float32), np.zeros(
            (len(queries), max(k, 0)), np.int64
        )
    if len(queries) * len(keys) >= GPU_MIN_WORK and gpu_available():
        try:
            return _torch_search(queries, keys, k)
        except Exception as error:  # e.g. out of GPU memory while embedders are running
            log.warning(
                "GPU search failed (%s: %s); using the CPU for this call",
                type(error).__name__,
                error,
            )
    return _faiss_search(queries, keys, k)
