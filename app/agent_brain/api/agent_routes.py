"""Internal Agent Brain API.

Internal-only (doc 15): every route requires a valid X-API-Key. The
request/response bodies use the camelCase Java contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import RecommendationResponse
from app.agent_brain.service import AgentBrainService
from app.insights.config.settings import get_settings
from app.security import enforce_content_length, verify_api_key
from app.security.rate_limit import agent_brain_rate_limit

# Same guards as every other network-reachable route (Tier S): API key,
# a payload cap sized from settings, and a per-key rate limit. The body
# carries a whole transcript and the handler runs five agents over it, so
# it is neither cheap nor unbounded.
_max_payload_bytes = get_settings().api_max_session_payload_kb * 1024
_enforce_payload_size = enforce_content_length(_max_payload_bytes)

router = APIRouter(
    prefix="/internal/v1/agent-brain",
    tags=["agent-brain"],
    dependencies=[
        Depends(verify_api_key),
        Depends(_enforce_payload_size),
        Depends(agent_brain_rate_limit),
    ],
)

_service = AgentBrainService()


@router.post("/recommendations/generate", response_model=RecommendationResponse)
def generate_recommendations(context: AgentContext) -> RecommendationResponse:
    """Generate ranked, deduplicated action recommendations for one session."""
    return _service.generate(context)
