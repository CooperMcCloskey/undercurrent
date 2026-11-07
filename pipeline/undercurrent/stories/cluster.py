"""Cluster items into stories at the size of a real day (hundreds of thousands of items).

graph.py (a kNN graph and Louvain in networkx) is the right idea for a few hundred items, but its
similarity matrix and its Python Louvain don't scale. This keeps the idea (link every item to its
nearest neighbours above a cutoff, never across clearly different places) and changes how:

1. Neighbours come from FAISS, exact and brute force (100k items x 384 dims take ~7 seconds).
2. The link graph is cut into connected components (scipy; about a second).
3. A component can still be several stories chained together (A~B, B~C, C~D joins things that
   share nothing), so every component with room for two stories is tried with Louvain. The split
   is only kept if it makes the groups clearly tighter (see _tighter): Louvain also cuts a single
   coherent story into pieces, because a nearest-neighbour graph of one story looks like a dense
   random graph. Components are small, so this is fast; a giant component is first cut down by
   raising the cutoff in steps.

stories/tracker.py calls this on the items that matched no existing story.
"""

import sys
from collections.abc import Sequence

import networkx as nx
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from undercurrent.stories.graph import Place, far_apart


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


def knn(vectors: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Exact k nearest neighbours of every row among the other rows: (similarities, indices),
    both (n, k), best first. `vectors` must be unit length."""
    faiss = get_faiss()
    n = len(vectors)
    k = min(k, n - 1)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(np.ascontiguousarray(vectors, dtype=np.float32))
    sims, idx = index.search(np.ascontiguousarray(vectors, dtype=np.float32), k + 1)
    # Drop each row's own entry. It is normally first, but with many identical vectors another
    # row can rank above it, or it can fall off the end: a stable sort on "is self" handles both.
    is_self = idx == np.arange(n)[:, None]
    keep = np.argsort(is_self, axis=1, kind="stable")[:, :k]
    return np.take_along_axis(sims, keep, axis=1), np.take_along_axis(idx, keep, axis=1)


def _geo_ok(
    rows: np.ndarray, cols: np.ndarray, places: Sequence[Sequence[Place]], geo_km: float
) -> np.ndarray:
    """graph.far_apart for every edge, with a fast path: items with no places, or whose main
    place is in the same country, can't be blocked, so only the rest need the full check."""
    has = np.fromiter((len(p) > 0 for p in places), dtype=bool, count=len(places))
    codes: dict[str, int] = {}
    primary = np.fromiter(
        (codes.setdefault(p[0][2], len(codes)) if p and p[0][2] else -1 for p in places),
        dtype=np.int64,
        count=len(places),
    )
    need = has[rows] & has[cols] & ((primary[rows] != primary[cols]) | (primary[rows] < 0))
    ok = np.ones(len(rows), dtype=bool)
    for t in np.flatnonzero(need):
        ok[t] = not far_apart(places[rows[t]], places[cols[t]], geo_km)
    return ok


def _components(
    nodes: np.ndarray, ei: np.ndarray, ej: np.ndarray, es: np.ndarray, min_size: int
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Connected components of the graph on `nodes` (sorted global ids) with these edges, as
    (nodes, ei, ej, es) per component of at least min_size nodes."""
    m = len(nodes)
    li, lj = np.searchsorted(nodes, ei), np.searchsorted(nodes, ej)
    graph = coo_matrix((np.ones(len(li), dtype=np.int8), (li, lj)), shape=(m, m))
    labels = connected_components(graph, directed=False)[1]
    sizes = np.bincount(labels)
    node_order = np.argsort(labels, kind="stable")  # stable: nodes stay sorted within a component
    node_start = np.r_[0, np.cumsum(sizes)]
    edge_labels = labels[li]
    edge_order = np.argsort(edge_labels, kind="stable")
    edge_start = np.r_[0, np.cumsum(np.bincount(edge_labels, minlength=len(sizes)))]
    out = []
    for c in np.flatnonzero(sizes >= min_size):
        sel = edge_order[edge_start[c] : edge_start[c + 1]]
        out.append(
            (nodes[node_order[node_start[c] : node_start[c + 1]]], ei[sel], ej[sel], es[sel])
        )
    return out


def _louvain(
    nodes: np.ndarray, ei: np.ndarray, ej: np.ndarray, es: np.ndarray, resolution: float, seed: int
) -> list[np.ndarray]:
    graph = nx.Graph()
    graph.add_nodes_from(nodes.tolist())
    graph.add_weighted_edges_from(zip(ei.tolist(), ej.tolist(), es.tolist(), strict=True))
    communities = nx.community.louvain_communities(
        graph, weight="weight", resolution=resolution, seed=seed
    )
    return [np.array(sorted(c)) for c in communities]


def coherence(vectors: np.ndarray) -> float:
    """How tightly a group points the same way: the average cosine to its own centroid (the length
    of the mean of unit vectors). 1 = identical; two unrelated stories mixed give about 0.7."""
    return float(np.linalg.norm(vectors.mean(axis=0)))


def _tighter(
    vectors: np.ndarray, nodes: np.ndarray, parts: list[np.ndarray], min_size: int, min_gain: float
) -> list[np.ndarray] | None:
    """The parts that are worth keeping instead of `nodes` as one story, or None to keep it whole.
    A split is worth it if the size-weighted coherence of its parts beats the whole's by min_gain."""
    kept = [part for part in parts if len(part) >= min_size]
    if len(kept) < 2:
        return None
    within = sum(len(part) * coherence(vectors[part]) for part in kept) / sum(
        len(part) for part in kept
    )
    return kept if within - coherence(vectors[nodes]) >= min_gain else None


def cluster_items(
    vectors: np.ndarray,
    places: Sequence[Sequence[Place]] | None = None,
    *,
    k: int = 10,
    min_sim: float = 0.42,
    min_size: int = 3,
    max_component: int = 300,
    geo_km: float = 1000.0,
    resolution: float = 1.0,
    step: float = 0.04,
    rounds: int = 3,
    louvain_min: int | None = None,
    min_gain: float = 0.05,
    seed: int = 0,
) -> np.ndarray:
    """Story label per item: 0, 1, 2... largest first; -1 = no story (too few items).

    Each item links to its `k` nearest neighbours with cosine >= min_sim (and not clearly
    elsewhere: see graph.py). Components with at least `louvain_min` items (default 2 * min_size,
    the smallest that could hold two stories) are split by Louvain. A component bigger than
    `max_component` is first re-cut with the cutoff raised by `step`, up to `rounds` times, which
    is much cheaper than Louvain on thousands of items. A Louvain split is kept only if it raises
    the groups' coherence by `min_gain` (see _tighter). `resolution` 1.0 is plain modularity:
    graph.py used 3.5 on one big graph, but each component is a much smaller graph, and
    modularity's scale follows the graph's size. `places`: per item, the (lat, lon, country) of
    every place it mentions (empty if unknown)."""
    louvain_min = louvain_min or 2 * min_size
    n = len(vectors)
    labels = np.full(n, -1, dtype=np.int64)
    if n < max(min_size, 2):
        return labels
    sims, idx = knn(vectors, k)
    rows = np.repeat(np.arange(n), idx.shape[1])
    cols, s = idx.ravel(), sims.ravel()
    keep = s >= min_sim
    rows, cols, s = rows[keep], cols[keep], s[keep]
    if places is not None and len(rows):
        ok = _geo_ok(rows, cols, places, geo_km)
        rows, cols, s = rows[ok], cols[ok], s[ok]
    if len(rows) == 0:
        return labels

    groups: list[np.ndarray] = []
    stack = [(*part, 0) for part in _components(np.arange(n), rows, cols, s, min_size)]
    while stack:
        nodes, ei, ej, es, level = stack.pop()
        if len(nodes) > max_component and level < rounds:
            stricter = es >= min_sim + step * (level + 1)
            parts = _components(nodes, ei[stricter], ej[stricter], es[stricter], min_size)
            stack.extend((*part, level + 1) for part in parts)
        elif len(nodes) >= louvain_min:
            parts = _louvain(nodes, ei, ej, es, resolution, seed)
            groups.extend(_tighter(vectors, nodes, parts, min_size, min_gain) or [nodes])
        else:
            groups.append(nodes)
    for label, members in enumerate(sorted(groups, key=lambda g: (-len(g), g[0]))):
        labels[members] = label
    return labels
