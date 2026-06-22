"""Escalation Agent: detect risk, conflict, dissatisfaction, complaints."""

from __future__ import annotations

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.extraction.priority_classifier import classify_priority
from app.agent_brain.extraction.signals import find_signals
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import Entities, Recommendation
from app.insights.core._math import clamp

_ANGER = [
    "upset",
    "angry",
    "furious",
    "frustrated",
    "annoyed",
    "disappointed",
    "unacceptable",
    "ridiculous",
    "terrible",
]
_COMPLAINT = ["refund", "complaint", "complain", "delayed", "delay", "not working", "broken", "still waiting"]
_RISK = ["legal", "lawyer", "compliance", "sue", "escalate", "manager", "supervisor", "cancel my", "cancel the"]


class EscalationAgent(BaseAgent):
    agent_type = "ESCALATION_AGENT"
    action_type = "SUPPORT"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        recommendations: list[Recommendation] = []
        # Session-level context booster (the agent consumes insights, not just
        # raw text): negative overall sentiment or any upstream escalation flag.
        session_negative = (context.insights.sentiment or "").lower() == "negative" or bool(
            context.insights.escalation_flags
        )

        for segment in context.transcript:
            text = (segment.text or "").strip()
            if not text:
                continue

            anger = find_signals(text, _ANGER)
            complaint = find_signals(text, _COMPLAINT)
            risk = find_signals(text, _RISK)
            if not (anger or complaint or risk):
                continue

            reasons = []
            if anger:
                reasons.append("negative sentiment")
            if complaint:
                reasons.append("complaint/delay")
            if risk:
                reasons.append("explicit risk/escalation cue")
            risk_reason = ", ".join(reasons).capitalize() + " detected."

            subject = self._subject(text)
            recommendations.append(
                Recommendation(
                    agent_type=self.agent_type,
                    action_type=self.action_type,
                    title=self._title(text, subject),
                    description=f"Potential escalation: {risk_reason}",
                    priority=classify_priority(text, base="HIGH"),
                    confidence=clamp(
                        0.55
                        + (0.15 if anger else 0.0)
                        + (0.15 if (complaint or risk) else 0.0)
                        + (0.1 if session_negative else 0.0)
                    ),
                    source=self._source(segment),
                    entities=Entities(topic=subject, customer_name=None),
                    suggested_payload={"ticketTitle": self._ticket_title(text, subject), "riskReason": risk_reason},
                    explanation="Dissatisfaction / risk language detected in the conversation.",
                )
            )

        return recommendations

    @staticmethod
    def _subject(text: str) -> str:
        t = text.lower()
        if "refund" in t:
            return "refund"
        if "payment" in t or "billing" in t or "invoice" in t:
            return "billing"
        return "service"

    @staticmethod
    def _title(text: str, subject: str) -> str:
        qualifier = "delayed " if "delay" in text.lower() else ""
        return f"Escalate {qualifier}{subject} issue"

    @staticmethod
    def _ticket_title(text: str, subject: str) -> str:
        qualifier = "Delayed " if "delay" in text.lower() else ""
        return f"{qualifier}{subject} escalation".strip().capitalize()
