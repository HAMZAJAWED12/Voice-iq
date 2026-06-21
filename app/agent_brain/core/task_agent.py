"""Task Agent: detect TODOs, commitments, and assignments."""

from __future__ import annotations

import re

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.extraction.assignee_extractor import extract_assignee
from app.agent_brain.extraction.datetime_extractor import extract_date_phrase
from app.agent_brain.extraction.priority_classifier import classify_priority
from app.agent_brain.extraction.signals import find_signals
from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.models.recommendation import Entities, Recommendation
from app.insights.core._math import clamp

# Commitment / assignment markers. Kept deliberately conservative for Phase 1;
# cross-agent overlap (e.g. with the email/follow-up agents) is resolved later
# by deduplication.
_TASK_SIGNALS = [
    "will",
    "shall",
    "need to",
    "needs to",
    "has to",
    "have to",
    "please",
    "assigned to",
    "make sure",
    "don't forget",
    "follow up on",
    "action item",
    "to-do",
    "todo",
]

# Strip a leading "<subject> will/should/needs to ..." so the title becomes the
# action clause ("Prepare the report by Friday").
_LEADING_SUBJECT = re.compile(
    r"^.*?\b(?:will|shall|should|must|needs to|has to|have to|is going to)\s+",
    re.IGNORECASE,
)


class TaskAgent(BaseAgent):
    agent_type = "TASK_AGENT"
    action_type = "TASK"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        recommendations: list[Recommendation] = []

        for segment in context.transcript:
            text = (segment.text or "").strip()
            if not text:
                continue

            hits = find_signals(text, _TASK_SIGNALS)
            if not hits:
                continue

            assignee = extract_assignee(text)
            deadline = extract_date_phrase(text)
            title = self._title(text, has_assignee=assignee is not None)
            description = (
                f"{assignee} was assigned to {title[0].lower() + title[1:]}."
                if assignee
                else f"A task was identified: {title}."
            )

            recommendations.append(
                Recommendation(
                    agent_type=self.agent_type,
                    action_type=self.action_type,
                    title=title,
                    description=description,
                    priority=classify_priority(text),
                    confidence=self._confidence(assignee=assignee, deadline=deadline),
                    source=self._source(segment),
                    entities=Entities(assignee=assignee, deadline_text=deadline),
                    explanation="Detected a commitment/assignment cue with an actionable clause.",
                )
            )

        return recommendations

    @staticmethod
    def _title(text: str, *, has_assignee: bool) -> str:
        clause = _LEADING_SUBJECT.sub("", text, count=1) if has_assignee else text
        clause = clause.strip().rstrip(".!?").strip()
        clause = clause[:80].strip()
        if not clause:
            return "Follow-up task"
        return clause[0].upper() + clause[1:]

    @staticmethod
    def _confidence(*, assignee: str | None, deadline: str | None) -> float:
        # Provisional, entity-completeness based. The confidence module
        # (Sprint 6 #8) refines this with context factors (ASR confidence,
        # repetition, fact-check risk) in the runner.
        return clamp(0.55 + (0.2 if assignee else 0.0) + (0.15 if deadline else 0.0))
