"""Coverage for the pipeline adapter (internal models -> AgentContext)."""

from __future__ import annotations

from typing import get_args

import pytest

from app.agent_brain.adapters.pipeline_adapter import _VERDICT_TO_STATUS, PipelineAdapter
from app.agent_brain.models.enums import FactCheckStatus
from app.insights.models.factcheck_models import (
    ClaimSpan,
    Confidence,
    DetectedClaim,
    FactCheckResponse,
    FactCheckResult,
    Verdict,
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


# --------------------------------------------------------------------------- #
# Verdict map totality                                                        #
# --------------------------------------------------------------------------- #
#
# The adapter indexes `_VERDICT_TO_STATUS` directly instead of using a
# `.get(..., "UNVERIFIED")` default. mypy cannot see a *missing* dict key, so
# these tests are the only thing standing between a new verdict and a silent
# coercion to UNVERIFIED. Sprint 7 adds a second verdict set; this is the
# pattern it must follow.


def test_verdict_map_is_total() -> None:
    """Every Verdict member must have an entry. Fails the day one is added."""
    assert set(_VERDICT_TO_STATUS) == set(get_args(Verdict))


def test_verdict_map_targets_are_real_statuses() -> None:
    """Totality is worthless if the right-hand side drifts off the enum."""
    assert set(_VERDICT_TO_STATUS.values()) <= set(get_args(FactCheckStatus))


@pytest.mark.parametrize("verdict", get_args(Verdict))
def test_every_verdict_maps_without_a_default(verdict: str) -> None:
    """Drive each verdict end-to-end; a gap raises rather than degrading."""
    fc = FactCheckResponse(
        conversation_id="sess-1",
        speaker_id="A",
        fact_check_results=[
            FactCheckResult(
                claim=DetectedClaim(
                    claim_id="c1", text="A claim.", span=ClaimSpan(start=0, end=5), claim_type="STATIC_FACT"
                ),
                verdict=verdict,
                confidence=Confidence(label="LOW", score=0.4),
                reason="r",
            ),
        ],
    )
    ctx = PipelineAdapter.to_context(_session(), fact_check=fc)
    assert ctx.fact_checks.claims[0].status == _VERDICT_TO_STATUS[verdict]


def test_contradicting_verdict_is_not_downgraded() -> None:
    """The specific regression the default would have caused.

    FALSE must survive as FALSE. Coerced to UNVERIFIED it would drop the
    FactCheckReviewAgent from CRITICAL/0.9 to HIGH/0.8 with nothing logged.
    """
    assert _VERDICT_TO_STATUS["FALSE"] == "FALSE"
    assert _VERDICT_TO_STATUS["FALSE"] != "UNVERIFIED"


def test_unknown_verdict_raises_instead_of_degrading() -> None:
    """A verdict outside the literal is a bug, and must fail loudly.

    Pydantic validation makes this unreachable through normal construction,
    so `model_construct` is used to bypass it and prove the failure mode.
    """
    bad = FactCheckResult.model_construct(
        claim=DetectedClaim(claim_id="c1", text="A claim.", span=ClaimSpan(start=0, end=5), claim_type="STATIC_FACT"),
        verdict="CONTRADICTED",  # a Sprint 7 verdict, not in the v1 literal
        confidence=Confidence(label="LOW", score=0.4),
        reason="r",
    )
    fc = FactCheckResponse.model_construct(conversation_id="sess-1", speaker_id="A", fact_check_results=[bad])
    with pytest.raises(KeyError):
        PipelineAdapter.to_context(_session(), fact_check=fc)
