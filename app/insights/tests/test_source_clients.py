"""Unit tests for fact-check source clients.

Every test uses an in-process httpx mock transport - no real network calls.
This keeps the suite deterministic and CI-safe (free Actions minutes).
"""

from __future__ import annotations

import json
from typing import Callable

import httpx
import pytest

from app.insights.core.factcheck.source_clients import (
    CoinGeckoClient,
    ForexClient,
    OpenWeatherClient,
    StaticFactsClient,
    StockClient,
)
from app.insights.models.factcheck_models import ClaimSpan, DetectedClaim


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Return an httpx.Client that routes all requests through `handler`."""
    return httpx.Client(transport=httpx.MockTransport(handler), timeout=5.0)


def _claim(
    *,
    claim_type,
    raw_value=None,
    raw_value_text=None,
    subject=None,
    unit=None,
):
    return DetectedClaim(
        claim_id="claim_1",
        text="x",
        span=ClaimSpan(start=0, end=1),
        claim_type=claim_type,
        raw_value=raw_value,
        raw_value_text=raw_value_text,
        subject=subject or {},
        unit=unit,
    )


# --------------------------------------------------------------------------- #
# ForexClient                                                                 #
# --------------------------------------------------------------------------- #

def test_forex_returns_evidence_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["base"] == "USD"
        assert request.url.params["symbols"] == "PKR"
        return httpx.Response(
            200, json={"base": "USD", "rates": {"PKR": 280.5}}
        )

    client = ForexClient(client=_mock_client(handler))
    claim = _claim(
        claim_type="CURRENCY_RATE",
        raw_value=280.0,
        subject={"base": "USD", "quote": "PKR", "pair": "USD/PKR"},
    )
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.value == 280.5
    assert evidence.unit == "PKR"
    assert evidence.source == "exchangerate.host"


def test_forex_returns_none_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = ForexClient(client=_mock_client(handler))
    claim = _claim(
        claim_type="CURRENCY_RATE",
        raw_value=280.0,
        subject={"base": "USD", "quote": "PKR"},
    )
    assert client.fetch(claim) is None


def test_forex_skips_non_currency_claims():
    client = ForexClient(client=_mock_client(lambda r: httpx.Response(200, json={})))
    claim = _claim(claim_type="CRYPTO_PRICE", raw_value=1.0, subject={"asset": "BTC"})
    assert client.fetch(claim) is None


# --------------------------------------------------------------------------- #
# CoinGeckoClient                                                             #
# --------------------------------------------------------------------------- #

def test_coingecko_returns_evidence_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["ids"] == "bitcoin"
        return httpx.Response(200, json={"bitcoin": {"usd": 91234.5}})

    client = CoinGeckoClient(client=_mock_client(handler))
    claim = _claim(claim_type="CRYPTO_PRICE", raw_value=95000.0, subject={"asset": "BTC"})
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.value == 91234.5
    assert evidence.unit == "USD"


def test_coingecko_unknown_symbol_returns_none():
    client = CoinGeckoClient(
        client=_mock_client(lambda r: httpx.Response(200, json={}))
    )
    claim = _claim(
        claim_type="CRYPTO_PRICE", raw_value=1.0, subject={"asset": "XYZ_UNKNOWN"}
    )
    assert client.fetch(claim) is None


# --------------------------------------------------------------------------- #
# OpenWeatherClient                                                           #
# --------------------------------------------------------------------------- #

def test_openweather_returns_evidence_for_celsius():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["units"] = request.url.params["units"]
        captured["q"] = request.url.params["q"]
        captured["appid"] = request.url.params["appid"]
        return httpx.Response(200, json={"main": {"temp": 32.4}, "id": 1174872})

    client = OpenWeatherClient(api_key="testkey", client=_mock_client(handler))
    claim = _claim(
        claim_type="WEATHER",
        raw_value=32.0,
        subject={"city": "Karachi", "unit": "C"},
        unit="C",
    )
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.value == 32.4
    assert evidence.unit == "C"
    assert captured["units"] == "metric"
    assert captured["appid"] == "testkey"


def test_openweather_returns_evidence_for_fahrenheit():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["units"] == "imperial"
        return httpx.Response(200, json={"main": {"temp": 90.1}})

    client = OpenWeatherClient(api_key="testkey", client=_mock_client(handler))
    claim = _claim(
        claim_type="WEATHER",
        raw_value=90.0,
        subject={"city": "Phoenix", "unit": "F"},
        unit="F",
    )
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.unit == "F"


def test_openweather_missing_api_key_returns_none():
    client = OpenWeatherClient(api_key="", client=_mock_client(lambda r: httpx.Response(200)))
    claim = _claim(
        claim_type="WEATHER", raw_value=1.0, subject={"city": "Karachi", "unit": "C"}
    )
    assert client.fetch(claim) is None


def test_openweather_terminal_4xx_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"cod": 401, "message": "Invalid API key"})

    client = OpenWeatherClient(api_key="bad", client=_mock_client(handler))
    claim = _claim(
        claim_type="WEATHER", raw_value=32.0, subject={"city": "Karachi", "unit": "C"}
    )
    assert client.fetch(claim) is None


# --------------------------------------------------------------------------- #
# StockClient                                                                 #
# --------------------------------------------------------------------------- #

def test_stock_returns_evidence_on_success():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Global Quote": {"01. symbol": "AAPL", "05. price": "271.35"}},
        )

    client = StockClient(api_key="testkey", client=_mock_client(handler))
    claim = _claim(
        claim_type="STOCK_PRICE", raw_value=270.0, subject={"symbol": "AAPL"}
    )
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.value == 271.35
    assert evidence.unit == "USD"


def test_stock_rate_limit_note_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"Note": "Thank you for using Alpha Vantage! Our standard API rate limit..."},
        )

    client = StockClient(api_key="testkey", client=_mock_client(handler))
    claim = _claim(
        claim_type="STOCK_PRICE", raw_value=200.0, subject={"symbol": "AAPL"}
    )
    assert client.fetch(claim) is None


def test_stock_missing_api_key_returns_none():
    client = StockClient(api_key="", client=_mock_client(lambda r: httpx.Response(200)))
    claim = _claim(
        claim_type="STOCK_PRICE", raw_value=200.0, subject={"symbol": "AAPL"}
    )
    assert client.fetch(claim) is None


# --------------------------------------------------------------------------- #
# StaticFactsClient                                                           #
# --------------------------------------------------------------------------- #

def test_static_facts_extracts_capital_from_summary():
    extract = (
        "France, officially the French Republic, is a country in Western Europe. "
        "Its capital is Paris and the country has fourteen overseas regions."
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "France", "extract": extract})

    client = StaticFactsClient(client=_mock_client(handler))
    claim = _claim(
        claim_type="STATIC_FACT",
        raw_value_text="Paris",
        subject={"country": "France"},
    )
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.value_text == "Paris"
    assert evidence.source == "wikipedia"


def test_static_facts_returns_none_when_summary_has_no_capital_phrase():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "title": "Atlantis",
                "extract": "Atlantis is a fictional island mentioned in Plato's works.",
            },
        )

    client = StaticFactsClient(client=_mock_client(handler))
    claim = _claim(
        claim_type="STATIC_FACT",
        raw_value_text="Poseidonia",
        subject={"country": "Atlantis"},
    )
    assert client.fetch(claim) is None


def test_static_facts_matches_helper_is_case_and_accent_insensitive():
    assert StaticFactsClient.matches("paris", "Paris")
    assert StaticFactsClient.matches("São Paulo", "Sao Paulo")
    assert not StaticFactsClient.matches("Paris", "London")


@pytest.mark.parametrize(
    "extract,expected_capital",
    [
        # "Its capital is X"
        ("Its capital is Tokyo, the largest city.", "Tokyo"),
        # "with its capital in X"  (current Wikipedia phrasing for France)
        (
            "France is a unitary semi-presidential republic with its capital in Paris, "
            "the country's largest city.",
            "Paris",
        ),
        # "capital and largest city is X"
        ("Germany's capital and largest city is Berlin.", "Berlin"),
        # "X is the nation's capital"
        ("Pakistan is a country in South Asia. Islamabad is the nation's capital.", "Islamabad"),
        # "X is the capital and largest city"
        ("Japan is an island country. Tokyo is the capital and largest city.", "Tokyo"),
        # "capital city is X"
        ("Spain is a country in Europe. The capital city is Madrid.", "Madrid"),
    ],
)
def test_static_facts_extracts_capital_across_common_phrasings(
    extract: str, expected_capital: str
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"title": "X", "extract": extract})

    client = StaticFactsClient(client=_mock_client(handler))
    claim = _claim(
        claim_type="STATIC_FACT",
        raw_value_text=expected_capital,
        subject={"country": "Whatever"},
    )
    evidence = client.fetch(claim)
    assert evidence is not None, f"Failed phrasing: {extract!r}"
    assert evidence.value_text == expected_capital


# Regression: real Wikipedia France summary as of 2026-05-01. Capital
# sentence is ~970 chars deep, so this guards both the regex AND the
# scan-window length.
_FRANCE_REAL_EXTRACT = (
    "France, officially the French Republic, is a country primarily located "
    "in Western Europe. Its overseas regions and territories include French "
    "Guiana in South America, Saint Pierre and Miquelon in the North Atlantic, "
    "the French West Indies, and many islands in Oceania and the Indian Ocean. "
    "Metropolitan France shares borders with Belgium and Luxembourg to the "
    "north; Germany to the northeast; Switzerland to the east; Italy and Monaco "
    "to the southeast; Andorra and Spain to the south; and a maritime border "
    "with the United Kingdom to the northwest. Its metropolitan area extends "
    "from the Rhine to the Atlantic Ocean and from the Mediterranean Sea to "
    "the English Channel and the North Sea. Its 18 integral regions—five of "
    "which are overseas—span a combined area of 632,702 km2 (244,288 sq mi), "
    "with a total population estimated at over 69.1 million in 2026. Its "
    "capital, largest city and main cultural and economic centre is Paris."
)


def test_static_facts_handles_real_france_extract():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"title": "France", "extract": _FRANCE_REAL_EXTRACT}
        )

    client = StaticFactsClient(client=_mock_client(handler))
    claim = _claim(
        claim_type="STATIC_FACT",
        raw_value_text="Paris",
        subject={"country": "France"},
    )
    evidence = client.fetch(claim)
    assert evidence is not None
    assert evidence.value_text == "Paris"
