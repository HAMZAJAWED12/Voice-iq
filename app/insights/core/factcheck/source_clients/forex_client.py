"""Currency-rate source client (exchangerate.host, no API key required)."""

from __future__ import annotations

from typing import ClassVar

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence


class ForexClient(BaseSourceClient):
    """Resolve CURRENCY_RATE claims via exchangerate.host."""

    name: ClassVar[str] = "exchangerate.host"
    BASE_URL: ClassVar[str] = "https://api.exchangerate.host/latest"

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        if claim.claim_type != "CURRENCY_RATE":
            return None
        base = claim.subject.get("base")
        quote = claim.subject.get("quote")
        if not base or not quote:
            return None

        payload = self._get_json(
            self.BASE_URL,
            params={"base": base, "symbols": quote},
        )
        if not payload:
            return None

        rates = payload.get("rates") or {}
        rate = rates.get(quote)
        if rate is None:
            return None
        try:
            value = float(rate)
        except (TypeError, ValueError):
            return None

        return Evidence(
            source=self.name,
            value=value,
            unit=quote,
            raw={"base": base, "quote": quote, "rate": value},
        )
