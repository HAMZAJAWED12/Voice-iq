"""Currency-rate source client (ExchangeRate-API open access, open.er-api.com).

Keyless. One request per base currency returns that currency's rate against
every other; the quote currency is picked out locally, so only the base code
leaves the host. The provider updates rates once a day and asks callers not to
fetch more often than they need, so rates are cached per base for an hour.

The provider's terms require attribution: every Evidence this client returns
carries it in `raw["attribution"]`.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, ClassVar

import httpx

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
    SourceUnavailable,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence
from app.utils.logger import logger

ATTRIBUTION = "Rates By Exchange Rate API (https://www.exchangerate-api.com)"

# Provider error types worth a plain-language reason. Anything else is
# reported by its type token.
_ERROR_REASONS: dict[str, str] = {
    "unsupported-code": "provider does not support this currency",
    "malformed-request": "provider rejected the request",
    "quota-reached": "provider usage limit reached",
}
_ERROR_TYPE_TOKEN = re.compile(r"[a-z][a-z0-9-]{0,63}")
_CURRENCY_CODE = re.compile(r"[A-Z]{3}")


class ForexClient(BaseSourceClient):
    """Resolve CURRENCY_RATE claims via open.er-api.com."""

    name: ClassVar[str] = "open.er-api.com"
    BASE_URL: ClassVar[str] = "https://open.er-api.com/v6/latest/"
    CACHE_TTL_SEC: ClassVar[float] = 3600.0

    def __init__(self, *, timeout_sec: float = 5.0, client: httpx.Client | None = None) -> None:
        super().__init__(timeout_sec=timeout_sec, client=client)
        self._rates: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        if claim.claim_type != "CURRENCY_RATE":
            return None
        base = claim.subject.get("base")
        quote = claim.subject.get("quote")
        if not base or not quote or not _CURRENCY_CODE.fullmatch(base) or not _CURRENCY_CODE.fullmatch(quote):
            return None

        rates = self._rates_for(base)
        if rates is None:
            return None
        value = self._finite_float(rates.get(quote))
        if value is None:
            return None

        return Evidence(
            source=self.name,
            value=value,
            unit=quote,
            raw={"base": base, "quote": quote, "rate": value, "attribution": ATTRIBUTION},
        )

    def _rates_for(self, base: str) -> dict | None:
        """`base`'s rate table, from the hourly cache or one request."""
        now = time.monotonic()
        with self._lock:
            cached = self._rates.get(base)
            if cached is not None and cached[0] > now:
                return cached[1]

        payload = self._get_json(self.BASE_URL + base)
        if not payload:
            return None
        if payload.get("result") == "error":
            self._raise_provider_error(payload.get("error-type"))
        if "rates" not in payload:
            logger.warning("%s: reply has no rates", self.name)
            raise SourceUnavailable(f"{self.name}: provider reply had no rates.")

        rates = self._as_dict(payload.get("rates"))
        if rates:
            with self._lock:
                self._rates[base] = (now + self.CACHE_TTL_SEC, rates)
        return rates

    def _raise_provider_error(self, error_type: Any) -> None:
        """Log the provider's error type (nothing else) and raise."""
        if not isinstance(error_type, str) or not _ERROR_TYPE_TOKEN.fullmatch(error_type):
            error_type = "unknown"
        logger.warning("%s: provider error type=%s", self.name, error_type)
        friendly = _ERROR_REASONS.get(error_type, "provider returned an error")
        raise SourceUnavailable(f"{self.name}: {friendly} ({error_type}).")
