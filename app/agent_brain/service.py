"""AgentBrainService: public entry point for recommendation generation."""

from __future__ import annotations

from app.agent_brain.core.agent_runner import AgentRunner
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import RecommendationResponse


class AgentBrainService:
    """Stateless Phase-1 service: context in, ranked recommendations out."""

    def __init__(self, runner: AgentRunner | None = None) -> None:
        self.runner = runner or AgentRunner()

    def generate(self, context: AgentContext) -> RecommendationResponse:
        recommendations = self.runner.run(context)
        return RecommendationResponse(session_id=context.session_id, recommendations=recommendations)
