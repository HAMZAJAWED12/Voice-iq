"""The audio pipeline runs off the event loop, one run at a time per worker.

While a pipeline run is in progress, other requests on the same worker are
still served; a second upload waits for the slot instead of running beside
the first. The pipeline is stubbed with events so the overlap is
deterministic.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.pipeline.orchestrator import VoiceIQOrchestrator
from app.routes import process_audio
from app.security.api_key import verify_api_key
from app.security.rate_limit import process_audio_rate_limit
from app.utils.job_io import JobIO

_WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 120
_TIMEOUT = 5.0


class _Gate:
    """Counts pipeline runs and holds each one until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0
        self.runs = 0

    def run(self, _orch, *, job_id, **_kwargs) -> dict:
        with self.lock:
            self.running += 1
            self.runs += 1
            self.max_running = max(self.max_running, self.running)
        self.entered.set()
        try:
            assert self.release.wait(_TIMEOUT), "test never released the pipeline"
        finally:
            with self.lock:
                self.running -= 1
        return {
            "request_id": job_id,
            "transcript": "",
            "fact_checks": [],
            "warnings": [],
            "pipeline_meta": {"status": "ok", "timings_ms": {}, "skipped_steps": []},
        }


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Gate:
    gate = _Gate()
    monkeypatch.setattr(VoiceIQOrchestrator, "run", lambda self, **kw: gate.run(self, **kw))
    monkeypatch.setattr(process_audio, "JobIO", lambda: JobIO(base_dir=tmp_path / "jobs"))
    return gate


@pytest.fixture
def client(gate: _Gate) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(process_audio.router, prefix="/v1")
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    app.dependency_overrides[process_audio_rate_limit] = lambda: None

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    gate.release.set()


def _upload(client: TestClient, results: list, index: int) -> None:
    response = client.post("/v1/process-audio", files={"file": ("call.wav", _WAV_BYTES, "audio/wav")})
    results[index] = response


def test_other_requests_are_served_while_the_pipeline_runs(client, gate):
    results: list = [None]
    upload = threading.Thread(target=_upload, args=(client, results, 0))
    upload.start()
    try:
        assert gate.entered.wait(_TIMEOUT)

        health: list = [None]
        probe = threading.Thread(target=lambda: health.__setitem__(0, client.get("/healthz")))
        probe.start()
        probe.join(timeout=1.0)

        assert not probe.is_alive(), "/healthz waited for the pipeline run to finish"
        assert health[0].status_code == 200
    finally:
        gate.release.set()
        upload.join(_TIMEOUT)

    assert results[0].status_code == 200
    keys = {"request_id", "transcript", "fact_checks", "warnings", "pipeline_meta", "fact_checks_v2"}
    assert set(results[0].json()) == keys


def test_a_second_upload_waits_for_the_first_run(client, gate):
    results: list = [None, None]
    first = threading.Thread(target=_upload, args=(client, results, 0))
    first.start()
    assert gate.entered.wait(_TIMEOUT)

    second = threading.Thread(target=_upload, args=(client, results, 1))
    second.start()
    second.join(timeout=0.5)
    try:
        assert second.is_alive(), "the second upload finished while the first run held the slot"
        assert gate.runs == 1
    finally:
        gate.release.set()
        first.join(_TIMEOUT)
        second.join(_TIMEOUT)

    assert [r.status_code for r in results] == [200, 200]
    assert (gate.runs, gate.max_running) == (2, 1)


@pytest.mark.parametrize(
    ("warnings", "status"),
    [(["AUDIO_NORMALIZATION_TIMEOUT"], 422), (["AUDIO_NORMALIZATION_FAILED"], 400)],
)
def test_a_failed_run_still_maps_to_the_same_error(warnings, status, tmp_path, monkeypatch):
    # The failure is raised inside the worker thread; the response must not change.
    def failed_run(self, *, job_id, **_kwargs) -> dict:
        return {"request_id": job_id, "warnings": warnings, "pipeline_meta": {"status": "failed"}}

    monkeypatch.setattr(VoiceIQOrchestrator, "run", failed_run)
    monkeypatch.setattr(process_audio, "JobIO", lambda: JobIO(base_dir=tmp_path / "jobs"))
    app = FastAPI()
    app.include_router(process_audio.router, prefix="/v1")
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    app.dependency_overrides[process_audio_rate_limit] = lambda: None

    with TestClient(app) as c:
        response = c.post("/v1/process-audio", files={"file": ("call.wav", _WAV_BYTES, "audio/wav")})

    assert response.status_code == status
    detail = response.json()["detail"]
    assert set(detail) == {"request_id", "warnings", "pipeline_meta"}
    assert detail["warnings"] == warnings
