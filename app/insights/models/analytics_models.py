from __future__ import annotations

from pydantic import BaseModel, Field


class ValidationIssue(BaseModel):
    code: str
    message: str
    field: str | None = None
    severity: str = "warning"


class ValidationResult(BaseModel):
    valid: bool = True
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)


class PauseMetric(BaseModel):
    start_after_utterance_id: str | None = None
    end_before_utterance_id: str | None = None
    duration_sec: float = Field(ge=0.0)
    speaker_before: str | None = None
    speaker_after: str | None = None


class SessionMetrics(BaseModel):
    total_duration_sec: float = 0.0
    total_speakers: int = 0
    total_utterances: int = 0
    total_words: int = 0
    avg_utterance_length_words: float = 0.0
    avg_utterance_duration_sec: float = 0.0
    total_questions: int = 0
    total_pauses: int = 0
    avg_pause_sec: float = 0.0
    max_pause_sec: float = 0.0


class SpeakerMetrics(BaseModel):
    speaker: str
    speaking_time_sec: float = 0.0
    utterance_count: int = 0
    word_count: int = 0
    speaking_ratio: float = 0.0
    word_ratio: float = 0.0
    avg_utterance_length_words: float = 0.0
    avg_utterance_duration_sec: float = 0.0
    question_count: int = 0
    interruption_count: int = 0
    overlap_count: int = 0
    first_spoke_at_sec: float | None = None
    last_spoke_at_sec: float | None = None


class AnalyticsBundle(BaseModel):
    session_metrics: SessionMetrics
    speaker_metrics: dict[str, SpeakerMetrics] = Field(default_factory=dict)
    pauses: list[PauseMetric] = Field(default_factory=list)
