"""Measure a "same story" cutoff from labelled pairs.

From pairs made by sample_pairs.py (the normal route), labelled in one string, 1 = same story:

    uv run python -m undercurrent.vectors.calibrate --pairs pairs_unlabelled.csv --labels "1011 0010 ..."

or from a CSV whose `same` column is filled in (1 or 0; blank rows are skipped):

    uv run python -m undercurrent.vectors.calibrate --pairs pairs.csv --a news --b news

The CSV needs text_a and text_b, and optionally `sim` (the similarity measured when the pair was
sampled; without it the texts are embedded again, which needs the model) and `weight` (how many
real pairs each sampled pair stands for; sample_pairs.py sets it). The cutoff printed is the
lowest one whose precision (the share of pairs above it that really are the same story) is at
least --precision, which keeps as many true links as possible. Paste it into thresholds.py.
"""

import argparse
import csv
import re
from collections.abc import Callable
from pathlib import Path

import numpy as np

from undercurrent.vectors.embed import Source, embed

TABLE_CUTOFFS = np.arange(0.35, 0.851, 0.05)


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
    sims: np.ndarray,
    same: np.ndarray,
    precision: float = 0.9,
    min_pairs: int = 10,
    weights: np.ndarray | None = None,
) -> dict:
    """The lowest cutoff with precision >= `precision` among at least `min_pairs` pairs above it.
    With `weights`, precision and recall are weighted (a pair stands for `weight` real pairs)."""
    w = np.ones(len(sims)) if weights is None else np.asarray(weights, dtype=float)
    order = np.argsort(-sims, kind="stable")
    sims, same, w = sims[order], same[order].astype(bool), w[order]
    true_above = np.cumsum(w * same)
    precision_at = true_above / np.cumsum(w)
    n_above = np.arange(1, len(sims) + 1)
    # A cutoff keeps every pair at or above it, so only judge it at the end of a run of equal
    # similarities, never part-way through one.
    boundary = np.r_[sims[1:] != sims[:-1], True]
    ok = np.flatnonzero((precision_at >= precision) & (n_above >= min_pairs) & boundary)
    if len(ok) == 0:
        return {"cutoff": None, "precision": None, "recall": None, "pairs": len(sims)}
    i = ok[-1]  # the most pairs we can include while staying precise
    return {
        "cutoff": round(float(sims[i]), 3),
        "precision": float(precision_at[i]),
        "recall": float(true_above[i] / max((w * same).sum(), 1e-12)),
        "pairs": len(sims),
    }


def table(sims: np.ndarray, same: np.ndarray, weights: np.ndarray) -> list[dict]:
    """For a range of cutoffs: how many labelled pairs are above it, and the (weighted) precision
    and share of all true links kept."""
    positives = (weights * same).sum()
    rows = []
    for c in TABLE_CUTOFFS:
        above = sims >= c
        w_above = weights[above].sum()
        rows.append(
            {
                "cutoff": float(c),
                "pairs": int(above.sum()),
                "precision": float((weights[above] * same[above]).sum() / w_above) if w_above else float("nan"),
                "recall": float((weights[above] * same[above]).sum() / positives) if positives else float("nan"),
            }
        )  # fmt: skip
    return rows


def bootstrap_cutoff(
    sims: np.ndarray,
    same: np.ndarray,
    weights: np.ndarray,
    precision: float,
    n: int = 300,
    seed: int = 0,
) -> tuple[float, float] | None:
    """The 10th and 90th percentile of the cutoff over resamples of the labelled pairs: how much
    the answer would move with a different sample."""
    rng = np.random.default_rng(seed)
    cutoffs = []
    for _ in range(n):
        pick = rng.integers(0, len(sims), len(sims))
        found = best_cutoff(sims[pick], same[pick], precision, weights=weights[pick])["cutoff"]
        if found is not None:
            cutoffs.append(found)
    if len(cutoffs) < n // 2:
        return None
    return float(np.percentile(cutoffs, 10)), float(np.percentile(cutoffs, 90))


def parse_labels(text: str, expected: int) -> np.ndarray:
    """ "1011 0010 ..." -> an array of labels. Spaces and newlines are ignored; ? or - means
    "not labelled" (NaN). The length has to match the number of pairs: a missed digit would
    quietly shift every label after it."""
    digits = re.sub(r"\s+", "", text)
    if not re.fullmatch(r"[01?\-]*", digits):
        raise SystemExit("--labels may only contain 0, 1, ? and spaces")
    if len(digits) != expected:
        raise SystemExit(
            f"--labels has {len(digits)} labels but the file has {expected} pairs. "
            "Check for a missed or doubled digit."
        )
    return np.array([np.nan if ch in "?-" else float(ch) for ch in digits])


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--pairs", type=Path, required=True)
    p.add_argument("--labels", help='one label per pair, in file order: "1011 0010 ..."')
    p.add_argument("--a", default="news", help="source of text_a")
    p.add_argument("--b", default="news", help="source of text_b")
    p.add_argument("--precision", type=float, default=0.9)
    args = p.parse_args(argv)

    with open(args.pairs, newline="") as f:
        rows = list(csv.DictReader(f))
    if args.labels is not None:
        same_all = parse_labels(args.labels, len(rows))
    else:
        same_all = np.array(
            [float(r["same"]) if r.get("same", "").strip() else np.nan for r in rows]
        )
    labelled = ~np.isnan(same_all)
    rows = [r for r, ok in zip(rows, labelled, strict=True) if ok]
    same = same_all[labelled].astype(int)
    if len(rows) < 20:
        raise SystemExit(f"Only {len(rows)} labelled pairs: label at least 50 (more is better).")
    if "sim" in rows[0] and rows[0]["sim"]:
        sims = np.array([float(r["sim"]) for r in rows])
    else:
        sims = pair_similarities(
            [r["text_a"] for r in rows], [r["text_b"] for r in rows], args.a, args.b
        )
    weights = np.array([float(r.get("weight") or 1.0) for r in rows])

    print(f"{len(rows)} labelled pairs, {int(same.sum())} the same story")
    print(f"\n{'cutoff':>7} {'pairs above':>12} {'precision':>10} {'true links kept':>16}")
    for r in table(sims, same, weights):
        print(f"{r['cutoff']:>7.2f} {r['pairs']:>12} {r['precision']:>10.2f} {r['recall']:>16.2f}")
    print(
        "(precision and links kept are weighted to stand for all neighbouring pairs, not just the sample)"
    )

    chosen = None
    for target in (0.95, args.precision, 0.85):
        found = best_cutoff(sims, same, target, weights=weights)
        if found["cutoff"] is None:
            print(f"\nprecision {target:.0%}: no cutoff reaches it with 10+ pairs above")
            continue
        spread = bootstrap_cutoff(sims, same, weights, target)
        note = (
            f" (would likely land between {spread[0]:.2f} and {spread[1]:.2f} with another sample)"
            if spread
            else ""
        )
        print(
            f"\nprecision {target:.0%}: cutoff {found['cutoff']}, keeps {found['recall']:.0%} of true links{note}"
        )
        if target == args.precision:
            chosen = found
    if chosen is None:
        raise SystemExit(f"No cutoff reaches {args.precision:.0%} precision. Label more pairs.")
    print(
        f'\npaste into thresholds.py:  ("{args.a}", "{args.b}"): {chosen["cutoff"]},  and add the pair to CALIBRATED'
    )


if __name__ == "__main__":
    main()
