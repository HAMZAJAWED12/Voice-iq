"""Requests to guarded routes are checked before their body is read.

The API key and the declared Content-Length are checked first, so a request
that will be refused is refused without reading its body; the bytes that do
arrive are counted against the declared length. The apps are driven over
raw ASGI so the test can see how many body bytes were read.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi import FastAPI

from app.insights.config.settings import InsightSettings, get_settings
from app.security import rate_limit

KEY = "guard-test-key"
AGENT_PATH = "/internal/v1/agent-brain/recommendations/generate"
AGENT_BODY = json.dumps(
    {
        "sessionId": "s-guard",
        "transcript": [{"segmentId": "seg-1", "speakerId": "spk-1", "text": "Ali will send the report by Friday."}],
    }
).encode()
CHUNK = 64 * 1024
BIG = 8 * 1024 * 1024  # over both the JSON cap (2 MB) and small test caps


def _settings(**overrides) -> InsightSettings:
    values = {"environment": "test", "api_keys": [KEY]}
    values.update(overrides)
    return InsightSettings(_env_file=None, **values)


@pytest.fixture
def insight_app() -> Iterator[FastAPI]:
    from app.insight_main import app

    app.dependency_overrides[get_settings] = lambda: _settings()
    rate_limit.reset()
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def full_app() -> Iterator[FastAPI]:
    from app.main import app

    app.dependency_overrides[get_settings] = lambda: _settings()
    rate_limit.reset()
    yield app
    app.dependency_overrides.clear()


def _drive(
    app: FastAPI,
    path: str,
    headers: dict[str, str] | list[tuple[bytes, bytes]],
    body: bytes,
    method: str = "POST",
    root_path: str = "",
):
    """Send `body` in chunks over raw ASGI; return (status, body bytes read, response body, headers)."""
    chunks = [body[i : i + CHUNK] for i in range(0, len(body), CHUNK)] or [b""]
    state = {"next": 0, "read": 0}
    sent: list[dict] = []

    async def receive() -> dict:
        if state["next"] < len(chunks):
            chunk = chunks[state["next"]]
            state["next"] += 1
            state["read"] += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": state["next"] < len(chunks)}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    if isinstance(headers, dict):
        raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    else:
        raw_headers = headers
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": root_path + path,
        "raw_path": (root_path + path).encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    response_headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in start["headers"]}
    return start["status"], state["read"], payload, response_headers


def _json_headers(length: int | None, key: str | None = None) -> dict[str, str]:
    headers = {"content-type": "application/json"}
    if length is not None:
        headers["content-length"] = str(length)
    if key is not None:
        headers["x-api-key"] = key
    return headers


def _multipart(size: int) -> tuple[bytes, str]:
    boundary = "guardboundary"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + b"\0" * size + tail, f"multipart/form-data; boundary={boundary}"


# --------------------------------------------------------------------------- #
# Refused before the body is read                                             #
# --------------------------------------------------------------------------- #


def test_missing_key_is_refused_without_reading_the_body(insight_app):
    status, read, payload, headers = _drive(insight_app, "/v1/insights/generate", _json_headers(BIG), b"x" * BIG)

    assert (status, read) == (401, 0)
    assert json.loads(payload) == {"detail": "Missing X-API-Key header."}
    assert headers["www-authenticate"] == "X-API-Key"
    assert headers["connection"] == "close"


def test_wrong_key_is_refused_without_reading_the_body(insight_app):
    status, read, payload, _ = _drive(insight_app, "/v1/fact-check", _json_headers(BIG, "wrong"), b"x" * BIG)

    assert (status, read) == (403, 0)
    assert json.loads(payload) == {"detail": "Invalid API key."}


def test_missing_content_length_is_refused_without_reading_the_body(insight_app):
    status, read, _, _ = _drive(insight_app, AGENT_PATH, _json_headers(None, KEY), b"x" * BIG)

    assert (status, read) == (411, 0)


def test_declared_length_over_the_cap_is_refused_without_reading_the_body(insight_app):
    status, read, _, _ = _drive(insight_app, "/v1/insights/generate", _json_headers(BIG, KEY), b"x" * BIG)

    assert (status, read) == (413, 0)


def test_unauthenticated_upload_is_refused_without_reading_the_file(full_app):
    body, content_type = _multipart(BIG)
    headers = {"content-type": content_type, "content-length": str(len(body))}

    status, read, _, _ = _drive(full_app, "/v1/process-audio", headers, body)

    assert (status, read) == (401, 0)


def test_unconfigured_production_is_refused_without_reading_the_body(insight_app):
    insight_app.dependency_overrides[get_settings] = lambda: _settings(environment="production", api_keys=[])

    status, read, _, _ = _drive(insight_app, AGENT_PATH, _json_headers(BIG, KEY), b"x" * BIG)

    assert (status, read) == (503, 0)


def test_size_is_still_checked_when_auth_is_disabled(insight_app):
    insight_app.dependency_overrides[get_settings] = lambda: _settings(environment="development", api_keys=[])

    status, read, _, _ = _drive(insight_app, "/v1/insights/generate", _json_headers(BIG), b"x" * BIG)

    assert (status, read) == (413, 0)


# --------------------------------------------------------------------------- #
# Bytes received are counted                                                  #
# --------------------------------------------------------------------------- #


def test_a_body_longer_than_its_declared_length_is_refused(insight_app):
    padded = AGENT_BODY[:-1] + b", " + b'"pad": "' + b"x" * 5000 + b'"}'

    status, read, _, _ = _drive(insight_app, AGENT_PATH, _json_headers(100, KEY), padded)

    assert status == 413
    assert read <= 100 + CHUNK


# --------------------------------------------------------------------------- #
# Everything else behaves as before                                           #
# --------------------------------------------------------------------------- #


def test_a_valid_request_passes(insight_app):
    status, read, payload, _ = _drive(insight_app, AGENT_PATH, _json_headers(len(AGENT_BODY), KEY), AGENT_BODY)

    assert (status, read) == (200, len(AGENT_BODY))
    assert "recommendations" in json.loads(payload)


def test_unguarded_routes_and_bodyless_methods_pass_through(insight_app):
    assert _drive(insight_app, "/healthz", {}, b"", method="GET")[0] == 200
    # GET on a guarded router is still checked by the router's own dependency.
    assert _drive(insight_app, "/v1/insights/", {}, b"", method="GET")[0] == 401


def test_malformed_unauthenticated_body_gets_the_auth_error(insight_app):
    status, read, _, _ = _drive(insight_app, "/v1/insights/generate", _json_headers(9), b"{not json")

    assert (status, read) == (401, 0)


@pytest.mark.parametrize("module", ["app.insight_main", "app.main"])
def test_both_apps_install_the_guard(module):
    import importlib

    from app.security.request_guard import RequestGuardMiddleware

    app = importlib.import_module(module).app
    guards = [m for m in app.user_middleware if m.cls is RequestGuardMiddleware]

    assert len(guards) == 1
    prefixes = {prefix for prefix, _cap in guards[0].kwargs["limits"]}
    assert {"/v1/insights", "/v1/fact-check", "/internal/v1/agent-brain"} <= prefixes
    assert ("/v1/process-audio" in prefixes) == (module == "app.main")


def test_the_guard_applies_under_a_root_path(insight_app):
    status, read, _, _ = _drive(insight_app, "/v1/insights/generate", _json_headers(9), b"{not json", root_path="/api")

    assert (status, read) == (401, 0)


def test_a_key_with_non_ascii_characters_is_refused_not_an_error(insight_app):
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", b"9"),
        (b"x-api-key", bytes([0xE9, 0x74, 0xE9])),
    ]

    status, read, _, _ = _drive(insight_app, AGENT_PATH, headers, b"{not json")

    assert (status, read) == (403, 0)


def test_an_override_of_verify_api_key_also_turns_the_guard_check_off(insight_app):
    from app.security.api_key import verify_api_key

    insight_app.dependency_overrides[verify_api_key] = lambda: "overridden"

    status, _, _, _ = _drive(insight_app, AGENT_PATH, _json_headers(len(AGENT_BODY)), AGENT_BODY)

    assert status == 200


@pytest.mark.parametrize("module", ["app.insight_main", "app.main"])
def test_every_authenticated_body_route_is_guarded(module):
    import importlib

    from fastapi.routing import APIRoute

    from app.security.api_key import verify_api_key
    from app.security.request_guard import RequestGuardMiddleware

    app = importlib.import_module(module).app
    (entry,) = (m for m in app.user_middleware if m.cls is RequestGuardMiddleware)
    guard = RequestGuardMiddleware(app=app, **entry.kwargs)

    def needs_key(dependant) -> bool:
        return any(d.call is verify_api_key or needs_key(d) for d in dependant.dependencies)

    body_routes = [
        r
        for r in app.routes
        if isinstance(r, APIRoute) and r.methods & {"POST", "PUT", "PATCH"} and needs_key(r.dependant)
    ]
    assert body_routes
    assert [r.path for r in body_routes if guard.cap_for(r.path) is None] == []
