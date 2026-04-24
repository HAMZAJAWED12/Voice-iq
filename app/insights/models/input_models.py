from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

SentimentLabel = Literal["positive", "neutral", "negative"]


class SentimentInput(BaseModel):
    label: Optional[SentimentLabel] = None
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class EmotionInput(BaseModel):
    values: Dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_emotion_values(self):
        for k, v in self.values.items():
            if v < 0.0 or v > 1.0:
                raise ValueError(f"Emotion score for '{k}' must be between 0 and 1.")
        return self


class UtteranceInput(BaseModel):
    id: str
    speaker: str
    start: float = Field(ge=0.0)
    end: float = Field(ge=0.0)
    text: str = ""
    word_count: Optional[int] = Field(default=None, ge=0)

    sentiment: Optional[SentimentInput] = None
    emotion: Optional[EmotionInput] = None

    overlap: bool = False
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    diarization_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_time_order(self):
        if self.end < self.start:
            raise ValueError("Utterance end must be greater than or equal to start.")
        return self


class SessionMetaInput(BaseModel):
    source: Optional[str] = None
    language: Optional[str] = None
    created_at: Optional[str] = None
    pipeline_version: Optional[str] = None


class SessionInput(BaseModel):
    session_id: str
    duration_sec: Optional[float] = Field(default=None, ge=0.0)
    speakers: List[str] = Field(default_factory=list)
    utterances: List[UtteranceInput] = Field(default_factory=list)

    meta: Optional[SessionMetaInput] = None
    warnings: List[str] = Field(default_factory=list)

    speaker_stats: Dict[str, dict] = Field(default_factory=dict)
    conversation_stats: Dict[str, dict] = Field(default_factory=dict)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "session_id": "sample-call-2026-04-22",
                    "utterances": [
                        {
                            "id": "u1",
                            "speaker": "agent",
                            "start": 0.0,
                            "end": 2.4,
                            "text": "Hi, thanks for calling, how can I help you today?",
                            "word_count": 10,
                            "sentiment": {"label": "positive", "score": 0.82},
                            "emotion": {"values": {"happy": 0.6, "calm": 0.4}},
                        },
                        {
                            "id": "u2",
                            "speaker": "customer",
                            "start": 2.6,
                            "end": 6.5,
                            "text": "My order is late again, this is the second time this month.",
                            "word_count": 12,
                            "sentiment": {"label": "negative", "score": 0.28},
                            "emotion": {"values": {"frustrated": 0.7, "angry": 0.3}},
                        },
                    ],
                }
            ]
        }
    }

    @model_validator(mode="after")
    def validate_session(self):
        if not self.utterances:
            raise ValueError("Session must contain at least one utterance.")
        return self