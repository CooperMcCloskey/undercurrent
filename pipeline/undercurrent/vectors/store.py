"""The vector store: one file of vectors per source and day, plus loading.

Layout (docs/WORKPLAN.md section 3.4), under data/processed/vectors/ and mirrored in the bucket:

    manifest.json                  {model, dim, text_version, dtype}
    <source>/<YYYY-MM-DD>.npy      float16 (n, dim), L2-normalised
    <source>/<YYYY-MM-DD>.parquet  id, text (the exact text that was embedded; same row order)

Run it one day at a time, resumably:

    uv run python -m undercurrent.vectors.store --source news --start 2026-01-01 --end 2026-01-31
    uv run python -m undercurrent.vectors.store --source news --days 7 --shard 0/4   # 1 of 4 machines

How it behaves:
- A day is done when both files exist. They are written under a temporary name and renamed (the
  .parquet last), so a killed run never leaves a half-written day. Finished days are skipped.
- Texts are cleaned (embed.clean_text), junk is dropped (clean.is_usable), and identical cleaned
  texts are embedded once, then copied to every id that has them. The previous few days' vectors
  are reused for texts they already contain (wire stories repeat across days).
- The manifest records the model, dimension and text-cleaning version. Writing with a different
  one is refused: vectors from different models can't be compared.
- Days are independent, so shard them across machines (--shard i/n).
"""

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from undercurrent import config
from undercurrent.vectors.clean import is_usable
from undercurrent.vectors.embed import TEXT_VERSION, Source, clean_text, embed_cleaned

log = logging.getLogger(__name__)

Embedder = Callable[[list[str]], np.ndarray]


def vectors_dir(processed: Path | None = None) -> Path:
    return (processed or config.PROCESSED) / "vectors"


# ---- manifest ----------------------------------------------------------------------------------


class ManifestMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class Manifest:
    model: str
    dim: int
    text_version: int
    dtype: str = "float16"


def read_manifest(root: Path) -> Manifest | None:
    path = root / "manifest.json"
    return Manifest(**json.loads(path.read_text())) if path.exists() else None


def ensure_manifest(root: Path, dim: int) -> Manifest:
    """Create the manifest on first use; afterwards refuse anything that doesn't match it."""
    want = Manifest(config.EMBEDDING_MODEL, dim, TEXT_VERSION)
    have = read_manifest(root)
    if have is None:
        root.mkdir(parents=True, exist_ok=True)
        _atomic(root / "manifest.json", lambda p: p.write_text(json.dumps(asdict(want), indent=2)))
        return want
    if (have.model, have.dim, have.text_version) != (want.model, want.dim, want.text_version):
        raise ManifestMismatch(
            f"The vectors in {root} were made with {have}, but this run would write {want}. "
            "Vectors from different models or text versions can't be compared. Either switch "
            "back (UC_EMBEDDING_MODEL, TEXT_VERSION), or delete that folder and everything built "
            "from it (stories, forecasts) and rebuild."
        )
    return have


# ---- files -------------------------------------------------------------------------------------


def day_files(root: Path, source: str, day: dt.date) -> tuple[Path, Path]:
    return root / source / f"{day:%Y-%m-%d}.npy", root / source / f"{day:%Y-%m-%d}.parquet"


def is_done(root: Path, source: str, day: dt.date) -> bool:
    return all(p.exists() for p in day_files(root, source, day))


def done_days(root: Path, source: str) -> list[dt.date]:
    folder = root / source
    if not folder.is_dir():
        return []
    return sorted(
        dt.date.fromisoformat(p.stem)
        for p in folder.glob("*.parquet")
        if is_done(root, source, dt.date.fromisoformat(p.stem))
    )


def _atomic(path: Path, write: Callable[[Path], None]) -> None:
    """Write to a temporary name next to `path`, then rename: readers never see half a file."""
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _save_npy(path: Path, array: np.ndarray) -> None:
    def write(tmp: Path) -> None:
        with open(tmp, "wb") as f:
            np.save(f, array)

    _atomic(path, write)


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ---- embedding one day -------------------------------------------------------------------------


@dataclass
class DayStats:
    day: dt.date
    skipped: bool = False
    n_in: int = 0
    n_dropped: int = 0  # boilerplate, too short, or a repeated id
    n_kept: int = 0
    n_unique: int = 0  # distinct cleaned texts
    n_cached: int = 0  # taken from the previous days' vectors
    n_embedded: int = 0  # run through the model
    seconds: float = 0.0

    def line(self) -> str:
        if self.skipped:
            return f"{self.day}  already done"
        return (
            f"{self.day}  {self.n_in:>8,} in  {self.n_kept:>8,} kept ({self.n_dropped:,} dropped)  "
            f"{self.n_unique:>8,} unique  {self.n_cached:>7,} cached  {self.n_embedded:>8,} "
            f"embedded  {self.seconds:6.1f}s"
        )


def _cached_vectors(
    root: Path, source: str, day: dt.date, lookback: int, wanted: set[str]
) -> dict[str, np.ndarray]:
    """Vectors of texts we already embedded on the previous `lookback` days, keyed by text hash."""
    found: dict[str, np.ndarray] = {}
    for k in range(1, lookback + 1):
        npy, parquet = day_files(root, source, day - dt.timedelta(days=k))
        if not (npy.exists() and parquet.exists()):
            continue
        hashes = [
            text_hash(t)
            for t in pq.read_table(parquet, columns=["text"]).column("text").to_pylist()
        ]
        hits = [i for i, h in enumerate(hashes) if h in wanted and h not in found]
        if hits:
            vectors = np.load(npy, mmap_mode="r")
            for i in hits:
                found[hashes[i]] = np.array(vectors[i])
    return found


def embed_day(
    source: Source,
    day: dt.date,
    ids: list[str],
    texts: list[str],
    *,
    root: Path,
    embedder: Embedder | None = None,
    batch_size: int = 256,
    lookback_days: int = 3,
    force: bool = False,
) -> DayStats:
    """Embed one day's texts and write them. No-op if the day is done (unless `force`).

    `embedder` takes already cleaned texts and returns unit-length float vectors; the default is
    the real model. Tests pass a fake."""
    started = time.time()
    stats = DayStats(day, n_in=len(ids))
    if not force and is_done(root, source, day):
        stats.skipped = True
        return stats
    if len(ids) != len(texts):
        raise ValueError("ids and texts must be the same length")

    kept_ids, kept_texts, seen_ids = [], [], set()
    for item_id, text in zip(ids, texts, strict=True):
        cleaned = clean_text(text or "", source)
        if item_id in seen_ids or not is_usable(cleaned, source):
            continue
        seen_ids.add(item_id)
        kept_ids.append(str(item_id))
        kept_texts.append(cleaned)
    stats.n_kept, stats.n_dropped = len(kept_ids), len(ids) - len(kept_ids)
    if not kept_ids:
        stats.seconds = time.time() - started
        return stats

    hashes = [text_hash(t) for t in kept_texts]
    first_index: dict[str, int] = {}
    for i, h in enumerate(hashes):
        first_index.setdefault(h, i)
    unique = list(first_index)
    stats.n_unique = len(unique)

    cached = _cached_vectors(root, source, day, lookback_days, set(unique))
    todo = [h for h in unique if h not in cached]
    stats.n_cached, stats.n_embedded = len(cached), len(todo)
    new: dict[str, np.ndarray] = {}
    if todo:
        embed = embedder or (lambda batch: embed_cleaned(batch, batch_size))
        fresh = np.asarray(embed([kept_texts[first_index[h]] for h in todo]), dtype=np.float32)
        if fresh.ndim != 2 or len(fresh) != len(todo) or not np.isfinite(fresh).all():
            raise RuntimeError("the embedder returned the wrong shape or non-finite values")
        new = {h: fresh[j].astype(np.float16) for j, h in enumerate(todo)}
    dim = len(next(iter(new.values()))) if new else len(next(iter(cached.values())))
    ensure_manifest(root, dim)

    row_of = {h: j for j, h in enumerate(unique)}
    matrix = np.stack([cached[h] if h in cached else new[h] for h in unique]).astype(np.float16)
    vectors = matrix[[row_of[h] for h in hashes]]

    (root / source).mkdir(parents=True, exist_ok=True)
    npy, parquet = day_files(root, source, day)
    table = pa.table({"id": kept_ids, "text": kept_texts})
    _save_npy(npy, vectors)
    _atomic(parquet, lambda p: pq.write_table(table, p))  # last: its presence means "done"
    stats.seconds = time.time() - started
    return stats


# ---- loading -----------------------------------------------------------------------------------


def load(
    source: Source,
    days: Iterable[dt.date],
    *,
    root: Path | None = None,
    missing: str = "error",
) -> tuple[list[str], np.ndarray]:
    """(ids, vectors) for these days, in order. Vectors are float32 and unit length.

    missing="error" refuses days that haven't been embedded (a silent gap would distort
    everything built on top); "skip" ignores them (for a trailing window at the start of the data)."""
    root = root or vectors_dir()
    ids: list[str] = []
    parts: list[np.ndarray] = []
    absent: list[dt.date] = []
    for day in days:
        npy, parquet = day_files(root, source, day)
        if not (npy.exists() and parquet.exists()):
            absent.append(day)
            continue
        ids.extend(pq.read_table(parquet, columns=["id"]).column("id").to_pylist())
        parts.append(np.load(npy))
    if absent and missing == "error":
        shown = ", ".join(str(d) for d in absent[:5]) + (" ..." if len(absent) > 5 else "")
        raise FileNotFoundError(
            f"No {source} vectors for {len(absent)} day(s): {shown}. Run "
            f"`python -m undercurrent.vectors.store --source {source} --start ... --end ...`, "
            'or pass missing="skip" if gaps are expected.'
        )
    if not parts:
        manifest = read_manifest(root)
        return [], np.zeros((0, manifest.dim if manifest else 0), dtype=np.float32)
    vectors = np.concatenate(parts).astype(np.float32)
    vectors /= np.maximum(
        np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12
    )  # undo fp16 rounding
    return ids, vectors


def load_day(
    source: Source, day: dt.date, *, root: Path | None = None
) -> tuple[list[str], np.ndarray]:
    return load(source, [day], root=root)


# ---- reading the texts to embed (A's and V's files) ---------------------------------------------


def parquet_source(processed: Path, name: str) -> str | None:
    """A read_parquet(...) expression for a table that is one file `name.parquet` or a folder
    `name/` of partitions; None if neither exists."""
    single, folder = processed / f"{name}.parquet", processed / name
    if single.exists():
        return f"read_parquet('{str(single).replace(chr(39), chr(39) * 2)}')"
    if folder.is_dir():
        path = str(folder / "**" / "*.parquet").replace("'", "''")
        return f"read_parquet('{path}', hive_partitioning = true, union_by_name = true)"
    return None


def day_inputs(source: Source, processed: Path, day: dt.date) -> tuple[list[str], list[str]] | None:
    """(ids, texts) to embed for one day, or None if that day has no input file yet.

    Everything this knows about other workstreams' files is here:
      news         articles (A, docs/WORKPLAN.md 3.1): id, title, and day (or published_at)
      bluesky      bluesky/<day>.parquet (A, 3.3): id, text
      wiki_events  wiki_events.parquet (V, 3.9): id, day, text
      wiki_new     wiki_new.parquet (V, 3.9): id, day, text
    """
    con = duckdb.connect()
    if source == "news":
        articles = parquet_source(processed, "articles")
        if articles is None:
            raise FileNotFoundError(f"No articles under {processed}: A writes articles.parquet.")
        folder = processed / "articles"
        if folder.is_dir() and not (folder / f"day={day:%Y-%m-%d}").is_dir():
            # Partitioned articles: this day hasn't been pulled yet. Returning no rows here would
            # write an empty day and mark it done, so embedding alongside the pull would lose it.
            return None
        columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {articles}").fetchall()]
        missing = [c for c in ("id", "title") if c not in columns]
        if missing or not ({"day", "published_at"} & set(columns)):
            raise ValueError(
                f"articles is missing {missing or ['day or published_at']} (has {columns}). "
                "The expected schema is docs/WORKPLAN.md 3.1; if it changed, update day_inputs() "
                "in vectors/store.py."
            )
        when = "day" if "day" in columns else "CAST(published_at AS DATE)"
        rows = con.execute(f"SELECT id, title FROM {articles} WHERE {when} = ?", [day]).fetchall()
    elif source == "bluesky":
        path = processed / "bluesky" / f"{day:%Y-%m-%d}.parquet"
        if not path.exists():
            return None
        rows = con.execute("SELECT id, text FROM read_parquet(?)", [str(path)]).fetchall()
    elif source in ("wiki_events", "wiki_new"):
        path = processed / f"{source}.parquet"
        if not path.exists():
            return None
        rows = con.execute(
            "SELECT id, text FROM read_parquet(?) WHERE CAST(day AS DATE) = ?", [str(path), day]
        ).fetchall()
    else:
        raise NotImplementedError(
            f"No input defined for source {source!r} (reddit is a stretch goal)"
        )
    return [str(r[0]) for r in rows], [r[1] or "" for r in rows]


# ---- command line ------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--source", choices=["news", "bluesky", "wiki_events", "wiki_new"], default="news"
    )
    p.add_argument("--start", type=dt.date.fromisoformat)
    p.add_argument("--end", type=dt.date.fromisoformat, help="inclusive")
    p.add_argument(
        "--days", type=int, help="instead of --start/--end: the last N days, ending yesterday"
    )
    p.add_argument("--processed", type=Path, default=config.PROCESSED)
    p.add_argument("--shard", default="0/1", help="i/n: only days where day number %% n == i")
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument(
        "--lookback-days", type=int, default=3, help="reuse vectors from this many days back"
    )
    p.add_argument("--device", help="cuda, mps or cpu (default: the best available)")
    p.add_argument("--force", action="store_true", help="redo days that are already done")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, embedder: Embedder | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if args.device:
        os.environ["UC_DEVICE"] = args.device
    if args.days:
        end = dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)
        start = end - dt.timedelta(days=args.days - 1)
    elif args.start and args.end:
        start, end = args.start, args.end
    else:
        raise SystemExit("Give --start and --end, or --days N")
    shard, shards = (int(x) for x in args.shard.split("/"))
    if not (0 <= shard < shards):
        raise SystemExit("--shard must look like i/n with 0 <= i < n")

    root = vectors_dir(args.processed)
    all_days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in all_days if d.toordinal() % shards == shard]
    print(
        f"{args.source}: {len(days)} day(s), {start} to {end}, shard {shard}/{shards}, into {root}"
    )
    totals = DayStats(end)
    for day in days:
        found = day_inputs(args.source, args.processed, day)
        if found is None:
            print(f"{day}  no input file")
            continue
        stats = embed_day(
            args.source, day, *found, root=root, embedder=embedder,
            batch_size=args.batch_size, lookback_days=args.lookback_days, force=args.force,
        )  # fmt: skip
        print(stats.line(), flush=True)
        for field in ("n_in", "n_dropped", "n_kept", "n_unique", "n_cached", "n_embedded"):
            setattr(totals, field, getattr(totals, field) + getattr(stats, field))
    print(
        f"done: {totals.n_kept:,} vectors kept of {totals.n_in:,}, {totals.n_embedded:,} run through "
        f"the model, {totals.n_cached:,} reused"
    )


if __name__ == "__main__":
    main()
