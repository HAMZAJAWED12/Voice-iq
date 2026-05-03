"""Abstract base class for fact-check source clients.

Design contract:
  * One client per external data source.
  * `fetch(claim)` is the only public method, returns `Evidence | None`.
  * Any failure mode (timeout, HTTP non-2xx, missing field, missing key,
    parse error) MUST be caught locally and converted to `None`.
  * No exceptions propagate out of a client - the engine treats `None` as
    `SOURCE_UNAVAILABLE` and never crashes the request.
  * Each client owns a short, single-attempt retry policy. Persistent
    failures degrade gracefully.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

import httpx

from app.insights.models.factcheck_models import DetectedClaim, Evidence
from app.utils.logger import logger

# Polite, identifiable User-Agent. Required by Wikimedia (otherwise 403)
# and a courtesy for every other source. Includes a contact URL per
# Wikipedia's API etiquette guidelines.
_DEFAULT_UA: str = "VoiceIQ-FactCheck/1.0 " "(+https://github.com/HAMZAJAWED12/Voice-iq) " "httpx"


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

        Concrete implementations MUST NOT raise. Returning ``None``
        signals SOURCE_UNAVAILABLE to the engine.
        """

    # ------------------------------------------------------------------ #
    # Helpers for subclasses                                             #
    # ------------------------------------------------------------------ #

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
        4xx responses are considered terminal and not retried.
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
                    return response.json()
                except ValueError:
                    logger.warning("%s: invalid JSON in response", self.name)
                    return None
            return None
        finally:
            if client_owned_here:
                client.close()
