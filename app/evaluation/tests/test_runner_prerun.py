"""The schedule up to its first run: H3 spec U1-U5, the runner's side of D9 and D10, and rows X3-X7, X13-X15, X18-X22,
X25, X30, X31, X37, X40, X41, X43-X45, X48-X51 (S2 §1, §3.3, §3.5, §3.6, §11 item 5).

No test starts a process or opens a socket: `FakeHost` stands for the machine, and one `FakeModelServer` for each
launch. Records are read back through `load_run`, so these are the records H4 will see.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import pickle
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff.client import (
    CHAT_PATH,
    COUNT_PATH,
    MAX_TOKENS,
    PROPS_PATH,
    TIMEOUT_SEC,
    repair_request,
)
from app.evaluation.bakeoff.extraction import CUT_OFF_TEXT
from app.evaluation.bakeoff.inputs import FrozenInputs
from app.evaluation.bakeoff.launch import (
    CANDIDATE_IDS,
    PINNED_BUILD_INFO,
    VERBOSITY_ENV,
    ConfigError,
    LaunchCheckFailed,
    ServerStopFailed,
    StartupLog,
    cmdline_bytes,
    frozen_argv,
    template_parser,
)
from app.evaluation.bakeoff.records import OPERATOR_FIELDS, PreRunRecord, RunKey, StoreError, log_name
from app.evaluation.bakeoff.runner import PreRunStopped, run_schedule, thinking_verdict
from app.evaluation.bakeoff.schemas import EXTRACTION_SCHEMA_SHA256, PROBE_SCHEMA_SHA256, canonical_sha256
from app.evaluation.tests._client_fixtures import (
    EMPTY,
    block_sockets,
    chat_response,
    error_response,
    input_tokens_response,
    run_extract,
)
from app.evaluation.tests._runner_fixtures import (
    BINARY_BYTES,
    BINARY_PATH,
    CONDITIONS,
    LOG_BIND_FAILED,
    LOG_DEFAULT,
    LOG_VERBOSE,
    NET_DEV_LOOPBACK,
    NET_ETH0,
    NOW,
    OPERATOR,
    PORT,
    SCHEDULE_CANDIDATES,
    SERVER_HOME,
    TEMPLATE_TEXT,
    ScheduleRig,
    ServerScript,
    block_processes,
    frozen_inputs,
    loading_response,
    schedule_rig,
    small_inputs,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
PRE_4B, PRE_35, PRE_8B = "qwen3-4b.pre_run.a1", "qwen3.5-4b.pre_run.a1", "qwen3-8b.pre_run.a1"
ARGV = frozen_argv(CONDITIONS, SCHEDULE_CANDIDATES[0])
INVALID = "not json"


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> FrozenInputs:
    return frozen_inputs(tmp_path_factory.mktemp("gold"))


@pytest.fixture(scope="module")
def tiny(full: FrozenInputs) -> FrozenInputs:
    """One request of each kind, so a schedule stays quick: `warmup`, `syn_1`, `cap_1` and `probe_01`."""
    return small_inputs(full, singles=1, sessions=1, probes=1)


def stopped(rig: ScheduleRig) -> PreRunStopped:
    with pytest.raises(PreRunStopped) as caught:
        rig.run()
    return caught.value


def pre_run_of(rig: ScheduleRig, candidate: str = Q4B) -> PreRunRecord:
    return next(record for record in rig.view().pre_run if record.candidate == candidate)


def timed_launches(rig: ScheduleRig) -> list[str]:
    return [name for name in rig.host.launches if not name.endswith(".pre_run.a1")]


def fault_lines(rig: ScheduleRig) -> list[tuple[str, str | None, str]]:
    return [(fault.kind, fault.item_id, fault.detail) for fault in rig.view().faults]


def assert_stopped_before_any_run(rig: ScheduleRig) -> None:
    """Every candidate had its pre-run, and nothing else happened."""
    view = rig.view()
    assert [record.candidate for record in view.pre_run] == list(CANDIDATE_IDS)
    assert timed_launches(rig) == []
    assert view.runs == ()
    assert {stored.key.phase for stored in view.calls} <= {"pre_run"}
    assert {what for what, _ in rig.host.events} <= {"popen", "terminate"}


# ---------------------------------------------------------------- U1: the order of a schedule


def test_u1_a_bad_field_is_refused_before_anything_is_read(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    config = dataclasses.replace(rig.config, candidates=SCHEDULE_CANDIDATES[:2])
    with pytest.raises(ConfigError):
        run_schedule(tiny, config, rig.deps)
    assert rig.host.files.reads == []
    assert rig.host.hashed == []
    assert not Path(rig.run_dir).exists()


def test_u1_outside_the_namespace_nothing_is_left_behind(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, net_dev=NET_DEV_LOOPBACK + NET_ETH0)
    with pytest.raises(LaunchCheckFailed) as caught:
        rig.run()
    assert caught.value.check == "interfaces"
    assert rig.host.hashed == []
    assert not Path(rig.run_dir).exists()


def test_u1_a_binary_that_cannot_be_read_stops_before_anything_is_written(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    del rig.host.chunks[BINARY_PATH]
    with pytest.raises(FileNotFoundError):
        rig.run()
    assert not Path(rig.run_dir).exists()


def test_u1_the_binary_is_hashed_first_and_once_and_each_model_once(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    assert rig.host.hashed == [BINARY_PATH, *(candidate.gguf_path for candidate in SCHEDULE_CANDIDATES)]


@pytest.mark.parametrize(
    "operator",
    [
        {name: value for name, value in OPERATOR.items() if name != "gpu_driver"},
        {**OPERATOR, "gpu_driver": "  "},
        {**OPERATOR, "extra": "x"},
    ],
    ids=["missing", "blank", "extra"],
)
def test_d9_a_bad_operator_value_leaves_no_directory(
    tiny: FrozenInputs, tmp_path: Path, operator: dict[str, str]
) -> None:
    rig = schedule_rig(tiny, tmp_path)
    with pytest.raises(StoreError):
        run_schedule(tiny, dataclasses.replace(rig.config, operator=operator), rig.deps)
    assert not Path(rig.run_dir).exists()
    assert rig.host.launches == {}


def test_x13_a_store_under_onedrive_is_refused_before_any_launch(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, realpaths={f"{tmp_path}/run_1": "/mnt/c/data/OneDrive/runs/run_1"})
    with pytest.raises(StoreError, match="OneDrive"):
        rig.run()
    assert rig.host.launches == {}
    assert not Path(rig.run_dir).exists()


def test_x7_an_existing_run_directory_stops_the_schedule(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    Path(rig.run_dir).mkdir()
    with pytest.raises(StoreError, match="exists"):
        rig.run()
    assert rig.host.launches == {}
    assert list(Path(rig.run_dir).iterdir()) == []


def test_u1_a_raised_context_reaches_every_place_with_no_other_change(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, conditions=dataclasses.replace(CONDITIONS, context=16384))
    rig.run()
    view = rig.view()
    assert {arguments[arguments.index("-c") + 1] for arguments, _ in rig.host.launches.values()} == {"16384"}
    assert {n_ctx for _, n_ctx in rig.host.clients} == {16384}
    checks = [check for record in view.pre_run for check in record.context_checks]
    assert {(check.n_ctx, check.limit) for check in checks} == {(16384, 14745)}
    assert {(launch.n_ctx, launch.startup.n_ctx_slot) for launch in view.launches} == {(16384, 16384)}
    assert view.manifest["conditions"]["context"] == 16384


def test_u1_the_injected_clock_and_sync_reach_the_store(tiny: FrozenInputs, tmp_path: Path) -> None:
    syncs: list[int] = []
    rig = schedule_rig(tiny, tmp_path)
    rig.deps = dataclasses.replace(rig.deps, fsync=syncs.append)
    rig.run()
    view = rig.view()
    assert len(syncs) == 1 + len(view.pre_run) + len(view.launches) + len(view.calls) + len(view.runs)
    stamps = [row.stamp for row in (*view.pre_run, *view.launches, *view.calls, *view.runs)]
    assert set(stamps) == {NOW.isoformat()}


def test_u1_every_launch_has_its_own_client_on_the_loopback_port_and_closes_it(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    assert rig.host.clients == [(f"http://127.0.0.1:{PORT}", 12288)] * 18
    assert rig.host.closed == 18


# ---------------------------------------------------------------- D9: the manifest


def test_d9_the_manifest_holds_what_the_schedule_knows(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    manifest = rig.view().manifest
    hashes = tiny.hashes
    assert (manifest["gold_version"], manifest["gold_manifest"]) == ("v1.0", dict(tiny.gold_manifest))
    assert manifest["hashes"] == {
        "prompts_sha256": hashes.prompts_sha256,
        "repair_sha256": hashes.repair_sha256,
        "probe_sha256": hashes.probe_sha256,
        "cap_sessions_sha256": hashes.cap_sessions_sha256,
        "cap_requests_sha256": hashes.cap_requests_sha256,
        "warmup_sha256": hashes.warmup_sha256,
        "requests": dict(hashes.requests),
        "requests_unconstrained": dict(hashes.requests_unconstrained),
    }
    assert manifest["extraction_schema_sha256"] == EXTRACTION_SCHEMA_SHA256
    assert manifest["probe_schema_sha256"] == PROBE_SCHEMA_SHA256
    assert manifest["sampling"] == {"temperature": 0.0, "seed": 42, "top_k": 1, "repeat_penalty": 1.0}
    assert manifest["max_tokens"] == dict(MAX_TOKENS)
    assert manifest["timeout_sec"] == dict(TIMEOUT_SEC)
    assert manifest["thinking_mode"] == "off: chat_template_kwargs.enable_thinking=false"
    assert manifest["conditions"] == {
        "binary_path": BINARY_PATH,
        "context": 12288,
        "kv_type": "f16",
        "port": PORT,
        "ready_deadline_sec": 600.0,
    }
    assert manifest["binary_sha256"] == hashlib.sha256(BINARY_BYTES).hexdigest()
    assert manifest["pinned_build_info"] == PINNED_BUILD_INFO
    assert manifest["interfaces"] == ["lo"]
    assert manifest["operator"] == dict.fromkeys(OPERATOR_FIELDS, "recorded")


def test_d9_the_manifest_lists_every_candidate_with_its_frozen_line(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    assert rig.view().manifest["candidates"] == [
        {
            "candidate_id": candidate.candidate_id,
            "gguf_path": candidate.gguf_path,
            "gguf_sha256": candidate.gguf_sha256,
            "source_revision": candidate.source_revision,
            "quantization_commands": candidate.quantization_commands,
            "argv": list(frozen_argv(CONDITIONS, candidate)),
        }
        for candidate in SCHEDULE_CANDIDATES
    ]


# ---------------------------------------------------------------- U2 and D10: a clean pre-run


def test_u2_one_candidate_asks_its_server_in_this_order(full: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(props_states=(httpx.ConnectError, httpx.ConnectError, loading_response(), loading_response()))
    rig = schedule_rig(full, tmp_path, {PRE_4B: script, PRE_8B: ServerScript(cmdline=b"")})
    stopped(rig)
    paths = [request.url.path for request in rig.host.servers[PRE_4B].requests]
    assert paths == [PROPS_PATH] * 5 + [CHAT_PATH] + [COUNT_PATH] * (4 * (4 + 25))


def test_u2_the_pre_run_launch_is_verbose_on_the_frozen_line(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    for name, candidate in zip((PRE_4B, PRE_35, PRE_8B), SCHEDULE_CANDIDATES, strict=True):
        arguments, options = rig.host.launches[name]
        assert arguments == list(frozen_argv(CONDITIONS, candidate))
        assert options["env"] == {"HOME": SERVER_HOME, "PATH": "/usr/bin:/bin", VERBOSITY_ENV: "4"}


def test_u2_no_hook_fires_in_a_pre_run(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: ServerScript(cmdline=b"")})
    stopped(rig)
    assert {what for what, _ in rig.host.events} == {"popen", "terminate"}


def test_u2_the_warm_up_is_stored_as_a_pre_run_call(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: ServerScript(cmdline=b"")})
    stopped(rig)
    first, second = rig.view().calls
    assert (first.key, first.role) == (RunKey(Q4B, "pre_run", None, 1), "warmup")
    assert (first.session_id, first.session_index, first.position) == (None, None, None)
    assert first.call.item_id == "warmup"
    assert first.call.constrained is True
    assert first.call.attempts[0].request_sha256 == tiny.hashes.warmup_sha256
    assert second.key.candidate == Q35


def test_d10_a_clean_pre_run_record(full: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(full, tmp_path, {PRE_8B: ServerScript(cmdline=b"")})
    stopped(rig)
    record = pre_run_of(rig)
    assert (record.status, record.thinking, record.blocked) == ("ready", "off", ())
    assert record.gguf_sha256 == SCHEDULE_CANDIDATES[0].gguf_sha256
    assert record.exit_code is None
    assert len(record.context_checks) == 4 * (4 + 25)


def test_d10_a_launch_record_comes_after_the_stop_and_holds_names_only(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    launch = rig.view().launches[0]
    assert launch.key == RunKey(Q4B, "pre_run", None, 1)
    assert (launch.mode, launch.argv, launch.log_name) == ("pre_run", ARGV, log_name(launch.key))
    assert launch.env_names == ("HOME", VERBOSITY_ENV, "PATH")
    assert (launch.n_ctx, launch.total_slots, launch.build_info) == (12288, 1, PINNED_BUILD_INFO)
    assert launch.model_path == SCHEDULE_CANDIDATES[0].gguf_path
    assert launch.chat_template_sha256 == canonical_sha256(TEMPLATE_TEXT)
    assert launch.template_parser == template_parser(TEMPLATE_TEXT) == "auto"
    assert (launch.exit_code, launch.stopped_how) == (0, "terminated")
    assert launch.pid == rig.host.processes[PRE_4B].pid
    assert launch.startup == StartupLog(4, 1, 12288, PORT, 37, 37, 2, 2, 2375.91, 1728.0, None, 89.01)


def test_d10_the_recurrent_buffer_is_kept_for_the_candidate_that_has_one(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    assert [launch.startup.cuda_recurrent_mib for launch in rig.view().launches[:3]] == [None, 50.25, None]


def test_d10_a_slow_load_is_recorded_and_is_no_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    states = tuple(loading_response() for _ in range(149))
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: ServerScript(props_states=states)})
    rig.run()
    assert rig.view().launches[2].ready_sec == 149.0
    assert rig.view().faults == ()


def test_d10_nothing_in_the_store_holds_the_template_or_the_environment(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    stored = b"".join(path.read_bytes() for path in Path(rig.run_dir).glob("*.json*"))
    assert b"enable_thinking is false" not in stored
    assert SERVER_HOME.encode("ascii") not in stored


# ---------------------------------------------------------------- X30: the clean environment


def test_x30_inherited_variables_never_reach_the_server(
    tiny: FrozenInputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, value in (
        ("LLAMA_ARG_CTX_SIZE", "4096"),
        ("LLAMA_ARG_NO_MMAP", "1"),
        ("LLAMA_ARG_MMPROJ", "/x"),
        ("LLAMA_ARG_THINK", "auto"),
        ("GGML_CUDA_GRAPH_OPT", "1"),
        ("CUDA_VISIBLE_DEVICES", "1"),
        ("LD_PRELOAD", "/x"),
    ):
        monkeypatch.setenv(name, value)
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    environments = {name: options["env"] for name, (_, options) in rig.host.launches.items()}
    clean = {"HOME": SERVER_HOME, "PATH": "/usr/bin:/bin"}
    assert environments[PRE_4B] == {**clean, VERBOSITY_ENV: "4"}
    assert all(env == clean for name, env in environments.items() if not name.endswith(".pre_run.a1"))
    assert {launch.env_names for launch in rig.view().launches if launch.mode == "timed"} == {("HOME", "PATH")}


# ---------------------------------------------------------------- U3: a failed launch check


def verbose_log(old: str, new: str) -> str:
    assert old in LOG_VERBOSE
    return LOG_VERBOSE.replace(old, new)


GRAPH_LINE = "0.34.900.940 I sched_reserve: graph: nodes = 1269, splits = 2, input objects = 4, input tensors = 9\n"
CONFIG_LINE = "0.00.100.000 I using config file: /etc/llama.cpp/config.ini\n"
SHORT_ARGV = (*ARGV[:4], "8192", *ARGV[5:])
LAUNCH_CHECKS: dict[str, tuple[ServerScript, str, str]] = {
    "x15-graph-line-missing": (ServerScript(log=verbose_log(GRAPH_LINE, "")), "log", "log: GRAPH line missing"),
    "x18-36-of-37": (
        ServerScript(log=verbose_log("offloaded 37/37", "offloaded 36/37")),
        "layers",
        "layers: 36 of 37",
    ),
    "x20-props-context": (
        ServerScript(props={"default_generation_settings": {"n_ctx": 8192}}),
        "context",
        "context: props n_ctx=8192 expected=12288",
    ),
    "log-context": (
        ServerScript(log=verbose_log("n_slots = 1, n_ctx_slot = 12288", "n_slots = 1, n_ctx_slot = 8192")),
        "context",
        "context: log n_ctx_slot=8192 expected=12288",
    ),
    "x21-build": (
        ServerScript(props={"build_info": "b11936-27b7a8a0"}),
        "build",
        "build: build_info differs from b1-27b7a8a",
    ),
    "x22-vision-on": (
        ServerScript(props={"modalities": {"vision": True, "video": False, "audio": False}}),
        "modalities",
        "modalities: on: vision",
    ),
    "x22-a-fourth-key": (
        ServerScript(props={"modalities": {"vision": False, "video": False, "audio": False, "text": False}}),
        "props",
        "props: ServerCheckError http_status=200",
    ),
    "http-500-on-props": (
        ServerScript(props_states=(error_response(500, "server_error", "synthetic"),)),
        "props",
        "props: ServerCheckError http_status=500",
    ),
    "x31-one-argument": (
        ServerScript(cmdline=cmdline_bytes(SHORT_ARGV)),
        "cmdline",
        "cmdline: differs at argument 4",
    ),
    "x31-a-trailing-argument": (
        ServerScript(cmdline=cmdline_bytes((*ARGV, "--verbose"))),
        "cmdline",
        "cmdline: differs at argument 30",
    ),
    "x45-config-file": (ServerScript(log=CONFIG_LINE + LOG_VERBOSE), "config_file", "config_file: config file in use"),
    "x49-no-think-tags": (
        ServerScript(props={"chat_template": "{{ messages }}"}),
        "think_tags",
        "think_tags: the template lacks a think tag",
    ),
    "another-model": (
        ServerScript(props={"model_path": "/models/other.gguf"}),
        "model_path",
        "model_path: model_path differs from the launched path",
    ),
    "two-slots": (ServerScript(props={"total_slots": 2}), "slots", "slots: props total_slots=2 expected=1"),
    "default-verbosity": (ServerScript(log=LOG_DEFAULT), "verbosity", "verbosity: log verbosity=3 expected=4"),
}


@pytest.mark.parametrize(("script", "check", "detail"), LAUNCH_CHECKS.values(), ids=LAUNCH_CHECKS.keys())
def test_u3_a_failed_launch_check_blocks_and_every_candidate_is_still_visited(
    tiny: FrozenInputs, tmp_path: Path, script: ServerScript, check: str, detail: str
) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", (check,), None)
    assert fault_lines(rig) == [("launch_check", None, detail)]
    assert rig.view().faults[0].key == RunKey(Q4B, "pre_run", None, 1)
    assert [launch.key.candidate for launch in rig.view().launches] == [Q35, Q8B]
    assert ("terminate", PRE_4B) in rig.host.events
    assert_stopped_before_any_run(rig)


def test_x19_three_graph_splits_are_recorded_and_the_candidate_runs(tiny: FrozenInputs, tmp_path: Path) -> None:
    log = verbose_log("nodes = 1269, splits = 2,", "nodes = 1269, splits = 3,")
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(log=log)})
    result = rig.run()
    assert result.pre_run[0].status == "ready"
    assert rig.view().launches[0].startup.graph_splits == 3
    assert rig.view().faults == ()


def test_x37_the_second_graph_form_is_recorded_too(tiny: FrozenInputs, tmp_path: Path) -> None:
    graph = (
        "0.34.900.940 I sched_reserve: graph (pp bs=512, tg bs=1): nodes = 1269 / 1265, splits = 3 / 2, "
        "input objects = 4 / 4, input tensors = 9 / 9\n"
    )
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(log=verbose_log(GRAPH_LINE, graph))})
    assert rig.run().pre_run[0].status == "ready"
    startup = rig.view().launches[0].startup
    assert (startup.graph_splits_pp, startup.graph_splits_tg, startup.graph_splits) == (3, 2, 3)


# ---------------------------------------------------------------- U3: the model file


def test_x14_a_model_under_mnt_is_neither_hashed_nor_launched(tiny: FrozenInputs, tmp_path: Path) -> None:
    path = SCHEDULE_CANDIDATES[1].gguf_path
    rig = schedule_rig(tiny, tmp_path, realpaths={path: "/mnt/c/m.gguf"})
    record = stopped(rig).pre_run[1]
    assert (record.status, record.blocked, record.gguf_sha256) == ("blocked", ("model_location",), None)
    assert path not in rig.host.hashed
    assert PRE_35 not in rig.host.launches
    assert rig.view().faults == ()
    assert [launch.key.candidate for launch in rig.view().launches] == [Q4B, Q8B]
    assert_stopped_before_any_run(rig)


def test_u3_a_model_with_another_hash_is_not_launched(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.host.chunks[SCHEDULE_CANDIDATES[0].gguf_path] = b"another model"
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.gguf_sha256) == ("blocked", ("model_hash",), None)
    assert PRE_4B not in rig.host.launches
    assert rig.view().faults == ()


def test_amb_94_a_model_that_cannot_be_read_blocks_and_the_others_are_visited(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    rig = schedule_rig(tiny, tmp_path)
    del rig.host.chunks[SCHEDULE_CANDIDATES[0].gguf_path]
    assert stopped(rig).pre_run[0].blocked == ("model_hash",)
    assert_stopped_before_any_run(rig)


# ---------------------------------------------------------------- U3: a launch that fails


def dies_loading(code: int = 1) -> ServerScript:
    """Two refused polls, then an exit: the evidence's first answers, and a load that fails."""
    return ServerScript(polls=(None, None, code), props_states=(httpx.ConnectError, httpx.ConnectError))


def test_x3_a_server_that_dies_loading_does_not_load_and_is_no_fault(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: dies_loading()})
    record = rig.run().pre_run[2]
    assert (record.status, record.exit_code, record.blocked, record.thinking) == ("not_loaded", 1, (), None)
    assert record.gguf_sha256 == SCHEDULE_CANDIDATES[2].gguf_sha256
    assert record.context_checks == ()
    assert rig.view().faults == ()
    assert all(launch.key.candidate != Q8B for launch in rig.view().launches)


def test_x4_a_candidate_that_does_not_load_is_left_out_of_every_later_phase(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: dies_loading()})
    result = rig.run()
    assert result.ran == (Q4B, Q35)
    assert [name for name in rig.host.launches if name.startswith("qwen3-8b")] == [PRE_8B]
    assert {stored.key.candidate for stored in rig.view().calls} == {Q4B, Q35}
    assert {run.key.candidate for run in rig.view().runs} == {Q4B, Q35}


def test_x41_no_candidate_loads(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: dies_loading(), PRE_35: dies_loading(), PRE_8B: dies_loading()})
    caught = stopped(rig)
    assert [record.status for record in caught.pre_run] == ["not_loaded"] * 3
    assert_stopped_before_any_run(rig)
    assert rig.view().calls == ()


def test_x5_a_bind_failure_is_the_port_never_a_model_that_does_not_load(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(polls=(1,), log=LOG_BIND_FAILED)})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("port",), 1)
    assert fault_lines(rig) == [("launch_failed", None, f"port {PORT} in use")]
    assert_stopped_before_any_run(rig)


def test_x6_a_stale_server_answers_while_ours_dies_on_the_port(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(polls=(None, 1), log=LOG_BIND_FAILED, cmdline=b"")
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    assert stopped(rig).pre_run[0].blocked == ("port",)
    assert fault_lines(rig) == [("launch_failed", None, f"port {PORT} in use")]


def test_x6_a_live_process_with_no_listening_line_fails_the_log_check(tiny: FrozenInputs, tmp_path: Path) -> None:
    log = "\n".join(line for line in LOG_VERBOSE.split("\n") if "server_on_li" not in line)
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(log=log)})
    assert stopped(rig).pre_run[0].blocked == ("log",)
    assert fault_lines(rig) == [("launch_check", None, "log: no listening line")]


def test_x48_a_zombie_reads_as_an_exit_never_as_a_command_line_check(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(polls=(None, 1), cmdline=b"")})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("server_exit",), 1)
    assert fault_lines(rig) == [("launch_failed", None, "server exited with code 1")]


def test_x40_a_load_past_the_deadline_blocks_and_is_not_does_not_load(tiny: FrozenInputs, tmp_path: Path) -> None:
    states = tuple(loading_response() for _ in range(601))
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(props_states=states)})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("ready_timeout",), None)
    assert fault_lines(rig) == [("ready_timeout", None, "not ready after 600 s")]
    assert ("terminate", PRE_4B) in rig.host.events


def test_x51_a_spawn_failure_blocks_with_no_client_and_no_launch_line(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(spawn_error=PermissionError("denied"))})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("spawn",), None)
    assert fault_lines(rig) == [("launch_failed", None, "spawn failed: PermissionError")]
    assert len(rig.host.clients) == 2
    assert [launch.key.candidate for launch in rig.view().launches] == [Q35, Q8B]


def test_amb_100_a_pre_run_server_that_will_not_stop_ends_the_schedule(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(waits=(None, None))})
    with pytest.raises(ServerStopFailed):
        rig.run()
    view = rig.view()
    assert [record.blocked for record in view.pre_run] == [("server_not_stopped",)]
    assert fault_lines(rig) == [("server_not_stopped", None, "still running after kill")]
    assert view.launches == ()
    assert view.voids == ()
    assert list(rig.host.launches) == [PRE_4B]


# ---------------------------------------------------------------- U3: after the warm-up began


def test_u3_an_exit_during_the_warm_up_is_a_fault_and_the_call_stays_stored(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(die_at=1)})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("server_exit",), -11)
    assert record.thinking is None
    assert record.context_checks == ()
    assert fault_lines(rig) == [("server_exit", "warmup", "server exited with code -11")]
    view = rig.view()
    assert view.calls[0].call.outcome == "server_error"
    assert (view.launches[0].exit_code, view.launches[0].stopped_how) == (-11, "already_exited")


def test_u3_an_exit_during_the_context_checks_ends_the_pre_run_at_once(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(die_after=1)})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("server_exit",), -11)
    assert record.thinking == "off"
    assert record.context_checks == ()
    assert fault_lines(rig) == [("server_exit", "warmup", "server exited with code -11")]


def test_u3_an_exit_found_by_the_last_poll_blocks(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(polls=(None, None, 9))})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("server_exit",), 9)
    assert len(record.context_checks) == 4 * len(tiny.all_requests())
    assert fault_lines(rig) == [("server_exit", None, "server exited with code 9")]


def test_u3_a_client_fault_on_a_live_server_keeps_the_checks_made_so_far(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(counts={"cap_1": error_response(503, "unavailable_error", "synthetic")})
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.exit_code) == ("blocked", ("client_fault",), None)
    assert len(record.context_checks) == 8
    detail = "ServerCheckError path=/v1/chat/completions/input_tokens http_status=503"
    assert fault_lines(rig) == [("client_fault", "cap_1", detail)]


def test_u14_a_client_fault_on_the_warm_up_stores_no_call(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(
        chat={"warmup": [chat_response(INVALID)]},
        counts={"warmup": error_response(503, "unavailable_error", "synthetic")},
    )
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    record = stopped(rig).pre_run[0]
    assert (record.blocked, record.thinking) == (("client_fault",), None)
    assert all(stored.key.candidate != Q4B for stored in rig.view().calls)


def test_u3_nonzero_cached_tokens_on_the_warm_up_block(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"warmup": [chat_response(EMPTY, cached_tokens=7)]})
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.blocked, record.thinking) == ("blocked", ("cached_tokens",), None)
    assert fault_lines(rig) == [("cached_tokens", "warmup", "cached_tokens=7 cache_n=7 attempt=1")]
    assert rig.view().calls[0].call.outcome == "valid"


def test_x45_a_config_file_at_default_verbosity_fails_a_timed_launch_too(tiny: FrozenInputs, tmp_path: Path) -> None:
    logs = {f"qwen3-4b.run1.a{number}": ServerScript(log=CONFIG_LINE + LOG_DEFAULT) for number in (1, 2)}
    rig = schedule_rig(tiny, tmp_path, logs)
    rig.run()
    assert fault_lines(rig) == [("launch_check", None, "config_file: config file in use")] * 2


# ---------------------------------------------------------------- U4: thinking


@pytest.mark.parametrize(
    ("steps", "verdict"),
    [
        ((chat_response(EMPTY),), "off"),
        ((chat_response(EMPTY, reasoning_content="Let me check"),), "seen"),
        ((chat_response(INVALID), input_tokens_response(100), chat_response(EMPTY)), "off"),
        ((httpx.ReadTimeout, chat_response(EMPTY)), "no_evidence"),
        ((error_response(500, "server_error", "x"), error_response(500, "server_error", "x")), "no_evidence"),
        ((httpx.ReadTimeout, chat_response(EMPTY, reasoning_content="Let me check")), "seen"),
        ((httpx.ReadTimeout, httpx.ReadTimeout), "no_evidence"),
        ((chat_response(INVALID), input_tokens_response(100), httpx.ReadTimeout), "no_evidence"),
    ],
    ids=[
        "clean",
        "reasoning",
        "repair-clean",
        "timeout-then-clean",
        "http-500-twice",
        "timeout-then-text",
        "timeouts",
        "clean-then-timeout",
    ],
)
def test_u4_the_verdict_reads_every_attempt(steps: tuple[Any, ...], verdict: str) -> None:
    assert thinking_verdict(run_extract(*steps)[0]) == verdict


def test_u4_text_in_a_repair_s_content_is_seen_though_the_first_attempt_was_clean() -> None:
    steps = (chat_response(INVALID), input_tokens_response(100), chat_response("<think>x</think>" + EMPTY))
    call = run_extract(*steps)[0]
    assert (call.attempts[0].thinking.seen, call.attempts[1].thinking.in_content) == (False, True)  # type: ignore[union-attr]
    assert thinking_verdict(call) == "seen"


def test_x25_a_warm_up_with_no_response_is_no_evidence_and_the_context_checks_still_run(
    tiny: FrozenInputs, tmp_path: Path
) -> None:
    script = ServerScript(chat={"warmup": [httpx.ReadTimeout, httpx.ReadTimeout]})
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    record = stopped(rig).pre_run[0]
    assert (record.status, record.thinking, record.blocked) == ("blocked", "no_evidence", ("thinking",))
    assert len(record.context_checks) == 4 * len(tiny.all_requests())
    assert rig.view().faults == ()
    assert rig.view().calls[0].call.outcome == "timeout"


def test_x25_an_invalid_warm_up_that_is_clean_is_off_and_ready(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"warmup": [chat_response(INVALID), chat_response(INVALID)]})
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    record = rig.run().pre_run[0]
    assert (record.status, record.thinking) == ("ready", "off")
    assert rig.view().calls[0].call.outcome == "invalid_output"


@pytest.mark.parametrize(
    "steps",
    [
        [chat_response(EMPTY, reasoning_content="Let me check")],
        [httpx.ReadTimeout, chat_response(EMPTY, reasoning_content="Let me check")],
    ],
    ids=["x43-thinking-seen", "x50-timeout-then-text"],
)
def test_x43_x50_thinking_seen_means_information_only_so_the_candidate_runs(
    tiny: FrozenInputs, tmp_path: Path, steps: list[Any]
) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_35: ServerScript(chat={"warmup": steps})})
    result = rig.run()
    assert (result.pre_run[1].status, result.pre_run[1].thinking, result.pre_run[1].blocked) == ("ready", "seen", ())
    assert result.ran == CANDIDATE_IDS
    assert [run.key.run for run in rig.view().runs if run.key.candidate == Q35][:3] == [1, 2, 3]


# ---------------------------------------------------------------- U5: the context checks


def test_u5_every_frozen_request_is_counted_in_both_forms_on_each_server(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    expected = []
    for frozen in tiny.all_requests():
        for request in (frozen.request, frozen.unconstrained):
            bound = repair_request(request, invalid_output="", error_text=CUT_OFF_TEXT, template=tiny.prompts.repair)
            expected += [("request", request.sha256()), ("repair_bound", bound.sha256())]
    assert len(expected) == 4 * len(tiny.all_requests())
    for record in rig.view().pre_run:
        assert [(check.label, check.request_sha256) for check in record.context_checks] == expected


def test_u5_the_timed_launches_count_nothing_again(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path)
    rig.run()
    for name, server in rig.host.servers.items():
        if not name.endswith(".pre_run.a1"):
            assert {request.url.path for request in server.requests} == {PROPS_PATH, CHAT_PATH}


def test_u5_a_repair_bound_that_does_not_fit_stops_before_any_run(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, counts={"cap_1": 5700})
    caught = stopped(rig)
    assert [record.blocked for record in caught.pre_run] == [("context",)] * 3
    checks = caught.pre_run[0].context_checks
    assert len(checks) == 4 * len(tiny.all_requests())
    request, bound = checks[8], checks[9]
    assert (request.label, request.uses, request.fits) == ("request", 9796, True)
    assert (bound.label, bound.uses, bound.limit, bound.fits) == ("repair_bound", 13892, 11059, False)
    assert rig.view().faults == ()
    assert [stored.role for stored in rig.view().calls] == ["warmup"] * 3
    assert_stopped_before_any_run(rig)


def test_x44_one_tokenizer_that_does_not_fit_stops_every_candidate(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_35: ServerScript(counts={"cap_1": 5700})})
    assert [record.blocked for record in stopped(rig).pre_run] == [(), ("context",), ()]
    assert_stopped_before_any_run(rig)


def test_x44_the_same_request_fits_at_a_raised_context(tiny: FrozenInputs, tmp_path: Path) -> None:
    conditions = dataclasses.replace(CONDITIONS, context=16384)
    rig = schedule_rig(tiny, tmp_path, conditions=conditions, counts={"cap_1": 6000})
    result = rig.run()
    bound = result.pre_run[0].context_checks[9]
    assert (bound.uses, bound.limit, bound.fits) == (14192, 14745, True)
    assert result.ran == CANDIDATE_IDS


def test_u5_a_block_on_thinking_and_on_context_names_both(tiny: FrozenInputs, tmp_path: Path) -> None:
    script = ServerScript(chat={"warmup": [httpx.ReadTimeout, httpx.ReadTimeout]}, counts={"cap_1": 5700})
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: script})
    assert stopped(rig).pre_run[0].blocked == ("thinking", "context")


def test_u5_a_fault_after_a_context_block_names_both(tiny: FrozenInputs, tmp_path: Path) -> None:
    counts = {"syn_1": 9100, "cap_1": error_response(503, "unavailable_error", "synthetic")}
    rig = schedule_rig(tiny, tmp_path, {PRE_4B: ServerScript(counts=counts)})
    assert stopped(rig).pre_run[0].blocked == ("context", "client_fault")


# ---------------------------------------------------------------- the stop itself


def test_u3_pre_run_stopped_says_where_the_records_are(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: ServerScript(cmdline=b"")})
    caught = stopped(rig)
    assert caught.run_dir == rig.run_dir
    assert caught.pre_run == rig.view().pre_run
    assert rig.run_dir in str(caught)


def test_u3_pre_run_stopped_survives_copy_and_pickle(tiny: FrozenInputs, tmp_path: Path) -> None:
    rig = schedule_rig(tiny, tmp_path, {PRE_8B: ServerScript(cmdline=b"")})
    caught = stopped(rig)
    for clone in (copy.copy(caught), pickle.loads(pickle.dumps(caught))):
        assert (clone.run_dir, clone.pre_run) == (caught.run_dir, caught.pre_run)
