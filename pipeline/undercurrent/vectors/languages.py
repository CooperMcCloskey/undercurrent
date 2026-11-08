"""Which headlines the story tracker may use: only those in a language we have checked the model handles.

The embedding model (paraphrase-multilingual-MiniLM) only separates the languages it was taught. For
others it gives nearly every headline the same vector, so any two look like "the same story" and the
tracker glues them into one giant story (on 2026-11-01..06 at cutoff 0.80: 2,444 unrelated Korean,
Bengali and Telugu headlines in one story). Being in the model card is not enough: Korean is in it
and is broken.

So this is an ALLOWLIST. A language is trusted only if BOTH hold:
  - it is one of the languages the model card says it was trained on, and
  - it kept four unrelated news sentences apart (rain forecast, football final, new road, central
    bank rates, translated by hand into each language; mean cosine of the 6 pairs, English 0.01).
Anything else is left out of tracking, including languages that scored fine but are not in the card
(Afrikaans, Swahili, Tagalog, Uzbek, Kazakh, Azerbaijani, Nepali): we aren't certain about those.

Measured and left OUT (mean cosine of unrelated headlines; 0.35 or more is not trusted):
    Korean 0.82 (in the card, but broken), Punjabi 0.80, Tamil 0.79, Malayalam 0.76 (real headlines:
    0.94), Hausa 0.71, Kannada 0.70, Telugu 0.62, Amharic 0.58, Welsh 0.56, Bengali 0.52, Sinhala 0.48.

To bring a language back (a better model, or a measurement on real headlines: `vm.sh languages`),
add its codes below. The codes are what GDELT's translated feed puts in `articles.lang`: three
letters (`fra`); the English feed has none. Both ISO 639-2 variants are listed where they differ.
Re-measuring a language is cheap, so when in doubt measure.
"""

import logging
import unicodedata
from collections import Counter
from collections.abc import Sequence
from functools import cache

import numpy as np

log = logging.getLogger(__name__)

# language -> (codes, measured mean cosine of unrelated headlines). English is 0.01.
TRUSTED: dict[str, tuple[tuple[str, ...], float]] = {
    "English": (("eng",), 0.01),
    "Albanian": (("sqi", "alb"), 0.03),
    "Latvian": (("lav",), 0.04),
    "Indonesian": (("ind",), 0.02),
    "Hindi": (("hin",), 0.03),
    "Macedonian": (("mkd", "mac"), 0.05),
    "Slovak": (("slk", "slo"), 0.05),
    "Arabic": (("ara",), 0.05),
    "French": (("fra", "fre"), 0.06),
    "Estonian": (("est",), 0.06),
    "Malay": (("msa", "may", "zsm"), 0.06),
    "Persian": (("fas", "per"), 0.07),
    "Urdu": (("urd",), 0.07),
    "Finnish": (("fin",), 0.07),
    "Croatian": (("hrv",), 0.08),
    "Hungarian": (("hun",), 0.08),
    "Portuguese": (("por",), 0.08),
    "Galician": (("glg",), 0.08),
    "Spanish": (("spa",), 0.09),
    "Thai": (("tha",), 0.09),
    "Marathi": (("mar",), 0.09),
    "Hebrew": (("heb",), 0.09),
    "Norwegian": (("nob", "nor"), 0.09),
    "Serbian": (("srp",), 0.09),
    "Bulgarian": (("bul",), 0.09),
    "Vietnamese": (("vie",), 0.10),
    "Lithuanian": (("lit",), 0.10),
    "Dutch": (("nld", "dut"), 0.10),
    "Catalan": (("cat",), 0.10),
    "Italian": (("ita",), 0.10),
    "Swedish": (("swe",), 0.10),
    "Turkish": (("tur",), 0.11),
    "German": (("deu", "ger"), 0.11),
    "Russian": (("rus",), 0.11),
    "Chinese": (("zho", "chi", "cmn"), 0.11),
    "Ukrainian": (("ukr",), 0.11),
    "Romanian": (("ron", "rum"), 0.11),
    "Slovenian": (("slv",), 0.11),
    "Greek": (("ell", "gre"), 0.12),
    "Czech": (("ces", "cze"), 0.12),
    "Burmese": (("mya", "bur"), 0.12),
    "Danish": (("dan",), 0.12),
    "Gujarati": (("guj",), 0.13),
    "Mongolian": (("mon", "khk"), 0.13),
    "Japanese": (("jpn",), 0.16),
    "Armenian": (("hye", "arm"), 0.17),
    "Georgian": (("kat", "geo"), 0.22),
    "Polish": (("pol",), 0.25),
    "Kurdish": (("kur", "kmr"), 0.25),
    # Not probed by itself, but the same language as Croatian and Serbian in the model's eyes, and
    # Bosnian headlines (Mostar, Sarajevo) came out as clean stories in the real run on 2026-11-01..06.
    "Bosnian": (("bos",), 0.08),
}
ALLOWED_LANGS: frozenset[str] = frozenset(code for codes, _ in TRUSTED.values() for code in codes)

# For a headline with no language code (the English feed, or articles without the column): the
# scripts the trusted languages are written in. No Hangul, and none of the Indic scripts the model
# collapses; Hindi, Marathi and Gujarati are the Indic languages it was taught.
SAFE_SCRIPTS: frozenset[str] = frozenset(
    {
        "LATIN",
        "CYRILLIC",
        "GREEK",
        "ARABIC",
        "HEBREW",
        "ARMENIAN",
        "GEORGIAN",
        "THAI",
        "MYANMAR",
        "DEVANAGARI",
        "GUJARATI",
        "CJK",
        "JAPANESE",
    }
)


@cache
def _script_of_char(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    try:
        return unicodedata.name(ch).split()[0]
    except ValueError:  # a character without a name
        return None


def script_of(text: str) -> str:
    """The script most of the letters of `text` are written in ('LATIN', 'MALAYALAM', 'HANGUL',
    'CJK' for Chinese characters without kana, 'JAPANESE' when kana are present), or 'NONE'."""
    counts: Counter[str] = Counter()
    for ch in text:
        script = _script_of_char(ch)
        if script:
            counts[script] += 1
    if not counts:
        return "NONE"
    if counts["HIRAGANA"] or counts["KATAKANA"]:
        return "JAPANESE"
    return counts.most_common(1)[0][0]


def is_trusted(text: str, lang: str | None) -> bool:
    """Whether the model can be trusted with this headline.

    With a language code (the translated feed), the code decides. Without one the headline is from
    the English feed, so it has to look English: Latin letters."""
    if lang:
        return lang.lower() in ALLOWED_LANGS
    return script_of(text) == "LATIN"


def trusted_mask(texts: Sequence[str], langs: Sequence[str | None] | None) -> np.ndarray:
    """True for the headlines the model can be trusted with. `langs` is the language code of each
    headline, or None when the articles table has no language column: then the script decides,
    a coarser test that still keeps out Korean, the Indic scripts, Ethiopic, Khmer..."""
    if langs is None:
        return np.array([script_of(t) in SAFE_SCRIPTS for t in texts], dtype=bool)
    return np.array([is_trusted(t, lang) for t, lang in zip(texts, langs, strict=True)], dtype=bool)


def left_out_summary(
    texts: Sequence[str], langs: Sequence[str | None] | None, keep: np.ndarray, top: int = 6
) -> str:
    """'kor 9,812, ben 2,100, ...': what was left out, biggest first, by language code (or by script
    when there are no language codes)."""
    if langs is None:
        left = Counter(script_of(t) for t, ok in zip(texts, keep, strict=True) if not ok)
        label = "script "
    else:
        left = Counter((lang or "-") for lang, ok in zip(langs, keep, strict=True) if not ok)
        label = ""
    return ", ".join(f"{label}{code} {n:,}" for code, n in left.most_common(top))
