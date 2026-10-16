"""Abstract base class for fact-check source clients.

Design contract:
  * One client per external data source.
  * `fetch(claim)` is the only public method, returns `Evidence | None`.
  * Any failure mode (timeout, HTTP non-2xx, missing field, missing key,
    parse error) MUST be caught locally and converted to `None`.
  * No exceptions propagate out of a client - the engine treats `None` as
    `SOURCE_UNAVAILABLE` and never crashes the request. The one allowed
    exception is `SourceUnavailable`, raised to say *why* there is no
    evidence (e.g. the provider demands an access key); the engine maps it
    to `SOURCE_UNAVAILABLE` with that reason.
  * Each client owns a short, single-attempt retry policy. Persistent
    failures degrade gracefully.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from typing import Any, ClassVar

import httpx

from app.insights.models.factcheck_models import DetectedClaim, Evidence
from app.utils.logger import logger

# Polite, identifiable User-Agent. Required by Wikimedia (otherwise 403)
# and a courtesy for every other source. Includes a contact URL per
# Wikipedia's API etiquette guidelines.
_DEFAULT_UA: str = "VoiceIQ-FactCheck/1.0 " "(+https://github.com/HAMZAJAWED12/Voice-iq) " "httpx"


# json.loads hooks: a provider reply carrying a non-finite number (the NaN /
# Infinity tokens Python's json accepts, or a float literal like 1e999 that
# overflows to inf) is rejected as invalid JSON instead of reaching a verdict.
def _reject_non_finite_constant(token: str) -> float:
    raise ValueError(f"non-finite JSON constant: {token}")


def _finite_json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    return value


class SourceUnavailable(Exception):
    """A source could not answer, for a reason worth showing the caller.

    ``reason`` becomes the fact-check result's reason. Keep it short, and
    never put URLs, keys, response bodies or claim text in it.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BaseSourceClient(ABC):
    """Common scaffolding shared by every source client."""

    #: Stable identifier surfaced in `Evidence.source` and logs.
    name: ClassVar[str] = "base"

    def __init__(
        self,
        *,
        timeout_sec: float = 5.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._timeout_sec = timeout_sec
        # Allow tests to inject a mocked transport via httpx.Client(...)
        self._client = client

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    @abstractmethod
    def fetch(self, claim: DetectedClaim) -> Evidence | None:
        """Resolve `claim` against the upstream source.

        Concrete implementations MUST NOT raise, except
        ``SourceUnavailable`` to give the reason. Returning ``None`` signals
        SOURCE_UNAVAILABLE to the engine.
        """

    # ------------------------------------------------------------------ #
    # Helpers for subclasses                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _as_dict(value: Any) -> dict:
        """`value` if it is a JSON object, else an empty dict (for nested lookups)."""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        """`float(value)` if that is a finite number, else None."""
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    def _get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
        retries: int = 1,
    ) -> dict | None:
        """GET `url`, return parsed JSON dict, or `None` on any error.

        Performs at most ``retries + 1`` attempts. Retries are *only*
        triggered for transient failures (timeout, connection error, 5xx).
        4xx responses are considered terminal and not retried. A reply that
        cannot be decoded, is not valid JSON, contains a non-finite number,
        or is not a JSON object also yields `None` (no retry).
        """
        attempts = max(1, retries + 1)
        client_owned_here = self._client is None
        client = self._client or httpx.Client(
            timeout=self._timeout_sec,
            headers={"User-Agent": _DEFAULT_UA, "Accept": "application/json"},
        )
        try:
            for attempt in range(attempts):
                try:
                    response = client.get(url, params=params)
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    logger.warning(
                        "%s: transport error on attempt %d/%d (%s)",
                        self.name,
                        attempt + 1,
                        attempts,
                        type(exc).__name__,
                    )
                    if attempt == attempts - 1:
                        return None
                    continue
                except httpx.HTTPError as exc:
                    # Not transient (e.g. a body that fails to decode): no retry.
                    logger.warning("%s: unusable response (%s)", self.name, type(exc).__name__)
                    return None

                status = response.status_code
                if 500 <= status < 600 and attempt < attempts - 1:
                    logger.warning(
                        "%s: server error %d on attempt %d/%d, retrying",
                        self.name,
                        status,
                        attempt + 1,
                        attempts,
                    )
                    continue
                if status >= 400:
                    logger.warning("%s: HTTP %d (terminal)", self.name, status)
                    return None
                try:
                    payload = response.json(
                        parse_constant=_reject_non_finite_constant,
                        parse_float=_finite_json_float,
                    )
                except (ValueError, RecursionError):
                    logger.warning("%s: invalid JSON in response", self.name)
                    return None
                if not isinstance(payload, dict):
                    logger.warning("%s: JSON reply is not an object (%s)", self.name, type(payload).__name__)
                    return None
                return payload
            return None
        finally:
            if client_owned_here:
                client.close()
