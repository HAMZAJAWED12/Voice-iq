"""Coverage for the recommendation ranker."""

from __future__ import annotations

from app.agent_brain.core.ranker import rank
from app.agent_brain.models.recommendation import Recommendation, Source


def _rec(priority, confidence, title):
    return Recommendation(
        agent_type="TASK_AGENT",
        action_type="TASK",
        title=title,
        description="d",
        priority=priority,
        confidence=confidence,
        source=Source(text="t"),
        explanation="e",
    )


def test_priority_dominates_confidence() -> None:
    ranked = rank([_rec("HIGH", 0.99, "h"), _rec("CRITICAL", 0.10, "c")])
    assert [r.title for r in ranked] == ["c", "h"]


def test_confidence_breaks_ties_within_priority() -> None:
    ranked = rank([_rec("HIGH", 0.70, "low"), _rec("HIGH", 0.95, "high")])
    assert [r.title for r in ranked] == ["high", "low"]


def test_full_ordering() -> None:
    ranked = rank(
        [
            _rec("LOW", 0.99, "low"),
            _rec("CRITICAL", 0.50, "crit"),
            _rec("MEDIUM", 0.80, "med"),
            _rec("HIGH", 0.60, "high"),
        ]
    )
    assert [r.title for r in ranked] == ["crit", "high", "med", "low"]


def test_empty() -> None:
    assert rank([]) == []
