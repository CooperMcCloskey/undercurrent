"""Group items into stories with a similarity graph and community detection.

1. Link every item to its k most similar items, keeping only links with cosine >= min_sim.
   This is the "compare the angle between vectors" step, as a sparse graph.
2. Run Louvain community detection on that graph: it finds groups of items that link to each
   other much more densely than to the rest.

Why not just take connected components of the graph? Components chain: A~B, B~C, C~D puts A and
D in one story even when they share nothing. Louvain maximises modularity, so two dense groups
joined by a single weak link stay separate stories.

Defaults were tuned on the demo items (undercurrent.demo) with the multilingual MiniLM model:
min_sim 0.40-0.45, k 8-10 and resolution 1.0-2.0 all recovered the 9 hand-labelled stories
exactly. min_sim is model-specific, so recalibrate it on real data (V's threshold() task).

Knobs: `min_sim` (lower = more cross-source links, more risk of merging), `k` (links per item),
`resolution` (> 1 splits into smaller, tighter stories), `min_size` (smaller groups are noise).
"""

import networkx as nx
import numpy as np


def knn_edges(vectors: np.ndarray, k: int, min_sim: float, block: int = 4096):
    """Yield (i, j, similarity) for each item's top-k neighbours above min_sim.

    Vectors must be L2-normalised. Works in blocks, so memory stays bounded; for very large
    days, swap this for a FAISS index.
    """
    n = len(vectors)
    k = min(k, n - 1)
    for start in range(0, n, block):
        sims = vectors[start : start + block] @ vectors.T
        for row, i in enumerate(range(start, min(start + block, n))):
            sims[row, i] = -1  # no self-links
            nbrs = np.argpartition(-sims[row], k)[:k] if k > 0 else []
            for j in nbrs:
                if sims[row, j] >= min_sim:
                    yield i, int(j), float(sims[row, j])


def similarity_graph(vectors: np.ndarray, k: int = 10, min_sim: float = 0.42) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(range(len(vectors)))
    for i, j, s in knn_edges(vectors, k, min_sim):
        graph.add_edge(i, j, weight=s)
    return graph


def cluster(
    vectors: np.ndarray,
    *,
    k: int = 10,
    min_sim: float = 0.42,
    resolution: float = 1.25,
    min_size: int = 3,
    seed: int = 0,
) -> np.ndarray:
    """Story label per item: 0, 1, 2... largest story first; -1 = noise (no story)."""
    graph = similarity_graph(vectors, k=k, min_sim=min_sim)
    communities = nx.community.louvain_communities(
        graph, weight="weight", resolution=resolution, seed=seed
    )
    labels = np.full(len(vectors), -1)
    stories = sorted((c for c in communities if len(c) >= min_size), key=len, reverse=True)
    for label, members in enumerate(stories):
        labels[list(members)] = label
    return labels


def centroid(vectors: np.ndarray) -> np.ndarray:
    """L2-normalised mean direction of a story's vectors."""
    mean = vectors.mean(axis=0)
    return mean / np.linalg.norm(mean)
