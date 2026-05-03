"""Unit tests for FactCheckComparator."""

from __future__ import annotations

from app.insights.core.factcheck.comparator import FactCheckComparator
from app.insights.models.factcheck_models import (
    ClaimSpan,
    DetectedClaim,
    Evidence,
)


def _numeric_claim(claimed: float):
    return DetectedClaim(
        claim_id="c1",
        text="x",
        span=ClaimSpan(start=0, end=1),
        claim_type="CRYPTO_PRICE",
        raw_value=claimed,
        subject={"asset": "BTC"},
    )


def _static_claim(claimed_text: str, country: str = "France"):
    return DetectedClaim(
        claim_id="c1",
        text="x",
        span=ClaimSpan(start=0, end=1),
        claim_type="STATIC_FACT",
        raw_value_text=claimed_text,
        subject={"country": country},
    )


def _evidence(value=None, value_text=None):
    return Evidence(source="test", value=value, value_text=value_text)


# --------------------------------------------------------------------------- #
# Numeric verdict bands                                                       #
# --------------------------------------------------------------------------- #

def test_numeric_zero_diff_is_true():
    out = FactCheckComparator.compare(_numeric_claim(100.0), _evidence(value=100.0))
    assert out.verdict == "TRUE"
    assert out.diff_pct == 0.0


def test_numeric_within_3_pct_is_true():
    out = FactCheckComparator.compare(_numeric_claim(102.0), _evidence(value=100.0))
    assert out.verdict == "TRUE"
    assert out.diff_pct == 2.0


def test_numeric_between_3_and_10_is_partial():
    out = FactCheckComparator.compare(_numeric_claim(95.0), _evidence(value=100.0))
    assert out.verdict == "PARTIALLY_TRUE"
    assert out.diff_pct == 5.0


def test_numeric_above_10_pct_is_false():
    out = FactCheckComparator.compare(_numeric_claim(150.0), _evidence(value=100.0))
    assert out.verdict == "FALSE"
    assert out.diff_pct == 50.0


def test_numeric_boundary_at_3_pct_is_true():
    out = FactCheckComparator.compare(_numeric_claim(103.0), _evidence(value=100.0))
    assert out.verdict == "TRUE"
    assert out.diff_pct == 3.0


def test_numeric_boundary_at_10_pct_is_partial():
    out = FactCheckComparator.compare(_numeric_claim(110.0), _evidence(value=100.0))
    assert out.verdict == "PARTIALLY_TRUE"
    assert out.diff_pct == 10.0


# --------------------------------------------------------------------------- #
# Defensive branches                                                          #
# --------------------------------------------------------------------------- #

def test_missing_evidence_is_source_unavailable():
    out = FactCheckComparator.compare(_numeric_claim(100.0), None)
    assert out.verdict == "SOURCE_UNAVAILABLE"
    assert out.diff_pct is None


def test_zero_actual_value_is_unverified():
    out = FactCheckComparator.compare(_numeric_claim(100.0), _evidence(value=0.0))
    assert out.verdict == "UNVERIFIED"


def test_missing_claim_value_is_unverified():
    claim = DetectedClaim(
        claim_id="c1",
        text="x",
        span=ClaimSpan(start=0, end=1),
        claim_type="CRYPTO_PRICE",
        raw_value=None,
        subject={"asset": "BTC"},
    )
    out = FactCheckComparator.compare(claim, _evidence(value=100.0))
    assert out.verdict == "UNVERIFIED"


# --------------------------------------------------------------------------- #
# Static-fact comparison                                                      #
# --------------------------------------------------------------------------- #

def test_static_fact_exact_match_is_true():
    out = FactCheckComparator.compare(
        _static_claim("Paris"), _evidence(value_text="Paris")
    )
    assert out.verdict == "TRUE"
    assert out.diff_pct is None


def test_static_fact_case_and_accent_insensitive_match():
    out = FactCheckComparator.compare(
        _static_claim("sao paulo"), _evidence(value_text="São Paulo")
    )
    assert out.verdict == "TRUE"


def test_static_fact_mismatch_is_false():
    out = FactCheckComparator.compare(
        _static_claim("Tokyo"), _evidence(value_text="Paris")
    )
    assert out.verdict == "FALSE"


def test_static_fact_missing_either_text_is_unverified():
    out = FactCheckComparator.compare(_static_claim("Paris"), _evidence(value_text=""))
    assert out.verdict == "UNVERIFIED"
