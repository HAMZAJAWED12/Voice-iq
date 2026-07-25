"""The Agent Brain router must actually be reachable on the app (regression).

Sprint 6 built and fully tested the recommendation API, but nothing ever
called `include_router` for it — the only place it was wired up was inside
its own test fixture, so the endpoint 404'd on every real deployment. These
tests pin the mount and the Tier S guards that come with being exposed.

app.main pulls the heavy pipeline, so the route table is inspected without
importing it where possible, and TestClient is used only for behaviour.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent_brain.api.agent_routes import router as agent_router
from app.insights.config.settings import get_settings

_PATH = "/internal/v1/agent-brain/recommendations/generate"
_BODY = {
    "sessionId": "s-1",
    "transcript": [{"segmentId": "seg-1", "speakerId": "spk-1", "text": "Ali will send the report by Friday."}],
}


def test_router_is_mounted_on_app_main() -> None:
    """The regression this file exists for: main.py must include the router."""
    import app.main as main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert _PATH in paths, "Agent Brain router is not mounted on app.main"


def test_router_is_mounted_on_insight_main() -> None:
    """The lightweight image serves it too — Agent Brain has no ML deps."""
    import app.insight_main as insight_main

    paths = {getattr(r, "path", None) for r in insight_main.app.routes}
    assert _PATH in paths, "Agent Brain router is not mounted on app.insight_main"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(agent_router)
    return TestClient(app)


def test_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    get_settings.cache_clear()
    try:
        resp = _client().post(_PATH, json=_BODY)
        assert resp.status_code == 401  # no X-API-Key header
    finally:
        get_settings.cache_clear()


def test_generates_recommendations_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        resp = _client().post(_PATH, json=_BODY, headers={"X-API-Key": "testkey"})
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["sessionId"] == "s-1"
        assert isinstance(payload["recommendations"], list)
    finally:
        get_settings.cache_clear()


def test_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_AGENT_BRAIN", "2/minute")
    get_settings.cache_clear()

    from app.security import rate_limit

    rate_limit.reset()
    try:
        client = _client()
        headers = {"X-API-Key": "testkey"}
        codes = [client.post(_PATH, json=_BODY, headers=headers).status_code for _ in range(3)]
        assert codes[:2] == [200, 200]
        assert codes[2] == 429
    finally:
        rate_limit.reset()
        get_settings.cache_clear()


def test_recommend_only_contract_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every recommendation must still be approval-gated (never auto-executed)."""
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_ENABLED", "false")
    get_settings.cache_clear()
    try:
        resp = _client().post(_PATH, json=_BODY, headers={"X-API-Key": "testkey"})
        for rec in resp.json()["recommendations"]:
            assert rec["requiresApproval"] is True
    finally:
        get_settings.cache_clear()
