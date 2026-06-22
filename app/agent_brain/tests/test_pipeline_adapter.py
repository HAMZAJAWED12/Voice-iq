"""Coverage for the pipeline adapter (internal models -> AgentContext)."""

from __future__ import annotations

from app.agent_brain.adapters.pipeline_adapter import PipelineAdapter
from app.insights.models.factcheck_models import (
    ClaimSpan,
    Confidence,
    DetectedClaim,
    FactCheckResponse,
    FactCheckResult,
)
from app.insights.models.input_models import SessionInput, UtteranceInput
from app.insights.models.insight_models import InsightBundle, InsightFlag, InsightScores
from app.insights.models.signal_models import SentimentAggregate


def _session() -> SessionInput:
    return SessionInput(
        session_id="sess-1",
        utterances=[
            UtteranceInput(id="u1", speaker="A", start=72.0, end=85.0, text="Ali will prepare the report by Friday."),
        ],
    )


def test_adapter_maps_transcript_and_timestamps() -> None:
    ctx = PipelineAdapter.to_context(_session(), organization_id="org-1")
    assert ctx.session_id == "sess-1"
    assert ctx.organization_id == "org-1"
    seg = ctx.transcript[0]
    assert seg.segment_id == "u1"
    assert seg.speaker_id == "A"
    assert seg.start_time == "00:01:12"  # 72s
    assert seg.end_time == "00:01:25"  # 85s


def test_adapter_maps_insights() -> None:
    insights = InsightBundle(
        scores=InsightScores(),
        session_sentiment=SentimentAggregate(label="negative", sample_count=3),
        flags=[InsightFlag(type="high_tension", reason="r")],
    )
    ctx = PipelineAdapter.to_context(_session(), insights=insights, summary="A heated call.")
    assert ctx.insights.sentiment == "negative"
    assert ctx.insights.summary == "A heated call."
    assert ctx.insights.escalation_flags == ["high_tension"]


def test_adapter_maps_factchecks_and_unsupported_verdict() -> None:
    fc = FactCheckResponse(
        conversation_id="sess-1",
        speaker_id="A",
        fact_check_results=[
            FactCheckResult(
                claim=DetectedClaim(
                    claim_id="c1", text="ISO certified.", span=ClaimSpan(start=0, end=5), claim_type="STATIC_FACT"
                ),
                verdict="UNSUPPORTED_CLAIM_TYPE",
                confidence=Confidence(label="LOW", score=0.4),
                reason="unsupported",
            ),
        ],
    )
    ctx = PipelineAdapter.to_context(_session(), fact_check=fc)
    claim = ctx.fact_checks.claims[0]
    assert claim.claim_id == "c1"
    assert claim.claim_text == "ISO certified."
    assert claim.status == "UNVERIFIED"  # UNSUPPORTED_CLAIM_TYPE -> UNVERIFIED
    assert claim.confidence == 0.4


def test_adapter_minimal_session_only() -> None:
    ctx = PipelineAdapter.to_context(_session())
    assert ctx.insights.sentiment is None
    assert ctx.fact_checks.claims == []
    assert ctx.language == "en"
