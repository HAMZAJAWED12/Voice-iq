"""HTTP routes for the rule-based Fact-Check Engine.

Exposes a single endpoint:

    POST /v1/fact-check

Receives a transcript, detects + classifies + verifies factual claims,
persists every per-claim verdict, and returns the structured response.

Engine + repository are wired via FastAPI's `Depends`, so tests can swap
in a stub router (no live network) and an isolated SQLite session factory.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status

from app.insights.config.settings import get_settings
from app.insights.core.factcheck.factcheck_engine import (
    FactCheckEngine,
    SourceRouter,
)
from app.insights.models.factcheck_models import (
    FactCheckRequest,
    FactCheckResponse,
)
from app.insights.repository import (
    FactCheckRepository,
    FactCheckRepositoryError,
    get_factcheck_repository,
)
from app.utils.logger import logger


router = APIRouter(prefix="/fact-check", tags=["FactCheck"])


# --------------------------------------------------------------------------- #
# Engine dependency (process-wide singleton)                                  #
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _build_engine() -> FactCheckEngine:
    """Build the production engine once per process from current settings."""
    settings = get_settings()
    router_obj = SourceRouter.from_settings(settings)
    return FactCheckEngine(router=router_obj)


def get_factcheck_engine() -> FactCheckEngine:
    """FastAPI dependency: return the process-wide singleton engine."""
    return _build_engine()


# --------------------------------------------------------------------------- #
# Routes                                                                      #
# --------------------------------------------------------------------------- #

@router.post(
    "",
    response_model=FactCheckResponse,
    summary="Detect and verify factual claims in a transcript",
    description=(
        "Run the rule-based fact-check pipeline (claim detection → "
        "classification → external source verification → comparison → "
        "scoring) against the supplied transcript. Persists every result "
        "and returns the structured response."
    ),
    status_code=status.HTTP_200_OK,
)
def run_fact_check(
    payload: FactCheckRequest,
    engine: FactCheckEngine = Depends(get_factcheck_engine),
    repository: FactCheckRepository = Depends(get_factcheck_repository),
) -> FactCheckResponse:
    response = engine.run(
        conversation_id=payload.conversation_id,
        speaker_id=payload.speaker_id,
        transcript_text=payload.transcript_text,
    )

    # Persist asynchronously-ish (best-effort): a storage failure must not
    # break the verification response the caller already paid CPU for.
    try:
        repository.save_response(response)
    except FactCheckRepositoryError as exc:
        logger.warning(
            "fact-check persistence failed for conversation_id=%s (%s)",
            payload.conversation_id,
            exc,
        )

    return response


@router.get(
    "/{conversation_id}",
    summary="List persisted fact-check results for a conversation",
)
def list_fact_check_results(
    conversation_id: str,
    repository: FactCheckRepository = Depends(get_factcheck_repository),
) -> list[dict]:
    rows = repository.list_for_conversation(conversation_id)
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No fact-check results found for conversation '{conversation_id}'.",
        )
    return rows
