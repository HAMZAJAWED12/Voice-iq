"""The margin and the decision rule: H4 spec G4-25 to G4-29, section 5's decision rows and section 8's test_decision
row, with the H4a brief's amendment 2 (S2 §1, §2, §3.6, §3.13, §8).

Candidate results are `gates.CandidateResult` values built here. Their run metrics are H1 `RunMetrics` scored on the
synthetic fixture X gold set, with the margin figures replaced, so every number is synthetic. Two rows score a whole
in-memory schedule from `_h4_fixtures.make_view` through `gates.evaluate`.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Mapping
from fractions import Fraction
from types import MappingProxyType
from typing import Any, get_args

import pytest

from app.evaluation.bakeoff import stats
from app.evaluation.bakeoff.decision import (
    CHALLENGER,
    DEFAULT,
    MARGIN_READING,
    MODEL_SETTING,
    NO_MARGIN_NOTE,
    NO_SHARE_NOTE,
    REVISION_SETTING,
    RULE5_OPTIONS,
    RULES,
    THIRD,
    Decision,
    Rule,
    decide,
    margin,
    revision_setting,
    settings,
)
from app.evaluation.bakeoff.gates import GATES, CandidateResult, Gate, OperatorValues, ReportInputs, evaluate
from app.evaluation.bakeoff.gold import GoldSet
from app.evaluation.bakeoff.launch import CANDIDATE_IDS
from app.evaluation.bakeoff.runner import DeterminismShare, determinism_shares
from app.evaluation.bakeoff.scoring import RunMetrics, score_run
from app.evaluation.bakeoff.stats import (
    BOOTSTRAP_RESAMPLES,
    BootstrapResult,
    ConversationCounts,
    MarginCondition,
    MarginResult,
)
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._fixtures import load_x, run_xm
from app.evaluation.tests._h4_fixtures import h4_gold, make_view, passing_evidence
from app.evaluation.tests._runner_fixtures import VALID_OPERATOR, block_processes

Q4B, Q35, Q8B = CANDIDATE_IDS
SHA = "f" * 64
NO_INPUTS = ReportInputs(MappingProxyType({}), None, None, None, None, None)
ZERO_SHARES = {candidate: DeterminismShare(candidate, 0, 1000) for candidate in CANDIDATE_IDS}


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> RunMetrics:
    gold = load_x(tmp_path_factory.mktemp("gold_x"))
    return score_run(gold, run_xm(gold))


def counts(*tps: int, n: int = 10) -> dict[str, ConversationCounts]:
    """One synthetic conversation per tp value, no false positives."""
    return {f"syn_{index:02d}": ConversationCounts(tp, 0, n) for index, tp in enumerate(tps, start=1)}


def metric(
    base: RunMetrics,
    f1: Fraction | None = Fraction(4, 5),
    routing: Fraction | None = Fraction(4, 5),
    leaks: int = 0,
    per: Mapping[str, ConversationCounts] | None = None,
) -> RunMetrics:
    return dataclasses.replace(
        base,
        f1=f1,
        routing_accuracy=routing,
        high_stakes_leaks=leaks,
        per_conversation=counts(8, 8, 8, 8) if per is None else per,
    )


def result(
    candidate: str,
    base: RunMetrics,
    *,
    status: str = "ran",
    failing: tuple[str, ...] = (),
    not_measured: tuple[str, ...] = (),
    thinking: bool = False,
    metrics: Mapping[int, RunMetrics] | None = None,
) -> CandidateResult:
    """A candidate result with the given gate statuses. A not-loaded one fails L and measures nothing else (G4-7)."""
    loaded = status == "ran"
    gates: dict[str, Gate] = {}
    for gate in GATES:
        if not loaded:
            state = "fail" if gate == "L" else "not_measured"
        else:
            state = "fail" if gate in failing else "not_measured" if gate in not_measured else "pass"
        gates[gate] = Gate(gate, state, (), ())
    information_only = ("gate_L",) if not loaded else ("thinking",) if thinking else ()
    return CandidateResult(
        candidate=candidate,
        status=status,
        information_only=information_only,
        thinking_pre_run=None if not loaded else "seen" if thinking else "off",
        thinking_in_runs=MappingProxyType({}),
        metrics=MappingProxyType({} if not loaded else dict(metrics or {r: metric(base) for r in (1, 2, 3)})),
        gates=MappingProxyType(gates),
        probe_readings=MappingProxyType({}),
        sessions=(),
        memory=None,
        latency=None,
        cutoffs=MappingProxyType({}),
        unconstrained=None,
        contended=None,
        runtime=MappingProxyType({}),
        voided=(),
    )


def trio(base: RunMetrics, **per_candidate: dict[str, Any]) -> dict[str, CandidateResult]:
    """Results for the three candidates; keyword names q4b, q35 and q8b carry each one's options."""
    names = {"q4b": Q4B, "q35": Q35, "q8b": Q8B}
    return {names[key]: result(names[key], base, **per_candidate.get(key, {})) for key in ("q4b", "q35", "q8b")}


def fake_margin(holds: bool, *, resamples: int = BOOTSTRAP_RESAMPLES, failing: tuple[int, ...] = ()) -> MarginResult:
    """A run-1 margin result whose conditions are all `holds`, except the `failing` numbers."""
    bootstrap = BootstrapResult(Fraction(1, 50), Fraction(1, 100), True, resamples, 7, Fraction(95, 100), 4, "3.x")
    conditions = tuple(
        MarginCondition(number, f"syn_{number}", ok, None, ok, "synthetic")
        for number in (1, 2, 3, 4)
        for ok in [holds and number not in failing]
    )
    return MarginResult(False, conditions, all(c.holds for c in conditions), bootstrap)


def refuse(name: str) -> Any:
    def stub(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"stats.{name} must not be called")

    return stub


def no_stats_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stats, "determinism_variant", refuse("determinism_variant"))
    monkeypatch.setattr(stats, "margin_case1", refuse("margin_case1"))


# ---------------------------------------------------------------- constants and signatures


def test_the_constants() -> None:
    assert (DEFAULT, CHALLENGER, THIRD) == CANDIDATE_IDS == ("Qwen/Qwen3-4B", "Qwen/Qwen3.5-4B", "Qwen/Qwen3-8B")
    assert MARGIN_READING == "literal"
    assert RULES == get_args(Rule) == ("rule 2", "rule 3", "rule 4 via rule 3", "rule 4 (Qwen3-8B)", "rule 5")
    assert [option[:3] for option in RULE5_OPTIONS] == ["(a)", "(b)"]
    assert (MODEL_SETTING, REVISION_SETTING) == ("FACTCHECK_LLM_MODEL", "FACTCHECK_LLM_REVISION")


def test_margin_defaults_to_the_s2_resample_count() -> None:
    """H4a brief section 12, amendment 2: the `margin` default only; `score_and_report`'s is test_entry's (A6)."""
    parameter = inspect.signature(margin).parameters["resamples"]
    assert parameter.default is stats.BOOTSTRAP_RESAMPLES
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert stats.BOOTSTRAP_RESAMPLES == 10_000


# ---------------------------------------------------------------- the 24-row grid (G4-28)

HOLDS, FAILS, NONE = "holds", "fails", "none"
GRID: list[tuple[bool, bool, str, bool, Rule, str | None]] = [
    # pinnable 4B, pinnable 3.5-4B, margin, pinnable 8B -> rule, pinned
    (True, True, HOLDS, True, "rule 3", Q35),
    (True, True, HOLDS, False, "rule 3", Q35),
    (True, True, FAILS, True, "rule 2", Q4B),
    (True, True, FAILS, False, "rule 2", Q4B),
    (True, True, NONE, True, "rule 2", Q4B),
    (True, True, NONE, False, "rule 2", Q4B),
    (True, False, HOLDS, True, "rule 2", Q4B),
    (True, False, HOLDS, False, "rule 2", Q4B),
    (True, False, FAILS, True, "rule 2", Q4B),
    (True, False, FAILS, False, "rule 2", Q4B),
    (True, False, NONE, True, "rule 2", Q4B),
    (True, False, NONE, False, "rule 2", Q4B),
    (False, True, HOLDS, True, "rule 4 via rule 3", Q35),
    (False, True, HOLDS, False, "rule 4 via rule 3", Q35),
    (False, True, FAILS, True, "rule 4 (Qwen3-8B)", Q8B),
    (False, True, FAILS, False, "rule 5", None),
    (False, True, NONE, True, "rule 4 (Qwen3-8B)", Q8B),
    (False, True, NONE, False, "rule 5", None),
    (False, False, HOLDS, True, "rule 4 (Qwen3-8B)", Q8B),
    (False, False, HOLDS, False, "rule 5", None),
    (False, False, FAILS, True, "rule 4 (Qwen3-8B)", Q8B),
    (False, False, FAILS, False, "rule 5", None),
    (False, False, NONE, True, "rule 4 (Qwen3-8B)", Q8B),
    (False, False, NONE, False, "rule 5", None),
]


def test_the_grid_has_every_combination_once() -> None:
    assert len({row[:4] for row in GRID}) == len(GRID) == 24


@pytest.mark.parametrize(("p4b", "p35", "margin_case", "p8b", "rule", "pinned"), GRID)
def test_the_decision_grid(
    base: RunMetrics, p4b: bool, p35: bool, margin_case: str, p8b: bool, rule: Rule, pinned: str | None
) -> None:
    results = trio(
        base,
        q4b={} if p4b else {"failing": ("T",)},
        q35={} if p35 else {"failing": ("A",)},
        q8b={} if p8b else {"failing": ("M",)},
    )
    found = None if margin_case == NONE else fake_margin(margin_case == HOLDS)
    outcome = decide(results, found, False)
    assert (outcome.rule, outcome.pinned) == (rule, pinned)
    assert dict(outcome.pinnable) == {Q4B: p4b, Q35: p35, Q8B: p8b}
    assert outcome.margin is found and outcome.variant is False
    assert outcome.margin_conforming is (None if found is None else True)
    assert outcome.margin_note == (NO_MARGIN_NOTE if found is None else None)
    if p4b:
        assert outcome.pinned != Q8B  # Qwen3-8B is never pinned while Qwen3-4B is pinnable


# ---------------------------------------------------------------- named rows (spec section 5)


def test_the_g4_28_example_and_its_steps(base: RunMetrics) -> None:
    """Qwen3-4B fails T, Qwen3.5-4B passes every gate but margin condition 1 fails, Qwen3-8B passes all seven."""
    outcome = decide(trio(base, q4b={"failing": ("T",)}), fake_margin(True, failing=(1,)), False)
    assert (outcome.rule, outcome.pinned, outcome.changes_baseline) == ("rule 4 (Qwen3-8B)", Q8B, True)
    assert outcome.steps == (
        "rule 1: Qwen/Qwen3-4B is not pinnable: gate T fail",
        "rule 1: Qwen/Qwen3.5-4B is pinnable",
        "rule 1: Qwen/Qwen3-8B is pinnable",
        "margin (literal reading, case 1, run 1): does not hold (conditions not holding: 1)",
        "rule 3: Qwen/Qwen3.5-4B is pinnable and the margin does not hold",
        "rule 4: Qwen/Qwen3-4B is not pinnable; Qwen/Qwen3-8B is pinnable",
        "pinned Qwen/Qwen3-8B under rule 4 (Qwen3-8B)",
    )


def test_the_steps_of_rule_3_rule_2_and_rule_5(base: RunMetrics) -> None:
    won = decide(trio(base), fake_margin(True), False)
    assert won.steps[3:] == (
        "margin (literal reading, case 1, run 1): holds",
        "rule 3: Qwen/Qwen3.5-4B is pinnable and the margin holds",
        "pinned Qwen/Qwen3.5-4B under rule 3",
    )
    kept = decide(trio(base), fake_margin(False), False)
    assert kept.steps[-2:] == ("rule 2: Qwen/Qwen3-4B is pinnable", "pinned Qwen/Qwen3-4B under rule 2")
    failing = {"failing": ("M",), "not_measured": ("J",)}
    none = decide(trio(base, q4b=failing, q35=failing, q8b=failing), None, False)
    assert none.steps[0] == "rule 1: Qwen/Qwen3-4B is not pinnable: gate M fail; gate J not_measured"
    assert none.steps[-2:] == (
        "rule 4: Qwen/Qwen3-4B is not pinnable; Qwen/Qwen3-8B is not pinnable",
        "rule 5: nothing is pinned; the senior sponsor takes option (a) or (b)",
    )


def test_every_candidate_failing_a_gate_gives_rule_5(base: RunMetrics) -> None:
    outcome = decide(trio(base, q4b={"failing": ("T",)}, q35={"failing": ("S",)}, q8b={"failing": ("L",)}), None, False)
    assert (outcome.rule, outcome.pinned, outcome.changes_baseline) == ("rule 5", None, None)
    assert not any(outcome.pinnable.values())


def test_amb_129_an_ineligible_qwen3_4b_takes_rule_4(base: RunMetrics) -> None:
    """Qwen3-4B shows thinking at pre-run but passes all seven gates (AMB-129, the implemented reading)."""
    results = trio(base, q4b={"thinking": True}, q35={"failing": ("C",)})
    assert results[Q4B].passes_every_gate and not results[Q4B].eligible
    outcome = decide(results, fake_margin(False), False)
    assert (outcome.rule, outcome.pinned) == ("rule 4 (Qwen3-8B)", Q8B)
    assert outcome.steps[0] == "rule 1: Qwen/Qwen3-4B is not pinnable: information only (thinking)"
    via_3 = decide(trio(base, q4b={"thinking": True}), fake_margin(True), False)
    assert (via_3.rule, via_3.pinned) == ("rule 4 via rule 3", Q35)
    alone = decide(trio(base, q4b={"thinking": True}, q35={"failing": ("C",)}, q8b={"failing": ("T",)}), None, False)
    assert (alone.rule, alone.pinned) == ("rule 5", None)


@pytest.mark.parametrize("challenger", [{"failing": ("L",)}, {"thinking": True}], ids=["fails L", "thinking"])
@pytest.mark.parametrize(
    ("q4b", "q8b", "rule", "pinned"),
    [
        ({}, {}, "rule 2", Q4B),
        ({"failing": ("T",)}, {}, "rule 4 (Qwen3-8B)", Q8B),
        ({"failing": ("T",)}, {"failing": ("M",)}, "rule 5", None),
    ],
)
def test_a_challenger_failing_l_or_thinking_is_never_pinned_on_a_holding_margin(
    base: RunMetrics,
    challenger: dict[str, Any],
    q4b: dict[str, Any],
    q8b: dict[str, Any],
    rule: Rule,
    pinned: str | None,
) -> None:
    outcome = decide(trio(base, q4b=q4b, q35=challenger, q8b=q8b), fake_margin(True), False)
    assert (outcome.rule, outcome.pinned) == (rule, pinned)
    assert outcome.margin is not None and outcome.margin.all_hold and outcome.pinnable[Q35] is False


def test_only_qwen3_5_4b_pinnable_and_missing_the_margin_gives_rule_5(base: RunMetrics) -> None:
    outcome = decide(trio(base, q4b={"failing": ("T",)}, q8b={"failing": ("M",)}), fake_margin(False), False)
    assert (outcome.rule, outcome.pinned) == ("rule 5", None)


def test_both_4b_pinnable_with_the_margin_holding_pins_the_challenger(base: RunMetrics) -> None:
    outcome = decide(trio(base), fake_margin(True), False)
    assert (outcome.rule, outcome.pinned, outcome.changes_baseline) == ("rule 3", Q35, True)


def test_qwen3_4b_and_8b_pinnable_with_the_margin_failing_pins_qwen3_4b(base: RunMetrics) -> None:
    outcome = decide(trio(base, q35={"failing": ("J",)}), fake_margin(False), False)
    assert (outcome.rule, outcome.pinned, outcome.changes_baseline) == ("rule 2", Q4B, False)


def test_qwen3_8b_not_loaded_leaves_the_others_decided_normally(base: RunMetrics) -> None:
    outcome = decide(trio(base, q8b={"status": "not_loaded"}), fake_margin(False), False)
    assert (outcome.rule, outcome.pinned) == ("rule 2", Q4B)
    assert outcome.steps[2] == "rule 1: Qwen/Qwen3-8B is not pinnable: did not load"
    alone = decide(trio(base, q4b={"failing": ("T",)}, q8b={"status": "not_loaded"}), fake_margin(False), False)
    assert (alone.rule, alone.pinned) == ("rule 5", None)


def test_a_missing_candidate_is_never_pinnable(base: RunMetrics) -> None:
    results = trio(base)
    del results[Q8B]
    outcome = decide(results, fake_margin(False), False)
    assert outcome.pinnable[Q8B] is False and outcome.steps[2] == "rule 1: Qwen/Qwen3-8B is not pinnable: no result"


# ---------------------------------------------------------------- not loaded (AMB-130, AMB-141)


def test_qwen3_5_4b_not_loaded_with_qwen3_4b_passing_gives_rule_2(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_stats_calls(monkeypatch)
    results = trio(base, q35={"status": "not_loaded"})
    shares = {Q4B: ZERO_SHARES[Q4B], Q8B: ZERO_SHARES[Q8B]}
    found = margin(results, shares, 7)
    assert found == (None, None, "Qwen/Qwen3.5-4B did not load")
    outcome = decide(results, found[0], found[1])
    assert (outcome.rule, outcome.pinned) == ("rule 2", Q4B)
    assert outcome.margin_note == "Qwen/Qwen3.5-4B did not load" and outcome.margin_conforming is None
    assert outcome.steps[3] == "margin (literal reading, case 1): not computed: Qwen/Qwen3.5-4B did not load"


def test_qwen3_5_4b_not_loaded_with_qwen3_4b_failing_pins_qwen3_8b(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_stats_calls(monkeypatch)
    results = trio(base, q4b={"failing": ("T",)}, q35={"status": "not_loaded"})
    found = margin(results, {Q4B: ZERO_SHARES[Q4B], Q8B: ZERO_SHARES[Q8B]}, 7)
    outcome = decide(results, found[0], found[1])
    assert (outcome.rule, outcome.pinned, outcome.changes_baseline) == ("rule 4 (Qwen3-8B)", Q8B, True)


def test_qwen3_5_4b_not_loaded_with_a_qwen3_4b_share_gives_no_variant(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_stats_calls(monkeypatch)
    results = trio(base, q35={"status": "not_loaded"})
    shares = {Q4B: DeterminismShare(Q4B, 3, 1000), Q8B: ZERO_SHARES[Q8B]}
    assert margin(results, shares, 7) == (None, None, "Qwen/Qwen3.5-4B did not load")


@pytest.mark.parametrize(
    ("q8b", "rule", "pinned"), [({}, "rule 4 (Qwen3-8B)", Q8B), ({"failing": ("T",)}, "rule 5", None)]
)
def test_amb_130_qwen3_4b_not_loaded_means_the_challenger_cannot_win(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch, q8b: dict[str, Any], rule: Rule, pinned: str | None
) -> None:
    no_stats_calls(monkeypatch)
    results = trio(base, q4b={"status": "not_loaded"}, q8b=q8b)
    found = margin(results, {Q35: ZERO_SHARES[Q35], Q8B: ZERO_SHARES[Q8B]}, 7)
    assert found == (None, None, "Qwen/Qwen3-4B did not load")
    outcome = decide(results, found[0], found[1])
    assert outcome.pinnable[Q35] is True
    assert (outcome.rule, outcome.pinned) == (rule, pinned)
    assert outcome.margin_note == "Qwen/Qwen3-4B did not load"


def test_a_ran_4b_model_with_no_share_gets_no_margin(base: RunMetrics, monkeypatch: pytest.MonkeyPatch) -> None:
    no_stats_calls(monkeypatch)
    results = trio(base)
    found = margin(results, {Q4B: ZERO_SHARES[Q4B]}, 7)
    assert found == (None, None, NO_SHARE_NOTE)
    assert decide(results, None, None).margin_note == NO_SHARE_NOTE


# ---------------------------------------------------------------- the variant (G4-26)


@pytest.mark.parametrize(
    ("q4b", "q35", "q8b", "variant"),
    [(0, 0, 0, False), (0, 3, 0, True), (3, 0, 0, True), (0, 0, 5, False)],
    ids=["0/0", "0/x", "x/0", "8B only"],
)
def test_the_variant_table(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch, q4b: int, q35: int, q8b: int, variant: bool
) -> None:
    calls: list[tuple[int, int, bool]] = []

    def spy(challenger: Any, default: Any, *, variant: bool, seed: int, resamples: int) -> MarginResult:
        calls.append((len(challenger), len(default), variant))
        return dataclasses.replace(fake_margin(False), variant=variant)

    monkeypatch.setattr(stats, "margin_case1", spy)
    shares = {
        Q4B: DeterminismShare(Q4B, q4b, 1000),
        Q35: DeterminismShare(Q35, q35, 1000),
        Q8B: DeterminismShare(Q8B, q8b, 1000),
    }
    found, found_variant, why_not = margin(trio(base), shares, 7)
    assert found_variant is variant and why_not is None
    assert found is not None and found.variant is variant
    assert calls == ([(3, 3, True)] if variant else [(1, 1, False)])


def test_the_margin_spy_sees_the_runs_in_order_the_seed_and_the_resamples(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def spy(challenger: Any, default: Any, *, variant: bool, seed: int, resamples: int) -> MarginResult:
        seen.update(challenger=[s.f1 for s in challenger], default=[s.f1 for s in default], seed=seed)
        seen["resamples"] = resamples
        return dataclasses.replace(fake_margin(True), variant=variant)

    monkeypatch.setattr(stats, "margin_case1", spy)
    challenger = {r: metric(base, f1=Fraction(80 + r, 100)) for r in (1, 2, 3)}
    default = {r: metric(base, f1=Fraction(70 + r, 100)) for r in (1, 2, 3)}
    results = trio(base, q4b={"metrics": default}, q35={"metrics": challenger})
    margin(results, ZERO_SHARES, 4242)
    assert seen == {
        "challenger": [Fraction(81, 100)],
        "default": [Fraction(71, 100)],
        "seed": 4242,
        "resamples": 10_000,
    }
    assert seen["resamples"] is stats.BOOTSTRAP_RESAMPLES
    margin(results, {**ZERO_SHARES, Q35: DeterminismShare(Q35, 1, 1000)}, 99, resamples=200)
    assert seen == {
        "challenger": [Fraction(81, 100), Fraction(82, 100), Fraction(83, 100)],
        "default": [Fraction(71, 100), Fraction(72, 100), Fraction(73, 100)],
        "seed": 99,
        "resamples": 200,
    }


def test_the_variant_needs_three_scored_runs_with_no_fallback(base: RunMetrics) -> None:
    results = trio(base, q35={"metrics": {1: metric(base)}})
    with pytest.raises(ValueError, match="no scored run"):
        margin(results, {**ZERO_SHARES, Q4B: DeterminismShare(Q4B, 2, 1000)}, 7, resamples=200)


def test_under_the_variant_holding_in_runs_2_and_3_but_not_on_the_mean_does_not_hold(base: RunMetrics) -> None:
    per = counts(9, 9, 8, 8)
    challenger = {
        1: metric(base, f1=Fraction(1, 2), per=per),
        2: metric(base, f1=Fraction(83, 100), per=per),
        3: metric(base, f1=Fraction(83, 100), per=per),
    }
    results = trio(base, q35={"metrics": challenger})
    found, variant, _ = margin(results, {**ZERO_SHARES, Q35: DeterminismShare(Q35, 4, 1000)}, 7, resamples=200)
    assert variant is True and found is not None and found.variant is True
    first = found.conditions[0]
    assert (first.number, first.holds_on_value, first.runs_holding, first.holds) == (1, False, 2, False)
    assert not found.all_hold


# ---------------------------------------------------------------- margin wiring (G4-27)


def tie_results(base: RunMetrics, challenger_per: Mapping[str, ConversationCounts]) -> dict[str, CandidateResult]:
    """F1 166/200 against 160/200 (exactly 3/100), routing exactly 1/50 lower, equal leaks (spec section 5)."""
    challenger = metric(base, f1=Fraction(166, 200), routing=Fraction(39, 50), leaks=1, per=challenger_per)
    default = metric(base, f1=Fraction(160, 200), routing=Fraction(4, 5), leaks=1, per=counts(*[8] * 20))
    return trio(
        base, q4b={"metrics": {r: default for r in (1, 2, 3)}}, q35={"metrics": {r: challenger for r in (1, 2, 3)}}
    )


def test_a_margin_tie_holds_conditions_1_3_and_4_and_rests_on_condition_2(base: RunMetrics) -> None:
    found, variant, why_not = margin(tie_results(base, counts(*[9] * 20)), ZERO_SHARES, 7, resamples=200)
    assert variant is False and why_not is None and found is not None
    assert [c.holds for c in found.conditions] == [True, True, True, True]
    assert found.all_hold


def test_a_lower_bound_of_exactly_zero_fails_condition_2(base: RunMetrics) -> None:
    found, _, _ = margin(tie_results(base, counts(9, *[8] * 19)), ZERO_SHARES, 7, resamples=200)
    assert found is not None and isinstance(found.bootstrap, BootstrapResult)
    assert found.bootstrap.lower_bound == 0 and found.bootstrap.observed_difference > 0
    assert [c.holds for c in found.conditions] == [True, False, True, True]


def test_a_margin_at_200_resamples_is_not_conforming_and_never_switches_the_pin(base: RunMetrics) -> None:
    results = tie_results(base, counts(*[9] * 20))
    found, variant, _ = margin(results, ZERO_SHARES, 7, resamples=200)
    assert found is not None and found.all_hold
    outcome = decide(results, found, variant)
    assert (outcome.rule, outcome.pinned) == ("rule 2", Q4B)
    assert outcome.margin_conforming is False
    assert outcome.margin_note == "not S2 §8 conforming (resamples 200, needs 10,000)"
    assert outcome.steps[3] == (
        "margin (literal reading, case 1, run 1): not S2 §8 conforming (resamples 200, needs 10,000), "
        "so it is treated as not holding"
    )


def test_the_same_margin_at_10000_resamples_conforms_and_pins_the_challenger(base: RunMetrics) -> None:
    results = tie_results(base, counts(*[9] * 20))
    found, variant, _ = margin(results, ZERO_SHARES, 7)
    outcome = decide(results, found, variant)
    assert (outcome.rule, outcome.pinned, outcome.margin_conforming, outcome.margin_note) == ("rule 3", Q35, True, None)


def test_a_variant_margin_below_10000_resamples_is_not_conforming(base: RunMetrics) -> None:
    results = tie_results(base, counts(*[9] * 20))
    found, variant, _ = margin(results, {**ZERO_SHARES, Q4B: DeterminismShare(Q4B, 1, 1000)}, 7, resamples=200)
    assert variant is True and found is not None
    outcome = decide(results, found, variant)
    assert outcome.margin_conforming is False and outcome.pinned == Q4B
    assert outcome.steps[3].startswith("margin (literal reading, case 1, runs 1 to 3 (S2 §3.13 variant)): not S2")


def test_qwen3_4b_failing_a_gate_with_f1_not_lower_does_not_pin_the_challenger(base: RunMetrics) -> None:
    """Case 2 was not adopted: F1 not lower and no more leaks is not case 1 (S2 §8, §12 row 16)."""
    same = {r: metric(base, f1=Fraction(4, 5), leaks=0) for r in (1, 2, 3)}
    results = trio(base, q4b={"failing": ("T",), "metrics": same}, q35={"metrics": same})
    found, variant, _ = margin(results, ZERO_SHARES, 7)
    assert found is not None and not found.conditions[0].holds and not found.all_hold
    assert found.conditions[3].holds
    assert decide(results, found, variant).pinned == Q8B
    no_8b = trio(base, q4b={"failing": ("T",), "metrics": same}, q35={"metrics": same}, q8b={"failing": ("M",)})
    assert decide(no_8b, found, variant).rule == "rule 5"


@pytest.mark.parametrize(("variant_share", "run"), [(0, 1), (1, 2)])
def test_a_summary_that_raises_makes_the_margin_not_computable(
    base: RunMetrics, monkeypatch: pytest.MonkeyPatch, variant_share: int, run: int
) -> None:
    monkeypatch.setattr(stats, "margin_case1", refuse("margin_case1"))
    challenger = {r: metric(base, f1=None if r == run else Fraction(9, 10)) for r in (1, 2, 3)}
    results = trio(base, q35={"metrics": challenger})
    shares = {**ZERO_SHARES, Q35: DeterminismShare(Q35, variant_share, 1000)}
    found, variant, why_not = margin(results, shares, 7)
    note = f"margin not computable: Qwen/Qwen3.5-4B run {run} has an undefined F1 or routing accuracy (0/0)"
    assert (found, variant, why_not) == (None, variant_share > 0, note)
    outcome = decide(results, None, variant)
    assert outcome.margin_note == note and (outcome.rule, outcome.pinned) == ("rule 2", Q4B)


def test_the_margin_runs_whatever_the_gates(base: RunMetrics) -> None:
    failing = {"failing": ("L", "M", "J", "C", "T", "A", "S")}
    found, _, why_not = margin(trio(base, q4b=failing, q35=failing), ZERO_SHARES, 7, resamples=200)
    assert found is not None and why_not is None


def test_decide_refuses_a_variant_the_margin_was_not_computed_under(base: RunMetrics) -> None:
    with pytest.raises(ValueError, match="variant"):
        decide(trio(base), fake_margin(True), True)


# ---------------------------------------------------------------- the decision value and the settings (G4-29)


@pytest.mark.parametrize(
    ("pinned", "rule", "changes"), [(Q35, "rule 3", True), (Q4B, "rule 2", False), (None, "rule 5", None)]
)
def test_changes_baseline(pinned: str | None, rule: Rule, changes: bool | None) -> None:
    outcome = Decision(rule, pinned, None, None, None, None, {}, ())
    assert outcome.changes_baseline is changes
    assert Decision("rule 4 (Qwen3-8B)", Q8B, None, None, None, None, {}, ()).changes_baseline is True


def test_a_decision_is_frozen_and_consistent() -> None:
    outcome = Decision("rule 2", Q4B, None, None, False, None, {Q4B: True}, ["step"])
    assert isinstance(outcome.pinnable, MappingProxyType) and outcome.steps == ("step",)
    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.rule = "rule 5"
    with pytest.raises(ValueError, match="rule 5"):
        Decision("rule 5", Q4B, None, None, None, None, {}, ())
    with pytest.raises(ValueError, match="rule 5"):
        Decision("rule 2", None, None, None, None, None, {}, ())
    with pytest.raises(ValueError, match="not an S2"):
        Decision("rule 1", Q4B, None, None, None, None, {}, ())


def test_revision_setting_joins_the_revision_and_the_sha256() -> None:
    assert revision_setting("synthetic-revision-0001", SHA) == "synthetic-revision-0001@sha256:" + SHA


@pytest.mark.parametrize(
    ("revision", "sha"),
    [("", SHA), ("   ", SHA), ("rev", "F" * 64), ("rev", "f" * 63), ("rev", "f" * 65), ("rev", "g" * 64)],
)
def test_revision_setting_refuses_a_blank_revision_or_a_malformed_sha256(revision: str, sha: str) -> None:
    with pytest.raises(ValueError):
        revision_setting(revision, sha)


def test_settings_are_the_pinned_id_and_the_revision_string() -> None:
    values = settings(Q35, "synthetic-revision-0002", SHA)
    assert dict(values) == {
        "FACTCHECK_LLM_MODEL": "Qwen/Qwen3.5-4B",
        "FACTCHECK_LLM_REVISION": "synthetic-revision-0002@sha256:" + SHA,
    }
    assert all(isinstance(value, str) for value in values.values())
    with pytest.raises(TypeError):
        values["FACTCHECK_LLM_MODEL"] = Q4B
    with pytest.raises(ValueError, match="candidate"):
        settings("Qwen/Qwen3-1.7B", "rev", SHA)


# ---------------------------------------------------------------- a whole schedule through gates.evaluate


@pytest.fixture(scope="module")
def gold(tmp_path_factory: pytest.TempPathFactory) -> GoldSet:
    return h4_gold(tmp_path_factory.mktemp("gold_h4"))


def test_a_clean_schedule_with_identical_candidates_ends_at_rule_2(gold: GoldSet) -> None:
    view = make_view(gold)
    results = evaluate(view, gold, passing_evidence(view), OperatorValues.from_manifest(VALID_OPERATOR), NO_INPUTS)
    shares = determinism_shares(view)
    assert all(share.share == 0 for share in shares.values())
    found, variant, why_not = margin(results, shares, 7)
    assert variant is False and why_not is None and found is not None
    assert not found.conditions[0].holds  # identical outputs: an F1 difference of 0
    outcome = decide(results, found, variant)
    assert (outcome.rule, outcome.pinned, outcome.changes_baseline) == ("rule 2", Q4B, False)
    assert all(outcome.pinnable.values())


def test_a_schedule_without_qwen3_5_4b_never_calls_the_margin(gold: GoldSet, monkeypatch: pytest.MonkeyPatch) -> None:
    no_stats_calls(monkeypatch)
    view = make_view(gold, not_loaded=(Q35,))
    results = evaluate(view, gold, passing_evidence(view), OperatorValues.from_manifest(VALID_OPERATOR), NO_INPUTS)
    shares = determinism_shares(view)
    assert Q35 not in shares
    found = margin(results, shares, 7)
    assert found == (None, None, "Qwen/Qwen3.5-4B did not load")
    outcome = decide(results, *found[:2])
    assert (outcome.rule, outcome.pinned) == ("rule 2", Q4B) and outcome.pinnable[Q35] is False
