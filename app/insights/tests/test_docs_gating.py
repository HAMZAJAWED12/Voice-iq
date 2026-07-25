"""Coverage for production docs gating (S8 / wave S-E).

/docs, /redoc and /openapi.json expose the full request/response schema of
every route. Useful in dev, needless detail in production. The apps build
FastAPI with those URLs set to None when environment == "production".
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


def _build_insight_app(monkeypatch: pytest.MonkeyPatch, environment: str):
    """Re-import app.insight_main so the module-level app picks up the env."""
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", environment)
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")

    from app.insights.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()

    import app.insight_main as insight_main

    importlib.reload(insight_main)
    return insight_main.app


def test_docs_served_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_insight_app(monkeypatch, "development")
    try:
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 200
            assert client.get("/openapi.json").status_code == 200
    finally:
        _restore(monkeypatch)


def test_docs_hidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _build_insight_app(monkeypatch, "production")
    try:
        with TestClient(app) as client:
            assert client.get("/docs").status_code == 404
            assert client.get("/redoc").status_code == 404
            assert client.get("/openapi.json").status_code == 404
            # Ops probes stay public so healthchecks keep working.
            assert client.get("/healthz").status_code == 200
    finally:
        _restore(monkeypatch)


def _restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reload the module back to a non-production default for other tests."""
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "development")

    from app.insights.config import settings as settings_mod

    settings_mod.get_settings.cache_clear()

    import app.insight_main as insight_main

    importlib.reload(insight_main)
    settings_mod.get_settings.cache_clear()
