from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

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

ScalarEvidence = Union[float, int, str, bool]


class InsightFlag(BaseModel):
    type: str
    speaker: Optional[str] = None
    severity: SeverityLevel = "low"
    reason: str
    evidence: Dict[str, ScalarEvidence] = Field(default_factory=dict)


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

    breakdown: Dict[str, List[ScoreBreakdownItem]] = Field(default_factory=dict)


class TimelineMarker(BaseModel):
    marker_id: str
    type: MarkerType
    time_sec: float = Field(ge=0.0)
    speaker: Optional[str] = None
    severity: SeverityLevel = "low"
    reason: str
    start_sec: Optional[float] = Field(default=None, ge=0.0)
    end_sec: Optional[float] = Field(default=None, ge=0.0)
    evidence: Dict[str, ScalarEvidence] = Field(default_factory=dict)


class SpeakerInsight(BaseModel):
    speaker: str
    sentiment: Optional[SentimentAggregate] = None
    emotion: Optional[EmotionAggregate] = None
    dominance_ratio: float = 0.0
    engagement_ratio: float = 0.0
    flags: List[InsightFlag] = Field(default_factory=list)


class InsightBundle(BaseModel):
    session_sentiment: Optional[SentimentAggregate] = None
    session_emotion: Optional[EmotionAggregate] = None
    session_sentiment_trend: Optional[SessionSentimentTrend] = None
    speaker_insights: Dict[str, SpeakerInsight] = Field(default_factory=dict)
    scores: InsightScores
    flags: List[InsightFlag] = Field(default_factory=list)
    timeline: List[TimelineMarker] = Field(default_factory=list)
    escalation: Optional[EscalationAssessment] = None
    inconsistency: Optional[InconsistencyAssessment] = None