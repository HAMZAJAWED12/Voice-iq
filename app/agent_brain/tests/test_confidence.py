"""Coverage for the confidence refinement module."""

from __future__ import annotations

from app.agent_brain.core.confidence import refine_confidence


def test_base_passthrough_when_no_factors() -> None:
    assert refine_confidence(base=0.8) == 0.8


def test_high_asr_boosts_low_asr_dampens() -> None:
    assert refine_confidence(base=0.8, asr_confidence=1.0) == 0.85  # +0.05 (bounded)
    assert refine_confidence(base=0.8, asr_confidence=0.4) == 0.75  # -0.05 (bounded floor)


def test_repetition_adds_capped() -> None:
    assert refine_confidence(base=0.8, repetition=1) == 0.8
    assert refine_confidence(base=0.8, repetition=2) == 0.83
    assert refine_confidence(base=0.8, repetition=3) == 0.86
    assert refine_confidence(base=0.8, repetition=9) == 0.86  # capped at +0.06


def test_fact_check_link_bonus() -> None:
    assert refine_confidence(base=0.8, fact_check_linked=True) == 0.85


def test_clamped_to_unit_interval() -> None:
    assert refine_confidence(base=0.99, asr_confidence=1.0, repetition=5, fact_check_linked=True) == 1.0
