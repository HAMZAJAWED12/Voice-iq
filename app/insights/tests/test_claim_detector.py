"""Unit tests for the rule-based claim detector."""

from __future__ import annotations

import time

import pytest

from app.insights.core.factcheck.claim_detector import (
    _GOLD_KARATS,
    _NOT_PRICE_PER_UNITS,
    _NOT_PRICE_WORDS,
    _PER_UNIT_WORDS,
    ClaimDetector,
)


def test_detects_currency_rate():
    claims = ClaimDetector.detect("USD to PKR is 280")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "CURRENCY_RATE"
    assert c.raw_value == 280.0
    assert c.subject["pair"] == "USD/PKR"


def test_detects_crypto_price():
    claims = ClaimDetector.detect("Bitcoin is at 95000 dollars and trading well")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "CRYPTO_PRICE"
    assert c.raw_value == 95000.0
    assert c.subject["asset"] == "bitcoin"


def test_detects_stock_price():
    claims = ClaimDetector.detect("AAPL price 175.20 today")
    assert len(claims) == 1
    assert claims[0].claim_type == "STOCK_PRICE"
    assert claims[0].subject["symbol"] == "AAPL"
    assert claims[0].raw_value == 175.20


def test_detects_weather_with_unit():
    claims = ClaimDetector.detect("temperature in Karachi is 32 C")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "WEATHER"
    assert c.subject["city"] == "Karachi"
    assert c.unit == "C"
    assert c.raw_value == 32.0


def test_detects_static_fact():
    claims = ClaimDetector.detect("capital of France is Paris")
    assert len(claims) == 1
    c = claims[0]
    assert c.claim_type == "STATIC_FACT"
    assert c.subject["country"] == "France"
    assert c.raw_value_text == "Paris"


def test_detects_multiple_claims_in_one_text():
    text = "Bitcoin is at 95000 dollars. capital of France is Paris."
    claims = ClaimDetector.detect(text)
    types = sorted(c.claim_type for c in claims)
    assert types == ["CRYPTO_PRICE", "STATIC_FACT"]


def test_empty_input_returns_no_claims():
    assert ClaimDetector.detect("") == []
    assert ClaimDetector.detect(None) == []
    assert ClaimDetector.detect("   \t\n   ") == []


def test_no_match_returns_no_claims():
    claims = ClaimDetector.detect("Hello, how are you today?")
    assert claims == []


def test_overlapping_matches_keep_only_first():
    # Crafted to make CRYPTO_PRICE the earlier match; STOCK alone shouldn't fire.
    text = "BTC price 95000"
    claims = ClaimDetector.detect(text)
    assert len(claims) == 1
    assert claims[0].claim_type == "CRYPTO_PRICE"


def test_long_input_is_truncated_safely():
    # Build text with a valid claim near the start; long tail should not crash.
    text = "Bitcoin is at 95000 dollars. " + ("padding " * 5000)
    claims = ClaimDetector.detect(text)
    assert any(c.claim_type == "CRYPTO_PRICE" for c in claims)


# --------------------------------------------------------------------------- #
# M5: no claims from names, ages or counts; common price phrasing             #
# --------------------------------------------------------------------------- #

NBSP = "\u00a0"


@pytest.mark.parametrize(
    "text",
    [
        # Names and ages: an ambiguous subject with no price context.
        "Ada is 30.",
        "Ada is at 30 years old.",
        "My friend Sol is 25.",
        "eth is 3.",
        "doge is at 10.",
        "Brent is 30.",
        "sol at 150.",
        "eth = 3.",
        "The meeting with Sol is at 4 and Ada is 30 minutes late.",
        # A stock ticker not written in capitals needs price context too.
        "Meta is 30.",
        "Nvda is 900.",
        "Amd is at 150.",
        # Counts, durations, measures and ranks. Capitals or unambiguous
        # subjects, so only what follows the number rejects them.
        "META is 30 people.",
        "AMD is 5 minutes away.",
        "Bitcoin is 2 years old.",
        "Gold is 5 minutes from here.",
        "AAPL is at 30 times earnings.",
        "DOGE is 12 cents.",
        "Oil is 100 million barrels a day.",
        "Gold is 5m away.",
        "Bitcoin is 5mins away.",
        "Bitcoin is 10-year high.",
        "Gold is 29.99 monthly.",
        "1 BTC is 100 million sats.",
        "Ethereum is 18 decimals.",
        "BTC is 95 k.",
        "Bitcoin is 30" + NBSP + "years old.",
        # Signs, clock times and ranges.
        "Gold is 5% up this year.",
        "Gold is 30°.",
        "Bitcoin is at 3:15.",
        "Bitcoin is 10-20 minutes.",
        "Bitcoin is 1 to 2 hours to confirm.",
        "Bitcoin is 90 to 95k.",
        "Bitcoin is 90 to 95 thousand.",
        "Gold is 24 to 25 hundred.",
        "Bitcoin is 1 or 2 hours to confirm.",
        "Bitcoin is 1 to 2.5 hours to confirm.",
        "Bitcoin is 10–20 minutes.",
        "Bitcoin is at 12 o'clock.",
        # A share, a rate over time, or a fee.
        "Bitcoin is 5 per cent up.",
        "AAPL is 3 per cent higher.",
        "Gold is 50 a month.",
        "Gold is 50 a Month.",
        "Gold is 50 an hour.",
        "Platinum is 100 per month.",
        "Gold is 29.99 every month.",
        "Gold is 5 a.m.",
        "Meta is 30 dollars a day.",
        "Meta is $5 per click.",
        # Karats.
        "Gold is 24k.",
        "Gold is 18k.",
        "Gold is 24K.",
        # Crypto and stock sources quote USD; a price in another currency
        # would be checked against a USD figure, so it is not a claim.
        "BTC is 95000 rupees.",
        "BTC is 95000 Rupees.",
        "Bitcoin is 95000 PKR.",
        "Bitcoin is 80,000 CHF.",
        "Ethereum is 3500 quid.",
        "Bitcoin is 90000 in euros.",
        "Bitcoin is 95000 €.",
        "Bitcoin is 95000 ¥.",
        "Gold is 2400 £.",
    ],
)
def test_names_ages_counts_and_other_currencies_are_not_claims(text):
    assert ClaimDetector.detect(text) == []


@pytest.mark.parametrize("word", sorted(_NOT_PRICE_WORDS))
def test_every_listed_word_after_the_number_is_seen(word):
    assert ClaimDetector.detect(f"Bitcoin is 95 {word}.") == []


@pytest.mark.parametrize("unit", sorted(_NOT_PRICE_PER_UNITS))
def test_every_listed_unit_after_a_per_unit_word_is_seen(unit):
    assert ClaimDetector.detect(f"Bitcoin is 95 per {unit}.") == []


@pytest.mark.parametrize("word", sorted(_PER_UNIT_WORDS))
def test_every_per_unit_word_is_seen(word):
    assert ClaimDetector.detect(f"Gold is 50 {word} month.") == []


@pytest.mark.parametrize("karat", sorted(_GOLD_KARATS))
def test_every_gold_karat_is_a_purity_not_a_price(karat):
    assert ClaimDetector.detect(f"Gold is {int(karat)}k.") == []


@pytest.mark.parametrize(
    ("text", "claim_type", "subject_key", "subject", "value"),
    [
        ("Bitcoin is 95000.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("ETH price 3000.", "CRYPTO_PRICE", "asset", "eth", 3000.0),
        ("ETH is at 3000.", "CRYPTO_PRICE", "asset", "eth", 3000.0),
        ("sol is trading at 150", "CRYPTO_PRICE", "asset", "sol", 150.0),
        # Price context for an ambiguous subject: "$", a dollar word,
        # "per token", in any case and after "US" / "U.S.".
        ("ada is $0.45.", "CRYPTO_PRICE", "asset", "ada", 0.45),
        ("Sol is $150.", "CRYPTO_PRICE", "asset", "sol", 150.0),
        ("doge is 0.12 dollars", "CRYPTO_PRICE", "asset", "doge", 0.12),
        ("doge is 1 dollar", "CRYPTO_PRICE", "asset", "doge", 1.0),
        ("doge is 1 usd", "CRYPTO_PRICE", "asset", "doge", 1.0),
        ("sol is 150 USD", "CRYPTO_PRICE", "asset", "sol", 150.0),
        ("sol is 150 US dollars.", "CRYPTO_PRICE", "asset", "sol", 150.0),
        ("sol is 150 U.S. dollars.", "CRYPTO_PRICE", "asset", "sol", 150.0),
        ("eth is 3000" + NBSP + "dollars", "CRYPTO_PRICE", "asset", "eth", 3000.0),
        ("eth is 3500 per token.", "CRYPTO_PRICE", "asset", "eth", 3500.0),
        ("AAPL is at 175.", "STOCK_PRICE", "symbol", "AAPL", 175.0),
        ("AAPL is trading at 175.", "STOCK_PRICE", "symbol", "AAPL", 175.0),
        ("AAPL price 175.20 today", "STOCK_PRICE", "symbol", "AAPL", 175.20),
        ("META price 500.", "STOCK_PRICE", "symbol", "META", 500.0),
        ("Meta stock 500", "STOCK_PRICE", "symbol", "META", 500.0),
        ("Meta is 500 a share.", "STOCK_PRICE", "symbol", "META", 500.0),
        ("Gold price 2400.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        ("Gold is trading at 2400.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        ("Gold is 2400 per ounce.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        ("Oil is at 80 dollars.", "COMMODITY_PRICE", "commodity", "oil", 80.0),
        ("Oil is 80 a barrel.", "COMMODITY_PRICE", "commodity", "oil", 80.0),
        ("Brent is at 80 dollars.", "COMMODITY_PRICE", "commodity", "brent", 80.0),
        ("Brent is at 82 a barrel.", "COMMODITY_PRICE", "commodity", "brent", 82.0),
        ("BTC is 95,000.50 today", "CRYPTO_PRICE", "asset", "btc", 95000.5),
        ("Bitcoin is 95000 US dollars.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        # Conversational tails after the number do not hide a claim.
        ("Bitcoin is 95000 isn't it?", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("Bitcoin is 95000 um I think.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("AAPL is 175 my friend.", "STOCK_PRICE", "symbol", "AAPL", 175.0),
        ("Bitcoin is 95000 not 90000.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("BTC is at 95000 to 96000.", "CRYPTO_PRICE", "asset", "btc", 95000.0),
        ("Bitcoin is 95000 sir.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("Bitcoin is 95000 guys.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("Bitcoin is at 95,000 won't be long.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("Bitcoin is 95000 this week.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("AAPL is 175 in late trading.", "STOCK_PRICE", "symbol", "AAPL", 175.0),
        ("Gold is 2400 in Singapore.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        # A full stop ends what is read after the number.
        ("Bitcoin is 95000 dollars. A day ago it was 92000.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("Gold is 2400 dollars. A year ago it was 1900.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        # Not ranges: the second number is smaller, so its unit is not the price's.
        ("Bitcoin is 95000 or 3 percent above last week.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("Bitcoin is 95,000 -2% today.", "CRYPTO_PRICE", "asset", "bitcoin", 95000.0),
        ("BTC is 95000 or 2 years ago it was 30000.", "CRYPTO_PRICE", "asset", "btc", 95000.0),
        # A range: what follows its end counts.
        ("Gold is 2400 to 2500 an ounce.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        # The next line is not read: it holds no unit or multiplier.
        ("Gold is 2400\nmillion people watched.", "COMMODITY_PRICE", "commodity", "gold", 2400.0),
        ("Gold is 50 a\nmonth later it fell.", "COMMODITY_PRICE", "commodity", "gold", 50.0),
    ],
)
def test_price_phrasing_is_still_detected(text, claim_type, subject_key, subject, value):
    claims = ClaimDetector.detect(text)

    assert [(c.claim_type, c.subject[subject_key], c.raw_value) for c in claims] == [(claim_type, subject, value)]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("Bitcoin is 95k.", 95_000.0),
        ("BTC is at 1.2m dollars.", 1_200_000.0),
        ("Bitcoin is 95 thousand dollars.", 95_000.0),
        ("Bitcoin is at 1.5 million.", 1_500_000.0),
        ("AAPL is at 2K", 2_000.0),
        ("Bitcoin is 1.2M.", 1_200_000.0),
        ("BTC is 0.1 billion dollars.", 100_000_000.0),
        ("Gold is 2.4k.", 2_400.0),
        ("Gold is 2k.", 2_000.0),
        ("Bitcoin is 95 grand.", 95_000.0),
        ("Gold is 24 hundred dollars an ounce.", 2_400.0),
        ("Bitcoin is 1.2 mil.", 1_200_000.0),
        ("Bitcoin is 95-thousand.", 95_000.0),
        # Exact: 4.02 * 1000 in floating point is 4019.9999999999995.
        ("Gold is 4.02k.", 4_020.0),
        ("BTC is 2.01k dollars.", 2_010.0),
        # A multiplier only as a whole word.
        ("Bitcoin is 2 millionaires.", 2.0),
        # Ranges: a multiplier on both ends, or a start already at full size.
        ("Bitcoin is 95k to 100k.", 95_000.0),
        ("Bitcoin is 90 thousand to 95 thousand.", 90_000.0),
        ("Bitcoin is 95000 to 100k.", 95_000.0),
        ("Bitcoin is 95000 or 100 thousand by Friday.", 95_000.0),
    ],
)
def test_multipliers_scale_the_value(text, value):
    claims = ClaimDetector.detect(text)

    assert [c.raw_value for c in claims] == [value]


@pytest.mark.parametrize(
    ("text", "base", "quote", "value"),
    [
        ("1 USD = 280 PKR", "USD", "PKR", 280.0),
        ("100 USD = 28000 PKR.", "USD", "PKR", 280.0),
        ("1,000 USD = 280,000 PKR", "USD", "PKR", 280.0),
        ("10 EUR is 3250 PKR", "EUR", "PKR", 325.0),
        ("10 gbp equals 12.5 usd", "GBP", "USD", 1.25),
        ("1 USD is worth 280 PKR", "USD", "PKR", 280.0),
        ("1 USD is equal to 280 PKR", "USD", "PKR", 280.0),
        ("1 USD to PKR is 280", "USD", "PKR", 280.0),
        ("In 2022, USD to PKR is 280", "USD", "PKR", 280.0),
        # A year, a date or a number on the previous line is not an amount.
        ("In 2026 USD to PKR is 280", "USD", "PKR", 280.0),
        ("As of October 16 USD to PKR is 280", "USD", "PKR", 280.0),
        ("We sold 100\nUSD to PKR is 280 right?", "USD", "PKR", 280.0),
    ],
)
def test_currency_rate_forms(text, base, quote, value):
    claims = ClaimDetector.detect(text)

    assert [(c.claim_type, c.subject["pair"], c.raw_value) for c in claims] == [
        ("CURRENCY_RATE", f"{base}/{quote}", value)
    ]


@pytest.mark.parametrize(
    "text",
    [
        "0 USD = 280 PKR",
        # A rate is quoted per 1, 10, 100, ... units; any other number
        # before the currency is a year, a date or a count.
        "2 EUR is 650 PKR",
        "Since 2022 USD is 280 PKR",
        "On March 15 USD is 280 PKR",
        "50 USD is 10 EUR less than the other plan.",
        # The amount must be on the same line.
        "We sold 100\nUSD is 280 PKR right?",
        # A total for 100 units is not the rate.
        "100 USD to PKR is 28000",
        "100 USD = PKR 28,000",
        "1,000 USD to PKR is 280000",
        "100 USD to PKR is 28000, the customer said, and that was the end of the call for today.",
    ],
)
def test_totals_and_stray_numbers_are_not_currency_rates(text):
    assert ClaimDetector.detect(text) == []


def test_a_rate_after_another_claims_value_is_kept():
    # "280" ends the first rate; it is not an amount for the second.
    claims = ClaimDetector.detect("usd to pkr is 280 eur to pkr is 305")

    assert [(c.subject["pair"], c.raw_value) for c in claims] == [("USD/PKR", 280.0), ("EUR/PKR", 305.0)]


def test_a_number_is_never_cut_short_to_dodge_a_unit_word():
    # "25 years" must not become a claim of 2 by backtracking into the number.
    assert ClaimDetector.detect("Bitcoin is 25 years old.") == []
    assert ClaimDetector.detect("Gold price 12.5 years ago") == []


def test_a_rejected_mention_does_not_hide_an_overlapping_claim():
    # "Ada is 1" and "1 EUR = 325 PKR" share the "1". The name is rejected
    # before overlaps are resolved, so the currency claim survives.
    claims = ClaimDetector.detect("Ada is 1 EUR = 325 PKR")

    assert [(c.claim_id, c.subject["pair"], c.raw_value) for c in claims] == [("claim_1", "EUR/PKR", 325.0)]


def test_the_word_after_a_number_is_read_on_the_same_line_only():
    # One speaker turn per line: the next line's first word says nothing
    # about this line's number.
    claims = ClaimDetector.detect("Gold is 2400\nSilver is 30\nCustomer: Bitcoin is 95000\nYears ago it was 100")

    assert [(c.claim_type, c.raw_value) for c in claims] == [
        ("COMMODITY_PRICE", 2400.0),
        ("COMMODITY_PRICE", 30.0),
        ("CRYPTO_PRICE", 95000.0),
    ]


@pytest.mark.parametrize(
    ("text", "city", "value", "unit"),
    [
        ("temperature in Karachi is 32 C", "Karachi", 32.0, "C"),
        ("The temperature in London is 18 F.", "London", 18.0, "F"),
        ("weather in New York 25", "New York", 25.0, None),
        ("temp at Lahore: 40°C", "Lahore", 40.0, "C"),
    ],
)
def test_weather_claims_read_as_before(text, city, value, unit):
    claims = ClaimDetector.detect(text)

    assert [(c.subject["city"], c.raw_value, c.unit) for c in claims] == [(city, value, unit)]


@pytest.mark.parametrize(
    "text",
    [
        "1," * 5000,
        "1." * 5000,
        "BTC is" + " " * 9990 + "x",
        "USD to PKR" + " " * 9985 + "x",
        "temperature in Karachi is 5" + " " * 9970,
        "temperature in Karachi" + " " * 9975 + "x",
        "1 USD" + " " * 9994 + "x",
        "1 USD = " + "1," * 4990,
        "BTC is 5" + " " * 9991 + "?",
        "BTC is 5" + NBSP * 9991 + "?",
        "BTC is 5" + " to 5" * 1990,
    ],
    ids=[
        "comma-run",
        "dot-run",
        "space-run-price",
        "space-run-currency",
        "space-run-weather-value",
        "space-run-weather-city",
        "space-run-currency-amount",
        "comma-run-currency-amount",
        "space-run-after-price",
        "nbsp-run-after-price",
        "range-run-after-price",
    ],
)
def test_scan_time_stays_linear_on_hostile_input(text):
    # On 10 000 characters a quadratic pattern took 0.5-2 s here; the
    # linear scan takes a few milliseconds.
    start = time.perf_counter()
    ClaimDetector.detect(text)

    assert time.perf_counter() - start < 0.25
