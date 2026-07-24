"""Coverage for per-endpoint rate limiting (S-C).

Two levels: the bucket-key function in isolation, and a real 429 through the
lightweight insight app's fact-check route (which is importable without the
heavy ML stack).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.insights.config.settings import get_settings
from app.security import rate_limit
from app.security.rate_limit import rate_limit_key

_HEADERS = {"X-API-Key": "testkey"}
_BODY = {"conversation_id": "c1", "speaker_id": "s1", "transcript_text": "Thanks, talk soon."}


def _req(headers: dict[str, str] | None = None, host: str = "1.2.3.4") -> SimpleNamespace:
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def test_key_prefers_api_key() -> None:
    assert rate_limit_key(_req({"X-API-Key": "abc123"})) == "key:abc123"


def test_key_falls_back_to_client_ip() -> None:
    assert rate_limit_key(_req({}, host="9.9.9.9")) == "ip:9.9.9.9"


def test_factcheck_route_returns_429_over_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Tiny limit + a known key so auth passes deterministically regardless of
    # any local .env. Claim-free transcript => no external network calls.
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_FACTCHECK", "2/minute")
    get_settings.cache_clear()
    rate_limit.reset()  # clear counter state from earlier tests

    try:
        from app.insight_main import app

        with TestClient(app) as client:
            r1 = client.post("/v1/fact-check", json=_BODY, headers=_HEADERS)
            r2 = client.post("/v1/fact-check", json=_BODY, headers=_HEADERS)
            r3 = client.post("/v1/fact-check", json=_BODY, headers=_HEADERS)

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429  # 3rd call in the window is throttled
        assert "Retry-After" in r3.headers
    finally:
        rate_limit.reset()
        get_settings.cache_clear()


def test_disabled_flag_lets_all_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_ENABLED", "false")  # master switch off
    monkeypatch.setenv("VOICEIQ_RATE_LIMIT_FACTCHECK", "2/minute")
    get_settings.cache_clear()
    rate_limit.reset()

    try:
        from app.insight_main import app

        with TestClient(app) as client:
            codes = [client.post("/v1/fact-check", json=_BODY, headers=_HEADERS).status_code for _ in range(4)]

        assert codes == [200, 200, 200, 200]  # nothing throttled
    finally:
        rate_limit.reset()
        get_settings.cache_clear()
