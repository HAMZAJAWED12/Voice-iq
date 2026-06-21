"""Agent Brain enums (as Literal type aliases, matching repo convention)."""

from __future__ import annotations

from typing import Literal

AgentType = Literal[
    "TASK_AGENT",
    "FOLLOW_UP_AGENT",
    "EMAIL_DRAFT_AGENT",
    "ESCALATION_AGENT",
    "FACT_CHECK_REVIEW_AGENT",
]

ActionType = Literal[
    "TASK",
    "CALENDAR",
    "EMAIL",
    "SUPPORT",
    "FACT_CHECK",
    "NOTIFICATION",
    "CRM",
    "CUSTOM",
]

Priority = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Language of the conversation. Phase 1 fills only English rules; ur / ar /
# mixed are accepted and preserved but not yet specially handled.
LanguageCode = Literal["en", "ur", "ar", "mixed"]

# Fact-check status as it arrives in the Agent Brain *input contract*
# (distinct from the internal fact-check engine's Verdict literals). The
# pipeline adapter maps internal verdicts onto these.
FactCheckStatus = Literal[
    "TRUE",
    "FALSE",
    "PARTIALLY_TRUE",
    "UNVERIFIED",
    "NEEDS_REVIEW",
    "SOURCE_UNAVAILABLE",
]

# Recommendation lifecycle is owned by Java. Python only ever produces the
# implicit "GENERATED" state; this alias is here for reference/documentation
# and is intentionally not set on Python-side models.
RecommendationStatus = Literal[
    "GENERATED",
    "PENDING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "EXECUTING",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]
