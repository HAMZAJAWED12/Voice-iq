"""Crypto-price source client (CoinGecko, no API key required for free tier)."""

from __future__ import annotations

from typing import ClassVar

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence

# Symbol → CoinGecko id mapping. Kept tight (only assets the detector knows).
_SYMBOL_TO_ID: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "XRP": "ripple",
    "LTC": "litecoin",
}


class CoinGeckoClient(BaseSourceClient):
    """Resolve CRYPTO_PRICE claims via the CoinGecko public price endpoint."""

    name: ClassVar[str] = "coingecko"
    BASE_URL: ClassVar[str] = "https://api.coingecko.com/api/v3/simple/price"

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        if claim.claim_type != "CRYPTO_PRICE":
            return None
        symbol = (claim.subject.get("asset") or "").upper()
        gecko_id = _SYMBOL_TO_ID.get(symbol)
        if gecko_id is None:
            return None

        payload = self._get_json(
            self.BASE_URL,
            params={"ids": gecko_id, "vs_currencies": "usd"},
        )
        if not payload:
            return None

        asset_block = payload.get(gecko_id) or {}
        price = asset_block.get("usd")
        if price is None:
            return None
        try:
            value = float(price)
        except (TypeError, ValueError):
            return None

        return Evidence(
            source=self.name,
            value=value,
            unit="USD",
            raw={"asset": symbol, "id": gecko_id, "usd": value},
        )
