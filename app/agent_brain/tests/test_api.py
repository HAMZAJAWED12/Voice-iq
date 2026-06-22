"""Coverage for the Agent Brain API route."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_brain.api.agent_routes import router
from app.security import verify_api_key

_URL = "/internal/v1/agent-brain/recommendations/generate"


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    return TestClient(app)


def test_generate_returns_camelcase_recommendations(client: TestClient) -> None:
    body = {
        "sessionId": "s1",
        "language": "en",
        "transcript": [
            {"segmentId": "seg-1", "speakerId": "speaker_1", "text": "Please send me the pricing proposal by Friday."}
        ],
    }
    resp = client.post(_URL, json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["sessionId"] == "s1"
    assert data["schemaVersion"] == "1.0"
    assert data["recommendations"]
    rec = data["recommendations"][0]
    assert rec["agentType"] == "EMAIL_DRAFT_AGENT"
    assert rec["requiresApproval"] is True


def test_generate_empty_transcript(client: TestClient) -> None:
    resp = client.post(_URL, json={"sessionId": "s2"})
    assert resp.status_code == 200
    assert resp.json()["recommendations"] == []


def test_generate_rejects_invalid_language(client: TestClient) -> None:
    resp = client.post(_URL, json={"sessionId": "s3", "language": "fr"})
    assert resp.status_code == 422
