"""The email-draft triggers match whole words and phrases only.

"send a" must not fire inside "send all". Article and pronoun forms ("send
an", "share these", "forward them") are triggers of their own, and the
title takes the object without its article. "We will send an engineer" is a
trigger too: an accepted trade-off, since every recommendation needs approval.
Other agents' signal matching is unchanged.
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
    ("text", "title"),
    [
        ("Please send an invoice by Friday.", "Send invoice"),
        ("Could you send them over by Monday?", "Send the requested document"),
        ("Please share these slides with the team.", "Send these slides"),
        ("Forward these documents to legal.", "Send these documents"),
        ("Can you send those?", "Send the requested document"),
        ("Can you send these?", "Send the requested document"),
        ("Could you send the, um, later?", "Send the requested document"),
        ("Please send these files today.", "Send these files today"),
        ("Can you share those figures?", "Send those figures"),
        ("Forward those invoices to accounts.", "Send those invoices"),
        ("Share them with finance.", "Send the requested document"),
        ("Send them by Friday.", "Send the requested document"),
        ("Share them before noon.", "Send the requested document"),
        ("Forward them to legal.", "Send the requested document"),
        ("Send them on Monday.", "Send the requested document"),
        ("Send them via post.", "Send the requested document"),
    ],
)
def test_an_email_trigger_fires_as_a_whole_phrase_with_an_article_or_pronoun(text: str, title: str) -> None:
    recs = EmailDraftAgent().detect(_ctx(text))

    assert len(recs) == 1
    assert recs[0].action_type == "EMAIL"
    assert recs[0].title == title


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("Send the before and after photos.", "Send before and after photos"),
        ("Send the to do list to the team.", "Send to do list"),
        ("Send me the on site report by Monday.", "Send on site report"),
        ("Please share the on call rota by Friday.", "Send on call rota"),
    ],
)
def test_an_object_after_an_article_may_start_with_a_linking_word(text: str, title: str) -> None:
    assert [r.title for r in EmailDraftAgent().detect(_ctx(text))] == [title]


def test_a_request_to_send_a_person_is_an_email_trigger_too() -> None:
    # Accepted trade-off: "send an" cannot tell a document from a person.
    assert len(EmailDraftAgent().detect(_ctx("We will send an engineer tomorrow."))) == 1


@pytest.mark.parametrize(
    "text",
    ["We will send all of them tomorrow.", "They resend another batch weekly.", "Unsend anything you regret."],
)
def test_an_email_trigger_does_not_fire_on_part_of_a_word(text: str) -> None:
    assert EmailDraftAgent().detect(_ctx(text)) == []


def test_substring_matching_is_still_the_default() -> None:
    assert find_signals("we will send all of them", ["send a"]) == ["send a"]
    assert find_signals("we will send all of them", ["send a"], whole_words=True) == []
