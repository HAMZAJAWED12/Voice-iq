"""Comparator.

Pure logic. Given a `DetectedClaim` and an `Evidence` instance, decide a
``Verdict``, an optional numeric ``diff_pct``, and a human-readable
``reason`` string. No I/O, no randomness, fully deterministic.

Numeric comparison bands (per task spec):

  | abs((claimed - actual) / actual) * 100 | Verdict          |
  |----------------------------------------|------------------|
  | 0   ≤ diff ≤ 3                         | TRUE             |
  | 3   <  diff ≤ 10                       | PARTIALLY_TRUE   |
  |       diff > 10                        | FALSE            |

Static-fact comparison: case- and accent-insensitive string equality, via
``StaticFactsClient.matches``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from app.insights.core.factcheck.source_clients.static_facts_client import (
    StaticFactsClient,
)
from app.insights.models.factcheck_models import (
    DetectedClaim,
    Evidence,
    Verdict,
)


# Verdict band thresholds. Centralised here so the scorer can reuse them
# without re-deriving the boundaries.
TRUE_MAX_DIFF_PCT: float = 3.0
PARTIAL_MAX_DIFF_PCT: float = 10.0


@dataclass(frozen=True)
class ComparisonOutcome:
    """Output triple from the comparator."""

    verdict: Verdict
    diff_pct: Optional[float]
    reason: str


class FactCheckComparator:
    """Stateless comparator. Single public entrypoint: ``compare``."""

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def compare(
        cls,
        claim: DetectedClaim,
        evidence: Optional[Evidence],
    ) -> ComparisonOutcome:
        """Decide the verdict for ``claim`` given ``evidence``.

        Branches:
          * Evidence missing                    → SOURCE_UNAVAILABLE
          * STATIC_FACT (string compare)        → TRUE / FALSE
          * Numeric claim, value missing        → UNVERIFIED
          * Numeric claim, evidence non-numeric → UNVERIFIED
          * Numeric claim, ground truth = 0     → UNVERIFIED (no division)
          * Otherwise                           → numeric band → verdict
        """
        if evidence is None:
            return ComparisonOutcome(
                verdict="SOURCE_UNAVAILABLE",
                diff_pct=None,
                reason="No evidence returned by source client.",
            )

        if claim.claim_type == "STATIC_FACT":
            return cls._compare_static_fact(claim, evidence)

        return cls._compare_numeric(claim, evidence)

    # ------------------------------------------------------------------ #
    # Internals                                                          #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _compare_static_fact(
        claim: DetectedClaim, evidence: Evidence
    ) -> ComparisonOutcome:
        claimed = (claim.raw_value_text or "").strip()
        actual = (evidence.value_text or "").strip()
        if not claimed or not actual:
            return ComparisonOutcome(
                verdict="UNVERIFIED",
                diff_pct=None,
                reason="Missing claimed or actual text for static fact.",
            )
        if StaticFactsClient.matches(claimed, actual):
            return ComparisonOutcome(
                verdict="TRUE",
                diff_pct=None,
                reason=f"Claimed '{claimed}' matches source value '{actual}'.",
            )
        return ComparisonOutcome(
            verdict="FALSE",
            diff_pct=None,
            reason=f"Claimed '{claimed}' does not match source value '{actual}'.",
        )

    @classmethod
    def _compare_numeric(
        cls, claim: DetectedClaim, evidence: Evidence
    ) -> ComparisonOutcome:
        claimed = claim.raw_value
        actual = evidence.value
        if claimed is None or actual is None:
            return ComparisonOutcome(
                verdict="UNVERIFIED",
                diff_pct=None,
                reason="Numeric claim or evidence value is missing.",
            )
        if actual == 0:
            return ComparisonOutcome(
                verdict="UNVERIFIED",
                diff_pct=None,
                reason="Source value is zero; relative diff undefined.",
            )

        diff_pct = round(abs(claimed - actual) / abs(actual) * 100.0, 2)

        if diff_pct <= TRUE_MAX_DIFF_PCT:
            verdict: Verdict = "TRUE"
            reason = (
                f"Diff {diff_pct:.2f}% within 0-{TRUE_MAX_DIFF_PCT:.0f}% band → TRUE."
            )
        elif diff_pct <= PARTIAL_MAX_DIFF_PCT:
            verdict = "PARTIALLY_TRUE"
            reason = (
                f"Diff {diff_pct:.2f}% within "
                f"{TRUE_MAX_DIFF_PCT:.0f}-{PARTIAL_MAX_DIFF_PCT:.0f}% band → PARTIALLY_TRUE."
            )
        else:
            verdict = "FALSE"
            reason = (
                f"Diff {diff_pct:.2f}% exceeds {PARTIAL_MAX_DIFF_PCT:.0f}% threshold → FALSE."
            )

        return ComparisonOutcome(verdict=verdict, diff_pct=diff_pct, reason=reason)
