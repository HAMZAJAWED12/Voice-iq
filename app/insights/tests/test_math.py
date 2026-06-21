"""Unit coverage for the shared clamp helper (core/_math.py)."""

from __future__ import annotations

import pytest

from app.insights.core._math import clamp


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.5, 0.5), (-1.0, 0.0), (1.5, 1.0), (0.0, 0.0), (1.0, 1.0)],
)
def test_clamp_default_unit_range(value, expected) -> None:
    assert clamp(value) == expected


def test_clamp_custom_bounds() -> None:
    assert clamp(5.0, high=0.6) == 0.6
    assert clamp(-3.0, low=0.1) == 0.1
    assert clamp(0.4, low=0.1, high=0.6) == 0.4


def test_clamp_coerces_to_float() -> None:
    result = clamp(1)  # int in
    assert result == 1.0
    assert isinstance(result, float)
