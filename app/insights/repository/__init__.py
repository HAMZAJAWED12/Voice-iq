"""Repository package for the Insight Service.

Exposes:
- `InsightRepository`        - SQLite-backed persistence for InsightStoredRecord
- `insight_repository`       - default lazy-initialised singleton (kept for
                               backwards compatibility with imports that
                               predate the FastAPI dependency injection)
- `get_insight_repository`   - FastAPI dependency that returns the singleton
"""

from app.insights.repository.insight_repository import (
    InsightRepository,
    InsightRepositoryError,
    build_repository_from_factory,
)

# Module-level singleton kept for any caller that still imports it directly.
# Construction is cheap (no DB I/O) — the engine is only created on first
# call to a method that needs a session.
insight_repository = InsightRepository()


def get_insight_repository() -> InsightRepository:
    """FastAPI dependency: return the process-wide singleton repository."""
    return insight_repository


__all__ = [
    "InsightRepository",
    "InsightRepositoryError",
    "build_repository_from_factory",
    "insight_repository",
    "get_insight_repository",
]