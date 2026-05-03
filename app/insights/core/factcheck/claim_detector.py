"""Claim detection.

Pure regex-based extractor. No LLM, no network. Each pattern is anchored on
both a *subject* (asset, currency, city, country) and a *value or assertion*
to keep false positives low.

Output is a list of `DetectedClaim` objects. Classification (mapping the
match to a normalized subject + unit) is handled by `ClaimClassifier`; this
module only finds claim *spans* and tags them with a tentative `claim_type`.
"""

from __future__ import annotations

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


# Keep patterns intentionally narrow. Each must require both a recognisable
# subject token AND an anchor (a number, "is", "=", "at"). Order matters:
# the first match wins for any given character span.
_PATTERNS: list[_ClaimPattern] = [
    # CURRENCY_RATE: e.g. "USD to PKR is 280", "1 USD = 280 PKR"
    _ClaimPattern(
        "CURRENCY_RATE",
        re.compile(
            r"\b(?P<base>USD|PKR|EUR|GBP|INR|AED|SAR|JPY|CNY|CAD|AUD)\s*"
            r"(?:to|/|=)\s*"
            r"(?P<quote>USD|PKR|EUR|GBP|INR|AED|SAR|JPY|CNY|CAD|AUD)"
            r"\s*(?:is|=|at|:)?\s*"
            r"(?P<value>\d+(?:[.,]\d+)?)",
            re.IGNORECASE,
        ),
    ),
    # CRYPTO_PRICE: e.g. "Bitcoin is at 95000 dollars", "BTC price 95000"
    # NOTE: multi-word verbs MUST precede single tokens in the alternation
    # so the regex engine prefers "is at" over "is" before falling through.
    _ClaimPattern(
        "CRYPTO_PRICE",
        re.compile(
            r"\b(?P<asset>bitcoin|btc|ethereum|eth|solana|sol|dogecoin|doge|"
            r"cardano|ada|ripple|xrp|litecoin|ltc)\b"
            r"\s+(?:trading\s+at|is\s+at|price\s+is|price|costs?|is|at|=)\s*"
            r"\$?\s*(?P<value>\d+(?:[.,]\d+)?)",
            re.IGNORECASE,
        ),
    ),
    # STOCK_PRICE: e.g. "AAPL price 175.20", "AAPL is at 175"
    _ClaimPattern(
        "STOCK_PRICE",
        re.compile(
            r"\b(?P<symbol>AAPL|TSLA|MSFT|GOOGL|GOOG|AMZN|META|NVDA|NFLX|"
            r"AMD|INTC|IBM|ORCL|CRM)\b"
            r"\s+(?:trading\s+at|is\s+at|price\s+is|price|stock|share|is|at|=)\s*"
            r"\$?\s*(?P<value>\d+(?:[.,]\d+)?)",
            re.IGNORECASE,
        ),
    ),
    # WEATHER: e.g. "temperature in Karachi is 32 C", "weather in London 18"
    _ClaimPattern(
        "WEATHER",
        re.compile(
            r"\b(?:temperature|temp|weather)\s+(?:in|at)\s+"
            r"(?P<city>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?)"
            r"\s*(?:is|=|:|at)?\s*"
            r"(?P<value>\d+(?:\.\d+)?)\s*"
            r"°?\s*(?P<unit>[CF])?\b",
        ),
    ),
    # COMMODITY_PRICE: e.g. "gold price 2400", "oil is at 80 dollars"
    _ClaimPattern(
        "COMMODITY_PRICE",
        re.compile(
            r"\b(?P<commodity>gold|silver|oil|brent|wti|copper|platinum|natural\s+gas)\b"
            r"\s+(?:trading\s+at|is\s+at|price\s+is|price|costs?|is|at|=)\s*"
            r"\$?\s*(?P<value>\d+(?:[.,]\d+)?)",
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


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #


class ClaimDetector:
    """Stateless detector: text in, list of `DetectedClaim` out.

    Detection is deterministic: given the same text, the same claims are
    produced in the same order. Spans are non-overlapping; on overlap the
    earlier match (by start offset, then by pattern declaration order) wins.
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
                raw_matches.append((m.start(), m.end(), rule.claim_type, m))

        # Resolve overlaps: prefer earliest start, then earliest declared type.
        raw_matches.sort(key=lambda item: (item[0], item[1]))
        kept: list[tuple[int, int, ClaimType, re.Match[str]]] = []
        last_end = -1
        for start, end, ctype, match in raw_matches:
            if start < last_end:
                continue  # overlap - skip
            kept.append((start, end, ctype, match))
            last_end = end

        claims: list[DetectedClaim] = []
        for idx, (start, end, ctype, match) in enumerate(kept):
            claims.append(
                _build_detected_claim(
                    claim_id=f"claim_{idx + 1}",
                    text_snippet=scan_text[start:end],
                    span=ClaimSpan(start=start, end=end),
                    claim_type=ctype,
                    match=match,
                )
            )
        return claims


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _to_float(value: str) -> float | None:
    """Parse '95,000.50' or '95000.50' safely. None on failure."""
    try:
        return float(value.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _build_detected_claim(
    *,
    claim_id: str,
    text_snippet: str,
    span: ClaimSpan,
    claim_type: ClaimType,
    match: re.Match[str],
) -> DetectedClaim:
    """Convert a regex match into a `DetectedClaim`.

    Subject extraction is *minimal* here — the classifier normalizes labels
    further (e.g. "btc" → "BTC"). This keeps the detector simple and the
    classifier the single source of truth for normalization.
    """
    groups = match.groupdict()
    raw_value = _to_float(groups.get("value", "")) if "value" in groups else None
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
