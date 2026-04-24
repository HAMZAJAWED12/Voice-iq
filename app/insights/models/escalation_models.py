from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


EscalationLevel = Literal["none", "mild", "moderate", "severe"]


class EscalationSignal(BaseModel):
    signal_type: str
    severity: str = "low"
    score: float = 0.0
    reason: str
    evidence: Dict[str, float | int | str | bool] = Field(default_factory=dict)


class EscalationWindow(BaseModel):
    start_sec: float
    end_sec: float
    level: EscalationLevel
    primary_speaker: Optional[str] = None
    reason: str
    evidence: Dict[str, float | int | str | bool] = Field(default_factory=dict)


class EscalationAssessment(BaseModel):
    level: EscalationLevel = "none"
    score: float = 0.0
    signals: List[EscalationSignal] = Field(default_factory=list)
    windows: List[EscalationWindow] = Field(default_factory=list)
    primary_speaker: Optional[str] = None
    summary: str = "No escalation detected."