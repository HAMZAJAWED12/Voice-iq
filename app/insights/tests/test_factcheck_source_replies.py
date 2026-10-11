"""Bad provider replies never become verdicts and never sink a run.

A source client talks to a third-party API. Whatever comes back — a
non-finite number (NaN, Infinity, 1e999), JSON that is not an object, a
non-object where a nested object belongs, a number too large for a float, or
a body that cannot be decoded — must end as the existing v1 verdict
``SOURCE_UNAVAILABLE`` for that claim only:

  * every source client returns ``None`` (the client contract) instead of
    raising or handing back non-finite evidence;
  * the engine isolates each claim: the other claims in the run complete;
  * ``POST /v1/fact-check`` answers 200 with strict JSON, never 500;
  * ``/v1/process-audio`` answers 200 with ``fact_checks_v2`` degraded at the
    claim level, not replaced by an error stub.

No v1 model or literal changes: ``SOURCE_UNAVAILABLE`` already exists.
Every request goes through ``httpx.MockTransport``; nothing leaves the host.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
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
from app.insights.core.factcheck.source_clients import (
    CoinGeckoClient,
    ForexClient,
    OpenWeatherClient,
    StaticFactsClient,
    StockClient,
)
from app.insights.core.factcheck.source_clients.base_client import BaseSourceClient
from app.insights.models.factcheck_models import DetectedClaim, Evidence
from app.insights.repository import (
    build_factcheck_repository_from_factory,
    get_factcheck_repository,
)
from app.insights.repository.db import Base
from app.pipeline.orchestrator import VoiceIQOrchestrator
from app.routes import process_audio
from app.security import verify_api_key
from app.security.rate_limit import factcheck_rate_limit, process_audio_rate_limit
from app.utils.job_io import JobIO

FAKE_KEY = "FAKEKEY-not-a-real-key-7f3a"

# One claim per source client, detected by the real regex detector.
_TRANSCRIPT = (
    "The temperature in Karachi is 32 C. AAPL is at 175. USD to PKR is 280. "
    "BTC is at 95000. The capital of France is Paris."
)

_WEATHER = "api.openweathermap.org"
_STOCK = "www.alphavantage.co"
_FOREX = "api.exchangerate.host"
_CRYPTO = "api.coingecko.com"
_WIKI = "en.wikipedia.org"
_NUMERIC_HOSTS = [_WEATHER, _STOCK, _FOREX, _CRYPTO]
_ALL_HOSTS = [*_NUMERIC_HOSTS, _WIKI]

_CLAIM_TYPE_OF_HOST = {
    _WEATHER: "WEATHER",
    _STOCK: "STOCK_PRICE",
    _FOREX: "CURRENCY_RATE",
    _CRYPTO: "CRYPTO_PRICE",
    _WIKI: "STATIC_FACT",
}

# Good replies that make every claim in _TRANSCRIPT come out TRUE.
_GOOD: dict[str, bytes] = {
    _WEATHER: b'{"main": {"temp": 32.0}, "id": 1174872}',
    _STOCK: b'{"Global Quote": {"05. price": "175.00"}}',
    _FOREX: b'{"success": true, "base": "USD", "rates": {"PKR": 280.0}}',
    _CRYPTO: b'{"bitcoin": {"usd": 95000.0}}',
    _WIKI: b'{"title": "France", "extract": "France is a country in Western Europe. Its capital is Paris."}',
}

# Where each numeric provider puts its value, as a JSON template.
_VALUE_SLOT: dict[str, str] = {
    _WEATHER: '{"main": {"temp": %s}, "id": 1174872}',
    _STOCK: '{"Global Quote": {"05. price": %s}}',
    _FOREX: '{"rates": {"PKR": %s}}',
    _CRYPTO: '{"bitcoin": {"usd": %s}}',
}

# JSON number tokens Python's json module accepts but that are not finite,
# their string spellings, and an integer too large to convert to a float.
_NON_FINITE_LITERALS = {
    "nan-token": "NaN",
    "infinity-token": "Infinity",
    "minus-infinity-token": "-Infinity",
    "overflow-1e999": "1e999",
    "nan-string": '"NaN"',
    "infinity-string": '"Infinity"',
    "huge-int": "1" + "0" * 400,
}

# A non-object where the client expects a nested object (or, for Wikipedia,
# a string extract).
_NESTED_WRONG_TYPE: dict[str, list[bytes]] = {
    _WEATHER: [b'{"main": [32.0]}', b'{"main": "32"}'],
    _STOCK: [b'{"Global Quote": ["175.00"]}', b'{"Global Quote": "175.00"}'],
    _FOREX: [b'{"rates": [280.0]}', b'{"rates": "280"}'],
    _CRYPTO: [b'{"bitcoin": 95000}', b'{"bitcoin": ["usd", 95000]}'],
    _WIKI: [b'{"extract": 42}', b'{"extract": ["Its capital is Paris."]}'],
}

_NON_OBJECT_BODIES = {"list": b"[1]", "string": b'"x"', "number": b"123", "true": b"true"}


class _Reply:
    """A canned provider reply: raw body bytes plus optional headers."""

    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {"Content-Type": "application/json"}

    def response(self) -> httpx.Response:
        return httpx.Response(200, content=self.body, headers=self.headers)


_UNDECODABLE = {
    # Declared gzip, body is not gzip: httpx raises DecodingError on read.
    "corrupt-gzip": _Reply(
        b"\x00\x01not-gzip-at-all",
        {"Content-Type": "application/json", "Content-Encoding": "gzip"},
    ),
    # Not valid UTF-8 / JSON at all.
    "invalid-utf8": _Reply(b"\xff\xfe\xfa{"),
}


# A non-finite number outside the field the client reads: the reply is
# malformed as a whole and is rejected (the JSON parse hooks), not used.
_NON_FINITE_ELSEWHERE: dict[str, list[bytes]] = {
    _WEATHER: [b'{"main": {"temp": 32.0}, "id": NaN}', b'{"main": {"temp": 32.0}, "id": 1e999}'],
    _STOCK: [b'{"Global Quote": {"05. price": "175.00", "09. change": -Infinity}}'],
    _FOREX: [b'{"rates": {"PKR": 280.0, "EUR": Infinity}}'],
    _CRYPTO: [b'{"bitcoin": {"usd": 95000.0, "usd_24h_change": NaN}}'],
    _WIKI: [b'{"title": NaN, "extract": "France is a country in Western Europe. Its capital is Paris."}'],
}

# Nested deeper than Python's json decoder allows: RecursionError while parsing.
_TOO_DEEP = 100_000
_TOO_DEEP_BODY = b'{"x": ' + b"[" * _TOO_DEEP + b"]" * _TOO_DEEP + b"}"


def _bad_replies() -> list[tuple[str, str, _Reply]]:
    cases: list[tuple[str, str, _Reply]] = []
    for host in _NUMERIC_HOSTS:
        for name, literal in _NON_FINITE_LITERALS.items():
            cases.append((host, name, _Reply((_VALUE_SLOT[host] % literal).encode())))
    for host, bodies in _NESTED_WRONG_TYPE.items():
        for i, body in enumerate(bodies):
            cases.append((host, f"nested-wrong-type-{i}", _Reply(body)))
    for host, bodies in _NON_FINITE_ELSEWHERE.items():
        for i, body in enumerate(bodies):
            cases.append((host, f"non-finite-elsewhere-{i}", _Reply(body)))
    for host in _ALL_HOSTS:
        for name, body in _NON_OBJECT_BODIES.items():
            cases.append((host, f"non-object-{name}", _Reply(body)))
        for name, reply in _UNDECODABLE.items():
            cases.append((host, name, reply))
        cases.append((host, "too-deep-to-parse", _Reply(_TOO_DEEP_BODY)))
    return cases


_BAD_REPLIES = _bad_replies()
_BAD_IDS = [f"{host.split('.')[-2]}-{name}" for host, name, _ in _BAD_REPLIES]

# A representative subset for the slower engine / API / process-audio tests:
# one of each failure class, on every host it applies to.
_REPRESENTATIVE_NAMES = {
    "nan-token",
    "overflow-1e999",
    "huge-int",
    "nested-wrong-type-0",
    "non-finite-elsewhere-0",
    "non-object-list",
    "corrupt-gzip",
}

# Finite but degenerate: so small that the relative difference to the claimed
# value overflows to inf. The client hands back finite evidence; the run must
# still not turn it into a verdict.
_TINY_LITERALS = {"subnormal-5e-324": "5e-324", "tiny-1e-310": "1e-310", "tiny-string": '"5e-324"'}

_RUN_LEVEL = [(h, n, r) for h, n, r in _BAD_REPLIES if n in _REPRESENTATIVE_NAMES] + [
    (host, name, _Reply((_VALUE_SLOT[host] % literal).encode()))
    for host in _NUMERIC_HOSTS
    for name, literal in _TINY_LITERALS.items()
]
_RUN_LEVEL_IDS = [f"{host.split('.')[-2]}-{name}" for host, name, _ in _RUN_LEVEL]

# A valid reply whose metadata field (copied into Evidence.raw) is a list
# nested too deep to serialize. The claim is still verifiable.
_DEEP_FIELD = b"[" * 150 + b"]" * 150
_DEEP_METADATA = {
    _WEATHER: b'{"main": {"temp": 32.0}, "id": ' + _DEEP_FIELD + b"}",
    _WIKI: b'{"title": '
    + _DEEP_FIELD
    + b', "extract": "France is a country in Western Europe. Its capital is Paris."}',
}


def _upstream(bad_host: str | None = None, bad: _Reply | None = None) -> httpx.Client:
    """One mocked httpx.Client for all five providers; `bad_host` gets `bad`."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == bad_host and bad is not None:
            return bad.response()
        if host in _GOOD:
            return _Reply(_GOOD[host]).response()
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def _router(http: httpx.Client) -> SourceRouter:
    return SourceRouter(
        {
            "WEATHER": OpenWeatherClient(api_key=FAKE_KEY, client=http),
            "STOCK_PRICE": StockClient(api_key=FAKE_KEY, client=http),
            "CURRENCY_RATE": ForexClient(client=http),
            "CRYPTO_PRICE": CoinGeckoClient(client=http),
            "STATIC_FACT": StaticFactsClient(client=http),
        }
    )


def _engine(bad_host: str | None = None, bad: _Reply | None = None) -> FactCheckEngine:
    return FactCheckEngine(router=_router(_upstream(bad_host, bad)))


def _claims() -> dict[str, DetectedClaim]:
    from app.insights.core.factcheck.claim_detector import ClaimDetector
    from app.insights.core.factcheck.classifier import ClaimClassifier

    return {c.claim_type: c for c in ClaimClassifier.classify(ClaimDetector.detect(_TRANSCRIPT))}


def _reject_constant(token: str) -> float:
    raise ValueError(f"non-finite JSON constant in response: {token}")


def _strict_json(text: str) -> dict:
    """Parse as standard JSON: NaN / Infinity tokens are an error."""
    return json.loads(text, parse_constant=_reject_constant)


def _verdicts(results: list[dict]) -> dict[str, str]:
    return {r["claim"]["claim_type"]: r["verdict"] for r in results}


def _expected(bad_host: str) -> dict[str, str]:
    return {
        claim_type: ("SOURCE_UNAVAILABLE" if host == bad_host else "TRUE")
        for host, claim_type in _CLAIM_TYPE_OF_HOST.items()
    }


# --------------------------------------------------------------------------- #
# Source clients                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", _ALL_HOSTS)
def test_good_reply_still_gives_finite_evidence(host):
    claim = _claims()[_CLAIM_TYPE_OF_HOST[host]]
    client = _router(_upstream()).client_for(claim.claim_type)
    assert client is not None

    evidence = client.fetch(claim)

    assert isinstance(evidence, Evidence)
    if evidence.value is not None:
        assert math.isfinite(evidence.value)


@pytest.mark.parametrize(("host", "name", "reply"), _BAD_REPLIES, ids=_BAD_IDS)
def test_bad_reply_gives_no_evidence_and_never_raises(host, name, reply):
    claim = _claims()[_CLAIM_TYPE_OF_HOST[host]]
    client = _router(_upstream(host, reply)).client_for(claim.claim_type)
    assert client is not None

    assert client.fetch(claim) is None


@pytest.mark.parametrize("host", list(_DEEP_METADATA))
def test_odd_metadata_field_is_not_copied_into_evidence(host):
    claim = _claims()[_CLAIM_TYPE_OF_HOST[host]]
    client = _router(_upstream(host, _Reply(_DEEP_METADATA[host]))).client_for(claim.claim_type)
    assert client is not None

    evidence = client.fetch(claim)

    assert isinstance(evidence, Evidence)  # the claim is still verifiable
    assert all(not isinstance(v, list | dict) for v in evidence.raw.values())
    _strict_json(json.dumps(evidence.model_dump(mode="json"), allow_nan=False))


# --------------------------------------------------------------------------- #
# Engine: per-claim isolation                                                 #
# --------------------------------------------------------------------------- #


def _assert_isolated(body: dict, bad_host: str) -> None:
    assert _verdicts(body["fact_check_results"]) == _expected(bad_host)
    bad_type = _CLAIM_TYPE_OF_HOST[bad_host]
    bad_result = next(r for r in body["fact_check_results"] if r["claim"]["claim_type"] == bad_type)
    assert bad_result["evidence"] is None
    assert bad_result["diff_pct"] is None
    assert body["stats"] == {"claims_detected": 5, "verified": 4, "skipped": 1}


@pytest.mark.parametrize(("host", "name", "reply"), _RUN_LEVEL, ids=_RUN_LEVEL_IDS)
def test_one_bad_reply_affects_only_its_own_claim(host, name, reply):
    response = _engine(host, reply).run(conversation_id="c1", speaker_id="s1", transcript_text=_TRANSCRIPT)

    body = response.model_dump(mode="json")
    _strict_json(json.dumps(body, allow_nan=False))  # serializable as standard JSON
    _assert_isolated(body, host)


class _ExplodingClient(BaseSourceClient):
    """A client that breaks its own contract and raises."""

    name = "exploding"

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        raise RuntimeError("client bug")


class _NonFiniteClient(BaseSourceClient):
    """A client that breaks its contract by returning non-finite evidence."""

    name = "non_finite"

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        return Evidence(source=self.name, value=float("nan"), unit="USD")


@pytest.mark.parametrize("rogue", [_ExplodingClient, _NonFiniteClient], ids=["raises", "non-finite-evidence"])
def test_engine_contains_a_client_that_breaks_its_contract(rogue):
    router = _router(_upstream())
    router._mapping["STOCK_PRICE"] = rogue()
    response = FactCheckEngine(router=router).run(conversation_id="c1", speaker_id="s1", transcript_text=_TRANSCRIPT)

    body = response.model_dump(mode="json")
    _strict_json(json.dumps(body, allow_nan=False))
    _assert_isolated(body, _STOCK)


# --------------------------------------------------------------------------- #
# POST /v1/fact-check                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture()
def factcheck_app(tmp_path: Path) -> Iterator[tuple[FastAPI, TestClient]]:
    from app.insights.repository import factcheck_orm_models, orm_models  # noqa: F401

    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'source_replies.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=db_engine)
    repo = build_factcheck_repository_from_factory(
        sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    )
    app = FastAPI()
    app.include_router(factcheck_router, prefix="/v1")
    app.dependency_overrides[get_factcheck_repository] = lambda: repo
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    # Many parametrized requests in one test run: keep them out of the shared
    # in-memory rate-limit bucket (rate limiting has its own tests).
    app.dependency_overrides[factcheck_rate_limit] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        yield app, client
    db_engine.dispose()


@pytest.mark.parametrize(("host", "name", "reply"), _RUN_LEVEL, ids=_RUN_LEVEL_IDS)
def test_fact_check_api_never_500s_on_a_bad_reply(host, name, reply, factcheck_app):
    app, client = factcheck_app
    app.dependency_overrides[get_factcheck_engine] = lambda: _engine(host, reply)

    response = client.post(
        "/v1/fact-check",
        json={"conversation_id": "conv-bad-reply", "speaker_id": "spk-1", "transcript_text": _TRANSCRIPT},
    )

    assert response.status_code == 200, response.text[:500]
    _assert_isolated(_strict_json(response.text), host)


# --------------------------------------------------------------------------- #
# /v1/process-audio                                                           #
# --------------------------------------------------------------------------- #

_WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 120


@pytest.fixture()
def process_audio_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """The real route with the pipeline, job storage and persistence stubbed."""

    def fake_run(self, *, job_id, **_kwargs) -> dict:
        return {
            "request_id": job_id,
            "transcript": _TRANSCRIPT,
            "fact_checks": [],
            "warnings": [],
            "pipeline_meta": {"status": "ok", "timings_ms": {}, "skipped_steps": []},
        }

    class _NoopRepository:
        def save_response(self, response) -> None:
            return None

    monkeypatch.setattr(VoiceIQOrchestrator, "run", fake_run)
    monkeypatch.setattr(process_audio, "JobIO", lambda: JobIO(base_dir=tmp_path / "jobs"))
    monkeypatch.setattr(process_audio, "factcheck_repository", _NoopRepository())

    app = FastAPI()
    app.include_router(process_audio.router, prefix="/v1")
    app.dependency_overrides[verify_api_key] = lambda: "test-key"
    # Many parametrized requests in one test run: keep them out of the shared
    # in-memory rate-limit bucket (rate limiting has its own tests).
    app.dependency_overrides[process_audio_rate_limit] = lambda: None
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize(("host", "name", "reply"), _RUN_LEVEL, ids=_RUN_LEVEL_IDS)
def test_process_audio_degrades_fact_checks_v2_instead_of_failing(host, name, reply, process_audio_client, monkeypatch):
    monkeypatch.setattr(process_audio, "get_factcheck_engine", lambda: _engine(host, reply))

    response = process_audio_client.post(
        "/v1/process-audio",
        params={"fact_check": "true"},
        files={"file": ("call.wav", _WAV_BYTES, "audio/wav")},
    )

    assert response.status_code == 200, response.text[:500]
    v2 = _strict_json(response.text)["fact_checks_v2"]
    assert v2.get("status") != "failed", v2
    _assert_isolated(v2, host)


def test_process_audio_with_good_replies_is_unchanged(process_audio_client, monkeypatch):
    monkeypatch.setattr(process_audio, "get_factcheck_engine", lambda: _engine())

    response = process_audio_client.post(
        "/v1/process-audio",
        params={"fact_check": "true"},
        files={"file": ("call.wav", _WAV_BYTES, "audio/wav")},
    )

    assert response.status_code == 200
    v2 = _strict_json(response.text)["fact_checks_v2"]
    assert set(v2) == {"conversation_id", "speaker_id", "fact_check_results", "stats"}
    assert set(_verdicts(v2["fact_check_results"]).values()) == {"TRUE"}
    assert v2["stats"] == {"claims_detected": 5, "verified": 5, "skipped": 0}


@pytest.mark.parametrize("host", list(_DEEP_METADATA))
def test_odd_metadata_field_does_not_break_either_endpoint(host, factcheck_app, process_audio_client, monkeypatch):
    app, client = factcheck_app
    app.dependency_overrides[get_factcheck_engine] = lambda: _engine(host, _Reply(_DEEP_METADATA[host]))
    monkeypatch.setattr(process_audio, "get_factcheck_engine", lambda: _engine(host, _Reply(_DEEP_METADATA[host])))

    api = client.post(
        "/v1/fact-check",
        json={"conversation_id": "conv-deep", "speaker_id": "spk-1", "transcript_text": _TRANSCRIPT},
    )
    audio = process_audio_client.post(
        "/v1/process-audio",
        params={"fact_check": "true"},
        files={"file": ("call.wav", _WAV_BYTES, "audio/wav")},
    )

    assert api.status_code == 200, api.text[:500]
    assert set(_verdicts(_strict_json(api.text)["fact_check_results"]).values()) == {"TRUE"}
    assert audio.status_code == 200, audio.text[:500]
    v2 = _strict_json(audio.text)["fact_checks_v2"]
    assert v2.get("status") != "failed", v2
    assert set(_verdicts(v2["fact_check_results"]).values()) == {"TRUE"}
