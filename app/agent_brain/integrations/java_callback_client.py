"""HMAC-signed callback to the Java Action Layer (doc 7.2).

Disabled by default: when no callback URL/secret is configured, send() is a
no-op that returns False. Java is responsible for validating the signature
and the session ownership.

Signature scheme (S7 hardening)
-------------------------------
Two signatures are sent so the Java side can migrate without downtime:

* ``X-VoiceIQ-Signature``    - HMAC-SHA256(secret, body).  **v1, legacy.**
  Unchanged, so existing Java validation keeps working.
* ``X-VoiceIQ-Signature-V2`` - HMAC-SHA256(secret, f"{timestamp}.{body}")
  together with ``X-VoiceIQ-Timestamp`` (unix seconds).

v1 alone is replayable: an attacker who captures a request can resend it
forever, because nothing in the signed material expires. v2 binds the
signature to a timestamp, so Java can reject anything outside a freshness
window (a few minutes) and de-duplicate on the trace id.

**Migration:** Java should switch to validating v2 (checking the timestamp
skew), after which v1 can be dropped from this client. Until Java migrates,
both headers are present and v1 remains authoritative.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlparse

import httpx

from app.agent_brain.config.settings import AgentBrainSettings
from app.agent_brain.models.recommendation import CallbackPayload
from app.utils.logger import logger

_SERVICE_NAME = "python-agent-brain"

# Hosts for which plaintext http:// is tolerated (local development).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "host.docker.internal"})


def _is_transport_secure(url: str) -> bool:
    """True when the callback URL is https, or http to a local dev host.

    The payload carries conversation-derived recommendations and is
    authenticated by a shared secret; sending it in plaintext over a
    routable network would expose both.
    """
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and (parsed.hostname or "") in _LOCAL_HOSTS:
        return True
    return False


class JavaCallbackClient:
    def __init__(self, settings: AgentBrainSettings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport  # injected MockTransport in tests

    def send(self, payload: CallbackPayload, *, trace_id: str) -> bool:
        """POST the payload to Java with an HMAC signature. Returns True if sent."""
        if not self._settings.callback_enabled:
            logger.info("agent_brain: Java callback disabled (no URL/secret); skipping send")
            return False

        if not _is_transport_secure(self._settings.callback_url):
            logger.error(
                "agent_brain: refusing to send callback over an insecure transport "
                "(callback_url must be https, or http to a local host); skipping send"
            )
            return False

        secret = self._settings.callback_secret.encode("utf-8")
        body = payload.model_dump_json(by_alias=True).encode("utf-8")
        timestamp = str(int(time.time()))

        # v1 (legacy, body only) + v2 (timestamp-bound, replay-resistant).
        # See the module docstring for the migration plan.
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        signed_v2 = timestamp.encode("utf-8") + b"." + body
        signature_v2 = hmac.new(secret, signed_v2, hashlib.sha256).hexdigest()

        headers = {
            "Content-Type": "application/json",
            "X-VoiceIQ-Service": _SERVICE_NAME,
            "X-VoiceIQ-Signature": signature,
            "X-VoiceIQ-Signature-V2": signature_v2,
            "X-VoiceIQ-Timestamp": timestamp,
            "X-VoiceIQ-Trace-Id": trace_id,
        }

        with httpx.Client(timeout=self._settings.callback_timeout_sec, transport=self._transport) as client:
            response = client.post(self._settings.callback_url, content=body, headers=headers)
        response.raise_for_status()
        return True
