"""Coverage for Agent Brain models: camelCase contract + schema invariants."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import (
    SCHEMA_VERSION,
    Recommendation,
    RecommendationResponse,
    Source,
)


def _rec(**overrides):
    base = dict(
        agent_type="TASK_AGENT",
        action_type="TASK",
        title="Prepare report",
        description="Ali was assigned to prepare the report.",
        confidence=0.91,
        source=Source(text="Ali will prepare the report by Friday."),
        explanation="Sentence contains an assignment and a deadline.",
    )
    base.update(overrides)
    return Recommendation(**base)


# --------------------------------------------------------------------------- #
# Recommendation                                                              #
# --------------------------------------------------------------------------- #


def test_recommendation_dumps_camelcase() -> None:
    data = _rec().model_dump(by_alias=True)
    # Every multi-word field must serialise camelCase for the Java contract.
    for key in ("recommendationId", "agentType", "actionType", "requiresApproval", "linkedClaimId", "suggestedPayload"):
        assert key in data
    assert "agent_type" not in data
    assert data["requiresApproval"] is True


def test_recommendation_id_is_uuid4_by_default() -> None:
    rid = _rec().recommendation_id
    assert uuid.UUID(rid).version == 4


def test_requires_approval_locked_true() -> None:
    # Literal[True] rejects any attempt to emit a non-approval recommendation.
    with pytest.raises(ValidationError):
        _rec(requires_approval=False)


def test_recommendation_rejects_empty_text_fields() -> None:
    with pytest.raises(ValidationError):
        _rec(title="")
    with pytest.raises(ValidationError):
        _rec(explanation="")
    with pytest.raises(ValidationError):
        _rec(source=Source(text=""))


def test_confidence_bounds_enforced() -> None:
    with pytest.raises(ValidationError):
        _rec(confidence=1.5)
    with pytest.raises(ValidationError):
        _rec(confidence=-0.1)


def test_priority_defaults_medium_and_entities_empty() -> None:
    rec = _rec()
    assert rec.priority == "MEDIUM"
    assert rec.entities.assignee is None
    assert rec.suggested_payload == {}


# --------------------------------------------------------------------------- #
# AgentContext (input contract)                                               #
# --------------------------------------------------------------------------- #


def test_agent_context_parses_camelcase_json() -> None:
    payload = {
        "sessionId": "s-1",
        "organizationId": "org-1",
        "language": "en",
        "transcript": [
            {"segmentId": "seg-1", "speakerId": "speaker_1", "text": "Send me the proposal.", "confidence": 0.94}
        ],
        "insights": {"summary": "x", "topics": ["pricing"], "escalationFlags": []},
        "factChecks": {"claims": [{"claimId": "c1", "claimText": "ISO certified", "status": "UNVERIFIED"}]},
    }
    ctx = AgentContext.model_validate(payload)
    assert ctx.session_id == "s-1"
    assert ctx.transcript[0].segment_id == "seg-1"
    assert ctx.insights.topics == ["pricing"]
    assert ctx.fact_checks.claims[0].status == "UNVERIFIED"


def test_agent_context_defaults_are_empty() -> None:
    ctx = AgentContext(session_id="s")
    assert ctx.language == "en"
    assert ctx.transcript == []
    assert ctx.insights.topics == []
    assert ctx.fact_checks.claims == []


def test_agent_context_rejects_unknown_language() -> None:
    with pytest.raises(ValidationError):
        AgentContext(session_id="s", language="fr")


# --------------------------------------------------------------------------- #
# Response envelope                                                           #
# --------------------------------------------------------------------------- #


def test_response_envelope_camelcase_and_schema_version() -> None:
    resp = RecommendationResponse(session_id="s", recommendations=[_rec()])
    data = resp.model_dump(by_alias=True)
    assert data["schemaVersion"] == SCHEMA_VERSION
    assert data["sessionId"] == "s"
    assert len(data["recommendations"]) == 1
