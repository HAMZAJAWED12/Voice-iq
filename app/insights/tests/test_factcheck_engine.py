"""Integration-style tests for the FactCheckEngine orchestrator.

Source clients are stubbed to avoid live network. The engine wires
detector → classifier → router → comparator → scorer end-to-end.
"""

from __future__ import annotations

from typing import Optional

from app.insights.core.factcheck.factcheck_engine import (
    FactCheckEngine,
    SourceRouter,
)
from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import (
    DetectedClaim,
    Evidence,
)


# --------------------------------------------------------------------------- #
# Stub clients                                                                #
# --------------------------------------------------------------------------- #

class _StubClient(BaseSourceClient):
    """Returns a pre-canned `Evidence` (or None) regardless of input."""

    def __init__(self, *, name: str, evidence: Optional[Evidence]) -> None:
        super().__init__(timeout_sec=1.0)
        self.name = name
        self._evidence = evidence
        self.call_count = 0

    def fetch(self, claim: DetectedClaim) -> Optional[Evidence]:
        self.call_count += 1
        return self._evidence


def _build_engine(
    *,
    crypto_evidence: Optional[Evidence] = None,
    static_evidence: Optional[Evidence] = None,
    forex_evidence: Optional[Evidence] = None,
):
    router = SourceRouter(
        {
            "CRYPTO_PRICE": _StubClient(name="crypto_stub", evidence=crypto_evidence),
            "STATIC_FACT": _StubClient(name="static_stub", evidence=static_evidence),
            "CURRENCY_RATE": _StubClient(name="forex_stub", evidence=forex_evidence),
        }
    )
    return FactCheckEngine(router=router, cache_ttl_sec=60.0)


# --------------------------------------------------------------------------- #
# Happy paths                                                                 #
# --------------------------------------------------------------------------- #

def test_engine_runs_end_to_end_for_crypto_true():
    engine = _build_engine(
        crypto_evidence=Evidence(source="crypto_stub", value=95000.0, unit="USD")
    )
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="Bitcoin is at 95000 dollars",
    )
    assert response.stats.claims_detected == 1
    assert response.stats.verified == 1
    assert response.fact_check_results[0].verdict == "TRUE"


def test_engine_handles_static_fact_match():
    engine = _build_engine(
        static_evidence=Evidence(source="static_stub", value_text="Paris")
    )
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="capital of France is Paris",
    )
    assert response.fact_check_results[0].verdict == "TRUE"
    assert response.fact_check_results[0].confidence.label == "HIGH"


def test_engine_marks_static_fact_mismatch_as_false():
    engine = _build_engine(
        static_evidence=Evidence(source="static_stub", value_text="Paris")
    )
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="capital of France is Tokyo",
    )
    assert response.fact_check_results[0].verdict == "FALSE"


# --------------------------------------------------------------------------- #
# Empty / no-claim inputs                                                     #
# --------------------------------------------------------------------------- #

def test_engine_returns_empty_for_no_claims():
    engine = _build_engine()
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="Hello, how are you today?",
    )
    assert response.fact_check_results == []
    assert response.stats.claims_detected == 0


# --------------------------------------------------------------------------- #
# Source-unavailable + unsupported branches                                   #
# --------------------------------------------------------------------------- #

def test_engine_emits_source_unavailable_when_client_returns_none():
    engine = _build_engine(crypto_evidence=None)
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="Bitcoin is at 95000 dollars",
    )
    assert response.fact_check_results[0].verdict == "SOURCE_UNAVAILABLE"
    assert response.stats.skipped == 1


def test_engine_emits_unsupported_for_missing_router_entry():
    # Build a router with NO STOCK_PRICE client, then submit a stock claim.
    router = SourceRouter({})
    engine = FactCheckEngine(router=router)
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="AAPL price 175.20",
    )
    assert response.fact_check_results[0].verdict == "UNSUPPORTED_CLAIM_TYPE"


# --------------------------------------------------------------------------- #
# Cache behaviour                                                             #
# --------------------------------------------------------------------------- #

def test_engine_caches_repeated_identical_claims_within_request():
    crypto_stub = _StubClient(
        name="crypto_stub",
        evidence=Evidence(source="crypto_stub", value=95000.0, unit="USD"),
    )
    router = SourceRouter({"CRYPTO_PRICE": crypto_stub})
    engine = FactCheckEngine(router=router, cache_ttl_sec=60.0)

    text = "Bitcoin is at 95000 dollars. Bitcoin is at 95000 dollars."
    response = engine.run(conversation_id="c1", speaker_id="s1", transcript_text=text)

    assert response.stats.claims_detected == 2
    # Both claims share the same cache_key → only one upstream fetch.
    assert crypto_stub.call_count == 1


def test_engine_does_not_crash_on_huge_payload_with_no_claims():
    engine = _build_engine()
    response = engine.run(
        conversation_id="c1",
        speaker_id="s1",
        transcript_text="lorem ipsum " * 500,
    )
    assert response.stats.claims_detected == 0
