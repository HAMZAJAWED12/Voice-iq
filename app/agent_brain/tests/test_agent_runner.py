"""Coverage for the agent runner + service (orchestration + fault isolation)."""

from __future__ import annotations

from app.agent_brain.core.agent_runner import AgentRunner, default_agents
from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.core.task_agent import TaskAgent
from app.agent_brain.models.agent_context import AgentContext, ContextClaim, ContextFactChecks, TranscriptSegment
from app.agent_brain.models.recommendation import Recommendation
from app.agent_brain.service import AgentBrainService


def _seg(sid, text, conf=None):
    return TranscriptSegment(segment_id=sid, speaker_id="s1", text=text, confidence=conf)


class _BoomAgent(BaseAgent):
    agent_type = "TASK_AGENT"
    action_type = "TASK"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        raise RuntimeError("boom")


def test_runner_end_to_end_ranked() -> None:
    ctx = AgentContext(
        session_id="s",
        transcript=[
            _seg("a", "Please send me the pricing proposal by Friday."),  # EMAIL
            _seg("b", "Let's schedule a follow-up call next Monday."),  # CALENDAR
        ],
    )
    recs = AgentRunner().run(ctx)
    types = {r.action_type for r in recs}
    assert {"EMAIL", "CALENDAR"} <= types
    # ranked: confidences non-increasing
    confs = [r.confidence for r in recs]
    assert confs == sorted(confs, reverse=True)


def test_per_agent_fault_isolation() -> None:
    # One agent raises; the others must still produce output.
    ctx = AgentContext(session_id="s", transcript=[_seg("a", "Let's schedule a follow-up call next Monday.")])
    runner = AgentRunner(agents=[_BoomAgent(), *[a for a in default_agents() if not isinstance(a, TaskAgent)]])
    recs = runner.run(ctx)
    assert any(r.action_type == "CALENDAR" for r in recs)  # FollowUp survived the boom


def test_repetition_boosts_confidence() -> None:
    one = AgentContext(session_id="s", transcript=[_seg("a", "Let's schedule another call.")])
    many = AgentContext(
        session_id="s",
        transcript=[
            _seg("a", "Let's schedule another call."),
            _seg("b", "Yes, let's have another call."),
            _seg("c", "Okay, let's set up a call."),
        ],
    )
    single_conf = AgentRunner().run(one)[0].confidence
    # 3 mentions dedupe to 1, but the survivor carries the repetition boost.
    deduped = AgentRunner().run(many)
    assert len(deduped) == 1
    assert deduped[0].confidence > single_conf


def test_low_asr_confidence_dampens() -> None:
    high = AgentRunner().run(AgentContext(session_id="s", transcript=[_seg("a", "Send me the report.", conf=1.0)]))
    low = AgentRunner().run(AgentContext(session_id="s", transcript=[_seg("a", "Send me the report.", conf=0.3)]))
    assert high[0].confidence > low[0].confidence


def test_factcheck_link_refine_path() -> None:
    ctx = AgentContext(
        session_id="s",
        fact_checks=ContextFactChecks(
            claims=[ContextClaim(claim_id="c1", claim_text="ISO certified.", status="UNVERIFIED")]
        ),
    )
    recs = AgentRunner().run(ctx)
    assert recs and recs[0].linked_claim_id == "c1"


def test_runner_empty_context() -> None:
    assert AgentRunner().run(AgentContext(session_id="s")) == []


def test_service_wraps_response() -> None:
    ctx = AgentContext(session_id="sess-9", transcript=[_seg("a", "Please send me the quotation.")])
    resp = AgentBrainService().generate(ctx)
    assert resp.session_id == "sess-9"
    assert resp.schema_version == "1.0"
    assert len(resp.recommendations) >= 1
