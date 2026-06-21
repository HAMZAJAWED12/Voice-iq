"""Shared numeric helpers for the insight engines.

CLAUDE.md standard #5 mandates uniform score clamping. This module is the
single source of truth — engines import `clamp` rather than each defining
their own.
"""

from __future__ import annotations


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    """Coerce `value` to float and constrain it to the closed [low, high] range."""
    return max(low, min(high, float(value)))
