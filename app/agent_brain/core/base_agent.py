"""BaseAgent: contract every detection agent implements."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment
from app.agent_brain.models.enums import ActionType, AgentType
from app.agent_brain.models.recommendation import Recommendation, Source


class BaseAgent(ABC):
    """A rule-based agent that scans a conversation for one action family.

    Subclasses set `agent_type` / `action_type` and implement `detect`,
    returning zero or more recommendations. Agents are pure and stateless:
    same context in, same recommendations out. They never raise for normal
    "no match" cases — they return an empty list. (Genuine faults are
    isolated by the agent runner so one agent cannot sink the others.)
    """

    agent_type: AgentType
    action_type: ActionType

    @abstractmethod
    def detect(self, context: AgentContext) -> list[Recommendation]:
        """Return recommendations derived from `context` (possibly empty)."""

    @staticmethod
    def _source(segment: TranscriptSegment) -> Source:
        """Build the evidence Source for a segment (caller ensures non-empty text)."""
        return Source(
            segment_id=segment.segment_id,
            speaker_id=segment.speaker_id,
            speaker_label=segment.speaker_label,
            start_time=segment.start_time,
            end_time=segment.end_time,
            text=segment.text,
        )
