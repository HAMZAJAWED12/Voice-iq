"""Recommendation deduplication (doc 10), stdlib difflib only.

"Let's meet Monday." / "Monday works." / "Okay, let's schedule it." should
yield ONE calendar recommendation, not three. We collapse near-duplicates
within the same action type, keeping the highest-confidence survivor.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from app.agent_brain.models.recommendation import Recommendation

# Title similarity above which two same-action recommendations are duplicates.
_TITLE_SIMILARITY = 0.82


def _titles_similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= _TITLE_SIMILARITY


def _is_duplicate(candidate: Recommendation, kept: Recommendation) -> bool:
    if candidate.action_type != kept.action_type:
        return False
    # Same fact-check claim.
    if candidate.linked_claim_id and candidate.linked_claim_id == kept.linked_claim_id:
        return True
    # Same person + same deadline.
    if (
        candidate.entities.assignee
        and candidate.entities.assignee == kept.entities.assignee
        and candidate.entities.deadline_text == kept.entities.deadline_text
    ):
        return True
    # Same source segment.
    if candidate.source.segment_id and candidate.source.segment_id == kept.source.segment_id:
        return True
    # Similar title.
    return _titles_similar(candidate.title, kept.title)


def deduplicate(recommendations: list[Recommendation]) -> list[Recommendation]:
    """Collapse near-duplicate recommendations, keeping the strongest of each."""
    kept: list[Recommendation] = []
    # Highest confidence first so the survivor of each duplicate group is the
    # most confident one.
    for candidate in sorted(recommendations, key=lambda r: r.confidence, reverse=True):
        if not any(_is_duplicate(candidate, k) for k in kept):
            kept.append(candidate)
    return kept
