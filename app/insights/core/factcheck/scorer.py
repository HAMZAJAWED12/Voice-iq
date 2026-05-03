"""Confidence scoring for fact-check verdicts.

Inputs: the `Verdict` and the optional numeric `diff_pct` from the
comparator. Output: a `Confidence` object whose `score` is clamped to
[0.0, 1.0] and whose `label` is one of HIGH / MEDIUM / LOW / NONE.

Rationale:

  * TRUE with diff = 0       → HIGH, score 1.00
  * TRUE with small diff     → HIGH, score scales 1.00 → 0.85 across
                                the TRUE band (0% → 3%)
  * PARTIALLY_TRUE           → MEDIUM, score scales 0.65 → 0.45
                                across the partial band (3% → 10%)
  * FALSE with diff > 10%    → HIGH, score scales 0.70 → 0.95 as
                                diff grows (capped at 30%+ → 0.95)
  * SOURCE_UNAVAILABLE       → NONE, score 0.0
  * UNVERIFIED               → NONE, score 0.0
  * UNSUPPORTED_CLAIM_TYPE   → NONE, score 0.0
"""

from __future__ import annotations

from typing import Optional

from app.insights.core.factcheck.comparator import (
    PARTIAL_MAX_DIFF_PCT,
    TRUE_MAX_DIFF_PCT,
)
from app.insights.models.factcheck_models import (
    Confidence,
    ConfidenceLabel,
    Verdict,
)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp `value` to the [lo, hi] interval. Project standard helper."""
    return max(lo, min(hi, value))


class FactCheckScorer:
    """Stateless confidence scorer."""

    @classmethod
    def score(cls, verdict: Verdict, diff_pct: Optional[float]) -> Confidence:
        """Return a `Confidence` object for the given verdict + diff."""
        label, raw = cls._label_and_raw(verdict, diff_pct)
        return Confidence(label=label, score=_clamp(raw))

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _label_and_raw(
        verdict: Verdict, diff_pct: Optional[float]
    ) -> tuple[ConfidenceLabel, float]:
        if verdict == "TRUE":
            if diff_pct is None:
                # Static fact exact match - no numeric diff.
                return "HIGH", 1.0
            # Linear: 0% → 1.00, TRUE_MAX (3%) → 0.85
            span = TRUE_MAX_DIFF_PCT or 1.0
            return "HIGH", 1.0 - 0.15 * (diff_pct / span)

        if verdict == "PARTIALLY_TRUE" and diff_pct is not None:
            # Linear: TRUE_MAX (3%) → 0.65, PARTIAL_MAX (10%) → 0.45
            span = max(PARTIAL_MAX_DIFF_PCT - TRUE_MAX_DIFF_PCT, 1.0)
            normalized = (diff_pct - TRUE_MAX_DIFF_PCT) / span
            return "MEDIUM", 0.65 - 0.20 * normalized

        if verdict == "FALSE":
            if diff_pct is None:
                # Static-fact mismatch - high confidence the claim is wrong.
                return "HIGH", 0.95
            # Diff above PARTIAL_MAX (10%): scales 0.70 → 0.95 as diff grows.
            # Cap saturates at +20pp above PARTIAL_MAX, i.e. 30% diff.
            over = diff_pct - PARTIAL_MAX_DIFF_PCT
            normalized = _clamp(over / 20.0)
            return "HIGH", 0.70 + 0.25 * normalized

        # SOURCE_UNAVAILABLE / UNVERIFIED / UNSUPPORTED_CLAIM_TYPE
        return "NONE", 0.0
