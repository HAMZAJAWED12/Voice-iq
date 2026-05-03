"""Pydantic models for the rule-based Fact-Check Engine.

This module defines the public schema used by:
  - the claim detector + classifier (input/output)
  - the source clients (evidence shape)
  - the comparator + scorer (verdict + confidence)
  - the API layer (request + response)

No business logic lives here. Engines and clients import these types only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Enums                                                                       #
# --------------------------------------------------------------------------- #

ClaimType = Literal[
    "CURRENCY_RATE",
    "COMMODITY_PRICE",
    "CRYPTO_PRICE",
    "STOCK_PRICE",
    "WEATHER",
    "STATIC_FACT",
]

Verdict = Literal[
    "TRUE",
    "FALSE",
    "PARTIALLY_TRUE",
    "UNVERIFIED",
    "UNSUPPORTED_CLAIM_TYPE",
    "SOURCE_UNAVAILABLE",
]

ConfidenceLabel = Literal["HIGH", "MEDIUM", "LOW", "NONE"]


# --------------------------------------------------------------------------- #
# Core building blocks                                                        #
# --------------------------------------------------------------------------- #

class ClaimSpan(BaseModel):
    """Character-offset span of the matched claim inside the source text."""

    start: int = Field(..., ge=0, description="Inclusive start offset.")
    end: int = Field(..., ge=0, description="Exclusive end offset.")

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: int, info) -> int:  # type: ignore[no-untyped-def]
        start = info.data.get("start", 0)
        if v < start:
            raise ValueError("end must be >= start")
        return v


class DetectedClaim(BaseModel):
    """One fact-checkable claim extracted from a transcript."""

    claim_id: str = Field(..., description="Stable id within the request scope.")
    text: str = Field(..., description="Verbatim claim substring.")
    span: ClaimSpan
    claim_type: ClaimType
    raw_value: Optional[float] = Field(
        default=None,
        description=(
            "Numeric value the speaker asserted (e.g. 95000 for "
            "'Bitcoin is at 95000 dollars'). None for STATIC_FACT and "
            "weather claims with no numeric anchor."
        ),
    )
    raw_value_text: Optional[str] = Field(
        default=None,
        description=(
            "String value asserted (e.g. 'Paris' for "
            "'capital of France is Paris'). Used by STATIC_FACT and "
            "non-numeric claims."
        ),
    )
    subject: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Normalized subject metadata. Keys vary by ClaimType, e.g. "
            "{'pair': 'USD/PKR'} for CURRENCY_RATE, "
            "{'asset': 'BTC'} for CRYPTO_PRICE, "
            "{'city': 'Karachi', 'unit': 'C'} for WEATHER, "
            "{'country': 'France'} for STATIC_FACT."
        ),
    )
    unit: Optional[str] = Field(
        default=None,
        description="Unit reported with the value, e.g. 'USD', 'C', 'F'.",
    )


class Evidence(BaseModel):
    """Raw fact returned by an external source for a single claim."""

    source: str = Field(..., description="Source client identifier, e.g. 'coingecko'.")
    fetched_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when the source was queried.",
    )
    value: Optional[float] = Field(
        default=None,
        description="Numeric ground-truth (None for static-fact lookups).",
    )
    value_text: Optional[str] = Field(
        default=None,
        description="String ground-truth (e.g. 'Paris').",
    )
    unit: Optional[str] = Field(default=None, description="Unit of `value`.")
    raw: Dict[str, Any] = Field(
        default_factory=dict,
        description="Minimal source payload stored for traceability.",
    )


class Confidence(BaseModel):
    """Confidence in the verdict. Score is clamped 0.0 - 1.0."""

    label: ConfidenceLabel = "NONE"
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class FactCheckResult(BaseModel):
    """Per-claim verdict, evidence and explanation."""

    claim: DetectedClaim
    evidence: Optional[Evidence] = None
    verdict: Verdict
    confidence: Confidence = Field(default_factory=Confidence)
    diff_pct: Optional[float] = Field(
        default=None,
        description=(
            "Percentage delta between claimed and actual numeric value. "
            "None for static facts and unverifiable claims."
        ),
    )
    reason: str = Field(..., description="Human-readable explanation of the verdict.")


# --------------------------------------------------------------------------- #
# API contract                                                                #
# --------------------------------------------------------------------------- #

# Hard ceiling on transcript size accepted by the /v1/fact-check endpoint.
MAX_TRANSCRIPT_CHARS: int = 10_000


class FactCheckRequest(BaseModel):
    """Inbound payload for POST /v1/fact-check."""

    conversation_id: str = Field(..., min_length=1, max_length=128)
    speaker_id: str = Field(..., min_length=1, max_length=128)
    transcript_text: str = Field(..., min_length=1, max_length=MAX_TRANSCRIPT_CHARS)


class FactCheckStats(BaseModel):
    claims_detected: int = 0
    verified: int = 0
    skipped: int = 0


class FactCheckResponse(BaseModel):
    """Outbound payload for POST /v1/fact-check."""

    conversation_id: str
    speaker_id: str
    fact_check_results: List[FactCheckResult] = Field(default_factory=list)
    stats: FactCheckStats = Field(default_factory=FactCheckStats)
