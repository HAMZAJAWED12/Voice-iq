"""The email-draft triggers match whole words and phrases only.

"send a" must not fire inside "send all". Other agents' signal matching is
unchanged.
"""

from __future__ import annotations

import pytest

from app.agent_brain.core.email_draft_agent import EmailDraftAgent
from app.agent_brain.extraction.signals import find_signals
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment


def _ctx(text: str) -> AgentContext:
    return AgentContext(
        session_id="s", transcript=[TranscriptSegment(segment_id="s0", speaker_id="speaker_1", text=text)]
    )


@pytest.mark.parametrize(
    "text",
    ["Please send a copy of the contract.", "Can you email me the invoice?", "Forward the details, please."],
)
def test_an_email_trigger_fires_as_a_whole_phrase(text: str) -> None:
    assert len(EmailDraftAgent().detect(_ctx(text))) == 1


@pytest.mark.parametrize(
    "text",
    ["We will send all of them tomorrow.", "They resend another batch weekly.", "Unsend anything you regret."],
)
def test_an_email_trigger_does_not_fire_on_part_of_a_word(text: str) -> None:
    assert EmailDraftAgent().detect(_ctx(text)) == []


def test_substring_matching_is_still_the_default() -> None:
    assert find_signals("we will send all of them", ["send a"]) == ["send a"]
    assert find_signals("we will send all of them", ["send a"], whole_words=True) == []
