"""Adapt internal VoiceIQ pipeline outputs into an Agent Brain AgentContext.

Lets the standalone Agent Brain be driven from our own SessionInput +
InsightBundle + FactCheckResponse (instead of only the Java-shaped JSON).

Contract note: our fact-check Verdict and the Agent Brain's input
FactCheckStatus do not fully overlap — UNSUPPORTED_CLAIM_TYPE has no
Agent-Brain equivalent (mapped to UNVERIFIED) and NEEDS_REVIEW is never
produced by our engine. All other verdicts pass through unchanged.
"""

from __future__ import annotations

from app.agent_brain.models.agent_context import (
    AgentContext,
    ContextClaim,
    ContextFactChecks,
    ContextInsights,
    TranscriptSegment,
)
from app.agent_brain.models.enums import FactCheckStatus, LanguageCode
from app.insights.models.factcheck_models import FactCheckResponse
from app.insights.models.input_models import SessionInput
from app.insights.models.insight_models import InsightBundle

_VERDICT_TO_STATUS: dict[str, FactCheckStatus] = {
    "TRUE": "TRUE",
    "FALSE": "FALSE",
    "PARTIALLY_TRUE": "PARTIALLY_TRUE",
    "UNVERIFIED": "UNVERIFIED",
    "SOURCE_UNAVAILABLE": "SOURCE_UNAVAILABLE",
    "UNSUPPORTED_CLAIM_TYPE": "UNVERIFIED",  # no Agent-Brain equivalent
}


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


class PipelineAdapter:
    """Convert internal models into the Agent Brain input contract."""

    @staticmethod
    def to_context(
        session: SessionInput,
        *,
        insights: InsightBundle | None = None,
        fact_check: FactCheckResponse | None = None,
        summary: str | None = None,
        organization_id: str | None = None,
        language: LanguageCode = "en",
    ) -> AgentContext:
        transcript = [
            TranscriptSegment(
                segment_id=utt.id,
                speaker_id=utt.speaker,
                speaker_label=utt.speaker,
                start_time=_timestamp(utt.start),
                end_time=_timestamp(utt.end),
                text=utt.text,
                confidence=utt.confidence,
            )
            for utt in session.utterances
        ]

        context_insights = ContextInsights()
        if insights is not None:
            context_insights = ContextInsights(
                summary=summary,
                sentiment=insights.session_sentiment.label if insights.session_sentiment else None,
                escalation_flags=[flag.type for flag in insights.flags],
            )

        claims: list[ContextClaim] = []
        if fact_check is not None:
            claims = [
                ContextClaim(
                    claim_id=result.claim.claim_id,
                    claim_text=result.claim.text,
                    status=_VERDICT_TO_STATUS.get(result.verdict, "UNVERIFIED"),
                    confidence=result.confidence.score,
                )
                for result in fact_check.fact_check_results
            ]

        return AgentContext(
            session_id=session.session_id,
            organization_id=organization_id,
            language=language,
            transcript=transcript,
            insights=context_insights,
            fact_checks=ContextFactChecks(claims=claims),
        )
