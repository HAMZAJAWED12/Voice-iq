"""Date/time PHRASE extraction (Phase 1).

Extracts the phrase as spoken ("next Monday", "2 PM", "by Friday") and
stores it verbatim. Resolution to an absolute date is `datetime_resolver`
(N4a), which consumes the phrase this module produces.

N4b-1 Phase 1: the patterns moved into a language-keyed table. The `"en"`
entry is the pre-N4b triple, byte-identical. Urdu/Arabic weekday and
meridiem tokens are Phase 2, gated on decision **L1**; Arabic-Indic numeral
normalization (`٢` → `2`) is stdlib `unicodedata.digit()` and lands with
them, not before.
"""

from __future__ import annotations

import re
from re import Pattern
from typing import NamedTuple

from app.agent_brain.extraction.language import vocabulary_for

_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"


class _DatePatterns(NamedTuple):
    date_phrase: Pattern[str]
    bare_day: Pattern[str]
    time_phrase: Pattern[str]


# Add a language by adding a key. Do not edit the lookups below.
_PATTERNS: dict[str, _DatePatterns] = {
    "en": _DatePatterns(
        # Relative/qualified date phrases: "next Monday", "by Friday",
        # "this week", "before Tuesday", "on Thursday".
        date_phrase=re.compile(
            rf"\b((?:next|this|last|by|before|on|coming)\s+(?:{_WEEKDAYS}|week|month|tomorrow|today))\b",
            re.IGNORECASE,
        ),
        # Bare day words as a fallback: "tomorrow", "Monday".
        bare_day=re.compile(rf"\b((?:{_WEEKDAYS})|tomorrow|today|tonight)\b", re.IGNORECASE),
        # Clock times: "2 PM", "2:30pm", "10 am".
        time_phrase=re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", re.IGNORECASE),
    ),
}


def extract_date_phrase(text: str, *, language: str | None = None) -> str | None:
    """Return the date phrase as spoken, or None."""
    if not text:
        return None
    patterns = vocabulary_for(_PATTERNS, language)
    match = patterns.date_phrase.search(text) or patterns.bare_day.search(text)
    return match.group(1) if match else None


def extract_time_phrase(text: str, *, language: str | None = None) -> str | None:
    """Return the time phrase as spoken, or None."""
    if not text:
        return None
    match = vocabulary_for(_PATTERNS, language).time_phrase.search(text)
    return match.group(1) if match else None
