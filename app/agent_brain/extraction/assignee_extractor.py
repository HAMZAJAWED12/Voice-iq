"""Best-effort assignee extraction (Phase 1: capitalized-name heuristics)."""

from __future__ import annotations

import re

# "<Name> will / should / needs to / is going to ..."
_SUBJECT_VERB = re.compile(
    r"\b([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)\s+(?:will|shall|should|must|needs to|has to|is going to)\b"
)
# "assigned to <Name>"
_ASSIGNED_TO = re.compile(r"\bassigned to\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", re.IGNORECASE)


def extract_assignee(text: str) -> str | None:
    """Return a likely assignee name, or None when none is clearly stated.

    Deliberately conservative: second-person requests ("can you send ...")
    have no named assignee and return None — Java/the user resolves those.
    """
    if not text:
        return None
    match = _SUBJECT_VERB.search(text)
    if match:
        return match.group(1).strip()
    match = _ASSIGNED_TO.search(text)
    if match:
        return match.group(1).strip()
    return None
