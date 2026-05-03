from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

InconsistencyLevel = Literal["none", "low", "medium", "high"]


class InconsistencySignal(BaseModel):
    """A single detected inconsistency cue.

    Each signal is fully explainable: it carries a human readable reason and a
    structured evidence dictionary describing the data points that triggered it.
    """

    signal_type: str
    severity: str = "low"
    score: float = 0.0
    speaker: str | None = None
    reason: str
    evidence: dict[str, float | int | str | bool] = Field(default_factory=dict)


class InconsistencyWindow(BaseModel):
    """A localized time window where a contradiction was observed."""

    start_sec: float
    end_sec: float
    level: InconsistencyLevel = "low"
    speaker: str | None = None
    reason: str
    evidence: dict[str, float | int | str | bool] = Field(default_factory=dict)


class InconsistencyAssessment(BaseModel):
    """Top-level result of the Inconsistency Engine.

    Aggregates all detected signals and windows into a single, scored,
    explainable assessment that downstream layers (rule engine, summary,
    timeline, flags) can consume safely.
    """

    level: InconsistencyLevel = "none"
    score: float = 0.0
    signals: list[InconsistencySignal] = Field(default_factory=list)
    windows: list[InconsistencyWindow] = Field(default_factory=list)
    primary_speaker: str | None = None
    summary: str = "No inconsistency detected."
