"""A fact-check run checks at most 25 claims and stops starting checks at 20 s.

Claims past either limit are counted as skipped and get no result. Claims
are injected directly and source clients are stubs; nothing leaves the host.
"""

from __future__ import annotations

import pytest

from app.insights.api import factcheck_routes
from app.insights.config.settings import InsightSettings
from app.insights.core.factcheck import factcheck_engine
from app.insights.core.factcheck.factcheck_engine import FactCheckEngine, SourceRouter
from app.insights.core.factcheck.source_clients.base_client import BaseSourceClient
from app.insights.models.factcheck_models import ClaimSpan, DetectedClaim, Evidence


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _StubClient(BaseSourceClient):
    """Confirms every claim; each fetch takes `seconds` on `clock`."""

    name = "crypto_stub"

    def __init__(self, clock: _Clock | None = None, seconds: float = 0.0) -> None:
        super().__init__(timeout_sec=1.0)
        self._clock = clock
        self._seconds = seconds
        self.fetched: list[str] = []

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        self.fetched.append(claim.claim_id)
        if self._clock is not None:
            self._clock.now += self._seconds
        return Evidence(source=self.name, value=1.0, unit="USD")


def _claims(count: int) -> list[DetectedClaim]:
    return [
        DetectedClaim(
            claim_id=f"c{i}",
            text=f"coin{i} is at 1 dollar",
            span=ClaimSpan(start=0, end=1),
            claim_type="CRYPTO_PRICE",
            raw_value=1.0,
            subject={"asset": f"coin{i}", "vs_currency": "usd"},
        )
        for i in range(count)
    ]


@pytest.fixture
def detect(monkeypatch: pytest.MonkeyPatch):
    """Make the detector find `count` distinct claims."""

    def set_count(count: int) -> None:
        claims = _claims(count)
        monkeypatch.setattr(factcheck_engine.ClaimDetector, "detect", staticmethod(lambda _text: claims))
        monkeypatch.setattr(factcheck_engine.ClaimClassifier, "classify", staticmethod(lambda found: found))

    return set_count


def _run(engine: FactCheckEngine):
    return engine.run(conversation_id="conv", speaker_id="spk", transcript_text="synthetic")


def test_only_the_first_25_claims_are_checked(detect) -> None:
    detect(30)
    client = _StubClient()

    response = _run(FactCheckEngine(SourceRouter({"CRYPTO_PRICE": client})))

    assert client.fetched == [f"c{i}" for i in range(25)]
    assert [r.claim.claim_id for r in response.fact_check_results] == client.fetched
    assert response.stats.model_dump() == {"claims_detected": 30, "verified": 25, "skipped": 5}


def test_a_run_under_the_limits_checks_every_claim(detect) -> None:
    detect(5)
    client = _StubClient()

    response = _run(FactCheckEngine(SourceRouter({"CRYPTO_PRICE": client})))

    assert len(client.fetched) == 5
    assert response.stats.model_dump() == {"claims_detected": 5, "verified": 5, "skipped": 0}


def test_no_check_starts_after_the_deadline(detect) -> None:
    detect(5)
    clock = _Clock()
    client = _StubClient(clock, seconds=8.0)  # checks start at 0, 8 and 16 s; the fourth would start at 24 s

    response = _run(FactCheckEngine(SourceRouter({"CRYPTO_PRICE": client}), deadline_sec=20.0, clock=clock))

    assert client.fetched == ["c0", "c1", "c2"]
    assert len(response.fact_check_results) == 3
    assert response.stats.model_dump() == {"claims_detected": 5, "verified": 3, "skipped": 2}


def test_claims_without_a_source_count_toward_the_limit(detect) -> None:
    detect(3)

    response = _run(FactCheckEngine(SourceRouter({}), max_claims=2))

    assert [r.verdict for r in response.fact_check_results] == ["UNSUPPORTED_CLAIM_TYPE"] * 2
    assert response.stats.model_dump() == {"claims_detected": 3, "verified": 0, "skipped": 3}


def test_the_limit_defaults() -> None:
    settings = InsightSettings(_env_file=None)

    assert (settings.factcheck_max_claims, settings.factcheck_run_deadline_sec) == (25, 20.0)
    assert settings.factcheck_http_timeout_sec == 5.0


def test_the_service_engine_takes_its_limits_from_settings(monkeypatch: pytest.MonkeyPatch, detect) -> None:
    settings = InsightSettings(_env_file=None, factcheck_max_claims=2, factcheck_run_deadline_sec=7.0)
    monkeypatch.setattr(factcheck_routes, "get_settings", lambda: settings)
    factcheck_routes._build_engine.cache_clear()
    try:
        engine = factcheck_routes._build_engine()
    finally:
        factcheck_routes._build_engine.cache_clear()
    detect(3)
    client = _StubClient()
    monkeypatch.setattr(engine, "_router", SourceRouter({"CRYPTO_PRICE": client}))

    response = _run(engine)

    assert client.fetched == ["c0", "c1"]
    assert response.stats.skipped == 1
    assert engine._deadline_sec == 7.0
