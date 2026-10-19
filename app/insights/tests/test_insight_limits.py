"""Rate limits and paging on the /v1/insights routes.

Routes that store or delete (generate, generate-from-raw, delete) and routes
that read (a record, its views, the list) have separate per-caller limits,
and every route group keeps its own counter. The list returns at most 100
session_ids unless asked for more, and never more than 500.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.config.settings import InsightSettings, get_settings
from app.insights.repository import InsightRepository, get_insight_repository
from app.insights.repository.db import Base
from app.insights.repository.orm_models import InsightRecordORM
from app.security import rate_limit

_HEADERS = {"X-API-Key": "testkey"}


def _session(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "utterances": [{"id": "u1", "speaker": "S1", "start": 0.0, "end": 1.0, "text": "hello there", "word_count": 2}],
    }


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[InsightRepository]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'insights.db'}", future=True, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    try:
        yield InsightRepository(session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    finally:
        engine.dispose()


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch) -> Iterator:
    """Set rate-limit settings for one test; counters start empty."""

    def apply(**env: str) -> None:
        for name, value in env.items():
            monkeypatch.setenv(f"VOICEIQ_RATE_LIMIT_{name.upper()}", value)
        get_settings.cache_clear()

    monkeypatch.setenv("VOICEIQ_API_KEYS", "testkey")
    monkeypatch.setenv("VOICEIQ_ENVIRONMENT", "test")
    apply(enabled="true")
    rate_limit.reset()
    try:
        yield apply
    finally:
        rate_limit.reset()
        get_settings.cache_clear()


@pytest.fixture
def client(repo: InsightRepository, limits) -> Iterator[TestClient]:
    from app.insight_main import app

    app.dependency_overrides[get_insight_repository] = lambda: repo
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_insight_repository, None)


def test_writes_over_the_limit_get_429(client: TestClient, limits) -> None:
    limits(insight_write="2/minute")

    responses = [client.post("/v1/insights/generate", json=_session(f"s{i}"), headers=_HEADERS) for i in range(3)]

    assert [r.status_code for r in responses] == [200, 200, 429]
    assert "Retry-After" in responses[2].headers


def test_generate_from_raw_and_delete_count_as_writes(client: TestClient, limits) -> None:
    limits(insight_write="2/minute")

    codes = [
        client.post("/v1/insights/generate-from-raw", json=_session("s1"), headers=_HEADERS).status_code,
        client.delete("/v1/insights/s1", headers=_HEADERS).status_code,
        client.delete("/v1/insights/s1", headers=_HEADERS).status_code,
    ]

    assert codes == [200, 200, 429]


def test_reads_over_the_limit_get_429(client: TestClient, limits) -> None:
    limits(insight_read="4/minute")
    assert client.post("/v1/insights/generate", json=_session("s1"), headers=_HEADERS).status_code == 200

    paths = ["/v1/insights/s1", "/v1/insights/s1/summary", "/v1/insights/s1/speakers", "/v1/insights/s1/timeline"]
    codes = [client.get(path, headers=_HEADERS).status_code for path in [*paths, "/v1/insights/"]]

    assert codes == [200, 200, 200, 200, 429]


def test_reads_and_writes_have_separate_counters(client: TestClient, limits) -> None:
    limits(insight_write="1/minute", insight_read="1/minute")

    assert client.post("/v1/insights/generate", json=_session("s1"), headers=_HEADERS).status_code == 200
    assert client.get("/v1/insights/s1", headers=_HEADERS).status_code == 200


def test_route_groups_with_the_same_limit_do_not_share_a_counter(limits) -> None:
    groups = ["factcheck", "process_audio", "agent_brain", "insight_write", "insight_read"]
    limits(**dict.fromkeys(groups, "1/minute"))
    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"))

    for group in groups:
        getattr(rate_limit, f"{group}_rate_limit")(request)  # first call in each group passes

    with pytest.raises(HTTPException) as over:
        rate_limit.factcheck_rate_limit(request)
    assert over.value.status_code == 429


def test_the_limit_defaults() -> None:
    settings = InsightSettings(_env_file=None)

    assert (settings.rate_limit_insight_write, settings.rate_limit_insight_read) == ("60/minute", "120/minute")


def test_the_list_pages_through_every_session(client: TestClient, limits) -> None:
    limits(enabled="false")
    for i in range(5):
        assert client.post("/v1/insights/generate", json=_session(f"s{i}"), headers=_HEADERS).status_code == 200

    every = client.get("/v1/insights/", headers=_HEADERS).json()
    pages = [
        client.get("/v1/insights/", params={"limit": 2, "offset": offset}, headers=_HEADERS).json()
        for offset in (0, 2, 4)
    ]

    assert [len(page) for page in pages] == [2, 2, 1]
    assert [sid for page in pages for sid in page] == every
    assert sorted(every) == [f"s{i}" for i in range(5)]


def test_the_list_defaults_to_100_from_the_start(limits) -> None:
    from app.insight_main import app

    class Recorder:
        def list_session_ids(self, limit: int | None = None, offset: int = 0) -> list[str]:
            return [f"{limit}:{offset}"]

    app.dependency_overrides[get_insight_repository] = Recorder
    try:
        with TestClient(app) as client:
            default = client.get("/v1/insights/", headers=_HEADERS).json()
            largest = client.get("/v1/insights/", params={"limit": 500, "offset": 7}, headers=_HEADERS).json()
    finally:
        app.dependency_overrides.pop(get_insight_repository, None)

    assert (default, largest) == (["100:0"], ["500:7"])


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 501}, {"limit": "many"}, {"offset": -1}, {"offset": 2**31}],
    ids=["limit-zero", "limit-over-500", "limit-not-a-number", "offset-negative", "offset-too-large"],
)
def test_out_of_range_paging_is_refused(client: TestClient, params: dict) -> None:
    assert client.get("/v1/insights/", params=params, headers=_HEADERS).status_code == 422


def test_authentication_is_checked_before_paging(client: TestClient) -> None:
    assert client.get("/v1/insights/", params={"limit": 501}).status_code == 401


def test_sessions_updated_at_the_same_moment_page_in_session_id_order(repo: InsightRepository) -> None:
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc)
    session = repo._open()
    try:
        session.add_all(
            InsightRecordORM(session_id=sid, status="ok", payload_json="{}", created_at=moment, updated_at=moment)
            for sid in ["c", "a", "b"]
        )
        session.commit()
    finally:
        session.close()

    assert repo.list_session_ids(limit=2) + repo.list_session_ids(limit=2, offset=2) == ["a", "b", "c"]
