"""Confidence refinement (doc 11).

REFINE pattern: each agent emits a provisional confidence reflecting
*entity completeness* (what that agent could extract). This module blends
in the remaining, context-level factors the agent does not see and the
runner applies it to override the provisional value:

  * ASR confidence  - was the underlying transcript segment reliable?
  * Repetition      - was the same action confirmed multiple times?
  * Fact-check risk - is the action tied to a risky claim?

(Explicitness and speaker clarity are already folded into the agents'
provisional score; they remain documented factors here for completeness.)
All blending is additive then clamped to [0, 1] via the shared helper.
"""

from __future__ import annotations

from app.insights.core._math import clamp

# ASR factor is centered on 0.8: above lifts, below dampens, bounded +/-0.05.
_ASR_CENTER = 0.8
_ASR_GAIN = 0.25
_ASR_BOUND = 0.05
# Each extra confirmation adds a little, capped at +0.06 (3+ mentions).
_REPEAT_STEP = 0.03
_REPEAT_CAP_EXTRAS = 2
# Tie to a risky fact-check claim.
_FACT_CHECK_BONUS = 0.05


def refine_confidence(
    *,
    base: float,
    asr_confidence: float | None = None,
    repetition: int = 1,
    fact_check_linked: bool = False,
) -> float:
    """Blend the provisional `base` with context factors; return a [0,1] score."""
    score = base

    if asr_confidence is not None:
        delta = (asr_confidence - _ASR_CENTER) * _ASR_GAIN
        score += max(-_ASR_BOUND, min(_ASR_BOUND, delta))

    extras = min(max(repetition - 1, 0), _REPEAT_CAP_EXTRAS)
    score += extras * _REPEAT_STEP

    if fact_check_linked:
        score += _FACT_CHECK_BONUS

    return round(clamp(score), 4)
