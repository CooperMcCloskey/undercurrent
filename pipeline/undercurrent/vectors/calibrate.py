"""Measure a "same story" cutoff from labelled pairs.

    uv run python -m undercurrent.vectors.calibrate --pairs pairs.csv --a news --b news

pairs.csv has three columns: text_a, text_b, same (1 if the two are about the same story, else 0).
Label about 50 pairs per source pair, spread across easy and hard cases (include pairs about the
same kind of event in different places, which are the dangerous ones). The cutoff printed is the
lowest one whose precision (the share of pairs above it that really are the same story) is at
least --precision, which keeps as many true links as possible; paste it into thresholds.py.
"""

import argparse
import csv
from collections.abc import Callable
from pathlib import Path

import numpy as np

from undercurrent.vectors.embed import Source, embed


def pair_similarities(
    texts_a: list[str],
    texts_b: list[str],
    a: Source,
    b: Source,
    embedder: Callable[[list[str], Source], np.ndarray] = embed,
) -> np.ndarray:
    """Cosine similarity of each (texts_a[i], texts_b[i]), cleaned the way each source is."""
    va, vb = embedder(texts_a, a), embedder(texts_b, b)
    return np.einsum("ij,ij->i", va, vb)


def best_cutoff(
    sims: np.ndarray, same: np.ndarray, precision: float = 0.9, min_pairs: int = 10
) -> dict:
    """The lowest cutoff with precision >= `precision` among at least `min_pairs` pairs above it."""
    order = np.argsort(-sims)
    sims, same = sims[order], same[order].astype(bool)
    true_above = np.cumsum(same)
    n_above = np.arange(1, len(sims) + 1)
    precision_at = true_above / n_above
    ok = np.flatnonzero((precision_at >= precision) & (n_above >= min_pairs))
    if len(ok) == 0:
        return {"cutoff": None, "precision": None, "recall": None, "pairs": len(sims)}
    i = ok[-1]  # the most pairs we can include while staying precise
    cutoff = float(sims[i])
    return {
        "cutoff": round(cutoff, 3),
        "precision": float(precision_at[i]),
        "recall": float(true_above[i] / max(same.sum(), 1)),
        "pairs": len(sims),
    }


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--pairs", type=Path, required=True)
    p.add_argument("--a", default="news", help="source of text_a")
    p.add_argument("--b", default="news", help="source of text_b")
    p.add_argument("--precision", type=float, default=0.9)
    args = p.parse_args(argv)
    with open(args.pairs, newline="") as f:
        rows = list(csv.DictReader(f))
    sims = pair_similarities(
        [r["text_a"] for r in rows], [r["text_b"] for r in rows], args.a, args.b
    )
    same = np.array([int(r["same"]) for r in rows])
    result = best_cutoff(sims, same, args.precision)
    print(f"{len(rows)} pairs, {int(same.sum())} the same story; "
          f"mean similarity same {sims[same == 1].mean():.2f}, different {sims[same == 0].mean():.2f}")  # fmt: skip
    if result["cutoff"] is None:
        raise SystemExit(
            f"No cutoff reaches {args.precision:.0%} precision with 10+ pairs. Label more pairs."
        )
    print(
        f"cutoff {result['cutoff']}: precision {result['precision']:.2f}, recall {result['recall']:.2f}"
    )
    print(
        f'paste into thresholds.py:  ("{args.a}", "{args.b}"): {result["cutoff"]},  and add the pair to CALIBRATED'
    )


if __name__ == "__main__":
    main()
