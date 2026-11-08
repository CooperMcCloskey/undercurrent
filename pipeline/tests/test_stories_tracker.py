"""The story tracker: it follows stories across days, never looks ahead, and resumes exactly."""

import datetime as dt
import hashlib
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent import contracts
from undercurrent.forecast import panel as forecast_panel
from undercurrent.stories import track
from undercurrent.stories.cluster import cluster_items, knn
from undercurrent.stories.tracker import Tracker, TrackerConfig
from undercurrent.vectors import store

DIM = 32
START = dt.date(2026, 1, 1)
CFG = TrackerConfig(
    link_sim=0.5, assign_sim=0.55, min_size=3, merge_sim=0.9, idle_days=5, half_life_days=7.0
)


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


class World:
    """Topics are directions; an item is its topic plus noise. Noise items point nowhere."""

    def __init__(self, n_topics: int = 12, seed: int = 0, dim: int = DIM) -> None:
        self.dim = dim
        self.rng = np.random.default_rng(seed)
        self.topics = unit(self.rng.standard_normal((n_topics, dim)))

    def day(self, index: int, topics: dict[int, int], noise: int = 0, places=None):
        ids, vectors, truth = [], [], []
        for topic, count in topics.items():
            for i in range(count):
                ids.append(f"d{index:02d}-t{topic:02d}-{i:02d}")
                vectors.append(
                    unit(self.topics[topic] + 0.5 * unit(self.rng.standard_normal(self.dim)))
                )
                truth.append(topic)
        for i in range(noise):
            ids.append(f"d{index:02d}-noise-{i:02d}")
            vectors.append(unit(self.rng.standard_normal(self.dim)))
            truth.append(-1)
        return (
            START + dt.timedelta(days=index),
            ids,
            np.array(vectors, dtype=np.float32),
            np.array(truth),
        )


@pytest.fixture(scope="module")
def days() -> list:
    world = World()
    spec = [
        ({0: 8, 1: 8, 2: 8, 3: 8, 4: 8}, 15),
        ({0: 6, 1: 6, 2: 6, 3: 6, 4: 6}, 15),
        ({0: 5, 1: 5, 2: 5, 3: 5, 4: 5, 5: 7}, 10),
        ({0: 5, 1: 5, 5: 5}, 10),
        ({0: 4, 1: 5, 5: 5}, 10),
        ({0: 5, 1: 4, 5: 5}, 10),
    ]
    return [world.day(i, topics, noise) for i, (topics, noise) in enumerate(spec)]


def run(tracker: Tracker, days: list) -> list:
    return [tracker.step(day, ids, vectors) for day, ids, vectors, _ in days]


def story_of_topic(results: list, days: list) -> dict[int, set[str]]:
    truth = {i: t for _, ids, _, tr in days for i, t in zip(ids, tr, strict=True)}
    found: dict[int, set[str]] = {}
    for r in results:
        for row in r.assignments.itertuples():
            found.setdefault(truth[row.article_id], set()).add(row.story_id)
    return found


def test_stories_start_on_day_one_and_keep_their_id(days) -> None:
    tracker = Tracker(DIM, CFG)
    results = run(tracker, days)
    first = results[0].stats
    assert first["new_stories"] == 5 and first["joined_existing"] == 0
    assert first["no_story"] == 15  # the noise belongs to nothing

    assert results[1].stats["new_stories"] == 0 and results[1].stats["joined_existing"] == 30
    assert results[2].stats["new_stories"] == 1  # topic 5 appears
    stories = story_of_topic(results, days)
    assert -1 not in stories  # noise never gets a story
    assert {t: len(s) for t, s in stories.items()} == {0: 1, 1: 1, 2: 1, 3: 1, 4: 1, 5: 1}
    assert len({next(iter(s)) for s in stories.values()}) == 6  # and they are different stories


def test_output_is_what_the_exporter_needs(days) -> None:
    result = run(Tracker(DIM, CFG), days)[0]
    assert list(result.assignments.columns) == ["article_id", "story_id", "day", "similarity"]
    assert list(result.stories.columns) == [
        "story_id",
        "day",
        "n_articles_day",
        "first_seen",
        "label",
        "category",
    ]
    assert result.stories["n_articles_day"].tolist() == [8] * 5
    assert (result.assignments["similarity"].between(0, 1)).all()
    for story_id in result.stories["story_id"]:
        assert re.match(contracts.STORY_ID_PATTERN, story_id)


def test_the_past_never_depends_on_the_future(days) -> None:
    long_run = run(Tracker(DIM, CFG), days)
    short_run = run(Tracker(DIM, CFG), days[:3])
    for early, late in zip(short_run, long_run[:3], strict=True):
        pd.testing.assert_frame_equal(early.assignments, late.assignments)
        pd.testing.assert_frame_equal(early.stories, late.stories)


def test_input_order_does_not_matter(days) -> None:
    rng = np.random.default_rng(1)
    shuffled = []
    for day, ids, vectors, truth in days:
        p = rng.permutation(len(ids))
        shuffled.append((day, [ids[i] for i in p], vectors[p], truth[p]))
    for a, b in zip(run(Tracker(DIM, CFG), days), run(Tracker(DIM, CFG), shuffled), strict=True):
        pd.testing.assert_frame_equal(a.assignments, b.assignments)


def test_resuming_gives_exactly_the_same_result(days, tmp_path: Path) -> None:
    straight = run(Tracker(DIM, CFG), days)
    first = Tracker(DIM, CFG)
    run(first, days[:3])
    first.save(tmp_path / "state.pkl")
    resumed = run(Tracker.load(tmp_path / "state.pkl"), days[3:])
    for a, b in zip(straight[3:], resumed, strict=True):
        pd.testing.assert_frame_equal(a.assignments, b.assignments)
        pd.testing.assert_frame_equal(a.stories, b.stories)


def test_days_go_in_order_once(days) -> None:
    tracker = Tracker(DIM, CFG)
    day, ids, vectors, _ = days[1]
    tracker.step(day, ids, vectors)
    with pytest.raises(ValueError, match="in order, once"):
        tracker.step(day, ids, vectors)
    with pytest.raises(ValueError, match="in order, once"):
        tracker.step(day - dt.timedelta(days=1), ids, vectors)


def test_a_day_without_items_is_fine(days) -> None:
    tracker = Tracker(DIM, CFG)
    run(tracker, days[:1])
    empty = tracker.step(START + dt.timedelta(days=1), [], np.zeros((0, DIM), dtype=np.float32))
    assert empty.assignments.empty and empty.stories.empty and empty.stats["active"] == 5


def test_skipping_an_outage_ages_no_story() -> None:
    """An outage longer than idle_days retires nothing, and no centroid fades during it."""
    world = World(seed=12)
    tracker = Tracker(DIM, CFG)  # idle_days=5
    story = tracker.step(*world.day(0, {0: 6})[:3]).stories["story_id"].iloc[0]
    sums = tracker.sums.copy()
    for index in range(1, 9):  # eight days with no data at all
        tracker.skip(START + dt.timedelta(days=index))
    assert np.array_equal(tracker.sums, sums) and len(tracker) == 1
    back = tracker.step(*world.day(9, {0: 6})[:3])
    assert back.stats["retired"] == 0 and back.stats["new_stories"] == 0
    assert set(back.stories["story_id"]) == {story}
    assert back.stories["first_seen"].tolist() == [START]  # first_seen is still the real day
    with pytest.raises(ValueError, match="in order, once"):
        tracker.skip(START + dt.timedelta(days=9))


def test_duplicate_stories_are_merged_and_the_lineage_says_so() -> None:
    world = World(seed=3)
    # A strict assign_sim makes day 2 open a second story for the same topic; merging fixes it.
    cfg = TrackerConfig(link_sim=0.5, assign_sim=0.95, min_size=3, merge_sim=0.8, idle_days=30)
    tracker = Tracker(DIM, cfg)
    one = tracker.step(*world.day(0, {0: 6})[:3])
    two = tracker.step(*world.day(1, {0: 6})[:3])
    original = one.stories["story_id"].iloc[0]
    assert two.stats["new_stories"] == 1 and two.stats["merged"] == 1
    assert (
        two.lineage["new_story_id"].tolist() == [original]
        and two.lineage["old_story_id"].iloc[0] != original
    )
    assert set(two.assignments["story_id"]) == {original}  # today's rows already use the survivor
    assert (
        two.stories["story_id"].tolist() == [original]
        and two.stories["n_articles_day"].iloc[0] == 6
    )
    assert len(tracker) == 1


def test_idle_stories_are_retired_and_do_not_come_back_under_the_same_id() -> None:
    world = World(seed=4)
    tracker = Tracker(DIM, CFG)  # idle_days=5
    first = tracker.step(*world.day(0, {0: 6})[:3])
    story = first.stories["story_id"].iloc[0]
    retired_on = None
    for index in range(1, 8):
        r = tracker.step(*world.day(index, {1: 5})[:3])
        if r.stats["retired"] and retired_on is None:
            retired_on = index
    assert retired_on == 6  # six days after its last article, with idle_days=5
    back = tracker.step(*world.day(8, {0: 6})[:3])
    assert back.stats["new_stories"] == 1 and story not in set(back.stories["story_id"])


def test_the_place_rule_keeps_same_topic_stories_in_different_countries_apart() -> None:
    world = World(seed=5)
    frankfurt, buenos_aires = [(50.11, 8.68, "DE")], [(-34.60, -58.38, "AR")]

    def day(index: int):
        d, ids, vectors, _ = world.day(index, {0: 6})
        places = [frankfurt if i % 2 == 0 else buenos_aires for i in range(len(ids))]
        return d, ids, vectors, places

    tracker = Tracker(DIM, CFG)
    d0, ids0, v0, p0 = day(0)
    one = tracker.step(d0, ids0, v0, places=p0)
    assert one.stats["new_stories"] == 2  # same words, two countries: two stories
    d1, ids1, v1, p1 = day(1)
    two = tracker.step(d1, ids1, v1, places=p1)
    assert two.stats["new_stories"] == 0 and two.stats["joined_existing"] == 6
    country = {i: p[0][2] for i, p in zip(ids0 + ids1, p0 + p1, strict=True)}
    for _story_id, group in two.assignments.groupby("story_id"):
        assert (
            len({country[a] for a in group["article_id"]}) == 1
        )  # each story stays in one country


def test_nearest_story_for_a_live_post(days) -> None:
    tracker = Tracker(DIM, CFG)
    run(tracker, days[:1])
    world_topic = days[0][2][0]  # the first item of the first day is about topic 0
    ((story, sim),) = tracker.nearest(world_topic[None, :])
    assert story in {s for s in tracker.ids} and sim > 0.9
    assert tracker.nearest(unit(np.ones((1, DIM), dtype=np.float32)))[0] is None


def test_a_saved_state_from_another_version_is_refused(days, tmp_path: Path) -> None:
    import pickle

    tracker = Tracker(DIM, CFG)
    run(tracker, days[:1])
    tracker.save(tmp_path / "s.pkl")
    state = pickle.loads((tmp_path / "s.pkl").read_bytes())
    state["version"] = 99
    (tmp_path / "old.pkl").write_bytes(pickle.dumps(state))
    with pytest.raises(ValueError, match="another version"):
        Tracker.load(tmp_path / "old.pkl")


# ---- the clustering underneath --------------------------------------------------------------------


def test_clustering_recovers_separate_stories_and_leaves_noise_alone() -> None:
    world = World(n_topics=40, seed=6, dim=128)  # roomy enough that noise doesn't line up by chance
    _, _, vectors, truth = world.day(0, {t: 12 for t in range(40)}, noise=200)
    labels = cluster_items(vectors, None, min_sim=0.5, min_size=3)
    assert len(set(labels) - {-1}) == 40 and (labels[truth == -1] == -1).all()
    for t in range(40):
        assert len(set(labels[truth == t])) == 1


def test_a_chain_of_stories_is_split_where_it_is_weak() -> None:
    """Stories A and B are joined by a few bridging items: one component, but two stories."""
    rng = np.random.default_rng(7)
    a, b = unit(rng.standard_normal((2, DIM)))
    side_a = unit(a + 0.4 * unit(rng.standard_normal((15, DIM))))
    side_b = unit(b + 0.4 * unit(rng.standard_normal((15, DIM))))
    bridge = unit(a + b + 0.2 * unit(rng.standard_normal((3, DIM))))
    vectors = np.vstack([side_a, side_b, bridge]).astype(np.float32)
    labels = cluster_items(vectors, None, k=6, min_sim=0.4, min_size=3)
    assert len(set(labels[:15])) == 1 and len(set(labels[15:30])) == 1
    assert labels[0] != labels[15]


def test_one_coherent_story_is_not_cut_into_pieces() -> None:
    rng = np.random.default_rng(8)
    vectors = unit(
        unit(rng.standard_normal(DIM)) + 0.5 * unit(rng.standard_normal((60, DIM)))
    ).astype(np.float32)
    labels = cluster_items(vectors, None, k=10, min_sim=0.5, min_size=3)
    assert len(set(labels)) == 1


def test_neighbours_exclude_the_item_itself_even_with_duplicates() -> None:
    vectors = np.tile(unit(np.ones((1, DIM), dtype=np.float32)), (20, 1))  # 20 identical vectors
    sims, idx = knn(vectors, 5)
    assert idx.shape == (20, 5) and not (idx == np.arange(20)[:, None]).any()


# ---- the whole path: vector store -> tracker -> the forecast loader --------------------------------


def _topic_embedder(world: World):
    def embed(texts: list[str]) -> np.ndarray:
        out = []
        for text in texts:
            noise = np.random.default_rng(int(hashlib.sha1(text.encode()).hexdigest()[:8], 16))
            topic = int(re.search(r"topic (\d+)", text).group(1))
            out.append(unit(world.topics[topic] + 0.5 * unit(noise.standard_normal(DIM))))
        return np.array(out, dtype=np.float32)

    return embed


def _make_processed(path: Path, world: World, per_day: list[dict[int, int]]) -> None:
    rows = []
    for index, topics in enumerate(per_day):
        for topic, count in topics.items():
            for i in range(count):
                rows.append(
                    (
                        f"a{index}-{topic}-{i}",
                        f"topic {topic} story headline number {i} day {index}",
                        START + dt.timedelta(days=index),
                    )
                )
    place = {"name": "Paris", "country": "FR", "lat": 48.85, "lon": 2.35, "type": 3}
    location = pa.struct(
        [
            ("name", pa.string()),
            ("country", pa.string()),
            ("lat", pa.float64()),
            ("lon", pa.float64()),
            ("type", pa.int32()),
        ]
    )
    pq.write_table(
        pa.table(
            {
                "id": [r[0] for r in rows],
                "title": [r[1] for r in rows],
                "day": [r[2] for r in rows],
                "domain": [f"site{i % 3}.com" for i in range(len(rows))],
                "tone": [-1.0] * len(rows),
                "locations": [[place] for _ in rows],
            },
            schema=pa.schema(
                [
                    ("id", pa.string()),
                    ("title", pa.string()),
                    ("day", pa.date32()),
                    ("domain", pa.string()),
                    ("tone", pa.float64()),
                    ("locations", pa.list_(location)),
                ]
            ),
        ),
        path / "articles.parquet",
    )
    for index in range(len(per_day)):
        day = START + dt.timedelta(days=index)
        ids, texts = store.day_inputs("news", path, day)
        store.embed_day(
            "news", day, ids, texts, root=store.vectors_dir(path), embedder=_topic_embedder(world)
        )


def test_the_command_line_end_to_end_and_resume(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    world = World(seed=9)
    _make_processed(
        tmp_path, world, [{0: 6, 1: 6}, {0: 5, 1: 5, 2: 6}, {0: 5, 1: 4, 2: 5}, {1: 5, 2: 5}]
    )
    base = [
        "--processed",
        str(tmp_path),
        "--no-categories",
        "--link-sim",
        "0.5",
        "--assign-sim",
        "0.55",
    ]

    track.main(["--start", "2026-01-01", "--end", "2026-01-02", *base])
    assert (tmp_path / "tracker_state" / "state.pkl").exists()
    out = capsys.readouterr().out
    assert "2026-01-02" in out and "done:" in out

    # Carry on after "a crash": the state resumes, and the earlier days are not redone.
    track.main(["--start", "2026-01-01", "--end", "2026-01-04", *base])
    out = capsys.readouterr().out
    assert "resuming after 2026-01-02" in out and "2026-01-01 " not in out.replace("resuming", "")
    files = sorted(p.name for p in (tmp_path / "story_assignments").glob("*.parquet"))
    assert files == [f"2026-01-0{d}.parquet" for d in (1, 2, 3, 4)]

    # Skipping a day is refused: the tracker must see every day.
    with pytest.raises(SystemExit, match="Every day has to be processed"):
        track.main(["--start", "2026-01-09", "--end", "2026-01-10", *base])

    # What it wrote is what the forecast code reads.
    daily = pd.concat(pd.read_parquet(p) for p in (tmp_path / "stories_daily").glob("*.parquet"))
    assert daily["story_id"].nunique() == 3 and (daily["category"] == "other").all()
    panel, totals, _ = forecast_panel.build_panel(tmp_path)
    assert set(panel["story_id"]) == set(daily["story_id"])
    assert panel.groupby("story_id")["n_articles"].sum().sum() == daily["n_articles_day"].sum()
    assert {"n_domains", "n_countries", "tone_mean", "category", "first_seen"} <= set(panel.columns)


def test_a_missing_day_of_vectors_says_how_to_fix_it(tmp_path: Path) -> None:
    world = World(seed=10)
    _make_processed(tmp_path, world, [{0: 6}])
    with pytest.raises(SystemExit, match="Embed it first"):
        track.main(
            [
                "--start",
                "2026-01-01",
                "--end",
                "2026-01-02",
                "--processed",
                str(tmp_path),
                "--no-categories",
                "--link-sim",
                "0.5",
            ]
        )


def _make_partitioned(path: Path, world: World, per_day: list[dict[int, int]]) -> None:
    """Articles as the pullers write them: articles/day=<day>/part.parquet. An empty dict is an
    outage day, which the pullers still write, as an empty part.parquet."""
    location = pa.struct(
        [
            ("name", pa.string()),
            ("country", pa.string()),
            ("lat", pa.float64()),
            ("lon", pa.float64()),
        ]
    )
    schema = pa.schema(
        [("id", pa.string()), ("title", pa.string()), ("locations", pa.list_(location))]
    )
    place = {"name": "Paris", "country": "FR", "lat": 48.85, "lon": 2.35}
    for index, topics in enumerate(per_day):
        day = START + dt.timedelta(days=index)
        rows = [
            (f"a{index}-{t}-{i}", f"topic {t} story headline number {i} day {index}")
            for t, count in topics.items()
            for i in range(count)
        ]
        folder = path / "articles" / f"day={day:%Y-%m-%d}"
        folder.mkdir(parents=True)
        table = {"id": [r[0] for r in rows], "title": [r[1] for r in rows]}
        table["locations"] = [[place] for _ in rows]
        pq.write_table(pa.table(table, schema=schema), folder / "part.parquet")
    for index in range(len(per_day)):
        day = START + dt.timedelta(days=index)
        ids, texts = store.day_inputs("news", path, day)
        store.embed_day(
            "news", day, ids, texts, root=store.vectors_dir(path), embedder=_topic_embedder(world)
        )


def test_an_outage_day_is_skipped_and_the_stories_carry_on(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    world = World(seed=13)
    _make_partitioned(tmp_path, world, [{0: 6, 1: 6}, {}, {}, {0: 5, 1: 5}])
    assert track.no_articles(tmp_path, START + dt.timedelta(days=1))
    assert not track.no_articles(tmp_path, START)  # a day with articles
    assert not track.no_articles(tmp_path, START + dt.timedelta(days=9))  # not pulled yet
    # idle_days=1: if the two-day gap counted as idle time, every story would retire.
    base = ["--processed", str(tmp_path), "--no-categories", "--link-sim", "0.5"]
    base += ["--assign-sim", "0.55", "--idle-days", "1"]

    track.main(["--start", "2026-01-01", "--end", "2026-01-04", *base])
    assert capsys.readouterr().out.count("a source outage") == 2
    daily = {
        p.stem: pd.read_parquet(p) for p in sorted((tmp_path / "stories_daily").glob("*.parquet"))
    }
    assert list(daily) == ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"]
    assert daily["2026-01-02"].empty and daily["2026-01-03"].empty  # covered, but empty
    assert len(daily["2026-01-01"]) == 2
    assert set(daily["2026-01-04"]["story_id"]) == set(daily["2026-01-01"]["story_id"])

    # A day that hasn't been pulled is still an error, not an outage.
    with pytest.raises(SystemExit, match="Embed it first"):
        track.main(["--start", "2026-01-01", "--end", "2026-01-05", *base])


def test_a_day_that_produces_nothing_is_still_written_with_the_right_types(tmp_path: Path) -> None:
    """Found with real data: a quiet first day (4 headlines, no story) has empty frames."""
    world = World(seed=11)
    tracker = Tracker(DIM, CFG)
    day, ids, vectors, _ = world.day(0, {0: 2}, noise=2)
    result = tracker.step(day, ids, vectors)
    assert result.assignments.empty and result.stories.empty
    track.write_day(tmp_path, result)
    for folder in ("story_assignments", "stories_daily"):
        table = pq.read_table(tmp_path / folder / "2026-01-01.parquet")
        assert table.num_rows == 0 and table.schema.equals(track.SCHEMAS[folder])
    assert not (tmp_path / "story_lineage").exists()  # no merge, no file


@pytest.mark.parametrize(
    ("is_cached", "expected"), [(False, ["made in another process", "loaded"]), (True, ["loaded"])]
)
def test_missing_category_prototypes_are_made_in_another_process_not_this_one(
    is_cached: bool, expected: list[str], monkeypatch, tmp_path: Path
) -> None:
    """Making them loads PyTorch, which would leave FAISS on one thread for the whole run."""
    from undercurrent.vectors import categories

    events: list[str] = []

    class Done:
        returncode = 0

    def run(command, check):
        assert command[1] == "-c" and "prototypes()" in command[2]
        events.append("made in another process")
        return Done()

    monkeypatch.setattr(categories, "cached", lambda: is_cached)
    monkeypatch.setattr(categories, "prototypes", lambda: events.append("loaded"))
    monkeypatch.setattr(track.subprocess, "run", run)
    with pytest.raises(SystemExit, match="No news vectors"):  # nothing embedded: stops right after
        track.main(["--processed", str(tmp_path), "--start", "2026-01-01", "--end", "2026-01-01"])
    assert events == expected
