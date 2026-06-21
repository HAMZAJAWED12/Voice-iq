"""Date/time PHRASE extraction (Phase 1).

Phase 1 extracts the phrase as spoken ("next Monday", "2 PM", "by Friday")
and stores it verbatim — it does NOT resolve to an absolute datetime.
Resolution (timezone, locale, Urdu/Arabic phrasing) is a Phase 2 concern,
so no dateparser dependency is pulled in yet.
"""

from __future__ import annotations

import re

_WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"

# Relative/qualified date phrases: "next Monday", "by Friday", "this week",
# "before Tuesday", "on Thursday".
_DATE_PHRASE = re.compile(
    rf"\b((?:next|this|last|by|before|on|coming)\s+(?:{_WEEKDAYS}|week|month|tomorrow|today))\b",
    re.IGNORECASE,
)
# Bare day words as a fallback: "tomorrow", "Monday".
_BARE_DAY = re.compile(rf"\b((?:{_WEEKDAYS})|tomorrow|today|tonight)\b", re.IGNORECASE)

# Clock times: "2 PM", "2:30pm", "10 am".
_TIME_PHRASE = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", re.IGNORECASE)


def extract_date_phrase(text: str) -> str | None:
    """Return the date phrase as spoken, or None."""
    if not text:
        return None
    match = _DATE_PHRASE.search(text) or _BARE_DAY.search(text)
    return match.group(1) if match else None


def extract_time_phrase(text: str) -> str | None:
    """Return the time phrase as spoken, or None."""
    if not text:
        return None
    match = _TIME_PHRASE.search(text)
    return match.group(1) if match else None
