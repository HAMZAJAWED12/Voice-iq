"""Per-endpoint rate limiting (S-C).

Implemented as FastAPI **dependencies** over slowapi's engine (the ``limits``
library + slowapi's IP helper), rather than slowapi's ``@limiter.limit``
decorator. The decorator wraps the endpoint function, and combined with this
package's ``from __future__ import annotations`` that made FastAPI mis-resolve
Pydantic body models as query params. A dependency takes only ``request`` and
never touches the handler's signature, so it is robust and unit-testable.

Buckets are keyed on the presented ``X-API-Key`` (falling back to the client
IP for dev-mode / unauthenticated probes), so one caller cannot exhaust
another caller's budget. Limits are read from settings *per request*, so an
env override + ``get_settings.cache_clear()`` takes effect immediately (used
by the tests).

Usage - attach as a route dependency (limits differ per route):

    @router.post("", dependencies=[Depends(factcheck_rate_limit)])
    def run_fact_check(...): ...
"""

from __future__ import annotations

import math
import time

from fastapi import HTTPException, Request, status
from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from slowapi.util import get_remote_address

from app.insights.config.settings import get_settings

# In-memory counters (per process). Adequate for single-worker / dev; a
# multi-worker deployment needing shared counters would swap MemoryStorage
# for RedisStorage.
_storage = MemoryStorage()
_strategy = MovingWindowRateLimiter(_storage)


def rate_limit_key(request: Request) -> str:
    """Bucket key: the API key if presented, else the client IP."""
    presented = request.headers.get("X-API-Key")
    if presented:
        return f"key:{presented}"
    return f"ip:{get_remote_address(request)}"


def reset() -> None:
    """Clear all counters. For test isolation only."""
    _storage.reset()


def _enforce(request: Request, limit_str: str) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    item: RateLimitItem = parse(limit_str)
    key = rate_limit_key(request)
    if not _strategy.hit(item, key):
        # Over the limit. Compute Retry-After from the window reset time.
        reset_at, _remaining = _strategy.get_window_stats(item, key)
        retry_after = max(1, math.ceil(reset_at - time.time()))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Slow down and retry.",
            headers={"Retry-After": str(retry_after)},
        )


def factcheck_rate_limit(request: Request) -> None:
    """Route dependency for POST /v1/fact-check."""
    _enforce(request, get_settings().rate_limit_factcheck)


def process_audio_rate_limit(request: Request) -> None:
    """Route dependency for POST /v1/process-audio."""
    _enforce(request, get_settings().rate_limit_process_audio)


def agent_brain_rate_limit(request: Request) -> None:
    """Route dependency for the internal Agent Brain generate endpoint."""
    _enforce(request, get_settings().rate_limit_agent_brain)
