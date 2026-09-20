"""Best-effort assignee extraction (Phase 1: capitalized-name heuristics).

N4b-1 Phase 1: the patterns moved into a language-keyed table. The `"en"`
entry is the pre-N4b pair, byte-identical.

Known limit, recorded so nobody mistakes it for a vocabulary gap: these
patterns key on `[A-Z][a-z]+`, and Arabic script has no letter case. Urdu
and Arabic *names* therefore cannot be reached by adding vocabulary — that
is the one capability in N4b needing a model, tracked as **N4b-2**. Roman
Urdu is a different story and IS a vocabulary gap: "Ali kal tak report bhej
dega" already has a capitalized name and fails only because the verb
alternation below is English-only.
"""

from __future__ import annotations

import re
from re import Pattern
from typing import NamedTuple

from app.agent_brain.extraction.language import vocabulary_for


class _AssigneePatterns(NamedTuple):
    subject_verb: Pattern[str]
    assigned_to: Pattern[str]


# Add a language by adding a key. Do not edit the lookup below.
_PATTERNS: dict[str, _AssigneePatterns] = {
    "en": _AssigneePatterns(
        # "<Name> will / should / needs to / is going to ..."
        subject_verb=re.compile(
            r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(?:will|shall|should|must|needs to|has to|is going to)\b"
        ),
        # "assigned to <Name>"
        assigned_to=re.compile(r"\bassigned to\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", re.IGNORECASE),
    ),
}


def extract_assignee(text: str, *, language: str | None = None) -> str | None:
    """Return a likely assignee name, or None when none is clearly stated.

    Deliberately conservative: second-person requests ("can you send ...")
    have no named assignee and return None — Java/the user resolves those.

    `language` selects the pattern table; unknown or unpopulated languages
    fall back to English.
    """
    if not text:
        return None
    patterns = vocabulary_for(_PATTERNS, language)
    match = patterns.subject_verb.search(text)
    if match:
        return match.group(1).strip()
    match = patterns.assigned_to.search(text)
    if match:
        return match.group(1).strip()
    return None
