"""Rank recommendations by priority, then confidence (doc 14, step 8)."""

from __future__ import annotations

from app.agent_brain.models.recommendation import Recommendation

_PRIORITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}


def rank(recommendations: list[Recommendation]) -> list[Recommendation]:
    """Return recommendations ordered by priority (desc) then confidence (desc)."""
    return sorted(
        recommendations,
        key=lambda r: (_PRIORITY_RANK.get(r.priority, 0), r.confidence),
        reverse=True,
    )
