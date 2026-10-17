"""Stored insight and fact-check records are deleted after VOICEIQ_DB_RETENTION_DAYS.

The sweep that already purged job directories now purges records too: once
at startup, then hourly in the background. 0 keeps every record. Databases
are per-test SQLite files; nothing leaves the host.
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.config.settings import InsightSettings
from app.insights.repository import FactCheckRepository, InsightRepository
from app.insights.repository.db import Base
from app.insights.repository.factcheck_orm_models import FactCheckResultORM
from app.insights.repository.orm_models import InsightRecordORM
from app.utils import retention

_NOW = datetime.now(timezone.utc)


@pytest.fixture
def repos(tmp_path: Path) -> Iterator[tuple[InsightRepository, FactCheckRepository]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield InsightRepository(session_factory=factory), FactCheckRepository(session_factory=factory)
    finally:
        engine.dispose()


def _seed(repo: InsightRepository, *, insights: dict[str, int], factchecks: dict[str, int]) -> None:
    """Insert rows aged the given number of days."""
    session = repo._open()
    try:
        for sid, age in insights.items():
            at = _NOW - timedelta(days=age)
            session.add(InsightRecordORM(session_id=sid, status="ok", payload_json="{}", created_at=at, updated_at=at))
        for conversation, age in factchecks.items():
            at = _NOW - timedelta(days=age)
            session.add(
                FactCheckResultORM(
                    conversation_id=conversation,
                    speaker_id="s",
                    claim_text="synthetic",
                    claim_type="STATIC_FACT",
                    verdict="TRUE",
                    confidence_label="HIGH",
                    reason="synthetic",
                    created_at=at,
                    updated_at=at,
                )
            )
        session.commit()
    finally:
        session.close()


def _conversations(factchecks: FactCheckRepository) -> list[str]:
    session = factchecks._open()
    try:
        return sorted(row.conversation_id for row in session.query(FactCheckResultORM).all())
    finally:
        session.close()


def test_records_older_than_the_limit_are_deleted(repos) -> None:
    insights, factchecks = repos
    _seed(insights, insights={"old": 31, "recent": 29}, factchecks={"old-c": 40, "recent-c": 1})

    removed = retention.purge_expired_records(30, insights, factchecks)

    assert removed == 2
    assert insights.list_session_ids() == ["recent"]
    assert _conversations(factchecks) == ["recent-c"]


def test_zero_days_keeps_every_record(repos) -> None:
    insights, factchecks = repos
    _seed(insights, insights={"old": 400}, factchecks={"old-c": 400})

    assert retention.purge_expired_records(0, insights, factchecks) == 0
    assert insights.list_session_ids() == ["old"]
    assert _conversations(factchecks) == ["old-c"]


def test_the_retention_defaults_to_30_days() -> None:
    assert InsightSettings(_env_file=None).db_retention_days == 30


class _Recorder:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list = []

    def purge_older_than(self, cutoff: datetime) -> int:
        self.calls.append(cutoff)
        if self.fail:
            raise RuntimeError("database is gone")
        return 0

    def purge_expired(self, max_age_hours: float) -> int:
        self.calls.append(max_age_hours)
        return 0


def test_the_sweep_uses_both_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs, insights, factchecks = _Recorder(), _Recorder(), _Recorder()
    settings = InsightSettings(_env_file=None, job_retention_hours=5, db_retention_days=7)
    monkeypatch.setattr(retention, "get_settings", lambda: settings)
    monkeypatch.setattr(retention, "JobIO", lambda: jobs)
    monkeypatch.setattr(retention, "insight_repository", insights)
    monkeypatch.setattr(retention, "factcheck_repository", factchecks)

    retention.sweep()

    assert jobs.calls == [5]
    (cutoff,) = insights.calls
    assert abs(cutoff - (datetime.now(timezone.utc) - timedelta(days=7))) < timedelta(minutes=1)
    assert factchecks.calls == [cutoff]


def test_a_failing_record_purge_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = _Recorder()
    monkeypatch.setattr(retention, "get_settings", lambda: InsightSettings(_env_file=None))
    monkeypatch.setattr(retention, "JobIO", lambda: jobs)
    monkeypatch.setattr(retention, "insight_repository", _Recorder(fail=True))
    monkeypatch.setattr(retention, "factcheck_repository", _Recorder())

    retention.sweep()  # logged, not raised

    assert jobs.calls == [24.0]


def test_the_sweep_runs_at_start_then_repeatedly_until_the_block_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(retention, "sweep", lambda: calls.append(1))

    async def scenario() -> tuple[int, int, int]:
        async with retention.run_retention(interval_sec=0.01):
            at_start = len(calls)
            await asyncio.sleep(0.1)
            inside = len(calls)
        after = len(calls)
        await asyncio.sleep(0.05)
        return at_start, inside, after

    at_start, inside, after = asyncio.run(scenario())

    assert at_start == 1
    assert inside >= 2
    assert len(calls) == after


@pytest.mark.parametrize("module", ["app.insight_main", "app.main"])
def test_each_app_sweeps_at_startup(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(retention, "sweep", lambda: calls.append(1))
    app = importlib.import_module(module).app

    with TestClient(app):
        pass

    assert calls == [1]
