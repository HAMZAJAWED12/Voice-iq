"""Coverage for the Follow-Up Meeting Agent."""

from __future__ import annotations

from app.agent_brain.core.followup_agent import FollowUpAgent
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment


def _ctx(*texts: str) -> AgentContext:
    segs = [TranscriptSegment(segment_id=f"s{i}", speaker_id="speaker_1", text=t) for i, t in enumerate(texts)]
    return AgentContext(session_id="s", transcript=segs)


def test_followup_positive_full_payload() -> None:
    recs = FollowUpAgent().detect(_ctx("Let's have another call next Monday at 2 PM."))
    assert len(recs) == 1
    r = recs[0]
    assert r.agent_type == "FOLLOW_UP_AGENT"
    assert r.action_type == "CALENDAR"
    assert r.title == "Schedule follow-up call"
    assert r.priority == "HIGH"
    assert r.suggested_payload["dateText"] == "next Monday"
    assert r.suggested_payload["timeText"] == "2 PM"
    assert r.suggested_payload["durationMinutes"] == 30
    assert r.confidence == 0.9  # 0.6 + 0.18 + 0.12
    assert r.requires_approval is True


def test_followup_parses_explicit_duration() -> None:
    recs = FollowUpAgent().detect(_ctx("Let's sync up for 45 minutes next week."))
    assert recs[0].suggested_payload["durationMinutes"] == 45


def test_followup_parses_hour_duration() -> None:
    recs = FollowUpAgent().detect(_ctx("Let's meet again for an hour."))
    assert recs[0].suggested_payload["durationMinutes"] == 60


def test_followup_no_date_time_still_recommends() -> None:
    recs = FollowUpAgent().detect(_ctx("We should catch up soon."))
    assert len(recs) == 1
    assert recs[0].suggested_payload["dateText"] is None
    assert recs[0].suggested_payload["durationMinutes"] == 30


def test_followup_negative_and_empty() -> None:
    assert FollowUpAgent().detect(_ctx("Thanks for the call today.")) == []
    assert FollowUpAgent().detect(AgentContext(session_id="s")) == []
