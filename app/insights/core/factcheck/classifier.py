"""Claim classification.

The detector emits a tentative `claim_type` plus raw subject groups. The
classifier:

  * Normalises subject metadata into the canonical form expected by source
    clients (e.g. "btc" → "BTC", "Karachi" → "Karachi" with default unit C,
    currency pairs always uppercased).
  * Drops claims that lack the minimum metadata required for verification
    (e.g. a CRYPTO_PRICE claim with no asset, a WEATHER claim with no city).

Output is a *cleaned* list of `DetectedClaim` objects ready for the source
clients. Classification is deterministic and side-effect free.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.insights.models.factcheck_models import ClaimType, DetectedClaim


# --------------------------------------------------------------------------- #
# Lookup tables                                                               #
# --------------------------------------------------------------------------- #

# Minimal alias map for crypto. Source clients (e.g. CoinGecko) want the
# canonical short symbol — we normalize once here.
_CRYPTO_ALIAS: Dict[str, str] = {
    "bitcoin": "BTC",
    "btc": "BTC",
    "ethereum": "ETH",
    "eth": "ETH",
    "solana": "SOL",
    "sol": "SOL",
    "dogecoin": "DOGE",
    "doge": "DOGE",
    "cardano": "ADA",
    "ada": "ADA",
    "ripple": "XRP",
    "xrp": "XRP",
    "litecoin": "LTC",
    "ltc": "LTC",
}

# Commodity normalization for the (future) commodity client.
_COMMODITY_ALIAS: Dict[str, str] = {
    "gold": "XAU",
    "silver": "XAG",
    "platinum": "XPT",
    "copper": "COPPER",
    "oil": "BRENT",
    "brent": "BRENT",
    "wti": "WTI",
    "natural gas": "NATGAS",
}

# Default temperature unit when the speaker did not specify one.
_DEFAULT_WEATHER_UNIT: str = "C"


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #

class ClaimClassifier:
    """Stateless normaliser for `DetectedClaim` instances."""

    @classmethod
    def classify(cls, claims: List[DetectedClaim]) -> List[DetectedClaim]:
        """Return a new list of normalized, verifiable claims.

        Claims that cannot be normalized (missing required metadata) are
        dropped silently; they have no fact-check evidence to gather. The
        original list is not mutated.
        """
        cleaned: List[DetectedClaim] = []
        next_id = 1
        for claim in claims:
            normalized = cls._normalize_one(claim)
            if normalized is None:
                continue
            # Re-id sequentially so consumers see a contiguous claim_1..N.
            renumbered = normalized.model_copy(update={"claim_id": f"claim_{next_id}"})
            cleaned.append(renumbered)
            next_id += 1
        return cleaned

    # ------------------------------------------------------------------ #
    # Internal helpers                                                   #
    # ------------------------------------------------------------------ #

    @classmethod
    def _normalize_one(cls, claim: DetectedClaim) -> Optional[DetectedClaim]:
        """Return a normalized copy, or None if the claim is unverifiable."""
        ctype: ClaimType = claim.claim_type
        subject = dict(claim.subject)

        if ctype == "CURRENCY_RATE":
            base = subject.get("base", "").upper()
            quote = subject.get("quote", "").upper()
            if not base or not quote or base == quote or claim.raw_value is None:
                return None
            subject["base"] = base
            subject["quote"] = quote
            subject["pair"] = f"{base}/{quote}"

        elif ctype == "CRYPTO_PRICE":
            asset_raw = (subject.get("asset") or "").lower()
            asset = _CRYPTO_ALIAS.get(asset_raw)
            if asset is None or claim.raw_value is None:
                return None
            subject["asset"] = asset

        elif ctype == "STOCK_PRICE":
            symbol = (subject.get("symbol") or "").upper()
            if not symbol or claim.raw_value is None:
                return None
            subject["symbol"] = symbol

        elif ctype == "COMMODITY_PRICE":
            commodity_raw = (subject.get("commodity") or "").lower()
            commodity = _COMMODITY_ALIAS.get(commodity_raw)
            if commodity is None or claim.raw_value is None:
                return None
            subject["commodity"] = commodity

        elif ctype == "WEATHER":
            city = (subject.get("city") or "").strip()
            if not city or claim.raw_value is None:
                return None
            subject["city"] = city
            unit = (subject.get("unit") or claim.unit or _DEFAULT_WEATHER_UNIT).upper()
            if unit not in {"C", "F"}:
                unit = _DEFAULT_WEATHER_UNIT
            subject["unit"] = unit

        elif ctype == "STATIC_FACT":
            country = (subject.get("country") or "").strip()
            if not country or not claim.raw_value_text:
                return None
            subject["country"] = country

        else:  # pragma: no cover - exhaustive enum guard
            return None

        update = {"subject": subject}
        if ctype == "WEATHER":
            update["unit"] = subject.get("unit")
        return claim.model_copy(update=update)
