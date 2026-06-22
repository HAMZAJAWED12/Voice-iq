"""HMAC-signed callback to the Java Action Layer (doc 7.2).

Disabled by default: when no callback URL/secret is configured, send() is a
no-op that returns False. Java is responsible for validating the signature
and the session ownership.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx

from app.agent_brain.config.settings import AgentBrainSettings
from app.agent_brain.models.recommendation import CallbackPayload
from app.utils.logger import logger

_SERVICE_NAME = "python-agent-brain"


class JavaCallbackClient:
    def __init__(self, settings: AgentBrainSettings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport  # injected MockTransport in tests

    def send(self, payload: CallbackPayload, *, trace_id: str) -> bool:
        """POST the payload to Java with an HMAC signature. Returns True if sent."""
        if not self._settings.callback_enabled:
            logger.info("agent_brain: Java callback disabled (no URL/secret); skipping send")
            return False

        body = payload.model_dump_json(by_alias=True).encode("utf-8")
        signature = hmac.new(self._settings.callback_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        headers = {
            "Content-Type": "application/json",
            "X-VoiceIQ-Service": _SERVICE_NAME,
            "X-VoiceIQ-Signature": signature,
            "X-VoiceIQ-Trace-Id": trace_id,
        }

        with httpx.Client(timeout=self._settings.callback_timeout_sec, transport=self._transport) as client:
            response = client.post(self._settings.callback_url, content=body, headers=headers)
        response.raise_for_status()
        return True
