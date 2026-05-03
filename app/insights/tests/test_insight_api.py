"""End-to-end integration tests for the Insight Service HTTP surface.

These tests exercise the real FastAPI router + SQLite-backed repository
through `TestClient`. We override the `get_insight_repository` dependency
so each test gets its own isolated, file-backed database — no shared
state, no monkey-patching of module-level singletons.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.api import router as insight_router
from app.insights.repository import (
    InsightRepository,
    get_insight_repository,
)
from app.insights.repository.db import Base
from app.insights.repository.orm_models import InsightRecordORM  # noqa: F401

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def isolated_repo(tmp_path: Path) -> Iterator[InsightRepository]:
    """Per-test SQLite DB on disk. No interaction with the prod singleton."""
    db_path = tmp_path / "test_insights.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    repo = InsightRepository(session_factory=factory)
    try:
        yield repo
    finally:
        engine.dispose()
        if db_path.exists():
            os.remove(db_path)


@pytest.fixture
def client(isolated_repo: InsightRepository) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(insight_router, prefix="/v1")
    app.dependency_overrides[get_insight_repository] = lambda: isolated_repo
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _sample_session(session_id: str = "api_session") -> dict:
    """A small but realistic SessionInput payload."""
    return {
        "session_id": session_id,
        "utterances": [
            {
                "id": "u1",
                "speaker": "S1",
                "start": 0.0,
                "end": 2.0,
                "text": "this is terrible and awful",
                "word_count": 5,
                "sentiment": {"label": "positive", "score": 0.9},
                "emotion": {"values": {"angry": 0.8, "calm": 0.2}},
            },
            {
                "id": "u2",
                "speaker": "S2",
                "start": 2.5,
                "end": 4.5,
                "text": "I really hate this experience",
                "word_count": 5,
                "sentiment": {"label": "positive", "score": 0.85},
                "emotion": {"values": {"frustrated": 0.7, "calm": 0.3}},
            },
            {
                "id": "u3",
                "speaker": "S1",
                "start": 4.6,
                "end": 6.0,
                "text": "actually no I disagree",
                "word_count": 4,
                "sentiment": {"label": "negative", "score": 0.3},
                "emotion": {"values": {"angry": 0.65, "frustrated": 0.35}},
            },
        ],
    }


# --------------------------------------------------------------------------- #
# Generate + persist
# --------------------------------------------------------------------------- #


def test_generate_returns_full_bundle_and_persists(client: TestClient) -> None:
    payload = _sample_session()
    response = client.post("/v1/insights/generate", json=payload)
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["session_id"] == "api_session"
    assert body["status"] == "ok"
    assert "analytics" in body
    assert "insights" in body
    assert "summaries" in body
    assert "meta" in body

    # Insight bundle must include the new escalation + inconsistency blocks.
    insights = body["insights"]
    assert "escalation" in insights and insights["escalation"] is not None
    assert "inconsistency" in insights and insights["inconsistency"] is not None

    # The record should now be retrievable.
    listing = client.get("/v1/insights/").json()
    assert "api_session" in listing


def test_generate_from_raw_validates_and_persists(client: TestClient) -> None:
    raw = _sample_session(session_id="raw_session")
    response = client.post("/v1/insights/generate-from-raw", json=raw)
    assert response.status_code == 200, response.text
    assert response.json()["session_id"] == "raw_session"

    assert client.get("/v1/insights/raw_session").status_code == 200


# --------------------------------------------------------------------------- #
# Sub-resource GETs
# --------------------------------------------------------------------------- #


def _seed(client: TestClient, session_id: str = "api_session") -> dict:
    payload = _sample_session(session_id)
    response = client.post("/v1/insights/generate", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_get_full_record_returns_persisted_payload(client: TestClient) -> None:
    seeded = _seed(client)
    fetched = client.get("/v1/insights/api_session")
    assert fetched.status_code == 200
    body = fetched.json()
    # Round-trip equality: what we generated is what we persisted.
    assert body["session_id"] == seeded["session_id"]
    assert body["insights"]["timeline"] == seeded["insights"]["timeline"]


def test_get_summary_exposes_overall_summary(client: TestClient) -> None:
    _seed(client)
    response = client.get("/v1/insights/api_session/summary")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "api_session"
    assert isinstance(body["overall_summary"], str)
    assert isinstance(body["notable_concerns"], list)
    assert "key_moments_summary" in body


def test_get_speakers_exposes_per_speaker_buckets(client: TestClient) -> None:
    _seed(client)
    response = client.get("/v1/insights/api_session/speakers")
    assert response.status_code == 200
    body = response.json()
    assert set(body["speaker_metrics"].keys()) == {"S1", "S2"}
    # Each speaker_metrics value must look like a SpeakerMetrics dict.
    for sp, metric in body["speaker_metrics"].items():
        assert metric["speaker"] == sp
        assert "speaking_time_sec" in metric


def test_get_timeline_returns_marker_list(client: TestClient) -> None:
    _seed(client)
    response = client.get("/v1/insights/api_session/timeline")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "api_session"
    assert isinstance(body["timeline"], list)
    # The noisy fixture should produce at least one marker.
    assert len(body["timeline"]) >= 1
    for marker in body["timeline"]:
        assert "type" in marker
        assert "time_sec" in marker
        assert "severity" in marker


# --------------------------------------------------------------------------- #
# Listing + deletion + 404 contract
# --------------------------------------------------------------------------- #


def test_list_sessions_after_multiple_generates(client: TestClient) -> None:
    _seed(client, "session_one")
    _seed(client, "session_two")
    listing = client.get("/v1/insights/").json()
    assert {"session_one", "session_two"}.issubset(set(listing))


def test_delete_removes_record_and_subsequent_get_404s(client: TestClient) -> None:
    _seed(client)

    deleted = client.delete("/v1/insights/api_session")
    assert deleted.status_code == 200
    assert deleted.json() == {"status": "deleted", "session_id": "api_session"}

    follow_up = client.get("/v1/insights/api_session")
    assert follow_up.status_code == 404


def test_get_unknown_session_returns_404_with_detail(client: TestClient) -> None:
    response = client.get("/v1/insights/does-not-exist")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_delete_unknown_session_returns_404(client: TestClient) -> None:
    response = client.delete("/v1/insights/does-not-exist")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# Validation contract
# --------------------------------------------------------------------------- #


def test_generate_rejects_session_with_no_utterances(client: TestClient) -> None:
    bad = {"session_id": "bad", "utterances": []}
    response = client.post("/v1/insights/generate", json=bad)
    # Pydantic validation on SessionInput rejects empty utterance lists.
    assert response.status_code == 422


def test_generate_from_raw_surfaces_validation_errors_in_body(
    client: TestClient,
) -> None:
    # `generate_from_raw` is intentionally permissive: it returns 200 with
    # a structured `validation` block describing the problem rather than
    # rejecting the request outright. Tests that contract here.
    response = client.post("/v1/insights/generate-from-raw", json={"session_id": "x"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["validation"]["valid"] is False
    error_codes = {err["code"] for err in body["validation"]["errors"]}
    assert "missing_utterances" in error_codes
