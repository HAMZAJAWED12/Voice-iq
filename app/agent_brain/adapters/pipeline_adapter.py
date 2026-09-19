"""Adapt internal VoiceIQ pipeline outputs into an Agent Brain AgentContext.

Lets the standalone Agent Brain be driven from our own SessionInput +
InsightBundle + FactCheckResponse (instead of only the Java-shaped JSON).

Contract note: our fact-check Verdict and the Agent Brain's input
FactCheckStatus do not fully overlap — UNSUPPORTED_CLAIM_TYPE has no
Agent-Brain equivalent (mapped to UNVERIFIED) and NEEDS_REVIEW is never
produced by our engine. All other verdicts pass through unchanged.

`_VERDICT_TO_STATUS` is **total over `Verdict` and has no default**. A
default here is a trap rather than a safety net: an unmapped verdict would
be silently coerced to UNVERIFIED, which for a contradicting verdict means
the downstream FactCheckReviewAgent emits HIGH instead of CRITICAL at base
confidence 0.8 instead of 0.9 — a real finding quietly deprioritized, with
nothing raised anywhere. Indexing directly makes a gap loud, and
`test_verdict_map_is_total` makes it loud at test time rather than in
production. Add the entry when you add the verdict.
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
from app.insights.models.factcheck_models import FactCheckResponse, Verdict
from app.insights.models.input_models import SessionInput
from app.insights.models.insight_models import InsightBundle

# Keyed on Verdict (not str) so mypy rejects a key that is not a real
# verdict. Totality — every Verdict member present — is enforced by test.
_VERDICT_TO_STATUS: dict[Verdict, FactCheckStatus] = {
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
                    status=_VERDICT_TO_STATUS[result.verdict],
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
