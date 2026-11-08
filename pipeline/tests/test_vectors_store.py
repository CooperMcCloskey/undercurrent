"""The vector store: one file per day, resumable, deduplicated, never silently mixed."""

import datetime as dt
import hashlib
import importlib
import logging
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent import config
from undercurrent.vectors import store, thresholds
from undercurrent.vectors.calibrate import best_cutoff
from undercurrent.vectors.clean import is_usable
from undercurrent.vectors.embed import clean_text, embed, embed_cleaned

# The package re-exports the function `embed`, which hides the module of the same name.
embed_module = importlib.import_module("undercurrent.vectors.embed")

DIM = 16
D1, D2, D3 = dt.date(2026, 1, 1), dt.date(2026, 1, 2), dt.date(2026, 1, 3)


class FakeEmbedder:
    """Deterministic unit vectors from the text, and a record of what it was asked to embed."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> np.ndarray:
        self.calls.append(list(texts))
        out = []
        for t in texts:
            seed = int(hashlib.sha1(t.encode()).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).standard_normal(DIM)
            out.append(v / np.linalg.norm(v))
        return np.array(out, dtype=np.float32)

    @property
    def embedded(self) -> list[str]:
        return [t for call in self.calls for t in call]


HEADLINES = [
    "Dockworkers walk off the job at Rotterdam terminal",
    "Central bank holds interest rates despite inflation",
    "Glacial lake flood warning issued for Himalayan villages",
]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "vectors"


def test_a_day_is_written_and_loads_back(root: Path) -> None:
    fake = FakeEmbedder()
    ids = ["a", "b", "c"]
    stats = store.embed_day("news", D1, ids, HEADLINES, root=root, embedder=fake)
    assert (stats.n_in, stats.n_kept, stats.n_embedded) == (3, 3, 3) and not stats.skipped

    npy, parquet = store.day_files(root, "news", D1)
    assert np.load(npy).dtype == np.float16 and np.load(npy).shape == (3, DIM)
    table = pq.read_table(parquet).to_pydict()
    assert table["id"] == ids and table["text"] == HEADLINES  # the exact text that was embedded
    manifest = store.read_manifest(root)
    assert (manifest.dim, manifest.text_version, manifest.dtype) == (
        DIM,
        embed_module.TEXT_VERSION,
        "float16",
    )

    got_ids, vectors = store.load("news", [D1], root=root)
    assert got_ids == ids and vectors.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-6)  # unit length again
    np.testing.assert_allclose(vectors, fake(HEADLINES), atol=2e-3)  # fp16 rounding only


def test_identical_texts_are_embedded_once_but_every_id_gets_a_row(root: Path) -> None:
    fake = FakeEmbedder()
    texts = [
        "Dockworkers walk off the job at Rotterdam terminal - BBC News",
        "Dockworkers walk off the job at Rotterdam terminal | Reuters",  # same once cleaned
        "Dockworkers walk off the job at Rotterdam terminal",
        "Central bank holds interest rates despite inflation",
    ]
    stats = store.embed_day("news", D1, ["a", "b", "c", "d"], texts, root=root, embedder=fake)
    assert stats.n_kept == 4 and stats.n_unique == 2 and stats.n_embedded == 2
    assert len(fake.embedded) == 2
    ids, vectors = store.load("news", [D1], root=root)
    assert ids == ["a", "b", "c", "d"] and len(vectors) == 4
    np.testing.assert_allclose(vectors[0], vectors[1], atol=1e-6)


def test_junk_and_repeated_ids_are_dropped(root: Path) -> None:
    texts = ["Home", "404 Not Found", "Too short", HEADLINES[0], HEADLINES[1], HEADLINES[2], None]
    ids = ["h", "e", "s", "ok1", "ok2", "ok1", "n"]  # "ok1" appears twice
    stats = store.embed_day("news", D1, ids, texts, root=root, embedder=FakeEmbedder())
    assert stats.n_in == 7 and stats.n_kept == 2 and stats.n_dropped == 5
    assert store.load("news", [D1], root=root)[0] == ["ok1", "ok2"]


def test_finished_days_are_skipped_and_force_redoes_them(root: Path) -> None:
    fake = FakeEmbedder()
    store.embed_day("news", D1, ["a"], HEADLINES[:1], root=root, embedder=fake)
    again = store.embed_day("news", D1, ["a"], HEADLINES[:1], root=root, embedder=fake)
    assert again.skipped and len(fake.calls) == 1
    forced = store.embed_day("news", D1, ["a"], HEADLINES[:1], root=root, embedder=fake, force=True)
    assert not forced.skipped and len(fake.calls) == 2


def test_texts_seen_on_earlier_days_are_reused(root: Path) -> None:
    fake = FakeEmbedder()
    store.embed_day("news", D1, ["a", "b"], HEADLINES[:2], root=root, embedder=fake)
    stats = store.embed_day(
        "news", D2, ["c", "d"], [HEADLINES[1], HEADLINES[2]], root=root, embedder=fake
    )
    assert (stats.n_cached, stats.n_embedded) == (1, 1)
    assert fake.calls[-1] == [HEADLINES[2]]  # only the new headline went to the model
    _, v1 = store.load("news", [D1], root=root)
    _, v2 = store.load("news", [D2], root=root)
    np.testing.assert_allclose(v1[1], v2[0], atol=1e-6)  # and the reused vector is the same one
    # Too old to be looked up: three days back is the limit.
    far = dt.date(2026, 1, 10)
    assert (
        store.embed_day("news", far, ["e"], [HEADLINES[1]], root=root, embedder=fake).n_cached == 0
    )


def test_a_crash_leaves_no_half_written_day(root: Path) -> None:
    class Boom(FakeEmbedder):
        def __call__(self, texts):
            raise RuntimeError("out of memory")

    with pytest.raises(RuntimeError):
        store.embed_day("news", D1, ["a"], HEADLINES[:1], root=root, embedder=Boom())
    assert not store.is_done(root, "news", D1)
    # A stale temporary file from a killed run doesn't count as a finished day either.
    (root / "news").mkdir(parents=True, exist_ok=True)
    (root / "news" / "2026-01-01.npy.tmp123").write_bytes(b"junk")
    assert store.done_days(root, "news") == []
    store.embed_day("news", D1, ["a"], HEADLINES[:1], root=root, embedder=FakeEmbedder())
    assert store.done_days(root, "news") == [D1]


def test_mixing_models_in_one_store_is_refused(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store.embed_day("news", D1, ["a"], HEADLINES[:1], root=root, embedder=FakeEmbedder())
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "some/other-model")
    with pytest.raises(store.ManifestMismatch, match="can't be compared"):
        store.embed_day("news", D2, ["a"], HEADLINES[:1], root=root, embedder=FakeEmbedder())
    assert not store.is_done(root, "news", D2)


def test_loading_several_days_and_missing_ones(root: Path) -> None:
    fake = FakeEmbedder()
    store.embed_day("news", D1, ["a", "b"], HEADLINES[:2], root=root, embedder=fake)
    store.embed_day("news", D3, ["c"], HEADLINES[2:], root=root, embedder=fake)
    ids, vectors = store.load("news", [D1, D3], root=root)
    assert ids == ["a", "b", "c"] and vectors.shape == (3, DIM)
    with pytest.raises(FileNotFoundError, match="2026-01-02"):
        store.load("news", [D1, D2, D3], root=root)
    assert store.load("news", [D1, D2, D3], root=root, missing="skip")[0] == ["a", "b", "c"]
    empty_ids, empty = store.load("news", [D2], root=root, missing="skip")
    assert empty_ids == [] and empty.shape == (0, DIM)


def test_cleaning_twice_is_not_safe_so_the_store_embeds_cleaned_text() -> None:
    once = clean_text("Strike talks - Dockers debate - CNN", "news")
    assert clean_text(once, "news") != once  # a second pass would eat "Dockers debate"
    assert once == "Strike talks - Dockers debate"


def test_embed_is_clean_then_embed_cleaned(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class FakeModel:
        def encode(self, texts, **kwargs):
            seen.append(list(texts))
            return np.ones((len(texts), 4), dtype=np.float32) / 2

    monkeypatch.setattr(embed_module, "_model", lambda: FakeModel())
    embed(["Headline here today - BBC News"], "news")
    embed_cleaned(["Headline here today"])
    assert seen[0] == seen[1] == ["Headline here today"]
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    embed_cleaned(["abc def"])
    assert seen[2] == ["query: abc def"]  # e5 models need their prefix


def test_which_texts_are_worth_embedding() -> None:
    assert is_usable("Dockworkers walk off the job at Rotterdam terminal", "news")
    assert not is_usable("Home", "news") and not is_usable("", "news")
    assert not is_usable("Subscribe to read", "news") and not is_usable(
        "Error 404 - Not Found", "news"
    )
    assert is_usable(
        "400 killed in Nigeria attack", "news"
    )  # a headline that starts like an error code
    assert not is_usable("Three words only", "news")
    assert is_usable("东京发生强烈地震造成多人受伤", "news")  # no spaces, but plenty to say
    assert not is_usable("so true lol same", "bluesky") and is_usable(
        "this strike is about to shut every port in europe", "bluesky"
    )


def test_cutoffs(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    thresholds._warned.clear()
    with caplog.at_level(logging.WARNING):
        assert thresholds.threshold("news", "news") == 0.80  # measured, so no warning
        assert thresholds.threshold("news", "bluesky") == thresholds.threshold("bluesky", "news")
        thresholds.threshold("news", "bluesky")
    # a pair nobody has measured is still a placeholder, and says so, once
    assert "PLACEHOLDER" in caplog.text and caplog.text.count("news vs bluesky") == 1
    assert "news vs news" not in caplog.text
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "some/other-model")
    with pytest.raises(KeyError, match="calibrate"):
        thresholds.threshold("news", "news")


def test_calibration_picks_the_loosest_cutoff_that_stays_precise() -> None:
    rng = np.random.default_rng(0)
    same = np.r_[np.ones(100), np.zeros(100)].astype(int)
    sims = np.r_[rng.normal(0.65, 0.1, 100), rng.normal(0.25, 0.1, 100)]
    result = best_cutoff(sims, same, precision=0.9)
    assert 0.3 < result["cutoff"] < 0.6
    assert result["precision"] >= 0.9 and result["recall"] > 0.8
    assert (
        best_cutoff(np.array([0.5] * 5), np.array([0] * 5))["cutoff"] is None
    )  # too little to say


def _write_articles(processed: Path, per_day: dict[dt.date, list[tuple[str, str]]]) -> None:
    rows = [(i, t, d) for d, items in per_day.items() for i, t in items]
    table = pa.table(
        {"id": [r[0] for r in rows], "title": [r[1] for r in rows], "day": [r[2] for r in rows]},
        schema=pa.schema([("id", pa.string()), ("title", pa.string()), ("day", pa.date32())]),
    )
    pq.write_table(table, processed / "articles.parquet")


def test_command_line_over_a_range_of_days(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    _write_articles(
        tmp_path,
        {
            D1: [("a", HEADLINES[0]), ("b", HEADLINES[1])],
            D2: [("c", HEADLINES[2])],
            D3: [("d", HEADLINES[0])],
        },
    )
    fake = FakeEmbedder()
    args = [
        "--source",
        "news",
        "--start",
        "2026-01-01",
        "--end",
        "2026-01-03",
        "--processed",
        str(tmp_path),
    ]
    store.main(args, embedder=fake)
    root = store.vectors_dir(tmp_path)
    assert store.done_days(root, "news") == [D1, D2, D3]
    assert "done: 4 vectors kept of 4" in capsys.readouterr().out
    assert len(fake.embedded) == 3  # day 3's headline repeats day 1's: reused, not embedded again

    store.main(args, embedder=fake)  # a second run has nothing left to do
    assert capsys.readouterr().out.count("already done") == 3 and len(fake.embedded) == 3

    # Sharding: two machines split the days between them.
    other = tmp_path / "other"
    other.mkdir()
    _write_articles(
        other, {D1: [("a", HEADLINES[0])], D2: [("c", HEADLINES[2])], D3: [("d", HEADLINES[1])]}
    )
    for shard in ("0/2", "1/2"):
        store.main([*args[:-1], str(other), "--shard", shard], embedder=FakeEmbedder())
    assert store.done_days(store.vectors_dir(other), "news") == [D1, D2, D3]


def test_the_command_line_names_the_problem_when_a_schema_changes(tmp_path: Path) -> None:
    pq.write_table(
        pa.table({"id": ["a"], "headline": ["x"], "day": [D1]}), tmp_path / "articles.parquet"
    )
    with pytest.raises(ValueError, match=r"missing \['title'\].*day_inputs"):
        store.day_inputs("news", tmp_path, D1)
    assert store.day_inputs("bluesky", tmp_path, D1) is None  # no input file that day: not an error
