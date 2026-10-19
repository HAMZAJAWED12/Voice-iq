"""/readyz reports whether the service can work; /healthz stays a liveness probe.

Both apps check that the database answers; the full service also checks
that ffmpeg is on PATH. A failed check gives 503 and names the check
without the error text. ffmpeg's presence is simulated; nothing runs it.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app.insights.config.settings import get_settings
from app.utils import readiness

TOKEN = "SECRET-MARKER"


@pytest.fixture(autouse=True)
def _api_keys_required(monkeypatch: pytest.MonkeyPatch):
    """Keys configured, so a public probe is shown to need none."""
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app(module: str):
    return importlib.import_module(module).app


@pytest.fixture
def ffmpeg_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness.shutil, "which", lambda name: f"/usr/bin/{name}")


def _broken_engine():
    raise RuntimeError(f"{TOKEN} cannot open /srv/db")


def test_the_insight_service_is_ready_when_the_database_answers() -> None:
    with TestClient(_app("app.insight_main")) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_the_full_service_also_needs_ffmpeg(ffmpeg_on_path) -> None:
    with TestClient(_app("app.main")) as client:
        response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "ffmpeg": "ok"}}


def test_without_ffmpeg_the_full_service_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness.shutil, "which", lambda _name: None)

    with TestClient(_app("app.main")) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "checks": {"database": "ok", "ffmpeg": "failed"}}


@pytest.mark.parametrize("module", ["app.insight_main", "app.main"])
def test_an_unreachable_database_is_not_ready_but_still_alive(
    module: str, monkeypatch: pytest.MonkeyPatch, ffmpeg_on_path
) -> None:
    with TestClient(_app(module)) as client:
        monkeypatch.setattr(readiness, "get_engine", _broken_engine)
        ready = client.get("/readyz")
        alive = client.get("/healthz")

    assert ready.status_code == 503
    assert ready.json()["checks"]["database"] == "failed"
    assert TOKEN not in ready.text
    assert alive.status_code == 200
