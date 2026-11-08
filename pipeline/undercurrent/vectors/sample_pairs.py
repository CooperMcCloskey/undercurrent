"""Sample headline pairs to label, for measuring the "same story" cutoff.

    uv run python -m undercurrent.vectors.sample_pairs --days 4 --out ~/pairs_unlabelled.csv --print

The cutoff that matters is the one the story tracker uses to link two headlines of the same day:
it draws an edge from each headline to its nearest neighbours above the cutoff. So this samples
exactly those pairs: random headlines and their 10 nearest neighbours that day. They are spread
across similarity bands, so the close calls are well covered, and every pair carries a weight that
undoes the over-sampling; calibrate.py uses the weights to estimate the real precision.

--print shows the pairs WITHOUT their similarity, numbered, so you can judge them from the text
alone (seeing the number pulls the judgement towards it). Then:

    uv run python -m undercurrent.vectors.calibrate --pairs ~/pairs_unlabelled.csv --labels "1011 0010 ..."

How to label: 1 if both headlines report the same specific event or story (the same incident,
decision, match or announcement), even in different words or languages. 0 if they are the same
kind of thing but a different event or place ("floods in Spain" vs "floods in Brazil"), or only
related. When unsure, 0: wrongly joining two stories is worse than splitting one.

Pairs below similarity 0.30 are not sampled, so the cutoff it can recommend is at least 0.30.
Pairs with identical text are dropped (a wire story repeated word for word tells you nothing).
"""

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.vectors import store

EDGES = np.r_[np.arange(0.30, 0.901, 0.05), 1.01]  # the similarity bands that get sampled
FIELDS = ["text_a", "text_b", "same", "sim", "weight", "day", "id_a", "id_b"]


def read_day(root: Path, source: str, day: dt.date) -> tuple[list[str], list[str], np.ndarray]:
    """(ids, texts, unit-length float32 vectors) of one embedded day."""
    npy, parquet = store.day_files(root, source, day)
    table = pq.read_table(parquet).to_pydict()
    vectors = np.load(npy).astype(np.float32)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return table["id"], table["text"], vectors


def nearest_pairs(
    vectors: np.ndarray, anchors: np.ndarray, k: int, chunk: int = 150
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The k nearest neighbours of each anchor among all rows, as unique unordered pairs
    (lo, hi, similarity). Chunked, so a day of 250,000 headlines needs about 1.5 GB at the peak: it
    shares a VM with the embedders."""
    n = len(vectors)
    k = min(k, n - 1)
    firsts, seconds, sims = [], [], []
    for start in range(0, len(anchors), chunk):
        rows = anchors[start : start + chunk]
        scores = vectors[rows] @ vectors.T
        scores[np.arange(len(rows)), rows] = -2.0  # not its own neighbour
        top = np.argpartition(scores, n - k, axis=1)[:, n - k :]  # the k largest, unordered
        firsts.append(np.repeat(rows, k))
        seconds.append(top.ravel())
        sims.append(np.take_along_axis(scores, top, axis=1).ravel())
    a, b, s = np.concatenate(firsts), np.concatenate(seconds), np.concatenate(sims)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    _, first = np.unique(lo.astype(np.int64) * n + hi, return_index=True)
    return lo[first], hi[first], s[first]


def sample_by_similarity(
    sims: np.ndarray, per_bin: int, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Up to per_bin pairs from each similarity band: (chosen indices, weights). A pair's weight
    is how many pairs its band holds per pair sampled from it, so weighted counts estimate the
    counts among all the neighbour pairs."""
    band = np.digitize(sims, EDGES) - 1
    chosen, weights = [], []
    for b in range(len(EDGES) - 1):
        members = np.flatnonzero(band == b)
        if len(members) == 0:
            continue
        take = min(per_bin, len(members))
        chosen.append(rng.choice(members, take, replace=False))
        weights.append(np.full(take, len(members) / take))
    if not chosen:
        return np.zeros(0, dtype=int), np.zeros(0)
    return np.concatenate(chosen), np.concatenate(weights)


def pick_days(
    root: Path, source: str, count: int, start: dt.date | None, end: dt.date | None
) -> list[dt.date]:
    """`count` embedded days spread evenly over what is available (or all of them if fewer)."""
    days = [
        d
        for d in store.done_days(root, source)
        if (start is None or d >= start) and (end is None or d <= end)
    ]
    if not days:
        raise SystemExit(f"No embedded {source} days under {root} in that range.")
    if count >= len(days):
        return days
    return [days[i] for i in sorted(set(np.linspace(0, len(days) - 1, count).round().astype(int)))]


def build_sample(
    root: Path,
    source: str,
    days: list[dt.date],
    *,
    anchors: int,
    per_bin: int,
    k: int,
    seed: int,
) -> list[dict]:
    """The sampled pairs as rows for the CSV, in random order."""
    rng = np.random.default_rng(seed)
    pool: list[tuple] = []  # (day, id_a, id_b, text_a, text_b, sim)
    for day in days:
        ids, texts, vectors = read_day(root, source, day)
        if len(ids) < 3:
            continue
        chosen = rng.choice(len(ids), min(anchors, len(ids)), replace=False)
        lo, hi, sims = nearest_pairs(vectors, chosen, k)
        for a, b, s in zip(lo.tolist(), hi.tolist(), sims.tolist(), strict=True):
            if s >= EDGES[0] and texts[a].strip().lower() != texts[b].strip().lower():
                pool.append((day, ids[a], ids[b], texts[a], texts[b], s))
    picked, weights = sample_by_similarity(np.array([p[5] for p in pool]), per_bin, rng)
    order = rng.permutation(len(picked))  # shuffled: the order must not reveal the similarity
    rows = []
    for i in order:
        day, id_a, id_b, text_a, text_b, sim = pool[picked[i]]
        rows.append(
            {
                "text_a": text_a, "text_b": text_b, "same": "", "sim": f"{sim:.4f}",
                "weight": f"{weights[i]:.3f}", "day": day.isoformat(), "id_a": id_a, "id_b": id_b,
            }
        )  # fmt: skip
    return rows


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--processed", type=Path, default=config.PROCESSED)
    p.add_argument("--source", default="news")
    p.add_argument("--days", type=int, default=4, help="how many embedded days to sample from")
    p.add_argument("--start", type=dt.date.fromisoformat, help="earliest day to consider")
    p.add_argument("--end", type=dt.date.fromisoformat, help="latest day to consider")
    p.add_argument("--anchors", type=int, default=1500, help="random headlines per day")
    p.add_argument("--per-bin", type=int, default=12, help="pairs to label per similarity band")
    p.add_argument("--k", type=int, default=10, help="neighbours per headline (the tracker's knn)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--out", type=Path, default=Path("pairs_unlabelled.csv"))
    p.add_argument("--print", action="store_true", help="print the numbered pairs, without scores")
    args = p.parse_args(argv)

    root = store.vectors_dir(args.processed)
    days = pick_days(root, args.source, args.days, args.start, args.end)
    rows = build_sample(
        root,
        args.source,
        days,
        anchors=args.anchors,
        per_bin=args.per_bin,
        k=args.k,
        seed=args.seed,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"{len(rows)} pairs from {len(days)} days ({days[0]} to {days[-1]}) -> {args.out}",
        file=sys.stderr,
    )
    if args.print:
        print(f"# {len(rows)} pairs. Label each 1 (same story) or 0, from the text alone.")
        for i, row in enumerate(rows, 1):
            print(f"{i}. {row['text_a']}\n   || {row['text_b']}")


if __name__ == "__main__":
    main()
