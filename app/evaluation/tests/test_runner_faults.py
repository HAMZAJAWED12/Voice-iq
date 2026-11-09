"""Faults, voids and repeats: H3 spec U7, U14-U18, U20, U22, the runner's side of D11 and D12, and rows X1-X3, X5, X6,
X20, X21, X23, X24, X31, X36, X38-X40, X46-X48, X51, X52 (S2 §3.13, §6 gate J, §9).

What decides a test here is what is left in the run directory. A void attempt's calls stay stored, the attempt that
completes is the run, and no fault or void line may hold a prompt, a transcript or server text.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import pickle
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff import client as client_module
from app.evaluation.bakeoff.client import ChatMessage, ChatRequest, ClientFault, ModelServerClient
from app.evaluation.bakeoff.inputs import FrozenInputs, freeze_inputs
from app.evaluation.bakeoff.launch import (
    CANDIDATE_IDS,
    EXIT_SETTLE_SEC,
    LOG_POLL_SEC,
    ServerStopFailed,
    cmdline_bytes,
    frozen_argv,
)
from app.evaluation.bakeoff.records import RunKey, RunStore, RunView, StoredCall, StoreError, load_run
from app.evaluation.bakeoff.runner import RunsExhausted
from app.evaluation.tests._client_fixtures import EMPTY, block_sockets, chat_response, error_response
from app.evaluation.tests._fixtures import write_version
from app.evaluation.tests._runner_fixtures import (
    CAP_SESSIONS,
    CONDITIONS,
    LOG_BIND_FAILED,
    LOG_DEFAULT,
    LOG_VERBOSE,
    PORT,
    PROBE_ITEMS,
    PROMPTS,
    SCHEDULE_CANDIDATES,
    WARMUP,
    ServerScript,
    block_processes,
    fixed_now,
    frozen_inputs,
    gold_rows,
    loading_response,
    no_fsync,
    schedule_rig,
    small_inputs,
    stop_before,
    unit_of,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
RUN_1 = "qwen3-4b.run1.a1"
RUN_1_RETRY = "qwen3-4b.run1.a2"
NEXT = "qwen3.5-4b.run1.a1"
KEY = RunKey(Q4B, "timed_run", 1, 1)
RETRY = RunKey(Q4B, "timed_run", 1, 2)
TINY_CALLS = 4  # the warm-up, one single, one cap call, one probe
INVALID = "not json"
SENTINEL = "SENTINEL_TEXT"
ARGV = frozen_argv(CONDITIONS, SCHEDULE_CANDIDATES[0])
COUNT_ROUTE = "/v1/chat/completions/input_tokens"
Fail = Callable[[str, RunKey], BaseException | None]


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


@pytest.fixture(scope="module")
def five(tmp_path_factory: pytest.TempPathFactory) -> FrozenInputs:
    """Five singles, as in X1."""
    return small_inputs(frozen_inputs(tmp_path_factory.mktemp("gold"), syn_5=1), singles=5, sessions=1, probes=1)


def calls_of(view: RunView, key: RunKey) -> tuple[StoredCall, ...]:
    return tuple(stored for stored in view.calls if stored.key == key)


def items_of(view: RunView, key: RunKey) -> list[str]:
    return [stored.call.item_id for stored in calls_of(view, key)]


def fault_lines(view: RunView) -> list[tuple[RunKey, str, str | None, str]]:
    return [(fault.key, fault.kind, fault.item_id, fault.detail) for fault in view.faults]


def run_lines(view: RunView) -> list[tuple[RunKey, str, int]]:
    return [(run.key, run.status, run.calls) for run in view.runs]


def assert_void_then_complete(view: RunView, calls: int) -> None:
    """Attempt 1 of run 1 is void under fault 1, and attempt 2 is that run for every gate."""
    assert [(void.key, void.fault_id) for void in view.voids] == [(KEY, 1)]
    assert run_lines(view) == [(KEY, "void", calls), (RETRY, "complete", TINY_CALLS)]
    assert view.counted_attempt(Q4B, 1) == 2
    assert {stored.key for stored in view.counted_calls(Q4B, 1)} == {RETRY}


def raising(hook: str, *units: str, error: BaseException | None = None) -> Fail:
    """A `fail` that makes `hook` raise, on `units` only if any are named."""

    def fail(name: str, key: RunKey) -> BaseException | None:
        if name == hook and (not units or unit_of(key) in units):
            return error or RuntimeError("synthetic")
        return None

    return fail


def together(*fails: Fail) -> Fail:
    def fail(name: str, key: RunKey) -> BaseException | None:
        return next((error for error in (each(name, key) for each in fails) if error is not None), None)

    return fail


def cached(tokens: int = 7) -> httpx.Response:
    return chat_response(EMPTY, cached_tokens=tokens)


# ---------------------------------------------------------------- U7: a launch that fails


def default_log(old: str, new: str) -> str:
    assert old in LOG_DEFAULT
    return LOG_DEFAULT.replace(old, new)


LAUNCH_FAULTS: dict[str, tuple[ServerScript, str, str]] = {
    "x51-spawn": (
        ServerScript(spawn_error=PermissionError("denied")),
        "launch_failed",
        "spawn failed: PermissionError",
    ),
    "x3-dies-loading": (
        ServerScript(polls=(None, 1), props_states=(httpx.ConnectError,)),
        "launch_failed",
        "server exited with code 1",
    ),
    "x5-port-in-use": (ServerScript(polls=(1,), log=LOG_BIND_FAILED), "launch_failed", f"port {PORT} in use"),
    "x6-stale-server": (
        ServerScript(polls=(None, 1), log=LOG_BIND_FAILED, cmdline=b""),
        "launch_failed",
        f"port {PORT} in use",
    ),
    "x48-zombie": (ServerScript(polls=(None, 1), cmdline=b""), "launch_failed", "server exited with code 1"),
    "x40-deadline": (
        ServerScript(props_states=tuple(loading_response() for _ in range(601))),
        "ready_timeout",
        "not ready after 600 s",
    ),
    "u7-log-context": (
        ServerScript(log=default_log("n_ctx_slot = 12288", "n_ctx_slot = 8192")),
        "launch_check",
        "context: log n_ctx_slot=8192 expected=12288",
    ),
    "x20-props-context": (
        ServerScript(props={"default_generation_settings": {"n_ctx": 8192}}),
        "launch_check",
        "context: props n_ctx=8192 expected=12288",
    ),
    "x21-build": (
        ServerScript(props={"build_info": "b11936-27b7a8a0"}),
        "launch_check",
        "build: build_info differs from b1-27b7a8a",
    ),
    "x31-command-line": (
        ServerScript(cmdline=cmdline_bytes((*ARGV[:4], "8192", *ARGV[5:]))),
        "launch_check",
        "cmdline: differs at argument 4",
    ),
    "the-slow-mode": (ServerScript(log=LOG_VERBOSE), "launch_check", "verbosity: log verbosity=4 expected=3"),
    "no-listening-line": (
        ServerScript(log="\n".join(line for line in LOG_DEFAULT.split("\n") if "server_on_li" not in line)),
        "launch_check",
        "log: no listening line",
    ),
}


@pytest.mark.parametrize(("script", "kind", "detail"), LAUNCH_FAULTS.values(), ids=LAUNCH_FAULTS.keys())
def test_u7_a_failed_timed_launch_voids_that_attempt_and_it_repeats(
    tiny: FrozenInputs, tmp_path: Path, script: ServerScript, kind: str, detail: str
) -> None:
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, kind, None, detail)]
    assert_void_then_complete(view, 0)
    assert calls_of(view, KEY) == ()
    assert KEY not in {launch.key for launch in view.launches}


def test_u7_a_wrong_command_line_is_found_before_the_log_is_read(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(cmdline=cmdline_bytes((*ARGV, "--verbose")))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "launch_check", None, "cmdline: differs at argument 30")]
    assert not [path for path in rig.host.files.reads if Path(path).stem == RUN_1]


def test_x5_a_port_that_stays_in_use_ends_in_runs_exhausted(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {f"qwen3-4b.run1.a{n}": ServerScript(polls=(1,), log=LOG_BIND_FAILED) for n in (1, 2, 3)}
    rig = schedule_rig(tiny, tmp_path, scripts)
    with pytest.raises(RunsExhausted):
        rig.run()
    assert {fault.detail for fault in rig.view().faults} == {f"port {PORT} in use"}


# ---------------------------------------------------------------- U15: a server that exits


def test_x1_a_server_that_exits_mid_run_voids_the_attempt(five: FrozenInputs, tmp_path: Path) -> None:
    unit = "qwen3-4b.run2.a1"
    rig = schedule_rig(
        five, tmp_path, {unit: ServerScript(die_at=4, die_polls=3)}, fail=stop_before("qwen3-8b.run3.a1")
    )
    view = rig.run_to_the_stop()
    key = RunKey(Q4B, "timed_run", 2, 1)
    assert items_of(view, key) == ["warmup", "syn_1", "syn_2", "syn_3"]
    assert [attempt.failure_kind for attempt in calls_of(view, key)[-1].call.attempts] == ["server_error"] * 2
    assert fault_lines(view) == [(key, "server_exit", "syn_3", "server exited with code -11")]
    assert [void.key for void in view.voids] == [key]
    assert (key, "void", 4) in run_lines(view)
    launch = next(launch for launch in view.launches if launch.key == key)
    assert (launch.exit_code, launch.stopped_how) == (-11, "already_exited")
    assert view.counted_attempt(Q4B, 2) == 2
    counted = view.counted_calls(Q4B, 2)
    assert next(stored for stored in counted if stored.call.item_id == "syn_3").call.outcome == "valid"
    assert [stored.call.item_id for stored in counted[:5]] == [f"syn_{number}" for number in range(1, 6)]


def test_x2_a_server_that_dies_between_calls_is_found_on_the_next_call(five: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(five, tmp_path, {RUN_1: ServerScript(die_after=3)}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert items_of(view, KEY) == ["warmup", "syn_1", "syn_2", "syn_3"]
    assert calls_of(view, KEY)[-1].call.outcome == "server_error"
    assert fault_lines(view) == [(KEY, "server_exit", "syn_3", "server exited with code -11")]
    assert (KEY, "void", 4) in run_lines(view)


def test_x36_the_last_call_of_a_dying_process_gets_the_grace(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(die_at=TINY_CALLS, die_code=-6, die_polls=20)
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", "probe_01", "server exited with code -6")]
    assert_void_then_complete(view, TINY_CALLS)
    assert sum(rig.host.clock.sleeps) == pytest.approx(20 * LOG_POLL_SEC)


def test_u15_a_dropped_connection_on_a_live_server_waits_out_the_grace_and_goes_on(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    script = ServerScript(chat={"syn_1": [httpx.ConnectError, httpx.ConnectError]})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert view.faults == ()
    assert calls_of(view, KEY)[1].call.outcome == "server_error"
    assert view.runs[0].status == "complete"
    assert sum(rig.host.clock.sleeps) == pytest.approx(EXIT_SETTLE_SEC, abs=2 * LOG_POLL_SEC)


def test_u15_only_the_last_attempt_decides_so_a_later_answer_means_no_check(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"syn_1": [httpx.ConnectError, chat_response(EMPTY)]})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert calls_of(view, KEY)[1].call.outcome == "valid"
    assert rig.host.clock.sleeps == []


def test_u15_a_timeout_polls_once_and_finds_an_exit(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"syn_1": [httpx.ReadTimeout, httpx.ReadTimeout]}, polls=(None, None, 5))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", "syn_1", "server exited with code 5")]
    assert_void_then_complete(view, 2)
    assert rig.host.clock.sleeps == []


@pytest.mark.parametrize(
    ("status", "kind"), [(500, "server_error"), (400, "invalid_request_error")], ids=["http-500", "http-400"]
)
def test_u15_an_http_error_polls_once_and_finds_an_exit(
    tiny: FrozenInputs, tmp_path: Path, status: int, kind: str
) -> None:
    steps = [error_response(status, kind, "x"), error_response(status, kind, "x")]
    script = ServerScript(chat={"syn_1": steps}, polls=(None, None, 5))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", "syn_1", "server exited with code 5")]
    assert_void_then_complete(view, 2)
    assert rig.host.clock.sleeps == []


def test_u15_an_answer_needs_no_check_even_when_it_is_invalid(tiny: FrozenInputs, tmp_path: Path) -> None:
    """The poll that would have found this exit is the last one, so no failed call takes the blame for it."""
    script = ServerScript(chat={"syn_1": [chat_response(INVALID), chat_response(INVALID)]}, polls=(None, None, 5))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", None, "server exited with code 5")]
    assert_void_then_complete(view, TINY_CALLS)


def test_u15_a_server_found_dead_after_its_last_call_voids_the_run(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(die_after=TINY_CALLS, die_code=3)
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", None, "server exited with code 3")]
    assert_void_then_complete(view, TINY_CALLS)
    assert all(stored.call.outcome == "valid" for stored in calls_of(view, KEY))


def test_amb_78_a_slow_or_killed_stop_after_a_live_run_is_no_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(waits=(None, -9))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert view.faults == ()
    launch = next(launch for launch in view.launches if launch.key == KEY)
    assert (launch.exit_code, launch.stopped_how) == (-9, "killed")
    assert ("kill", RUN_1) in rig.host.events


# ---------------------------------------------------------------- U17: model failures from a live server


@pytest.mark.parametrize(
    ("steps", "outcome"),
    [
        ([error_response(500, "server_error", "x"), error_response(500, "server_error", "x")], "server_error"),
        (
            [
                error_response(400, "invalid_request_error", "x"),
                error_response(400, "invalid_request_error", "x"),
            ],
            "server_error",
        ),
        ([httpx.ReadTimeout, httpx.ReadTimeout], "timeout"),
        ([httpx.Response(200, content=b"{"), httpx.Response(200, content=b"{")], "server_error"),
        ([chat_response(INVALID), chat_response(INVALID)], "invalid_output"),
        ([chat_response(INVALID), chat_response(EMPTY, finish_reason="length")], "cut_off"),
    ],
    ids=["http-500", "http-400", "timeout", "malformed-200", "invalid", "cut-off"],
)
def test_u17_a_failure_from_a_live_server_is_stored_and_the_run_goes_on(
    tiny: FrozenInputs, tmp_path: Path, steps: list[Any], outcome: str
) -> None:
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"syn_1": steps})}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert [stored.call.outcome for stored in calls_of(view, KEY)] == ["valid", outcome, "valid", "valid"]
    assert view.faults == ()
    assert run_lines(view) == [(KEY, "complete", TINY_CALLS)]
    assert rig.host.clock.sleeps == []


def test_u17_the_error_type_of_a_4xx_is_kept(tiny: FrozenInputs, tmp_path: Path) -> None:
    steps = [error_response(400, "invalid_request_error", "x"), error_response(400, "invalid_request_error", "x")]
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"syn_1": steps})}, fail=stop_before(NEXT))
    attempts = calls_of(rig.run_to_the_stop(), KEY)[1].call.attempts
    assert [attempt.server_error_type for attempt in attempts] == ["invalid_request_error"] * 2


@pytest.mark.parametrize(
    ("step", "outcome"),
    [
        pytest.param(lambda: chat_response(INVALID), "invalid_output", id="invalid"),
        pytest.param(lambda: httpx.ReadTimeout, "timeout", id="timeout"),
        pytest.param(lambda: error_response(500, "server_error", "x"), "server_error", id="http-500"),
    ],
)
def test_u12_u17_a_pass_with_every_call_failed_twice_is_complete_with_no_fault(
    tiny: FrozenInputs, tmp_path: Path, step: Callable[[], Any], outcome: str
) -> None:
    """U12 holds the pass to U17, and every unconstrained call of the smoke was invalid on both attempts (F85)."""
    key = RunKey(Q4B, "unconstrained", None, 1)
    chat = {frozen.item_id: [step(), step()] for frozen in tiny.all_requests()}
    rig = schedule_rig(tiny, tmp_path, {"qwen3-4b.unconstrained.a1": ServerScript(chat=chat)})
    result = rig.run()
    view = rig.view()
    calls = calls_of(view, key)
    assert [(stored.call.outcome, len(stored.call.attempts)) for stored in calls] == [(outcome, 2)] * TINY_CALLS
    assert (view.faults, result.voided) == ((), ())
    assert [line for line in run_lines(view) if line[0] == key] == [(key, "complete", TINY_CALLS)]


# ---------------------------------------------------------------- U16: cached tokens


def test_x23_cached_tokens_on_the_second_attempt_only_are_a_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"syn_1": [chat_response(INVALID), cached(512)]})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    stored = calls_of(view, KEY)[-1]
    assert (stored.call.item_id, stored.call.outcome) == ("syn_1", "valid")
    assert fault_lines(view) == [(KEY, "cached_tokens", "syn_1", "cached_tokens=512 cache_n=512 attempt=2")]
    assert_void_then_complete(view, 2)


def test_u8_cached_tokens_on_a_warm_up_are_a_fault_like_any_other(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"warmup": [cached(7)]})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "cached_tokens", "warmup", "cached_tokens=7 cache_n=7 attempt=1")]
    assert_void_then_complete(view, 1)


TIMINGS = {
    "cache_n": 5,
    "prompt_n": 100,
    "prompt_ms": 1.5,
    "prompt_per_token_ms": 0.015,
    "prompt_per_second": 66666.7,
    "predicted_n": 10,
    "predicted_ms": 20.0,
    "predicted_per_token_ms": 2.0,
    "predicted_per_second": 500.0,
}
NO_DETAILS = {"completion_tokens": 10, "prompt_tokens": 100, "total_tokens": 110}


@pytest.mark.parametrize(
    ("step", "detail"),
    [
        (chat_response(EMPTY, cached_tokens=3, timings=False), "cached_tokens=3 cache_n=None attempt=1"),
        (chat_response(EMPTY, extra={"timings": TIMINGS}), "cached_tokens=0 cache_n=5 attempt=1"),
        (
            chat_response(EMPTY, extra={"timings": TIMINGS, "usage": NO_DETAILS}),
            "cached_tokens=None cache_n=5 attempt=1",
        ),
    ],
    ids=["usage-only", "timings-only", "no-details"],
)
def test_u16_either_counter_above_zero_is_a_fault(
    tiny: FrozenInputs, tmp_path: Path, step: httpx.Response, detail: str
) -> None:
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"syn_1": [step]})}, fail=stop_before(NEXT))
    assert fault_lines(rig.run_to_the_stop()) == [(KEY, "cached_tokens", "syn_1", detail)]


def test_u16_no_count_at_all_is_no_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    step = chat_response(EMPTY, timings=False, extra={"usage": NO_DETAILS})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"syn_1": [step]})}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    attempt = calls_of(view, KEY)[1].call.attempts[0]
    assert attempt.usage is not None
    assert (attempt.usage.cached_tokens, attempt.timings) == (None, None)
    assert view.faults == ()


def test_u16_comes_before_the_exit_check_so_one_call_gives_one_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    steps = [chat_response(INVALID, cached_tokens=4), httpx.ReadTimeout]
    script = ServerScript(chat={"syn_1": steps}, polls=(None, None, 5))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "cached_tokens", "syn_1", "cached_tokens=4 cache_n=4 attempt=1")]
    assert_void_then_complete(view, 2)


def test_u16_applies_on_every_phase(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {
        "qwen3-4b.unconstrained.a1": ServerScript(chat={"cap_1": [cached(2)]}),
        "qwen3-4b.contended.a1": ServerScript(chat={"probe_01": [cached(2)]}),
    }
    rig = schedule_rig(tiny, tmp_path, scripts)
    rig.run()
    assert [(fault.key.phase, fault.kind) for fault in rig.view().faults] == [
        ("unconstrained", "cached_tokens"),
        ("contended", "cached_tokens"),
    ]


# ---------------------------------------------------------------- U14: client faults


def test_x24_a_client_fault_mid_session_run_stops_and_voids_the_whole_attempt(
    full: FrozenInputs, tmp_path: Path
) -> None:
    inputs = small_inputs(full, singles=1, sessions=2, probes=8)
    script = ServerScript(
        chat={"probe_07": [None, chat_response(INVALID)]},
        counts={"probe_07": error_response(503, "unavailable_error", "synthetic")},
    )
    rig = schedule_rig(inputs, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    calls = calls_of(view, KEY)
    assert [(stored.session_index, stored.position) for stored in calls[-2:]] == [(2, 5), (2, 6)]
    detail = f"ServerCheckError path={COUNT_ROUTE} http_status=503"
    assert fault_lines(view) == [(KEY, "client_fault", "probe_07", detail)]
    assert [void.key for void in view.voids] == [KEY]
    assert (KEY, "void", len(calls)) in run_lines(view)
    assert calls_of(view, RETRY)[0].role == "warmup"
    assert len(calls_of(view, RETRY)) == 1 + 1 + 2 * 9


def test_u14_a_repair_that_does_not_fit_voids_the_attempt_and_its_first_attempt_is_not_stored(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    script = ServerScript(chat={"cap_1": [chat_response(INVALID)]}, counts={"cap_1": 6964})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "client_fault", "cap_1", "ContextBudgetExceeded uses=11060 limit=11059")]
    assert items_of(view, KEY) == ["warmup", "syn_1"]
    assert_void_then_complete(view, 2)
    # no http_status at all: the grace, as for a dropped connection
    assert sum(rig.host.clock.sleeps) == pytest.approx(EXIT_SETTLE_SEC, abs=2 * LOG_POLL_SEC)


def test_u14_a_repair_that_just_fits_is_sent(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"cap_1": [chat_response(INVALID)]}, counts={"cap_1": 6963})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert view.faults == ()
    call = calls_of(view, KEY)[2].call
    assert (call.outcome, call.context_checks[0].uses) == ("valid", 11059)


def test_u14_a_client_fault_with_the_server_gone_is_a_server_exit(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"syn_1": [chat_response(INVALID)]}, die_after=2, die_polls=4)
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", "syn_1", "server exited with code -11")]
    assert items_of(view, KEY) == ["warmup"]
    assert_void_then_complete(view, 1)


def test_amb_71_a_client_fault_of_no_known_kind_logs_its_class_and_never_its_text(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    class Faulty(ModelServerClient):
        def probe(self, request: ChatRequest, **kwargs: Any) -> Any:
            raise ClientFault(SENTINEL)

    rig = schedule_rig(tiny, tmp_path, fail=stop_before(NEXT))

    def faulty(base_url: str, n_ctx: int) -> ModelServerClient:
        return Faulty(base_url, transport=httpx.MockTransport(rig.host.handle), n_ctx=n_ctx)

    rig.deps = dataclasses.replace(rig.deps, client_factory=faulty)
    with pytest.raises(RunsExhausted):
        rig.run()
    assert fault_lines(rig.view())[0] == (KEY, "client_fault", "probe_01", "ClientFault")


def test_u14_a_fault_with_a_status_polls_at_once_and_one_with_none_gets_the_grace(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    with_status = ServerScript(
        chat={"syn_1": [chat_response(INVALID)]},
        counts={"syn_1": error_response(503, "unavailable_error", "synthetic")},
    )
    rig = schedule_rig(tiny, tmp_path, {RUN_1: with_status}, fail=stop_before(NEXT))
    rig.run_to_the_stop()
    assert rig.host.clock.sleeps == []
    no_status = ServerScript(chat={"syn_1": [chat_response(INVALID)]}, counts={"syn_1": httpx.ConnectError})
    root = tmp_path / "second"
    root.mkdir()
    rig = schedule_rig(tiny, root, {RUN_1: no_status}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert sum(rig.host.clock.sleeps) == pytest.approx(EXIT_SETTLE_SEC, abs=2 * LOG_POLL_SEC)
    assert fault_lines(view) == [
        (KEY, "client_fault", "syn_1", f"ServerCheckError path={COUNT_ROUTE} http_status=None")
    ]


def test_u14_a_fault_with_a_status_finds_an_exit_by_one_poll(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(
        chat={"syn_1": [chat_response(INVALID)]},
        counts={"syn_1": error_response(503, "unavailable_error", "synthetic")},
        polls=(None, None, 7),
    )
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "server_exit", "syn_1", "server exited with code 7")]
    assert rig.host.clock.sleeps == []
    assert_void_then_complete(view, 1)


# ---------------------------------------------------------------- U18: void and repeat


def test_u18_the_rotation_holds_around_a_repeat(tiny: FrozenInputs, tmp_path: Path) -> None:
    unit = "qwen3-8b.run2.a1"
    rig = schedule_rig(tiny, tmp_path, {unit: ServerScript(chat={"syn_1": [cached()]})})
    result = rig.run()
    popens = [name for what, name in rig.host.events if what == "popen" and ".run" in name]
    assert popens[3:7] == ["qwen3.5-4b.run2.a1", unit, "qwen3-8b.run2.a2", "qwen3-4b.run2.a1"]
    assert popens[7:] == ["qwen3-8b.run3.a1", "qwen3-4b.run3.a1", "qwen3.5-4b.run3.a1"]
    assert Path(rig.run_dir, "logs", f"{unit}.log").exists()
    assert Path(rig.run_dir, "logs", "qwen3-8b.run2.a2.log").exists()
    assert result.voided == (RunKey(Q8B, "timed_run", 2, 1),)


def test_u18_a_void_attempt_stays_stored_with_no_gap(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"cap_1": [cached()]})}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    assert len(calls_of(view, KEY)) == 3
    assert [stored.sequence for stored in view.calls] == list(range(1, len(view.calls) + 1))


def test_u18_the_order_of_a_void(tiny: FrozenInputs, tmp_path: Path) -> None:
    sizes: dict[str, int] = {}

    def watch(name: str, key: RunKey) -> BaseException | None:
        if name == "after_timed_run" and key == KEY:
            for file in ("faults.jsonl", "voids.jsonl", "runs.jsonl"):
                sizes[file] = Path(rig.run_dir, file).stat().st_size
            sizes["launches"] = Path(rig.run_dir, "launches.jsonl").read_bytes().count(b"\n")
        return None

    rig = schedule_rig(
        tiny,
        tmp_path,
        {RUN_1: ServerScript(chat={"syn_1": [cached()]})},
        fail=together(stop_before(NEXT), watch),
    )
    view = rig.run_to_the_stop()
    assert sizes["faults.jsonl"] > 0
    assert sizes["voids.jsonl"] == sizes["runs.jsonl"] == 0
    assert sizes["launches"] == 3  # the pre-runs': this launch's line comes after the hook
    events = [what for what, name in rig.host.events if name == RUN_1]
    assert events == ["before_timed_run", "popen", "terminate", "after_timed_run"]
    assert_void_then_complete(view, 2)


def test_x46_a_deterministic_fault_reaches_the_stop_with_everything_kept(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {
        f"qwen3-4b.run1.a{n}": ServerScript(chat={"cap_1": [chat_response(INVALID)]}, counts={"cap_1": 6964})
        for n in (1, 2, 3)
    }
    rig = schedule_rig(tiny, tmp_path, scripts)
    with pytest.raises(RunsExhausted) as caught:
        rig.run()
    keys = [RunKey(Q4B, "timed_run", 1, n) for n in (1, 2, 3)]
    assert (caught.value.key, caught.value.run_dir) == (keys[2], rig.run_dir)
    view = rig.view()
    assert [fault.key for fault in view.faults] == keys
    assert [void.key for void in view.voids] == keys
    assert run_lines(view) == [(key, "void", 2) for key in keys]
    assert all(items_of(view, key) == ["warmup", "syn_1"] for key in keys)
    assert NEXT not in rig.host.launches
    with pytest.raises(StoreError):
        view.counted_attempt(Q4B, 1)


def test_u18_a_third_void_of_a_later_run_stops_the_schedule_there(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {f"qwen3.5-4b.run3.a{n}": ServerScript(chat={"syn_1": [cached()]}) for n in (1, 2, 3)}
    rig = schedule_rig(tiny, tmp_path, scripts)
    with pytest.raises(RunsExhausted) as caught:
        rig.run()
    assert caught.value.key == RunKey(Q35, "timed_run", 3, 3)
    assert not [name for name in rig.host.launches if "unconstrained" in name or "contended" in name]


def test_u18_runs_exhausted_survives_copy_and_pickle() -> None:
    error = RunsExhausted(RunKey(Q4B, "timed_run", 1, 3), "/data/runs/run_1")
    for clone in (copy.copy(error), pickle.loads(pickle.dumps(error))):
        assert (clone.key, clone.run_dir) == (error.key, error.run_dir)
    assert str(error) == f"{Q4B} run 1 was void 3 times: see /data/runs/run_1"


@pytest.mark.parametrize("phase", ["unconstrained", "contended"])
def test_x47_a_third_void_of_an_ungated_unit_leaves_it_absent_and_the_schedule_goes_on(
    tiny: FrozenInputs, tmp_path: Path, phase: str
) -> None:
    scripts = {f"qwen3-4b.{phase}.a{n}": ServerScript(chat={"warmup": [cached()]}) for n in (1, 2, 3)}
    rig = schedule_rig(tiny, tmp_path, scripts)
    result = rig.run()
    keys = tuple(RunKey(Q4B, phase, None, n) for n in (1, 2, 3))  # type: ignore[arg-type]
    assert result.voided == keys
    view = rig.view()
    assert [(run.key, run.status) for run in view.runs if run.key in keys] == [(key, "void") for key in keys]
    assert sum(run.status == "complete" for run in view.runs) == 15 - 1
    assert "qwen3-8b.contended.a1" in rig.host.launches


def test_u18_a_void_ungated_unit_repeats_and_then_completes(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {"qwen3-4b.contended.a1": ServerScript(chat={"warmup": [cached()]})})
    result = rig.run()
    assert result.voided == (RunKey(Q4B, "contended", None, 1),)
    assert (RunKey(Q4B, "contended", None, 2), "complete", 3) in run_lines(rig.view())


# ---------------------------------------------------------------- U20: a hook that fails


def test_x38_a_failed_before_hook_launches_nothing_and_no_after_hook_fires(tiny: FrozenInputs, tmp_path: Path) -> None:
    fail = together(raising("before_timed_run", RUN_1), stop_before(NEXT))
    rig = schedule_rig(tiny, tmp_path, fail=fail)
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "hook_failed", None, "before_timed_run: RuntimeError")]
    assert_void_then_complete(view, 0)
    assert [what for what, name in rig.host.events if name == RUN_1] == ["before_timed_run"]
    assert RUN_1 not in rig.host.launches


def test_x38_a_lasting_hook_failure_ends_in_runs_exhausted(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, fail=raising("before_timed_run"))
    with pytest.raises(RunsExhausted):
        rig.run()
    assert len(rig.view().voids) == 3
    assert not [name for name in rig.host.launches if ".run" in name]


def test_u20_a_failed_after_hook_voids_a_run_that_otherwise_completed(tiny: FrozenInputs, tmp_path: Path) -> None:
    fail = together(raising("after_timed_run", RUN_1), stop_before(NEXT))
    rig = schedule_rig(tiny, tmp_path, fail=fail)
    view = rig.run_to_the_stop()
    assert fault_lines(view) == [(KEY, "hook_failed", None, "after_timed_run: RuntimeError")]
    assert_void_then_complete(view, TINY_CALLS)
    assert KEY in {launch.key for launch in view.launches}


def test_u18_a_later_hook_failure_logs_a_further_fault_and_no_second_void(tiny: FrozenInputs, tmp_path: Path) -> None:
    fail = together(raising("after_timed_run", RUN_1), stop_before(NEXT))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"syn_1": [cached()]})}, fail=fail)
    view = rig.run_to_the_stop()
    assert [fault.kind for fault in view.faults] == ["cached_tokens", "hook_failed"]
    assert_void_then_complete(view, 2)


CONTENDED = "qwen3-4b.contended.a1"
CONTENDED_KEY = RunKey(Q4B, "contended", None, 1)


def test_u20_a_failed_start_of_contention_sends_nothing_more_and_stops_nothing(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    rig = schedule_rig(tiny, tmp_path, fail=raising("start_contention", CONTENDED))
    result = rig.run()
    view = rig.view()
    assert fault_lines(view) == [(CONTENDED_KEY, "hook_failed", None, "start_contention: RuntimeError")]
    assert items_of(view, CONTENDED_KEY) == ["warmup"]
    events = [what for what, name in rig.host.events if name == CONTENDED]
    assert events == ["before_timed_run", "popen", "start_contention", "terminate", "after_timed_run"]
    assert result.voided == (CONTENDED_KEY,)


def test_u20_a_failed_stop_of_contention_voids_the_attempt(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, fail=raising("stop_contention", CONTENDED))
    result = rig.run()
    view = rig.view()
    assert fault_lines(view) == [(CONTENDED_KEY, "hook_failed", None, "stop_contention: RuntimeError")]
    assert (CONTENDED_KEY, "void", 3) in run_lines(view)
    assert result.voided == (CONTENDED_KEY,)


def test_u13_a_fault_during_contention_still_stops_it(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {CONTENDED: ServerScript(chat={"cap_1": [cached()]})})
    rig.run()
    events = [what for what, name in rig.host.events if name == CONTENDED]
    assert events == [
        "before_timed_run",
        "popen",
        "start_contention",
        "stop_contention",
        "terminate",
        "after_timed_run",
    ]


def test_u13_a_fault_on_the_contended_warm_up_never_starts_contention(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {CONTENDED: ServerScript(chat={"warmup": [cached()]})})
    rig.run()
    assert "start_contention" not in [what for what, name in rig.host.events if name == CONTENDED]


def test_u20_the_unconstrained_pass_fires_no_hook_even_when_it_repeats(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {"qwen3-4b.unconstrained.a1": ServerScript(chat={"warmup": [cached()]})})
    rig.run()
    events = [what for what, name in rig.host.events if ".unconstrained." in name]
    assert events == ["popen", "terminate"] * 4


# ---------------------------------------------------------------- X39: a server that will not stop


def test_x39_a_server_that_will_not_stop_ends_the_schedule_with_the_attempt_void(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(waits=(None, None))})
    with pytest.raises(ServerStopFailed):
        rig.run()
    view = rig.view()
    assert fault_lines(view) == [(KEY, "server_not_stopped", None, "still running after kill")]
    assert [(void.key, void.fault_id) for void in view.voids] == [(KEY, 1)]
    assert run_lines(view) == [(KEY, "void", TINY_CALLS)]
    assert KEY not in {launch.key for launch in view.launches}
    assert [what for what, name in rig.host.events if name == RUN_1] == [
        "before_timed_run",
        "popen",
        "terminate",
        "kill",
        "after_timed_run",
    ]
    assert list(rig.host.launches)[-1] == RUN_1


def test_x39_a_stop_failure_after_a_fault_is_a_further_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"syn_1": [cached()]}, waits=(None, None))
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script})
    with pytest.raises(ServerStopFailed):
        rig.run()
    view = rig.view()
    assert [fault.kind for fault in view.faults] == ["cached_tokens", "server_not_stopped"]
    assert [void.fault_id for void in view.voids] == [1]


def test_x39_on_an_unconstrained_pass_too(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {"qwen3-4b.unconstrained.a1": ServerScript(waits=(None, None))})
    with pytest.raises(ServerStopFailed):
        rig.run()
    assert "qwen3-4b.contended.a1" not in rig.host.launches


# ---------------------------------------------------------------- U22: a harness bug


def broken_validator(monkeypatch: pytest.MonkeyPatch, item_id: str) -> None:
    real = client_module.parse_extraction

    def broken(raw: str, *, source_id: str, **kwargs: Any) -> Any:
        if source_id == item_id:
            raise ValueError("a harness bug")
        return real(raw, source_id=source_id, **kwargs)

    monkeypatch.setattr(client_module, "parse_extraction", broken)


def test_u22_a_harness_bug_propagates_and_the_server_is_still_stopped(
    tiny: FrozenInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_validator(monkeypatch, "syn_1")
    rig = schedule_rig(tiny, tmp_path)
    with pytest.raises(ValueError, match="a harness bug"):
        rig.run()
    view = rig.view()
    assert (view.runs, view.faults, view.voids) == ((), (), ())
    assert [what for what, name in rig.host.events if name == RUN_1] == [
        "before_timed_run",
        "popen",
        "terminate",
        "after_timed_run",
    ]
    assert rig.host.closed == len(rig.host.clients)
    with pytest.raises(StoreError):
        view.counted_attempt(Q4B, 1)


def test_amb_102_a_launch_that_passed_its_checks_keeps_its_record_on_a_harness_bug(
    tiny: FrozenInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_validator(monkeypatch, "syn_1")
    rig = schedule_rig(tiny, tmp_path)
    with pytest.raises(ValueError, match="a harness bug"):
        rig.run()
    assert KEY in {launch.key for launch in rig.view().launches}


def test_u22_a_harness_bug_in_a_pre_run_propagates_with_no_record(
    tiny: FrozenInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_validator(monkeypatch, "warmup")
    rig = schedule_rig(tiny, tmp_path)
    with pytest.raises(ValueError, match="a harness bug"):
        rig.run()
    assert rig.view().pre_run == ()
    assert ("terminate", "qwen3-4b.pre_run.a1") in rig.host.events


def test_u22_keyboard_interrupt_still_stops_the_server_and_fires_the_after_hook(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"syn_1": [KeyboardInterrupt()]})})
    with pytest.raises(KeyboardInterrupt):
        rig.run()
    assert [what for what, name in rig.host.events if name == RUN_1][-2:] == ["terminate", "after_timed_run"]
    assert rig.view().runs == ()
    launch = next(launch for launch in rig.view().launches if launch.key == KEY)
    assert (launch.exit_code, launch.stopped_how) == (0, "terminated")


def test_u22_a_broken_client_factory_still_stops_the_server(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)

    def broken(_base_url: str, _n_ctx: int) -> ModelServerClient:
        raise ValueError("a harness bug")

    rig.deps = dataclasses.replace(rig.deps, client_factory=broken)
    with pytest.raises(ValueError, match="a harness bug"):
        rig.run()
    assert ("terminate", "qwen3-4b.pre_run.a1") in rig.host.events


class AlteringClient(ModelServerClient):
    """A client that sends something other than the frozen request."""

    def extract(self, request: ChatRequest, **kwargs: Any) -> Any:
        altered = (*request.messages[:-1], ChatMessage("user", "altered"))
        return super().extract(dataclasses.replace(request, messages=altered), **kwargs)


def test_x52_a_request_that_is_not_the_frozen_one_is_on_disk_then_a_harness_bug(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    rig = schedule_rig(tiny, tmp_path)

    def altering(base_url: str, n_ctx: int) -> ModelServerClient:
        return AlteringClient(base_url, transport=httpx.MockTransport(rig.host.handle), n_ctx=n_ctx)

    rig.deps = dataclasses.replace(rig.deps, client_factory=altering)
    with pytest.raises(RuntimeError, match="was not sent as frozen"):
        rig.run()
    view = rig.view()
    assert [stored.call.item_id for stored in view.calls] == ["warmup"]
    assert view.faults == ()
    assert ("terminate", "qwen3-4b.pre_run.a1") in rig.host.events


def test_amb_72_after_a_crash_the_operator_can_log_and_void_through_attach(
    tiny: FrozenInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken_validator(monkeypatch, "syn_1")
    rig = schedule_rig(tiny, tmp_path)
    with pytest.raises(ValueError, match="a harness bug"):
        rig.run()
    store = RunStore.attach(rig.run_dir, now=fixed_now, fsync=no_fsync, realpath=lambda path: path)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="host rebooted")
    store.void_run(KEY, fault.fault_id)
    view = load_run(rig.run_dir)
    assert [void.key for void in view.voids] == [KEY]
    assert view.runs == ()


# ---------------------------------------------------------------- D12: what the logs may hold


def inputs_with_a_sentinel(root: Path) -> FrozenInputs:
    conversations, items = gold_rows()
    conversations[0]["segments"][0]["text"] += f" {SENTINEL}"
    items[0]["utterance"] += f" {SENTINEL}"
    items[0]["span_text"] += f" {SENTINEL}"
    gold = write_version(root, conversations, items, name="v1.0")
    frozen = freeze_inputs(
        str(gold), prompts=PROMPTS, cap_sessions=CAP_SESSIONS, probe_items=PROBE_ITEMS, warmup=WARMUP
    )
    return small_inputs(frozen, singles=1, sessions=1, probes=1)


def test_d12_no_fault_or_void_line_holds_a_transcript_an_output_or_server_text(tmp_path: Path) -> None:
    gold = tmp_path / "gold"
    gold.mkdir()
    inputs = inputs_with_a_sentinel(gold)
    assert SENTINEL in inputs.singles[0].request.messages[-1].content
    output = json.dumps({"claims": [{"segmentIds": ["seg_001"], "text": SENTINEL}], "nonCheckable": []})
    script = ServerScript(
        chat={
            "syn_1": [chat_response(output)],
            "cap_1": [error_response(500, "server_error", SENTINEL), error_response(500, "server_error", SENTINEL)],
            "probe_01": [chat_response(f"not json {SENTINEL}")],
        },
        counts={"probe_01": error_response(503, "unavailable_error", SENTINEL)},
    )
    hook_error = RuntimeError(SENTINEL)
    fail = together(raising("after_timed_run", RUN_1, error=hook_error), stop_before(NEXT))
    rig = schedule_rig(inputs, tmp_path, {RUN_1: script}, fail=fail)
    view = rig.run_to_the_stop()
    assert [fault.kind for fault in view.faults] == ["client_fault", "hook_failed"]
    assert view.faults[0].detail == f"ServerCheckError path={COUNT_ROUTE} http_status=503"
    assert SENTINEL.encode("ascii") in Path(rig.run_dir, "calls.jsonl").read_bytes()
    for name in ("faults.jsonl", "voids.jsonl", "runs.jsonl", "launches.jsonl", "pre_run.jsonl"):
        assert SENTINEL.encode("ascii") not in Path(rig.run_dir, name).read_bytes(), name


def test_d12_the_calls_file_holds_no_request_text(tmp_path: Path) -> None:
    gold = tmp_path / "gold"
    gold.mkdir()
    rig = schedule_rig(inputs_with_a_sentinel(gold), tmp_path, fail=stop_before(NEXT))
    rig.run_to_the_stop()
    assert SENTINEL.encode("ascii") not in Path(rig.run_dir, "calls.jsonl").read_bytes()
