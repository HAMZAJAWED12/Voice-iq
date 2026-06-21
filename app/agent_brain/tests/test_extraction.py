"""Coverage for BaseAgent helper + extraction utilities."""

from __future__ import annotations

import pytest

from app.agent_brain.core.base_agent import BaseAgent
from app.agent_brain.extraction.assignee_extractor import extract_assignee
from app.agent_brain.extraction.datetime_extractor import extract_date_phrase, extract_time_phrase
from app.agent_brain.extraction.priority_classifier import classify_priority
from app.agent_brain.extraction.signals import find_signals, has_signal
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment
from app.agent_brain.models.recommendation import Recommendation

# --------------------------------------------------------------------------- #
# signals                                                                     #
# --------------------------------------------------------------------------- #


def test_find_signals_case_insensitive() -> None:
    assert find_signals("Please SEND me the file", ["send me", "follow up"]) == ["send me"]
    assert find_signals("nothing here", ["send me"]) == []
    assert find_signals("", ["x"]) == []


def test_has_signal() -> None:
    assert has_signal("Let's schedule a follow-up call", ["follow-up"]) is True
    assert has_signal("hello", ["follow-up"]) is False


# --------------------------------------------------------------------------- #
# assignee                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ali will prepare the report by Friday.", "Ali"),
        ("The task is assigned to Sara Khan tomorrow.", "Sara Khan"),
        ("Can you send me the proposal?", None),  # second person -> no named assignee
        ("", None),
    ],
)
def test_extract_assignee(text, expected) -> None:
    assert extract_assignee(text) == expected


# --------------------------------------------------------------------------- #
# date / time phrases                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Let's meet next Monday.", "next Monday"),
        ("Please send it by Friday.", "by Friday"),
        ("I'll do it tomorrow.", "tomorrow"),
        ("No date here.", None),
    ],
)
def test_extract_date_phrase(text, expected) -> None:
    assert extract_date_phrase(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Call at 2 PM.", "2 PM"),
        ("Around 10:30am works.", "10:30am"),
        ("No time mentioned.", None),
    ],
)
def test_extract_time_phrase(text, expected) -> None:
    assert extract_time_phrase(text) == expected


# --------------------------------------------------------------------------- #
# priority                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "base", "expected"),
    [
        ("This is urgent, fix it now.", "MEDIUM", "CRITICAL"),
        ("This is important.", "MEDIUM", "HIGH"),
        ("Just a normal note.", "MEDIUM", "MEDIUM"),
        ("Nothing special.", "HIGH", "HIGH"),  # base passthrough (escalation default)
    ],
)
def test_classify_priority(text, base, expected) -> None:
    assert classify_priority(text, base=base) == expected


# --------------------------------------------------------------------------- #
# BaseAgent                                                                   #
# --------------------------------------------------------------------------- #


class _DummyAgent(BaseAgent):
    agent_type = "TASK_AGENT"
    action_type = "TASK"

    def detect(self, context: AgentContext) -> list[Recommendation]:
        return []


def test_base_agent_is_abstract() -> None:
    with pytest.raises(TypeError):
        BaseAgent()  # type: ignore[abstract]


def test_base_agent_source_maps_segment_fields() -> None:
    seg = TranscriptSegment(
        segment_id="seg-1",
        speaker_id="speaker_1",
        speaker_label="Speaker 1",
        start_time="00:01:00",
        end_time="00:01:10",
        text="Ali will prepare the report.",
    )
    src = _DummyAgent()._source(seg)
    assert src.segment_id == "seg-1"
    assert src.speaker_id == "speaker_1"
    assert src.text == "Ali will prepare the report."
    assert _DummyAgent().detect(AgentContext(session_id="s")) == []
