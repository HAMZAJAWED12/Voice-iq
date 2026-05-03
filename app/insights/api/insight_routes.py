"""HTTP routes for the Insight Service.

The repository is supplied via FastAPI's dependency injection
(`Depends(get_insight_repository)`), which lets tests swap in an
isolated in-process repository without monkey-patching imports.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.insights.models.api_models import (
    InsightGenerateResponse,
    InsightSpeakersResponse,
    InsightStoredRecord,
    InsightSummaryResponse,
    InsightTimelineResponse,
)
from app.insights.models.input_models import SessionInput
from app.insights.repository import InsightRepository, get_insight_repository
from app.insights.service import InsightService

router = APIRouter(prefix="/insights", tags=["Insights"])


def _store_response(repository: InsightRepository, response: InsightGenerateResponse) -> None:
    """Persist a freshly-generated response, isolating storage failures."""
    repository.save(
        InsightStoredRecord(
            session_id=response.session_id,
            status=response.status,
            payload=response,
        )
    )


@router.post(
    "/generate",
    response_model=InsightGenerateResponse,
    summary="Generate insights for a structured session",
    description=(
        "Run the full Insight Service pipeline (analytics → signals → "
        "rules → summary) over a validated `SessionInput` payload, "
        "persist the generated bundle, and return it."
    ),
    status_code=status.HTTP_200_OK,
)
def generate_insights(
    payload: SessionInput,
    repository: InsightRepository = Depends(get_insight_repository),
) -> InsightGenerateResponse:
    response = InsightService.generate(payload)
    _store_response(repository, response)
    return response


@router.post(
    "/generate-from-raw",
    response_model=InsightGenerateResponse,
    summary="Generate insights from a raw upstream payload",
    description=(
        "Accept a loosely-typed dict (e.g. straight from the ASR/NLP "
        "pipeline), validate it into a `SessionInput`, then run the same "
        "generation pipeline as `/generate`."
    ),
    status_code=status.HTTP_200_OK,
)
def generate_insights_from_raw(
    payload: dict,
    repository: InsightRepository = Depends(get_insight_repository),
) -> InsightGenerateResponse:
    response = InsightService.generate_from_raw(payload)
    _store_response(repository, response)
    return response


@router.get(
    "/{session_id}",
    response_model=InsightGenerateResponse,
    summary="Fetch the full insight bundle for a session",
)
def get_full_insight_record(
    session_id: str,
    repository: InsightRepository = Depends(get_insight_repository),
) -> InsightGenerateResponse:
    record = repository.get(session_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return record.payload


@router.get(
    "/{session_id}/summary",
    response_model=InsightSummaryResponse,
    summary="Fetch the conversation-level summary for a session",
)
def get_summary(
    session_id: str,
    repository: InsightRepository = Depends(get_insight_repository),
) -> InsightSummaryResponse:
    record = repository.get(session_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    payload = record.payload
    return InsightSummaryResponse(
        session_id=payload.session_id,
        overall_summary=payload.summaries.overall_summary,
        notable_concerns=payload.summaries.notable_concerns,
        key_moments_summary=payload.summaries.key_moments_summary,
    )


@router.get(
    "/{session_id}/speakers",
    response_model=InsightSpeakersResponse,
    summary="Fetch per-speaker metrics, insights and summaries",
)
def get_speakers(
    session_id: str,
    repository: InsightRepository = Depends(get_insight_repository),
) -> InsightSpeakersResponse:
    record = repository.get(session_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    payload = record.payload

    return InsightSpeakersResponse(
        session_id=payload.session_id,
        speaker_metrics={speaker: metric.model_dump() for speaker, metric in payload.analytics.speaker_metrics.items()},
        speaker_insights={
            speaker: insight.model_dump() for speaker, insight in payload.insights.speaker_insights.items()
        },
        speaker_summaries=payload.summaries.speaker_summaries,
    )


@router.get(
    "/{session_id}/timeline",
    response_model=InsightTimelineResponse,
    summary="Fetch the chronological timeline markers for a session",
)
def get_timeline(
    session_id: str,
    repository: InsightRepository = Depends(get_insight_repository),
) -> InsightTimelineResponse:
    record = repository.get(session_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )

    payload = record.payload
    return InsightTimelineResponse(
        session_id=payload.session_id,
        timeline=[marker.model_dump() for marker in payload.insights.timeline],
    )


@router.get(
    "/",
    response_model=list[str],
    summary="List every persisted session_id",
)
def list_sessions(
    repository: InsightRepository = Depends(get_insight_repository),
) -> list[str]:
    return repository.list_session_ids()


@router.delete(
    "/{session_id}",
    summary="Delete a persisted insight record",
)
def delete_session(
    session_id: str,
    repository: InsightRepository = Depends(get_insight_repository),
) -> dict:
    deleted = repository.delete(session_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found.",
        )
    return {"status": "deleted", "session_id": session_id}
