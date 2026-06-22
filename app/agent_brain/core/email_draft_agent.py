"""Email Draft Agent: detect a needed email and prepare a draft payload.

Important: the brain only DRAFTS. Java must not send anything until the
user approves (doc 5.3).
"""

from __future__ import annotations

import re

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.extraction.datetime_extractor import extract_date_phrase
from app.agent_brain.extraction.signals import find_signals
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import Recommendation
from app.insights.core._math import clamp

_EMAIL_SIGNALS = [
    "send me",
    "email me",
    "send over",
    "send across",
    "send the",
    "send a",
    "share the",
    "forward the",
    "forward details",
    "send assessment",
    "send quotation",
    "send proposal",
    "email the",
    "send a copy",
]

# Capture the object of the send/share request ("the pricing proposal").
_OBJECT = re.compile(
    r"\b(?:send|share|forward|email)(?:\s+(?:me|us|over|across))?\s+(?:the|a|an|my|your)?\s*"
    r"([A-Za-z][A-Za-z ]*?)(?:\s+(?:by|before|to|with|on|via)\b|[.?!,]|$)",
    re.IGNORECASE,
)


class EmailDraftAgent(BaseAgent):
    agent_type = "EMAIL_DRAFT_AGENT"
    action_type = "EMAIL"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        recommendations: list[Recommendation] = []

        for segment in context.transcript:
            text = (segment.text or "").strip()
            if not text or not find_signals(text, _EMAIL_SIGNALS):
                continue

            obj = self._object(text)
            subject = obj.title()
            deadline = extract_date_phrase(text)
            body_draft = f"Hi,\n\nAs discussed, please find the details for {obj} below.\n\nRegards,"

            recommendations.append(
                Recommendation(
                    agent_type=self.agent_type,
                    action_type=self.action_type,
                    title=f"Send {obj}",
                    description=f"A request to send {obj} was detected"
                    + (f" with deadline {deadline}." if deadline else "."),
                    priority="HIGH",
                    confidence=clamp(0.6 + (0.18 if obj != _FALLBACK_OBJECT else 0.0) + (0.12 if deadline else 0.0)),
                    source=self._source(segment),
                    suggested_payload={
                        "subject": subject,
                        "bodyDraft": body_draft,
                        "deadlineText": deadline,
                    },
                    explanation="An explicit request to send/share a document was detected.",
                )
            )

        return recommendations

    @staticmethod
    def _object(text: str) -> str:
        match = _OBJECT.search(text)
        if match:
            obj = match.group(1).strip()
            # Guard against the regex grabbing a trailing pronoun/adverb
            # ("send over", "email me") instead of a real document object.
            if obj and obj.lower() not in _STOP_OBJECTS:
                return obj
        return _FALLBACK_OBJECT


_FALLBACK_OBJECT = "the requested document"
_STOP_OBJECTS = {"me", "us", "you", "over", "across", "it", "them"}
