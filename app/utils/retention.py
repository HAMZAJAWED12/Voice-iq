"""Retention sweep: expired job directories and stored records.

`sweep()` deletes job directories older than `job_retention_hours` and
insight and fact-check records older than `db_retention_days`. Each app's
lifespan runs it through `run_retention()`: once at startup, then hourly in
the background, so a server that rarely restarts still purges.

Fail-soft: a sweep that fails is logged and never raised.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone

import anyio

from app.insights.config.settings import get_settings
from app.insights.repository import FactCheckRepository, InsightRepository, factcheck_repository, insight_repository
from app.utils.job_io import JobIO
from app.utils.logger import logger

SWEEP_INTERVAL_SEC = 3600.0


def purge_expired_records(days: int, insights: InsightRepository, factchecks: FactCheckRepository) -> int:
    """Delete records older than `days` days; returns how many. `days <= 0` keeps all."""
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    removed = insights.purge_older_than(cutoff) + factchecks.purge_older_than(cutoff)
    if removed:
        logger.info("retention: removed %d expired record(s)", removed)
    return removed


def sweep() -> None:
    """Purge expired job directories and stored records. Never raises."""
    settings = get_settings()
    try:
        JobIO().purge_expired(settings.job_retention_hours)
    except Exception:  # retention must never break its caller
        logger.exception("retention: job purge failed")
    try:
        purge_expired_records(settings.db_retention_days, insight_repository, factcheck_repository)
    except Exception:  # retention must never break its caller
        logger.exception("retention: record purge failed")


async def _sweep_periodically(interval_sec: float) -> None:
    while True:
        await asyncio.sleep(interval_sec)
        await anyio.to_thread.run_sync(sweep)


@asynccontextmanager
async def run_retention(interval_sec: float = SWEEP_INTERVAL_SEC) -> AsyncIterator[None]:
    """Sweep now, then every `interval_sec` until the block exits."""
    await anyio.to_thread.run_sync(sweep)
    task = asyncio.create_task(_sweep_periodically(interval_sec))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
