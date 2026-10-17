"""Keyword rules match whole words and phrases, never part of a longer word.

Covers the pipeline's flag rules (hesitation, aggression, lie risk) and its
intent rules. Each rule fires on its keyword as a whole word or phrase, and
does not fire when the keyword is only part of a longer word. The
vocabularies are unchanged.
"""

from __future__ import annotations

import pytest

from app.services.flag_service import FlagService
from app.services.intent_service import IntentService
from app.utils.text_match import contains_phrase, starts_with_phrase


def _flag_types(text: str) -> set[str]:
    return {f["type"] for f in FlagService.generate_flags([{"speaker": "S1", "start": 0.0, "end": 1.0, "text": text}])}


@pytest.mark.parametrize(
    ("text", "flag"),
    [
        ("Um, I am not sure about that.", "hesitation"),
        ("Well, you know, it depends.", "hesitation"),
        ("I mean... fine.", "hesitation"),
        ("That is a stupid idea.", "aggression"),
        ("I hate waiting on hold.", "aggression"),
        ("I think it always works.", "lie_risk"),
    ],
)
def test_a_flag_fires_on_its_whole_keyword(text: str, flag: str) -> None:
    assert flag in _flag_types(text)


@pytest.mark.parametrize(
    ("text", "flag"),
    [
        ("The number on her order is 42.", "hesitation"),  # um, er
        ("Yeah, the summary is ready.", "hesitation"),  # ah, um
        ("Whatever works for the team.", "aggression"),  # hate
        ("The dumbbells arrived in a scrappy box.", "aggression"),  # dumb, crap
        ("Nevertheless, I think it works.", "lie_risk"),  # never
    ],
)
def test_a_flag_does_not_fire_on_part_of_a_word(text: str, flag: str) -> None:
    assert flag not in _flag_types(text)


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Hi there", "greeting"),
        ("hi", "greeting"),
        ("Hello, how are you?", "greeting"),
        ("Can you send it over", "question"),
        ("Sorry about that", "apology"),
        ("It was my fault", "apology"),
        ("I am so mad", "complaint"),
        ("Let me tell you a story", "storytelling"),
        ("Thanks a lot", "gratitude"),
        ("ok.", "agreement"),
        ("Okay then", "agreement"),
        ("Bye now", "closing"),
    ],
)
def test_an_intent_rule_fires_on_its_whole_keyword(text: str, intent: str) -> None:
    assert IntentService.classify_utterance(text) == intent


@pytest.mark.parametrize(
    ("text", "not_intent"),
    [
        ("His order is late", "greeting"),  # hi
        ("Please scan your badge", "question"),  # can you
        ("Keep my faultless record", "apology"),  # my fault
        ("I made it", "complaint"),  # mad
        ("The history of it", "storytelling"),  # story
        ("Thanksgiving plans", "gratitude"),  # thanks
        ("Book it", "agreement"),  # ok
        ("Check the byelaw", "closing"),  # bye
    ],
)
def test_an_intent_rule_does_not_fire_on_part_of_a_word(text: str, not_intent: str) -> None:
    assert IntentService.classify_utterance(text) != not_intent


@pytest.mark.parametrize(
    ("text", "phrase", "found"),
    [
        ("you know what", "you know", True),
        ("you knowledge", "you know", False),
        ("wait... what", "...", True),
        ("is it?", "?", True),
        ("number", "um", False),
        ("UM, sure", "um", True),
        ("", "um", False),
    ],
)
def test_contains_phrase(text: str, phrase: str, found: bool) -> None:
    assert contains_phrase(text.lower(), phrase) is found


@pytest.mark.parametrize(
    ("text", "phrase", "found"),
    [("hi there", "hi", True), ("his order", "hi", False), ("hi", "hi", True), ("oh hi", "hi", False)],
)
def test_starts_with_phrase(text: str, phrase: str, found: bool) -> None:
    assert starts_with_phrase(text, phrase) is found
