"""Unit tests for the claim classifier (post-detection normalization)."""

from __future__ import annotations

from app.insights.core.factcheck.claim_detector import ClaimDetector
from app.insights.core.factcheck.classifier import ClaimClassifier
from app.insights.models.factcheck_models import (
    ClaimSpan,
    DetectedClaim,
)


def _detect_then_classify(text: str):
    return ClaimClassifier.classify(ClaimDetector.detect(text))


def test_classifier_normalizes_crypto_alias():
    claims = _detect_then_classify("Bitcoin is at 95000 dollars")
    assert len(claims) == 1
    assert claims[0].subject["asset"] == "BTC"


def test_classifier_normalizes_currency_pair():
    claims = _detect_then_classify("usd to pkr is 280")
    assert len(claims) == 1
    c = claims[0]
    assert c.subject["base"] == "USD"
    assert c.subject["quote"] == "PKR"
    assert c.subject["pair"] == "USD/PKR"


def test_classifier_supplies_default_weather_unit():
    claims = _detect_then_classify("temperature in Karachi is 32")
    assert len(claims) == 1
    assert claims[0].subject["unit"] == "C"


def test_classifier_drops_claims_missing_required_subject():
    # Manually construct a CRYPTO claim with an unknown asset.
    bad = DetectedClaim(
        claim_id="claim_1",
        text="foo is at 100",
        span=ClaimSpan(start=0, end=11),
        claim_type="CRYPTO_PRICE",
        raw_value=100.0,
        subject={"asset": "wat-coin"},
    )
    out = ClaimClassifier.classify([bad])
    assert out == []


def test_classifier_drops_currency_claim_with_same_base_and_quote():
    bad = DetectedClaim(
        claim_id="claim_1",
        text="USD to USD = 1",
        span=ClaimSpan(start=0, end=14),
        claim_type="CURRENCY_RATE",
        raw_value=1.0,
        subject={"base": "USD", "quote": "USD"},
    )
    assert ClaimClassifier.classify([bad]) == []


def test_classifier_renumbers_claim_ids_contiguously():
    text = "Bitcoin is at 95000 dollars. capital of France is Paris."
    claims = _detect_then_classify(text)
    assert [c.claim_id for c in claims] == ["claim_1", "claim_2"]


def test_classifier_preserves_static_fact_capital():
    claims = _detect_then_classify("capital of France is Paris")
    assert len(claims) == 1
    c = claims[0]
    assert c.subject["country"] == "France"
    assert c.raw_value_text == "Paris"
