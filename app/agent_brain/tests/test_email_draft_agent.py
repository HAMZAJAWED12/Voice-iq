"""Coverage for the Email Draft Agent."""

from __future__ import annotations

from app.agent_brain.core.email_draft_agent import EmailDraftAgent
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment


def _ctx(*texts: str) -> AgentContext:
    segs = [TranscriptSegment(segment_id=f"s{i}", speaker_id="speaker_1", text=t) for i, t in enumerate(texts)]
    return AgentContext(session_id="s", transcript=segs)


def test_email_positive_full() -> None:
    recs = EmailDraftAgent().detect(_ctx("Please send me the pricing proposal by Friday."))
    assert len(recs) == 1
    r = recs[0]
    assert r.agent_type == "EMAIL_DRAFT_AGENT"
    assert r.action_type == "EMAIL"
    assert r.title == "Send pricing proposal"
    assert r.suggested_payload["subject"] == "Pricing Proposal"
    assert r.suggested_payload["deadlineText"] == "by Friday"
    assert "Regards," in r.suggested_payload["bodyDraft"]
    assert r.priority == "HIGH"
    assert r.confidence == 0.9
    assert r.requires_approval is True


def test_email_object_fallback() -> None:
    # Signal present but no clean object captured -> generic object, lower conf.
    recs = EmailDraftAgent().detect(_ctx("Could you send over?"))
    assert len(recs) == 1
    assert recs[0].suggested_payload["subject"] == "The Requested Document".title()
    assert recs[0].confidence < 0.9


def test_email_no_deadline() -> None:
    recs = EmailDraftAgent().detect(_ctx("Send me the quotation."))
    assert recs[0].suggested_payload["deadlineText"] is None
    assert recs[0].title == "Send quotation"


def test_email_negative_and_empty() -> None:
    assert EmailDraftAgent().detect(_ctx("Good talking to you.")) == []
    assert EmailDraftAgent().detect(AgentContext(session_id="s")) == []
