"""ASGI middleware that checks guarded requests before their body is used.

FastAPI reads and parses a request body before it resolves route
dependencies, so `verify_api_key` and `enforce_content_length` alone run
only after the whole body has arrived. For POST, PUT and PATCH requests to
the guarded path prefixes, this middleware runs the same two checks first —
API key, then declared Content-Length, in the same order as the router
dependencies — and answers with the same status and `{"detail": ...}` body
without passing the body to the app. The refusal closes the connection, so
the server stops reading. Requests that pass have their body bytes counted,
and a body longer than its declared length is refused with 413.

Because the checks run before routing, a body-method request to an unknown
or wrong-method path under a guarded prefix gets the auth or size error
rather than 404 or 405.

The router dependencies stay in place: they keep the OpenAPI security
scheme, cover GET and DELETE, and still apply where the middleware is not
installed. Settings come from the app's `get_settings` override when one is
set, the same instance the dependencies see, and an override of
`verify_api_key` turns the key check off here too.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import HTTPException, status
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.insights.config.settings import InsightSettings, get_settings
from app.security.api_key import API_KEY_HEADER, api_key_error, verify_api_key
from app.security.payload_size import METHODS_WITH_BODY, content_length_error


def guarded_limits(settings: InsightSettings, upload_max_bytes: int | None = None) -> list[tuple[str, int]]:
    """Path prefixes and their body caps in bytes, from the same settings the routers use."""
    session_cap = settings.api_max_session_payload_kb * 1024
    limits = [
        ("/v1/insights", session_cap),
        ("/v1/fact-check", session_cap),
        ("/internal/v1/agent-brain", session_cap),
    ]
    if upload_max_bytes is not None:
        limits.append(("/v1/process-audio", upload_max_bytes))
    return limits


class RequestGuardMiddleware:
    """Check the API key and the declared size before a guarded body is used."""

    def __init__(self, app: ASGIApp, limits: Sequence[tuple[str, int]]) -> None:
        self.app = app
        self.limits = tuple(limits)

    def cap_for(self, path: str) -> int | None:
        """The body cap for a route path, or None when the path is not guarded."""
        for prefix, cap in self.limits:
            if path == prefix or path.startswith(prefix + "/"):
                return cap
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"].upper() not in METHODS_WITH_BODY:
            await self.app(scope, receive, send)
            return
        cap = self.cap_for(_route_path(scope))
        if cap is None:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        raw_length = headers.get("content-length")
        error = _key_error(scope, headers) or content_length_error(scope["method"], raw_length, cap)
        if error is not None:
            refusal_headers = {**(error.headers or {}), "Connection": "close"}
            response = JSONResponse({"detail": error.detail}, status_code=error.status_code, headers=refusal_headers)
            await response(scope, receive, send)
            return

        assert raw_length is not None  # content_length_error refuses a missing header
        declared = int(raw_length)
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > declared:
                    # Starlette closes a multipart temp file only on its own
                    # parse errors; this one is released when the request ends.
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Request body is longer than its Content-Length header.",
                    )
            return message

        await self.app(scope, counting_receive, send)


def _route_path(scope: Scope) -> str:
    """The path the router matches: `scope["path"]` without the root path."""
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path) and path[len(root_path) : len(root_path) + 1] in ("", "/"):
        return path[len(root_path) :] or "/"
    return path


def _key_error(scope: Scope, headers: Headers) -> HTTPException | None:
    overrides = getattr(scope.get("app"), "dependency_overrides", {})
    if verify_api_key in overrides:
        return None
    settings: InsightSettings = overrides.get(get_settings, get_settings)()
    return api_key_error(headers.get(API_KEY_HEADER), settings)


__all__ = ["RequestGuardMiddleware", "guarded_limits"]
