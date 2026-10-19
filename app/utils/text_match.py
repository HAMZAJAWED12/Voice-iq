"""Whole-word and whole-phrase matching for keyword rules.

A keyword matches only where it is not part of a longer word: "um" is found
in "um, sure" but not in "number". An edge of the phrase that is a word
character must not touch another word character; an edge that is
punctuation ("...", "?") matches wherever it appears. Matching is
case-sensitive, so callers lower both sides first.
"""

from __future__ import annotations

import re
from functools import lru_cache

_WORD_CHAR = re.compile(r"\w")


@lru_cache(maxsize=1024)
def _pattern(phrase: str) -> re.Pattern[str]:
    head = r"(?<!\w)" if _WORD_CHAR.match(phrase[0]) else ""
    tail = r"(?!\w)" if _WORD_CHAR.match(phrase[-1]) else ""
    return re.compile(head + re.escape(phrase) + tail)


def contains_phrase(text: str, phrase: str) -> bool:
    """True if `phrase` occurs in `text` as whole words."""
    return bool(phrase) and _pattern(phrase).search(text) is not None


def starts_with_phrase(text: str, phrase: str) -> bool:
    """True if `text` begins with `phrase` as whole words."""
    return bool(phrase) and _pattern(phrase).match(text) is not None
