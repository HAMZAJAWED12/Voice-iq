"""SQLite-backed repository for fact-check results.

Mirrors the construction style of `InsightRepository`: short-lived sessions,
no ORM leakage, optional injected session factory for tests.

Public surface:
  * ``save_response`` - bulk-store every result from one engine run.
  * ``save_result``   - single-result variant (rarely needed; convenience).
  * ``list_for_conversation`` - chronological list filtered by conversation.
  * ``list_for_speaker``      - same, but also filtered by speaker.
  * ``count`` / ``clear``     - bookkeeping helpers (tests + admin).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.insights.models.factcheck_models import (
    FactCheckResponse,
    FactCheckResult,
)
from app.insights.repository.db import get_session_factory
from app.insights.repository.factcheck_orm_models import FactCheckResultORM


SessionFactory = Callable[[], Session]


class FactCheckRepositoryError(RuntimeError):
    """Raised when the repository cannot satisfy a request safely."""


class FactCheckRepository:
    """Persistence gateway for `FactCheckResultORM` rows."""

    def __init__(self, session_factory: Optional[SessionFactory] = None) -> None:
        self._session_factory_override: Optional[SessionFactory] = session_factory

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    def _session_factory(self) -> SessionFactory:
        if self._session_factory_override is not None:
            return self._session_factory_override
        return get_session_factory()

    def _open(self) -> Session:
        return self._session_factory()()

    @staticmethod
    def _to_orm(
        *,
        conversation_id: str,
        speaker_id: str,
        result: FactCheckResult,
    ) -> FactCheckResultORM:
        """Map a Pydantic `FactCheckResult` onto a fresh ORM row."""
        evidence = result.evidence
        evidence_json: Optional[str] = None
        if evidence is not None:
            try:
                evidence_json = evidence.model_dump_json()
            except Exception:  # pragma: no cover - defensive
                evidence_json = None

        return FactCheckResultORM(
            conversation_id=conversation_id,
            speaker_id=speaker_id,
            claim_text=result.claim.text,
            claim_type=result.claim.claim_type,
            claimed_value=result.claim.raw_value,
            claimed_text=result.claim.raw_value_text,
            actual_value=evidence.value if evidence else None,
            actual_text=evidence.value_text if evidence else None,
            diff_pct=result.diff_pct,
            source=evidence.source if evidence else None,
            source_fetched_at=evidence.fetched_at if evidence else None,
            evidence_json=evidence_json,
            verdict=result.verdict,
            confidence_label=result.confidence.label,
            confidence_score=result.confidence.score,
            reason=result.reason,
        )

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def save_response(self, response: FactCheckResponse) -> List[int]:
        """Persist every result inside a `FactCheckResponse`. Returns IDs."""
        if not response.fact_check_results:
            return []
        rows = [
            self._to_orm(
                conversation_id=response.conversation_id,
                speaker_id=response.speaker_id,
                result=result,
            )
            for result in response.fact_check_results
        ]
        session = self._open()
        try:
            session.add_all(rows)
            session.commit()
            for row in rows:
                session.refresh(row)
        except SQLAlchemyError as exc:
            session.rollback()
            raise FactCheckRepositoryError(
                f"failed to save fact-check results for "
                f"conversation_id={response.conversation_id!r}"
            ) from exc
        finally:
            session.close()
        return [row.id for row in rows]

    def save_result(
        self,
        *,
        conversation_id: str,
        speaker_id: str,
        result: FactCheckResult,
    ) -> int:
        """Persist a single result. Returns the new row id."""
        row = self._to_orm(
            conversation_id=conversation_id,
            speaker_id=speaker_id,
            result=result,
        )
        session = self._open()
        try:
            session.add(row)
            session.commit()
            session.refresh(row)
        except SQLAlchemyError as exc:
            session.rollback()
            raise FactCheckRepositoryError(
                f"failed to save fact-check result for "
                f"conversation_id={conversation_id!r}"
            ) from exc
        finally:
            session.close()
        return row.id

    def list_for_conversation(self, conversation_id: str) -> List[Dict[str, Any]]:
        """Return every result for `conversation_id`, newest first."""
        if not conversation_id:
            return []
        session = self._open()
        try:
            stmt = (
                select(FactCheckResultORM)
                .where(FactCheckResultORM.conversation_id == conversation_id)
                .order_by(FactCheckResultORM.created_at.desc())
            )
            rows = session.execute(stmt).scalars().all()
            return [self._row_to_dict(row) for row in rows]
        finally:
            session.close()

    def list_for_speaker(
        self, conversation_id: str, speaker_id: str
    ) -> List[Dict[str, Any]]:
        """Return every result for a (conversation, speaker) pair, newest first."""
        if not conversation_id or not speaker_id:
            return []
        session = self._open()
        try:
            stmt = (
                select(FactCheckResultORM)
                .where(FactCheckResultORM.conversation_id == conversation_id)
                .where(FactCheckResultORM.speaker_id == speaker_id)
                .order_by(FactCheckResultORM.created_at.desc())
            )
            rows = session.execute(stmt).scalars().all()
            return [self._row_to_dict(row) for row in rows]
        finally:
            session.close()

    def count(self) -> int:
        """Number of persisted rows."""
        session = self._open()
        try:
            stmt = select(FactCheckResultORM.id)
            return len(session.execute(stmt).all())
        finally:
            session.close()

    def clear(self) -> None:
        """Remove every row. Intended for tests / dev resets."""
        session = self._open()
        try:
            session.execute(sa_delete(FactCheckResultORM))
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise FactCheckRepositoryError("failed to clear fact-check results") from exc
        finally:
            session.close()

    # ------------------------------------------------------------------ #
    # Hydration helpers                                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _row_to_dict(row: FactCheckResultORM) -> Dict[str, Any]:
        """Expand `to_dict` with parsed evidence + reason for full readers."""
        out = row.to_dict()
        out["claimed_value"] = row.claimed_value
        out["claimed_text"] = row.claimed_text
        out["actual_value"] = row.actual_value
        out["actual_text"] = row.actual_text
        out["reason"] = row.reason
        out["source_fetched_at"] = (
            row.source_fetched_at.isoformat() if row.source_fetched_at else None
        )
        if row.evidence_json:
            try:
                out["evidence"] = json.loads(row.evidence_json)
            except json.JSONDecodeError:
                out["evidence"] = None
        else:
            out["evidence"] = None
        return out


def build_factcheck_repository_from_factory(
    session_factory: sessionmaker[Session],
) -> FactCheckRepository:
    """Convenience for tests: build a repository bound to a custom factory."""
    return FactCheckRepository(session_factory=session_factory)


__all__ = [
    "FactCheckRepository",
    "FactCheckRepositoryError",
    "build_factcheck_repository_from_factory",
]
