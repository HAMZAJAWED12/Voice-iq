"""SQLite-backed repository for generated InsightStoredRecord rows.

Persists the full `InsightGenerateResponse` payload as JSON in the
`insight_records` table. The public method surface intentionally matches
the previous in-memory implementation so callers (routes, services,
tests) don't have to change.

Two construction modes are supported:

* `InsightRepository()` -> uses the module-level engine / SessionLocal
  configured by `app.insights.repository.db`. This is the production
  path, used by the FastAPI dependency.
* `InsightRepository(session_factory=...)` -> dependency-injected
  factory, useful for tests that want to point at an isolated DB.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.insights.models.api_models import (
    InsightGenerateResponse,
    InsightStoredRecord,
)
from app.insights.repository.db import get_session_factory
from app.insights.repository.orm_models import InsightRecordORM

SessionFactory = Callable[[], Session]


class InsightRepositoryError(RuntimeError):
    """Raised when the repository cannot satisfy a request safely."""


class InsightRepository:
    """Persistence gateway for `InsightStoredRecord` rows.

    Thin, intentionally boring: every method opens a short-lived session,
    does a single unit of work, and closes it. We do not leak SQLAlchemy
    sessions or ORM objects to callers.
    """

    def __init__(self, session_factory: SessionFactory | None = None) -> None:
        # Allow tests to inject a custom factory; default to the shared
        # module-level SessionLocal at first use.
        self._session_factory_override: SessionFactory | None = session_factory

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _session_factory(self) -> SessionFactory:
        if self._session_factory_override is not None:
            return self._session_factory_override
        return get_session_factory()

    def _open(self) -> Session:
        return self._session_factory()()

    @staticmethod
    def _to_stored(row: InsightRecordORM) -> InsightStoredRecord:
        """Hydrate an ORM row back into the Pydantic API model."""
        payload = InsightGenerateResponse.model_validate_json(row.payload_json)
        return InsightStoredRecord(
            session_id=row.session_id,
            status=row.status,
            payload=payload,
        )

    # ------------------------------------------------------------------ #
    # Public API (mirrors the previous in-memory implementation)
    # ------------------------------------------------------------------ #

    def save(self, record: InsightStoredRecord) -> InsightStoredRecord:
        """Insert or update a record keyed by session_id."""
        payload_json = record.payload.model_dump_json()
        session = self._open()
        try:
            existing = session.get(InsightRecordORM, record.session_id)
            if existing is None:
                row = InsightRecordORM(
                    session_id=record.session_id,
                    status=record.status,
                    payload_json=payload_json,
                )
                session.add(row)
            else:
                existing.status = record.status
                existing.payload_json = payload_json
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise InsightRepositoryError(f"failed to save insight record for session_id={record.session_id!r}") from exc
        finally:
            session.close()
        return record

    def get(self, session_id: str) -> InsightStoredRecord | None:
        """Fetch a record by session_id, or None when missing."""
        if not session_id:
            return None
        session = self._open()
        try:
            row = session.get(InsightRecordORM, session_id)
            if row is None:
                return None
            return self._to_stored(row)
        finally:
            session.close()

    def exists(self, session_id: str) -> bool:
        """True iff a record with that session_id is persisted."""
        if not session_id:
            return False
        session = self._open()
        try:
            stmt = select(InsightRecordORM.session_id).where(InsightRecordORM.session_id == session_id).limit(1)
            return session.execute(stmt).scalar_one_or_none() is not None
        finally:
            session.close()

    def delete(self, session_id: str) -> bool:
        """Remove a record. Returns True iff a row was deleted."""
        if not session_id:
            return False
        session = self._open()
        try:
            stmt = sa_delete(InsightRecordORM).where(InsightRecordORM.session_id == session_id)
            result = session.execute(stmt)
            session.commit()
            return (result.rowcount or 0) > 0
        except SQLAlchemyError as exc:
            session.rollback()
            raise InsightRepositoryError(f"failed to delete insight record for session_id={session_id!r}") from exc
        finally:
            session.close()

    def list_session_ids(self) -> list[str]:
        """Return every session_id, ordered by most recent update."""
        session = self._open()
        try:
            stmt = select(InsightRecordORM.session_id).order_by(InsightRecordORM.updated_at.desc())
            return [row[0] for row in session.execute(stmt).all()]
        finally:
            session.close()

    def list_records(self) -> list[InsightStoredRecord]:
        """Return every persisted record, ordered by most recent update.

        Note: this materialises every payload into Pydantic models, so it
        scales linearly. For large stores prefer paginated queries.
        """
        session = self._open()
        try:
            stmt = select(InsightRecordORM).order_by(InsightRecordORM.updated_at.desc())
            rows = session.execute(stmt).scalars().all()
            return [self._to_stored(r) for r in rows]
        finally:
            session.close()

    def count(self) -> int:
        """Number of persisted records."""
        session = self._open()
        try:
            stmt = select(InsightRecordORM.session_id)
            return len(session.execute(stmt).all())
        finally:
            session.close()

    def clear(self) -> None:
        """Remove every record. Intended for tests / dev resets."""
        session = self._open()
        try:
            session.execute(sa_delete(InsightRecordORM))
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise InsightRepositoryError("failed to clear insight records") from exc
        finally:
            session.close()


# ---------------------------------------------------------------------- #
# Helper construction functions
# ---------------------------------------------------------------------- #


def build_repository_from_factory(
    session_factory: sessionmaker[Session],
) -> InsightRepository:
    """Convenience for tests: build a repository bound to a custom factory."""
    return InsightRepository(session_factory=session_factory)


__all__ = [
    "InsightRepository",
    "InsightRepositoryError",
    "build_repository_from_factory",
]
