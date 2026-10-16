"""Title similarity checks cheap upper bounds before the full ratio.

`SequenceMatcher.real_quick_ratio()` and `quick_ratio()` are upper bounds on
`ratio()`, so a pair below the threshold on either is not similar, and the
O(L^2) `ratio()` is skipped. Verdicts are identical.
"""

from __future__ import annotations

import random
import string
from difflib import SequenceMatcher

import pytest

from app.agent_brain.core import deduplication as dedup


@pytest.fixture
def ratio_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    real = SequenceMatcher.ratio

    def counting(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(SequenceMatcher, "ratio", counting)
    return calls


def test_clearly_different_titles_skip_the_full_ratio(ratio_calls):
    assert dedup._titles_similar("Send the quarterly report", "x" * 80) is False
    assert dedup._titles_similar("abcdefgh", "stuvwxyz") is False

    assert ratio_calls == []


def test_similar_titles_still_get_the_full_ratio(ratio_calls):
    assert dedup._titles_similar("Review false claim", "Review flagged claim") is True

    assert len(ratio_calls) == 1


def test_verdicts_match_the_full_ratio_on_varied_titles():
    rng = random.Random(1234)
    words = ["send", "report", "follow", "up", "call", "review", "claim", "email", "meeting", "friday", "draft"]
    titles = [" ".join(rng.choice(words) for _ in range(rng.randint(1, 6))) for _ in range(120)]
    titles += ["".join(rng.choice(string.ascii_lowercase + " ") for _ in range(rng.randint(1, 40))) for _ in range(60)]

    for a in titles[:90]:
        for b in titles[90:]:
            expected = SequenceMatcher(None, a.lower(), b.lower()).ratio() >= dedup._TITLE_SIMILARITY
            assert dedup._titles_similar(a, b) is expected, (a, b)
