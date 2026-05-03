from __future__ import annotations

from pydantic import BaseModel, Field


class InsightThresholds(BaseModel):
    dominance_speaking_ratio_threshold: float = Field(0.60, ge=0.0, le=1.0)
    dominance_word_ratio_threshold: float = Field(0.60, ge=0.0, le=1.0)

    engagement_drop_pause_threshold_sec: float = Field(3.0, ge=0.0)
    severe_engagement_drop_pause_threshold_sec: float = Field(6.0, ge=0.0)

    high_tension_interruption_threshold: int = Field(2, ge=0)
    high_tension_overlap_threshold: int = Field(2, ge=0)

    emotional_shift_delta_threshold: float = Field(0.45, ge=0.0, le=1.0)
    severe_emotional_shift_delta_threshold: float = Field(0.70, ge=0.0, le=1.0)

    frequent_interruptions_threshold: int = Field(2, ge=0)
    high_overlap_participation_threshold: int = Field(2, ge=0)
    low_inquiry_min_utterances: int = Field(4, ge=0)


DEFAULT_THRESHOLDS = InsightThresholds()
