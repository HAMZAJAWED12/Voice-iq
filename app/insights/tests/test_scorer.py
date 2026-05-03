"""Unit tests for FactCheckScorer (confidence assignment)."""

from __future__ import annotations

from app.insights.core.factcheck.scorer import FactCheckScorer


def test_true_zero_diff_is_high_one():
    c = FactCheckScorer.score("TRUE", 0.0)
    assert c.label == "HIGH"
    assert c.score == 1.0


def test_true_at_max_band_is_high_but_lower():
    c = FactCheckScorer.score("TRUE", 3.0)
    assert c.label == "HIGH"
    assert 0.84 <= c.score <= 0.86


def test_partial_at_low_end_is_medium():
    c = FactCheckScorer.score("PARTIALLY_TRUE", 3.5)
    assert c.label == "MEDIUM"
    assert 0.6 <= c.score <= 0.66


def test_partial_at_high_end_is_lower_medium():
    c = FactCheckScorer.score("PARTIALLY_TRUE", 10.0)
    assert c.label == "MEDIUM"
    assert 0.44 <= c.score <= 0.46


def test_false_just_above_band_is_high_low_score():
    c = FactCheckScorer.score("FALSE", 11.0)
    assert c.label == "HIGH"
    assert 0.69 <= c.score <= 0.74


def test_false_huge_diff_is_high_max_score():
    c = FactCheckScorer.score("FALSE", 100.0)
    assert c.label == "HIGH"
    assert c.score == 0.95  # capped at 30%+ over the partial band


def test_static_true_no_diff_is_high_one():
    c = FactCheckScorer.score("TRUE", None)
    assert c.label == "HIGH"
    assert c.score == 1.0


def test_static_false_no_diff_is_high_strong():
    c = FactCheckScorer.score("FALSE", None)
    assert c.label == "HIGH"
    assert c.score == 0.95


def test_unverified_is_none_zero():
    c = FactCheckScorer.score("UNVERIFIED", None)
    assert c.label == "NONE"
    assert c.score == 0.0


def test_source_unavailable_is_none_zero():
    c = FactCheckScorer.score("SOURCE_UNAVAILABLE", None)
    assert c.label == "NONE"
    assert c.score == 0.0


def test_unsupported_is_none_zero():
    c = FactCheckScorer.score("UNSUPPORTED_CLAIM_TYPE", None)
    assert c.label == "NONE"
    assert c.score == 0.0


def test_score_is_always_clamped():
    # Sanity: pathological inputs never escape [0, 1].
    for verdict in (
        "TRUE",
        "FALSE",
        "PARTIALLY_TRUE",
        "UNVERIFIED",
        "SOURCE_UNAVAILABLE",
        "UNSUPPORTED_CLAIM_TYPE",
    ):
        for diff in (-100.0, 0.0, 0.001, 1.5, 9.99, 50.0, 9999.0):
            c = FactCheckScorer.score(verdict, diff)
            assert 0.0 <= c.score <= 1.0
