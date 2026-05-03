"""Unit tests for the rule-based claim detector."""

from __future__ import annotations

from app.insights.core.factcheck.claim_detector import ClaimDetector


def test_detects_currency_rate():
    claims = ClaimDetector.detect("USD to PKR is 280")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "CURRENCY_RATE"
    assert c.raw_value == 280.0
    assert c.subject["pair"] == "USD/PKR"


def test_detects_crypto_price():
    claims = ClaimDetector.detect("Bitcoin is at 95000 dollars and trading well")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "CRYPTO_PRICE"
    assert c.raw_value == 95000.0
    assert c.subject["asset"] == "bitcoin"


def test_detects_stock_price():
    claims = ClaimDetector.detect("AAPL price 175.20 today")
    assert len(claims) == 1
    assert claims[0].claim_type == "STOCK_PRICE"
    assert claims[0].subject["symbol"] == "AAPL"
    assert claims[0].raw_value == 175.20


def test_detects_weather_with_unit():
    claims = ClaimDetector.detect("temperature in Karachi is 32 C")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "WEATHER"
    assert c.subject["city"] == "Karachi"
    assert c.unit == "C"
    assert c.raw_value == 32.0


def test_detects_static_fact():
    claims = ClaimDetector.detect("capital of France is Paris")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "STATIC_FACT"
    assert c.subject["country"] == "France"
    assert c.raw_value_text == "Paris"


def test_detects_multiple_claims_in_one_text():
    text = "Bitcoin is at 95000 dollars. capital of France is Paris."
    claims = ClaimDetector.detect(text)
    types = sorted(c.claim_type for c in claims)
    assert types == ["CRYPTO_PRICE", "STATIC_FACT"]


def test_empty_input_returns_no_claims():
    assert ClaimDetector.detect("") == []
    assert ClaimDetector.detect(None) == []
    assert ClaimDetector.detect("   \t\n   ") == []


def test_no_match_returns_no_claims():
    claims = ClaimDetector.detect("Hello, how are you today?")
    assert claims == []


def test_overlapping_matches_keep_only_first():
    # Crafted to make CRYPTO_PRICE the earlier match; STOCK alone shouldn't fire.
    text = "BTC price 95000"
    claims = ClaimDetector.detect(text)
    assert len(claims) == 1
    assert claims[0].claim_type == "CRYPTO_PRICE"


def test_long_input_is_truncated_safely():
    # Build text with a valid claim near the start; long tail should not crash.
    text = "Bitcoin is at 95000 dollars. " + ("padding " * 5000)
    claims = ClaimDetector.detect(text)
    assert any(c.claim_type == "CRYPTO_PRICE" for c in claims)
