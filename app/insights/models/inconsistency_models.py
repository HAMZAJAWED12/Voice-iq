from __future__ import annotations

from typing import Dict, List, Literal, Optional

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
    speaker: Optional[str] = None
    reason: str
    evidence: Dict[str, float | int | str | bool] = Field(default_factory=dict)


class InconsistencyWindow(BaseModel):
    """A localized time window where a contradiction was observed."""

    start_sec: float
    end_sec: float
    level: InconsistencyLevel = "low"
    speaker: Optional[str] = None
    reason: str
    evidence: Dict[str, float | int | str | bool] = Field(default_factory=dict)


class InconsistencyAssessment(BaseModel):
    """Top-level result of the Inconsistency Engine.

    Aggregates all detected signals and windows into a single, scored,
    explainable assessment that downstream layers (rule engine, summary,
    timeline, flags) can consume safely.
    """

    level: InconsistencyLevel = "none"
    score: float = 0.0
    signals: List[InconsistencySignal] = Field(default_factory=list)
    windows: List[InconsistencyWindow] = Field(default_factory=list)
    primary_speaker: Optional[str] = None
    summary: str = "No inconsistency detected."
