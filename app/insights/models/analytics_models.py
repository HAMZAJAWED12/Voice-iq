from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ValidationIssue(BaseModel):
    code: str
    message: str
    field: Optional[str] = None
    severity: str = "warning"


class ValidationResult(BaseModel):
    valid: bool = True
    errors: List[ValidationIssue] = Field(default_factory=list)
    warnings: List[ValidationIssue] = Field(default_factory=list)


class PauseMetric(BaseModel):
    start_after_utterance_id: Optional[str] = None
    end_before_utterance_id: Optional[str] = None
    duration_sec: float = Field(ge=0.0)
    speaker_before: Optional[str] = None
    speaker_after: Optional[str] = None


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
    first_spoke_at_sec: Optional[float] = None
    last_spoke_at_sec: Optional[float] = None


class AnalyticsBundle(BaseModel):
    session_metrics: SessionMetrics
    speaker_metrics: Dict[str, SpeakerMetrics] = Field(default_factory=dict)
    pauses: List[PauseMetric] = Field(default_factory=list)