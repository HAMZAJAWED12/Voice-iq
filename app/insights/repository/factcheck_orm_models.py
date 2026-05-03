"""SQLAlchemy ORM model for fact-check results.

One row per `FactCheckResult` returned by the engine. The full evidence
payload (including the raw upstream blob) is stored as JSON for traceability,
while first-class indexed columns (`conversation_id`, `verdict`, timestamps)
keep listing / filtering cheap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.insights.repository.db import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC `now`, safe across SQLite/Postgres."""
    return datetime.now(UTC)


class FactCheckResultORM(Base):
    """Persistence shape for a single per-claim verdict.

    Schema matches the task spec: claim, verdict, confidence, values, source,
    timestamps. Numeric columns are nullable so static-fact rows (string
    comparison) remain valid.
    """

    __tablename__ = "fact_check_results"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    # --- Conversation context ------------------------------------------- #
    conversation_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )
    speaker_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    # --- Claim ----------------------------------------------------------- #
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    claimed_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    claimed_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Evidence -------------------------------------------------------- #
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Verdict + confidence ------------------------------------------- #
    verdict: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    confidence_label: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # --- Explainability -------------------------------------------------- #
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Timestamps ------------------------------------------------------ #
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - dev ergonomics only
        return f"<FactCheckResultORM id={self.id} " f"conv={self.conversation_id!r} verdict={self.verdict!r}>"

    def to_dict(self) -> dict[str, Any]:
        """Lightweight representation for listing endpoints."""
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "speaker_id": self.speaker_id,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type,
            "verdict": self.verdict,
            "confidence_label": self.confidence_label,
            "confidence_score": self.confidence_score,
            "diff_pct": self.diff_pct,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = ["FactCheckResultORM"]
