"""Which texts are worth embedding at all.

embed.clean_text() decides what a text becomes; this decides whether it is kept. Junk titles
("Home", "404 Not Found", "Subscribe to read") would otherwise form clusters of their own, and
posts of a couple of words match anything. Dropped items simply get no vector.

If these rules change, items that were dropped (or kept) change, but no stored vector does, so
TEXT_VERSION does not need to move.
"""

import re

from undercurrent.vectors.embed import Source

# Compared after lower-casing the cleaned text.
BOILERPLATE = {
    "home", "homepage", "index", "menu", "login", "log in", "sign in", "sign up", "register",
    "subscribe", "subscribe to read", "subscribe now", "404", "404 not found", "not found",
    "page not found", "403 forbidden", "forbidden", "access denied", "error", "just a moment...",
    "attention required!", "untitled", "search results", "cookie policy", "privacy policy",
    "terms of service", "advertisement",
}  # fmt: skip
# "404", "Error 404 - Not Found", "503 Service Unavailable"; but not "400 killed in attack".
_ERROR_PAGE = re.compile(
    r"^(error[\s:]*)?[45]\d\d\s*[-–:]?\s*(not found|forbidden|bad request|unauthori[sz]ed"
    r"|internal server error|service unavailable|bad gateway|gateway time-?out|page not found"
    r"|error)?\s*$",
    re.IGNORECASE,
)
_NO_SPACE_SCRIPT = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

# Minimum "words". Social posts need more to say anything about a story.
MIN_WORDS = {"news": 4, "wiki_events": 4, "wiki_new": 2, "bluesky": 6, "reddit": 6}


def effective_words(text: str) -> int:
    """Words, counting every two characters of Chinese, Japanese or Korean as a word (those
    scripts don't put spaces between words)."""
    return len(text.split()) + len(_NO_SPACE_SCRIPT.findall(text)) // 2


def is_usable(cleaned: str, source: Source) -> bool:
    """Should this (already cleaned) text get a vector?"""
    lowered = cleaned.lower()
    if not cleaned or lowered in BOILERPLATE or _ERROR_PAGE.match(cleaned):
        return False
    return effective_words(cleaned) >= MIN_WORDS.get(source, 4)
