"""Agent Brain input contract: the processed conversation it consumes."""

from __future__ import annotations

from pydantic import Field

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
