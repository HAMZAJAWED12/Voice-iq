"""Bootstrap, run averaging and the case-1 margin test: H1 spec rules B1-B9 and GT1 (S2 §8, §3.13)."""

from __future__ import annotations

import platform
import random
from fractions import Fraction as F

import pytest

from app.evaluation.bakeoff.stats import (
    BOOTSTRAP_CONFIDENCE,
    BOOTSTRAP_RESAMPLES,
    HIGH_STAKES_LEAK_MAX,
    MARGIN_F1,
    MARGIN_ROUTING,
    OOS_LEAK_RATE_MAX,
    PRECISION_FLOOR,
    RECALL_FLOOR,
    RECALL_TARGET,
    ROUTING_FLOOR,
    BootstrapResult,
    ConversationCounts,
    MultiRunBootstrap,
    RunSummary,
    average_runs,
    bootstrap_indices,
    determinism_variant,
    f1_from_counts,
    holds_over_runs,
    margin_case1,
    paired_bootstrap,
    paired_bootstrap_multi_run,
    percentile_lower_bound,
    pooled_f1,
)

CC = ConversationCounts

# B1 counts.
CHALLENGER = {"c1": CC(2, 0, 2), "c2": CC(1, 1, 2), "c3": CC(0, 0, 1)}
DEFAULT = {"c1": CC(1, 0, 2), "c2": CC(1, 1, 2), "c3": CC(0, 1, 1)}
# Dominance: the challenger finds everything with no FP; the default misses one unit everywhere.
STRONG = {f"d{i}": CC(n, 0, n) for i, n in enumerate((1, 2, 3, 4, 2), start=1)}
WEAK = {f"d{i}": CC(n - 1, 0, n) for i, n in enumerate((1, 2, 3, 4, 2), start=1)}
ZERO = {f"d{i}": CC(0, 0, n) for i, n in enumerate((1, 2, 3, 4, 2), start=1)}


def _reference(challenger: dict, default: dict, *, seed: int, resamples: int) -> tuple[F, list[F]]:
    """An independent slow implementation of S2 §8 as specified: sorted keys, one random.Random(seed), n draws of
    randrange(n) per resample, pooled F1 with repeats counted."""
    keys = sorted(challenger)
    ch = [challenger[k] for k in keys]
    de = [default[k] for k in keys]
    rng = random.Random(seed)
    diffs = []
    for _ in range(resamples):
        idx = [rng.randrange(len(keys)) for _ in range(len(keys))]
        diffs.append(pooled_f1(ch, idx) - pooled_f1(de, idx))
    return pooled_f1(ch) - pooled_f1(de), diffs


# ---------------------------------------------------------------- constants and counts


def test_thresholds_are_exact_fractions() -> None:
    assert (RECALL_FLOOR, PRECISION_FLOOR, ROUTING_FLOOR) == (F(3, 4), F(3, 4), F(7, 10))
    assert (HIGH_STAKES_LEAK_MAX, OOS_LEAK_RATE_MAX, RECALL_TARGET) == (2, F(1, 5), F(17, 20))
    assert (MARGIN_F1, MARGIN_ROUTING) == (F(3, 100), F(1, 50))
    assert (BOOTSTRAP_RESAMPLES, BOOTSTRAP_CONFIDENCE) == (10_000, F(95, 100))
    for value in (RECALL_FLOOR, PRECISION_FLOOR, ROUTING_FLOOR, OOS_LEAK_RATE_MAX, RECALL_TARGET, MARGIN_F1):
        assert isinstance(value, F)


def test_f1_from_counts() -> None:
    assert f1_from_counts(2, 1, 0) == F(4, 5)
    assert f1_from_counts(0, 0, 3) == 0
    assert f1_from_counts(0, 0, 0) == 0 and isinstance(f1_from_counts(0, 0, 0), F)
    assert f1_from_counts(F(5, 3), 0, F(1, 3)) == F(10, 11)


def test_conversation_counts() -> None:
    counts = CC(2, 1, 3)
    assert counts.fn == 1
    assert CC(F(5, 3), F(2, 3), 2).fn == F(1, 3)
    assert CC(0, 4, 0).fn == 0


@pytest.mark.parametrize(
    ("args", "error"),
    [
        ((True, 0, 1), ValueError),
        ((0, False, 1), ValueError),
        ((0, 0, True), ValueError),
        ((3, 0, 2), ValueError),
        ((-1, 0, 2), ValueError),
        ((0, -1, 2), ValueError),
        ((0.5, 0, 2), TypeError),
        ((0, 0, 2.0), TypeError),
        ((F(7, 3), 0, 2), ValueError),
    ],
)
def test_conversation_counts_reject_bad_values(args: tuple, error: type[Exception]) -> None:
    with pytest.raises(error):
        CC(*args)


# ---------------------------------------------------------------- B1 inputs


def test_inputs_must_pair_up() -> None:
    with pytest.raises(ValueError):
        paired_bootstrap(CHALLENGER, {"c1": DEFAULT["c1"], "c2": DEFAULT["c2"]}, seed=1, resamples=10)
    with pytest.raises(ValueError):
        paired_bootstrap({}, {}, seed=1, resamples=10)
    with pytest.raises(ValueError):
        paired_bootstrap({"c1": CC(1, 0, 2)}, {"c1": CC(1, 0, 3)}, seed=1, resamples=10)
    with pytest.raises(TypeError):
        paired_bootstrap({"c1": (1, 0, 2)}, {"c1": CC(1, 0, 2)}, seed=1, resamples=10)  # type: ignore[dict-item]


def test_a_conversation_with_no_checkable_rows_still_goes_in() -> None:
    result = paired_bootstrap({"a": CC(1, 0, 1), "z": CC(0, 1, 0)}, {"a": CC(1, 0, 1), "z": CC(0, 0, 0)}, seed=3)
    assert result.n_conversations == 2
    assert result.observed_difference == F(2, 3) - 1


# ---------------------------------------------------------------- B2 generator


def test_seed_is_required_and_checked() -> None:
    with pytest.raises(TypeError):
        paired_bootstrap(CHALLENGER, DEFAULT)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        paired_bootstrap(CHALLENGER, DEFAULT, 1)  # type: ignore[misc]
    for bad in (True, -1):
        with pytest.raises(ValueError):
            paired_bootstrap(CHALLENGER, DEFAULT, seed=bad, resamples=10)
    with pytest.raises(TypeError):
        paired_bootstrap(CHALLENGER, DEFAULT, seed=1.5, resamples=10)  # type: ignore[arg-type]


def test_resamples_and_confidence_are_checked() -> None:
    for bad in (True, 0, -5):
        with pytest.raises(ValueError):
            paired_bootstrap(CHALLENGER, DEFAULT, seed=1, resamples=bad)
    with pytest.raises(TypeError):
        paired_bootstrap(CHALLENGER, DEFAULT, seed=1, resamples=10.0)  # type: ignore[arg-type]
    for bad_confidence in (F(0), F(1), F(3, 2)):
        with pytest.raises(ValueError):
            paired_bootstrap(CHALLENGER, DEFAULT, seed=1, resamples=10, confidence=bad_confidence)
    with pytest.raises(TypeError):
        paired_bootstrap(CHALLENGER, DEFAULT, seed=1, resamples=10, confidence=0.95)  # type: ignore[arg-type]


def test_a_non_default_confidence_sets_the_rank() -> None:
    _, diffs = _reference(CHALLENGER, DEFAULT, seed=11, resamples=200)
    for confidence, bound in ((F(80, 100), F(1, 12)), (F(1, 2), F(2, 9))):
        result = paired_bootstrap(CHALLENGER, DEFAULT, seed=11, resamples=200, confidence=confidence)
        assert (result.confidence, result.lower_bound) == (confidence, bound)
        assert result.lower_bound == percentile_lower_bound(diffs, confidence)
    multi = paired_bootstrap_multi_run([CHALLENGER] * 3, [DEFAULT] * 3, seed=11, resamples=200, confidence=F(4, 5))
    assert {result.confidence for result in (multi.mean, *multi.per_run)} == {F(4, 5)}
    assert [result.lower_bound for result in (multi.mean, *multi.per_run)] == [F(1, 12)] * 4


def test_the_first_resample_is_the_first_n_randrange_draws() -> None:
    fresh = random.Random(7)
    expected = [fresh.randrange(3) for _ in range(3)]
    assert next(bootstrap_indices(3, seed=7, resamples=1)) == expected
    if platform.python_implementation() == "CPython":
        assert expected == [1, 0, 1]


def test_the_result_matches_an_independent_reference() -> None:
    observed, diffs = _reference(CHALLENGER, DEFAULT, seed=11, resamples=300)
    result = paired_bootstrap(CHALLENGER, DEFAULT, seed=11, resamples=300)
    assert result.observed_difference == observed
    assert result.lower_bound == percentile_lower_bound(diffs, BOOTSTRAP_CONFIDENCE)


def test_averaged_counts_match_the_reference_on_fractions() -> None:
    ch_runs = [CHALLENGER, {**CHALLENGER, "c3": CC(1, 0, 1)}, {**CHALLENGER, "c2": CC(2, 0, 2)}]
    de_runs = [DEFAULT, DEFAULT, {**DEFAULT, "c1": CC(0, 2, 2)}]
    ch, de = average_runs(ch_runs), average_runs(de_runs)
    observed, diffs = _reference(ch, de, seed=5, resamples=200)
    result = paired_bootstrap(ch, de, seed=5, resamples=200)
    assert result.observed_difference == observed
    assert result.lower_bound == percentile_lower_bound(diffs, BOOTSTRAP_CONFIDENCE)


def test_dict_insertion_order_does_not_matter() -> None:
    reordered_ch = dict(reversed(list(CHALLENGER.items())))
    reordered_de = dict(reversed(list(DEFAULT.items())))
    assert paired_bootstrap(CHALLENGER, DEFAULT, seed=9, resamples=200) == paired_bootstrap(
        reordered_ch, reordered_de, seed=9, resamples=200
    )


def test_global_random_state_changes_nothing() -> None:
    random.seed(0)
    first = paired_bootstrap(STRONG, WEAK, seed=21, resamples=200)
    random.seed(12345)
    random.random()
    second = paired_bootstrap(STRONG, WEAK, seed=21, resamples=200)
    assert first == second


# ---------------------------------------------------------------- B3 statistic


def test_observed_difference_is_pooled_f1() -> None:
    assert paired_bootstrap(CHALLENGER, DEFAULT, seed=1, resamples=10).observed_difference == F(2, 9)
    keys = sorted(CHALLENGER)
    ch, de = [CHALLENGER[k] for k in keys], [DEFAULT[k] for k in keys]
    assert (pooled_f1(ch), pooled_f1(de)) == (F(2, 3), F(4, 9))
    assert (pooled_f1(ch, [0, 0, 2]), pooled_f1(de, [0, 0, 2])) == (F(8, 9), F(1, 2))
    assert pooled_f1(ch, [0, 0, 2]) - pooled_f1(de, [0, 0, 2]) == F(7, 18)
    assert (pooled_f1(ch, [2, 2, 2]), pooled_f1(de, [2, 2, 2])) == (0, 0)


def test_pooled_f1_is_not_a_mean_of_per_conversation_f1() -> None:
    counts = [CC(1, 0, 1), CC(0, 0, 3)]
    assert pooled_f1(counts) == F(2, 5)
    assert (f1_from_counts(1, 0, 0) + f1_from_counts(0, 0, 3)) / 2 == F(1, 2)


# ---------------------------------------------------------------- B4 lower bound


def test_percentile_nearest_rank() -> None:
    values = [F(n) for n in range(1, 10_001)]
    random.Random(4).shuffle(values)
    assert percentile_lower_bound(values, F(95, 100)) == 500
    twenty = [F(n) for n in range(20, 0, -1)]
    assert percentile_lower_bound(twenty, F(95, 100)) == 1
    assert percentile_lower_bound([F(n) for n in range(21, 0, -1)], F(95, 100)) == 2
    assert percentile_lower_bound([F(-3, 7)], F(95, 100)) == F(-3, 7)


def test_percentile_input_checks() -> None:
    with pytest.raises(ValueError):
        percentile_lower_bound([], F(95, 100))
    with pytest.raises(TypeError):
        percentile_lower_bound([0.5], F(95, 100))  # type: ignore[list-item]
    with pytest.raises(TypeError):
        percentile_lower_bound([F(1)], 0.95)  # type: ignore[arg-type]


def test_one_conversation() -> None:
    result = paired_bootstrap({"c1": CC(2, 0, 2)}, {"c1": CC(1, 1, 2)}, seed=0, resamples=200)
    assert (result.observed_difference, result.lower_bound, result.lower_bound_above_zero) == (F(1, 2), F(1, 2), True)


def test_identical_inputs_give_a_bound_of_zero() -> None:
    result = paired_bootstrap(CHALLENGER, CHALLENGER, seed=0, resamples=200)
    assert (result.observed_difference, result.lower_bound, result.lower_bound_above_zero) == (0, 0, False)


@pytest.mark.parametrize("seed", [0, 1, 2, 12345])
def test_a_quarter_of_resamples_at_minus_one_sets_the_bound(seed: int) -> None:
    ch = {"c1": CC(2, 0, 2), "c2": CC(0, 0, 2)}
    de = {"c1": CC(0, 0, 2), "c2": CC(2, 0, 2)}
    result = paired_bootstrap(ch, de, seed=seed, resamples=200)
    assert (result.observed_difference, result.lower_bound, result.lower_bound_above_zero) == (0, -1, False)


@pytest.mark.parametrize("seed", [0, 1, 12345])
def test_ties_at_zero_keep_the_bound_at_zero(seed: int) -> None:
    result = paired_bootstrap(CHALLENGER, DEFAULT, seed=seed)  # the full 10,000 resamples
    assert result.resamples == 10_000
    assert (result.lower_bound, result.lower_bound_above_zero) == (0, False)
    _, diffs = _reference(CHALLENGER, DEFAULT, seed=seed, resamples=2_000)
    assert min(diffs) == 0  # d >= 0 always; P(d = 0) = 2/27


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_a_dominant_challenger_has_every_difference_above_zero(seed: int) -> None:
    _, diffs = _reference(STRONG, WEAK, seed=seed, resamples=200)
    assert all(diff > 0 for diff in diffs)
    result = paired_bootstrap(STRONG, WEAK, seed=seed, resamples=200)
    assert result.lower_bound > 0 and result.lower_bound_above_zero


# ---------------------------------------------------------------- B5 result record


def test_the_result_records_what_the_report_needs() -> None:
    first = paired_bootstrap(STRONG, WEAK, seed=12345)
    second = paired_bootstrap(STRONG, WEAK, seed=12345)
    assert first == second
    assert isinstance(first, BootstrapResult)
    assert (first.resamples, first.seed, first.confidence, first.n_conversations) == (10_000, 12345, F(95, 100), 5)
    assert first.python_version == platform.python_version()
    assert isinstance(first.observed_difference, F) and isinstance(first.lower_bound, F)


# ---------------------------------------------------------------- B6 averaging runs


def test_average_runs() -> None:
    runs = [{"c": CC(2, 0, 2)}, {"c": CC(1, 1, 2)}, {"c": CC(0, 1, 2)}]
    averaged = average_runs(runs)
    assert averaged == {"c": CC(1, F(2, 3), 2)}
    assert isinstance(averaged["c"].tp, F) and isinstance(averaged["c"].fp, F)
    assert average_runs([{"c": CC(2, 1, 3)}]) == {"c": CC(F(2), F(1), 3)}


def test_average_runs_checks_its_inputs() -> None:
    with pytest.raises(ValueError):
        average_runs([])
    with pytest.raises(ValueError):
        average_runs([{"c": CC(1, 0, 2)}, {"d": CC(1, 0, 2)}])
    with pytest.raises(ValueError):
        average_runs([{"c": CC(1, 0, 2)}, {"c": CC(1, 0, 3)}])
    with pytest.raises(TypeError):
        average_runs([{"c": (1, 0, 2)}])  # type: ignore[dict-item]


def test_averaged_counts_drive_the_bootstrap() -> None:
    ch_runs = [{"c": CC(2, 0, 2)}, {"c": CC(1, 0, 2)}, {"c": CC(2, 0, 2)}]
    de_runs = [{"c": CC(1, 1, 2)}] * 3
    ch = average_runs(ch_runs)
    assert ch == {"c": CC(F(5, 3), 0, 2)}
    result = paired_bootstrap(ch, average_runs(de_runs), seed=0, resamples=200)
    assert result.observed_difference == result.lower_bound == F(10, 11) - F(1, 2) == F(9, 22)
    per_run_f1 = [pooled_f1([run["c"]]) for run in ch_runs]
    assert sum(per_run_f1, F(0)) / 3 == F(8, 9)  # AMB-23: the mean of per-run F1 is not the pooled 10/11


# ---------------------------------------------------------------- B7 multi-run bootstrap


def test_three_identical_runs() -> None:
    multi = paired_bootstrap_multi_run([STRONG] * 3, [WEAK] * 3, seed=8, resamples=200)
    single = paired_bootstrap(STRONG, WEAK, seed=8, resamples=200)
    assert isinstance(multi, MultiRunBootstrap)
    assert multi.mean == single
    assert multi.per_run == (single, single, single)
    assert multi.holds


def test_the_multi_run_bootstrap_needs_exactly_three_runs() -> None:
    for count in (1, 2, 4):
        with pytest.raises(ValueError):
            paired_bootstrap_multi_run([STRONG] * count, [WEAK] * count, seed=1, resamples=10)
    with pytest.raises(ValueError):
        paired_bootstrap_multi_run([STRONG] * 3, [WEAK] * 2, seed=1, resamples=10)


def test_the_multi_run_bootstrap_combines_mean_and_runs() -> None:
    multi = paired_bootstrap_multi_run([STRONG, WEAK, STRONG], [WEAK, STRONG, WEAK], seed=2, resamples=200)
    per_run_holding = sum(1 for result in multi.per_run if result.lower_bound_above_zero)
    assert per_run_holding == 2
    assert multi.holds == (multi.mean.lower_bound_above_zero and per_run_holding >= 2)
    assert multi.per_run[1].lower_bound < 0


@pytest.mark.parametrize("seed", [0, 1, 12345])
def test_the_variant_bound_uses_averaged_counts_and_must_hold_on_the_mean(seed: int) -> None:
    # Two runs hold, but the bound on the averaged counts does not, so condition 2 fails (S2 §3.13, §8).
    ch_runs, de_runs = [STRONG, STRONG, ZERO], [WEAK, WEAK, STRONG]
    multi = paired_bootstrap_multi_run(ch_runs, de_runs, seed=seed, resamples=200)
    assert multi.mean == paired_bootstrap(average_runs(ch_runs), average_runs(de_runs), seed=seed, resamples=200)
    assert multi.per_run == tuple(
        paired_bootstrap(c, d, seed=seed, resamples=200) for c, d in zip(ch_runs, de_runs, strict=True)
    )
    assert multi.mean.observed_difference == F(-6, 155)
    assert [result.lower_bound_above_zero for result in multi.per_run] == [True, True, False]
    assert (multi.mean.lower_bound_above_zero, multi.holds) == (False, False)


def test_the_combining_rule_on_given_bounds() -> None:
    def holds(mean: F, runs: tuple[F, F, F]) -> bool:
        return holds_over_runs(mean > 0, [bound > 0 for bound in runs])

    assert holds(F(3, 1000), (F(10, 1000), F(-2, 1000), F(4, 1000)))
    assert not holds(F(3, 1000), (F(10, 1000), F(-2, 1000), F(-1, 1000)))
    assert not holds(F(0), (F(1, 1000), F(1, 1000), F(1, 1000)))
    with pytest.raises(ValueError):
        holds_over_runs(True, [True, True])


# ---------------------------------------------------------------- B8 margin, case 1


def _summary(f1: F, routing: F = F(3, 4), leaks: int = 0, pc: dict | None = None) -> RunSummary:
    return RunSummary(f1=f1, routing_accuracy=routing, high_stakes_leaks=leaks, per_conversation=pc or STRONG)


def _margin(ch: RunSummary, de: RunSummary) -> dict[int, bool]:
    result = margin_case1([ch], [de], variant=False, seed=4, resamples=200)
    assert result.variant is False and isinstance(result.bootstrap, BootstrapResult)
    assert all(condition.runs_holding is None for condition in result.conditions)
    return {condition.number: condition.holds for condition in result.conditions}


def test_all_four_conditions_hold() -> None:
    result = margin_case1(
        [_summary(F(80, 100), F(140, 200), 2, STRONG)],
        [_summary(F(77, 100), F(144, 200), 2, WEAK)],
        variant=False,
        seed=4,
        resamples=200,
    )
    assert [c.number for c in result.conditions] == [1, 2, 3, 4]
    assert [c.name for c in result.conditions] == [
        "f1_margin",
        "bootstrap_lower_bound",
        "routing_accuracy",
        "high_stakes_leaks",
    ]
    assert result.all_hold
    assert all(c.holds == c.holds_on_value for c in result.conditions)


def test_condition_one_uses_exact_fractions() -> None:
    assert _margin(_summary(F(80, 100)), _summary(F(77, 100)))[1]
    assert _margin(_summary(F(166, 200)), _summary(F(160, 200)))[1]  # float: 0.029999999999999916
    assert 166 / 200 - 160 / 200 < 0.03
    assert not _margin(_summary(F(79, 100)), _summary(F(77, 100)))[1]


def test_condition_one_fails_when_the_challenger_scores_lower() -> None:
    # S2 §8: "at least 0.03 higher", one-sided.
    assert not _margin(_summary(F(77, 100)), _summary(F(80, 100)))[1]
    ch = [_summary(F(70, 100), pc=STRONG)] * 3
    de = [_summary(F(80, 100), pc=WEAK)] * 3
    first = margin_case1(ch, de, variant=True, seed=4, resamples=200).conditions[0]
    assert (first.holds, first.holds_on_value, first.runs_holding) == (False, False, 0)


def test_condition_two_is_the_bootstrap_bound() -> None:
    assert _margin(_summary(F(1), pc=STRONG), _summary(F(1), pc=WEAK))[2]
    assert not _margin(_summary(F(1), pc=STRONG), _summary(F(1), pc=STRONG))[2]


@pytest.mark.parametrize("seed", [0, 4, 12345])
def test_condition_two_reads_the_lower_bound_not_the_observed_difference(seed: int) -> None:
    # B1 counts at the full 10,000 resamples: the observed difference is 2/9 but P(d = 0) = 2/27 keeps the bound at 0.
    result = margin_case1(
        [_summary(F(80, 100), pc=CHALLENGER)], [_summary(F(77, 100), pc=DEFAULT)], variant=False, seed=seed
    )
    assert isinstance(result.bootstrap, BootstrapResult)
    assert (result.bootstrap.observed_difference, result.bootstrap.lower_bound) == (F(2, 9), 0)
    assert [condition.holds for condition in result.conditions] == [True, False, True, True]
    assert result.all_hold is False


def test_condition_three_routing_accuracy() -> None:
    assert not _margin(_summary(F(1), F(139, 200)), _summary(F(1), F(144, 200)))[3]  # -1/40
    assert _margin(_summary(F(1), F(140, 200)), _summary(F(1), F(144, 200)))[3]  # exactly -1/50
    assert 140 / 200 - 144 / 200 < -0.02


def test_condition_three_holds_when_the_challenger_routes_better() -> None:
    # S2 §8: "no more than 0.02 lower", one-sided.
    assert _margin(_summary(F(1), F(160, 200)), _summary(F(1), F(144, 200)))[3]
    ch = [RunSummary(F(1), F(160, 200), 0, STRONG)] * 3
    de = [RunSummary(F(1), F(144, 200), 0, WEAK)] * 3
    third = margin_case1(ch, de, variant=True, seed=4, resamples=200).conditions[2]
    assert (third.holds, third.holds_on_value, third.runs_holding) == (True, True, 3)


def test_condition_four_high_stakes_leaks() -> None:
    assert _margin(_summary(F(1), leaks=2), _summary(F(1), leaks=2))[4]
    assert not _margin(_summary(F(1), leaks=3), _summary(F(1), leaks=2))[4]


def test_condition_four_holds_with_fewer_leaks() -> None:
    # S2 §8: "no more high-stakes leaks", so fewer is fine, on run 1 and on the mean over runs.
    assert _margin(_summary(F(1), leaks=1), _summary(F(1), leaks=2))[4]
    assert _margin(_summary(F(1), leaks=0), _summary(F(1), leaks=2))[4]
    ch = [RunSummary(F(1), F(3, 4), leaks, STRONG) for leaks in (0, 2, 0)]  # mean 2/3 against 1; runs 1 and 3 hold
    de = [RunSummary(F(1), F(3, 4), 1, WEAK) for _ in range(3)]
    fourth = margin_case1(ch, de, variant=True, seed=4, resamples=200).conditions[3]
    assert (fourth.holds, fourth.holds_on_value, fourth.runs_holding) == (True, True, 2)


def test_without_the_variant_run_one_decides() -> None:
    result = margin_case1(
        [_summary(F(80, 100)), _summary(F(10, 100))],
        [_summary(F(77, 100)), _summary(F(90, 100))],
        variant=False,
        seed=4,
        resamples=200,
    )
    assert result.conditions[0].holds


def test_the_margin_bootstrap_uses_the_given_seed_and_resamples() -> None:
    # B2, B5: the seed recorded in Annex A is the one the bootstrap used.
    single = margin_case1(
        [_summary(F(80, 100), pc=STRONG)], [_summary(F(77, 100), pc=WEAK)], variant=False, seed=13, resamples=150
    )
    assert single.bootstrap == paired_bootstrap(STRONG, WEAK, seed=13, resamples=150)
    variant = margin_case1(
        [_summary(F(1), pc=STRONG)] * 3, [_summary(F(1), pc=WEAK)] * 3, variant=True, seed=13, resamples=150
    )
    assert variant.bootstrap == paired_bootstrap_multi_run([STRONG] * 3, [WEAK] * 3, seed=13, resamples=150)
    default = margin_case1([_summary(F(1), pc=STRONG)], [_summary(F(1), pc=WEAK)], variant=False, seed=13)
    assert isinstance(default.bootstrap, BootstrapResult)
    assert (default.bootstrap.seed, default.bootstrap.resamples) == (13, BOOTSTRAP_RESAMPLES)


def _variant_runs(f1_diffs: tuple[F, F, F]) -> tuple[list[RunSummary], list[RunSummary]]:
    base = F(70, 100)
    return [_summary(base + d, pc=STRONG) for d in f1_diffs], [_summary(base, pc=WEAK) for _ in f1_diffs]


@pytest.mark.parametrize(
    ("diffs", "holds", "on_mean", "runs"),
    [
        ((F(4, 100), F(2, 100), F(35, 1000)), True, True, 2),  # mean 0.0317, runs 1 and 3
        ((F(4, 100), F(2, 100), F(25, 1000)), False, False, 1),  # mean 0.0283
        ((F(6, 100), F(2, 100), F(2, 100)), False, True, 1),  # mean 0.0333 but one run
    ],
)
def test_variant_condition_one(diffs: tuple[F, F, F], holds: bool, on_mean: bool, runs: int) -> None:
    ch, de = _variant_runs(diffs)
    result = margin_case1(ch, de, variant=True, seed=4, resamples=200)
    first = result.conditions[0]
    assert (first.holds, first.holds_on_value, first.runs_holding) == (holds, on_mean, runs)
    assert isinstance(result.bootstrap, MultiRunBootstrap)
    assert result.conditions[1].holds == result.bootstrap.holds


def test_variant_conditions_three_and_four() -> None:
    routing_ch = (F(140, 200), F(139, 200), F(144, 200))  # differences -1/50, -1/40, 0; mean -0.015
    leaks_ch = (0, 3, 0)  # mean 1 against 1; holds in runs 1 and 3
    ch = [RunSummary(F(1), r, k, STRONG) for r, k in zip(routing_ch, leaks_ch, strict=True)]
    de = [RunSummary(F(1), F(144, 200), 1, WEAK) for _ in range(3)]
    third, fourth = margin_case1(ch, de, variant=True, seed=4, resamples=200).conditions[2:]
    assert (third.holds, third.runs_holding) == (True, 2)
    assert (fourth.holds, fourth.holds_on_value, fourth.runs_holding) == (True, True, 2)
    ch_more = [RunSummary(F(1), F(144, 200), k, STRONG) for k in (2, 2, 0)]  # mean 4/3 > 1
    fourth_fails = margin_case1(ch_more, de, variant=True, seed=4, resamples=200).conditions[3]
    assert (fourth_fails.holds_on_value, fourth_fails.holds) == (False, False)


def test_variant_details_show_every_run() -> None:
    # The per-run values decide runs_holding, so every condition's detail lists them, as condition 1's does.
    routing_ch = (F(140, 200), F(139, 200), F(144, 200))
    ch = [RunSummary(F(1), r, k, STRONG) for r, k in zip(routing_ch, (0, 3, 0), strict=True)]
    de = [RunSummary(F(1), F(144, 200), 1, WEAK) for _ in range(3)]
    third, fourth = margin_case1(ch, de, variant=True, seed=4, resamples=200).conditions[2:]
    assert third.detail == (
        "mean routing accuracy difference -3/200 (~-0.0150); per run ['-1/50', '-1/40', '0']; needs >= -1/50"
    )
    assert fourth.detail == (
        "mean high-stakes leaks 1 against 1; per run ['0 against 1', '3 against 1', '0 against 1']; needs no more"
    )


def test_margin_input_checks() -> None:
    with pytest.raises(ValueError):
        margin_case1([_summary(F(1))] * 2, [_summary(F(1))] * 2, variant=True, seed=1, resamples=10)
    with pytest.raises(ValueError):
        margin_case1([], [_summary(F(1))], variant=False, seed=1, resamples=10)
    with pytest.raises(TypeError):
        margin_case1([_summary(F(1))], [_summary(F(1))], variant=1, seed=1, resamples=10)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        margin_case1([F(1)], [_summary(F(1))], variant=False, seed=1, resamples=10)  # type: ignore[list-item]


def test_run_summary_holds_exact_values() -> None:
    summary = RunSummary(f1=1, routing_accuracy=F(7, 10), high_stakes_leaks=0, per_conversation=STRONG)
    assert summary.f1 == 1 and isinstance(summary.f1, F)
    with pytest.raises(TypeError):
        summary.per_conversation["d9"] = CC(0, 0, 0)  # type: ignore[index]
    with pytest.raises(TypeError):
        RunSummary(f1=0.8, routing_accuracy=F(1), high_stakes_leaks=0, per_conversation=STRONG)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        RunSummary(f1=F(1), routing_accuracy=F(1), high_stakes_leaks=-1, per_conversation=STRONG)


# ---------------------------------------------------------------- B9 determinism trigger


def test_the_determinism_trigger() -> None:
    assert determinism_variant(F(0), F(1, 140))
    assert determinism_variant(F(1, 140), F(0))
    assert not determinism_variant(F(0), F(0))
    with pytest.raises(ValueError):
        determinism_variant(0.0, F(0))  # type: ignore[arg-type]
