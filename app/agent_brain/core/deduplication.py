"""Recommendation deduplication (doc 10), stdlib difflib only.

"Let's meet Monday." / "Monday works." / "Okay, let's schedule it." should
yield ONE calendar recommendation, not three. We collapse near-duplicates
within the same action type, keeping the highest-confidence survivor.

Cost note (L5-0). `SequenceMatcher.ratio()` is O(L²) in title length and was
the single hottest frame in the whole Agent Brain — the runner used to ask
"how many other candidates duplicate this one?" once per candidate, which is
an O(n²) sweep of O(L²) comparisons. Two things fix it without changing a
single verdict:

* `duplicate_counts()` answers that question for every candidate in one pass,
  so the runner no longer re-scans per candidate;
* both it and `deduplicate()` bucket by `action_type` first, which is free
  because a cross-type pair short-circuits to False on the very first check,
  and memoize title comparisons within a single call.

The memo is deliberately **per call**, not a module-level `lru_cache`:
titles are transcript-derived, and a process-global cache would retain
conversation content across requests for no benefit beyond the request that
produced it.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from app.agent_brain.models.recommendation import Recommendation

# Title similarity above which two same-action recommendations are duplicates.
_TITLE_SIMILARITY = 0.82

# Memo of ordered title pairs -> similarity verdict, scoped to one call.
# Ordered, never normalised: `SequenceMatcher` is not guaranteed symmetric
# under argument swap, so reusing a (b, a) result for (a, b) could change a
# verdict. Identical output matters more than a few extra cache misses.
_TitleMemo = dict[tuple[str, str], bool]


def _titles_similar(a: str, b: str, memo: _TitleMemo | None = None) -> bool:
    if memo is None:
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= _TITLE_SIMILARITY
    key = (a, b)
    cached = memo.get(key)
    if cached is None:
        cached = SequenceMatcher(None, a.lower(), b.lower()).ratio() >= _TITLE_SIMILARITY
        memo[key] = cached
    return cached


def is_duplicate(
    candidate: Recommendation,
    kept: Recommendation,
    *,
    title_memo: _TitleMemo | None = None,
) -> bool:
    """True when `candidate` duplicates `kept`.

    `title_memo` is an optional per-call cache for the title comparison; it
    changes performance only, never the verdict.
    """
    if candidate.action_type != kept.action_type:
        return False
    # Same fact-check claim.
    if candidate.linked_claim_id and candidate.linked_claim_id == kept.linked_claim_id:
        return True
    # Same person + same deadline.
    if (
        candidate.entities.assignee
        and candidate.entities.assignee == kept.entities.assignee
        and candidate.entities.deadline_text == kept.entities.deadline_text
    ):
        return True
    # Same source segment.
    if candidate.source.segment_id and candidate.source.segment_id == kept.source.segment_id:
        return True
    # Similar title.
    return _titles_similar(candidate.title, kept.title, title_memo)


def duplicate_counts(recommendations: list[Recommendation]) -> list[int]:
    """How many *other* recommendations duplicate each one, by index.

    Replaces the runner's per-candidate rescan. The comparison is kept
    **directed** (`is_duplicate(recs[i], recs[j])`, never assumed symmetric)
    and the `other is not rec` identity skip is preserved, so the counts are
    exactly what the old inline sweep produced.

    Only same-`action_type` pairs are compared: a cross-type pair returns
    False on `is_duplicate`'s first line, so skipping it is equivalent, not
    an approximation.
    """
    counts = [0] * len(recommendations)
    buckets: dict[str, list[int]] = defaultdict(list)
    for index, rec in enumerate(recommendations):
        buckets[rec.action_type].append(index)

    memo: _TitleMemo = {}
    for indices in buckets.values():
        for i in indices:
            rec = recommendations[i]
            counts[i] = sum(
                1
                for j in indices
                if recommendations[j] is not rec and is_duplicate(rec, recommendations[j], title_memo=memo)
            )
    return counts


def deduplicate(recommendations: list[Recommendation]) -> list[Recommendation]:
    """Collapse near-duplicate recommendations, keeping the strongest of each."""
    kept: list[Recommendation] = []
    memo: _TitleMemo = {}
    # Highest confidence first so the survivor of each duplicate group is the
    # most confident one.
    #
    # `kept` is NOT bucketed by action_type here, deliberately. It stays in
    # confidence order because the caller ranks with a stable sort, so the
    # order of `kept` decides ties — regrouping it would reorder equal-ranked
    # recommendations. The loop is O(n·k) with k small, so it was never the
    # hot path anyway; the memo is what it needed.
    for candidate in sorted(recommendations, key=lambda r: r.confidence, reverse=True):
        if not any(is_duplicate(candidate, k, title_memo=memo) for k in kept):
            kept.append(candidate)
    return kept
