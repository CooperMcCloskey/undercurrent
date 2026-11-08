"""The nearest-neighbour search the tracker relies on: the same answer on the CPU and the GPU."""

import logging
import subprocess
import sys

import numpy as np
import pytest

from undercurrent.stories import search


def unit(x: np.ndarray) -> np.ndarray:
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype(np.float32)


def brute_force(queries: np.ndarray, keys: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    scores = queries @ keys.T
    order = np.argsort(-scores, axis=1, kind="stable")[:, :k]
    return np.take_along_axis(scores, order, axis=1), order


def world(n_queries: int = 40, n_keys: int = 300, dim: int = 16, seed: int = 0):
    rng = np.random.default_rng(seed)
    return unit(rng.standard_normal((n_queries, dim))), unit(rng.standard_normal((n_keys, dim)))


def test_top_k_is_exact_and_best_first() -> None:
    queries, keys = world()
    sims, idx = search.top_k(queries, keys, 5)
    want_sims, want_idx = brute_force(queries, keys, 5)
    np.testing.assert_array_equal(idx, want_idx)
    np.testing.assert_allclose(sims, want_sims, atol=1e-5)
    assert sims.dtype == np.float32 and idx.dtype == np.int64


def test_k_is_cut_to_the_number_of_keys_and_nothing_to_search_is_fine() -> None:
    queries, keys = world(n_keys=3)
    sims, idx = search.top_k(queries, keys, 10)
    assert sims.shape == idx.shape == (40, 3)
    assert search.top_k(queries[:0], keys, 2)[0].shape == (0, 2)
    assert search.top_k(queries, keys[:0], 2)[1].shape == (40, 0)


def test_the_gpu_is_used_for_big_jobs_and_the_cpu_takes_over_when_it_fails(
    monkeypatch, caplog
) -> None:
    queries, keys = world()
    calls = []

    def fake_gpu(q, kk, k):
        calls.append(len(q))
        return brute_force(q, kk, k)

    monkeypatch.setattr(search, "gpu_available", lambda: True)
    monkeypatch.setattr(search, "_torch_search", fake_gpu)

    monkeypatch.setattr(search, "GPU_MIN_WORK", 10**12)  # a small job stays on the CPU
    search.top_k(queries, keys, 5)
    assert calls == []

    monkeypatch.setattr(search, "GPU_MIN_WORK", 0)
    sims, idx = search.top_k(queries, keys, 5)
    assert calls == [40]
    np.testing.assert_array_equal(idx, brute_force(queries, keys, 5)[1])

    def broken_gpu(q, kk, k):
        raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(search, "_torch_search", broken_gpu)
    with caplog.at_level(logging.WARNING):
        sims, idx = search.top_k(queries, keys, 5)
    assert "GPU search failed" in caplog.text and "out of memory" in caplog.text
    np.testing.assert_array_equal(idx, brute_force(queries, keys, 5)[1])  # FAISS answered


def test_the_gpu_can_be_turned_off(monkeypatch) -> None:
    monkeypatch.setenv("UC_SEARCH", "cpu")
    search.gpu_available.cache_clear()
    try:
        assert search.gpu_available() is False
    finally:
        search.gpu_available.cache_clear()


def test_the_pytorch_search_gives_the_same_answer_block_by_block() -> None:
    """Run on PyTorch's CPU, in its own process: PyTorch and FAISS can crash each other on macOS
    if both are loaded in one. The blocks are forced small so several are used."""
    code = """
import numpy as np
from undercurrent.stories import search

def unit(x):
    return (x / np.linalg.norm(x, axis=1, keepdims=True)).astype(np.float32)

rng = np.random.default_rng(1)
queries, keys = unit(rng.standard_normal((700, 16))), unit(rng.standard_normal((900, 16)))
search.CHUNK_SCORES = 1  # one block is 256 queries: three blocks
sims, idx = search._torch_search(queries, keys, 6, "cpu")
scores = queries @ keys.T
order = np.argsort(-scores, axis=1, kind="stable")[:, :6]
assert (idx == order).all(), "indices differ"
assert np.allclose(sims, np.take_along_axis(scores, order, axis=1), atol=1e-5), "similarities differ"
assert sims.shape == (700, 6) and idx.dtype == np.int64
print("ok")
"""
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr[-800:]


@pytest.mark.parametrize("k", [1, 5])
def test_the_cpu_search_agrees_with_brute_force_on_identical_rows(k: int) -> None:
    """Duplicated keys are the usual case with wire stories: the answer must still be valid."""
    rng = np.random.default_rng(3)
    keys = unit(np.repeat(rng.standard_normal((20, 16)), 5, axis=0))
    sims, idx = search.top_k(keys, keys, k)
    np.testing.assert_allclose(sims, brute_force(keys, keys, k)[0], atol=1e-5)
    assert (idx >= 0).all() and (idx < len(keys)).all()
