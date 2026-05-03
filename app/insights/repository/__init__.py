"""Repository package for the Insight Service.

Exposes:
- `InsightRepository`        - SQLite-backed persistence for InsightStoredRecord
- `insight_repository`       - default lazy-initialised singleton (kept for
                               backwards compatibility with imports that
                               predate the FastAPI dependency injection)
- `get_insight_repository`   - FastAPI dependency that returns the singleton
"""

from app.insights.repository.factcheck_repository import (
    FactCheckRepository,
    FactCheckRepositoryError,
    build_factcheck_repository_from_factory,
)
from app.insights.repository.insight_repository import (
    InsightRepository,
    InsightRepositoryError,
    build_repository_from_factory,
)

# Module-level singletons kept for any caller that still imports them
# directly. Construction is cheap (no DB I/O) — the engine is only created
# on first call to a method that needs a session.
insight_repository = InsightRepository()
factcheck_repository = FactCheckRepository()


def get_insight_repository() -> InsightRepository:
    """FastAPI dependency: return the process-wide singleton insight repo."""
    return insight_repository


def get_factcheck_repository() -> FactCheckRepository:
    """FastAPI dependency: return the process-wide singleton fact-check repo."""
    return factcheck_repository


__all__ = [
    "InsightRepository",
    "InsightRepositoryError",
    "build_repository_from_factory",
    "insight_repository",
    "get_insight_repository",
    "FactCheckRepository",
    "FactCheckRepositoryError",
    "build_factcheck_repository_from_factory",
    "factcheck_repository",
    "get_factcheck_repository",
]
