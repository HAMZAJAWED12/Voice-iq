from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.insights.models.analytics_models import AnalyticsBundle, ValidationResult
from app.insights.models.insight_models import InsightBundle


class SummaryBundle(BaseModel):
    overall_summary: str = ""
    speaker_summaries: Dict[str, str] = Field(default_factory=dict)
    notable_concerns: List[str] = Field(default_factory=list)
    key_moments_summary: str = ""


class InsightMeta(BaseModel):
    service_version: str = "1.0.0"
    threshold_profile: str = "default"
    generated_at: Optional[str] = None
    processing_ms: Optional[int] = None


class InsightGenerateResponse(BaseModel):
    session_id: str
    status: str = "ok"
    validation: ValidationResult
    analytics: AnalyticsBundle
    insights: InsightBundle
    summaries: SummaryBundle
    warnings: List[str] = Field(default_factory=list)
    meta: InsightMeta


class InsightSummaryResponse(BaseModel):
    session_id: str
    overall_summary: str
    notable_concerns: List[str] = Field(default_factory=list)
    key_moments_summary: str = ""


class InsightSpeakersResponse(BaseModel):
    session_id: str
    speaker_metrics: Dict[str, dict] = Field(default_factory=dict)
    speaker_insights: Dict[str, dict] = Field(default_factory=dict)
    speaker_summaries: Dict[str, str] = Field(default_factory=dict)


class InsightTimelineResponse(BaseModel):
    session_id: str
    timeline: List[dict] = Field(default_factory=list)


class InsightStoredRecord(BaseModel):
    session_id: str
    status: str
    payload: InsightGenerateResponse