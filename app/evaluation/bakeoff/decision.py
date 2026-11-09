"""The margin test and the decision rule of a bake-off (bake-off harness H4a).

This module applies the signed rule mechanically to what `gates.evaluate` built. It is pure: it reads
`CandidateResult` values and the determinism shares, calls H1's `stats` for the variant trigger and the case-1 margin,
and returns a `Decision`. `entry` calls `margin`, then `decide`; `report` encodes the result.

Implements, from the signed D6 criteria note S2:

- S2 §1, candidates: the pin is one of the three candidates or nothing, and the winner's id, source revision and GGUF
  SHA-256 become the `FACTCHECK_LLM_MODEL` and `FACTCHECK_LLM_REVISION` settings (`settings`, `revision_setting`).
- S2 §2 and §3.6, eligibility: a candidate that failed gate L (§2) or showed thinking at pre-run (§3.6) is never
  pinned; `gates.CandidateResult.eligible` carries both.
- S2 §3.13, runs: when either 4B model's determinism share is above 0, the case-1 conditions are read on the mean over
  runs 1 to 3 and must also hold in at least two of the three runs; fewer than three scored runs is an error, with no
  fallback to run 1.
- S2 §8, margin and decision rule: case 1 only (the literal reading, §12 row 16), at 10,000 resamples; rules 1 to 5
  in order. A margin computed at any other resample count is reported as not conforming and never switches the pin.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from . import stats
from .gates import GATES, CandidateResult
from .launch import CANDIDATE_IDS
from .runner import DeterminismShare

DEFAULT, CHALLENGER, THIRD = CANDIDATE_IDS  # S2 §1: Qwen3-4B, Qwen3.5-4B, Qwen3-8B
MARGIN_READING = "literal"
"""S2 §8 "Reading: literal [owner]" and §12 row 16: case 1 is the only margin test. A signed owner value, not an open
reading, so it carries no NEEDS-OWNER marker; the report restates it beside every outcome, rule 5 included."""

Rule = Literal["rule 2", "rule 3", "rule 4 via rule 3", "rule 4 (Qwen3-8B)", "rule 5"]
RULES: tuple[Rule, ...] = ("rule 2", "rule 3", "rule 4 via rule 3", "rule 4 (Qwen3-8B)", "rule 5")
# S2 §8 rule 5: what the senior sponsor may do when nothing is pinned.
RULE5_OPTIONS = (
    "(a) amend the run conditions within the limits S2 §8 fixes in advance, and re-run every candidate",
    "(b) revisit D5 or D6 by a new signed decision",
)
MODEL_SETTING = "FACTCHECK_LLM_MODEL"  # S2 §1; PHASE-0 §2.2 types it as a string
REVISION_SETTING = "FACTCHECK_LLM_REVISION"  # S2 §1, §7 and U3; PHASE-0 §2.2 types it as a string
NO_SHARE_NOTE = "a 4B model has no determinism share, so no margin test runs"
NO_MARGIN_NOTE = "no margin was computed"
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class Decision:
    """The S2 §8 outcome. `margin_conforming` is None when no margin was computed; `margin_note` says why there is no
    margin, or why it does not count. `steps` records each test in the order it was made."""

    rule: Rule
    pinned: str | None
    margin: stats.MarginResult | None
    margin_conforming: bool | None
    variant: bool | None
    margin_note: str | None
    pinnable: Mapping[str, bool]
    steps: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.rule not in RULES:
            raise ValueError(f"{self.rule!r} is not an S2 §8 rule")
        if (self.rule == "rule 5") != (self.pinned is None):
            raise ValueError("rule 5 pins nothing, and every other rule pins one candidate")
        object.__setattr__(self, "pinnable", MappingProxyType(dict(self.pinnable)))
        object.__setattr__(self, "steps", tuple(self.steps))

    @property
    def changes_baseline(self) -> bool | None:
        """True when the pin is not the Qwen3 baseline; None when nothing is pinned (G4-29)."""
        return None if self.pinned is None else self.pinned != DEFAULT


def revision_setting(source_revision: str, gguf_sha256: str) -> str:
    """The one `FACTCHECK_LLM_REVISION` string for the winner's source revision and GGUF SHA-256 (S2 §1)."""
    # NEEDS-OWNER(AMB-134): FACTCHECK_LLM_REVISION = <source_revision>@sha256:<gguf_sha256>
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise ValueError("the source revision must contain a non-whitespace character")
    if not isinstance(gguf_sha256, str) or not _SHA256.fullmatch(gguf_sha256):
        raise ValueError("the GGUF SHA-256 must be 64 lowercase hex characters")
    return f"{source_revision}@sha256:{gguf_sha256}"


def settings(pinned: str, source_revision: str, gguf_sha256: str) -> Mapping[str, str]:
    """The two settings recorded from the winner (S2 §1; G4-29), both strings. Nothing is recorded under rule 5."""
    if pinned not in CANDIDATE_IDS:
        raise ValueError(f"{pinned!r} is not an S2 §1 candidate")
    return MappingProxyType({MODEL_SETTING: pinned, REVISION_SETTING: revision_setting(source_revision, gguf_sha256)})


def _runs(variant: bool) -> tuple[int, ...]:
    """Run 1 alone, or runs 1 to 3 under the §3.13 variant."""
    return tuple(range(1, stats.VARIANT_RUNS + 1)) if variant else (1,)


def _not_loaded(results: Mapping[str, CandidateResult]) -> str | None:
    """The first 4B model, in S2 §1 order, with no scores (AMB-130, AMB-141)."""
    for candidate in (DEFAULT, CHALLENGER):
        result = results.get(candidate)
        if result is None or result.status != "ran":
            return candidate
    return None


def _undefined(results: Mapping[str, CandidateResult], runs: tuple[int, ...]) -> str | None:
    """Why the margin cannot be computed when a run's F1 or routing accuracy is 0/0 (G4-27), or None.

    S2 §3.13 has no fallback to run 1, so a missing scored run is an error, not a note."""
    for candidate in (CHALLENGER, DEFAULT):
        metrics = results[candidate].metrics
        missing = [run for run in runs if run not in metrics]
        if missing:
            raise ValueError(f"{candidate} has no scored run {missing}; the margin needs runs {list(runs)}")
    for candidate in (CHALLENGER, DEFAULT):
        for run in runs:
            try:
                results[candidate].metrics[run].summary()
            except ValueError:
                return f"margin not computable: {candidate} run {run} has an undefined F1 or routing accuracy (0/0)"
    return None


def margin(
    results: Mapping[str, CandidateResult],
    shares: Mapping[str, DeterminismShare],
    seed: int,
    *,
    resamples: int = stats.BOOTSTRAP_RESAMPLES,
) -> tuple[stats.MarginResult | None, bool | None, str | None]:
    """The S2 §8 case-1 margin, Qwen3.5-4B against Qwen3-4B, as `(result, variant, why not)` (G4-26, G4-27).

    It is computed whenever both 4B models have scores, whatever their gates (S2 §8: "applies whether or not Qwen3-4B
    passed its gates"). The variant is `stats.determinism_variant` on the two 4B shares; Qwen3-8B's share never
    triggers it. `resamples` exists for tests: the operator path is always `stats.BOOTSTRAP_RESAMPLES`.
    """
    missing = _not_loaded(results)
    if missing is not None:
        # NEEDS-OWNER(AMB-130): no Qwen3-4B scores means no margin, so the challenger cannot win
        return None, None, f"{missing} did not load"
    if DEFAULT not in shares or CHALLENGER not in shares:
        return None, None, NO_SHARE_NOTE
    variant = stats.determinism_variant(shares[DEFAULT].share, shares[CHALLENGER].share)
    runs = _runs(variant)
    why_not = _undefined(results, runs)
    if why_not is not None:
        return None, variant, why_not
    challenger = [results[CHALLENGER].metrics[run].summary() for run in runs]
    default = [results[DEFAULT].metrics[run].summary() for run in runs]
    result = stats.margin_case1(challenger, default, variant=variant, seed=seed, resamples=resamples)
    return result, variant, None


def _resamples(result: stats.MarginResult) -> frozenset[int]:
    bootstrap = result.bootstrap
    if isinstance(bootstrap, stats.MultiRunBootstrap):
        return frozenset(b.resamples for b in (bootstrap.mean, *bootstrap.per_run))
    return frozenset({bootstrap.resamples})


def _conformance(result: stats.MarginResult) -> tuple[bool, str | None]:
    """S2 §8 fixes 10,000 resamples. Any other count is reported and never switches the pin (G4-27)."""
    counts = _resamples(result)
    if counts == {stats.BOOTSTRAP_RESAMPLES}:
        return True, None
    shown = ", ".join(str(count) for count in sorted(counts))
    return False, f"not S2 §8 conforming (resamples {shown}, needs {stats.BOOTSTRAP_RESAMPLES:,})"


def _no_margin_note(results: Mapping[str, CandidateResult], variant: bool | None) -> str:
    """The note `margin` gave for its None result, rebuilt from the same inputs."""
    missing = _not_loaded(results)
    if missing is not None:
        return f"{missing} did not load"
    if variant is None:
        return NO_SHARE_NOTE
    return _undefined(results, _runs(variant)) or NO_MARGIN_NOTE


def _pinnable(result: CandidateResult | None) -> bool:
    """Eligible and passing all seven gates; `not_measured` is never a pass (G4-25)."""
    return result is not None and result.eligible and result.passes_every_gate


def _why_not_pinnable(result: CandidateResult | None) -> str:
    """The reasons a candidate is not pinnable, for the steps text."""
    if result is None:
        return "no result"
    if result.status != "ran":
        return "did not load"
    reasons = [
        f"gate {gate} {result.gates[gate].status}" if gate in result.gates else f"gate {gate} missing"
        for gate in GATES
        if gate not in result.gates or result.gates[gate].status != "pass"
    ]
    if result.information_only:
        reasons.append(f"information only ({', '.join(result.information_only)})")
    return "; ".join(reasons)


def _margin_step(
    result: stats.MarginResult | None, variant: bool | None, conforming: bool | None, note: str | None
) -> str:
    scope = "" if variant is None else (", runs 1 to 3 (S2 §3.13 variant)" if variant else ", run 1")
    head = f"margin ({MARGIN_READING} reading, case 1{scope})"
    if result is None:
        return f"{head}: not computed: {note}"
    if not conforming:
        return f"{head}: {note}, so it is treated as not holding"
    if result.all_hold:
        return f"{head}: holds"
    failing = ", ".join(str(c.number) for c in result.conditions if not c.holds)
    return f"{head}: does not hold (conditions not holding: {failing})"


def decide(
    results: Mapping[str, CandidateResult], margin_result: stats.MarginResult | None, variant: bool | None
) -> Decision:
    """S2 §8 rules 1 to 5, applied in order (G4-25, G4-28).

    Rule 3 is tested first: a pinnable Qwen3.5-4B with a conforming margin that holds is pinned, under rule 3 when
    Qwen3-4B is pinnable and through rule 4 otherwise. Then rule 2 pins a pinnable Qwen3-4B. Then rule 4 pins a
    pinnable Qwen3-8B. Otherwise rule 5 pins nothing. Qwen3-8B is never pinned while Qwen3-4B is pinnable.
    """
    if margin_result is not None and margin_result.variant != variant:
        raise ValueError("the variant must be the one the margin was computed under")
    pinnable = {candidate: _pinnable(results.get(candidate)) for candidate in CANDIDATE_IDS}
    steps = [
        f"rule 1: {candidate} is pinnable"
        if pinnable[candidate]
        else f"rule 1: {candidate} is not pinnable: {_why_not_pinnable(results.get(candidate))}"
        for candidate in CANDIDATE_IDS
    ]
    conforming: bool | None = None
    if margin_result is None:
        note: str | None = _no_margin_note(results, variant)
    else:
        conforming, note = _conformance(margin_result)
    holds = margin_result is not None and bool(conforming) and margin_result.all_hold
    steps.append(_margin_step(margin_result, variant, conforming, note))

    rule: Rule
    pinned: str | None
    win = pinnable[CHALLENGER] and holds
    steps.append(
        f"rule 3: {CHALLENGER} {'is' if pinnable[CHALLENGER] else 'is not'} pinnable and the margin "
        f"{'holds' if holds else 'does not hold'}"
    )
    if win:
        rule = "rule 3" if pinnable[DEFAULT] else "rule 4 via rule 3"
        pinned = CHALLENGER
    elif pinnable[DEFAULT]:
        steps.append(f"rule 2: {DEFAULT} is pinnable")
        rule = "rule 2"
        pinned = DEFAULT
    else:
        # NEEDS-OWNER(AMB-129): an ineligible Qwen3-4B takes rule 4 as if it failed a gate
        steps.append(f"rule 4: {DEFAULT} is not pinnable; {THIRD} {'is' if pinnable[THIRD] else 'is not'} pinnable")
        rule = "rule 4 (Qwen3-8B)" if pinnable[THIRD] else "rule 5"
        pinned = THIRD if pinnable[THIRD] else None
    if pinned is None:
        steps.append("rule 5: nothing is pinned; the senior sponsor takes option (a) or (b)")
    else:
        steps.append(f"pinned {pinned} under {rule}")
    return Decision(rule, pinned, margin_result, conforming, variant, note, pinnable, tuple(steps))
