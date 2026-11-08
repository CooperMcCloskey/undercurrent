"""Which scripts does the embedding model fail to tell apart? Measured on the vectors already made.

    uv run python -m undercurrent.vectors.language_check --days 3

A sentence-embedding model only separates the languages it was trained on. For another one it gives
nearly every headline the same vector, so any two headlines in that language look like "the same
story", whatever they are about, and the tracker would glue them all into one giant story at any
cutoff. On real headlines from the pairs we labelled, two unrelated Malayalam headlines scored 0.94
on average (English: -0.01), and Armenian 0.72.

This reads the stored vectors (no model needed), groups the headlines by script, and for each script
measures how similar RANDOM pairs are. Random headlines are about different events, so a high mean
means the model cannot tell that script's headlines apart. It prints the scripts to leave out of
tracking. It works by script, not by language, so it can't see a Latin-script language the model
doesn't know (Swahili, Hausa...).
"""

import argparse
import datetime as dt
import unicodedata
from collections import Counter
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.vectors import store

# Mean cosine of random same-script pairs. English is about 0; unrelated pairs across languages
# about 0.2. Above UNRELIABLE the model can't separate the script at all: leave it out. Between
# the two, a headline still has some signal, but a low cutoff would glue stories together.
UNRELIABLE = 0.5
WEAK = 0.3
MIN_HEADLINES = 30  # a script with fewer headlines in the sample isn't judged
PAIRS_PER_SCRIPT = 4000


def script_of(text: str) -> str:
    """The script most of the letters in `text` are written in: 'LATIN', 'MALAYALAM', 'ARABIC',
    'CJK' (Chinese characters without kana), 'JAPANESE' (kana present), 'HANGUL'..., or 'NONE'."""
    counts: Counter[str] = Counter()
    for ch in text:
        if not ch.isalpha():
            continue
        try:
            counts[unicodedata.name(ch).split()[0]] += 1
        except ValueError:  # a character without a name
            continue
    if not counts:
        return "NONE"
    if counts["HIRAGANA"] or counts["KATAKANA"]:
        return "JAPANESE"
    return counts.most_common(1)[0][0]


def read_day(
    root: Path, source: str, day: dt.date, limit: int, rng: np.random.Generator
) -> tuple[list[str], np.ndarray]:
    """Up to `limit` random headlines of one day and their unit vectors. A day can hold 250,000
    headlines, so the vectors are memory-mapped and only the chosen rows are read."""
    npy, parquet = store.day_files(root, source, day)
    texts = pq.read_table(parquet).to_pydict()["text"]
    mapped = np.load(npy, mmap_mode="r")
    keep = np.sort(rng.permutation(len(texts))[:limit])
    vectors = np.asarray(mapped[keep], dtype=np.float32)
    vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)
    return [texts[i] for i in keep], vectors


def pick_days(done: list[dt.date], count: int) -> list[dt.date]:
    if count >= len(done):
        return done
    return [done[i] for i in sorted(set(np.linspace(0, len(done) - 1, count).round().astype(int)))]


def random_pair_similarity(vectors: np.ndarray, pairs: int, rng: np.random.Generator) -> dict:
    """Mean and 90th percentile cosine of random pairs among `vectors`."""
    n = len(vectors)
    a = rng.integers(0, n, pairs)
    b = rng.integers(0, n - 1, pairs)
    b = np.where(b >= a, b + 1, b)  # never a headline with itself
    sims = np.einsum("ij,ij->i", vectors[a], vectors[b])
    return {"mean": float(sims.mean()), "p90": float(np.percentile(sims, 90))}


def measure(
    texts: list[str],
    vectors: np.ndarray,
    rng: np.random.Generator,
    min_headlines: int = MIN_HEADLINES,
) -> list[dict]:
    """Per script (with enough headlines): how many, and how similar random pairs of them are."""
    scripts = np.array([script_of(t) for t in texts])
    out = []
    for name in sorted(
        {str(s) for s in scripts}
    ):  # plain str: numpy's own string type prints badly
        members = np.flatnonzero(scripts == name)
        if name == "NONE" or len(members) < min_headlines:
            continue
        sims = random_pair_similarity(vectors[members], PAIRS_PER_SCRIPT, rng)
        out.append({"script": name, "headlines": len(members), **sims})
    return sorted(out, key=lambda r: -r["mean"])


def verdict(mean: float) -> str:
    return "UNRELIABLE" if mean >= UNRELIABLE else "weak" if mean >= WEAK else "ok"


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--processed", type=Path, default=config.PROCESSED)
    p.add_argument("--source", default="news")
    p.add_argument("--days", type=int, default=3, help="how many embedded days to sample from")
    p.add_argument("--per-day", type=int, default=20000, help="headlines to read from each day")
    p.add_argument("--min-headlines", type=int, default=MIN_HEADLINES)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    root = store.vectors_dir(args.processed)
    done = store.done_days(root, args.source)
    if not done:
        raise SystemExit(f"No embedded {args.source} days under {root}.")
    days = pick_days(done, args.days)
    rng = np.random.default_rng(args.seed)

    texts: list[str] = []
    chunks: list[np.ndarray] = []
    for day in days:
        day_texts, day_vectors = read_day(root, args.source, day, args.per_day, rng)
        texts += day_texts
        chunks.append(day_vectors)
    vectors = np.concatenate(chunks)

    rows = measure(texts, vectors, rng, args.min_headlines)
    baseline = random_pair_similarity(vectors, 20000, rng)
    print(f"{len(texts):,} headlines from {len(days)} days ({days[0]} to {days[-1]})")
    print(f"random pairs of ANY two headlines: mean cosine {baseline['mean']:.2f}\n")
    print(
        f"{'script':<14} {'headlines':>10} {'mean cosine of random pairs':>29} {'90th pct':>9}  verdict"
    )
    for r in rows:
        print(
            f"{r['script']:<14} {r['headlines']:>10,} {r['mean']:>29.2f} {r['p90']:>9.2f}  {verdict(r['mean'])}"
        )
    bad = [r["script"] for r in rows if r["mean"] >= UNRELIABLE]
    weak = [r["script"] for r in rows if WEAK <= r["mean"] < UNRELIABLE]
    print(
        "\nUNRELIABLE (the model can't tell these headlines apart; any two look alike, so leave them "
        f"out of tracking): {', '.join(bad) if bad else 'none'}"
    )
    print(
        f"weak (some signal, but a low cutoff would glue stories together): {', '.join(weak) if weak else 'none'}"
    )
    if bad:
        print(f"\nUNRELIABLE_SCRIPTS = {bad!r}")


if __name__ == "__main__":
    main()
