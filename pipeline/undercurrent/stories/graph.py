"""Group items into stories with a similarity graph and community detection.

1. Link every item to its k most similar items, keeping only links with cosine >= min_sim.
   This is the "compare the angle between vectors" step, as a sparse graph.
2. Don't link items that are clearly about different places: both have locations, they share no
   country, and they're more than `geo_km` apart. Otherwise "central bank holds rates" in
   Frankfurt and in Buenos Aires become one story, as would every election, flood or outbreak
   that week. Multi-country stories still link, because an article's places include the
   countries it mentions (a Taiwanese article about US tariffs lists the US too). Items with no
   location (most social posts) link on text alone.
3. Run Louvain community detection on that graph: it finds groups of items that link to each
   other much more densely than to the rest.

Why not just take connected components of the graph? Components chain: A~B, B~C, C~D puts A and
D in one story even when they share nothing. Louvain maximises modularity, so two dense groups
joined by a single weak link stay separate stories.

Defaults were tuned on the demo items (uv run python -m undercurrent.demo) with the multilingual
MiniLM model: on 27 hand-labelled stories in 4+ languages, min_sim 0.42, k 6-10 and
resolution 3.0-5.0 recover all 27 (adjusted Rand index 0.95; without the location rule the best
is 0.80 with 23 stories). min_sim is model-specific, so recalibrate it on real data (V's threshold() task).

Knobs: `min_sim` (lower = more cross-source links, more risk of merging), `k` (links per item),
`resolution` (> 1 splits into smaller, tighter stories), `min_size` (smaller groups are noise),
`geo_km` (how far apart two places can be and still be "the same place").
"""

import math
from collections.abc import Callable, Iterator, Sequence

import networkx as nx
import numpy as np

Place = tuple[float, float, str | None]  # lat, lon, ISO 3166-1 alpha-2 country


def distance_km(a: Place, b: Place) -> float:
    """Great-circle distance (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def far_apart(a: Sequence[Place], b: Sequence[Place], geo_km: float) -> bool:
    """Both items have places, share no country, and every pair of places is > geo_km apart."""
    if not a or not b:
        return False
    if {p[2] for p in a if p[2]} & {p[2] for p in b if p[2]}:
        return False
    return min(distance_km(p, q) for p in a for q in b) > geo_km


def knn_edges(
    vectors: np.ndarray,
    k: int,
    min_sim: float,
    allowed: Callable[[int, int], bool] | None = None,
    block: int = 4096,
) -> Iterator[tuple[int, int, float]]:
    """Yield (i, j, similarity) for each item's k most similar *allowed* neighbours >= min_sim.

    Vectors must be L2-normalised. Looks at 4k candidates per item before filtering, so items
    whose nearest neighbours are all disallowed (far away) still find local ones. Works in
    blocks, so memory stays bounded; for very large days, swap this for a FAISS index.
    """
    n = len(vectors)
    candidates = min(4 * k, n - 1)
    if candidates <= 0:
        return
    for start in range(0, n, block):
        sims = vectors[start : start + block] @ vectors.T
        for row, i in enumerate(range(start, min(start + block, n))):
            sims[row, i] = -1  # no self-links
            top = np.argpartition(-sims[row], candidates - 1)[:candidates]
            kept = 0
            for j in top[np.argsort(-sims[row, top])]:
                if kept == k or sims[row, j] < min_sim:
                    break
                if allowed is None or allowed(i, int(j)):
                    kept += 1
                    yield i, int(j), float(sims[row, j])


def similarity_graph(
    vectors: np.ndarray,
    k: int = 10,
    min_sim: float = 0.42,
    places: Sequence[Sequence[Place]] | None = None,
    geo_km: float = 1000,
) -> nx.Graph:
    allowed = None
    if places is not None:
        allowed = lambda i, j: not far_apart(places[i], places[j], geo_km)  # noqa: E731
    graph = nx.Graph()
    graph.add_nodes_from(range(len(vectors)))
    for i, j, s in knn_edges(vectors, k, min_sim, allowed):
        graph.add_edge(i, j, weight=s)
    return graph


def cluster(
    vectors: np.ndarray,
    *,
    places: Sequence[Sequence[Place]] | None = None,
    k: int = 10,
    min_sim: float = 0.42,
    resolution: float = 3.5,
    min_size: int = 3,
    geo_km: float = 1000,
    seed: int = 0,
) -> np.ndarray:
    """Story label per item: 0, 1, 2... largest story first; -1 = noise (no story).

    `places`: per item, the (lat, lon, country) of every place it mentions (empty if unknown).
    """
    graph = similarity_graph(vectors, k=k, min_sim=min_sim, places=places, geo_km=geo_km)
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
