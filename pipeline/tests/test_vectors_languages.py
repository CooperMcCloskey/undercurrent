"""vectors/languages.py and store.load_day: the tracker only sees headlines in languages we trust."""

import datetime as dt
import logging
import re
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from undercurrent.vectors import languages, store

DAY = dt.date(2026, 11, 3)
DIM = 8

ENGLISH = "Local team wins the final match number {} today"
FRENCH = "Une nouvelle route a été inaugurée hier à Lyon numéro {}"
RUSSIAN = "Сегодня в Москве ожидаются сильные дожди номер {}"
KOREAN = "오늘 서울에는 폭우가 예상된다는 소식이 전해졌습니다 {}"
MALAYALAM = "ഇന്ന് കൊച്ചിയിൽ കനത്ത മഴയ്ക്ക് സാധ്യതയുണ്ട് എന്ന് വാർത്ത {}"
BENGALI = "আজ কলকাতায় ভারী বৃষ্টির সম্ভাবনা রয়েছে বলে খবর {}"


def test_script_of_names_the_script_of_the_letters() -> None:
    assert languages.script_of(ENGLISH.format(1)) == "LATIN"
    assert languages.script_of(RUSSIAN.format(1)) == "CYRILLIC"
    assert languages.script_of(KOREAN.format(1)) == "HANGUL"
    assert languages.script_of(MALAYALAM.format(1)) == "MALAYALAM"
    assert languages.script_of("规范旅游市场秩序 净化行业营商环境") == "CJK"
    assert languages.script_of("妻夫木聡、映画『悪人』で迎えた転機を回顧") == "JAPANESE"
    assert languages.script_of("2026 — 12.5%") == "NONE"


def test_the_allowlist_is_measured_and_well_formed() -> None:
    codes = [code for codes, _ in languages.TRUSTED.values() for code in codes]
    assert all(re.fullmatch(r"[a-z]{3}", c) for c in codes)
    assert len(codes) == len(set(codes)), "a code belongs to one language"
    assert all(mean < 0.35 for _, mean in languages.TRUSTED.values()), "only languages that passed"
    # the big ones are in
    for code in "eng fra spa deu rus ara zho jpn tur hin ukr pol por ita nld swe heb ell vie ind tha".split():
        assert code in languages.ALLOWED_LANGS, code
    # Korean is in the model card and still broken; the rest were not taught, or collapsed
    for code in "kor mal tam tel kan ben pan sin amh cym hau".split():
        assert code not in languages.ALLOWED_LANGS, code
    # languages that looked fine but are not in the model card are left out: we aren't certain
    for code in "afr swa tgl uzb kaz aze nep".split():
        assert code not in languages.ALLOWED_LANGS, code


def test_a_language_code_decides_and_no_code_means_the_english_feed() -> None:
    assert languages.is_trusted(FRENCH.format(1), "fra") and languages.is_trusted(
        FRENCH.format(1), "FRA"
    )
    assert not languages.is_trusted(KOREAN.format(1), "kor")
    assert not languages.is_trusted(ENGLISH.format(1), "swa"), "the code wins over the letters"
    assert not languages.is_trusted(ENGLISH.format(1), "xyz"), "an unknown code is not trusted"
    # no code: it is from the English feed, so it must look English
    assert languages.is_trusted(ENGLISH.format(1), None) and languages.is_trusted(
        ENGLISH.format(1), ""
    )
    assert not languages.is_trusted(KOREAN.format(1), None)
    assert not languages.is_trusted(MALAYALAM.format(1), None)


def test_without_language_codes_the_script_decides() -> None:
    texts = [
        ENGLISH.format(1),
        RUSSIAN.format(1),
        KOREAN.format(1),
        MALAYALAM.format(1),
        BENGALI.format(1),
    ]
    assert languages.trusted_mask(texts, None).tolist() == [True, True, False, False, False]
    with_codes = languages.trusted_mask(texts, ["eng", "rus", "kor", "mal", "ben"])
    assert with_codes.tolist() == [True, True, False, False, False]
    with pytest.raises(ValueError):  # codes and texts must line up
        languages.trusted_mask(texts, ["eng"])


def test_the_summary_names_what_was_left_out() -> None:
    texts = [KOREAN.format(i) for i in range(3)] + [BENGALI.format(1), ENGLISH.format(1)]
    langs = ["kor", "kor", "kor", "ben", None]
    keep = languages.trusted_mask(texts, langs)
    assert languages.left_out_summary(texts, langs, keep) == "kor 3, ben 1"
    by_script = languages.left_out_summary(texts, None, languages.trusted_mask(texts, None))
    assert by_script == "script HANGUL 3, script BENGALI 1"


# ---- load_day -------------------------------------------------------------------------------------


def embedder(texts: list[str]) -> np.ndarray:
    rng = np.random.default_rng(0)
    v = rng.standard_normal((len(texts), DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


ROWS = (
    [(f"e{i}", ENGLISH.format(i), None) for i in range(3)]  # the English feed: no code
    + [(f"f{i}", FRENCH.format(i), "fra") for i in range(3)]
    + [(f"r{i}", RUSSIAN.format(i), "rus") for i in range(2)]
    + [(f"k{i}", KOREAN.format(i), "kor") for i in range(4)]
    + [(f"m{i}", MALAYALAM.format(i), "mal") for i in range(3)]
    + [(f"b{i}", BENGALI.format(i), "ben") for i in range(2)]
)
KEPT = {"e0", "e1", "e2", "f0", "f1", "f2", "r0", "r1"}


def build(tmp: Path, articles: str = "partitioned") -> Path:
    ids, texts, langs = zip(*ROWS, strict=True)
    store.embed_day(
        "news", DAY, list(ids), list(texts), root=store.vectors_dir(tmp), embedder=embedder
    )
    if articles == "partitioned":
        part = tmp / "articles" / f"day={DAY}"
        part.mkdir(parents=True)
        pq.write_table(pa.table({"id": list(ids), "lang": list(langs)}), part / "part.parquet")
    elif articles == "no-lang-column":
        part = tmp / "articles" / f"day={DAY}"
        part.mkdir(parents=True)
        pq.write_table(pa.table({"id": list(ids), "title": list(texts)}), part / "part.parquet")
    return tmp


def test_load_day_leaves_out_the_languages_we_do_not_trust(tmp_path: Path, caplog) -> None:
    build(tmp_path)
    with caplog.at_level(logging.INFO):
        ids, vectors = store.load_day("news", DAY, root=store.vectors_dir(tmp_path))
    assert set(ids) == KEPT and len(ids) == len(vectors) == len(KEPT)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    # a line says how much, and in which languages
    assert "left out 9 of 17 headlines" in caplog.text
    assert "kor 4" in caplog.text and "mal 3" in caplog.text and "ben 2" in caplog.text


def test_the_vectors_stay_with_their_ids(tmp_path: Path) -> None:
    build(tmp_path)
    root = store.vectors_dir(tmp_path)
    all_ids, all_vectors = store.load("news", [DAY], root=root)
    ids, vectors = store.load_day("news", DAY, root=root)
    for i, vec in zip(ids, vectors, strict=True):
        assert np.allclose(vec, all_vectors[all_ids.index(i)], atol=1e-6)


def test_load_day_can_return_everything(tmp_path: Path) -> None:
    build(tmp_path)
    root = store.vectors_dir(tmp_path)
    assert len(store.load_day("news", DAY, root=root, reliable_only=False)[0]) == len(ROWS)
    assert len(store.load("news", [DAY], root=root)[0]) == len(ROWS), "load itself is unchanged"


@pytest.mark.parametrize("articles", ["none", "no-lang-column"])
def test_without_language_codes_it_falls_back_to_the_script(tmp_path: Path, articles: str) -> None:
    build(tmp_path, articles)
    ids, _ = store.load_day("news", DAY, root=store.vectors_dir(tmp_path))
    # Korean (Hangul), Malayalam and Bengali are out by script; the rest are in
    assert set(ids) == KEPT


def test_other_sources_are_untouched(tmp_path: Path) -> None:
    root = store.vectors_dir(tmp_path)
    texts = [KOREAN.format(i) for i in range(3)]
    store.embed_day("bluesky", DAY, ["a", "b", "c"], texts, root=root, embedder=embedder)
    # (a short post of Korean words is still a post: this filter is for news headlines)
    ids, _ = store.load_day("bluesky", DAY, root=root)
    assert set(ids) <= {"a", "b", "c"}
