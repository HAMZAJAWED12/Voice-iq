"""End-to-end scenario coverage for the Agent Brain (doc 16 / 17).

Each test builds a representative AgentContext fixture and runs it through
the full AgentBrainService (agents -> confidence refine -> dedupe -> rank).
"""

from __future__ import annotations

from app.agent_brain.models.agent_context import (
    AgentContext,
    ContextClaim,
    ContextFactChecks,
    ContextInsights,
    TranscriptSegment,
)
from app.agent_brain.service import AgentBrainService


def _seg(sid: str, text: str, conf: float | None = None) -> TranscriptSegment:
    return TranscriptSegment(segment_id=sid, speaker_id="speaker_1", text=text, confidence=conf)


def _types(ctx: AgentContext) -> set[str]:
    return {r.action_type for r in AgentBrainService().generate(ctx).recommendations}


def test_scenario_sales_call_proposal_and_followup() -> None:
    # Doc 17 end-to-end.
    ctx = AgentContext(
        session_id="sales-1",
        transcript=[
            _seg("a", "Please send me the pricing proposal by Friday."),
            _seg("b", "Sure, I will send it. Let's also schedule a follow-up call next Monday."),
        ],
    )
    assert {"EMAIL", "CALENDAR"} <= _types(ctx)


def test_scenario_meeting_next_call_scheduling() -> None:
    ctx = AgentContext(session_id="m-1", transcript=[_seg("a", "Let's meet again next Tuesday at 3 PM.")])
    assert _types(ctx) == {"CALENDAR"}


def test_scenario_support_refund_escalation() -> None:
    ctx = AgentContext(
        session_id="sup-1",
        transcript=[_seg("a", "The customer is furious because the refund has been delayed for two weeks.")],
        insights=ContextInsights(sentiment="negative"),
    )
    recs = AgentBrainService().generate(ctx).recommendations
    assert any(r.action_type == "SUPPORT" for r in recs)
    assert recs[0].priority in {"HIGH", "CRITICAL"}  # escalation ranks at top


def test_scenario_interview_assessment_task() -> None:
    ctx = AgentContext(
        session_id="int-1",
        transcript=[_seg("a", "We will send you the assessment link; please complete it by Monday.")],
    )
    # An email (send link) and/or a task (complete by Monday) is expected.
    assert _types(ctx) & {"EMAIL", "TASK"}


def test_scenario_factcheck_manual_review() -> None:
    ctx = AgentContext(
        session_id="fc-1",
        fact_checks=ContextFactChecks(
            claims=[ContextClaim(claim_id="c1", claim_text="The product is ISO certified.", status="UNVERIFIED")]
        ),
    )
    recs = AgentBrainService().generate(ctx).recommendations
    assert len(recs) == 1
    assert recs[0].action_type == "FACT_CHECK"
    assert recs[0].linked_claim_id == "c1"


def test_scenario_no_actions() -> None:
    ctx = AgentContext(
        session_id="none-1",
        transcript=[_seg("a", "Nice weather today."), _seg("b", "Yes, lovely. How are you?")],
    )
    assert AgentBrainService().generate(ctx).recommendations == []


def test_scenario_duplicate_action_mentions_collapse() -> None:
    ctx = AgentContext(
        session_id="dup-1",
        transcript=[
            _seg("a", "Let's schedule another call."),
            _seg("b", "Yes, let's have another call."),
            _seg("c", "Okay, let's set up a call then."),
        ],
    )
    recs = AgentBrainService().generate(ctx).recommendations
    assert len(recs) == 1
    assert recs[0].action_type == "CALENDAR"


def test_scenario_low_confidence_asr_segment() -> None:
    high = AgentBrainService().generate(
        AgentContext(session_id="asr-h", transcript=[_seg("a", "Please send me the report.", conf=0.97)])
    )
    low = AgentBrainService().generate(
        AgentContext(session_id="asr-l", transcript=[_seg("a", "Please send me the report.", conf=0.25)])
    )
    assert high.recommendations[0].confidence > low.recommendations[0].confidence
