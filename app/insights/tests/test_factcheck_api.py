"""Integration tests for the POST /v1/fact-check endpoint."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.api.factcheck_routes import (
    get_factcheck_engine,
    router as factcheck_router,
)
from app.insights.core.factcheck.factcheck_engine import (
    FactCheckEngine,
    SourceRouter,
)
from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence
from app.insights.repository import (
    FactCheckRepository,
    build_factcheck_repository_from_factory,
    get_factcheck_repository,
)
from app.insights.repository.db import Base
from app.insights.repository.factcheck_orm_models import FactCheckResultORM  # noqa: F401


# --------------------------------------------------------------------------- #
# Stubs                                                                       #
# --------------------------------------------------------------------------- #

class _StubClient(BaseSourceClient):
    def __init__(self, *, name: str, evidence: Optional[Evidence]) -> None:
        super().__init__(timeout_sec=1.0)
        self.name = name
        self._evidence = evidence

    def fetch(self, claim: DetectedClaim) -> Optional[Evidence]:
        return self._evidence


def _stub_engine() -> FactCheckEngine:
    """Engine wired with deterministic stub clients (no live network)."""
    router = SourceRouter(
        {
            "CRYPTO_PRICE": _StubClient(
                name="crypto_stub",
                evidence=Evidence(source="crypto_stub", value=95000.0, unit="USD"),
            ),
            "STATIC_FACT": _StubClient(
                name="static_stub",
                evidence=Evidence(source="static_stub", value_text="Paris"),
            ),
        }
    )
    return FactCheckEngine(router=router, cache_ttl_sec=60.0)


# --------------------------------------------------------------------------- #
# Fixture: app + isolated DB + stubbed engine                                 #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Per-test SQLite DB on disk so TestClient threads share the same store."""
    # Make sure ORM modules are imported so tables register on Base.metadata.
    from app.insights.repository import (  # noqa: F401
        factcheck_orm_models,
        orm_models,
    )

    db_path = tmp_path / "test_factcheck.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    repo = build_factcheck_repository_from_factory(factory)

    app = FastAPI()
    app.include_router(factcheck_router, prefix="/v1")
    app.dependency_overrides[get_factcheck_engine] = _stub_engine
    app.dependency_overrides[get_factcheck_repository] = lambda: repo

    with TestClient(app) as c:
        yield c

    engine.dispose()
    if db_path.exists():
        os.remove(db_path)


# --------------------------------------------------------------------------- #
# POST /v1/fact-check                                                         #
# --------------------------------------------------------------------------- #

def test_post_returns_results_for_crypto_claim(client: TestClient):
    payload = {
        "conversation_id": "conv_1",
        "speaker_id": "spk_1",
        "transcript_text": "Bitcoin is at 95000 dollars",
    }
    response = client.post("/v1/fact-check", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == "conv_1"
    assert body["stats"]["claims_detected"] == 1
    assert body["fact_check_results"][0]["verdict"] == "TRUE"


def test_post_returns_empty_results_for_no_claim_text(client: TestClient):
    payload = {
        "conversation_id": "conv_1",
        "speaker_id": "spk_1",
        "transcript_text": "Hello, how are you today?",
    }
    response = client.post("/v1/fact-check", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["fact_check_results"] == []
    assert body["stats"]["claims_detected"] == 0


def test_post_handles_static_fact_match(client: TestClient):
    payload = {
        "conversation_id": "conv_1",
        "speaker_id": "spk_1",
        "transcript_text": "capital of France is Paris",
    }
    response = client.post("/v1/fact-check", json=payload)
    assert response.status_code == 200
    body = response.json()
    result = body["fact_check_results"][0]
    assert result["verdict"] == "TRUE"
    assert result["confidence"]["label"] == "HIGH"


def test_post_marks_static_fact_mismatch_as_false(client: TestClient):
    payload = {
        "conversation_id": "conv_1",
        "speaker_id": "spk_1",
        "transcript_text": "capital of France is Tokyo",
    }
    response = client.post("/v1/fact-check", json=payload)
    body = response.json()
    assert body["fact_check_results"][0]["verdict"] == "FALSE"


def test_post_rejects_empty_transcript_text(client: TestClient):
    payload = {
        "conversation_id": "conv_1",
        "speaker_id": "spk_1",
        "transcript_text": "",
    }
    response = client.post("/v1/fact-check", json=payload)
    assert response.status_code == 422


def test_post_rejects_oversized_transcript_text(client: TestClient):
    payload = {
        "conversation_id": "conv_1",
        "speaker_id": "spk_1",
        "transcript_text": "x" * 10_001,
    }
    response = client.post("/v1/fact-check", json=payload)
    assert response.status_code == 422


def test_post_persists_results_to_repository(client: TestClient):
    payload = {
        "conversation_id": "conv_persist",
        "speaker_id": "spk_1",
        "transcript_text": "Bitcoin is at 95000 dollars",
    }
    client.post("/v1/fact-check", json=payload)
    listing = client.get("/v1/fact-check/conv_persist")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "conv_persist"
    assert rows[0]["claim_type"] == "CRYPTO_PRICE"


def test_get_returns_404_for_unknown_conversation(client: TestClient):
    response = client.get("/v1/fact-check/never_existed")
    assert response.status_code == 404
