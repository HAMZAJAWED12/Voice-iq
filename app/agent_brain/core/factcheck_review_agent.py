"""Fact-Check Review Agent: turn risky fact-check verdicts into review actions."""

from __future__ import annotations

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.models.agent_context import AgentContext, ContextClaim
from app.agent_brain.models.enums import FactCheckStatus, Priority
from app.agent_brain.models.recommendation import Recommendation, Source
from app.insights.core._math import clamp

# Only these verdicts warrant a manual-review recommendation.
_STATUS_WORD: dict[FactCheckStatus, str] = {
    "FALSE": "false",
    "UNVERIFIED": "unverified",
    "NEEDS_REVIEW": "flagged",
}
_STATUS_BASE_CONFIDENCE: dict[FactCheckStatus, float] = {
    "FALSE": 0.9,
    "NEEDS_REVIEW": 0.85,
    "UNVERIFIED": 0.8,
}


class FactCheckReviewAgent(BaseAgent):
    agent_type = "FACT_CHECK_REVIEW_AGENT"
    action_type = "FACT_CHECK"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        recommendations: list[Recommendation] = []

        for claim in context.fact_checks.claims:
            status = claim.status
            text = (claim.claim_text or "").strip()
            if status not in _STATUS_WORD or not text:
                continue

            word = _STATUS_WORD[status]
            recommendations.append(
                Recommendation(
                    agent_type=self.agent_type,
                    action_type=self.action_type,
                    title=f"Review {word} claim",
                    description=f'The claim "{text}" is {word} and should be reviewed manually.',
                    priority=self._priority(status),
                    confidence=self._confidence(status, claim),
                    linked_claim_id=claim.claim_id,
                    source=Source(text=text),
                    explanation="Fact-check verdict requires manual verification.",
                )
            )

        return recommendations

    @staticmethod
    def _priority(status: FactCheckStatus) -> Priority:
        return "CRITICAL" if status == "FALSE" else "HIGH"

    @staticmethod
    def _confidence(status: FactCheckStatus, claim: ContextClaim) -> float:
        base = _STATUS_BASE_CONFIDENCE[status]
        # A low-confidence claim is a stronger manual-review candidate.
        low = claim.confidence is not None and claim.confidence < 0.7
        return clamp(base + (0.05 if low else 0.0))
