"""Agent runner: fan out to the agents, then score/dedupe/rank (doc 14)."""

from __future__ import annotations

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.core.confidence import refine_confidence
from app.agent_brain.core.deduplication import deduplicate, duplicate_counts
from app.agent_brain.core.email_draft_agent import EmailDraftAgent
from app.agent_brain.core.escalation_agent import EscalationAgent
from app.agent_brain.core.factcheck_review_agent import FactCheckReviewAgent
from app.agent_brain.core.followup_agent import FollowUpAgent
from app.agent_brain.core.ranker import rank
from app.agent_brain.core.task_agent import TaskAgent
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import Recommendation
from app.utils.logger import logger


def default_agents() -> list[BaseAgent]:
    return [
        TaskAgent(),
        FollowUpAgent(),
        EmailDraftAgent(),
        EscalationAgent(),
        FactCheckReviewAgent(),
    ]


class AgentRunner:
    """Runs every agent with per-agent fault isolation, then refines/dedups/ranks.

    Fault-isolation contract (scoped to this multi-agent fan-out): a single
    agent raising must NOT sink the others — it is logged and skipped, and the
    partial set of recommendations is still returned. This is intentionally
    different from the insight pipeline's rule_engine, which raises and lets
    the service catch (see CLAUDE.md "Orchestration + fault isolation").
    """

    def __init__(self, agents: list[BaseAgent] | None = None) -> None:
        self.agents = agents if agents is not None else default_agents()

    def run(self, context: AgentContext) -> list[Recommendation]:
        candidates = self._collect(context)
        # Both of these used to be recomputed once per candidate inside
        # _refine — the repetition sweep quadratically, over difflib. Hoisting
        # them changes cost, not results (L5-0).
        repetitions = duplicate_counts(candidates)
        asr_by_segment = {seg.segment_id: seg.confidence for seg in context.transcript if seg.segment_id}
        refined = [self._refine(rec, asr_by_segment, repetitions[index]) for index, rec in enumerate(candidates)]
        return rank(deduplicate(refined))

    def _collect(self, context: AgentContext) -> list[Recommendation]:
        candidates: list[Recommendation] = []
        for agent in self.agents:
            try:
                candidates.extend(agent.detect(context))
            except Exception as exc:  # noqa: BLE001 - per-agent fault isolation
                logger.warning("agent_brain: agent %s failed (%s)", getattr(agent, "agent_type", "?"), exc)
        return candidates

    @staticmethod
    def _refine(
        rec: Recommendation,
        asr_by_segment: dict[str, float | None],
        duplicates: int,
    ) -> Recommendation:
        asr = asr_by_segment.get(rec.source.segment_id) if rec.source.segment_id else None
        repetition = 1 + duplicates
        confidence = refine_confidence(
            base=rec.confidence,
            asr_confidence=asr,
            repetition=repetition,
            fact_check_linked=rec.linked_claim_id is not None,
        )
        return rec.model_copy(update={"confidence": confidence})
