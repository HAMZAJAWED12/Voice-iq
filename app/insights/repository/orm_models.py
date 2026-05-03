"""SQLAlchemy ORM models for the Insight Service.

We persist the full `InsightGenerateResponse` payload as a JSON blob so the
schema doesn't need to change every time an engine adds a new field. The
session_id, status, and timestamps are kept as first-class indexed columns
for cheap listing / filtering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.insights.repository.db import Base


def _utcnow() -> datetime:
    """Timezone-aware UTC `now`, safe across SQLite/Postgres."""
    return datetime.now(timezone.utc)


class InsightRecordORM(Base):
    """Persistence shape for a single generated insight bundle.

    The payload column stores the JSON-serialised
    `InsightGenerateResponse` — flexible enough to absorb future engine
    additions without a migration.
    """

    __tablename__ = "insight_records"

    session_id: Mapped[str] = mapped_column(
        String(128),
        primary_key=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="ok",
        index=True,
    )
    payload_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

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
        return (
            f"<InsightRecordORM session_id={self.session_id!r} "
            f"status={self.status!r} updated_at={self.updated_at.isoformat()!r}>"
        )

    def to_dict(self) -> dict[str, Any]:
        """Lightweight representation, useful for listing endpoints."""
        return {
            "session_id": self.session_id,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


__all__ = ["InsightRecordORM"]
