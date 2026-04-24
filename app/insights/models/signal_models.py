from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class SentimentAggregate(BaseModel):
    label: Optional[str] = None
    avg_score: Optional[float] = None
    distribution: Dict[str, int] = Field(default_factory=dict)
    sample_count: int = 0


class EmotionAggregate(BaseModel):
    dominant: Optional[str] = None
    distribution: Dict[str, float] = Field(default_factory=dict)
    sample_count: int = 0


class SentimentTrendPoint(BaseModel):
    utterance_id: str
    speaker: str
    start: float
    end: float
    label: Optional[str] = None
    score: Optional[float] = None


class SessionSentimentTrend(BaseModel):
    direction: Optional[str] = None
    slope: Optional[float] = None
    points: List[SentimentTrendPoint] = Field(default_factory=list)


class AggregatedSignals(BaseModel):
    session_sentiment: SentimentAggregate = Field(default_factory=SentimentAggregate)
    session_emotion: EmotionAggregate = Field(default_factory=EmotionAggregate)
    session_sentiment_trend: SessionSentimentTrend = Field(default_factory=SessionSentimentTrend)
    speaker_sentiment: Dict[str, SentimentAggregate] = Field(default_factory=dict)
    speaker_emotion: Dict[str, EmotionAggregate] = Field(default_factory=dict)
    emotion_volatility_score: float = 0.0