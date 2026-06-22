"""Internal Agent Brain API.

Internal-only (doc 15): every route requires a valid X-API-Key. The
request/response bodies use the camelCase Java contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import RecommendationResponse
from app.agent_brain.service import AgentBrainService
from app.security import verify_api_key

router = APIRouter(
    prefix="/internal/v1/agent-brain",
    tags=["agent-brain"],
    dependencies=[Depends(verify_api_key)],
)

_service = AgentBrainService()


@router.post("/recommendations/generate", response_model=RecommendationResponse)
def generate_recommendations(context: AgentContext) -> RecommendationResponse:
    """Generate ranked, deduplicated action recommendations for one session."""
    return _service.generate(context)
