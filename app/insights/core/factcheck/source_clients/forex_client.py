"""Currency-rate source client (exchangerate.host).

exchangerate.host now rejects every call without an access key (HTTP 200,
``{"success": false, "error": {"code": 101, "type": "missing_access_key"}}``;
owner-verified 2026-10-11) and this client sends none, so CURRENCY_RATE
claims end as SOURCE_UNAVAILABLE. Which provider to use instead is an open
decision (see DOCS/FACT-CHECK-AGENT-STATUS.md); until then the client names
the provider's error instead of failing silently.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
    SourceUnavailable,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence
from app.utils.logger import logger

# Provider error types worth a plain-language reason. Anything else is
# reported by its type token.
_ERROR_REASONS: dict[str, str] = {
    "missing_access_key": "provider requires an access key",
    "invalid_access_key": "provider rejected the access key",
    "usage_limit_reached": "provider usage limit reached",
    "base_currency_access_restricted": "provider plan does not allow this request",
    "function_access_restricted": "provider plan does not allow this request",
}
_ERROR_TYPE_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,63}")


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
        if payload.get("success") is False or isinstance(payload.get("error"), dict):
            self._raise_provider_error(payload.get("error"))
        if "rates" not in payload:
            logger.warning("%s: reply has no rates", self.name)
            raise SourceUnavailable(f"{self.name}: provider reply had no rates.")

        rates = self._as_dict(payload.get("rates"))
        value = self._finite_float(rates.get(quote))
        if value is None:
            return None

        return Evidence(
            source=self.name,
            value=value,
            unit=quote,
            raw={"base": base, "quote": quote, "rate": value},
        )

    def _raise_provider_error(self, error: Any) -> None:
        """Log the provider's error code and type (nothing else) and raise."""
        error = self._as_dict(error)
        code = error.get("code")
        if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code < 100_000:
            code = None
        error_type = error.get("type")
        if not isinstance(error_type, str) or not _ERROR_TYPE_TOKEN.fullmatch(error_type):
            error_type = "unknown"
        logger.warning("%s: provider error code=%s type=%s", self.name, code, error_type)
        friendly = _ERROR_REASONS.get(error_type, "provider returned an error")
        raise SourceUnavailable(f"{self.name}: {friendly} ({error_type}).")
