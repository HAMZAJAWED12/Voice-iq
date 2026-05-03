from __future__ import annotations

from pydantic import BaseModel, Field


class SentimentAggregate(BaseModel):
    label: str | None = None
    avg_score: float | None = None
    distribution: dict[str, int] = Field(default_factory=dict)
    sample_count: int = 0


class EmotionAggregate(BaseModel):
    dominant: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    sample_count: int = 0


class SentimentTrendPoint(BaseModel):
    utterance_id: str
    speaker: str
    start: float
    end: float
    label: str | None = None
    score: float | None = None


class SessionSentimentTrend(BaseModel):
    direction: str | None = None
    slope: float | None = None
    points: list[SentimentTrendPoint] = Field(default_factory=list)


class AggregatedSignals(BaseModel):
    session_sentiment: SentimentAggregate = Field(default_factory=SentimentAggregate)
    session_emotion: EmotionAggregate = Field(default_factory=EmotionAggregate)
    session_sentiment_trend: SessionSentimentTrend = Field(default_factory=SessionSentimentTrend)
    speaker_sentiment: dict[str, SentimentAggregate] = Field(default_factory=dict)
    speaker_emotion: dict[str, EmotionAggregate] = Field(default_factory=dict)
    emotion_volatility_score: float = 0.0
