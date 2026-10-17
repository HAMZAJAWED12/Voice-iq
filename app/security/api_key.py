"""API-key authentication dependency for the FastAPI surface.

Validates the `X-API-Key` request header against the list of keys
configured in `InsightSettings.api_keys` (sourced from the
`VOICEIQ_API_KEYS` environment variable, comma-separated).

Behaviour matrix:

| Keys configured | Environment    | Result                                  |
| --------------- | -------------- | --------------------------------------- |
| 1 or more       | any            | enforce; 401 missing, 403 invalid       |
| empty           | development,   | warn + allow (developer convenience)    |
|                 | test           |                                         |
| empty           | staging,       | fail closed with 503 (loud misconfig)   |
|                 | production     |                                         |

Health probes (`/healthz`, `/version`) live on the FastAPI app itself
and not on the routers protected by this dependency, so they remain
public and uvicorn / docker healthchecks keep working.

Key comparison uses `hmac.compare_digest` to avoid leaking key
length / prefix via timing.
"""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.insights.config.settings import InsightSettings, get_settings
from app.utils.logger import logger

# Environments that refuse every protected request when no key is configured.
_FAIL_CLOSED_ENVIRONMENTS: frozenset[str] = frozenset({"production", "staging"})

# Public so tests and middleware can reference the exact header name.
API_KEY_HEADER = "X-API-Key"

# `auto_error=False` so missing headers do not raise the FastAPI default
# 403 — we return a custom 401 with a `WWW-Authenticate` hint instead.
_api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def _matches_any(candidate: str, configured: list[str]) -> bool:
    """Constant-time check of the presented key against every configured key.

    Iterates the full list every time to avoid short-circuiting on a
    matching prefix; `compare_digest` itself is constant-time only when
    both operands have the same length, so we do not rely on that alone.
    Compares UTF-8 bytes: `compare_digest` accepts ASCII-only `str`, and a
    header may carry any character.
    """
    presented = candidate.encode("utf-8")
    matched = False
    for key in configured:
        if hmac.compare_digest(presented, key.encode("utf-8")):
            matched = True
    return matched


def api_key_error(presented: str | None, settings: InsightSettings) -> HTTPException | None:
    """The error a request with header value `presented` gets, or None if it passes.

    Shared by `verify_api_key` and the request guard middleware, which runs
    the same check before a request body is read.
    """
    configured = settings.api_keys
    if not configured:
        if settings.environment in _FAIL_CLOSED_ENVIRONMENTS:
            logger.critical(
                "API auth misconfigured: no api_keys configured in %s. "
                "Refusing every request until VOICEIQ_API_KEYS is set.",
                settings.environment,
            )
            return HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Authentication is not configured on this service.",
            )
        return None

    if not presented:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {API_KEY_HEADER} header.",
            headers={"WWW-Authenticate": API_KEY_HEADER},
        )

    if not _matches_any(presented, configured):
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )

    return None


def verify_api_key(
    presented: str | None = Security(_api_key_header),
    settings: InsightSettings = Depends(get_settings),
) -> str:
    """FastAPI dependency: validate the X-API-Key header.

    Returns the matched key on success (or an empty string in dev mode
    where auth is intentionally disabled). Returning the key gives
    downstream dependencies a hook for future per-key audit logging.
    """
    error = api_key_error(presented, settings)
    if error is not None:
        raise error

    if not settings.api_keys:
        # Dev / staging / test: log and allow through so local development
        # against /docs and TestClient remains friction-free.
        logger.warning(
            "API auth disabled: no VOICEIQ_API_KEYS configured (environment=%s).",
            settings.environment,
        )
        return ""

    return presented or ""


__all__ = ["API_KEY_HEADER", "api_key_error", "verify_api_key"]
