"""Internal exception text stays out of responses and logs.

Exception messages can carry file paths, provider details or input values.
Responses give a fixed message and logs name only the exception class.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.insights import service as insight_service
from app.insights.api import router as insight_router
from app.insights.repository import get_insight_repository
from app.pipeline.orchestrator import VoiceIQOrchestrator
from app.routes import process_audio
from app.security.api_key import verify_api_key
from app.security.rate_limit import process_audio_rate_limit
from app.utils.job_io import JobIO

MARKER = r"SECRET-MARKER C:\internal\path\config.yaml"
# JSON escapes backslashes, so responses are searched for the plain token.
TOKEN = "SECRET-MARKER"
_WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 120


class _MemoryRepository:
    def __init__(self) -> None:
        self.records: dict = {}

    def save(self, record) -> None:
        self.records[record.session_id] = record

    def get(self, session_id):
        return self.records.get(session_id)

    def exists(self, session_id) -> bool:
        return session_id in self.records


def _raise(*_args, **_kwargs):
    raise RuntimeError(MARKER)


@pytest.fixture
def insight_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(insight_service.InsightAnalyticsEngine, "run", staticmethod(_raise))
    repo = _MemoryRepository()
    app = FastAPI()
    app.include_router(insight_router, prefix="/v1")
    app.dependency_overrides[get_insight_repository] = lambda: repo
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    with TestClient(app) as client:
        yield client


def test_a_failed_insight_run_does_not_return_the_exception_text(insight_client, caplog):
    session = {"session_id": "s-err", "utterances": [{"id": "u1", "speaker": "a", "start": 0, "end": 1, "text": "hi"}]}

    with caplog.at_level(logging.DEBUG):
        posted = insight_client.post("/v1/insights/generate", json=session)
        read = insight_client.get("/v1/insights/s-err")

    assert posted.status_code == 200
    errors = posted.json()["validation"]["errors"]
    assert [e["code"] for e in errors] == ["pipeline_execution_error"]
    assert TOKEN not in posted.text
    assert TOKEN not in read.text
    assert TOKEN not in caplog.text
    assert "RuntimeError" in caplog.text


def test_a_failed_fact_check_enrichment_gives_a_fixed_reason(tmp_path: Path, monkeypatch, caplog):
    def ok_run(self, *, job_id, **_kwargs) -> dict:
        return {
            "request_id": job_id,
            "transcript": "BTC is at 95000.",
            "fact_checks": [],
            "warnings": [],
            "pipeline_meta": {"status": "ok", "timings_ms": {}, "skipped_steps": []},
        }

    class _Engine:
        def run(self, **_kwargs):
            raise RuntimeError(MARKER)

    monkeypatch.setattr(VoiceIQOrchestrator, "run", ok_run)
    monkeypatch.setattr(process_audio, "JobIO", lambda: JobIO(base_dir=tmp_path / "jobs"))
    monkeypatch.setattr(process_audio, "get_factcheck_engine", lambda: _Engine())
    app = FastAPI()
    app.include_router(process_audio.router, prefix="/v1")
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    app.dependency_overrides[process_audio_rate_limit] = lambda: None

    with caplog.at_level(logging.DEBUG), TestClient(app) as client:
        response = client.post(
            "/v1/process-audio",
            params={"fact_check": "true"},
            files={"file": ("call.wav", _WAV_BYTES, "audio/wav")},
        )

    assert response.status_code == 200
    assert response.json()["fact_checks_v2"] == {"status": "failed", "reason": "fact-check enrichment failed"}
    assert TOKEN not in response.text
    assert TOKEN not in caplog.text
    assert "RuntimeError" in caplog.text
