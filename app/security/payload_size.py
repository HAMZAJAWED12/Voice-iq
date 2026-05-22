"""Per-route payload-size enforcement for the FastAPI surface.

Provides a dependency *factory* `enforce_content_length(max_bytes)` that
returns a FastAPI dependency. Each protected router declares its own cap
in bytes, and the dependency rejects requests above that cap before the
body is read into memory.

Why a factory rather than a single dep?
    Different routes have wildly different size budgets (a JSON session
    payload is KBs, an audio upload can be MBs to hundreds of MBs).
    Embedding the cap in the dependency closure keeps the wiring at
    each router obvious and self-documenting.

Behaviour:
    * Header missing             -> 411 Length Required
    * Header malformed (non-int) -> 400 Bad Request
    * Header value >  max_bytes  -> 413 Payload Too Large
    * Header value <= max_bytes  -> pass

Note: the Content-Length check is the *first* line of defence. For
upload routes the handler should additionally enforce a byte counter
while streaming to disk, because a hostile client can lie about
Content-Length.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Request, status

# Methods that carry a request body. Other methods (GET / HEAD / OPTIONS /
# DELETE) are skipped because TestClient and most HTTP clients omit
# Content-Length on body-less requests, and they cannot exhaust memory.
_METHODS_WITH_BODY: frozenset[str] = frozenset({"POST", "PUT", "PATCH"})


def enforce_content_length(max_bytes: int) -> Callable[[Request], None]:
    """Return a FastAPI dependency that rejects oversized requests.

    Args:
        max_bytes: Hard cap on the Content-Length header value, in bytes.
            Must be a positive integer; the factory validates this once
            at wiring time so misconfiguration is loud at import.

    Returns:
        A callable taking a `fastapi.Request` and raising `HTTPException`
        on a violation. Returns `None` on success.
    """
    if max_bytes <= 0:
        raise ValueError(f"max_bytes must be a positive integer, got {max_bytes!r}.")

    def _dependency(request: Request) -> None:
        if request.method.upper() not in _METHODS_WITH_BODY:
            return
        raw = request.headers.get("content-length")
        if raw is None:
            raise HTTPException(
                status_code=status.HTTP_411_LENGTH_REQUIRED,
                detail="Content-Length header is required.",
            )
        try:
            length = int(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content-Length header is not an integer.",
            ) from exc

        if length < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Content-Length header is negative.",
            )

        if length > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"Request body of {length} bytes exceeds the limit " f"of {max_bytes} bytes for this endpoint."
                ),
            )

    return _dependency


__all__ = ["enforce_content_length"]
