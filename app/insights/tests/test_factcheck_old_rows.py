"""Stored fact-check rows with non-finite numbers read back as null.

Rows saved before the 15-digit cap (M4) may hold infinity or NaN in their
numeric columns or in their evidence. The repository returns those values
as None, so every reader gets strict JSON — not only the HTTP route, whose
serializer happens to do the same.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.api.factcheck_routes import get_factcheck_repository
from app.insights.api.factcheck_routes import router as factcheck_router
from app.insights.repository.factcheck_orm_models import FactCheckResultORM
from app.insights.repository.factcheck_repository import FactCheckRepository
from app.insights.repository.orm_models import Base
from app.security.api_key import verify_api_key


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[FactCheckRepository]:
    engine = create_engine(f"sqlite:///{tmp_path / 'fc.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add(
            FactCheckResultORM(
                conversation_id="old",
                speaker_id="s",
                claim_text="BTC is at a very long number",
                claim_type="CRYPTO_PRICE",
                claimed_value=float("inf"),
                actual_value=float("nan"),
                diff_pct=float("inf"),
                verdict="FALSE",
                confidence_label="HIGH",
                confidence_score=0.9,
                source="coingecko",
                evidence_json='{"value": Infinity, "raw": {"usd": NaN, "ok": 1.5}}',
                reason="r",
            )
        )
        session.commit()
    yield FactCheckRepository(session_factory=factory)
    engine.dispose()


def test_the_repository_returns_non_finite_numbers_as_none(repository):
    (row,) = repository.list_for_conversation("old")

    assert (row["claimed_value"], row["actual_value"], row["diff_pct"]) == (None, None, None)
    assert row["evidence"] == {"value": None, "raw": {"usd": None, "ok": 1.5}}
    assert row["confidence_score"] == 0.9
    json.dumps(row, allow_nan=False)


def test_the_route_reads_the_row_back_as_strict_json(repository):
    app = FastAPI()
    app.include_router(factcheck_router, prefix="/v1")
    app.dependency_overrides[get_factcheck_repository] = lambda: repository
    app.dependency_overrides[verify_api_key] = lambda: "test-key"

    with TestClient(app) as client:
        response = client.get("/v1/fact-check/old")

    assert response.status_code == 200
    (row,) = json.loads(response.text, parse_constant=lambda token: pytest.fail(f"non-strict JSON: {token}"))
    assert row["claimed_value"] is None
