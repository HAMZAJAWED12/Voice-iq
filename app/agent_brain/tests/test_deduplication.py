"""Coverage for recommendation deduplication."""

from __future__ import annotations

from app.agent_brain.core.deduplication import deduplicate
from app.agent_brain.core.followup_agent import FollowUpAgent
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment
from app.agent_brain.models.recommendation import Entities, Recommendation, Source


def _rec(action_type, title, confidence, *, assignee=None, deadline=None, claim=None, segment_id="seg"):
    return Recommendation(
        agent_type="TASK_AGENT",
        action_type=action_type,
        title=title,
        description="d",
        confidence=confidence,
        linked_claim_id=claim,
        source=Source(segment_id=segment_id, text="t"),
        entities=Entities(assignee=assignee, deadline_text=deadline),
        explanation="e",
    )


def test_dedup_collapses_repeated_followup_mentions() -> None:
    ctx = AgentContext(
        session_id="s",
        transcript=[
            TranscriptSegment(segment_id="a", speaker_id="s1", text="Let's meet again next Monday."),
            TranscriptSegment(segment_id="b", speaker_id="s2", text="Monday works, let's schedule another call."),
            TranscriptSegment(segment_id="c", speaker_id="s1", text="Okay, let's have another call then."),
        ],
    )
    recs = FollowUpAgent().detect(ctx)  # 3 candidates, identical titles
    assert len(recs) == 3
    assert len(deduplicate(recs)) == 1


def test_dedup_keeps_highest_confidence() -> None:
    kept = deduplicate(
        [
            _rec("CALENDAR", "Schedule follow-up call", 0.70, segment_id="x"),
            _rec("CALENDAR", "Schedule follow-up call", 0.92, segment_id="y"),
        ]
    )
    assert len(kept) == 1
    assert kept[0].confidence == 0.92


def test_dedup_different_action_types_preserved() -> None:
    recs = [_rec("EMAIL", "Send proposal", 0.8), _rec("CALENDAR", "Schedule call", 0.8)]
    assert len(deduplicate(recs)) == 2


def test_dedup_same_linked_claim() -> None:
    recs = [
        _rec("FACT_CHECK", "Review unverified claim", 0.8, claim="c1", segment_id=None),
        _rec("FACT_CHECK", "Review flagged claim", 0.85, claim="c1", segment_id=None),
    ]
    assert len(deduplicate(recs)) == 1


def test_dedup_same_assignee_and_deadline() -> None:
    recs = [
        _rec("TASK", "Prepare report", 0.8, assignee="Ali", deadline="by Friday", segment_id="1"),
        _rec("TASK", "Compile the report document", 0.7, assignee="Ali", deadline="by Friday", segment_id="2"),
    ]
    assert len(deduplicate(recs)) == 1


def test_dedup_same_source_segment_dissimilar_titles() -> None:
    # Same action type + same source segment -> duplicate even when titles
    # differ and there is no assignee/claim to match on.
    recs = [
        _rec("TASK", "Alpha thing", 0.8, segment_id="seg-1"),
        _rec("TASK", "Completely different wording here", 0.7, segment_id="seg-1"),
    ]
    assert len(deduplicate(recs)) == 1


def test_dedup_empty_and_single() -> None:
    assert deduplicate([]) == []
    one = [_rec("TASK", "x", 0.8)]
    assert deduplicate(one) == one
