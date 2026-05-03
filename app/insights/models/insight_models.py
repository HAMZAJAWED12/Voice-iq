from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.insights.models.escalation_models import EscalationAssessment
from app.insights.models.inconsistency_models import InconsistencyAssessment
from app.insights.models.signal_models import (
    EmotionAggregate,
    SentimentAggregate,
    SessionSentimentTrend,
)

SeverityLevel = Literal["low", "medium", "high"]
MarkerType = Literal[
    "emotional_shift",
    "high_tension",
    "interruption",
    "dominance_window",
    "inconsistency_candidate",
    "engagement_drop",
    "session_tone_decline",
]

ScalarEvidence = float | int | str | bool


class InsightFlag(BaseModel):
    type: str
    speaker: str | None = None
    severity: SeverityLevel = "low"
    reason: str
    evidence: dict[str, ScalarEvidence] = Field(default_factory=dict)


class ScoreBreakdownItem(BaseModel):
    component: str
    weight: float
    value: float
    reason: str


class InsightScores(BaseModel):
    dominance_score: float = Field(0.0, ge=0.0, le=1.0)
    engagement_score: float = Field(0.0, ge=0.0, le=1.0)
    conflict_score: float = Field(0.0, ge=0.0, le=1.0)
    cooperation_score: float = Field(0.0, ge=0.0, le=1.0)
    emotion_volatility_score: float = Field(0.0, ge=0.0, le=1.0)

    breakdown: dict[str, list[ScoreBreakdownItem]] = Field(default_factory=dict)


class TimelineMarker(BaseModel):
    marker_id: str
    type: MarkerType
    time_sec: float = Field(ge=0.0)
    speaker: str | None = None
    severity: SeverityLevel = "low"
    reason: str
    start_sec: float | None = Field(default=None, ge=0.0)
    end_sec: float | None = Field(default=None, ge=0.0)
    evidence: dict[str, ScalarEvidence] = Field(default_factory=dict)


class SpeakerInsight(BaseModel):
    speaker: str
    sentiment: SentimentAggregate | None = None
    emotion: EmotionAggregate | None = None
    dominance_ratio: float = 0.0
    engagement_ratio: float = 0.0
    flags: list[InsightFlag] = Field(default_factory=list)


class InsightBundle(BaseModel):
    session_sentiment: SentimentAggregate | None = None
    session_emotion: EmotionAggregate | None = None
    session_sentiment_trend: SessionSentimentTrend | None = None
    speaker_insights: dict[str, SpeakerInsight] = Field(default_factory=dict)
    scores: InsightScores
    flags: list[InsightFlag] = Field(default_factory=list)
    timeline: list[TimelineMarker] = Field(default_factory=list)
    escalation: EscalationAssessment | None = None
    inconsistency: InconsistencyAssessment | None = None
