"""Tests for the per-route payload-size dependency.

Mixes two styles:

* Unit tests against the dependency function directly with a lightweight
  fake `Request`. These cover the branches that are awkward to trigger
  via `TestClient` (missing / malformed / negative Content-Length).
* One integration test through `TestClient` to confirm the dep is wired
  into a router correctly and that real HTTP traffic gets the expected
  413.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.security.payload_size import enforce_content_length


def _fake_request(method: str, content_length: str | None) -> SimpleNamespace:
    """Build a stand-in for `fastapi.Request` that exposes only what the
    dependency reads: `.method` and `.headers.get("content-length")`.
    """
    headers: dict[str, str] = {}
    if content_length is not None:
        headers["content-length"] = content_length
    return SimpleNamespace(method=method, headers=headers)


# --------------------------------------------------------------------------- #
# Factory validation                                                          #
# --------------------------------------------------------------------------- #


def test_factory_rejects_zero_max_bytes() -> None:
    with pytest.raises(ValueError):
        enforce_content_length(0)


def test_factory_rejects_negative_max_bytes() -> None:
    with pytest.raises(ValueError):
        enforce_content_length(-1)


# --------------------------------------------------------------------------- #
# Method gating                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "DELETE"])
def test_body_less_methods_skip_enforcement(method: str) -> None:
    """The dep must not 411 a GET just because TestClient omits CL."""
    dep = enforce_content_length(max_bytes=10)
    # Even with a CL that would otherwise blow the cap, body-less methods
    # are skipped entirely.
    dep(_fake_request(method, "999999"))
    dep(_fake_request(method, None))


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH"])
def test_body_methods_enforce_within_cap(method: str) -> None:
    dep = enforce_content_length(max_bytes=1000)
    # 999 bytes is under the 1000-byte cap; should pass silently.
    dep(_fake_request(method, "999"))


# --------------------------------------------------------------------------- #
# Header validation                                                           #
# --------------------------------------------------------------------------- #


def test_missing_content_length_on_post_returns_411() -> None:
    dep = enforce_content_length(max_bytes=1000)
    with pytest.raises(HTTPException) as exc:
        dep(_fake_request("POST", None))
    assert exc.value.status_code == 411


def test_malformed_content_length_returns_400() -> None:
    dep = enforce_content_length(max_bytes=1000)
    with pytest.raises(HTTPException) as exc:
        dep(_fake_request("POST", "not-a-number"))
    assert exc.value.status_code == 400


def test_negative_content_length_returns_400() -> None:
    dep = enforce_content_length(max_bytes=1000)
    with pytest.raises(HTTPException) as exc:
        dep(_fake_request("POST", "-1"))
    assert exc.value.status_code == 400


def test_zero_content_length_passes() -> None:
    """An empty body is legitimate (think: a no-op POST)."""
    dep = enforce_content_length(max_bytes=1000)
    dep(_fake_request("POST", "0"))


def test_oversized_content_length_returns_413() -> None:
    dep = enforce_content_length(max_bytes=1000)
    with pytest.raises(HTTPException) as exc:
        dep(_fake_request("POST", "1001"))
    assert exc.value.status_code == 413
    # The detail should mention the limit and the offending size so
    # callers can understand exactly which cap they hit.
    assert "1000" in exc.value.detail
    assert "1001" in exc.value.detail


# --------------------------------------------------------------------------- #
# Integration through a real FastAPI app                                      #
# --------------------------------------------------------------------------- #


def test_integration_post_over_cap_returns_413() -> None:
    """End-to-end: a real HTTP client gets 413 when the body exceeds cap."""
    cap_bytes = 64
    dep = enforce_content_length(cap_bytes)

    router = APIRouter(dependencies=[Depends(dep)])

    @router.post("/echo")
    async def _echo(payload: dict) -> dict:  # pragma: no cover - never reached
        return payload

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        # A payload comfortably over 64 bytes once JSON-encoded.
        big_payload = {"key": "x" * 200}
        response = client.post("/echo", json=big_payload)

    assert response.status_code == 413
    assert "exceeds the limit" in response.json()["detail"]


def test_integration_get_is_not_blocked_by_size_dep() -> None:
    """Sanity: GET routes on the same router must not 411."""
    dep = enforce_content_length(max_bytes=10)

    router = APIRouter(dependencies=[Depends(dep)])

    @router.get("/ping")
    async def _ping() -> dict:
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
