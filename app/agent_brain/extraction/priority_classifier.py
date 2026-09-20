"""Rule-based priority classification (Phase 1).

N4b-1 Phase 1: the term lists moved into language-keyed tables. The `"en"`
entries are the pre-N4b tuples, byte-identical. No other language ships
terms yet — decision **L1** (native-speaker vocabulary) gates that, and
until it lands every other language falls back to English.
"""

from __future__ import annotations

from app.agent_brain.extraction.language import vocabulary_for
from app.agent_brain.models.enums import Priority

# Add a language by adding a key. Do not edit the lookup below.
# TODO(L2): "mixed" routing is undecided — see DOCS/N4B-MULTILINGUAL-STRATEGY.md.
_CRITICAL_TERMS: dict[str, tuple[str, ...]] = {
    "en": ("urgent", "asap", "immediately", "critical", "emergency", "right away", "right now"),
}
_HIGH_TERMS: dict[str, tuple[str, ...]] = {
    "en": ("important", "high priority", "as soon as possible", "today", "deadline", "end of day", "eod"),
}


def classify_priority(text: str, *, base: Priority = "MEDIUM", language: str | None = None) -> Priority:
    """Escalate `base` priority when urgency language is present.

    Returns CRITICAL/HIGH when matching terms appear, otherwise `base`
    (so callers like the Escalation agent can pass base="HIGH").

    `language` selects the term table; an unknown or unpopulated language
    falls back to English, so behaviour is unchanged for every caller that
    does not pass it.
    """
    lowered = (text or "").lower()
    if any(term in lowered for term in vocabulary_for(_CRITICAL_TERMS, language)):
        return "CRITICAL"
    if any(term in lowered for term in vocabulary_for(_HIGH_TERMS, language)):
        return "HIGH"
    return base
