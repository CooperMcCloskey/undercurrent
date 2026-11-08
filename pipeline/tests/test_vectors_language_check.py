"""language_check: finding the scripts whose headlines the embedding model can't tell apart."""

import datetime as dt
import hashlib
from pathlib import Path

import numpy as np
import pytest

from undercurrent.vectors import language_check as lc
from undercurrent.vectors import store

DIM = 32
DAYS = [dt.date(2026, 1, 1) + dt.timedelta(i) for i in range(3)]


def unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=-1, keepdims=True)


def test_script_of_names_the_script_most_letters_are_written_in() -> None:
    assert lc.script_of("Pilot killed in plane crash near Hamilton") == "LATIN"
    assert lc.script_of("Минздрав Белоруссии: состояние детей стабильное") == "CYRILLIC"
    assert lc.script_of("موعد مباراة القناة والجونة اليوم") == "ARABIC"
    assert lc.script_of("സ്മാർട്ട് മീറ്റർ വൈകരുത്: കെഎസ്ഇബിക്ക് വിമർശനം") == "MALAYALAM"
    assert lc.script_of("Մոսկվան Հայաստանին թշնամի է նշանակել") == "ARMENIAN"
    assert lc.script_of("신약 개발 속도 낸다…국내 독자 기술 AI 모델 나왔다") == "HANGUL"
    assert lc.script_of("规范旅游市场秩序 净化行业营商环境") == "CJK"
    assert lc.script_of("妻夫木聡、映画『悪人』で迎えた転機を回顧") == "JAPANESE"  # kana present
    assert lc.script_of('นายกฯ เผยเคาะชื่อ "เสธ.ปูด้วง"') == "THAI"
    # digits and punctuation don't count; the majority of the letters decides
    assert lc.script_of("2026 — 12.5%") == "NONE" and lc.script_of("") == "NONE"
    assert (
        lc.script_of("Cockroach Janta Party founder Abhijeet Dipke writes to PM Modi കത്ത്")
        == "LATIN"
    )


def test_a_script_the_model_collapses_scores_high_and_a_healthy_one_near_zero() -> None:
    rng = np.random.default_rng(0)
    english = unit(rng.standard_normal((200, DIM)))  # unrelated headlines point everywhere
    collapsed = unit(np.ones(DIM) + 0.2 * rng.standard_normal((100, DIM)))  # all nearly alike
    texts = [f"English headline number {i} here" for i in range(200)] + [
        f"കേരളത്തിൽ ഇന്ന് വലിയ മഴ പെയ്തു {i}" for i in range(100)
    ]
    rows = {r["script"]: r for r in lc.measure(texts, np.vstack([english, collapsed]), rng)}
    assert rows["LATIN"]["mean"] < 0.1 and lc.verdict(rows["LATIN"]["mean"]) == "ok"
    assert rows["MALAYALAM"]["mean"] > 0.9 and lc.verdict(rows["MALAYALAM"]["mean"]) == "UNRELIABLE"
    assert [r["script"] for r in lc.measure(texts, np.vstack([english, collapsed]), rng)][
        0
    ] == "MALAYALAM"
    assert (lc.verdict(0.35), lc.verdict(0.49), lc.verdict(0.5)) == ("weak", "weak", "UNRELIABLE")


def test_a_script_with_too_few_headlines_is_not_judged() -> None:
    rng = np.random.default_rng(1)
    vectors = unit(np.ones(DIM) + 0.1 * rng.standard_normal((10, DIM)))
    assert lc.measure([f"Մոսկվան Հայաստանին թշնամի է {i}" for i in range(10)], vectors, rng) == []
    assert (
        len(lc.measure([f"Մոսկվան Հայաստանին թշնամի է {i}" for i in range(10)], vectors, rng, 5))
        == 1
    )


def embedder(texts: list[str]) -> np.ndarray:
    """A stand-in model: it knows English (each headline its own direction) and not Malayalam
    (every headline nearly the same vector)."""
    out = []
    for text in texts:
        noise = np.random.default_rng(int(hashlib.sha1(text.encode()).hexdigest()[:8], 16))
        if lc.script_of(text) == "MALAYALAM":
            out.append(unit(np.ones(DIM) + 0.2 * noise.standard_normal(DIM)))
        else:
            out.append(unit(noise.standard_normal(DIM)))
    return np.array(out, dtype=np.float32)


@pytest.fixture
def processed(tmp_path: Path) -> Path:
    for day in DAYS:
        texts = [f"Local team wins the final match number {i} today" for i in range(120)] + [
            f"കേരളത്തിൽ ഇന്ന് വലിയ മഴ പെയ്തു എന്ന് വാർത്ത {i} വന്നു" for i in range(60)
        ]
        ids = [f"{day}-{i}" for i in range(len(texts))]
        store.embed_day(
            "news", day, ids, texts, root=store.vectors_dir(tmp_path), embedder=embedder
        )
    return tmp_path


def test_the_command_reports_the_unreliable_script_and_a_line_to_paste(
    processed: Path, capsys
) -> None:
    lc.main(["--processed", str(processed), "--days", "3"])
    out = capsys.readouterr().out
    assert "540 headlines from 3 days" in out
    malayalam = next(line for line in out.splitlines() if line.startswith("MALAYALAM"))
    assert "UNRELIABLE" in malayalam
    latin = next(line for line in out.splitlines() if line.startswith("LATIN"))
    assert latin.rstrip().endswith("ok")
    assert "UNRELIABLE_SCRIPTS = ['MALAYALAM']" in out


def test_nothing_embedded_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="No embedded news days"):
        lc.main(["--processed", str(tmp_path)])
