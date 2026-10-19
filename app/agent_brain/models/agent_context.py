"""Agent Brain input contract: the processed conversation it consumes."""

from __future__ import annotations

import math
from typing import Any

from pydantic import Field, field_validator

from app.agent_brain.models.base import CamelModel
from app.agent_brain.models.enums import FactCheckStatus, LanguageCode


class TranscriptSegment(CamelModel):
    segment_id: str | None = None
    speaker_id: str | None = None
    speaker_label: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    text: str = ""
    confidence: float | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _finite_confidence(cls, value: Any) -> Any:
        return _none_if_non_finite(value)


class ContextInsights(CamelModel):
    summary: str | None = None
    topics: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    escalation_flags: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[str] = Field(default_factory=list)


class ContextClaim(CamelModel):
    claim_id: str | None = None
    claim_text: str = ""
    status: FactCheckStatus | None = None
    confidence: float | None = None

    @field_validator("confidence", mode="before")
    @classmethod
    def _finite_confidence(cls, value: Any) -> Any:
        return _none_if_non_finite(value)


class ContextFactChecks(CamelModel):
    claims: list[ContextClaim] = Field(default_factory=list)


class AgentContext(CamelModel):
    """Everything an agent needs to reason about one conversation."""

    session_id: str
    organization_id: str | None = None
    language: LanguageCode = "en"
    transcript: list[TranscriptSegment] = Field(default_factory=list)
    insights: ContextInsights = Field(default_factory=ContextInsights)
    fact_checks: ContextFactChecks = Field(default_factory=ContextFactChecks)


def _none_if_non_finite(value: Any) -> Any:
    """A non-finite confidence (NaN, Infinity) counts as no confidence."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
