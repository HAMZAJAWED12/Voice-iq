"""Readiness checks for the /readyz probe.

/healthz answers while the process is up (liveness). /readyz answers 200
only when the service can do its work: the database answers a trivial
query and, on the full service, ffmpeg is on PATH. A failed check gives 503
and names the check; the error itself goes to the log, never the response.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable

from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.insights.repository.db import get_engine
from app.utils.logger import logger


def database_ready() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means not ready
        logger.warning("readiness: database check failed (%s)", type(exc).__name__)
        return False
    return True


def ffmpeg_ready() -> bool:
    if shutil.which("ffmpeg") is None:
        logger.warning("readiness: ffmpeg is not on PATH")
        return False
    return True


def readiness(checks: dict[str, Callable[[], bool]]) -> JSONResponse:
    """Run `checks`; 200 when all pass, else 503. The body names each result."""
    results = {name: "ok" if check() else "failed" for name, check in checks.items()}
    ready = all(result == "ok" for result in results.values())
    return JSONResponse(
        {"status": "ok" if ready else "unavailable", "checks": results},
        status_code=200 if ready else 503,
    )
