"""Coverage for the Java callback client (HMAC + disabled-by-default)."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from app.agent_brain.config.settings import AgentBrainSettings
from app.agent_brain.integrations.java_callback_client import JavaCallbackClient
from app.agent_brain.models.recommendation import CallbackPayload

_SECRET = "s3cr3t-shared-key"


def _payload() -> CallbackPayload:
    return CallbackPayload(session_id="sess-1", generated_at="2026-05-30T12:00:00Z", recommendations=[])


def test_callback_signs_and_sends() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    settings = AgentBrainSettings(callback_url="https://java.internal/cb", callback_secret=_SECRET, _env_file=None)
    client = JavaCallbackClient(settings, transport=httpx.MockTransport(handler))

    assert client.send(_payload(), trace_id="trace-123") is True

    headers = captured["headers"]
    body = captured["body"]
    assert headers["X-VoiceIQ-Service"] == "python-agent-brain"
    assert headers["X-VoiceIQ-Trace-Id"] == "trace-123"

    expected = hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-VoiceIQ-Signature"] == expected
    assert expected == expected.lower()  # lowercase hex

    data = json.loads(body)
    assert {"sessionId", "schemaVersion", "generatedAt", "recommendations"} <= set(data)
    assert data["sessionId"] == "sess-1"
    assert data["schemaVersion"] == "1.0"


def test_callback_sends_v2_signature_and_timestamp() -> None:
    """S7: v2 binds the signature to a timestamp so Java can reject replays."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200, json={"ok": True})

    settings = AgentBrainSettings(callback_url="https://java.internal/cb", callback_secret=_SECRET, _env_file=None)
    client = JavaCallbackClient(settings, transport=httpx.MockTransport(handler))
    assert client.send(_payload(), trace_id="t") is True

    headers = captured["headers"]
    body = captured["body"]

    timestamp = headers["X-VoiceIQ-Timestamp"]
    assert timestamp.isdigit()

    expected_v2 = hmac.new(_SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert headers["X-VoiceIQ-Signature-V2"] == expected_v2
    # v1 stays body-only so existing Java validation keeps working.
    assert headers["X-VoiceIQ-Signature"] == hmac.new(_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-VoiceIQ-Signature-V2"] != headers["X-VoiceIQ-Signature"]


def test_callback_refuses_plaintext_http_to_remote_host() -> None:
    """S7: never ship the payload + shared-secret auth over plaintext."""
    settings = AgentBrainSettings(callback_url="http://java.example.com/cb", callback_secret=_SECRET, _env_file=None)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("request must not be sent over plaintext http")

    client = JavaCallbackClient(settings, transport=httpx.MockTransport(handler))
    assert client.send(_payload(), trace_id="t") is False


def test_callback_allows_http_to_localhost_for_dev() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    settings = AgentBrainSettings(callback_url="http://localhost:8080/cb", callback_secret=_SECRET, _env_file=None)
    client = JavaCallbackClient(settings, transport=httpx.MockTransport(handler))
    assert client.send(_payload(), trace_id="t") is True


def test_callback_disabled_when_url_empty() -> None:
    settings = AgentBrainSettings(callback_url="", callback_secret=_SECRET, _env_file=None)
    client = JavaCallbackClient(settings)
    assert client.send(_payload(), trace_id="t") is False


def test_callback_disabled_when_secret_empty() -> None:
    settings = AgentBrainSettings(callback_url="https://x/cb", callback_secret="", _env_file=None)
    assert JavaCallbackClient(settings).send(_payload(), trace_id="t") is False


def test_callback_raises_on_http_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    settings = AgentBrainSettings(callback_url="https://x/cb", callback_secret=_SECRET, _env_file=None)
    client = JavaCallbackClient(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        client.send(_payload(), trace_id="t")


def test_settings_callback_enabled_flag() -> None:
    assert AgentBrainSettings(callback_url="u", callback_secret="s", _env_file=None).callback_enabled is True
    assert AgentBrainSettings(callback_url="", callback_secret="", _env_file=None).callback_enabled is False


def test_get_agent_settings_is_cached() -> None:
    from app.agent_brain.config.settings import get_agent_settings

    assert get_agent_settings() is get_agent_settings()
