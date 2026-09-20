"""L5-0: the Agent Brain's duplicate sweep must stay sub-quadratic in difflib.

The runner used to ask "how many other candidates duplicate this one?" once
per candidate — an O(n²) sweep where every comparison could reach
`SequenceMatcher.ratio()`, itself O(L²) in title length. On a 16-minute call
that was 44,539 `find_longest_match` calls and ~2.3 s, on an endpoint that
is publicly mounted.

Two guards here, and the second is the one that matters:

1. `duplicate_counts()` returns exactly what the old inline sweep returned.
2. difflib work does **not grow with transcript size**.

Guard 2 exists because of the E3 lesson: the output is a constant 6
recommendations at every n, so wall-clock and output size can both look fine
while the complexity is unchanged. Only the call count proves it.
"""

from __future__ import annotations

import difflib

import pytest

import app.agent_brain.core.deduplication as dedup
from app.agent_brain.core.deduplication import duplicate_counts, is_duplicate
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment
from app.agent_brain.models.recommendation import Entities, Recommendation, Source
from app.agent_brain.service import AgentBrainService

_LINES = [
    "Ali will send the report by Friday at 2 PM.",
    "I am really upset about this delay, it is unacceptable.",
    "Can you email me the pricing proposal today?",
    "Let's meet again next Monday for 30 minutes.",
    "This is urgent, please escalate to your manager immediately.",
]


def _context(n: int) -> AgentContext:
    return AgentContext(
        session_id="s",
        transcript=[
            TranscriptSegment(segment_id=f"s{i}", speaker_id="A", text=_LINES[i % len(_LINES)], confidence=0.9)
            for i in range(n)
        ],
    )


def _rec(action_type, title, confidence, *, assignee=None, deadline=None, claim=None, segment_id="seg"):
    return Recommendation(
        agent_type="TASK_AGENT",
        action_type=action_type,
        title=title,
        description="d",
        confidence=confidence,
        linked_claim_id=claim,
        source=Source(segment_id=segment_id, text="t"),
        entities=Entities(assignee=assignee, deadline_text=deadline),
        explanation="e",
    )


def _naive_counts(recs: list[Recommendation]) -> list[int]:
    """Exactly what AgentRunner._refine used to compute, inline and per candidate."""
    return [sum(1 for other in recs if other is not rec and is_duplicate(rec, other)) for rec in recs]


def _count_sequencematcher(fn) -> int:
    """Count SequenceMatcher constructions during `fn()`.

    Patches the name in `deduplication`'s namespace, which is where
    `_titles_similar` resolves it. Patching `difflib` alone would miss it.
    """
    calls = 0
    real = dedup.SequenceMatcher

    class Counting(real):  # type: ignore[misc,valid-type]
        def __init__(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            super().__init__(*args, **kwargs)

    dedup.SequenceMatcher = Counting  # type: ignore[attr-defined]
    try:
        fn()
    finally:
        dedup.SequenceMatcher = real  # type: ignore[attr-defined]
    return calls


# --------------------------------------------------------------------------- #
# 1. Equivalence with the sweep it replaced                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("n", [0, 1, 5, 20, 60])
def test_duplicate_counts_matches_the_naive_sweep(n: int) -> None:
    """The optimization must be a pure refactor of the count, not a new rule."""
    recs = AgentBrainService().runner._collect(_context(n))
    assert duplicate_counts(recs) == _naive_counts(recs)


def test_duplicate_counts_matches_naive_across_every_duplicate_branch() -> None:
    """Hand-built set hitting claim-id, assignee+deadline, segment-id and title."""
    recs = [
        _rec("TASK", "Send the report", 0.9, assignee="Ali", deadline="Friday", segment_id="a"),
        _rec("TASK", "Send the report", 0.8, assignee="Ali", deadline="Friday", segment_id="b"),
        _rec("TASK", "Send the reports", 0.7, segment_id="c"),  # near title
        _rec("TASK", "Completely unrelated action", 0.6, segment_id="d"),
        _rec("CALENDAR", "Send the report", 0.5, segment_id="a"),  # same title, other type
        _rec("TASK", "Verify claim", 0.4, claim="c1", segment_id="e"),
        _rec("TASK", "Verify claim again", 0.3, claim="c1", segment_id="f"),
        _rec("TASK", "Shared segment", 0.2, segment_id="a"),
    ]
    assert duplicate_counts(recs) == _naive_counts(recs)


def test_duplicate_counts_handles_empty() -> None:
    assert duplicate_counts([]) == []


def test_cross_action_type_never_counts() -> None:
    """Bucketing by action_type is only valid because this holds."""
    a = _rec("TASK", "Identical title", 0.9, segment_id="x")
    b = _rec("CALENDAR", "Identical title", 0.9, segment_id="x")
    assert is_duplicate(a, b) is False
    assert duplicate_counts([a, b]) == [0, 0]


# --------------------------------------------------------------------------- #
# 2. The complexity guard — the one that would have caught the regression     #
# --------------------------------------------------------------------------- #


def test_difflib_work_does_not_grow_with_transcript_size() -> None:
    """difflib cost must depend on distinct titles, not on transcript length.

    Before L5-0 this was 779 at n=40 and 12,347 at n=160 — quadratic. The
    memo makes it a function of how many distinct title pairs exist, which
    these fixtures hold constant while n quadruples.
    """
    svc = AgentBrainService()
    small = _count_sequencematcher(lambda: svc.generate(_context(40)))
    large = _count_sequencematcher(lambda: svc.generate(_context(160)))

    assert large == small, f"difflib work grew with n: {small} -> {large}"


def test_difflib_work_stays_small_in_absolute_terms() -> None:
    """A bound, so a future change cannot quietly reintroduce the sweep."""
    svc = AgentBrainService()
    calls = _count_sequencematcher(lambda: svc.generate(_context(160)))
    assert calls < 200, f"{calls} SequenceMatcher constructions for one call"


def test_output_is_unchanged_by_the_optimization() -> None:
    """Same recommendations, same order, same confidences at every size.

    recommendation_id is excluded: it is a fresh uuid4 per instance, so
    including it makes any fingerprint differ run-to-run on identical code.
    """
    svc = AgentBrainService()

    def fingerprint(n: int) -> list[tuple[str, str, str, float]]:
        return [(r.agent_type, r.action_type, r.title, r.confidence) for r in svc.generate(_context(n)).recommendations]

    # Stable within a run...
    assert fingerprint(40) == fingerprint(40)
    # ...and the documented shape: dedup collapses to the same set regardless
    # of how many times the same lines repeat.
    assert fingerprint(40) == fingerprint(160)
    assert len(fingerprint(160)) == 6


def test_sequencematcher_patch_targets_the_right_namespace() -> None:
    """Guard the guard: if _titles_similar stops resolving SequenceMatcher
    from deduplication's namespace, the counter above would silently read 0
    and both complexity tests would pass vacuously."""
    assert dedup.SequenceMatcher is difflib.SequenceMatcher
    counted = _count_sequencematcher(lambda: dedup._titles_similar("abcdef", "abcdeg"))
    assert counted == 1
