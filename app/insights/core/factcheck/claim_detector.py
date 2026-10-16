"""Claim detection.

Pure regex-based extractor. No LLM, no network. Each pattern is anchored on
both a *subject* (asset, currency, city, country) and a *value or assertion*
to keep false positives low.

Every match then passes `_keep_match`. A price (crypto, stock, commodity)
followed by what it counts or measures ("Ada is 30 years old", "Meta is 30
people", "5 per cent") or by another currency is not a USD price, and a
subject that is also a name or an everyday word ("Sol", "Brent"), or a
stock ticker not written in capitals ("Meta", "Nvda"), needs explicit price
context. A currency rate must be quoted per 1, 10, 100, ... units.

Output is a list of `DetectedClaim` objects. Classification (mapping the
match to a normalized subject + unit) is handled by `ClaimClassifier`; this
module only finds claim *spans* and tags them with a tentative `claim_type`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from re import Pattern

from app.insights.models.factcheck_models import (
    MAX_TRANSCRIPT_CHARS,
    ClaimSpan,
    ClaimType,
    DetectedClaim,
)

# --------------------------------------------------------------------------- #
# Pattern table                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _ClaimPattern:
    """Internal container for one regex rule."""

    claim_type: ClaimType
    pattern: Pattern[str]


_CURRENCY = r"USD|PKR|EUR|GBP|INR|AED|SAR|JPY|CNY|CAD|AUD"

# A claimed number: digits with optional "," or "." groups ("95,000.50").
# Greedy, so a number is never cut short ("25 years" never yields "2").
_NUMBER = r"\d+(?:[.,]\d+)*"

# A word read after a number: letters plus one apostrophe part, so "won't"
# is not read as "won" and "o'clock" stays one word.
_WORD = r"[^\W\d_]+(?:['’][^\W\d_]+)?"

# Whitespace that is not a line break, so a check never reads the next line
# (one speaker turn per line). Includes no-break and thin spaces.
_HSPACE = r"[^\S\n\r\v\f\x1c-\x1e\x85\u2028\u2029]"

_SCALE_WORDS = r"thousand|million|billion|hundred|grand|mil"


def _peek(prefix: str) -> str:
    """What follows a number on the same line; a lookahead body for `_PRICE_TAIL`.

    A sign ("5%", "2400 £", "3:15"), or up to three words ("30 people", "80
    a barrel", "30 dollars a day"), skipping a "US" / "U.S." before the
    first ("150 U.S. dollars"). A full stop ends the words, except after a
    one-letter word ("5 a.m."). Group names start with `prefix`.
    """
    return (
        _HSPACE + r"*-?(?:(?P<" + prefix + r"sign>[%°€£¥₹₨]|:\d\d)"
        r"|(?:u\.?" + _HSPACE + r"*s\b\.?" + _HSPACE + r"*)?"
        r"(?P<" + prefix + r"after>" + _WORD + r")"
        r"(?:(?:(?<![^\W\d_]{2})\.)?" + _HSPACE + r"*"
        r"(?P<" + prefix + r"after2>" + _WORD + r")"
        r"(?:" + _HSPACE + r"+(?P<" + prefix + r"after3>" + _WORD + r"))?)?)"
    )


# What follows the verb in every price pattern: an optional "$", the number,
# an optional multiplier ("95k", "1.2m", "95 thousand", "95-grand"), then
# two lookaheads, not consumed, for `_is_price_mention`: what follows the
# number, and an optional range end with what follows it ("10-20 minutes",
# "1 to 2 hours", "90 to 95k").
_PRICE_TAIL = (
    r"\s*(?:(?P<dollar>\$)\s*)?(?P<value>" + _NUMBER + r")"
    r"(?:(?P<suffix>[km])\b|(?:" + _HSPACE + r"+|-)(?P<scale>" + _SCALE_WORDS + r")\b)?"
    r"(?=(?:" + _peek("") + r")?)"
    r"(?=(?:" + _HSPACE + r"*(?:-|–|—|to|or)" + _HSPACE + r"*(?P<range_end>" + _NUMBER + r")"
    r"(?:(?P<range_suffix>[km])\b|" + _HSPACE + r"+(?P<range_scale>" + _SCALE_WORDS + r")\b)?"
    r"(?:" + _peek("range_") + r")?)?)"
)

# Keep patterns intentionally narrow. Each must require both a recognisable
# subject token AND an anchor (a number, "is", "=", "at"). Overlapping
# matches are resolved in `ClaimDetector.detect`.
# NOTE: multi-word verbs MUST precede single tokens in each alternation so
# the regex engine prefers "is at" over "is" before falling through.
_PATTERNS: list[_ClaimPattern] = [
    # CURRENCY_RATE: e.g. "USD to PKR is 280"
    _ClaimPattern(
        "CURRENCY_RATE",
        re.compile(
            r"\b(?P<base>" + _CURRENCY + r")\s*"
            r"(?:to|/|=)\s*"
            r"(?P<quote>" + _CURRENCY + r")"
            r"\s*(?:(?:is|=|at|:)\s*)?"
            r"(?P<value>" + _NUMBER + r")",
            re.IGNORECASE,
        ),
    ),
    # CURRENCY_RATE: e.g. "1 USD = 280 PKR", "100 USD is 28000 PKR" — the
    # rate is value / amount, on one line. The amount may start only where
    # a number starts: "\b" would also start inside "1,1,1,..." at every
    # comma, and rescanning each start makes the scan quadratic.
    _ClaimPattern(
        "CURRENCY_RATE",
        re.compile(
            r"(?<![\w.,])(?P<amount>" + _NUMBER + r")" + _HSPACE + r"*"
            r"(?P<base>" + _CURRENCY + r")" + _HSPACE + r"*"
            r"(?:=|equals|is" + _HSPACE + r"+equal" + _HSPACE + r"+to"
            r"|is" + _HSPACE + r"+worth|is)" + _HSPACE + r"*"
            r"(?P<value>" + _NUMBER + r")" + _HSPACE + r"*"
            r"(?P<quote>" + _CURRENCY + r")\b",
            re.IGNORECASE,
        ),
    ),
    # CRYPTO_PRICE: e.g. "Bitcoin is at 95000 dollars", "BTC price 95k"
    _ClaimPattern(
        "CRYPTO_PRICE",
        re.compile(
            r"\b(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|dogecoin|doge|"
            r"cardano|ada|ripple|xrp|litecoin|ltc)\b"
            r"\s+(?P<verb>is\s+trading\s+at|trading\s+at|is\s+at|price\s+is|"
            r"price|costs?|is|at|=)" + _PRICE_TAIL,
            re.IGNORECASE,
        ),
    ),
    # STOCK_PRICE: e.g. "AAPL price 175.20", "AAPL is at 175"
    _ClaimPattern(
        "STOCK_PRICE",
        re.compile(
            r"\b(?P<symbol>AAPL|TSLA|MSFT|GOOGL|GOOG|AMZN|META|NVDA|NFLX|"
            r"AMD|INTC|IBM|ORCL|CRM)\b"
            r"\s+(?P<verb>is\s+trading\s+at|trading\s+at|is\s+at|price\s+is|"
            r"price|stock|share|is|at|=)" + _PRICE_TAIL,
            re.IGNORECASE,
        ),
    ),
    # WEATHER: e.g. "temperature in Karachi is 32 C", "weather in London 18"
    _ClaimPattern(
        "WEATHER",
        re.compile(
            r"\b(?:temperature|temp|weather)\s+(?:in|at)\s+"
            r"(?P<city>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
            r"\s*(?:(?:is|=|:|at)\s*)?"
            r"(?P<value>\d+(?:\.\d+)?)\s*"
            r"(?:°\s*)?(?P<unit>[CF])?\b",
        ),
    ),
    # COMMODITY_PRICE: e.g. "gold price 2400", "oil is at 80 dollars"
    _ClaimPattern(
        "COMMODITY_PRICE",
        re.compile(
            r"\b(?P<commodity>gold|silver|oil|brent|wti|copper|platinum|natural\s+gas)\b"
            r"\s+(?P<verb>is\s+trading\s+at|trading\s+at|is\s+at|price\s+is|"
            r"price|costs?|is|at|=)" + _PRICE_TAIL,
            re.IGNORECASE,
        ),
    ),
    # STATIC_FACT: e.g. "capital of France is Paris"
    _ClaimPattern(
        "STATIC_FACT",
        re.compile(
            r"\bcapital\s+of\s+(?P<country>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
            r"\s+is\s+(?P<capital>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)",
        ),
    ),
]


#: Most digits a claimed number may have, integer and fraction digits
#: together. 15 is a float's guaranteed decimal precision (DBL_DIG): every
#: number of up to 15 digits converts to a float and back unchanged, and the
#: largest one (< 1e15) is far above any real price, rate or temperature —
#: yet far below float overflow (~1.8e308), where a 309-digit run used to
#: become an infinite claimed value that no response could serialize. A
#: longer number is not a checkable claim: the claim is dropped, not
#: truncated. The cap counts the digits as spoken; a multiplier ("billion")
#: or a currency amount's division can move the value past 1e15, but never
#: anywhere near overflow.
MAX_VALUE_DIGITS: int = 15

# Powers of ten for "95k", "1.2m", "24 hundred", "95 grand", "1.5 million".
_SCALE_EXPONENT: dict[str, int] = {
    "k": 3,
    "m": 6,
    "hundred": 2,
    "thousand": 3,
    "grand": 3,
    "million": 6,
    "mil": 6,
    "billion": 9,
}

# Amounts an exchange rate is quoted per ("1 USD = 280 PKR", "100 USD is
# 28000 PKR"). Any other number before the base currency is a year, a date
# or a count ("Since 2022 USD is 280 PKR"), not an amount.
_RATE_AMOUNTS: frozenset[float] = frozenset(10.0**k for k in range(MAX_VALUE_DIGITS))

# An amount of 10, 100, ... right before the base currency of "USD to PKR
# is 280" makes the value a total for that many units ("100 USD to PKR is
# 28000"), not the rate. Other numbers there are kept: a year, a date, or
# the previous rate's value ("USD to PKR is 280 EUR to PKR is 305").
# Searched in the few characters before the match.
_NUMBER_BEFORE = re.compile(r"(?<![\w.,])(" + _NUMBER + r")" + _HSPACE + r"*\Z")
_NUMBER_BEFORE_CHARS = 64

# The subject group of each price pattern.
_PRICE_SUBJECT_GROUP: dict[ClaimType, str] = {
    "CRYPTO_PRICE": "asset",
    "STOCK_PRICE": "symbol",
    "COMMODITY_PRICE": "commodity",
}

# Subjects that are also given names or everyday words ("Ada is 30", "my
# friend Sol is 25", "Brent is 30"). Unless written in capitals, they need
# explicit price context — and so does every stock ticker ("Meta", "Amd").
_AMBIGUOUS_SUBJECTS: frozenset[str] = frozenset({"ada", "sol", "eth", "doge", "brent"})

# Verbs that state nothing about price ("is", "at"); every other verb in the
# price patterns ("price", "costs", "trading at", "stock") is price context.
_PLAIN_VERBS: frozenset[str] = frozenset({"is", "is at", "at", "="})

# Words after the number that name US dollars ("US dollars" and "U.S.
# dollars" too: `_PRICE_TAIL` skips the "US").
_USD_WORDS: frozenset[str] = frozenset({"dollar", "dollars", "usd", "usdt", "usdc", "bucks"})

# Currencies other than the US dollar, and the rupee multipliers. The
# crypto and stock sources quote USD, so a price in rupees would be checked
# against a dollar figure ("95000 rupees", "90000 in euros", "2 lakh").
_OTHER_CURRENCY_WORDS: frozenset[str] = frozenset(
    {
        "rupee", "rupees", "rs", "pkr", "inr", "euro", "euros", "eur",
        "pound", "pounds", "gbp", "sterling", "pence", "quid", "yen", "jpy",
        "yuan", "cny", "rmb", "dirham", "dirhams", "aed", "riyal", "riyals",
        "sar", "cad", "aud", "chf", "sgd", "hkd", "nzd", "php", "mxn", "zar",
        "ngn", "krw", "brl", "franc", "francs", "peso", "pesos", "naira",
        "taka", "rand", "lira", "ringgit", "won", "ruble", "rubles", "rouble",
        "roubles", "baht", "reais", "lakh", "lakhs", "lac", "lacs", "crore",
        "crores", "cr",
    }
)  # fmt: skip

# Words after the number that say it is not a USD price: what it counts or
# measures ("30 people", "5 minutes", "2 years old", "30 times earnings",
# "100 million sats"), a multiplier this detector does not apply ("95 k"),
# or another currency (`_OTHER_CURRENCY_WORDS`, and the first word of
# "Canadian dollars", "Hong Kong dollars"). A sign after the number ("5%",
# "2400 £", "3:15") rejects the same way; see `_peek`.
_NOT_PRICE_WORDS: frozenset[str] = _OTHER_CURRENCY_WORDS | frozenset(
    {
        # time and age
        "second", "seconds", "sec", "secs", "minute", "minutes", "min",
        "mins", "hour", "hours", "hr", "hrs", "day", "days", "week", "weeks",
        "month", "months", "year", "years", "yr", "yrs", "decade", "decades",
        "old", "am", "pm", "o", "o'clock", "o’clock", "p", "ago", "late",
        "away", "apart", "long", "hourly", "daily", "nightly", "weekly",
        "monthly", "yearly", "annually",
        # people and things counted ("guys" and "man" are left out: "BTC is
        # 95000 guys" addresses people, it does not count them)
        "people", "person", "persons", "men", "women", "kid", "kids",
        "child", "children", "boys", "girls", "friends", "members",
        "employees", "staff", "users", "customers", "clients", "students",
        "players", "engineers", "workers", "times", "items", "units",
        "pieces", "coins", "tokens", "shares", "seats", "calls", "tickets",
        "orders", "barrel", "barrels", "ounce", "ounces", "oz", "tola",
        "tolas", "supply",
        # crypto units and counts
        "sat", "sats", "satoshi", "satoshis", "gwei", "wei", "lamport",
        "lamports", "decimal", "decimals", "confirmation", "confirmations",
        "block", "blocks", "drop", "drops",
        # measures and ranks
        "percent", "pct", "cent", "cents", "points", "pts", "bps", "x",
        "th", "st", "nd", "rd", "degree", "degrees", "mile", "miles", "km",
        "kilometers", "kilometres", "meter", "meters", "metre", "metres",
        "m", "cm", "mm", "foot", "feet", "ft", "inch", "inches", "kg", "kgs",
        "kilo", "kilos", "gram", "grams", "g", "lb", "lbs", "ton", "tons",
        "tonne", "tonnes", "liter", "liters", "litre", "litres", "gallon",
        "gallons", "karat", "karats", "carat", "carats", "kt",
        # multipliers not applied
        "k", "b", "bn", "trillion",
        # first word of another dollar or franc
        "canadian", "australian", "singapore", "hong", "swiss",
    }
)  # fmt: skip

# "5 per cent", "50 a month", "29.99 every month", "$5 per click", "5 a.m.":
# a per-unit word followed by one of these is a share, a rate over time or
# a fee, not a price. "80 a barrel" and "2400 per ounce" stay prices.
_PER_UNIT_WORDS: frozenset[str] = frozenset({"a", "an", "per", "each", "every"})
_NOT_PRICE_PER_UNITS: frozenset[str] = frozenset(
    {
        "cent", "m", "second", "minute", "hour", "day", "night", "week",
        "month", "year", "annum", "person", "head", "click", "lead", "seat",
        "session", "visit",
    }
)  # fmt: skip

# "a barrel", "per share": a per-unit word followed by one of these is
# price context for an ambiguous subject ("Brent is 82 a barrel").
_PRICE_PER_UNITS: frozenset[str] = frozenset({"barrel", "ounce", "oz", "share", "coin", "token"})

# "24k gold" is a purity in karats, not a $24,000 price.
_GOLD_KARATS: frozenset[float] = frozenset({9.0, 10.0, 14.0, 18.0, 22.0, 24.0})


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


class ClaimDetector:
    """Stateless detector: text in, list of `DetectedClaim` out.

    Detection is deterministic: given the same text, the same claims are
    produced in the same order. Matches that are not checkable claims are
    discarded first (`_keep_match`), so they never hide another claim.
    Spans are then made non-overlapping: on overlap the match that
    starts first wins, then the shorter one, then the one whose pattern is
    declared first.
    """

    @classmethod
    def detect(cls, text: str | None) -> list[DetectedClaim]:
        """Return all claims found in `text`.

        Returns an empty list for `None`, empty strings, or whitespace-only
        input. Strings longer than `MAX_TRANSCRIPT_CHARS` are truncated for
        scanning safety; the API layer is expected to reject oversized
        payloads earlier with a 422.
        """
        if not text or not text.strip():
            return []

        scan_text = text[:MAX_TRANSCRIPT_CHARS]
        raw_matches: list[tuple[int, int, ClaimType, re.Match[str]]] = []

        for rule in _PATTERNS:
            for m in rule.pattern.finditer(scan_text):
                if _keep_match(rule.claim_type, m):
                    raw_matches.append((m.start(), m.end(), rule.claim_type, m))

        # Resolve overlaps: prefer earliest start, then the shorter match; the
        # stable sort keeps declaration order for identical spans.
        raw_matches.sort(key=lambda item: (item[0], item[1]))
        kept: list[tuple[int, int, ClaimType, re.Match[str]]] = []
        last_end = -1
        for start, end, ctype, match in raw_matches:
            if start < last_end:
                continue  # overlap - skip
            kept.append((start, end, ctype, match))
            last_end = end

        claims: list[DetectedClaim] = []
        for start, end, ctype, match in kept:
            claim = _build_detected_claim(
                claim_id=f"claim_{len(claims) + 1}",
                text_snippet=scan_text[start:end],
                span=ClaimSpan(start=start, end=end),
                claim_type=ctype,
                match=match,
            )
            if claim is not None:
                claims.append(claim)
        return claims


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _keep_match(claim_type: ClaimType, match: re.Match[str]) -> bool:
    """False for a match that is not a checkable claim.

    Currency-rate matches go to `_is_rate_mention`, price matches to
    `_is_price_mention`; weather and static-fact matches are always kept.
    """
    if claim_type == "CURRENCY_RATE":
        return _is_rate_mention(match)
    if claim_type in _PRICE_SUBJECT_GROUP:
        return _is_price_mention(claim_type, match)
    return True


def _is_rate_mention(match: re.Match[str]) -> bool:
    """False when a currency-rate match states a total, not a rate.

    "<amount> BASE = <value> QUOTE" needs an amount of 1, 10, 100, ...
    (`_RATE_AMOUNTS`). "BASE to QUOTE is <value>" is dropped when an amount
    of 10, 100, ... stands right before it on the same line.
    """
    groups = match.groupdict()
    if "amount" in groups:
        return _to_float(groups.get("amount") or "") in _RATE_AMOUNTS
    before = match.string[max(0, match.start() - _NUMBER_BEFORE_CHARS) : match.start()]
    number = _NUMBER_BEFORE.search(before)
    amount = _to_float(number.group(1)) if number else None
    return amount is None or amount == 1.0 or amount not in _RATE_AMOUNTS


def _is_price_mention(claim_type: ClaimType, match: re.Match[str]) -> bool:
    """False when a crypto, stock or commodity match is not a USD price.

    Rejected when the number is followed by what it counts or measures, or
    by another currency (`_NOT_PRICE_WORDS`, a sign, a per-unit word plus
    one of `_NOT_PRICE_PER_UNITS`, "in euros"); for a range ("10-20
    minutes"), what follows its end counts, and a multiplier on the end only
    ("90 to 95k") rejects; "24k" gold is a purity in karats. Then an
    ambiguous subject — one of `_AMBIGUOUS_SUBJECTS`, or any stock ticker,
    unless written in capitals — needs price context: a "$", a dollar word,
    "a barrel" / "per share", or a price verb.
    """
    groups = match.groupdict()
    subject = groups.get(_PRICE_SUBJECT_GROUP[claim_type]) or ""
    scaled = bool(groups.get("suffix") or groups.get("scale"))

    # A range only when it rises ("10-20", "95000 to 96000"); "95000 - 5
    # percent" and "95000 or 2 years ago" are not ranges.
    tail = ""
    start = _scaled_value(groups, "value", "suffix", "scale")
    end = _scaled_value(groups, "range_end", "range_suffix", "range_scale")
    if start is not None and end is not None and end >= start:
        end_scaled = bool(groups.get("range_suffix") or groups.get("range_scale"))
        unscaled_start = _to_float(groups["value"]) or 0.0
        unscaled_end = _to_float(groups["range_end"]) or 0.0
        if end_scaled and not scaled and unscaled_start <= unscaled_end:
            return False  # "90 to 95k": the "k" may or may not scale the 90
        tail = "range_"

    sign = groups.get(tail + "sign")
    after, after2, after3 = ((groups.get(tail + name) or "").lower() for name in ("after", "after2", "after3"))
    names_dollars = after in _USD_WORDS
    if sign or after in _NOT_PRICE_WORDS:
        return False  # "5%", "at 3:15", "30 people", "95000 rupees"
    if after in _PER_UNIT_WORDS and after2 in _NOT_PRICE_PER_UNITS:
        return False  # "5 per cent", "50 a month"
    if names_dollars and after2 in _PER_UNIT_WORDS and after3 in _NOT_PRICE_PER_UNITS:
        return False  # "30 dollars a day"
    if after == "in" and after2 in _OTHER_CURRENCY_WORDS:
        return False  # "90000 in euros"
    if (
        subject.lower() == "gold"
        and (groups.get("suffix") or "").lower() == "k"
        and _to_float(groups.get("value") or "") in _GOLD_KARATS
    ):
        return False

    if subject.isupper():
        return True  # written as a ticker: "ETH", "META"
    if claim_type != "STOCK_PRICE" and subject.lower() not in _AMBIGUOUS_SUBJECTS:
        return True
    priced_per_unit = after in _PER_UNIT_WORDS and after2 in _PRICE_PER_UNITS
    verb = " ".join((groups.get("verb") or "").lower().split())
    return bool(groups.get("dollar")) or names_dollars or priced_per_unit or verb not in _PLAIN_VERBS


def _to_float(value: str, exponent: int = 0) -> float | None:
    """Parse '95,000.50' or '95000.50' safely, times 10**`exponent`.

    None on failure, when the number has more than `MAX_VALUE_DIGITS` digits,
    or when it is not finite. The exponent is applied while parsing, so
    "1.2" with exponent 6 is exactly 1200000.0.
    """
    cleaned = (value or "").replace(",", "")
    if sum(ch.isdigit() for ch in cleaned) > MAX_VALUE_DIGITS:
        return None
    try:
        number = float(f"{cleaned}e{exponent}")
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scaled_value(groups: dict[str, str | None], number: str, suffix: str, scale: str) -> float | None:
    """Group `number` times its multiplier ("95k", "1.2 million"); None if absent or unusable."""
    multiplier = (groups.get(suffix) or groups.get(scale) or "").lower()
    return _to_float(groups.get(number) or "", _SCALE_EXPONENT.get(multiplier, 0))


def _build_detected_claim(
    *,
    claim_id: str,
    text_snippet: str,
    span: ClaimSpan,
    claim_type: ClaimType,
    match: re.Match[str],
) -> DetectedClaim | None:
    """Convert a regex match into a `DetectedClaim`.

    Returns None — the claim is dropped — when the match carries a number
    that cannot be used (see `MAX_VALUE_DIGITS`).

    Subject extraction is *minimal* here — the classifier normalizes labels
    further (e.g. "btc" → "BTC"). This keeps the detector simple and the
    classifier the single source of truth for normalization.
    """
    groups = match.groupdict()
    raw_value = _scaled_value(groups, "value", "suffix", "scale") if "value" in groups else None
    if "value" in groups and raw_value is None:
        return None
    if "amount" in groups:
        amount = _to_float(groups.get("amount") or "")
        if raw_value is None or not amount:  # for the type checker: `_is_rate_mention`
            return None  # admits only 1, 10, 100, ... and the value was checked above
        raw_value /= amount
    unit = groups.get("unit") or None

    subject: dict[str, str] = {}
    if claim_type == "CURRENCY_RATE":
        base = (groups.get("base") or "").upper()
        quote = (groups.get("quote") or "").upper()
        if base and quote:
            subject["pair"] = f"{base}/{quote}"
            subject["base"] = base
            subject["quote"] = quote
    elif claim_type == "CRYPTO_PRICE":
        subject["asset"] = (groups.get("asset") or "").lower()
    elif claim_type == "STOCK_PRICE":
        subject["symbol"] = (groups.get("symbol") or "").upper()
    elif claim_type == "COMMODITY_PRICE":
        subject["commodity"] = (groups.get("commodity") or "").lower()
    elif claim_type == "WEATHER":
        subject["city"] = (groups.get("city") or "").strip()
        if unit:
            subject["unit"] = unit.upper()
    elif claim_type == "STATIC_FACT":
        subject["country"] = (groups.get("country") or "").strip()

    raw_value_text: str | None = None
    if claim_type == "STATIC_FACT":
        raw_value_text = (groups.get("capital") or "").strip() or None

    return DetectedClaim(
        claim_id=claim_id,
        text=text_snippet,
        span=span,
        claim_type=claim_type,
        raw_value=raw_value,
        raw_value_text=raw_value_text,
        subject=subject,
        unit=unit,
    )
