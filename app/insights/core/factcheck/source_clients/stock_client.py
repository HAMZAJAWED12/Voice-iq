"""Stock-price source client (Alpha Vantage GLOBAL_QUOTE)."""

from __future__ import annotations

from typing import ClassVar

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence


class StockClient(BaseSourceClient):
    """Resolve STOCK_PRICE claims via Alpha Vantage. Requires an API key.

    Note: free tier is 25 calls/day. Engine cache (commit 3+) and Postman
    test plan account for this hard limit.
    """

    name: ClassVar[str] = "alphavantage"
    BASE_URL: ClassVar[str] = "https://www.alphavantage.co/query"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_sec: float = 5.0,
        client=None,
    ) -> None:
        super().__init__(timeout_sec=timeout_sec, client=client)
        self._api_key = (api_key or "").strip()

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        if claim.claim_type != "STOCK_PRICE":
            return None
        if not self._api_key:
            return None

        symbol = claim.subject.get("symbol")
        if not symbol:
            return None

        payload = self._get_json(
            self.BASE_URL,
            params={
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": self._api_key,
            },
        )
        if not payload:
            return None

        # Alpha Vantage returns the soft-rate-limit message under "Note".
        # Treat that as SOURCE_UNAVAILABLE rather than a numeric quote.
        if "Note" in payload or "Information" in payload:
            return None

        quote = payload.get("Global Quote") or {}
        price_raw = quote.get("05. price")
        if price_raw in (None, ""):
            return None
        try:
            value = float(price_raw)
        except (TypeError, ValueError):
            return None

        return Evidence(
            source=self.name,
            value=value,
            unit="USD",
            raw={"symbol": symbol, "price": value},
        )
