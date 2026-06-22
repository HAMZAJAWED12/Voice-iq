"""Coverage for the Escalation Agent."""

from __future__ import annotations

from app.agent_brain.core.escalation_agent import EscalationAgent
from app.agent_brain.models.agent_context import AgentContext, ContextInsights, TranscriptSegment

_UPSET = "The customer is very upset because the refund has been delayed for two weeks."


def _ctx(*texts: str, sentiment: str | None = None, flags: list[str] | None = None) -> AgentContext:
    segs = [TranscriptSegment(segment_id=f"s{i}", speaker_id="speaker_1", text=t) for i, t in enumerate(texts)]
    insights = ContextInsights(sentiment=sentiment, escalation_flags=flags or [])
    return AgentContext(session_id="s", transcript=segs, insights=insights)


def test_escalation_positive_refund_delay() -> None:
    recs = EscalationAgent().detect(_ctx(_UPSET))
    assert len(recs) == 1
    r = recs[0]
    assert r.agent_type == "ESCALATION_AGENT"
    assert r.action_type == "SUPPORT"
    assert r.title == "Escalate delayed refund issue"
    assert r.priority == "HIGH"
    assert r.entities.topic == "refund"
    assert "detected" in r.suggested_payload["riskReason"]
    assert r.suggested_payload["ticketTitle"] == "Delayed refund escalation"
    assert r.confidence == 0.85  # anger + complaint, no session boost
    assert r.requires_approval is True


def test_escalation_session_sentiment_boosts_confidence() -> None:
    base = EscalationAgent().detect(_ctx(_UPSET))[0].confidence
    boosted = EscalationAgent().detect(_ctx(_UPSET, sentiment="negative"))[0].confidence
    assert boosted > base
    flagged = EscalationAgent().detect(_ctx(_UPSET, flags=["HIGH_TENSION"]))[0].confidence
    assert flagged > base


def test_escalation_explicit_risk_cue() -> None:
    recs = EscalationAgent().detect(_ctx("I want to escalate this to your manager."))
    assert len(recs) == 1
    assert recs[0].entities.topic == "service"


def test_escalation_billing_subject() -> None:
    recs = EscalationAgent().detect(_ctx("I'm furious about the wrong billing charge."))
    assert len(recs) == 1
    assert recs[0].entities.topic == "billing"


def test_escalation_skips_blank_segment() -> None:
    recs = EscalationAgent().detect(_ctx("   ", _UPSET))
    assert len(recs) == 1


def test_escalation_negative_and_empty() -> None:
    assert EscalationAgent().detect(_ctx("Everything is great, thank you!")) == []
    assert EscalationAgent().detect(AgentContext(session_id="s")) == []
