"""Normalized action-recommendation schema (Agent Brain output contract)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator

from app.agent_brain.models.base import CamelModel
from app.agent_brain.models.enums import ActionType, AgentType, Priority

SCHEMA_VERSION = "1.0"


class Source(CamelModel):
    """Evidence: the transcript span a recommendation was derived from."""

    segment_id: str | None = None
    speaker_id: str | None = None
    speaker_label: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    text: str = Field(min_length=1)


class Entities(CamelModel):
    """Structured fields extracted from the source span."""

    assignee: str | None = None
    deadline_text: str | None = None
    customer_name: str | None = None
    topic: str | None = None


class Recommendation(CamelModel):
    """A single suggested next action. Recommend-only: never executed here."""

    recommendation_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_type: AgentType
    action_type: ActionType
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    priority: Priority = "MEDIUM"
    confidence: float = Field(ge=0.0, le=1.0)
    # Locked True: the Agent Brain never emits a self-approving / auto-executing
    # recommendation. Approval + execution belong to the Java Action Layer.
    requires_approval: Literal[True] = True
    linked_claim_id: str | None = None
    source: Source
    entities: Entities = Field(default_factory=Entities)
    suggested_payload: dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=1)

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, value: float) -> float:
        # Uniform 4dp rounding so floating-point accumulation in the agents'
        # provisional scoring never leaks into the contract (0.85, not
        # 0.8500000000000001).
        return round(value, 4)


class RecommendationResponse(CamelModel):
    """Response body for the generate endpoint."""

    session_id: str
    schema_version: str = SCHEMA_VERSION
    recommendations: list[Recommendation] = Field(default_factory=list)


class CallbackPayload(CamelModel):
    """Body POSTed to the Java Action Layer callback."""

    session_id: str
    schema_version: str = SCHEMA_VERSION
    generated_at: str  # ISO-8601 UTC
    recommendations: list[Recommendation] = Field(default_factory=list)
