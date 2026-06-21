"""Coverage for the Task Agent."""

from __future__ import annotations

from app.agent_brain.core.task_agent import TaskAgent
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment


def _ctx(*texts: str) -> AgentContext:
    segments = [TranscriptSegment(segment_id=f"seg-{i}", speaker_id="speaker_1", text=t) for i, t in enumerate(texts)]
    return AgentContext(session_id="s", transcript=segments)


def test_task_positive_with_assignee_and_deadline() -> None:
    recs = TaskAgent().detect(_ctx("Ali will prepare the report by Friday."))
    assert len(recs) == 1
    r = recs[0]
    assert r.agent_type == "TASK_AGENT"
    assert r.action_type == "TASK"
    assert r.entities.assignee == "Ali"
    assert r.entities.deadline_text == "by Friday"
    assert "prepare the report" in r.title.lower()
    assert r.source.text == "Ali will prepare the report by Friday."
    assert r.requires_approval is True
    assert r.explanation
    assert r.confidence == 0.9  # 0.55 + 0.20 (assignee) + 0.15 (deadline)


def test_task_positive_without_assignee() -> None:
    recs = TaskAgent().detect(_ctx("Please send the onboarding pack by Monday."))
    assert len(recs) == 1
    r = recs[0]
    assert r.entities.assignee is None
    assert r.entities.deadline_text == "by Monday"
    assert r.description.startswith("A task was identified:")


def test_task_negative_no_signal() -> None:
    assert TaskAgent().detect(_ctx("The weather is nice today.")) == []


def test_task_empty_and_blank_segments() -> None:
    assert TaskAgent().detect(AgentContext(session_id="s")) == []
    assert TaskAgent().detect(_ctx("   ")) == []


def test_task_multiple_segments_yield_multiple() -> None:
    recs = TaskAgent().detect(
        _ctx(
            "Sara will finish the migration.",
            "Nice talking to you.",
            "We need to update the pricing sheet.",
        )
    )
    assert len(recs) == 2  # segment 2 has no task signal


def test_task_priority_escalates_on_urgency() -> None:
    recs = TaskAgent().detect(_ctx("We need to fix the outage urgently."))
    assert recs[0].priority == "CRITICAL"


def test_title_falls_back_when_clause_strips_empty() -> None:
    # Stripping the leading subject+modal leaves nothing -> generic title.
    assert TaskAgent._title("will ", has_assignee=True) == "Follow-up task"
