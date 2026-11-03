"""Matching, metrics, failures, rows used and gates (S2 §5.1-§5.3, §6).

H1 spec rules M1-M11, P1-P4, L1-L6, K1-K3, FL1-FL4, RU1-RU3, GT2-GT3 and G7-G8, and the section 6 cross-checks.
Every expected value is an exact Fraction from the spec; fixtures are synthetic (`_fixtures.py`).
"""

from __future__ import annotations

import dataclasses
import json
from fractions import Fraction as F
from pathlib import Path

import pytest

from app.evaluation.bakeoff.extraction import (
    ExtractedClaim,
    Extraction,
    ExtractionFailure,
    NonCheckableEntry,
    parse_extraction,
)
from app.evaluation.bakeoff.gold import OUT_OF_SCOPE_BUCKETS, GoldSet, load_gold_set
from app.evaluation.bakeoff.scoring import (
    FIDELITY_PENDING,
    SPELLED_NUMBER_WORDS,
    FailedConversation,
    FlaggedGroup,
    FlaggedGroupStatus,
    RunMetrics,
    SliceRecall,
    claim_fidelity,
    gate_a,
    gate_s,
    has_digits,
    has_spelled_number,
    per_proposition_recall,
    score_conversation,
    score_run,
)
from app.evaluation.bakeoff.stats import ConversationCounts as CC
from app.evaluation.bakeoff.stats import paired_bootstrap, pooled_f1
from app.evaluation.tests._fixtures import (
    E1_CLAIMS,
    E1_NC,
    XM_A,
    XM_A_NC,
    extraction,
    f_run,
    fixture_f,
    fixture_x,
    load_f,
    load_x,
    make_conversation,
    make_item,
    output_json,
    run_e1,
    run_e1t,
    run_xm,
    run_xp,
    run_xw,
    seg,
    write_version,
    x_run,
)

NO_FAILURES = {"invalid_output": 0, "cut_off": 0, "timeout": 0, "server_error": 0}


@pytest.fixture
def gold_f(tmp_path: Path) -> GoldSet:
    return load_f(tmp_path / "f")


@pytest.fixture
def gold_fp(tmp_path: Path) -> GoldSet:
    return load_f(tmp_path / "fplus", plus=True)


@pytest.fixture
def gold_x(tmp_path: Path) -> GoldSet:
    return load_x(tmp_path / "x")


def key(source_id: str, number: int) -> tuple[str, str]:
    return (source_id, seg(number))


def route(m: RunMetrics, source_id: str, number: int) -> str:
    return m.conversation_scores[source_id].routing[key(source_id, number)]


def label(m: RunMetrics, source_id: str, number: int) -> str:
    return m.conversation_scores[source_id].labels[key(source_id, number)]


def kinds(m: RunMetrics, source_id: str) -> list[str]:
    return [outcome.kind for outcome in m.conversation_scores[source_id].claim_outcomes]


def buckets(**nonzero: int) -> dict[str, int]:
    return {bucket: nonzero.get(bucket, 0) for bucket in OUT_OF_SCOPE_BUCKETS}


def high_stakes(health: int = 0, legal: int = 0, personal_finance: int = 0) -> dict[str, int]:
    return {"HEALTH": health, "LEGAL": legal, "PERSONAL_FINANCE": personal_finance}


# ---------------------------------------------------------------- section 6 cross-checks


def test_e1_on_gold_f(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f))
    assert (m.n_checkable_units, m.found_units, m.fp_claims) == (2, 2, 2)
    assert (m.recall, m.precision, m.f1, m.recall_target_met) == (1, F(1, 2), F(2, 3), True)
    assert all(isinstance(value, F) for value in (m.recall, m.precision, m.f1))
    assert (m.raw_claims, m.finding_claims, m.duplicate_claims, m.fp_claims, m.leak_only_claims) == (6, 2, 1, 2, 1)
    assert (m.neutral_context_citations, m.neutral_context_out_of_scope, m.neutral_context_high_stakes) == (2, 1, 1)
    assert m.non_item_context_citations == 0
    assert (m.non_checkable_entries, m.non_checkable_non_item_citations) == (4, 1)
    assert m.conflicting_non_checkable_rows == ()
    assert (m.n_rows, m.routing_correct, m.routing_leaked, m.routing_misrouted, m.routing_omitted) == (7, 4, 3, 0, 0)
    assert m.routing_accuracy == F(4, 7)
    assert [route(m, "syn_900", n) for n in range(2, 9)] == [
        "correct",
        "correct",
        "correct",
        "leaked",
        "correct",
        "leaked",
        "leaked",
    ]
    assert m.checkability_binary_f1 == F(4, 7)
    assert dict(m.checkability_class_f1) == {
        "": F(4, 7),
        "opinion": 0,
        "prediction": None,
        "request": 1,
        "vague": None,
        "outOfScope": F(2, 3),
    }
    assert m.checkability_macro_f1 == F(47, 84)
    assert (m.n_in_scope_non_checkable, m.leaked_in_scope_non_checkable) == (2, ("fx_04",))
    assert m.in_scope_non_checkable_leak_rate == F(1, 2)
    assert (m.n_out_of_scope, m.leaked_out_of_scope, m.out_of_scope_leak_rate) == (3, ("fx_06", "fx_07"), F(2, 3))
    assert dict(m.out_of_scope_leaks_by_domain) == buckets(OWN_COMPANY=1, PERSONAL_FINANCE=1)
    assert (m.n_high_stakes, m.high_stakes_leaks) == (2, 1)
    assert dict(m.high_stakes_leaks_by_domain) == high_stakes(personal_finance=1)
    assert (m.failed_conversations, dict(m.failures_by_kind)) == ((), NO_FAILURES)
    assert (m.recall_digits, m.recall_spelled) == (SliceRecall(1, 1, F(1)), SliceRecall(1, 1, F(1)))
    assert (m.recall_no_number, m.digits_and_spelled_rows) == (SliceRecall(0, 0, None), 0)
    hc = m.high_confidence
    assert (hc.n_checkable_units, hc.found_units, hc.fp_claims) == (1, 1, 2)
    assert (hc.recall, hc.precision, hc.f1) == (1, F(1, 3), F(1, 2))
    assert (hc.n_rows, hc.routing_correct, hc.routing_accuracy) == (6, 3, F(1, 2))
    assert (hc.checkability_binary_f1, hc.checkability_macro_f1) == (F(2, 5), F(31, 60))
    assert (hc.in_scope_non_checkable_leak_rate, hc.out_of_scope_leak_rate, hc.high_stakes_leaks) == (
        F(1, 2),
        F(2, 3),
        1,
    )
    assert dict(m.per_conversation) == {"syn_900": CC(2, 2, 2)}
    assert m.flagged_groups == ()
    assert not gate_a(m).passed and not gate_s(m).passed


def test_e1t_on_gold_f_plus(gold_fp: GoldSet) -> None:
    m = score_run(gold_fp, run_e1t(gold_fp))
    assert (m.n_checkable_units, m.found_units, m.fp_claims) == (3, 2, 2)
    assert (m.recall, m.precision, m.f1) == (F(2, 3), F(1, 2), F(4, 7))
    assert (m.routing_accuracy, m.routing_correct, m.routing_leaked, m.routing_omitted) == (F(4, 9), 4, 3, 2)
    assert (route(m, "syn_901", 1), route(m, "syn_901", 2)) == ("omitted", "omitted")
    assert m.checkability_binary_f1 == F(1, 2)
    assert dict(m.checkability_class_f1) == {
        "": F(1, 2),
        "opinion": 0,
        "prediction": None,
        "request": 1,
        "vague": None,
        "outOfScope": F(1, 2),
    }
    assert m.checkability_macro_f1 == F(1, 2)
    assert m.in_scope_non_checkable_leak_rate == F(1, 2)
    assert (m.out_of_scope_leak_rate, m.n_out_of_scope) == (F(2, 4), 4)
    assert (m.high_stakes_leaks, m.n_high_stakes) == (1, 3)
    assert m.failed_conversations == (FailedConversation("syn_901", "timeout", ("fx_09",)),)
    assert dict(m.failures_by_kind) == {**NO_FAILURES, "timeout": 1}
    assert (m.recall_digits, m.recall_spelled, m.recall_no_number) == (
        SliceRecall(1, 1, F(1)),
        SliceRecall(1, 1, F(1)),
        SliceRecall(1, 0, F(0)),
    )
    hc = m.high_confidence
    assert (hc.recall, hc.precision, hc.f1, hc.routing_accuracy) == (F(1, 2), F(1, 3), F(2, 5), F(3, 8))
    assert (hc.out_of_scope_leak_rate, hc.high_stakes_leaks) == (F(1, 2), 1)
    assert dict(m.per_conversation) == {"syn_900": CC(2, 2, 2), "syn_901": CC(0, 0, 1)}


def test_xm_on_gold_x(gold_x: GoldSet) -> None:
    m = score_run(gold_x, run_xm(gold_x))
    assert (m.n_checkable_units, m.found_units, m.fp_claims) == (3, 2, 0)
    assert (m.recall, m.precision, m.f1, m.recall_target_met) == (F(2, 3), 1, F(4, 5), False)
    assert (m.raw_claims, m.finding_claims, m.duplicate_claims, m.leak_only_claims) == (4, 2, 1, 1)
    assert kinds(m, "syn_a") == ["finding", "duplicate", "leak_only", "finding"]
    assert (m.neutral_context_citations, m.neutral_context_out_of_scope, m.neutral_context_high_stakes) == (1, 0, 0)
    assert m.non_item_context_citations == 1
    assert (m.non_checkable_entries, m.conflicting_non_checkable_rows) == (3, ())
    assert (m.routing_accuracy, m.routing_correct, m.routing_leaked, m.routing_misrouted, m.routing_omitted) == (
        F(3, 8),
        3,
        1,
        1,
        3,
    )
    assert [route(m, "syn_a", n) for n in (2, 3, 5)] == ["correct"] * 3
    assert (route(m, "syn_a", 4), route(m, "syn_a", 6)) == ("leaked", "misrouted")
    assert [route(m, "syn_b", n) for n in (1, 2, 4)] == ["omitted"] * 3
    assert m.checkability_binary_f1 == F(2, 3)
    assert dict(m.checkability_class_f1) == {
        "": F(2, 3),
        "opinion": 1,
        "prediction": None,
        "request": 0,
        "vague": 0,
        "outOfScope": 0,
    }
    assert m.checkability_macro_f1 == F(1, 3)
    assert (m.leaked_in_scope_non_checkable, m.in_scope_non_checkable_leak_rate) == ((), 0)
    # AMB-14: x8 sits in the failed syn_b and stays in the in-scope non-checkable denominator, as in the others.
    assert (m.n_in_scope_non_checkable, m.n_out_of_scope, m.n_high_stakes, m.n_rows) == (2, 3, 2, 8)
    assert (m.leaked_out_of_scope, m.out_of_scope_leak_rate) == (("x_03",), F(1, 3))
    assert dict(m.out_of_scope_leaks_by_domain) == buckets(HEALTH=1)
    assert (m.high_stakes_leaks, m.n_high_stakes, dict(m.high_stakes_leaks_by_domain)) == (1, 2, high_stakes(1))
    assert m.failed_conversations == (FailedConversation("syn_b", "timeout", ("x_07",)),)
    assert (m.recall_digits, m.recall_spelled, m.recall_no_number) == (
        SliceRecall(1, 0, F(0)),
        SliceRecall(1, 1, F(1)),
        SliceRecall(1, 1, F(1)),
    )
    hc = m.high_confidence
    assert (hc.n_checkable_units, hc.found_units, hc.recall, hc.precision, hc.f1) == (2, 1, F(1, 2), 1, F(2, 3))
    assert (hc.routing_accuracy, hc.checkability_binary_f1, hc.checkability_macro_f1) == (F(2, 7), F(1, 2), F(3, 10))
    assert hc.out_of_scope_leak_rate == F(1, 3)
    assert dict(m.per_conversation) == {"syn_a": CC(2, 0, 2), "syn_b": CC(0, 0, 1)}
    assert pooled_f1(list(m.per_conversation.values())) == m.f1  # B1: the pooled F1 equals RunMetrics.f1
    assert not gate_a(m).passed and not gate_s(m).passed


def test_xw_whole_conversation_claim(gold_x: GoldSet) -> None:
    m = score_run(gold_x, run_xw(gold_x))
    assert (m.found_units, m.fp_claims, m.leaked_in_scope_non_checkable, m.leaked_out_of_scope) == (2, 0, (), ())
    assert (m.recall, m.precision, m.f1, m.routing_accuracy) == (F(2, 3), 1, F(4, 5), F(5, 8))
    assert (m.neutral_context_citations, m.neutral_context_out_of_scope, m.neutral_context_high_stakes) == (3, 2, 1)
    assert m.non_item_context_citations == 1
    assert m.conflicting_non_checkable_rows == ("x_02", "x_03", "x_05")
    assert (m.checkability_binary_f1, m.checkability_macro_f1) == (F(4, 5), F(52, 75))
    assert dict(m.checkability_class_f1) == {
        "": F(4, 5),
        "opinion": 1,
        "prediction": None,
        "request": 1,
        "vague": 0,
        "outOfScope": F(2, 3),
    }


def test_xp_perfect_output(gold_x: GoldSet) -> None:
    m = score_run(gold_x, run_xp(gold_x))
    figures = (m.recall, m.precision, m.f1, m.routing_accuracy, m.checkability_binary_f1, m.checkability_macro_f1)
    assert figures == (1, 1, 1, 1, 1, 1)
    assert (m.out_of_scope_leak_rate, m.high_stakes_leaks) == (0, 0)
    assert dict(m.checkability_class_f1) == {
        "": 1,
        "opinion": 1,
        "prediction": None,
        "request": 1,
        "vague": 1,
        "outOfScope": 1,
    }
    assert gate_a(m).passed and gate_s(m).passed


# ---------------------------------------------------------------- M1 unit of matching, M2 claim classes


def test_matching_uses_segment_ids_only(gold_f: GoldSet) -> None:
    score = score_conversation(gold_f, extraction(gold_f, "syn_900", E1_CLAIMS, E1_NC))
    c2, c3 = score.claim_outcomes[2], score.claim_outcomes[3]
    assert (c2.predicted, c2.neutral, c2.non_item_segment_ids) == (
        {key("syn_900", 4)},
        {key("syn_900", 6)},
        frozenset(),
    )
    assert (c3.predicted, c3.neutral, c3.non_item_segment_ids) == (frozenset(), frozenset(), {"seg_001"})
    unrelated = extraction(gold_f, "syn_900", [[2]], text="the moon is made of cheese")
    assert score_conversation(gold_f, unrelated).claim_outcomes[0].kind == "finding"


@pytest.mark.parametrize(
    ("claim", "kind"),
    [
        ([2], "finding"),
        ([1], "false_positive"),
        ([3], "false_positive"),
        ([4], "leak_only"),
        ([6], "leak_only"),  # x5 is a request but out of scope
        ([4, 1], "false_positive"),
    ],
)
def test_claim_classes(gold_x: GoldSet, claim: list[int], kind: str) -> None:
    assert score_conversation(gold_x, extraction(gold_x, "syn_a", [claim])).claim_outcomes[0].kind == kind


def test_e1_claim_classes(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f))
    assert kinds(m, "syn_900") == ["finding", "duplicate", "finding", "false_positive", "leak_only", "false_positive"]
    new_units = [outcome.new_units for outcome in m.conversation_scores["syn_900"].claim_outcomes]
    assert new_units == [1, 0, 1, 0, 0, 0]


# ---------------------------------------------------------------- M3 context rule


def test_context_rows_are_neutral_even_when_high_stakes(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [[2, 4]]))
    assert m.found_units == 1
    assert route(m, "syn_a", 4) == "omitted"
    assert (m.leaked_out_of_scope, m.high_stakes_leaks) == ((), 0)
    assert (m.neutral_context_citations, m.neutral_context_out_of_scope, m.neutral_context_high_stakes) == (1, 1, 1)
    assert (m.recall, m.precision, m.f1, m.routing_accuracy) == (F(1, 3), 1, F(1, 2), F(1, 8))


def test_a_neutral_row_is_scored_from_non_checkable_entries(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [[5, 3]], [("opinion", [3])]))
    assert (m.found_units, route(m, "syn_a", 3), m.routing_accuracy) == (1, "correct", F(2, 8))


@pytest.mark.parametrize("claims", [[[2, 4], [4]], [[4], [2, 4]]])
def test_a_context_row_predicted_by_another_claim_is_leaked(gold_x: GoldSet, claims: list[list[int]]) -> None:
    # §5.2: "a row is leaked if any claim predicts CHECKABLE for it"; context for one claim does not cancel that.
    m = score_run(gold_x, x_run(gold_x, claims))
    assert (route(m, "syn_a", 4), m.leaked_out_of_scope, m.high_stakes_leaks) == ("leaked", ("x_03",), 1)
    assert (m.leak_only_claims, m.neutral_context_citations, m.out_of_scope_leak_rate) == (1, 1, F(1, 3))
    m = score_run(gold_x, x_run(gold_x, [[2, 3], [3]], [("opinion", [3])]))
    assert (route(m, "syn_a", 3), m.leaked_in_scope_non_checkable, m.fp_claims) == ("leaked", ("x_02",), 1)


def test_e1_context_citations(gold_f: GoldSet) -> None:
    score = score_run(gold_f, run_e1(gold_f)).conversation_scores["syn_900"]
    c1, c2 = score.claim_outcomes[1], score.claim_outcomes[2]
    assert (c1.predicted, c1.neutral) == ({key("syn_900", 2)}, {key("syn_900", 3)})
    assert key("syn_900", 6) in c2.neutral and key("syn_900", 6) not in score.leaked_rows


# ---------------------------------------------------------------- M4 claims with no in-scope CHECKABLE row


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        (
            [[4]],
            {
                "fp_claims": 0,
                "leak_only_claims": 1,
                "leaked_out_of_scope": ("x_03",),
                "out_of_scope_leak_rate": F(1, 3),
                "high_stakes_leaks": 1,
                "precision": None,
            },
        ),
        ([[4, 1]], {"fp_claims": 1, "leaked_out_of_scope": ("x_03",), "high_stakes_leaks": 1, "precision": 0}),
        (
            [[3, 4]],
            {
                "fp_claims": 1,
                "leaked_in_scope_non_checkable": ("x_02",),
                "in_scope_non_checkable_leak_rate": F(1, 2),
                "out_of_scope_leak_rate": F(1, 3),
            },
        ),
        ([[1], [1]], {"fp_claims": 2}),
        ([[3], [3]], {"fp_claims": 2, "leaked_in_scope_non_checkable": ("x_02",)}),
        ([[4], [4]], {"leak_only_claims": 2, "leaked_out_of_scope": ("x_03",)}),
    ],
)
def test_claims_with_no_in_scope_checkable_row(gold_x: GoldSet, claims: list[list[int]], expected: dict) -> None:
    m = score_run(gold_x, x_run(gold_x, claims))
    assert {name: getattr(m, name) for name in expected} == expected


def test_a_leak_only_claim_is_in_neither_precision_term(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [[2], [4]]))
    assert (m.found_units, m.fp_claims, m.precision, dict(m.high_stakes_leaks_by_domain)) == (1, 0, 1, high_stakes(1))


def test_fixture_f_claims_with_no_checkable_row(gold_f: GoldSet) -> None:
    m = score_run(gold_f, f_run(gold_f, [[7, 9]]))
    assert (m.fp_claims, m.leaked_out_of_scope) == (1, ("fx_06",))
    m = score_run(gold_f, f_run(gold_f, [[6, 8]]))
    assert (m.fp_claims, m.leak_only_claims, m.leaked_out_of_scope) == (0, 1, ("fx_05", "fx_07"))
    assert (m.high_stakes_leaks, m.precision) == (2, None)


# ---------------------------------------------------------------- M5 duplicates and order


@pytest.mark.parametrize(
    ("claims", "dup", "found"),
    [
        ([[2], [2]], 1, 1),
        ([[2, 5], [2]], 1, 2),
        ([[2], [2, 5]], 0, 2),
        ([[2], [2, 1]], 1, 1),
        ([[2], [2, 4]], 1, 1),
    ],
)
def test_duplicates(gold_x: GoldSet, claims: list[list[int]], dup: int, found: int) -> None:
    m = score_run(gold_x, x_run(gold_x, claims))
    assert (m.raw_claims, m.duplicate_claims, m.found_units, m.fp_claims, m.precision) == (2, dup, found, 0, 1)
    assert m.leaked_out_of_scope == ()


def test_claims_order_decides_only_which_claim_is_the_duplicate(gold_f: GoldSet) -> None:
    first = score_run(gold_f, f_run(gold_f, [[2, 4], [2]]))
    second = score_run(gold_f, f_run(gold_f, [[2], [2, 4]]))
    assert (first.duplicate_claims, second.duplicate_claims) == (1, 0)
    for m in (first, second):
        assert (m.found_units, m.fp_claims, m.precision) == (2, 0, 1)


def _gated(m: RunMetrics) -> tuple:
    return (
        m.found_units,
        m.fp_claims,
        m.recall,
        m.precision,
        m.f1,
        m.routing_accuracy,
        m.high_stakes_leaks,
        m.out_of_scope_leak_rate,
        m.leaked_in_scope_non_checkable,
        m.leaked_out_of_scope,
        m.checkability_macro_f1,
        m.raw_claims,
        dict(m.per_conversation),
    )


def test_gated_figures_do_not_depend_on_claims_order(gold_f: GoldSet, gold_x: GoldSet) -> None:
    reversed_e1 = run_e1(gold_f, list(reversed(E1_CLAIMS)))
    assert _gated(score_run(gold_f, run_e1(gold_f))) == _gated(score_run(gold_f, reversed_e1))
    reversed_xm = x_run(gold_x, list(reversed(XM_A)), XM_A_NC, b_fail="timeout")
    assert _gated(score_run(gold_x, run_xm(gold_x))) == _gated(score_run(gold_x, reversed_xm))


# ---------------------------------------------------------------- M6 several rows per claim; compound rows


def test_one_claim_can_find_several_rows(gold_x: GoldSet, gold_f: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [[2, 5]]))
    assert (m.found_units, m.precision, m.conversation_scores["syn_a"].claim_outcomes[0].new_units) == (2, 1, 2)
    m = score_run(gold_f, f_run(gold_f, [[2, 4]]))
    assert (m.found_units, m.recall, m.precision, m.f1) == (2, 1, 1, 1)


def test_a_compound_row_is_found_at_most_once(gold_f: GoldSet) -> None:
    raw = json.dumps(
        {
            "claims": [
                {"text": "part one", "segmentIds": ["seg_004"]},
                {"text": "part two", "segmentIds": ["seg_004"]},
            ],
            "nonCheckable": [],
        }
    )
    result = parse_extraction(raw, source_id="syn_900", request_segment_ids=gold_f.segment_ids("syn_900"))
    m = score_run(gold_f, {"syn_900": result})
    assert (m.found_units, m.duplicate_claims, m.fp_claims) == (1, 1, 0)
    assert (m.recall, m.precision, m.f1) == (F(1, 2), 1, F(2, 3))


# ---------------------------------------------------------------- M7 counters


def test_counters_with_an_extra_duplicate(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f, [*E1_CLAIMS, [2, 1]]))
    assert kinds(m, "syn_900")[-1] == "duplicate"
    assert (m.duplicate_claims, m.neutral_context_citations, m.non_item_context_citations) == (2, 2, 1)


def test_non_checkable_entries_on_non_item_segments_are_counted_only(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [], [("vague", [1])]))
    assert (m.non_checkable_entries, m.non_checkable_non_item_citations) == (1, 1)
    assert (m.routing_accuracy, m.routing_omitted) == (0, 8)


def test_non_checkable_non_item_citations_count_ids(gold_f: GoldSet) -> None:
    m = score_run(gold_f, f_run(gold_f, [], [("vague", [1, 9]), ("vague", [1])]))
    assert (m.non_checkable_entries, m.non_checkable_non_item_citations, m.routing_omitted) == (2, 3, 7)


@pytest.mark.parametrize(
    ("claims", "expected"),
    [([[2, 3], [5, 3]], (2, 0, 0, 0)), ([[2, 4], [5, 4]], (2, 2, 2, 0)), ([[2, 1], [5, 1]], (0, 0, 0, 2))],
)
def test_context_counters_count_claim_row_pairs(
    gold_x: GoldSet, claims: list[list[int]], expected: tuple[int, int, int, int]
) -> None:
    # AMB-12: two findings citing the same context row or non-item segment count twice.
    m = score_run(gold_x, x_run(gold_x, claims))
    assert kinds(m, "syn_a") == ["finding", "finding"]
    counters = (
        m.neutral_context_citations,
        m.neutral_context_out_of_scope,
        m.neutral_context_high_stakes,
        m.non_item_context_citations,
    )
    assert counters == expected


# ---------------------------------------------------------------- M9-M11 correction pairs and repeats


def test_a_correction_pair_changes_nothing(gold_f: GoldSet, gold_x: GoldSet) -> None:
    group = FlaggedGroup("correction", ("fx_01", "fx_03"))
    flagged = score_run(gold_f, f_run(gold_f, [[4]]), flagged_groups=[group])
    plain = score_run(gold_f, f_run(gold_f, [[4]]))
    assert flagged.recall == plain.recall == F(1, 2)
    expected = FlaggedGroupStatus("correction", ("fx_01", "fx_03"), {"fx_01": False, "fx_03": True}, None)
    assert flagged.flagged_groups == (expected,)
    assert dataclasses.replace(flagged, flagged_groups=()) == plain
    x_pair = FlaggedGroup("correction", ("x_01", "x_04"))
    assert score_run(gold_x, x_run(gold_x, [[2]]), flagged_groups=[x_pair]).recall == F(1, 3)


def test_a_repeat_is_one_unit(gold_f: GoldSet) -> None:
    group = FlaggedGroup("repeat", ("fx_01", "fx_03"))
    m = score_run(gold_f, f_run(gold_f, [[4], [2]]), flagged_groups=[group])
    assert (m.n_checkable_units, m.recall, m.precision, m.duplicate_claims, m.routing_accuracy) == (1, 1, 1, 1, F(2, 7))
    status = m.flagged_groups[0]
    assert (dict(status.found), status.unit_found) == ({"fx_01": True, "fx_03": True}, True)
    assert dict(m.per_conversation) == {"syn_900": CC(1, 0, 1)}
    plain = score_run(gold_f, f_run(gold_f, [[4], [2]]))
    assert (plain.n_checkable_units, plain.recall, plain.duplicate_claims) == (2, 1, 0)


def test_a_repeat_and_the_high_confidence_view(gold_x: GoldSet) -> None:
    group = FlaggedGroup("repeat", ("x_01", "x_04"))
    m = score_run(gold_x, x_run(gold_x, [[2], [5]]), flagged_groups=[group])
    assert (m.n_checkable_units, m.found_units, m.duplicate_claims, m.recall, m.precision) == (2, 1, 1, F(1, 2), 1)
    hc = m.high_confidence
    assert (hc.n_checkable_units, hc.found_units, hc.recall, hc.precision) == (1, 0, 0, None)
    plain = score_run(gold_x, x_run(gold_x, [[2], [5]]))
    assert (plain.recall, plain.duplicate_claims) == (F(2, 3), 0)


def test_a_repeat_of_high_rows_is_one_high_confidence_unit(tmp_path: Path) -> None:
    conversation = make_conversation(
        "syn_r", ["hello", "Lyon is in France", "Lyon is a French city", "Oslo is in Norway"]
    )
    items = [make_item(f"r_0{n}", conversation, n + 1, "CHECKABLE", "GENERAL") for n in (1, 2, 3)]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    group = FlaggedGroup("repeat", ("r_01", "r_02"))
    m = score_run(gold, {"syn_r": extraction(gold, "syn_r", [[2]])}, flagged_groups=[group])
    hc = m.high_confidence
    assert (m.n_checkable_units, hc.n_checkable_units, hc.found_units, hc.recall) == (2, 2, 1, F(1, 2))


def test_precision_counts_units_under_a_repeat(gold_f: GoldSet) -> None:
    group = FlaggedGroup("repeat", ("fx_01", "fx_03"))
    m = score_run(gold_f, f_run(gold_f, [[4], [2], [1]]), flagged_groups=[group])
    assert (m.found_units, m.fp_claims, m.precision, m.f1, m.recall) == (1, 1, F(1, 2), F(2, 3), 1)


def test_flagged_groups_are_reported_in_input_order(tmp_path: Path) -> None:
    conversation = make_conversation("syn_q", ["hello"] + [f"fact {n} about rivers" for n in range(1, 5)])
    items = [make_item(f"q_{n}", conversation, n + 1, "CHECKABLE", "GENERAL") for n in range(1, 5)]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    groups = [FlaggedGroup("repeat", ("q_4", "q_3")), FlaggedGroup("correction", ("q_2", "q_1"))]
    m = score_run(gold, {"syn_q": extraction(gold, "syn_q", [[5]])}, flagged_groups=groups)
    assert m.flagged_groups == (
        FlaggedGroupStatus("repeat", ("q_4", "q_3"), {"q_4": True, "q_3": False}, True),
        FlaggedGroupStatus("correction", ("q_2", "q_1"), {"q_2": False, "q_1": False}, None),
    )


def test_an_uncited_repeat_member_is_omitted_in_routing(gold_f: GoldSet) -> None:
    group = FlaggedGroup("repeat", ("fx_01", "fx_03"))
    m = score_run(gold_f, f_run(gold_f, [[2]]), flagged_groups=[group])
    assert (m.recall, route(m, "syn_900", 4)) == (1, "omitted")
    status = m.flagged_groups[0]
    assert (dict(status.found), status.unit_found) == ({"fx_01": True, "fx_03": False}, True)
    assert m.recall_spelled == SliceRecall(1, 1, F(1))  # a slice row is found when its unit is found


def test_score_conversation_applies_repeat_groups(gold_f: GoldSet) -> None:
    group = FlaggedGroup("repeat", ("fx_01", "fx_03"))
    score = score_conversation(gold_f, extraction(gold_f, "syn_900", [[4], [2]]), flagged_groups=[group])
    assert [outcome.kind for outcome in score.claim_outcomes] == ["finding", "duplicate"]
    assert score.counts == CC(1, 0, 1)


@pytest.mark.parametrize(
    "groups",
    [
        [FlaggedGroup("repeat", ("x_01", "x_99"))],
        [FlaggedGroup("repeat", ("x_01", "x_06"))],
        [FlaggedGroup("correction", ("x_01", "x_02"))],
        [FlaggedGroup("repeat", ("x_01", "x_03"))],
        [FlaggedGroup("repeat", ("x_01", "x_04")), FlaggedGroup("correction", ("x_04", "x_01"))],
    ],
)
def test_flagged_groups_are_validated(gold_x: GoldSet, groups: list[FlaggedGroup]) -> None:
    with pytest.raises(ValueError):
        score_run(gold_x, x_run(gold_x), flagged_groups=groups)
    with pytest.raises(ValueError):
        score_conversation(gold_x, extraction(gold_x, "syn_a"), flagged_groups=groups)


@pytest.mark.parametrize(
    "args",
    [("merge", ("x_01", "x_04")), ("repeat", ("x_01",)), ("repeat", ("x_01", "x_01")), ("repeat", ["x_01", "x_04"])],
)
def test_flagged_group_records_check_themselves(args: tuple) -> None:
    with pytest.raises(ValueError):
        FlaggedGroup(*args)


def test_flagged_groups_must_be_flagged_group_records(gold_x: GoldSet) -> None:
    with pytest.raises(TypeError):
        score_run(gold_x, x_run(gold_x), flagged_groups=[("repeat", ("x_01", "x_04"))])  # type: ignore[list-item]


# ---------------------------------------------------------------- P1-P4 extraction metrics


def test_precision_counts_rows_over_rows_plus_fp_claims(gold_f: GoldSet) -> None:
    m = score_run(gold_f, f_run(gold_f, [[2, 4], [1]]))
    assert (m.precision, m.f1) == (F(2, 3), F(4, 5))
    m = score_run(gold_f, f_run(gold_f, [[7]]))
    assert m.precision is None
    check = gate_a(m).checks[1]
    assert (check.name, check.value, check.passed, gate_a(m).passed) == ("precision", None, False, False)


def test_no_claims_anywhere(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x))
    assert (m.recall, m.precision, m.f1) == (0, None, 0)
    assert (m.routing_accuracy, m.routing_omitted) == (0, 8)


def _gold_with_95_units(tmp_path: Path) -> GoldSet:
    conversation = make_conversation("syn_p4", [f"fact {n} about the river delta" for n in range(1, 96)])
    items = [make_item(f"p4_{n:03d}", conversation, n, "CHECKABLE", "GENERAL") for n in range(1, 96)]
    return load_gold_set(write_version(tmp_path, [conversation], items))


@pytest.mark.parametrize(("found", "met"), [(81, True), (80, False)])
def test_the_recall_target_is_reported_never_gated(tmp_path: Path, found: int, met: bool) -> None:
    gold = _gold_with_95_units(tmp_path)
    m = score_run(gold, {"syn_p4": extraction(gold, "syn_p4", [list(range(1, found + 1))])})
    assert (m.recall, m.recall_target_met) == (F(found, 95), met)
    assert [check.name for check in gate_a(m).checks] == ["recall", "precision", "routing_accuracy"]


def test_the_recall_target_boundary_is_inclusive(tmp_path: Path) -> None:
    conversation = make_conversation("syn_t", [f"fact {n} about the river delta" for n in range(1, 21)])
    items = [make_item(f"t_{n:02d}", conversation, n, "CHECKABLE", "GENERAL") for n in range(1, 21)]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    m = score_run(gold, {"syn_t": extraction(gold, "syn_t", [list(range(1, 18))])})
    assert (m.recall, m.recall_target_met) == (F(17, 20), True)


def test_undefined_ratios_are_none(tmp_path: Path) -> None:
    conversation = make_conversation("syn_o", ["I quite like the euro"])
    items = [make_item("o_01", conversation, 1, "OPINION", "MARKET_FINANCE")]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    m = score_run(gold, {"syn_o": extraction(gold, "syn_o")})
    assert (m.recall, m.recall_target_met, m.precision, m.f1, m.checkability_binary_f1) == (None,) * 5
    assert m.out_of_scope_leak_rate is None and m.n_out_of_scope == 0
    with pytest.raises(ValueError):
        m.summary()


def test_no_rows_and_no_high_rows_report_none(tmp_path: Path) -> None:
    # AMB-15 / L6: macro F1 is None when no class is defined, and so is every other 0/0 ratio.
    empty = make_conversation("syn_e", ["hello", "bye"])
    gold = load_gold_set(write_version(tmp_path / "e", [empty], []))
    m = score_run(gold, {"syn_e": extraction(gold, "syn_e")})
    assert (m.n_rows, m.routing_accuracy, m.checkability_binary_f1, m.checkability_macro_f1) == (0, None, None, None)
    assert set(m.checkability_class_f1.values()) == {None}
    assert (m.recall, m.precision, m.f1) == (None, None, None)
    assert not gate_a(m).passed and not gate_s(m).passed
    low = make_conversation("syn_l", ["hello", "Oslo is in Norway", "I like the euro"])
    items = [
        make_item("l_01", low, 2, "CHECKABLE", "GENERAL", confidence="low"),
        make_item("l_02", low, 3, "OPINION", "MARKET_FINANCE", confidence="low"),
    ]
    gold = load_gold_set(write_version(tmp_path / "l", [low], items))
    m = score_run(gold, {"syn_l": extraction(gold, "syn_l", [[2]], [("opinion", [3])])})
    hc = m.high_confidence
    assert m.routing_accuracy == 1
    assert (hc.n_rows, hc.routing_accuracy) == (0, None)
    assert (hc.checkability_binary_f1, hc.checkability_macro_f1) == (None, None)
    assert set(hc.checkability_class_f1.values()) == {None}
    assert (hc.recall, hc.precision, hc.f1) == (None, None, None)


# ---------------------------------------------------------------- L1-L6 Layer B


@pytest.mark.parametrize(
    ("claims", "nc", "routes", "accuracy"),
    [
        ([], [("vague", [3])], {3: "misrouted"}, F(0)),
        ([], [("outOfScope", [6])], {6: "misrouted"}, F(0)),
        ([], [("opinion", [4])], {4: "misrouted"}, F(0)),
        ([], [("opinion", [3, 6])], {3: "correct", 6: "misrouted"}, F(1, 8)),
        ([], [("prediction", [2])], {2: "misrouted"}, F(0)),
        ([[2]], [("vague", [2])], {2: "correct"}, F(1, 8)),
        ([], [("vague", [1])], {2: "omitted", 3: "omitted"}, F(0)),
    ],
)
def test_routing_outcomes(gold_x: GoldSet, claims: list, nc: list, routes: dict[int, str], accuracy: F) -> None:
    m = score_run(gold_x, x_run(gold_x, claims, nc))
    assert {number: route(m, "syn_a", number) for number in routes} == routes
    assert m.routing_accuracy == accuracy
    assert (m.leaked_in_scope_non_checkable, m.leaked_out_of_scope) == ((), ())
    assert m.routing_correct + m.routing_leaked + m.routing_misrouted + m.routing_omitted == m.n_rows == 8


def test_a_non_checkable_entry_never_finds_a_row(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [], [("prediction", [2])]))
    assert (m.found_units, route(m, "syn_a", 2), label(m, "syn_a", 2)) == (0, "misrouted", "prediction")
    m = score_run(gold_x, x_run(gold_x, [[2]], [("vague", [2])]))
    assert (m.found_units, route(m, "syn_a", 2), label(m, "syn_a", 2)) == (1, "correct", "")


def test_e1_without_the_request_entry(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f, nc=E1_NC[1:]))
    assert (m.routing_accuracy, route(m, "syn_900", 3), m.checkability_macro_f1) == (F(3, 7), "omitted", F(13, 42))


def test_a_duplicate_turns_its_other_rows_into_context(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f, [*E1_CLAIMS[:5], [2, 5]]))
    assert (m.found_units, m.fp_claims, m.precision, m.f1) == (2, 1, F(2, 3), F(4, 5))
    assert (m.routing_accuracy, m.routing_correct, m.routing_leaked, m.routing_omitted) == (F(5, 7), 5, 1, 1)
    assert [route(m, "syn_900", n) for n in (5, 7, 8)] == ["correct", "leaked", "omitted"]
    assert (m.duplicate_claims, m.neutral_context_citations) == (2, 3)
    assert (m.in_scope_non_checkable_leak_rate, m.out_of_scope_leak_rate, m.high_stakes_leaks) == (0, F(1, 3), 0)
    assert m.checkability_macro_f1 == F(47, 60)


def test_a_claim_wins_a_shared_segment(gold_x: GoldSet, gold_f: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [[3]], [("opinion", [3])]))
    assert (route(m, "syn_a", 3), m.fp_claims, m.leaked_in_scope_non_checkable) == ("leaked", 1, ("x_02",))
    assert route(score_run(gold_f, run_e1(gold_f)), "syn_900", 5) == "leaked"


@pytest.mark.parametrize(
    ("kind", "outcome", "accuracy"), [("opinion", "correct", F(1, 7)), ("outOfScope", "misrouted", 0)]
)
def test_checkability_first_then_scope(gold_f: GoldSet, kind: str, outcome: str, accuracy: F) -> None:
    m = score_run(gold_f, f_run(gold_f, [], [(kind, [8])]))
    assert (route(m, "syn_900", 8), m.routing_accuracy, m.n_high_stakes) == (outcome, accuracy, 2)


def test_conflicting_kinds_use_the_existential_reading(gold_x: GoldSet, gold_f: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [], [("vague", [3]), ("opinion", [3])]))
    assert (route(m, "syn_a", 3), label(m, "syn_a", 3), m.routing_accuracy) == ("correct", "opinion", F(1, 8))
    assert m.conflicting_non_checkable_rows == ("x_02",)
    m = score_run(gold_x, x_run(gold_x, [], [("vague", [3]), ("request", [3])]))
    assert (route(m, "syn_a", 3), label(m, "syn_a", 3), m.conflicting_non_checkable_rows) == (
        "misrouted",
        "vague",
        ("x_02",),
    )
    e1 = score_run(gold_f, run_e1(gold_f, nc=[*E1_NC, ("vague", [3])]))
    assert (e1.routing_accuracy, e1.checkability_macro_f1, e1.conflicting_non_checkable_rows) == (
        F(4, 7),
        F(47, 84),
        ("fx_02",),
    )


def test_conflicting_rows_are_listed_whatever_their_outcome(gold_x: GoldSet) -> None:
    # L4 pins the current reading of NEEDS-OWNER(AMB-1): a row a claim predicts CHECKABLE is listed too.
    m = score_run(gold_x, x_run(gold_x, [[2]], [("vague", [2]), ("opinion", [2])]))
    assert (route(m, "syn_a", 2), label(m, "syn_a", 2), m.conflicting_non_checkable_rows) == ("correct", "", ("x_01",))
    m = score_run(gold_x, x_run(gold_x, [[3]], [("vague", [3]), ("opinion", [3])]))
    assert (route(m, "syn_a", 3), m.fp_claims, m.conflicting_non_checkable_rows) == ("leaked", 1, ("x_02",))


def test_class_true_positives_sum_to_routing_correct(gold_f: GoldSet, gold_fp: GoldSet, gold_x: GoldSet) -> None:
    cases = [
        (gold_f, run_e1(gold_f)),
        (gold_fp, run_e1t(gold_fp)),
        (gold_x, run_xm(gold_x)),
        (gold_x, run_xw(gold_x)),
        (gold_x, run_xp(gold_x)),
        (gold_x, x_run(gold_x, [], [("vague", [3]), ("opinion", [3])])),
    ]
    for gold, run in cases:
        m = score_run(gold, run)
        labels = {k: v for score in m.conversation_scores.values() for k, v in score.labels.items()}
        assert sum(1 for item in gold.items if labels[item.key] == item.reason_kind) == m.routing_correct
        assert m.checkability_binary_f1 == m.checkability_class_f1[""]


# ---------------------------------------------------------------- K1-K3 leaks


def test_leaks_are_counted_per_row(gold_f: GoldSet, gold_x: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f, [*E1_CLAIMS, [7]]))
    assert (m.leaked_out_of_scope, m.leak_only_claims) == (("fx_06", "fx_07"), 2)
    m = score_run(gold_x, x_run(gold_x, [[3]]))
    assert (m.leaked_in_scope_non_checkable, m.in_scope_non_checkable_leak_rate) == (("x_02",), F(1, 2))
    m = score_run(gold_x, x_run(gold_x, [[4]]))
    assert (m.leaked_out_of_scope, m.out_of_scope_leak_rate) == (("x_03",), F(1, 3))


def test_routing_leaked_is_both_leak_lists(gold_f: GoldSet, gold_x: GoldSet) -> None:
    for gold, run in ((gold_f, run_e1(gold_f)), (gold_x, x_run(gold_x, [[3, 4], [6]])), (gold_x, run_xw(gold_x))):
        m = score_run(gold, run)
        assert m.routing_leaked == len(m.leaked_in_scope_non_checkable) + len(m.leaked_out_of_scope)


def test_high_stakes_leaks_per_domain(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f, [[2], [2, 3], [4], [1], [7], [5, 8], [6]]))
    assert (m.high_stakes_leaks, dict(m.high_stakes_leaks_by_domain)) == (2, high_stakes(1, 0, 1))
    assert m.routing_accuracy == F(3, 7)


def _private_gold(tmp_path: Path) -> GoldSet:
    conversation = make_conversation(
        "syn_k",
        [
            "hello there",
            "my neighbour ran a marathon in York last year",
            "my cousin was fined for speeding on that road",
            "my sister's charity run was in the local paper on Monday",
            "the river Quellin flows into the Varn Sea",
        ],
    )
    items = [
        make_item("k_01", conversation, 2, "CHECKABLE", "GENERAL", private_individual="yes"),
        make_item("k_02", conversation, 3, "CHECKABLE", "LEGAL", private_individual="yes"),
        make_item("k_03", conversation, 4, "CHECKABLE", "NEWS", private_individual="yes"),
        make_item("k_04", conversation, 5, "CHECKABLE", "GENERAL"),
    ]
    return load_gold_set(write_version(tmp_path, [conversation], items))


def test_out_of_scope_leaks_by_bucket(tmp_path: Path) -> None:
    gold = _private_gold(tmp_path)
    m = score_run(gold, {"syn_k": extraction(gold, "syn_k", [[2], [3], [4]])})
    assert list(m.out_of_scope_leaks_by_domain) == list(OUT_OF_SCOPE_BUCKETS)
    assert dict(m.out_of_scope_leaks_by_domain) == buckets(NEWS=1, LEGAL=1, PRIVATE_INDIVIDUAL=1)
    assert (m.high_stakes_leaks, dict(m.high_stakes_leaks_by_domain)) == (1, high_stakes(legal=1))
    assert m.leak_only_claims == 3


# ---------------------------------------------------------------- FL1-FL4 failures


def test_a_failed_conversation_scores_as_empty_output(gold_x: GoldSet) -> None:
    b_claims, b_nc = [[1]], [("outOfScope", [2]), ("vague", [4])]
    failed = score_run(gold_x, x_run(gold_x, b=b_claims, b_nc=b_nc, a_fail="timeout"))
    empty = score_run(gold_x, x_run(gold_x, b=b_claims, b_nc=b_nc))
    for m in (failed, empty):
        figures = (m.recall, m.precision, m.f1, m.routing_accuracy, m.out_of_scope_leak_rate)
        assert figures == (F(1, 3), 1, F(1, 2), F(3, 8), 0)
    assert failed.failed_conversations == (FailedConversation("syn_a", "timeout", ("x_03", "x_05")),)
    assert empty.failed_conversations == ()
    assert set(failed.conversation_scores["syn_a"].labels.values()) == {"omitted"}


def test_a_retry_that_is_also_invalid(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, a_fail="invalid_output"))
    score = m.conversation_scores["syn_a"]
    assert (score.found_rows, score.leaked_rows, score.failure) == (frozenset(), frozenset(), "invalid_output")
    assert {route(m, "syn_a", n) for n in range(2, 7)} == {"omitted"}
    assert (m.leaked_out_of_scope, m.failures_by_kind["invalid_output"]) == ((), 1)
    assert m.failed_conversations == (FailedConversation("syn_a", "invalid_output", ("x_03", "x_05")),)


def test_failures_are_listed_sorted_with_every_kind(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, a_fail="server_error", b_fail="cut_off"))
    assert [failure.source_id for failure in m.failed_conversations] == ["syn_a", "syn_b"]
    assert dict(m.failures_by_kind) == {**NO_FAILURES, "cut_off": 1, "server_error": 1}
    assert dict(m.per_conversation) == {"syn_a": CC(0, 0, 2), "syn_b": CC(0, 0, 1)}


def test_reported_claim_id_lists_are_sorted_whatever_the_file_order(tmp_path: Path) -> None:
    conversations, items = fixture_x()
    gold = load_gold_set(write_version(tmp_path, list(reversed(conversations)), list(reversed(items))))
    m = score_run(gold, x_run(gold, [[3, 4, 6]], b_fail="timeout"))
    assert (m.leaked_in_scope_non_checkable, m.leaked_out_of_scope) == (("x_02",), ("x_03", "x_05"))
    m = score_run(gold, x_run(gold, [], [("vague", [3, 4, 6]), ("opinion", [3, 4, 6])]))
    assert m.conflicting_non_checkable_rows == ("x_02", "x_03", "x_05")
    m = score_run(gold, x_run(gold, a_fail="timeout", b_fail="server_error"))
    assert m.failed_conversations == (
        FailedConversation("syn_a", "timeout", ("x_03", "x_05")),
        FailedConversation("syn_b", "server_error", ("x_07",)),
    )


def test_a_failure_is_never_turned_into_claims(gold_fp: GoldSet) -> None:
    score = score_conversation(gold_fp, ExtractionFailure("syn_901", "server_error"))
    assert (score.claim_outcomes, score.failure, score.counts) == ((), "server_error", CC(0, 0, 1))
    assert set(score.routing.values()) == {"omitted"}


def test_results_must_cover_exactly_the_gold_conversations(gold_f: GoldSet, gold_fp: GoldSet) -> None:
    with pytest.raises(ValueError):
        score_run(gold_fp, {"syn_900": run_e1(gold_fp)["syn_900"]})
    with pytest.raises(ValueError):
        score_run(gold_f, {**run_e1(gold_f), "cap_1": ExtractionFailure("cap_1", "timeout")})


def test_a_result_must_sit_under_its_own_key(gold_x: GoldSet) -> None:
    run = x_run(gold_x)
    with pytest.raises(ValueError):
        score_run(gold_x, {"syn_a": run["syn_b"], "syn_b": run["syn_a"]})


@pytest.mark.parametrize(
    "outside",
    [
        Extraction("syn_a", (ExtractedClaim(0, "x", ("seg_009",)),), ()),
        Extraction("syn_a", (ExtractedClaim(0, "x", ("syn_a:seg_002",)),), ()),
        Extraction("syn_a", (), (NonCheckableEntry(0, ("seg_007",), "vague"),)),
    ],
)
def test_an_id_outside_the_conversation_is_a_harness_error(gold_x: GoldSet, outside: Extraction) -> None:
    with pytest.raises(ValueError):
        score_run(gold_x, {"syn_a": outside, "syn_b": x_run(gold_x)["syn_b"]})
    with pytest.raises(ValueError):
        score_conversation(gold_x, outside)


def test_bad_inputs(gold_x: GoldSet) -> None:
    with pytest.raises(ValueError):
        score_conversation(gold_x, ExtractionFailure("syn_z", "timeout"))
    with pytest.raises(TypeError):
        score_run(gold_x, [("syn_a", None)])  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        score_run(gold_x, {"syn_a": None, "syn_b": None})  # type: ignore[dict-item]
    with pytest.raises(TypeError):
        score_conversation(gold_x, "syn_a")  # type: ignore[arg-type]


# ---------------------------------------------------------------- RU1-RU3 rows used


def test_gates_read_all_rows(gold_f: GoldSet) -> None:
    m = score_run(gold_f, run_e1(gold_f))
    assert {check.name: check.value for check in gate_a(m).checks} == {
        "recall": 1,
        "precision": F(1, 2),
        "routing_accuracy": F(4, 7),
    }
    assert (m.high_confidence.precision, m.high_confidence.routing_accuracy) == (F(1, 3), F(1, 2))


def test_a_finding_on_low_rows_adds_nothing_to_the_high_view(gold_x: GoldSet) -> None:
    m = score_run(gold_x, x_run(gold_x, [[5, 1]]))
    assert (m.found_units, m.fp_claims, kinds(m, "syn_a")) == (1, 0, ["finding"])
    hc = m.high_confidence
    assert (hc.found_units, hc.fp_claims, hc.recall, hc.precision) == (0, 0, 0, None)


def test_the_high_view_counts_fp_claims_on_non_item_or_high_in_scope_rows(tmp_path: Path) -> None:
    conversation = make_conversation(
        "syn_h",
        ["hello", "the Rhine is longer than the Thames", "I find the yen confusing", "that clinic shuts at noon"],
    )
    items = [
        make_item("h_01", conversation, 2, "CHECKABLE", "GENERAL"),
        make_item("h_02", conversation, 3, "OPINION", "MARKET_FINANCE", confidence="low"),
        make_item("h_03", conversation, 4, "CHECKABLE", "HEALTH"),
    ]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    m = score_run(gold, {"syn_h": extraction(gold, "syn_h", [[3], [3, 4], [1]])})
    assert (m.fp_claims, m.leaked_in_scope_non_checkable, m.leaked_out_of_scope) == (3, ("h_02",), ("h_03",))
    hc = m.high_confidence
    assert (hc.n_rows, hc.fp_claims) == (2, 1)
    assert (hc.leaked_in_scope_non_checkable, hc.leaked_out_of_scope, hc.high_stakes_leaks) == ((), ("h_03",), 1)


def test_a_low_confidence_high_stakes_leak_is_gated_but_not_in_the_high_view(tmp_path: Path) -> None:
    conversation = make_conversation(
        "syn_hs", ["hello", "Oslo is in Norway", "tenants can withhold rent after 14 days"]
    )
    items = [
        make_item("hs_01", conversation, 2, "CHECKABLE", "GENERAL"),
        make_item("hs_02", conversation, 3, "CHECKABLE", "LEGAL", confidence="low"),
    ]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    m = score_run(gold, {"syn_hs": extraction(gold, "syn_hs", [[2], [3]])})
    assert (m.high_stakes_leaks, dict(m.high_stakes_leaks_by_domain)) == (1, high_stakes(legal=1))
    assert (m.n_out_of_scope, m.out_of_scope_leak_rate) == (1, 1)
    assert [check.value for check in gate_s(m).checks] == [1, 1]  # §5.3: gates use all rows
    hc = m.high_confidence
    assert (hc.high_stakes_leaks, hc.n_high_stakes, hc.n_out_of_scope, hc.out_of_scope_leak_rate) == (0, 0, 0, None)


def test_a_row_with_digits_and_a_spelled_number_is_in_both_slices(tmp_path: Path) -> None:
    conversation = make_conversation(
        "syn_s", ["hello", "it was 7 degrees, about seven", "gold is at 2,400 an ounce", "it is snowing in Leeds"]
    )
    items = [
        make_item("s_01", conversation, 2, "CHECKABLE", "WEATHER"),
        make_item("s_02", conversation, 3, "CHECKABLE", "MARKET_FINANCE"),
        make_item("s_03", conversation, 4, "CHECKABLE", "WEATHER"),
    ]
    gold = load_gold_set(write_version(tmp_path, [conversation], items))
    m = score_run(gold, {"syn_s": extraction(gold, "syn_s", [[2]])})
    assert m.digits_and_spelled_rows == 1
    assert (m.recall_digits, m.recall_spelled, m.recall_no_number) == (
        SliceRecall(2, 1, F(1, 2)),
        SliceRecall(1, 1, F(1)),
        SliceRecall(1, 0, F(0)),
    )


@pytest.mark.parametrize(
    ("text", "digits", "spelled"),
    [
        ("gold is at 2,400 an ounce", True, False),
        ("53°C", True, False),
        ("minus three in Oslo and snowing", False, True),
        ("one twenty-nine", False, True),
        ("Twenty-two", False, True),
        ("Oh yeah, it is one of the busiest", False, False),
        ("no one", False, False),
        ("it was 7 degrees, about seven", True, True),
        ("one point oh", False, False),
        ("THREE", False, True),
        ("it's ten-ish", False, True),
        ("threefold", False, False),
        ("", False, False),
        # RU3 tokens are runs of a-z: digits, underscores, apostrophes and non-ASCII letters split words.
        ("twenty20", True, True),
        ("5million", True, True),
        ("ten_k", False, True),
        ("the nine's down", False, True),
        ("seven" + chr(0xE9), False, True),
        # AMB-20: "dozen", "half" and ordinals are not number words.
        ("a dozen eggs", False, False),
        ("half a percent", False, False),
        ("the third quarter", False, False),
        ("the first of May", False, False),
    ],
)
def test_number_slices(text: str, digits: bool, spelled: bool) -> None:
    assert (has_digits(text), has_spelled_number(text)) == (digits, spelled)


def test_the_spelled_number_vocabulary_is_the_ru3_list() -> None:
    words = (
        "zero two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety "
        "hundred thousand million billion trillion"
    )
    assert SPELLED_NUMBER_WORDS == frozenset(words.split())


def test_only_ascii_digits_count() -> None:
    arabic_indic = "".join(chr(0x660 + n) for n in (5, 3))
    assert arabic_indic.isdigit() and not has_digits(arabic_indic)


# ---------------------------------------------------------------- GT2-GT3 gates


def test_gate_a_bounds_are_inclusive(gold_x: GoldSet) -> None:
    m = score_run(gold_x, run_xp(gold_x))
    edge = gate_a(dataclasses.replace(m, recall=F(3, 4), precision=F(3, 4), routing_accuracy=F(7, 10)))
    assert (edge.gate, edge.passed, [check.passed for check in edge.checks]) == ("A", True, [True, True, True])
    assert [(check.name, check.limit, check.op) for check in edge.checks] == [
        ("recall", F(3, 4), ">="),
        ("precision", F(3, 4), ">="),
        ("routing_accuracy", F(7, 10), ">="),
    ]
    assert not gate_a(dataclasses.replace(m, routing_accuracy=F(69, 100))).passed
    assert not gate_a(dataclasses.replace(m, recall=F(71, 95))).passed
    assert gate_a(dataclasses.replace(m, recall=F(72, 95))).passed
    assert not gate_a(dataclasses.replace(m, precision=None)).passed


def test_gate_a_fails_on_e1(gold_f: GoldSet) -> None:
    result = gate_a(score_run(gold_f, run_e1(gold_f)))
    assert (result.passed, [check.passed for check in result.checks]) == (False, [True, False, False])


def test_gate_s(gold_x: GoldSet, gold_f: GoldSet) -> None:
    m = score_run(gold_x, run_xp(gold_x))
    edge = gate_s(dataclasses.replace(m, high_stakes_leaks=2, out_of_scope_leak_rate=F(9, 45)))
    assert (edge.gate, edge.passed) == ("S", True)
    assert [(check.name, check.limit, check.op) for check in edge.checks] == [
        ("high_stakes_leaks", 2, "<="),
        ("out_of_scope_leak_rate", F(1, 5), "<="),
    ]
    assert not gate_s(dataclasses.replace(m, high_stakes_leaks=3)).passed
    assert not gate_s(dataclasses.replace(m, out_of_scope_leak_rate=F(10, 45))).passed
    assert not gate_s(dataclasses.replace(m, out_of_scope_leak_rate=None)).passed
    e1 = gate_s(score_run(gold_f, run_e1(gold_f)))
    assert (e1.passed, e1.checks[1].value) == (False, F(2, 3))
    xm = gate_s(score_run(gold_x, run_xm(gold_x)))
    assert (xm.passed, xm.checks[1].value) == (False, F(1, 3))


def test_gates_refuse_floats(gold_x: GoldSet) -> None:
    with pytest.raises(TypeError):
        gate_a(dataclasses.replace(score_run(gold_x, run_xp(gold_x)), recall=0.75))


# ---------------------------------------------------------------- G7-G8 fields scoring never reads


def _bootstrap_against_empty(gold: GoldSet, m: RunMetrics) -> object:
    empty = score_run(gold, f_run(gold))
    return paired_bootstrap(m.per_conversation, empty.per_conversation, seed=3, resamples=200)


def test_deliberately_false_never_changes_a_result(tmp_path: Path) -> None:
    conversations, items = fixture_f(plus=True)
    plain_gold = load_gold_set(write_version(tmp_path / "plain", conversations, items))
    for row in items:
        row["deliberately_false"] = "yes" if row["deliberately_false"] == "no" else "no"
    flipped_gold = load_gold_set(write_version(tmp_path / "flipped", conversations, items))
    plain = score_run(plain_gold, run_e1t(plain_gold))
    flipped = score_run(flipped_gold, run_e1t(flipped_gold))
    assert plain == flipped
    assert _bootstrap_against_empty(plain_gold, plain) == _bootstrap_against_empty(flipped_gold, flipped)


def test_spans_claim_text_dates_and_settings_are_never_read(tmp_path: Path) -> None:
    conversations, items = fixture_f(plus=True)
    plain_gold = load_gold_set(write_version(tmp_path / "plain", conversations, items))
    for conversation in conversations:
        conversation["call_date"] = "2024-12-31"
        conversation["setting"] = "a different synthetic setting"
    for row in items:
        if row["span_text"]:
            row["span_text"] = "a different span"
            row["claim_text"] = "a rewritten proposition | with a second part on 2024-12-31"
    rewritten_gold = load_gold_set(write_version(tmp_path / "rewritten", conversations, items))
    assert rewritten_gold.conversation("syn_900").call_date != plain_gold.conversation("syn_900").call_date
    plain = score_run(plain_gold, run_e1t(plain_gold))
    failure = ExtractionFailure("syn_901", "timeout")
    rewritten_run = f_run(rewritten_gold, E1_CLAIMS, E1_NC, syn_901=failure, text="x")
    assert score_run(rewritten_gold, rewritten_run) == plain


def test_claim_ids_speaker_ids_and_id_order_are_never_read(gold_f: GoldSet) -> None:
    # M1: matching reads segment ids only, as a set: never claimId, speakerId or the order of the ids.
    raw = json.loads(output_json(E1_CLAIMS, E1_NC))
    for entry in raw["claims"]:
        entry.update(claimId="claim_1", speakerId="SPEAKER_00", checkability={"isCheckable": True, "reason": "r"})
    for entry in raw["nonCheckable"]:
        entry.update(claimId="claim_1", speakerId="SPEAKER_00", text="t", reason="r")
    tagged = parse_extraction(json.dumps(raw), source_id="syn_900", request_segment_ids=gold_f.segment_ids("syn_900"))
    assert {claim.claim_id for claim in tagged.claims} == {"claim_1"}
    plain = score_run(gold_f, run_e1(gold_f))
    assert score_run(gold_f, {"syn_900": tagged}) == plain
    assert score_run(gold_f, run_e1(gold_f, [list(reversed(ids)) for ids in E1_CLAIMS])) == plain


# ---------------------------------------------------------------- stubs, summary, records


def test_fidelity_stubs_raise() -> None:
    for stub in (claim_fidelity, per_proposition_recall):
        with pytest.raises(NotImplementedError, match="AMB-4"):
            stub()
        with pytest.raises(NotImplementedError) as caught:
            stub("claim", gold="row")
        assert str(caught.value) == FIDELITY_PENDING
    names = [field.name for field in dataclasses.fields(RunMetrics)]
    assert not [name for name in names if "fidelity" in name or "proposition" in name]


def test_summary(gold_x: GoldSet) -> None:
    m = score_run(gold_x, run_xp(gold_x))
    summary = m.summary()
    assert (summary.f1, summary.routing_accuracy, summary.high_stakes_leaks) == (1, 1, 0)
    assert dict(summary.per_conversation) == dict(m.per_conversation)
    with pytest.raises(ValueError):
        dataclasses.replace(m, f1=None).summary()
    with pytest.raises(ValueError):
        dataclasses.replace(m, routing_accuracy=None).summary()


def test_per_conversation_covers_every_conversation_in_sorted_order(tmp_path: Path) -> None:
    conversations, items = fixture_x()
    gold = load_gold_set(write_version(tmp_path, list(reversed(conversations)), items))
    m = score_run(gold, run_xm(gold))
    assert list(m.per_conversation) == list(m.conversation_scores) == ["syn_a", "syn_b"]
    assert pooled_f1(list(m.per_conversation.values())) == m.f1 == F(4, 5)


def test_a_conversation_without_checkable_rows_stays_in_per_conversation(tmp_path: Path) -> None:
    # B1: every gold conversation goes into the bootstrap, so its FP claims count in condition 2 as in F1.
    a = make_conversation("syn_c1", ["hello", "the river Quellin flows into the Varn Sea"])
    b = make_conversation("syn_c2", ["hello", "I quite like the euro"])
    items = [make_item("c_01", a, 2, "CHECKABLE", "GENERAL"), make_item("c_02", b, 2, "OPINION", "MARKET_FINANCE")]
    gold = load_gold_set(write_version(tmp_path, [a, b], items))
    m = score_run(gold, {"syn_c1": extraction(gold, "syn_c1", [[2]]), "syn_c2": extraction(gold, "syn_c2", [[1]])})
    assert dict(m.per_conversation) == {"syn_c1": CC(1, 0, 1), "syn_c2": CC(0, 1, 0)}
    assert pooled_f1(list(m.per_conversation.values())) == m.f1 == F(2, 3)


def test_metrics_are_immutable(gold_x: GoldSet) -> None:
    m = score_run(gold_x, run_xm(gold_x))
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.recall = F(1)  # type: ignore[misc]
    with pytest.raises(TypeError):
        m.per_conversation["syn_c"] = CC(0, 0, 0)  # type: ignore[index]
    with pytest.raises(TypeError):
        m.checkability_class_f1["opinion"] = F(1)  # type: ignore[index]
    with pytest.raises(TypeError):
        m.conversation_scores["syn_a"].routing[key("syn_a", 2)] = "omitted"  # type: ignore[index]
    assert isinstance(m.failed_conversations, tuple) and isinstance(m.leaked_out_of_scope, tuple)
