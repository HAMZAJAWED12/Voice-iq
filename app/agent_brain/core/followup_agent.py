"""Follow-Up Meeting Agent: detect future meeting / call requirements."""

from __future__ import annotations

import re

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.extraction.datetime_extractor import extract_date_phrase, extract_time_phrase
from app.agent_brain.extraction.signals import find_signals
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import Recommendation
from app.insights.core._math import clamp

_FOLLOWUP_SIGNALS = [
    "let's meet",
    "let us meet",
    "meet again",
    "follow-up call",
    "follow up call",
    "follow-up meeting",
    "next meeting",
    "another call",
    "another session",
    "schedule another",
    "schedule a call",
    "schedule a follow",
    "let's have another",
    "talk again",
    "catch up",
    "reconnect",
    "sync up",
]

_DEFAULT_DURATION_MIN = 30
_DURATION_MIN = re.compile(r"\b(\d{1,3})\s*(?:minutes|minute|mins|min)\b", re.IGNORECASE)
_DURATION_HOUR = re.compile(r"\b(?:an?\s+hour|one\s+hour|1\s*hour)\b", re.IGNORECASE)


class FollowUpAgent(BaseAgent):
    agent_type = "FOLLOW_UP_AGENT"
    action_type = "CALENDAR"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        recommendations: list[Recommendation] = []

        for segment in context.transcript:
            text = (segment.text or "").strip()
            if not text or not find_signals(text, _FOLLOWUP_SIGNALS):
                continue

            date_text = extract_date_phrase(text)
            time_text = extract_time_phrase(text)
            duration = self._duration_minutes(text)

            when = " ".join(part for part in (date_text, f"at {time_text}" if time_text else None) if part)
            description = "The conversation indicates a follow-up call" + (f" {when}." if when else ".")

            payload: dict[str, object] = {
                "meetingTitle": "Follow-up Discussion",
                "dateText": date_text,
                "timeText": time_text,
                "durationMinutes": duration,
            }

            recommendations.append(
                Recommendation(
                    agent_type=self.agent_type,
                    action_type=self.action_type,
                    title="Schedule follow-up call",
                    description=description,
                    priority="HIGH",
                    confidence=self._confidence(date_text=date_text, time_text=time_text),
                    source=self._source(segment),
                    suggested_payload=payload,
                    explanation="A future meeting/call was explicitly requested.",
                )
            )

        return recommendations

    @staticmethod
    def _duration_minutes(text: str) -> int:
        match = _DURATION_MIN.search(text)
        if match:
            return int(match.group(1))
        if _DURATION_HOUR.search(text):
            return 60
        return _DEFAULT_DURATION_MIN

    @staticmethod
    def _confidence(*, date_text: str | None, time_text: str | None) -> float:
        return clamp(0.6 + (0.18 if date_text else 0.0) + (0.12 if time_text else 0.0))
