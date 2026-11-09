"""The determinism share: H3 spec U19 and rows X27-X29 (S2 §3.13; H1 AMB-25, B9).

Most tests run a schedule of one candidate over a cut of the inputs, whose three scored requests give 6 pairs, and
script the outputs of one run. The rows about broken stores change a loaded `RunView` by hand, since the store itself
would never write them.
"""

from __future__ import annotations

import dataclasses
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff.inputs import FrozenInputs
from app.evaluation.bakeoff.launch import CANDIDATE_IDS
from app.evaluation.bakeoff.records import RunView, StoredCall, StoreError
from app.evaluation.bakeoff.runner import (
    DeterminismError,
    DeterminismShare,
    PreRunStopped,
    RunsExhausted,
    determinism_shares,
)
from app.evaluation.bakeoff.stats import determinism_variant
from app.evaluation.tests._client_fixtures import EMPTY, block_sockets, chat_response
from app.evaluation.tests._runner_fixtures import (
    ScheduleRig,
    ServerScript,
    block_processes,
    frozen_inputs,
    schedule_rig,
    small_inputs,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
RUN_1, RUN_2, RUN_3 = (f"qwen3-4b.run{run}.a1" for run in (1, 2, 3))
SHORT = '{"claims":[],"nonCheckable":[]}'
INVALID = "not json"
PAIRS = 6


def only_the_first_candidate() -> dict[str, ServerScript]:
    return {name: ServerScript(polls=(1,)) for name in ("qwen3.5-4b.pre_run.a1", "qwen3-8b.pre_run.a1")}


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> FrozenInputs:
    return frozen_inputs(tmp_path_factory.mktemp("gold"))


@pytest.fixture(scope="module")
def tiny(full: FrozenInputs) -> FrozenInputs:
    return small_inputs(full, singles=1, sessions=1, probes=1)


def schedule(inputs: FrozenInputs, root: Path, scripts: dict[str, ServerScript] | None = None) -> ScheduleRig:
    return schedule_rig(inputs, root, {**only_the_first_candidate(), **(scripts or {})})


def share_of(inputs: FrozenInputs, root: Path, scripts: dict[str, ServerScript] | None = None) -> DeterminismShare:
    rig = schedule(inputs, root, scripts)
    rig.run()
    return determinism_shares(rig.view())[Q4B]


def chat(item_id: str, *steps: Any) -> ServerScript:
    return ServerScript(chat={item_id: list(steps)})


@pytest.fixture(scope="module")
def stored(full: FrozenInputs, tmp_path_factory: pytest.TempPathFactory) -> RunView:
    """A clean store with three sessions of seven probes, as in X28."""
    with pytest.MonkeyPatch.context() as patch:
        block_sockets(patch)
        block_processes(patch)
        rig = schedule(small_inputs(full, singles=1, sessions=3, probes=7), tmp_path_factory.mktemp("stored"))
        rig.run()
    return rig.view()


def at(view: RunView, run: int, session_index: int | None, position: int | None) -> StoredCall:
    return next(
        row
        for row in view.calls
        if (row.key.phase, row.key.candidate, row.key.run, row.session_index, row.position)
        == ("timed_run", Q4B, run, session_index, position)
    )


def without(view: RunView, *rows: StoredCall) -> RunView:
    return dataclasses.replace(view, calls=tuple(row for row in view.calls if row not in rows))


def with_row(view: RunView, old: StoredCall, new: StoredCall) -> RunView:
    return dataclasses.replace(view, calls=tuple(new if row == old else row for row in view.calls))


# ---------------------------------------------------------------- the share


def test_u19_runs_that_agree_differ_in_nothing(tiny: FrozenInputs, tmp_path: Path) -> None:
    share = share_of(tiny, tmp_path)
    assert (share.candidate, share.differing, share.compared) == (Q4B, 0, PAIRS)
    assert share.share == Fraction(0)


def test_u19_a_difference_in_run_2_counts_once(tiny: FrozenInputs, tmp_path: Path) -> None:
    share = share_of(tiny, tmp_path, {RUN_2: chat("syn_1", chat_response(SHORT))})
    assert (share.differing, share.compared) == (1, PAIRS)
    assert share.share == Fraction(1, 6)


def test_u19_a_difference_in_run_1_counts_against_both_later_runs(tiny: FrozenInputs, tmp_path: Path) -> None:
    share = share_of(tiny, tmp_path, {RUN_1: chat("syn_1", chat_response(SHORT))})
    assert (share.differing, share.compared) == (2, PAIRS)


def test_u19_runs_2_and_3_are_never_compared_with_each_other(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {RUN_2: chat("probe_01", chat_response(INVALID)), RUN_3: chat("cap_1", chat_response(SHORT))}
    assert share_of(tiny, tmp_path, scripts).differing == 2


def test_u19_the_same_json_with_other_spaces_differs(tiny: FrozenInputs, tmp_path: Path) -> None:
    assert EMPTY != SHORT
    assert share_of(tiny, tmp_path, {RUN_3: chat("cap_1", chat_response(SHORT))}).differing == 1


def test_u19_a_retry_in_one_run_only_differs(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = chat("syn_1", chat_response(INVALID), chat_response(EMPTY))
    assert share_of(tiny, tmp_path, {RUN_1: script}).differing == 2


def test_u19_a_difference_in_the_second_attempt_only_differs(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {
        RUN_1: chat("syn_1", chat_response(INVALID), chat_response(EMPTY)),
        RUN_2: chat("syn_1", chat_response(INVALID), chat_response(SHORT)),
        RUN_3: chat("syn_1", chat_response(INVALID), chat_response(EMPTY)),
    }
    assert share_of(tiny, tmp_path, scripts).differing == 1


def test_u19_two_runs_that_time_out_alike_are_the_same(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {run: chat("syn_1", httpx.ReadTimeout, httpx.ReadTimeout) for run in (RUN_1, RUN_2, RUN_3)}
    assert share_of(tiny, tmp_path, scripts).differing == 0


def test_x29_a_failure_kind_differs_when_the_text_does_not(tiny: FrozenInputs, tmp_path: Path) -> None:
    length = chat_response(INVALID, finish_reason="length")
    scripts = {
        RUN_1: chat("syn_1", chat_response(INVALID), chat_response(EMPTY)),
        RUN_2: chat("syn_1", chat_response(INVALID), chat_response(EMPTY)),
        RUN_3: chat("syn_1", length, chat_response(EMPTY)),
    }
    assert share_of(tiny, tmp_path, scripts).differing == 1


def test_x29_a_cut_off_output_with_the_same_text_as_a_valid_one_differs(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = chat("syn_1", chat_response(EMPTY, finish_reason="length"))
    assert share_of(tiny, tmp_path, {RUN_2: script}).differing == 1


def test_amb_77_reasoning_text_is_raw_text(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = chat("syn_1", chat_response(EMPTY, reasoning_content="Let me check"))
    assert share_of(tiny, tmp_path, {RUN_2: script}).differing == 1


def test_u19_usage_is_not_output(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = chat("syn_1", chat_response(EMPTY, prompt_tokens=900, completion_tokens=77))
    assert share_of(tiny, tmp_path, {RUN_2: script}).differing == 0


def test_amb_77_each_probe_of_each_session_is_its_own_pair(full: FrozenInputs, tmp_path: Path) -> None:
    inputs = small_inputs(full, singles=1, sessions=2, probes=2)
    script = chat("probe_02", None, chat_response(INVALID))  # only the second session's
    share = share_of(inputs, tmp_path, {RUN_2: script})
    assert (share.differing, share.compared) == (1, 2 * (1 + 2 + 4))


# ---------------------------------------------------------------- what stays out


def test_u19_warm_ups_are_left_out(tiny: FrozenInputs, tmp_path: Path) -> None:
    assert share_of(tiny, tmp_path, {RUN_2: chat("warmup", chat_response(SHORT))}).differing == 0


def test_u19_the_unconstrained_pass_and_the_contended_run_are_left_out(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {
        "qwen3-4b.unconstrained.a1": chat("syn_1", chat_response(SHORT)),
        "qwen3-4b.contended.a1": chat("cap_1", chat_response(SHORT)),
    }
    assert share_of(tiny, tmp_path, scripts).differing == 0


def test_x27_a_voided_attempt_does_not_count(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"syn_1": [chat_response(SHORT)], "cap_1": [chat_response(EMPTY, cached_tokens=1)]})
    rig = schedule(tiny, tmp_path, {RUN_2: script})
    rig.run()
    view = rig.view()
    assert view.counted_attempt(Q4B, 2) == 2
    assert determinism_shares(view)[Q4B].differing == 0


def test_u19_a_candidate_with_no_timed_records_is_absent(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule(tiny, tmp_path)
    rig.run()
    assert list(determinism_shares(rig.view())) == [Q4B]


def test_u19_a_pre_run_alone_gives_no_share(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = chat("warmup", chat_response(EMPTY, cached_tokens=1))
    rig = schedule(tiny, tmp_path, {"qwen3-4b.pre_run.a1": script})
    with pytest.raises(PreRunStopped):
        rig.run()
    assert len(rig.view().calls) == 1
    assert determinism_shares(rig.view()) == {}


def test_u19_a_store_with_no_timed_records_gives_no_share(stored: RunView) -> None:
    empty = dataclasses.replace(stored, calls=(), runs=())
    assert determinism_shares(empty) == {}


# ---------------------------------------------------------------- what is an error


def test_x27_a_run_with_no_counted_attempt_raises(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {f"qwen3-4b.run2.a{n}": chat("syn_1", chat_response(EMPTY, cached_tokens=1)) for n in (1, 2, 3)}
    rig = schedule(tiny, tmp_path, scripts)
    with pytest.raises(RunsExhausted):
        rig.run()
    with pytest.raises(StoreError, match="run 2"):
        determinism_shares(rig.view())


def test_u19_a_run_with_records_but_no_call_raises_and_is_never_left_out(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {f"qwen3-4b.run1.a{n}": ServerScript(spawn_error=PermissionError("denied")) for n in (1, 2, 3)}
    rig = schedule(tiny, tmp_path, scripts)
    with pytest.raises(RunsExhausted):
        rig.run()
    assert {stored.key.phase for stored in rig.view().calls} == {"pre_run"}
    with pytest.raises(StoreError, match="run 1"):
        determinism_shares(rig.view())


def test_u19_records_of_a_candidate_with_calls_but_no_run_record_raise(stored: RunView) -> None:
    with pytest.raises(StoreError):
        determinism_shares(dataclasses.replace(stored, runs=()))


def test_x28_a_missing_counterpart_names_the_candidate_the_run_and_the_place(stored: RunView) -> None:
    broken = without(stored, at(stored, 2, 3, 7))
    with pytest.raises(DeterminismError) as caught:
        determinism_shares(broken)
    assert str(caught.value) == f"{Q4B} run 2: ('probe', 3, 7) has no counterpart"


def test_x28_a_place_only_in_the_later_run_raises(stored: RunView) -> None:
    with pytest.raises(DeterminismError, match="run 2: .'probe', 3, 7. has no counterpart"):
        determinism_shares(without(stored, at(stored, 1, 3, 7)))


def test_x28_run_3_is_held_to_the_same_rule(stored: RunView) -> None:
    with pytest.raises(DeterminismError, match="run 3: .'single', None, 1. has no counterpart"):
        determinism_shares(without(stored, at(stored, 3, None, 1)))


def test_x28_a_duplicate_place_raises(stored: RunView) -> None:
    broken = dataclasses.replace(stored, calls=(*stored.calls, at(stored, 2, 1, 1)))
    with pytest.raises(DeterminismError, match="run 2 holds .'probe', 1, 1. twice"):
        determinism_shares(broken)


def test_u19_another_item_at_a_place_raises(stored: RunView) -> None:
    old = at(stored, 2, 1, 1)
    new = dataclasses.replace(old, call=at(stored, 2, 1, 2).call)
    with pytest.raises(DeterminismError, match="run 2: .'probe', 1, 1. is not the same request as in run 1"):
        determinism_shares(with_row(stored, old, new))


def test_u19_another_item_id_on_the_same_request_raises(stored: RunView) -> None:
    old = at(stored, 2, 2, 2)
    new = dataclasses.replace(old, call=dataclasses.replace(old.call, item_id="probe_99"))
    with pytest.raises(DeterminismError, match="run 2: .'probe', 2, 2. is not the same request as in run 1"):
        determinism_shares(with_row(stored, old, new))


def test_u19_another_request_under_the_same_item_id_raises(stored: RunView) -> None:
    old = at(stored, 3, 2, 3)
    attempt = dataclasses.replace(old.call.attempts[0], request_sha256="f" * 64)
    new = dataclasses.replace(old, call=dataclasses.replace(old.call, attempts=(attempt,)))
    with pytest.raises(DeterminismError, match="run 3"):
        determinism_shares(with_row(stored, old, new))


# ---------------------------------------------------------------- the value


@pytest.mark.parametrize(
    ("differing", "compared", "share"),
    [(3, 294, Fraction(1, 98)), (1, 172, Fraction(1, 172)), (0, 294, Fraction(0)), (294, 294, Fraction(1))],
)
def test_u19_the_spec_s_shares_are_exact(differing: int, compared: int, share: Fraction) -> None:
    assert DeterminismShare(Q4B, differing, compared).share == share


@pytest.mark.parametrize(("differing", "compared"), [(0, 0), (1, 0), (-1, 6), (7, 6)])
def test_u19_a_count_that_is_no_share_is_refused(differing: int, compared: int) -> None:
    with pytest.raises(DeterminismError):
        DeterminismShare(Q4B, differing, compared)


def test_u19_h1_takes_the_share_as_it_is() -> None:
    above = DeterminismShare(Q4B, 1, 140).share
    none = DeterminismShare(Q35, 0, 140).share
    assert determinism_variant(above, none) is True
    assert determinism_variant(none, none) is False
