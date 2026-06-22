"""Coverage for the Fact-Check Review Agent."""

from __future__ import annotations

from app.agent_brain.core.factcheck_review_agent import FactCheckReviewAgent
from app.agent_brain.models.agent_context import AgentContext, ContextClaim, ContextFactChecks


def _ctx(*claims: ContextClaim) -> AgentContext:
    return AgentContext(session_id="s", fact_checks=ContextFactChecks(claims=list(claims)))


def test_factcheck_unverified_review() -> None:
    ctx = _ctx(
        ContextClaim(claim_id="c1", claim_text="The product is certified by ISO.", status="UNVERIFIED", confidence=0.63)
    )
    recs = FactCheckReviewAgent().detect(ctx)
    assert len(recs) == 1
    r = recs[0]
    assert r.agent_type == "FACT_CHECK_REVIEW_AGENT"
    assert r.action_type == "FACT_CHECK"
    assert r.title == "Review unverified claim"
    assert r.linked_claim_id == "c1"
    assert r.priority == "HIGH"
    assert r.confidence == 0.85  # 0.80 base + 0.05 (claim conf < 0.7)
    assert r.requires_approval is True


def test_factcheck_false_is_critical() -> None:
    recs = FactCheckReviewAgent().detect(_ctx(ContextClaim(claim_id="c2", claim_text="X is true.", status="FALSE")))
    assert recs[0].priority == "CRITICAL"
    assert recs[0].confidence == 0.9  # no low-confidence bump


def test_factcheck_needs_review_status() -> None:
    recs = FactCheckReviewAgent().detect(_ctx(ContextClaim(claim_id="c3", claim_text="Claim.", status="NEEDS_REVIEW")))
    assert recs[0].title == "Review flagged claim"


def test_factcheck_skips_verified_and_unsupported() -> None:
    ctx = _ctx(
        ContextClaim(claim_id="t", claim_text="True thing.", status="TRUE"),
        ContextClaim(claim_id="s", claim_text="No source.", status="SOURCE_UNAVAILABLE"),
    )
    assert FactCheckReviewAgent().detect(ctx) == []


def test_factcheck_skips_empty_text_and_no_claims() -> None:
    assert FactCheckReviewAgent().detect(_ctx(ContextClaim(claim_id="e", claim_text="", status="FALSE"))) == []
    assert FactCheckReviewAgent().detect(AgentContext(session_id="s")) == []


def test_factcheck_multiple_claims() -> None:
    ctx = _ctx(
        ContextClaim(claim_id="a", claim_text="One.", status="FALSE"),
        ContextClaim(claim_id="b", claim_text="Two.", status="UNVERIFIED"),
    )
    assert len(FactCheckReviewAgent().detect(ctx)) == 2
