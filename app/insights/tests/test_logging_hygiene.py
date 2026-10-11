"""Outbound request URLs must never reach the logs.

httpx logs ``HTTP Request: <METHOD> <full URL> ...`` at INFO for every
response. The OpenWeather and Alpha Vantage clients pass their API keys as
query parameters, and every source client puts a transcript-derived claim
subject (city, ticker) in the URL, so ``setup_logging()`` pins the ``httpx``
and ``httpcore`` loggers to WARNING.

These tests pin that guard:
  * no key or subject in any log record, on success and on every error path
    (4xx, 5xx then retry, transport error);
  * the guard is load-bearing — without it the key does reach the log;
  * importing the Docker entrypoint (``app.insight_main``) leaves the loggers
    quiet, with and without uvicorn's own logging config applied first;
  * the ``POST /v1/fact-check`` response is identical with and without it.

Every request goes through ``httpx.MockTransport``; nothing leaves the host.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.insights.api.factcheck_routes import get_factcheck_engine
from app.insights.api.factcheck_routes import router as factcheck_router
from app.insights.core.factcheck.factcheck_engine import FactCheckEngine, SourceRouter
from app.insights.core.factcheck.source_clients import OpenWeatherClient, StockClient
from app.insights.core.factcheck.source_clients.base_client import BaseSourceClient
from app.insights.models.factcheck_models import ClaimSpan, DetectedClaim, FactCheckResponse
from app.insights.repository import (
    build_factcheck_repository_from_factory,
    get_factcheck_repository,
)
from app.insights.repository.db import Base
from app.security import verify_api_key
from app.utils.logger import setup_logging

FAKE_KEY = "FAKEKEY-not-a-real-key-7f3a"
_HTTP_LOGGERS = ("httpx", "httpcore")
_REPO_ROOT = Path(__file__).resolve().parents[3]

_WEATHER_OK = {"main": {"temp": 32.0}, "id": 1174872}
_STOCK_OK = {"Global Quote": {"05. price": "175.00"}}

Step = Callable[[httpx.Request], httpx.Response]

# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _claim(claim_type, subject: dict, value: float, unit: str) -> DetectedClaim:
    return DetectedClaim(
        claim_id="claim_1",
        text="x",
        span=ClaimSpan(start=0, end=1),
        claim_type=claim_type,
        raw_value=value,
        subject=subject,
        unit=unit,
    )


def _ok(payload: dict) -> Step:
    return lambda request: httpx.Response(200, json=payload)


def _status(code: int) -> Step:
    return lambda request: httpx.Response(code, json={"message": "upstream said no"})


def _connect_error(request: httpx.Request) -> httpx.Response:
    # The URL is in the exception text on purpose: it must not be logged either.
    raise httpx.ConnectError(f"connection refused: {request.url}", request=request)


def _mock_http(steps: list[Step], calls: list[httpx.Request]) -> httpx.Client:
    """httpx.Client that answers the n-th request with steps[n] (last one repeats)."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return steps[min(len(calls), len(steps)) - 1](request)

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


# client name -> (client factory, claim, claim subject that lands in the URL, success payload)
_CLIENTS: dict[str, tuple[Callable[[httpx.Client], BaseSourceClient], DetectedClaim, str, dict]] = {
    "openweather": (
        lambda http: OpenWeatherClient(api_key=FAKE_KEY, client=http),
        _claim("WEATHER", {"city": "Karachi"}, 32.0, "C"),
        "Karachi",
        _WEATHER_OK,
    ),
    "alphavantage": (
        lambda http: StockClient(api_key=FAKE_KEY, client=http),
        _claim("STOCK_PRICE", {"symbol": "AAPL"}, 175.0, "USD"),
        "AAPL",
        _STOCK_OK,
    ),
}

# scenario -> (steps built from the success payload, expected requests,
#              evidence expected?, app warning that proves the path ran)
_SCENARIOS: dict[str, tuple[Callable[[dict], list[Step]], int, bool, str | None]] = {
    "success": (lambda ok: [_ok(ok)], 1, True, None),
    "4xx": (lambda ok: [_status(401)], 1, False, "HTTP 401 (terminal)"),
    "5xx-then-retry": (lambda ok: [_status(503), _ok(ok)], 2, True, "server error 503"),
    "transport-error": (lambda ok: [_connect_error], 2, False, "transport error"),
}


def _record_text(record: logging.LogRecord) -> str:
    text = record.getMessage()
    if record.exc_info:
        text += logging.Formatter().formatException(record.exc_info)
    return text


def _assert_no_leak(records: list[logging.LogRecord], subject: str) -> None:
    for record in records:
        text = _record_text(record)
        assert FAKE_KEY not in text, f"API key logged by {record.name}: {text!r}"
        assert subject not in text, f"claim subject logged by {record.name}: {text!r}"
        if record.name.split(".")[0] in _HTTP_LOGGERS:
            assert record.levelno >= logging.WARNING, f"{record.name} logged at {record.levelname}"


@pytest.fixture()
def unguarded_httpx() -> Iterator[None]:
    """Temporarily undo the guard on the httpx logger (restored afterwards)."""
    httpx_logger = logging.getLogger("httpx")
    previous = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)
    try:
        yield
    finally:
        httpx_logger.setLevel(previous)


# --------------------------------------------------------------------------- #
# The guard itself                                                            #
# --------------------------------------------------------------------------- #


def test_setup_logging_pins_http_client_loggers_and_is_idempotent():
    # app.main calls setup_logging() again after the import-time call.
    setup_logging()
    setup_logging()
    for name in _HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.parametrize("scenario", list(_SCENARIOS))
@pytest.mark.parametrize("client_name", list(_CLIENTS))
def test_no_key_or_subject_in_any_log_record(client_name, scenario, caplog):
    make_client, claim, subject, ok_payload = _CLIENTS[client_name]
    build_steps, expected_calls, expect_evidence, expected_warning = _SCENARIOS[scenario]
    calls: list[httpx.Request] = []

    setup_logging()
    caplog.set_level(logging.DEBUG)  # root wide open: only the pin keeps httpx quiet
    evidence = make_client(_mock_http(build_steps(ok_payload), calls)).fetch(claim)

    # The secret and the subject really were on the wire...
    assert len(calls) == expected_calls
    assert all(FAKE_KEY in str(call.url) and subject in str(call.url) for call in calls)
    assert (evidence is not None) is expect_evidence
    if expected_warning:
        assert any(expected_warning in r.getMessage() for r in caplog.records)
    # ...and nowhere in the logs.
    _assert_no_leak(caplog.records, subject)


@pytest.mark.parametrize("client_name", list(_CLIENTS))
def test_guard_is_what_keeps_the_key_out(client_name, caplog, unguarded_httpx):
    make_client, claim, subject, ok_payload = _CLIENTS[client_name]

    caplog.set_level(logging.DEBUG)
    make_client(_mock_http([_ok(ok_payload)], [])).fetch(claim)

    httpx_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert any(FAKE_KEY in line and subject in line for line in httpx_lines), (
        "without the guard httpx should log the full request URL; if it no longer "
        "does, revisit whether the guard is still needed"
    )


# --------------------------------------------------------------------------- #
# Docker entrypoint                                                           #
# --------------------------------------------------------------------------- #

_ENTRYPOINT_PROBE = """
import logging, logging.config, sys
import dotenv
dotenv.load_dotenv = lambda *a, **k: False  # never read a developer .env
if sys.argv[1] == "uvicorn":
    from uvicorn.config import LOGGING_CONFIG
    logging.config.dictConfig(LOGGING_CONFIG)  # uvicorn configures logging before importing the app
import app.insight_main  # Docker CMD: uvicorn app.insight_main:app
print(logging.getLogger("httpx").getEffectiveLevel(), logging.getLogger("httpcore").getEffectiveLevel())
"""


@pytest.mark.parametrize("mode", ["plain-import", "uvicorn"])
def test_importing_docker_entrypoint_keeps_http_loggers_at_warning(mode, tmp_path):
    # Fresh interpreter, so nothing this test session already imported counts.
    # No VOICEIQ_* settings and an empty cwd, so no developer config is read.
    env = {k: v for k, v in os.environ.items() if not k.startswith("VOICEIQ_")}
    env.update(PYTHONPATH=str(_REPO_ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1")

    proc = subprocess.run(
        [sys.executable, "-c", _ENTRYPOINT_PROBE, mode],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode == 0, proc.stderr[-2000:]
    httpx_level, httpcore_level = (int(token) for token in proc.stdout.split()[-2:])
    assert httpx_level >= logging.WARNING
    assert httpcore_level >= logging.WARNING


# --------------------------------------------------------------------------- #
# API responses                                                               #
# --------------------------------------------------------------------------- #

_TRANSCRIPT = "The temperature in Karachi is 32 C. AAPL is at 175."


def _upstream(request: httpx.Request) -> httpx.Response:
    if request.url.host == "api.openweathermap.org":
        return httpx.Response(200, json=_WEATHER_OK)
    if request.url.host == "www.alphavantage.co":
        return httpx.Response(200, json=_STOCK_OK)
    return httpx.Response(404)


def _mocked_engine() -> FactCheckEngine:
    http = httpx.Client(transport=httpx.MockTransport(_upstream), timeout=5.0)
    router = SourceRouter(
        {
            "WEATHER": OpenWeatherClient(api_key=FAKE_KEY, client=http),
            "STOCK_PRICE": StockClient(api_key=FAKE_KEY, client=http),
        }
    )
    return FactCheckEngine(router=router)


@pytest.fixture()
def factcheck_client(tmp_path: Path) -> Iterator[TestClient]:
    from app.insights.repository import factcheck_orm_models, orm_models  # noqa: F401

    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'logging_hygiene.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=db_engine)
    repo = build_factcheck_repository_from_factory(
        sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    )

    app = FastAPI()
    app.include_router(factcheck_router, prefix="/v1")
    app.dependency_overrides[get_factcheck_engine] = _mocked_engine  # fresh engine (and cache) per request
    app.dependency_overrides[get_factcheck_repository] = lambda: repo
    app.dependency_overrides[verify_api_key] = lambda: "test-key"

    with TestClient(app) as client:
        yield client
    db_engine.dispose()


def _post(client: TestClient) -> dict:
    response = client.post(
        "/v1/fact-check",
        json={"conversation_id": "conv-logging", "speaker_id": "spk-1", "transcript_text": _TRANSCRIPT},
    )
    assert response.status_code == 200
    assert FAKE_KEY not in response.text
    return response.json()


def _without_timestamps(body: dict) -> dict:
    # Evidence.fetched_at is wall-clock time; everything else is deterministic.
    for result in body["fact_check_results"]:
        if result.get("evidence"):
            result["evidence"].pop("fetched_at", None)
    return body


def test_fact_check_api_response_is_unchanged_by_the_guard(factcheck_client, caplog):
    setup_logging()
    caplog.set_level(logging.DEBUG)
    guarded = _post(factcheck_client)
    _assert_no_leak(caplog.records, "Karachi")

    httpx_logger = logging.getLogger("httpx")
    previous = httpx_logger.level
    httpx_logger.setLevel(logging.NOTSET)
    try:
        unguarded = _post(factcheck_client)
    finally:
        httpx_logger.setLevel(previous)

    assert _without_timestamps(guarded) == _without_timestamps(unguarded)
    # Still the frozen v1 contract, with the expected verdicts.
    FactCheckResponse.model_validate(guarded)
    assert set(guarded) == {"conversation_id", "speaker_id", "fact_check_results", "stats"}
    assert [(r["claim"]["claim_type"], r["verdict"]) for r in guarded["fact_check_results"]] == [
        ("WEATHER", "TRUE"),
        ("STOCK_PRICE", "TRUE"),
    ]
