"""Caller-sent numbers must be finite on the insight and Agent Brain routes.

JSON parsing accepts the NaN, Infinity and 1e999 tokens. A non-finite number
in a session used to be stored and then fail every response that rendered
it, so the record could never be read again. Bodies are sent as raw bytes so
those tokens reach the server.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agent_brain.api.agent_routes import router as agent_router
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.service import AgentBrainService
from app.insights.api import router as insight_router
from app.insights.repository import InsightRepository, get_insight_repository
from app.insights.repository.orm_models import Base
from app.security import rate_limit
from app.security.api_key import verify_api_key


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[InsightRepository]:
    db_path = tmp_path / "insights.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    yield InsightRepository(session_factory=factory)
    engine.dispose()
    if db_path.exists():
        os.remove(db_path)


@pytest.fixture
def client(repo: InsightRepository) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(insight_router, prefix="/v1")
    app.include_router(agent_router)
    app.dependency_overrides[get_insight_repository] = lambda: repo
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    rate_limit.reset()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _session(session_id: str, utterance: str) -> bytes:
    """A session whose one utterance is the raw JSON text `utterance`."""
    return ('{"session_id": "' + session_id + '", "utterances": [' + utterance + '], "speakers": ["agent"]}').encode()


def _post(client: TestClient, path: str, body: bytes):
    return client.post(path, content=body, headers={"content-type": "application/json"})


_UTTERANCES = {
    "emotion-nan": '{"id": "u1", "speaker": "agent", "start": 0, "end": 2, "text": "hello", '
    '"emotion": {"values": {"calm": NaN, "angry": 0.1}}}',
    "end-infinity": '{"id": "u1", "speaker": "agent", "start": 0, "end": Infinity, "text": "hello"}',
    "end-1e999": '{"id": "u1", "speaker": "agent", "start": 0, "end": 1e999, "text": "hello"}',
    "start-infinity": '{"id": "u1", "speaker": "agent", "start": Infinity, "end": Infinity, "text": "hello"}',
}


@pytest.mark.parametrize("case", sorted(_UTTERANCES))
def test_generate_refuses_non_finite_numbers_and_stores_nothing(case, client, repo):
    response = _post(client, "/v1/insights/generate", _session(f"s-{case}", _UTTERANCES[case]))

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "json_invalid"
    assert repo.get(f"s-{case}") is None


def test_generate_refuses_a_non_finite_number_in_free_form_stats(client, repo):
    body = b'{"session_id": "s-stats", "utterances": [], "speaker_stats": {"agent": {"talk_ratio": NaN}}}'

    assert _post(client, "/v1/insights/generate", body).status_code == 422
    assert repo.get("s-stats") is None


def test_generate_refuses_a_non_finite_duration(client, repo):
    body = b'{"session_id": "s-dur", "duration_sec": Infinity, "utterances": []}'

    assert _post(client, "/v1/insights/generate", body).status_code == 422
    assert repo.get("s-dur") is None


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_generate_from_raw_refuses_non_finite_numbers_and_stores_nothing(token, client, repo):
    raw = (
        '{"session_id": "s-raw", "utterances": [{"id": "u1", "speaker": "agent", "start": 0, "end": ' + token + "}]}"
    ).encode()

    response = _post(client, "/v1/insights/generate-from-raw", raw)

    assert response.status_code == 422
    assert response.json()["detail"][0]["type"] == "json_invalid"
    assert repo.get("s-raw") is None


def test_the_normalizer_treats_non_finite_numbers_like_invalid_ones():
    from app.insights.core.normalizer import InsightNormalizer

    session = InsightNormalizer.normalize_to_session_input(
        {"session_id": "s-n", "utterances": [{"id": "u1", "speaker": "a", "start": 0, "end": float("inf")}]}
    )

    assert all(math.isfinite(u.end) for u in session.utterances)


def test_finite_sessions_are_unchanged(client, repo):
    ok = '{"id": "u1", "speaker": "agent", "start": 0, "end": 2, "text": "hello", "emotion": {"values": {"calm": 0.5}}}'

    response = _post(client, "/v1/insights/generate", _session("s-ok", ok))

    assert response.status_code == 200
    assert client.get("/v1/insights/s-ok").status_code == 200


def _context(confidence) -> AgentContext:
    return AgentContext.model_validate(
        {
            "sessionId": "s-agent",
            "transcript": [
                {
                    "segmentId": "seg-1",
                    "speakerId": "spk-1",
                    "text": "Ali will send the report by Friday.",
                    "confidence": confidence,
                }
            ],
            "factChecks": {
                "claims": [{"claimId": "c1", "claimText": "X", "status": "FALSE", "confidence": confidence}]
            },
        }
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_agent_brain_treats_a_non_finite_confidence_as_missing(value):
    expected = AgentBrainService().generate(_context(None))
    actual = AgentBrainService().generate(_context(value))

    assert [r.confidence for r in actual.recommendations] == [r.confidence for r in expected.recommendations]


def test_agent_brain_route_accepts_non_finite_confidence_tokens(client):
    body = (
        b'{"sessionId": "s-a", "transcript": [{"segmentId": "seg-1", "speakerId": "spk-1", '
        b'"text": "Ali will send the report by Friday.", "confidence": NaN}]}'
    )

    response = _post(client, "/internal/v1/agent-brain/recommendations/generate", body)

    assert response.status_code == 200
    json.dumps(response.json(), allow_nan=False)
