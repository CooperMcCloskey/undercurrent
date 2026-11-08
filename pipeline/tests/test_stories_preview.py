"""The story reader prints sizes and real headlines for a tracked day."""

import datetime as dt
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent.stories import preview, track
from undercurrent.vectors import store

DAY = dt.date(2026, 11, 2)


def embedder(texts: list[str]) -> np.ndarray:
    vectors = np.random.default_rng(0).standard_normal((len(texts), 8)).astype(np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def write(processed: Path, folder: str, when: dt.date, **columns) -> None:
    path = processed / folder / f"{when:%Y-%m-%d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pydict(columns, schema=track.SCHEMAS[folder]), path)


@pytest.fixture
def tracked(tmp_path: Path) -> Path:
    """Day 1: 12 flood headlines (one story), 5 match reports (another), 3 left over."""
    ids = [f"a{i}" for i in range(20)]
    texts = [f"Floods in town {i} leave roads closed" for i in range(12)]
    texts += [f"Rovers beat United in match report {i}" for i in range(5)]
    texts += [f"Unrelated local notice number {i} today" for i in range(3)]
    store.embed_day("news", DAY, ids, texts, root=store.vectors_dir(tmp_path), embedder=embedder)
    story = ["floods"] * 12 + ["match"] * 5
    write(
        tmp_path, track.ASSIGNMENTS, DAY,
        article_id=ids[:17], story_id=story, day=[DAY] * 17, similarity=[0.9] * 17,
    )  # fmt: skip
    write(
        tmp_path, track.DAILY, DAY,
        story_id=["floods", "match"], day=[DAY] * 2, n_articles_day=[12, 5],
        first_seen=[DAY - dt.timedelta(days=3), DAY], label=["Floods in town 0 leave roads closed", "Rovers beat United"],
        category=["disaster", None],
    )  # fmt: skip
    return tmp_path


def test_it_prints_the_sizes_and_headlines_of_the_largest_stories(tracked: Path, capsys) -> None:
    preview.main(
        ["--processed", str(tracked), "--day", "2026-11-02", "--largest", "1", "--random", "1"]
    )
    out = capsys.readouterr().out
    assert "20 headlines, 17 (85%) in 2 stories today" in out
    assert "largest 12" in out
    assert "[12 headlines today, 3 days old, disaster, mean similarity 0.90]" in out
    assert out.count("Floods in town") >= 5  # the label and the sampled headlines
    assert "[5 headlines today, 0 days old, no category" in out  # the random pick
    assert "Rovers beat United in match report" in out
    assert "Unrelated local notice" not in out  # not in any story


def test_the_last_tracked_day_is_the_default_and_an_untracked_day_is_refused(
    tracked: Path, capsys
) -> None:
    preview.main(["--processed", str(tracked), "--largest", "0", "--random", "0"])
    assert "2026-11-02: 20 headlines" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="has not been tracked"):
        preview.main(["--processed", str(tracked), "--day", "2026-11-03"])
    with pytest.raises(SystemExit, match="run stories.track first"):
        preview.main(["--processed", str(tracked / "nowhere")])
