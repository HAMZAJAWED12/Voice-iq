"""Tests for FactCheckRepository against an isolated in-memory SQLite."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.models.factcheck_models import (
    ClaimSpan,
    Confidence,
    DetectedClaim,
    Evidence,
    FactCheckResponse,
    FactCheckResult,
    FactCheckStats,
)
from app.insights.repository.db import Base
from app.insights.repository.factcheck_orm_models import FactCheckResultORM  # noqa: F401
from app.insights.repository.factcheck_repository import (
    FactCheckRepository,
    build_factcheck_repository_from_factory,
)

# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def repo() -> Iterator[FactCheckRepository]:
    """Fresh in-memory SQLite + repository, isolated per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        future=True,
    )
    # Ensure ORM modules register their tables on Base.metadata.
    from app.insights.repository import (  # noqa: F401
        factcheck_orm_models,
        orm_models,
    )

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield build_factcheck_repository_from_factory(factory)
    engine.dispose()


def _make_result(
    *,
    claim_text: str = "Bitcoin is at 95000 dollars",
    claim_type: str = "CRYPTO_PRICE",
    raw_value: float = 95000.0,
    raw_value_text: str | None = None,
    actual_value: float | None = 91234.5,
    actual_text: str | None = None,
    verdict: str = "PARTIALLY_TRUE",
    diff_pct: float | None = 4.13,
    confidence_label: str = "MEDIUM",
    confidence_score: float = 0.6,
    source: str | None = "coingecko",
) -> FactCheckResult:
    claim = DetectedClaim(
        claim_id="claim_1",
        text=claim_text,
        span=ClaimSpan(start=0, end=len(claim_text)),
        claim_type=claim_type,
        raw_value=raw_value,
        raw_value_text=raw_value_text,
        subject={"asset": "BTC"} if claim_type == "CRYPTO_PRICE" else {},
    )
    evidence = (
        Evidence(
            source=source,
            value=actual_value,
            value_text=actual_text,
            unit="USD" if claim_type == "CRYPTO_PRICE" else None,
            fetched_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
            raw={"asset": "BTC"},
        )
        if source
        else None
    )
    return FactCheckResult(
        claim=claim,
        evidence=evidence,
        verdict=verdict,
        confidence=Confidence(label=confidence_label, score=confidence_score),
        diff_pct=diff_pct,
        reason="Diff 4.13% within 3-10% band → PARTIALLY_TRUE.",
    )


def _make_response(*results: FactCheckResult) -> FactCheckResponse:
    return FactCheckResponse(
        conversation_id="conv_1",
        speaker_id="spk_1",
        fact_check_results=list(results),
        stats=FactCheckStats(
            claims_detected=len(results),
            verified=sum(1 for r in results if r.verdict in {"TRUE", "FALSE", "PARTIALLY_TRUE"}),
            skipped=sum(1 for r in results if r.verdict not in {"TRUE", "FALSE", "PARTIALLY_TRUE"}),
        ),
    )


# --------------------------------------------------------------------------- #
# Tests                                                                       #
# --------------------------------------------------------------------------- #


def test_save_response_persists_all_results(repo: FactCheckRepository):
    response = _make_response(_make_result(), _make_result(claim_text="ETH at 3000"))
    ids = repo.save_response(response)
    assert len(ids) == 2
    assert all(isinstance(i, int) and i > 0 for i in ids)
    assert repo.count() == 2


def test_save_response_with_no_results_is_noop(repo: FactCheckRepository):
    response = FactCheckResponse(conversation_id="c", speaker_id="s")
    assert repo.save_response(response) == []
    assert repo.count() == 0


def test_save_result_persists_single_row(repo: FactCheckRepository):
    new_id = repo.save_result(
        conversation_id="conv_x",
        speaker_id="spk_x",
        result=_make_result(),
    )
    assert isinstance(new_id, int) and new_id > 0
    assert repo.count() == 1


def test_list_for_conversation_returns_only_that_conversation(repo: FactCheckRepository):
    repo.save_response(_make_response(_make_result()))
    repo.save_result(
        conversation_id="other_conv",
        speaker_id="spk_2",
        result=_make_result(),
    )
    rows = repo.list_for_conversation("conv_1")
    assert len(rows) == 1
    assert rows[0]["conversation_id"] == "conv_1"


def test_list_for_speaker_filters_by_both_keys(repo: FactCheckRepository):
    repo.save_response(_make_response(_make_result()))
    repo.save_result(
        conversation_id="conv_1",
        speaker_id="spk_other",
        result=_make_result(),
    )
    rows = repo.list_for_speaker("conv_1", "spk_1")
    assert len(rows) == 1
    assert rows[0]["speaker_id"] == "spk_1"


def test_static_fact_row_keeps_text_values(repo: FactCheckRepository):
    static_result = _make_result(
        claim_text="capital of France is Paris",
        claim_type="STATIC_FACT",
        raw_value=None,
        raw_value_text="Paris",
        actual_value=None,
        actual_text="Paris",
        verdict="TRUE",
        diff_pct=None,
        confidence_label="HIGH",
        confidence_score=1.0,
        source="wikipedia",
    )
    repo.save_response(_make_response(static_result))
    rows = repo.list_for_conversation("conv_1")
    assert rows[0]["claimed_text"] == "Paris"
    assert rows[0]["actual_text"] == "Paris"
    assert rows[0]["claim_type"] == "STATIC_FACT"


def test_source_unavailable_row_has_no_source(repo: FactCheckRepository):
    sa_result = _make_result(
        verdict="SOURCE_UNAVAILABLE",
        diff_pct=None,
        confidence_label="NONE",
        confidence_score=0.0,
        source=None,
        actual_value=None,
    )
    repo.save_response(_make_response(sa_result))
    rows = repo.list_for_conversation("conv_1")
    assert rows[0]["source"] is None
    assert rows[0]["evidence"] is None


def test_clear_removes_all_rows(repo: FactCheckRepository):
    repo.save_response(_make_response(_make_result(), _make_result()))
    assert repo.count() == 2
    repo.clear()
    assert repo.count() == 0


def test_list_for_conversation_orders_newest_first(repo: FactCheckRepository):
    first_id = repo.save_result(
        conversation_id="conv_1",
        speaker_id="spk_1",
        result=_make_result(claim_text="first"),
    )
    second_id = repo.save_result(
        conversation_id="conv_1",
        speaker_id="spk_1",
        result=_make_result(claim_text="second"),
    )
    rows = repo.list_for_conversation("conv_1")
    # Newer row (higher id) appears first.
    assert rows[0]["id"] == max(first_id, second_id)
    assert rows[1]["id"] == min(first_id, second_id)


def test_list_for_unknown_conversation_returns_empty(repo: FactCheckRepository):
    repo.save_response(_make_response(_make_result()))
    assert repo.list_for_conversation("nope") == []
    assert repo.list_for_conversation("") == []
