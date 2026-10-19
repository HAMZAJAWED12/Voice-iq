"""Recommendations about different fact-check claims are never merged.

Fact-check reviews share generic titles ("Review false claim"), so title
similarity cannot tell them apart; the claim is what identifies them.
"""

from __future__ import annotations

from app.agent_brain.models.agent_context import AgentContext
from app.agent_brain.service import AgentBrainService


def _generate(claims: list[dict]):
    context = AgentContext.model_validate({"sessionId": "s-dedup", "factChecks": {"claims": claims}})
    return [r for r in AgentBrainService().generate(context).recommendations if r.action_type == "FACT_CHECK"]


def test_two_false_claims_both_get_a_review():
    recs = _generate(
        [
            {"claimId": "c1", "claimText": "Bitcoin is 5 dollars", "status": "FALSE"},
            {"claimId": "c2", "claimText": "Gold is 3 dollars", "status": "FALSE"},
        ]
    )

    assert {r.linked_claim_id for r in recs} == {"c1", "c2"}


def test_a_critical_review_is_not_absorbed_by_a_more_confident_lesser_one():
    recs = _generate(
        [
            {"claimId": "c1", "claimText": "Gold is 3 dollars", "status": "NEEDS_REVIEW", "confidence": 0.5},
            {"claimId": "c2", "claimText": "Bitcoin is 5 dollars", "status": "FALSE", "confidence": 0.95},
        ]
    )

    assert {r.linked_claim_id for r in recs} == {"c1", "c2"}
    assert "CRITICAL" in {r.priority for r in recs}


def test_claims_without_ids_are_told_apart_by_their_text():
    recs = _generate(
        [
            {"claimText": "Bitcoin is 5 dollars", "status": "FALSE"},
            {"claimText": "Gold is 3 dollars", "status": "FALSE"},
        ]
    )

    assert len(recs) == 2


def test_the_same_claim_twice_is_still_one_review():
    claim = {"claimId": "c1", "claimText": "Bitcoin is 5 dollars", "status": "FALSE"}

    assert len(_generate([claim, dict(claim)])) == 1


def test_a_different_claim_does_not_raise_a_reviews_confidence():
    alone = _generate([{"claimId": "c1", "claimText": "Bitcoin is 5 dollars", "status": "FALSE"}])
    together = _generate(
        [
            {"claimId": "c1", "claimText": "Bitcoin is 5 dollars", "status": "FALSE"},
            {"claimId": "c2", "claimText": "Gold is 3 dollars", "status": "FALSE"},
        ]
    )

    c1 = next(r for r in together if r.linked_claim_id == "c1")
    assert c1.confidence == alone[0].confidence
