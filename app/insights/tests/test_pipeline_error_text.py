"""A failed pipeline stage does not return its exception text in the response.

The topic result is part of the /v1/process-audio response, so its failure
record carries a fixed message; the exception text stays in the job's
status file on disk.
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from app.pipeline.orchestrator import VoiceIQOrchestrator, _PipelineState
from app.utils.job_io import JobIO

TOKEN = "SECRET-MARKER"


def test_a_failed_topic_step_returns_a_fixed_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    class FailingTopicService:
        @staticmethod
        def classify(_text):
            raise RuntimeError(f"{TOKEN} model path /srv/models/topic")

    fake = types.ModuleType("app.services.topic_service")
    fake.TopicService = FailingTopicService
    monkeypatch.setitem(__import__("sys").modules, "app.services.topic_service", fake)

    io = JobIO(base_dir=tmp_path / "jobs")
    job = io.init_job("topic-fail")
    state = _PipelineState(job=job, meta={"warnings": [], "timings_ms": {}, "skipped_steps": []}, job_id="topic-fail")

    VoiceIQOrchestrator(job_io=io)._run_topic(state)

    topic = io.load_json(job, "artifacts/nlp/topic.json")
    assert topic == {"topic": "unknown", "confidence": 0.0, "error": "topic classification failed"}
    assert "TOPIC_FAILED" in state.meta["warnings"]
    status = io.load_json(job, "artifacts/nlp/topic.status.json")
    assert status["status"] == "failed" and TOKEN in status["error"]
