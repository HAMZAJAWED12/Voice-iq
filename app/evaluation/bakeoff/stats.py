"""Paired bootstrap, run averaging and the case-1 margin test (bake-off harness H1).

Implements, from the signed D6 criteria note S2:

- §8, margin test case 1. Qwen3.5-4B (the challenger) against Qwen3-4B (the default): (1) extraction F1 at least
  0.03 higher; (2) a paired bootstrap over the conversations, 10,000 resamples with a recorded seed, gives a
  one-sided 95% lower bound on the F1 difference above 0; (3) Layer B routing accuracy no more than 0.02 lower;
  (4) no more high-stakes leaks. The "passes every gate" part and decision rules 1-5 are H4's, not here.
- §8, score: extraction F1 from run 1, pooled over conversations (2TP / (2TP + FP + FN)).
- §3.13, determinism variant: when the share of requests whose output differs from run 1 is above 0 for either 4B
  model, each case-1 condition must hold on the mean over runs and in at least two of the three runs, and the
  bootstrap averages each conversation's per-run results.
- §6 thresholds as exact fractions; `scoring` applies gates A and S with them.

Every ratio is an exact `fractions.Fraction`; floats appear only in display text. Each bootstrap call creates its own
`random.Random(seed)`: no global random state and no numpy. This module imports nothing from the rest of the package.
"""

from __future__ import annotations

import math
import platform
import random
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType

# S2 §6 gates A and S, §6 recall target, §8 margin. Exact fractions: "at least 0.75" is >= 3/4, never a float.
RECALL_FLOOR = Fraction(3, 4)
PRECISION_FLOOR = Fraction(3, 4)
ROUTING_FLOOR = Fraction(7, 10)
HIGH_STAKES_LEAK_MAX = 2
OOS_LEAK_RATE_MAX = Fraction(1, 5)
RECALL_TARGET = Fraction(17, 20)
MARGIN_F1 = Fraction(3, 100)
MARGIN_ROUTING = Fraction(1, 50)
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_CONFIDENCE = Fraction(95, 100)
VARIANT_RUNS = 3  # S2 §3.13: "Each candidate runs everything three times."

_Count = int | Fraction


def _check_count(owner: str, name: str, value: object) -> None:
    if isinstance(value, bool):
        raise ValueError(f"{owner}.{name} must be a count, not a bool")
    if not isinstance(value, int | Fraction):
        raise TypeError(f"{owner}.{name} must be an int or a Fraction, not {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class ConversationCounts:
    """One conversation's extraction counts for the bootstrap (S2 §8).

    `tp` is the number of found units and `fp` the number of false-positive claims; both are Fractions only after
    `average_runs`. `n_checkable` is the conversation's number of gold units (in-scope `CHECKABLE` rows, flagged
    repeat groups merged): a gold constant, so it must be identical across runs and candidates.
    """

    tp: _Count
    fp: _Count
    n_checkable: int

    def __post_init__(self) -> None:
        _check_count("ConversationCounts", "tp", self.tp)
        _check_count("ConversationCounts", "fp", self.fp)
        if isinstance(self.n_checkable, bool):
            raise ValueError("ConversationCounts.n_checkable must be a count, not a bool")
        if not isinstance(self.n_checkable, int):
            raise TypeError("ConversationCounts.n_checkable must be an int")
        if not 0 <= self.tp <= self.n_checkable:
            raise ValueError(f"ConversationCounts needs 0 <= tp <= n_checkable, got tp={self.tp}, n={self.n_checkable}")
        if self.fp < 0:
            raise ValueError(f"ConversationCounts.fp must be >= 0, got {self.fp}")

    @property
    def fn(self) -> _Count:
        """Gold units not found."""
        return self.n_checkable - self.tp


def f1_from_counts(tp: _Count, fp: _Count, fn: _Count) -> Fraction:
    """Pooled F1, 2TP / (2TP + FP + FN) (S2 §5.2, §8).

    Returns Fraction(0) when the denominator is 0. Only the bootstrap statistic uses this convention; reports show an
    undefined ratio as None (spec C1, AMB-15).
    """
    denominator = 2 * tp + fp + fn
    if denominator == 0:
        return Fraction(0)
    return Fraction(2 * tp) / denominator


def pooled_f1(counts: Sequence[ConversationCounts], indices: Iterable[int] | None = None) -> Fraction:
    """F1 of the counts summed over `indices` (every conversation when None), repeats counted (S2 §8).

    This is pooled F1, not a mean of per-conversation F1 values.
    """
    chosen = range(len(counts)) if indices is None else indices
    tp: _Count = 0
    fp: _Count = 0
    fn: _Count = 0
    for index in chosen:
        item = counts[index]
        tp += item.tp
        fp += item.fp
        fn += item.fn
    return f1_from_counts(tp, fp, fn)


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """One paired bootstrap (S2 §8). The report records the seed (Annex A), the method (AMB-3) and the Python
    version, because `random.randrange` output follows CPython's algorithm."""

    observed_difference: Fraction
    lower_bound: Fraction
    lower_bound_above_zero: bool
    resamples: int
    seed: int
    confidence: Fraction
    n_conversations: int
    python_version: str


def _check_seed(seed: object) -> int:
    if isinstance(seed, bool):
        raise ValueError("seed must be an int >= 0, not a bool")
    if not isinstance(seed, int):
        raise TypeError(f"seed must be an int >= 0, not {type(seed).__name__}")
    if seed < 0:
        raise ValueError(f"seed must be >= 0, got {seed}")
    return seed


def _check_resamples(resamples: object) -> int:
    if isinstance(resamples, bool):
        raise ValueError("resamples must be an int >= 1, not a bool")
    if not isinstance(resamples, int):
        raise TypeError(f"resamples must be an int >= 1, not {type(resamples).__name__}")
    if resamples < 1:
        raise ValueError(f"resamples must be >= 1, got {resamples}")
    return resamples


def _check_confidence(confidence: object) -> Fraction:
    if not isinstance(confidence, Fraction):
        raise TypeError(f"confidence must be a Fraction, not {type(confidence).__name__}")
    if not 0 < confidence < 1:
        raise ValueError(f"confidence must be strictly between 0 and 1, got {confidence}")
    return confidence


def percentile_lower_bound(differences: Sequence[Fraction], confidence: Fraction) -> Fraction:
    """The one-sided lower bound: the k-th smallest difference, counting from 1, with k = ceil(B * (1 - confidence))
    computed exactly (S2 §8). With B = 10,000 and confidence 95/100, k = 500."""
    # NEEDS-OWNER(AMB-3): percentile method, nearest-rank ceil(0.05·B); record in Annex A
    _check_confidence(confidence)
    values = list(differences)
    if not values:
        raise ValueError("percentile_lower_bound needs at least one difference")
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int | Fraction):
            raise TypeError("differences must be exact (int or Fraction)")
    k = math.ceil(len(values) * (1 - confidence))
    return Fraction(sorted(values)[k - 1])


def bootstrap_indices(n_conversations: int, *, seed: int, resamples: int) -> Iterator[list[int]]:
    """The resamples, in order: each is `[rng.randrange(n) for _ in range(n)]` from one `random.Random(seed)` made
    for this call (S2 §8; AMB-22)."""
    _check_seed(seed)
    _check_resamples(resamples)
    if isinstance(n_conversations, bool) or not isinstance(n_conversations, int) or n_conversations < 1:
        raise ValueError("n_conversations must be an int >= 1")
    rng = random.Random(seed)
    for _ in range(resamples):
        yield [rng.randrange(n_conversations) for _ in range(n_conversations)]


_Columns = tuple[list[int], list[int], list[int]]


def _paired_keys(challenger: Mapping[str, ConversationCounts], default: Mapping[str, ConversationCounts]) -> list[str]:
    if not isinstance(challenger, Mapping) or not isinstance(default, Mapping):
        raise TypeError("paired_bootstrap takes two mappings of source_id to ConversationCounts")
    if set(challenger) != set(default):
        missing = sorted(set(challenger) ^ set(default))
        raise ValueError(f"challenger and default must cover the same conversations; differing keys: {missing}")
    if not challenger:
        raise ValueError("paired_bootstrap needs at least one conversation")
    keys = sorted(challenger)
    for key in keys:
        ch, de = challenger[key], default[key]
        if not isinstance(ch, ConversationCounts) or not isinstance(de, ConversationCounts):
            raise TypeError(f"{key}: values must be ConversationCounts")
        if ch.n_checkable != de.n_checkable:
            raise ValueError(f"{key}: n_checkable differs ({ch.n_checkable} against {de.n_checkable})")
    return keys


def _integer_columns(counts: Sequence[ConversationCounts]) -> _Columns:
    """tp, fp and fn as integers, every conversation scaled by one common factor.

    Pooled F1 is unchanged when every count of one candidate is multiplied by the same factor, so averaged counts
    (S2 §8: "averages each conversation's per-run results") give identical numbers on per-conversation sums.
    """
    scale = 1
    for item in counts:
        for value in (item.tp, item.fp):
            if isinstance(value, Fraction):
                scale = math.lcm(scale, value.denominator)
    tp = [int(item.tp * scale) for item in counts]
    fp = [int(item.fp * scale) for item in counts]
    fn = [item.n_checkable * scale - t for item, t in zip(counts, tp, strict=True)]
    return tp, fp, fn


def _difference(challenger: _Columns, default: _Columns, indices: Iterable[int]) -> Fraction:
    """F1(challenger) - F1(default) over the same indices, repeats counted (S2 §8)."""
    c_tp, c_fp, c_fn = challenger
    d_tp, d_fp, d_fn = default
    ctp = cfp = cfn = dtp = dfp = dfn = 0
    for index in indices:
        ctp += c_tp[index]
        cfp += c_fp[index]
        cfn += c_fn[index]
        dtp += d_tp[index]
        dfp += d_fp[index]
        dfn += d_fn[index]
    c_den = 2 * ctp + cfp + cfn
    d_den = 2 * dtp + dfp + dfn
    if c_den and d_den:
        return Fraction(2 * ctp * d_den - 2 * dtp * c_den, c_den * d_den)
    if c_den:
        return Fraction(2 * ctp, c_den)
    if d_den:
        return Fraction(-2 * dtp, d_den)
    return Fraction(0)


def paired_bootstrap(
    challenger: Mapping[str, ConversationCounts],
    default: Mapping[str, ConversationCounts],
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: Fraction = BOOTSTRAP_CONFIDENCE,
) -> BootstrapResult:
    """Paired bootstrap over the conversations of the F1 difference, challenger minus default (S2 §8).

    Both mappings hold every gold conversation, including any with no in-scope `CHECKABLE` row; a failed
    conversation enters as (0, 0, n). Conversations are indexed in sorted source_id order, and each resample applies
    the same indices to both candidates. The lower bound must be strictly above 0.
    """
    _check_seed(seed)
    _check_resamples(resamples)
    _check_confidence(confidence)
    keys = _paired_keys(challenger, default)
    ch = _integer_columns([challenger[key] for key in keys])
    de = _integer_columns([default[key] for key in keys])
    n = len(keys)
    observed = _difference(ch, de, range(n))
    differences = [_difference(ch, de, idx) for idx in bootstrap_indices(n, seed=seed, resamples=resamples)]
    lower = percentile_lower_bound(differences, confidence)
    return BootstrapResult(
        observed_difference=observed,
        lower_bound=lower,
        lower_bound_above_zero=lower > 0,
        resamples=resamples,
        seed=seed,
        confidence=confidence,
        n_conversations=n,
        python_version=platform.python_version(),
    )


def average_runs(runs: Sequence[Mapping[str, ConversationCounts]]) -> dict[str, ConversationCounts]:
    """Each conversation's per-run counts averaged: tp = Fraction(sum tp, R), fp = Fraction(sum fp, R), n unchanged
    (S2 §8, the §3.13 variant). The runs must have identical keys and identical n per key."""
    run_list = list(runs)
    if not run_list:
        raise ValueError("average_runs needs at least one run")
    keys = sorted(run_list[0])
    for number, run in enumerate(run_list, start=1):
        if not isinstance(run, Mapping):
            raise TypeError(f"run {number} is not a mapping")
        if set(run) != set(keys):
            raise ValueError(f"run {number} covers different conversations from run 1")
    averaged: dict[str, ConversationCounts] = {}
    for key in keys:
        per_run = [run[key] for run in run_list]
        for item in per_run:
            if not isinstance(item, ConversationCounts):
                raise TypeError(f"{key}: values must be ConversationCounts")
        n = per_run[0].n_checkable
        if any(item.n_checkable != n for item in per_run):
            raise ValueError(f"{key}: n_checkable differs between runs")
        total_tp: _Count = sum((item.tp for item in per_run), 0)
        total_fp: _Count = sum((item.fp for item in per_run), 0)
        averaged[key] = ConversationCounts(
            tp=Fraction(total_tp, len(run_list)), fp=Fraction(total_fp, len(run_list)), n_checkable=n
        )
    return averaged


def holds_over_runs(on_mean: bool, per_run: Sequence[bool]) -> bool:
    """S2 §3.13: a case-1 condition holds on the mean over runs and in at least two of the three runs. Applied to each
    condition on its own (AMB-24)."""
    flags = tuple(per_run)
    if len(flags) != VARIANT_RUNS:
        raise ValueError(f"the §3.13 variant needs exactly {VARIANT_RUNS} runs, got {len(flags)}")
    return bool(on_mean) and sum(1 for flag in flags if flag) >= 2


@dataclass(frozen=True, slots=True)
class MultiRunBootstrap:
    """The §3.13 bootstrap: on per-conversation averaged counts (`mean`) and per run, all with one seed."""

    mean: BootstrapResult
    per_run: tuple[BootstrapResult, ...]
    holds: bool


def paired_bootstrap_multi_run(
    challenger_runs: Sequence[Mapping[str, ConversationCounts]],
    default_runs: Sequence[Mapping[str, ConversationCounts]],
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
    confidence: Fraction = BOOTSTRAP_CONFIDENCE,
) -> MultiRunBootstrap:
    """Condition 2 under the §3.13 variant (S2 §8, §3.13): the bound on averaged counts must be above 0, and the
    per-run bounds (run r against run r, the same seed for every call) in at least two of the three runs."""
    ch = tuple(challenger_runs)
    de = tuple(default_runs)
    if len(ch) != VARIANT_RUNS or len(de) != VARIANT_RUNS:
        raise ValueError(f"the §3.13 variant needs exactly {VARIANT_RUNS} runs per candidate")
    mean = paired_bootstrap(average_runs(ch), average_runs(de), seed=seed, resamples=resamples, confidence=confidence)
    per_run = tuple(
        paired_bootstrap(c, d, seed=seed, resamples=resamples, confidence=confidence)
        for c, d in zip(ch, de, strict=True)
    )
    holds = holds_over_runs(mean.lower_bound_above_zero, [result.lower_bound_above_zero for result in per_run])
    return MultiRunBootstrap(mean=mean, per_run=per_run, holds=holds)


def determinism_variant(default_share: Fraction, challenger_share: Fraction) -> bool:
    """S2 §3.13 trigger: True when the share of requests whose output differs from run 1 is above 0 for either 4B
    model. H2 computes the shares (AMB-25); H1 only applies the rule."""
    for share in (default_share, challenger_share):
        if not isinstance(share, Fraction) or not 0 <= share <= 1:
            raise ValueError("a determinism share is a Fraction between 0 and 1")
    return default_share > 0 or challenger_share > 0


def _exact(owner: str, name: str, value: object) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, int | Fraction):
        raise TypeError(f"{owner}.{name} must be an exact Fraction, not {type(value).__name__}")
    return Fraction(value)


@dataclass(frozen=True, slots=True)
class RunSummary:
    """The figures the margin test reads from one run's metrics (`scoring.RunMetrics.summary()`)."""

    f1: Fraction
    routing_accuracy: Fraction
    high_stakes_leaks: int
    per_conversation: Mapping[str, ConversationCounts]

    def __post_init__(self) -> None:
        object.__setattr__(self, "f1", _exact("RunSummary", "f1", self.f1))
        object.__setattr__(self, "routing_accuracy", _exact("RunSummary", "routing_accuracy", self.routing_accuracy))
        leaks = self.high_stakes_leaks
        if isinstance(leaks, bool) or not isinstance(leaks, int) or leaks < 0:
            raise ValueError("RunSummary.high_stakes_leaks must be an int >= 0")
        object.__setattr__(self, "per_conversation", MappingProxyType(dict(self.per_conversation)))


@dataclass(frozen=True, slots=True)
class MarginCondition:
    """One case-1 condition. `holds_on_value` is the check on run 1 (or on the mean over runs under the variant);
    `runs_holding` counts the runs where it holds under the variant and is None otherwise."""

    number: int
    name: str
    holds_on_value: bool
    runs_holding: int | None
    holds: bool
    detail: str


@dataclass(frozen=True, slots=True)
class MarginResult:
    variant: bool
    conditions: tuple[MarginCondition, ...]
    all_hold: bool
    bootstrap: BootstrapResult | MultiRunBootstrap


def _show(value: Fraction) -> str:
    return f"{value} (~{float(value):.4f})"


def _mean(values: Sequence[Fraction]) -> Fraction:
    return Fraction(sum(values, Fraction(0)), len(values))


def _single_run_conditions(ch: RunSummary, de: RunSummary, bootstrap: BootstrapResult) -> list[MarginCondition]:
    f1_diff = ch.f1 - de.f1
    routing_diff = ch.routing_accuracy - de.routing_accuracy
    checks = [
        (1, "f1_margin", f1_diff >= MARGIN_F1, f"F1 difference {_show(f1_diff)}; needs >= {MARGIN_F1}"),
        (
            2,
            "bootstrap_lower_bound",
            bootstrap.lower_bound_above_zero,
            f"lower bound {_show(bootstrap.lower_bound)}; needs > 0",
        ),
        (
            3,
            "routing_accuracy",
            routing_diff >= -MARGIN_ROUTING,
            f"routing accuracy difference {_show(routing_diff)}; needs >= -{MARGIN_ROUTING}",
        ),
        (
            4,
            "high_stakes_leaks",
            ch.high_stakes_leaks <= de.high_stakes_leaks,
            f"high-stakes leaks {ch.high_stakes_leaks} against {de.high_stakes_leaks}; needs no more",
        ),
    ]
    return [MarginCondition(number, name, ok, None, ok, detail) for number, name, ok, detail in checks]


def _variant_condition(number: int, name: str, on_mean: bool, per_run: list[bool], detail: str) -> MarginCondition:
    return MarginCondition(
        number=number,
        name=name,
        holds_on_value=on_mean,
        runs_holding=sum(1 for flag in per_run if flag),
        holds=holds_over_runs(on_mean, per_run),
        detail=detail,
    )


def _variant_conditions(
    ch: tuple[RunSummary, ...], de: tuple[RunSummary, ...], bootstrap: MultiRunBootstrap
) -> list[MarginCondition]:
    # AMB-23: "mean over runs" is the arithmetic mean of the per-run values (F1, routing accuracy, leak counts).
    f1_diffs = [c.f1 - d.f1 for c, d in zip(ch, de, strict=True)]
    routing_diffs = [c.routing_accuracy - d.routing_accuracy for c, d in zip(ch, de, strict=True)]
    mean_f1 = _mean(f1_diffs)
    mean_routing = _mean(routing_diffs)
    mean_leaks_ch = _mean([Fraction(c.high_stakes_leaks) for c in ch])
    mean_leaks_de = _mean([Fraction(d.high_stakes_leaks) for d in de])
    leak_pairs = [f"{c.high_stakes_leaks} against {d.high_stakes_leaks}" for c, d in zip(ch, de, strict=True)]
    per_run_bounds = [result.lower_bound for result in bootstrap.per_run]
    return [
        _variant_condition(
            1,
            "f1_margin",
            mean_f1 >= MARGIN_F1,
            [diff >= MARGIN_F1 for diff in f1_diffs],
            f"mean F1 difference {_show(mean_f1)}; per run {[str(d) for d in f1_diffs]}; needs >= {MARGIN_F1}",
        ),
        MarginCondition(
            number=2,
            name="bootstrap_lower_bound",
            holds_on_value=bootstrap.mean.lower_bound_above_zero,
            runs_holding=sum(1 for result in bootstrap.per_run if result.lower_bound_above_zero),
            holds=bootstrap.holds,
            detail=(
                f"lower bound on averaged counts {_show(bootstrap.mean.lower_bound)}; "
                f"per run {[str(b) for b in per_run_bounds]}; needs > 0"
            ),
        ),
        _variant_condition(
            3,
            "routing_accuracy",
            mean_routing >= -MARGIN_ROUTING,
            [diff >= -MARGIN_ROUTING for diff in routing_diffs],
            f"mean routing accuracy difference {_show(mean_routing)}; per run {[str(d) for d in routing_diffs]}; "
            f"needs >= -{MARGIN_ROUTING}",
        ),
        _variant_condition(
            4,
            "high_stakes_leaks",
            mean_leaks_ch <= mean_leaks_de,
            [c.high_stakes_leaks <= d.high_stakes_leaks for c, d in zip(ch, de, strict=True)],
            f"mean high-stakes leaks {mean_leaks_ch} against {mean_leaks_de}; per run {leak_pairs}; needs no more",
        ),
    ]


def margin_case1(
    challenger_runs: Sequence[RunSummary],
    default_runs: Sequence[RunSummary],
    *,
    variant: bool,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> MarginResult:
    """S2 §8 margin test, case 1 (the literal reading), challenger against default.

    Without the §3.13 variant, run 1 (index 0) decides. With it, exactly three runs per candidate are needed, and each
    condition must hold on the mean over runs and in at least two of the three runs (run r against run r). The test
    applies whether or not the default passed its gates; the "passes every gate" part is H4's.
    """
    if not isinstance(variant, bool):
        raise TypeError("variant must be a bool (S2 §3.13 determinism trigger)")
    ch = tuple(challenger_runs)
    de = tuple(default_runs)
    for summary in ch + de:
        if not isinstance(summary, RunSummary):
            raise TypeError("margin_case1 takes RunSummary values (RunMetrics.summary())")
    conditions: list[MarginCondition]
    bootstrap: BootstrapResult | MultiRunBootstrap
    if variant:
        if len(ch) != VARIANT_RUNS or len(de) != VARIANT_RUNS:
            raise ValueError(f"the §3.13 variant needs exactly {VARIANT_RUNS} runs per candidate")
        bootstrap = paired_bootstrap_multi_run(
            [run.per_conversation for run in ch], [run.per_conversation for run in de], seed=seed, resamples=resamples
        )
        conditions = _variant_conditions(ch, de, bootstrap)
    else:
        if not ch or not de:
            raise ValueError("margin_case1 needs run 1 of each candidate")
        bootstrap = paired_bootstrap(ch[0].per_conversation, de[0].per_conversation, seed=seed, resamples=resamples)
        conditions = _single_run_conditions(ch[0], de[0], bootstrap)
    return MarginResult(
        variant=variant,
        conditions=tuple(conditions),
        all_hold=all(condition.holds for condition in conditions),
        bootstrap=bootstrap,
    )
