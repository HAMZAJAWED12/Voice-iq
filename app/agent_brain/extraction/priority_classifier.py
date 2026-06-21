"""Rule-based priority classification (Phase 1)."""

from __future__ import annotations

from app.agent_brain.models.enums import Priority

_CRITICAL_TERMS = ("urgent", "asap", "immediately", "critical", "emergency", "right away", "right now")
_HIGH_TERMS = ("important", "high priority", "as soon as possible", "today", "deadline", "end of day", "eod")


def classify_priority(text: str, *, base: Priority = "MEDIUM") -> Priority:
    """Escalate `base` priority when urgency language is present.

    Returns CRITICAL/HIGH when matching terms appear, otherwise `base`
    (so callers like the Escalation agent can pass base="HIGH").
    """
    lowered = (text or "").lower()
    if any(term in lowered for term in _CRITICAL_TERMS):
        return "CRITICAL"
    if any(term in lowered for term in _HIGH_TERMS):
        return "HIGH"
    return base
