"""An error result never replaces a stored insight record that is not an error.

The caller still gets the error response; the stored record stays the last
good one. Per-test SQLite databases; nothing leaves the host.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.api import router as insight_router
from app.insights.models.api_models import InsightStoredRecord
from app.insights.models.input_models import SessionInput
from app.insights.repository import InsightRepository, get_insight_repository
from app.insights.repository.db import Base
from app.insights.service import InsightService
from app.security import verify_api_key


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[InsightRepository]:
    engine = create_engine(f"sqlite:///{tmp_path / 'i.db'}", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    try:
        yield InsightRepository(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    finally:
        engine.dispose()


def _session(session_id: str = "s1") -> dict:
    return {
        "session_id": session_id,
        "utterances": [{"id": "u1", "speaker": "S1", "start": 0.0, "end": 1.0, "text": "hello there", "word_count": 2}],
    }


def _record(status: str) -> InsightStoredRecord:
    payload = InsightService.generate(SessionInput.model_validate(_session())).model_copy(update={"status": status})
    return InsightStoredRecord(session_id="s1", status=status, payload=payload)


@pytest.mark.parametrize(
    ("stored", "incoming", "kept"),
    [
        ("ok", "error", "ok"),
        ("warning", "error", "warning"),
        ("error", "error", "error"),
        ("error", "ok", "ok"),
        ("ok", "warning", "warning"),
    ],
)
def test_only_a_non_error_record_is_protected(repo: InsightRepository, stored: str, incoming: str, kept: str) -> None:
    repo.save(_record(stored))

    repo.save(_record(incoming))

    record = repo.get("s1")
    assert record is not None
    assert (record.status, record.payload.status) == (kept, kept)


def test_a_failed_regeneration_returns_the_error_but_keeps_the_stored_record(repo: InsightRepository) -> None:
    app = FastAPI()
    app.include_router(insight_router, prefix="/v1")
    app.dependency_overrides[get_insight_repository] = lambda: repo
    app.dependency_overrides[verify_api_key] = lambda: "test-key"

    with TestClient(app) as client:
        good = client.post("/v1/insights/generate", json=_session())
        failed = client.post("/v1/insights/generate-from-raw", json={"session_id": "s1", "utterances": "not a list"})
        stored = client.get("/v1/insights/s1")

    assert good.json()["status"] == "ok"
    assert failed.status_code == 200 and failed.json()["status"] == "error"
    assert stored.json()["status"] == "ok"
    assert stored.json()["summaries"] == good.json()["summaries"]
