"""Sampling pairs to label, and turning the labels into a cutoff we can trust."""

import datetime as dt
import hashlib
import re
from pathlib import Path

import numpy as np
import pytest

from undercurrent.vectors import calibrate, sample_pairs, store

DIM = 64
DAYS = [dt.date(2026, 4, 10) + dt.timedelta(days=i) for i in range(10)]


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


class Topics:
    """Headlines about topics, some tight (items near their topic) and some loose, so pairs span
    every similarity band. The id carries the topic, which is the ground truth for 'same story'."""

    def __init__(
        self,
        n_topics: int = 60,
        seed: int = 0,
        shared: float = 0.0,
        spread: tuple[float, float] = (0.3, 1.3),
    ) -> None:
        """`shared` > 0 gives all topics a common direction, so different topics look alike and
        there are real close calls."""
        rng = np.random.default_rng(seed)
        common = unit(rng.standard_normal(DIM))
        own = unit(rng.standard_normal((n_topics, DIM)))
        self.vectors = unit(common + shared * own) if shared else own
        self.spread = rng.uniform(*spread, n_topics)  # how loosely each topic's items scatter

    def embedder(self):
        def embed(texts: list[str]) -> np.ndarray:
            out = []
            for text in texts:
                noise = np.random.default_rng(int(hashlib.sha1(text.encode()).hexdigest()[:8], 16))
                topic = int(re.search(r"topic (-?\d+)", text).group(1))
                direction = self.vectors[topic] if topic >= 0 else noise.standard_normal(DIM)
                scale = self.spread[topic] if topic >= 0 else 0.0
                out.append(unit(unit(direction) + scale * unit(noise.standard_normal(DIM))))
            return np.array(out, dtype=np.float32)

        return embed

    def day(self, day_index: int, per_topic: int = 30, noise: int = 300):
        ids, texts = [], []
        for topic in range(len(self.vectors)):
            for i in range(per_topic):
                ids.append(f"{day_index}-{topic}-{i}")
                texts.append(f"headline about topic {topic} item {i} on day {day_index}")
        for i in range(noise):
            ids.append(f"{day_index}--1-{i}")
            texts.append(f"unrelated headline with topic -1 item {i} on day {day_index}")
        return ids, texts


def topic_of(item_id: str) -> int:
    return int(item_id.split("-", 1)[1].rsplit("-", 1)[0])


@pytest.fixture(scope="module")
def processed(tmp_path_factory) -> tuple[Path, Topics]:
    root = tmp_path_factory.mktemp("processed")
    world = Topics()
    for i, day in enumerate(DAYS[:6]):
        ids, texts = world.day(i)
        store.embed_day(
            "news", day, ids, texts, root=store.vectors_dir(root), embedder=world.embedder()
        )
    return root, world


def test_nearest_pairs_are_exact_unique_and_never_self() -> None:
    rng = np.random.default_rng(1)
    vectors = unit(rng.standard_normal((300, 16))).astype(np.float32)
    anchors = rng.choice(300, 40, replace=False)
    lo, hi, sims = sample_pairs.nearest_pairs(vectors, anchors, k=5, chunk=16)
    assert (lo < hi).all() and len({*zip(lo.tolist(), hi.tolist(), strict=True)}) == len(lo)
    np.testing.assert_allclose(sims, np.einsum("ij,ij->i", vectors[lo], vectors[hi]), atol=1e-5)
    # Every anchor's true 5 nearest neighbours are among the pairs.
    full = vectors @ vectors.T
    np.fill_diagonal(full, -2)
    pairs = set(zip(lo.tolist(), hi.tolist(), strict=True))
    for a in anchors:
        for b in np.argsort(-full[a])[:5]:
            assert (min(a, b), max(a, b)) in pairs


def test_each_similarity_band_is_capped_and_its_weights_restore_its_size() -> None:
    rng = np.random.default_rng(2)
    sims = np.r_[rng.uniform(0.0, 0.3, 500), rng.uniform(0.3, 0.9, 3000), rng.uniform(0.9, 1.0, 40)]
    chosen, weights = sample_pairs.sample_by_similarity(sims, per_bin=10, rng=rng)
    assert (sims[chosen] >= 0.30).all()  # nothing below the lowest band
    band = np.digitize(sims[chosen], sample_pairs.EDGES) - 1
    population = np.digitize(sims, sample_pairs.EDGES) - 1
    for b in set(band):
        assert (band == b).sum() <= 10
        assert weights[band == b].sum() == pytest.approx(
            (population == b).sum()
        )  # the band's real size


def test_the_sample_spans_the_bands_hides_the_order_and_is_repeatable(processed) -> None:
    root, _ = processed
    vroot = store.vectors_dir(root)
    days = sample_pairs.pick_days(vroot, "news", 3, None, None)
    assert days == [DAYS[0], DAYS[2] if len(days) == 3 and days[1] == DAYS[2] else days[1], DAYS[5]]
    rows = sample_pairs.build_sample(vroot, "news", days, anchors=500, per_bin=8, k=10, seed=0)
    again = sample_pairs.build_sample(vroot, "news", days, anchors=500, per_bin=8, k=10, seed=0)
    assert rows == again
    assert 40 < len(rows) <= 8 * (len(sample_pairs.EDGES) - 1)
    assert set(rows[0]) == set(sample_pairs.FIELDS) and all(r["same"] == "" for r in rows)
    sims = [float(r["sim"]) for r in rows]
    assert (
        min(sims) >= 0.30
        and max(sims) > 0.8
        and sims != sorted(sims)
        and sims != sorted(sims, reverse=True)
    )
    assert all(r["text_a"].strip().lower() != r["text_b"].strip().lower() for r in rows)


def test_the_weighted_sample_finds_the_true_cutoff_where_a_plain_sample_misleads(
    tmp_path: Path,
) -> None:
    """Label the sampled pairs with the ground truth. The recommendation has to match the one
    from every neighbour pair (which nobody could label), and the weights have to matter: a
    sample with equal numbers per similarity band overstates how good the close calls are."""
    world = Topics(n_topics=120, seed=0, shared=0.6, spread=(0.5, 1.4))
    vroot = store.vectors_dir(tmp_path)
    day = DAYS[0]
    ids, texts = world.day(0, per_topic=25, noise=200)
    store.embed_day("news", day, ids, texts, root=vroot, embedder=world.embedder())

    ids, _, vectors = sample_pairs.read_day(vroot, "news", day)
    anchors = np.random.default_rng(0).choice(len(ids), 1500, replace=False)
    lo, hi, pop_sims = sample_pairs.nearest_pairs(vectors, anchors, k=10)
    pop_same = np.array(
        [topic_of(ids[a]) == topic_of(ids[b]) >= 0 for a, b in zip(lo, hi, strict=True)]
    )
    assert 0.1 < pop_same.mean() < 0.9  # a genuine mix of right and wrong links

    rows = sample_pairs.build_sample(vroot, "news", [day], anchors=1500, per_bin=12, k=10, seed=3)
    sims = np.array([float(r["sim"]) for r in rows])
    same = np.array([topic_of(r["id_a"]) == topic_of(r["id_b"]) >= 0 for r in rows])
    weights = np.array([float(r["weight"]) for r in rows])

    true_cutoff = calibrate.best_cutoff(pop_sims, pop_same.astype(int), 0.9)["cutoff"]
    estimated = calibrate.best_cutoff(sims, same.astype(int), 0.9, weights=weights)["cutoff"]
    assert estimated == pytest.approx(true_cutoff, abs=0.05)

    middle = 0.5
    true_precision = pop_same[pop_sims >= middle].mean()
    weighted = table_row(sims, same, weights, middle)["precision"]
    plain = same[sims >= middle].mean()
    assert abs(weighted - true_precision) < abs(plain - true_precision) - 0.05


def table_row(sims, same, weights, cutoff) -> dict:
    return next(r for r in calibrate.table(sims, same, weights) if abs(r["cutoff"] - cutoff) < 1e-9)


def test_best_cutoff_with_and_without_weights() -> None:
    sims = np.array([0.9] * 10 + [0.6] * 10 + [0.45] * 10)
    same = np.array([1] * 10 + [1] * 9 + [0] + [1] * 3 + [0] * 7)
    assert calibrate.best_cutoff(sims, same, 0.9)["cutoff"] == 0.6
    heavy_low = np.array(
        [1.0] * 20 + [50.0] * 10
    )  # each low-similarity pair stands for 50 real ones
    assert calibrate.best_cutoff(sims, same, 0.9, weights=heavy_low)["cutoff"] == 0.6
    heavy_mid = np.array(
        [1.0] * 10 + [30.0] * 10 + [1.0] * 10
    )  # the 0.6 band is mostly the 1 error
    assert calibrate.best_cutoff(sims, same, 0.95, weights=heavy_mid)["cutoff"] == 0.9
    assert calibrate.best_cutoff(sims[:5], same[:5])["cutoff"] is None  # too few to say


def test_labels_are_checked_before_they_are_trusted() -> None:
    assert calibrate.parse_labels("1011 00\n10", 8).tolist() == [1, 0, 1, 1, 0, 0, 1, 0]
    assert np.isnan(calibrate.parse_labels("1?0", 3)[1])
    with pytest.raises(SystemExit, match="3 labels but the file has 4"):
        calibrate.parse_labels("101", 4)
    with pytest.raises(SystemExit, match="only contain"):
        calibrate.parse_labels("10a", 3)


def test_end_to_end_label_string_to_cutoff(processed, tmp_path: Path, capsys) -> None:
    root, _ = processed
    vroot = store.vectors_dir(root)
    rows = sample_pairs.build_sample(vroot, "news", DAYS[:3], anchors=600, per_bin=12, k=10, seed=1)
    out = tmp_path / "pairs.csv"
    sample_pairs.main(
        [
            "--processed",
            str(root),
            "--days",
            "3",
            "--anchors",
            "600",
            "--per-bin",
            "12",
            "--seed",
            "1",
            "--out",
            str(out),
            "--print",
        ]
    )
    printed = capsys.readouterr().out
    assert f"# {len(rows)} pairs" in printed and "sim" not in printed.lower().replace(
        "similar", ""
    )  # no scores shown
    assert printed.count("   || ") == len(rows)

    labels = "".join("1" if topic_of(r["id_a"]) == topic_of(r["id_b"]) >= 0 else "0" for r in rows)
    spaced = " ".join(labels[i : i + 10] for i in range(0, len(labels), 10))
    calibrate.main(["--pairs", str(out), "--labels", spaced])
    report = capsys.readouterr().out
    assert (
        "labelled pairs" in report
        and "precision 90%: cutoff" in report
        and "paste into thresholds.py" in report
    )
    cutoff = float(re.search(r"precision 90%: cutoff ([\d.]+)", report).group(1))
    assert 0.3 <= cutoff <= 0.95

    # The same labels in the CSV's own column give the same answer, and a wrong length is refused.
    text = out.read_text().splitlines()
    filled = [text[0]] + [
        line.replace(",,", f",{labels[i]},", 1) for i, line in enumerate(text[1:])
    ]
    (tmp_path / "filled.csv").write_text("\n".join(filled) + "\n")
    calibrate.main(["--pairs", str(tmp_path / "filled.csv")])
    assert f"precision 90%: cutoff {cutoff}" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="labels but the file has"):
        calibrate.main(["--pairs", str(out), "--labels", labels[:-1]])


# ---- when no cutoff reaches the target: what the report says, and spending labels where it matters -----


def test_the_top_bands_can_get_more_pairs_and_every_weight_still_restores_its_band() -> None:
    rng = np.random.default_rng(5)
    sims = np.r_[rng.uniform(0.3, 0.75, 4000), rng.uniform(0.75, 1.0, 400)]
    chosen, weights = sample_pairs.sample_by_similarity(
        sims, per_bin=6, rng=rng, per_bin_top=30, top_from=0.75
    )
    band = np.digitize(sims[chosen], sample_pairs.EDGES) - 1
    population = np.digitize(sims, sample_pairs.EDGES) - 1
    first_top = next(b for b, e in enumerate(sample_pairs.EDGES) if e >= 0.75 - 1e-9)
    for b in set(band):
        cap = 30 if b >= first_top else 6
        assert (band == b).sum() == min(cap, (population == b).sum())
        assert weights[band == b].sum() == pytest.approx((population == b).sum())
    # Without the option every band is capped alike, as before.
    plain, _ = sample_pairs.sample_by_similarity(sims, per_bin=6, rng=np.random.default_rng(5))
    assert np.bincount(np.digitize(sims[plain], sample_pairs.EDGES) - 1).max() <= 6


def test_the_table_reaches_the_top_of_the_scale() -> None:
    assert calibrate.TABLE_CUTOFFS.max() == pytest.approx(0.95)
    assert 0.875 in set(np.round(calibrate.TABLE_CUTOFFS, 3))
    assert len(calibrate.TABLE_CUTOFFS) == len(set(np.round(calibrate.TABLE_CUTOFFS, 3)))


def test_bands_and_the_top_pairs() -> None:
    sims = np.array([0.37, 0.38, 0.62, 0.63, 0.91, 0.93, 0.95])
    same = np.array([0, 0, 1, 0, 1, 0, 1])
    weights = np.array([100.0, 100.0, 10.0, 10.0, 1.0, 1.0, 1.0])
    rows = {round(r["low"], 2): r for r in calibrate.bands(sims, same, weights)}
    assert rows[0.35]["pairs"] == 2 and rows[0.35]["same"] == 0 and rows[0.35]["stands_for"] == 200
    assert rows[0.6]["precision"] == pytest.approx(0.5)
    assert rows[0.9]["pairs"] == 3 and rows[0.9]["same"] == 2 and rows[0.9]["high"] == 1.0
    top = calibrate.top_precision(sims, same, weights, n=3)
    assert top["n"] == 3 and top["same"] == 2 and top["lowest_sim"] == 0.91
    assert top["precision"] == pytest.approx(2 / 3)


TEMPLATE_PAIRS = [
    # (text_a, text_b, same, sim, weight): the top bands are full of a template, as in real GDELT
    *[
        (f"Weather today for city {i}", f"Weather today for town {i}", 0, 0.93 + i / 1000, 400)
        for i in range(6)
    ],
    *[
        (f"Real event {i} reported", f"Same real event {i} in other words", 1, 0.91 + i / 1000, 2)
        for i in range(6)
    ],
    *[(f"Mid {i} a", f"Mid {i} b", int(i % 2 == 0), 0.6 + i / 100, 30) for i in range(10)],
    *[(f"Low {i} a", f"Low {i} b", 0, 0.4 + i / 100, 500) for i in range(10)],
]


def write_pairs(path: Path, spec) -> None:
    path.write_text(
        "text_a,text_b,same,sim,weight,day,id_a,id_b\n"
        + "\n".join(
            f"{a},{b},,{sim},{w},2026-01-01,x{i},y{i}" for i, (a, b, _, sim, w) in enumerate(spec)
        )
        + "\n"
    )


def test_when_no_cutoff_reaches_the_target_the_report_names_the_pairs_that_spoil_it(
    tmp_path: Path, capsys
) -> None:
    write_pairs(tmp_path / "t.csv", TEMPLATE_PAIRS)
    labels = "".join(str(s[2]) for s in TEMPLATE_PAIRS)
    with pytest.raises(SystemExit) as stop:
        calibrate.main(["--pairs", str(tmp_path / "t.csv"), "--labels", labels])
    report = capsys.readouterr().out
    # the wider table, the bands, and the top pairs' precision are shown
    assert "0.95" in report and "0.875" in report and "stands for" in report
    assert (
        "most similar pairs (down to" in report and "That is the best any cutoff could do" in report
    )
    # the most similar pairs labelled 0 are listed with their text, similarity and weight
    assert "The most similar pairs you labelled 0" in report
    assert "Weather today for city 5" in report and "|| Weather today for town 5" in report
    assert "weight 400" in report
    # ... and the lowest true link is shown, so the cost of a high cutoff is visible
    assert "The least similar pairs you labelled 1" in report
    # the failure says what to check instead of just "label more"
    message = str(stop.value)
    assert "TEMPLATES" in message and "vm.sh pairs 4 30 6" in message and "vm.sh preview" in message


def test_a_run_that_succeeds_still_shows_the_diagnostics(processed, tmp_path: Path, capsys) -> None:
    root, _ = processed
    out = tmp_path / "pairs.csv"
    sample_pairs.main(
        ["--processed", str(root), "--days", "3", "--anchors", "600", "--per-bin", "12",
         "--per-bin-top", "20", "--seed", "1", "--out", str(out)]
    )  # fmt: skip
    rows = list(__import__("csv").DictReader(out.open()))
    labels = "".join("1" if topic_of(r["id_a"]) == topic_of(r["id_b"]) >= 0 else "0" for r in rows)
    calibrate.main(["--pairs", str(out), "--labels", labels])
    report = capsys.readouterr().out
    assert "paste into thresholds.py" in report and "precision 90%: cutoff" in report
    assert "stands for" in report and "most similar pairs (down to" in report


def test_min_pairs_can_be_lowered_for_a_thin_top_band(tmp_path: Path, capsys) -> None:
    spec = [(f"A{i}", f"B{i}", int(i < 5), 0.92 - i / 100, 3) for i in range(6)] + [
        (f"C{i}", f"D{i}", 0, 0.5 - i / 100, 100) for i in range(30)
    ]
    write_pairs(tmp_path / "t.csv", spec)
    labels = "".join(str(s[2]) for s in spec)
    with pytest.raises(SystemExit):
        calibrate.main(["--pairs", str(tmp_path / "t.csv"), "--labels", labels])
    assert "with 10+ pairs above" in capsys.readouterr().out
    calibrate.main(["--pairs", str(tmp_path / "t.csv"), "--labels", labels, "--min-pairs", "4"])
    assert "paste into thresholds.py" in capsys.readouterr().out
