"""The schedule on a clean machine: H3 spec L14, U6-U13, U20, U21, T6, section 4 and row X32 (S2 §3.1, §3.8, §3.13, §6).

`whole` runs one complete schedule over the standard inputs, with sockets and processes refused, and the tests read
what it stored. The counts are the spec's, for N = 4. Tests that pin no count use a cut of the inputs, and those that
need only the start of a schedule end it with `stop_before`.
"""

from __future__ import annotations

import ast
import json
import socket
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff import runner as runner_module
from app.evaluation.bakeoff.client import CHAT_PATH, repair_request
from app.evaluation.bakeoff.extraction import Extraction
from app.evaluation.bakeoff.inputs import FrozenInputs
from app.evaluation.bakeoff.launch import CANDIDATE_IDS, CANDIDATE_SLUGS, VERBOSITY_ENV
from app.evaluation.bakeoff.records import RUNS, RunKey, RunView, StoredCall, log_name
from app.evaluation.bakeoff.runner import MAX_RUN_ATTEMPTS, ScheduleResult, determinism_shares, rotation
from app.evaluation.bakeoff.schemas import canonical_sha256
from app.evaluation.tests._client_fixtures import EMPTY, block_sockets, chat_response
from app.evaluation.tests._runner_fixtures import (
    CODER_TEMPLATE_TEXT,
    SCHEDULE_CANDIDATES,
    TEMPLATE_TEXT,
    ScheduleRig,
    ServerScript,
    block_processes,
    frozen_inputs,
    schedule_rig,
    small_inputs,
    stop_before,
    unit_of,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
A, B, C = SCHEDULE_CANDIDATES
RUN_1 = "qwen3-4b.run1.a1"
NEXT = "qwen3.5-4b.run1.a1"
N = 4


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


@dataclass
class Whole:
    """One complete schedule, as stored."""

    rig: ScheduleRig
    result: ScheduleResult
    view: RunView

    def calls_of(self, key: RunKey) -> tuple[StoredCall, ...]:
        return tuple(stored for stored in self.view.calls if stored.key == key)


@pytest.fixture(scope="module")
def whole(full: FrozenInputs, tmp_path_factory: pytest.TempPathFactory) -> Whole:
    with pytest.MonkeyPatch.context() as patch:
        block_sockets(patch)
        block_processes(patch)
        with pytest.raises(AssertionError):
            socket.create_connection(("127.0.0.1", 9))
        with pytest.raises(AssertionError):
            subprocess.Popen(["llama-server"])
        rig = schedule_rig(full, tmp_path_factory.mktemp("whole"))
        result = rig.run()
    return Whole(rig, result, rig.view())


def tags(calls: tuple[StoredCall, ...]) -> list[tuple[Any, ...]]:
    return [(stored.role, stored.session_id, stored.session_index, stored.position) for stored in calls]


def planned_run(inputs: FrozenInputs) -> list[tuple[Any, ...]]:
    """U9's order, spelled out."""
    plan: list[tuple[Any, ...]] = [("warmup", None, None, None, inputs.warmup.item_id)]
    plan += [("single", None, None, n, row.item_id) for n, row in enumerate(inputs.singles, start=1)]
    for index, cap in enumerate(inputs.cap_sessions, start=1):
        plan.append(("cap_session", cap.item_id, index, None, cap.item_id))
        plan += [("probe", cap.item_id, index, n, row.item_id) for n, row in enumerate(inputs.probes, start=1)]
    return plan


# ---------------------------------------------------------------- U21: a whole schedule


def test_full_schedule_with_sockets_and_processes_refused(whole: Whole) -> None:
    assert whole.result.ran == CANDIDATE_IDS
    assert whole.result.voided == ()
    assert whole.result.run_dir == whole.rig.run_dir
    assert whole.result.pre_run == whole.view.pre_run
    assert len(whole.view.calls) == 957
    assert [stored.sequence for stored in whole.view.calls] == list(range(1, 958))
    assert len(whole.view.runs) == 15
    assert len(whole.view.launches) == 18
    assert (whole.view.faults, whole.view.voids, whole.view.truncated) == ((), (), ())
    assert whole.view.scored == frozenset()


def test_u21_the_calls_by_phase_and_role(whole: Whole) -> None:
    assert Counter((stored.key.phase, stored.role) for stored in whole.view.calls) == {
        ("pre_run", "warmup"): 3,
        ("timed_run", "warmup"): 9,
        ("timed_run", "single"): 9 * N,
        ("timed_run", "cap_session"): 9 * 4,
        ("timed_run", "probe"): 9 * 80,
        ("unconstrained", "warmup"): 3,
        ("unconstrained", "single"): 3 * N,
        ("unconstrained", "cap_session"): 3 * 4,
        ("unconstrained", "probe"): 3 * 20,
        ("contended", "warmup"): 3,
        ("contended", "cap_session"): 3,
        ("contended", "probe"): 3 * 20,
    }


def test_u21_every_unit_ends_complete_with_its_count(whole: Whole) -> None:
    assert {run.status for run in whole.view.runs} == {"complete"}
    sizes = {"timed_run": N + 85, "unconstrained": N + 25, "contended": 22}
    assert all(run.calls == sizes[run.key.phase] for run in whole.view.runs)
    assert Counter(run.key.phase for run in whole.view.runs) == {"timed_run": 9, "unconstrained": 3, "contended": 3}


def test_section_4_a_schedule_leaves_the_layout(whole: Whole) -> None:
    root = Path(whole.rig.run_dir)
    assert sorted(path.name for path in root.iterdir()) == [
        "calls.jsonl",
        "faults.jsonl",
        "launches.jsonl",
        "logs",
        "manifest.json",
        "pre_run.jsonl",
        "runs.jsonl",
        "scored",
        "voids.jsonl",
    ]
    assert len(list((root / "logs").iterdir())) == 18
    assert list((root / "scored").iterdir()) == []


@pytest.mark.parametrize(
    "name",
    ["manifest.json", "pre_run.jsonl", "launches.jsonl", "calls.jsonl", "runs.jsonl", "faults.jsonl", "voids.jsonl"],
)
def test_section_4_every_stored_line_is_ascii_and_ends_in_one_line_feed(whole: Whole, name: str) -> None:
    raw = Path(whole.rig.run_dir, name).read_bytes()
    assert raw.isascii()
    assert b"\r" not in raw
    assert raw == b"" or (raw.endswith(b"\n") and not raw.endswith(b"\n\n"))


def test_u21_h3_has_no_entry_point_and_computes_no_gate() -> None:
    source = Path(runner_module.__file__).read_text(encoding="utf-8")
    assert "__main__" not in source
    assert "argparse" not in source
    names = {node.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.FunctionDef)}
    assert not [name for name in names if "gate" in name or "score" in name or "report" in name]


@pytest.mark.parametrize("module", ["inputs", "launch", "records", "runner"])
def test_u21_no_h3_module_has_an_entry_point(module: str) -> None:
    source = Path(runner_module.__file__).with_name(f"{module}.py").read_text(encoding="utf-8")
    assert "__main__" not in source
    assert "argparse" not in source


# ---------------------------------------------------------------- U6: rotation


@pytest.mark.parametrize(
    ("ready", "orders"),
    [
        ((A, B, C), [(A, B, C), (B, C, A), (C, A, B)]),
        ((A, C), [(A, C), (C, A), (A, C)]),
        ((C,), [(C,), (C,), (C,)]),
        ((), [(), (), ()]),
    ],
    ids=["three", "two", "one", "none"],
)
def test_x32_rotation(ready: tuple[Any, ...], orders: list[tuple[Any, ...]]) -> None:
    assert [rotation(ready, run) for run in RUNS] == orders


def timed_launches(events: list[tuple[str, str]]) -> list[str]:
    return [name for what, name in events if what == "popen" and ".run" in name]


def unit(candidate: Any, run: int, attempt: int = 1) -> str:
    return f"{CANDIDATE_SLUGS[candidate.candidate_id]}.run{run}.a{attempt}"


def test_u6_nine_launches_in_rotated_order(whole: Whole) -> None:
    order = [(A, 1), (B, 1), (C, 1), (B, 2), (C, 2), (A, 2), (C, 3), (A, 3), (B, 3)]
    assert timed_launches(whole.rig.host.events) == [unit(candidate, run) for candidate, run in order]


def test_x32_the_rotation_is_over_the_candidates_that_loaded(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(polls=(1,))
    rig = schedule_rig(tiny, tmp_path, {"qwen3.5-4b.pre_run.a1": script})
    assert rig.run().ran == (Q4B, Q8B)
    order = [(A, 1), (C, 1), (C, 2), (A, 2), (A, 3), (C, 3)]
    assert timed_launches(rig.host.events) == [unit(candidate, run) for candidate, run in order]


def test_x32_a_single_candidate_runs_three_times(tiny: FrozenInputs, tmp_path: Path) -> None:
    scripts = {"qwen3-4b.pre_run.a1": ServerScript(polls=(1,)), "qwen3.5-4b.pre_run.a1": ServerScript(polls=(1,))}
    rig = schedule_rig(tiny, tmp_path, scripts)
    assert rig.run().ran == (Q8B,)
    assert timed_launches(rig.host.events) == [unit(C, 1), unit(C, 2), unit(C, 3)]


# ---------------------------------------------------------------- U7: a timed launch


def test_u7_timed_launches_run_at_default_verbosity_on_the_same_line(whole: Whole) -> None:
    launches = [launch for launch in whole.view.launches if launch.key.phase != "pre_run"]
    assert len(launches) == 15
    assert {launch.mode for launch in launches} == {"timed"}
    assert {launch.startup.verbosity for launch in launches} == {3}
    assert {launch.startup.layers_offloaded for launch in launches} == {None}
    assert {launch.startup.cuda_model_mib for launch in launches} == {None}
    for launch in whole.view.launches:
        arguments, options = whole.rig.host.launches[unit_of(launch.key)]
        assert tuple(arguments) == launch.argv
        assert (VERBOSITY_ENV in options["env"]) == (launch.key.phase == "pre_run")
    by_candidate = {launch.key.candidate: launch.argv for launch in whole.view.launches if launch.mode == "pre_run"}
    assert all(launch.argv == by_candidate[launch.key.candidate] for launch in launches)


def test_u7_each_launch_has_its_own_log(whole: Whole) -> None:
    logs = [Path(whole.rig.run_dir, log_name(launch.key)) for launch in whole.view.launches]
    assert len(set(logs)) == 18
    assert all(log.stat().st_size > 0 for log in logs)


def test_u7_nothing_slow_or_outside_the_frozen_checks_is_repeated(whole: Whole) -> None:
    host = whole.rig.host
    assert len(host.hashed) == 4
    assert host.files.reads.count("/proc/net/dev") == 1


def test_u7_the_free_checks_are_repeated_on_every_launch(whole: Whole) -> None:
    reads = whole.rig.host.files.reads
    assert len([path for path in reads if path.endswith("/cmdline")]) == 18


# ---------------------------------------------------------------- L14: the template of each launch


@pytest.mark.parametrize(
    ("candidate", "template", "parser"),
    [(Q4B, TEMPLATE_TEXT, "auto"), (Q35, CODER_TEMPLATE_TEXT, "qwen3_coder"), (Q8B, TEMPLATE_TEXT, "auto")],
    ids=CANDIDATE_SLUGS.values(),
)
def test_l14_every_launch_records_the_template_of_its_own_server(
    whole: Whole, candidate: str, template: str, parser: str
) -> None:
    launches = [launch for launch in whole.view.launches if launch.key.candidate == candidate]
    assert len(launches) == 6
    assert {launch.template_parser for launch in launches} == {parser}
    assert {launch.chat_template_sha256 for launch in launches} == {canonical_sha256(template)}


# ---------------------------------------------------------------- U8: the warm-up


def launches_of_calls(view: RunView) -> dict[RunKey, list[StoredCall]]:
    units: dict[RunKey, list[StoredCall]] = {}
    for stored in view.calls:
        units.setdefault(stored.key, []).append(stored)
    return units


def test_u8_the_first_call_on_every_launch_is_the_one_frozen_warm_up(whole: Whole) -> None:
    units = launches_of_calls(whole.view)
    assert len(units) == 18
    assert {calls[0].role for calls in units.values()} == {"warmup"}
    assert {calls[0].call.item_id for calls in units.values()} == {"warmup"}
    assert all(sum(stored.role == "warmup" for stored in calls) == 1 for calls in units.values())


def test_u8_the_warm_up_is_unconstrained_on_the_unconstrained_pass(whole: Whole, full: FrozenInputs) -> None:
    warmups = [stored for stored in whole.view.calls if stored.role == "warmup"]
    hashes = {stored.call.attempts[0].request_sha256 for stored in warmups if stored.key.phase == "unconstrained"}
    assert hashes == {full.hashes.requests_unconstrained["warmup"]}
    hashes = {stored.call.attempts[0].request_sha256 for stored in warmups if stored.key.phase != "unconstrained"}
    assert hashes == {full.hashes.warmup_sha256}


def test_u8_a_failed_warm_up_on_a_live_server_is_discarded_and_the_unit_goes_on(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    script = ServerScript(chat={"warmup": [httpx.ReadTimeout, httpx.ReadTimeout]})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    key = RunKey(Q4B, "timed_run", 1, 1)
    calls = [stored for stored in view.calls if stored.key == key]
    assert [stored.call.outcome for stored in calls[:2]] == ["timeout", "valid"]
    assert view.faults == ()
    assert view.runs[0].status == "complete"
    assert [stored.role for stored in view.counted_calls(Q4B, 1)] == ["single", "cap_session", "probe"]


def test_u8_warm_ups_stay_out_of_what_counts(whole: Whole) -> None:
    for candidate in CANDIDATE_IDS:
        for run in RUNS:
            counted = whole.view.counted_calls(candidate, run)
            assert len(counted) == N + 84
            assert {stored.role for stored in counted} == {"single", "cap_session", "probe"}


# ---------------------------------------------------------------- U9: order within a run


def test_u9_the_calls_of_a_run_in_order(whole: Whole, full: FrozenInputs) -> None:
    for candidate in CANDIDATE_IDS:
        for run in RUNS:
            calls = whole.calls_of(RunKey(candidate, "timed_run", run, 1))
            assert len(calls) == N + 85
            got = [(*tag, stored.call.item_id) for tag, stored in zip(tags(calls), calls, strict=True)]
            assert got == planned_run(full)


def test_u9_every_call_was_sent_as_frozen(whole: Whole, full: FrozenInputs) -> None:
    for stored in whole.view.calls:
        hashes = full.hashes.requests_unconstrained if stored.key.phase == "unconstrained" else full.hashes.requests
        assert stored.call.attempts[0].request_sha256 == hashes[stored.call.item_id]


def test_u9_the_bytes_on_the_wire_are_the_frozen_bytes(whole: Whole, full: FrozenInputs) -> None:
    server = whole.rig.host.servers[RUN_1]
    sent = [request.content for request in server.requests if request.url.path == CHAT_PATH]
    frozen = {row.item_id: row for row in full.all_requests()}
    assert sent == [frozen[plan[-1]].request.body_bytes() for plan in planned_run(full)]


@pytest.mark.parametrize(
    ("unit", "constrained"), [(RUN_1, True), ("qwen3-4b.unconstrained.a1", False)], ids=["timed", "unconstrained"]
)
def test_u9_a_repair_in_a_run_is_sent_in_the_frozen_repair_wording(
    tiny: FrozenInputs, tmp_path: Path, unit: str, constrained: bool
) -> None:
    script = ServerScript(chat={"syn_1": [chat_response("not json"), chat_response(EMPTY)]})
    rig = schedule_rig(tiny, tmp_path, {unit: script})
    rig.run()
    stored = next(row for row in rig.view().calls if unit_of(row.key) == unit and row.role == "single")
    first, second = stored.call.attempts
    original = tiny.singles[0].request if constrained else tiny.singles[0].unconstrained
    expected = repair_request(
        original, invalid_output="not json", error_text=first.error_text, template=tiny.prompts.repair
    )
    assert (first.failure_kind, second.retry_type) == ("invalid_output", "repair")
    assert second.request_sha256 == expected.sha256()
    sent = [request.content for request in rig.host.servers[unit].requests if request.url.path == CHAT_PATH]
    assert sent[2] == expected.body_bytes()


def test_u9_a_cap_call_validates_against_its_re_keyed_ids(tiny: FrozenInputs, tmp_path: Path) -> None:
    claim = {"segmentIds": ["syn_1:seg_001"], "text": "A claim."}
    content = json.dumps({"claims": [claim], "nonCheckable": []})
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"cap_1": [chat_response(content)]})})
    assert rig.run().voided == ()
    stored = next(row for row in rig.view().calls if row.role == "cap_session")
    assert stored.call.outcome == "valid"
    assert isinstance(stored.call.output, Extraction)
    assert (stored.call.output.source_id, stored.session_id) == ("cap_1", "cap_1")


def test_u9_a_cap_call_citing_a_bare_id_is_invalid_output(tiny: FrozenInputs, tmp_path: Path) -> None:
    claim = {"segmentIds": ["seg_001"], "text": "A claim."}
    content = json.dumps({"claims": [claim], "nonCheckable": []})
    steps = [chat_response(content), chat_response(content)]
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"cap_1": steps})}, fail=stop_before(NEXT))
    stored = next(row for row in rig.run_to_the_stop().calls if row.role == "cap_session")
    assert stored.call.outcome == "invalid_output"


def test_u9_a_probe_is_checked_against_its_own_bundle(tiny: FrozenInputs, tmp_path: Path) -> None:
    unknown = '{"citations": [{"sourceId": "src_9"}], "reason": "r", "verdict": "SUPPORTED"}'
    steps = [chat_response(unknown), chat_response(unknown)]
    rig = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(chat={"probe_01": steps})}, fail=stop_before(NEXT))
    stored = next(row for row in rig.run_to_the_stop().calls if row.role == "probe")
    assert stored.call.outcome == "invalid_output"
    assert stored.call.attempts[0].unknown_source_ids == ("src_9",)


def test_u9_a_session_run_holds_its_cap_call_and_twenty_probes(whole: Whole) -> None:
    calls = whole.calls_of(RunKey(Q35, "timed_run", 2, 1))
    for index, session_id in enumerate(("cap_1", "cap_2", "cap_3", "cap_4"), start=1):
        session = [stored for stored in calls if stored.session_index == index]
        assert [stored.role for stored in session] == ["cap_session"] + ["probe"] * 20
        assert {stored.session_id for stored in session} == {session_id}
    assert sum(stored.session_index is not None for stored in calls) == 84


# ---------------------------------------------------------------- U10: nothing is skipped


@pytest.fixture(scope="module")
def one_session(full: FrozenInputs) -> FrozenInputs:
    return small_inputs(full, singles=1, sessions=1, probes=20)


def test_u10_a_cap_failure_skips_nothing(one_session: FrozenInputs, tmp_path: Path) -> None:
    steps = [chat_response("not json"), chat_response(EMPTY, finish_reason="length")]
    rig = schedule_rig(one_session, tmp_path, {RUN_1: ServerScript(chat={"cap_1": steps})}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    session = [stored for stored in view.counted_calls(Q4B, 1) if stored.session_index == 1]
    assert [stored.call.outcome for stored in session[:1]] == ["cut_off"]
    assert [attempt.failure_kind for attempt in session[0].call.attempts] == ["invalid_output", "cut_off"]
    assert [stored.role for stored in session] == ["cap_session"] + ["probe"] * 20
    assert view.faults == ()


def test_u10_a_failed_probe_skips_nothing(one_session: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"probe_05": [httpx.ReadTimeout, httpx.ReadTimeout]})
    rig = schedule_rig(one_session, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    view = rig.run_to_the_stop()
    session = [stored for stored in view.counted_calls(Q4B, 1) if stored.session_index == 1]
    assert len(session) == 21
    assert [stored.call.outcome for stored in session].count("timeout") == 1
    assert view.runs[0].status == "complete"


# ---------------------------------------------------------------- U11: which 20


def test_u11_run_1_stores_all_80_probe_calls_tagged(whole: Whole) -> None:
    probes = [stored for stored in whole.view.counted_calls(Q4B, 1) if stored.role == "probe"]
    assert len(probes) == 80
    assert Counter(stored.session_index for stored in probes) == {1: 20, 2: 20, 3: 20, 4: 20}
    best_reading = [stored.call.item_id for stored in probes if stored.session_index == 1]
    assert best_reading == [f"probe_{number:02d}" for number in range(1, 21)]


def test_u11_every_reading_comes_from_the_same_records(full: FrozenInputs, tmp_path: Path) -> None:
    fail = [httpx.ReadTimeout, httpx.ReadTimeout]
    script = ServerScript(chat={"probe_07": [None, None, *fail]})
    inputs = small_inputs(full, singles=1, sessions=4, probes=20)
    rig = schedule_rig(inputs, tmp_path, {RUN_1: script}, fail=stop_before(NEXT))
    probes = [row for row in rig.run_to_the_stop().counted_calls(Q4B, 1) if row.role == "probe"]
    failed = [stored for stored in probes if stored.call.outcome != "valid"]
    assert [(stored.call.item_id, stored.session_index) for stored in failed] == [("probe_07", 3)]
    assert sum(stored.session_index == 1 for stored in failed) == 0  # the best reading: 0 of 20
    assert sum(stored.session_index == 3 for stored in failed) == 1  # every set: 1 of 20 in set 3
    assert (len(failed), len(probes)) == (1, 80)  # pooled: 1 of 80


# ---------------------------------------------------------------- U12: the unconstrained pass


def test_u12_the_pass_comes_after_run_3_on_its_own_launch(whole: Whole) -> None:
    events = [name for what, name in whole.rig.host.events if what == "popen"]
    assert events[3:12] == timed_launches(whole.rig.host.events)
    assert events[12:] == [
        "qwen3-4b.unconstrained.a1",
        "qwen3-4b.contended.a1",
        "qwen3.5-4b.unconstrained.a1",
        "qwen3.5-4b.contended.a1",
        "qwen3-8b.unconstrained.a1",
        "qwen3-8b.contended.a1",
    ]


@pytest.mark.parametrize("candidate", CANDIDATE_IDS)
def test_u12_each_frozen_request_once_in_unconstrained_form(whole: Whole, full: FrozenInputs, candidate: str) -> None:
    calls = whole.calls_of(RunKey(candidate, "unconstrained", None, 1))
    assert [stored.call.item_id for stored in calls] == [row.item_id for row in full.all_requests()]
    assert len(calls) == N + 25
    assert {stored.call.constrained for stored in calls} == {False}
    assert tags(calls[N + 1 : N + 5]) == [
        ("cap_session", cap.item_id, index, None) for index, cap in enumerate(full.cap_sessions, start=1)
    ]
    assert tags(calls[N + 5 :]) == [("probe", None, None, position) for position in range(1, 21)]


def test_u12_every_body_leaves_out_response_format(whole: Whole) -> None:
    server = whole.rig.host.servers["qwen3-4b.unconstrained.a1"]
    assert len(server.chats) == N + 25
    assert not [body for body in server.chats if "response_format" in body]
    assert all("response_format" in body for body in whole.rig.host.servers[RUN_1].chats)


def test_u12_the_two_attempt_loop_applies_and_raw_validity_is_in_attempt_1(tiny: FrozenInputs, tmp_path: Path) -> None:
    fenced = "```json\n" + EMPTY + "\n```"
    script = ServerScript(chat={"syn_1": [chat_response(fenced)]})
    rig = schedule_rig(tiny, tmp_path, {"qwen3-4b.unconstrained.a1": script})
    rig.run()
    call = next(row.call for row in rig.view().calls if row.key.phase == "unconstrained" and row.role == "single")
    assert [attempt.failure_kind for attempt in call.attempts] == ["invalid_output", None]
    assert call.outcome == "valid"
    assert call.constrained is False


def test_u12_no_hook_fires_for_the_unconstrained_pass(whole: Whole) -> None:
    assert not [what for what, name in whole.rig.host.events if ".unconstrained." in name and "_" in what]


# ---------------------------------------------------------------- U13: the contended session-run


@pytest.mark.parametrize("candidate", CANDIDATE_IDS)
def test_u13_the_warm_up_then_the_first_session_constrained(whole: Whole, candidate: str) -> None:
    calls = whole.calls_of(RunKey(candidate, "contended", None, 1))
    assert len(calls) == 22
    assert tags(calls) == [
        ("warmup", None, None, None),
        ("cap_session", "cap_1", 1, None),
        *(("probe", "cap_1", 1, position) for position in range(1, 21)),
    ]
    assert {stored.call.constrained for stored in calls} == {True}


def test_u13_the_hooks_of_one_contended_run_in_order(whole: Whole) -> None:
    unit_name = "qwen3-8b.contended.a1"
    events = [what for what, name in whole.rig.host.events if name == unit_name]
    assert events == [
        "before_timed_run",
        "popen",
        "start_contention",
        "stop_contention",
        "terminate",
        "after_timed_run",
    ]


def test_u13_contention_starts_after_the_warm_up_and_stops_after_the_last_probe(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    seen: dict[str, int] = {}

    def watch(name: str, key: RunKey) -> BaseException | None:
        if key.phase == "contended" and "contention" in name:
            seen[name] = len(rig.host.servers[unit_of(key)].chats)
        return None

    rig = schedule_rig(tiny, tmp_path, fail=watch)
    rig.run()
    assert seen == {"start_contention": 1, "stop_contention": 3}


def test_u13_a_cap_failure_during_contention_still_runs_its_probes(full: FrozenInputs, tmp_path: Path) -> None:
    steps = [chat_response("not json"), chat_response("not json")]
    inputs = small_inputs(full, singles=1, sessions=1, probes=20)
    rig = schedule_rig(inputs, tmp_path, {"qwen3-4b.contended.a1": ServerScript(chat={"cap_1": steps})})
    rig.run()
    calls = [stored for stored in rig.view().calls if stored.key == RunKey(Q4B, "contended", None, 1)]
    assert [stored.call.outcome for stored in calls[:3]] == ["valid", "invalid_output", "valid"]
    assert len(calls) == 22
    assert rig.view().faults == ()


def test_u13_the_contended_run_stays_out_of_the_twelve_session_runs(whole: Whole) -> None:
    timed = {(row.key.run, row.session_index) for row in whole.view.calls if row.key.phase == "timed_run"}
    assert len(timed - {(run, None) for run in RUNS}) == 12
    assert all(stored.key.phase == "timed_run" for stored in whole.view.counted_calls(Q4B, 1))


# ---------------------------------------------------------------- U20: the hooks


def test_u20_the_order_of_one_timed_run(whole: Whole) -> None:
    events = [what for what, name in whole.rig.host.events if name == "qwen3.5-4b.run2.a1"]
    assert events == ["before_timed_run", "popen", "terminate", "after_timed_run"]


def test_u20_the_hooks_fire_for_timed_and_contended_attempts_only(whole: Whole) -> None:
    before = [name for what, name in whole.rig.host.events if what == "before_timed_run"]
    after = [name for what, name in whole.rig.host.events if what == "after_timed_run"]
    assert before == after
    assert len(before) == 9 + 3
    assert all(".run" in name or ".contended." in name for name in before)


def test_u20_a_hook_gets_the_attempt_s_own_key(tiny: FrozenInputs, tmp_path: Path) -> None:
    keys: list[RunKey] = []

    def watch(name: str, key: RunKey) -> BaseException | None:
        if name == "before_timed_run":
            keys.append(key)
        return None

    schedule_rig(tiny, tmp_path, fail=watch).run()
    assert keys[0] == RunKey(Q4B, "timed_run", 1, 1)
    assert keys[-1] == RunKey(Q8B, "contended", None, 1)


# ---------------------------------------------------------------- the records a schedule leaves


def test_d11_every_timed_run_counts_its_one_attempt(whole: Whole) -> None:
    for candidate in CANDIDATE_IDS:
        assert [whole.view.counted_attempt(candidate, run) for run in RUNS] == [1, 1, 1]


def test_u19_a_clean_schedule_compares_every_scored_request(whole: Whole) -> None:
    shares = determinism_shares(whole.view)
    assert list(shares) == list(CANDIDATE_IDS)
    assert {(share.differing, share.compared) for share in shares.values()} == {(0, 2 * N + 168)}


def test_the_spec_s_counts_at_n_63(tmp_path: Path) -> None:
    """U5, U9, U12 and U19's examples for N = 63, on one candidate, since the others add nothing here."""
    gold = tmp_path / "gold"
    gold.mkdir()
    inputs = frozen_inputs(gold, **dict.fromkeys((f"syn_x{number}" for number in range(59)), 1))
    scripts = {"qwen3.5-4b.pre_run.a1": ServerScript(polls=(1,)), "qwen3-8b.pre_run.a1": ServerScript(polls=(1,))}
    rig = schedule_rig(inputs, tmp_path, scripts)
    result = rig.run()
    view = rig.view()
    assert len(inputs.singles) == 63
    assert len(result.pre_run[0].context_checks) == 352
    assert [run.calls for run in view.runs] == [148, 148, 148, 88, 22]
    assert determinism_shares(view)[Q4B].compared == 294


def test_u18_max_run_attempts_is_three() -> None:
    assert MAX_RUN_ATTEMPTS == 3
