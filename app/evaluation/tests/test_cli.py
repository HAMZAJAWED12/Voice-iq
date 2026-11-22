"""The run CLI `cli`: H5 rows C-1 to C-11 (S2 §3.2, §3.12, §3.13, §11; AMB-150, AMB-151, AMB-154, AMB-159).

No test starts a process or opens a socket. `FakeHost` stands for the machine and the machine fixtures for the
sampler, so `run` and `context` exercise the real schedule and launch code over fakes. Gold sets are synthetic, under
`tmp_path`. The guard tests never touch a gold set: they map short argument names to fake resolved paths built from
`cli.REAL_GOLD_SUFFIXES` and `cli.FROZEN_DIR_SUFFIX`, and replace the gold step with a recorder, so a guard that lets
a call through is seen before anything is read.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff import cli, entry, frozen, machine, wrapper
from app.evaluation.bakeoff.client import COUNT_PATH, MAX_TOKENS, repair_request
from app.evaluation.bakeoff.decision import RULES
from app.evaluation.bakeoff.extraction import CUT_OFF_TEXT
from app.evaluation.bakeoff.gates import ReportInputs, parse_expected_freeze, schedule_end
from app.evaluation.bakeoff.gold import load_gold_set
from app.evaluation.bakeoff.inputs import FrozenInputs, ProbeItem, ProbeSource, freeze_inputs
from app.evaluation.bakeoff.launch import CANDIDATE_IDS, CANDIDATE_SLUGS, VERBOSITY_ENV, LaunchTools
from app.evaluation.bakeoff.machine import MachineTools
from app.evaluation.bakeoff.records import RunKey, load_run
from app.evaluation.bakeoff.schemas import canonical_sha256
from app.evaluation.tests._client_fixtures import block_sockets, input_tokens_response
from app.evaluation.tests._fixtures import make_conversation, write_version
from app.evaluation.tests._h4_fixtures import (
    MACHINE_CONFIG,
    FakeMachinePopen,
    FakeThreads,
    answering_scripts,
    apps_output,
    install_machine,
    machine_tools,
    proc_status,
    write_h4_gold,
)
from app.evaluation.tests._h5_fixtures import (
    H5_PROBE_DELIMITERS,
    H5_PROMPTS,
    h5_input_bytes,
    inputs_manifest_bytes,
    write_h5_inputs,
)
from app.evaluation.tests._runner_fixtures import (
    CAP_SESSIONS,
    CONDITIONS,
    NOW,
    PROBE_ITEMS,
    SCHEDULE_CANDIDATES,
    VALID_OPERATOR,
    WARMUP,
    FakeHost,
    ServerScript,
    block_processes,
    fixed_now,
    gold_rows,
    no_fsync,
    synthetic_gold,
    unit_of,
)

STAMP = NOW.strftime("%Y%m%dT%H%M%S%fZ")
RESAMPLES = 200
HASH_KEYS = ("prompts_sha256", "repair_sha256", "probe_sha256", "cap_sessions_sha256", "cap_requests_sha256")


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


# ---------------------------------------------------------------- the wrapper's whitelist


def whitelisted(result: cli.CommandResult) -> bool:
    """Every field key is in the wrapper's FIELD_GRAMMAR and every value matches its key's grammar."""
    for key, value in result.fields:
        grammar = wrapper.FIELD_GRAMMAR.get(key)
        if grammar is None:
            return False
        if isinstance(grammar, frozenset):
            if value not in grammar:
                return False
        elif grammar.fullmatch(value) is None:
            return False
    return True


def test_the_whitelist_check_refuses_an_unknown_key_and_a_bad_value() -> None:
    assert whitelisted(cli.CommandResult("run", 0, (("run", "dryrun-1"), ("request_count", "29"))))
    assert not whitelisted(cli.CommandResult("run", 0, (("path", "x"),)))
    assert not whitelisted(cli.CommandResult("run", 0, (("run", "a run"),)))
    assert not whitelisted(cli.CommandResult("run", 0, (("probe_sha256", "A" * 64),)))
    assert not whitelisted(cli.CommandResult("run", 0, (("stopped", "maybe"),)))


# ---------------------------------------------------------------- helpers


def read_file(path: str) -> bytes:
    return Path(path).read_bytes()


def refuse(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("this test must not reach this tool")


class Reads:
    """A `read_bytes` over the disk that records each path."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, path: str) -> bytes:
        self.paths.append(path)
        return Path(path).read_bytes()


class Syncs:
    def __init__(self) -> None:
        self.fds: list[int] = []

    def __call__(self, fd: int) -> None:
        self.fds.append(fd)


def plain_env(
    *,
    realpaths: Mapping[str, str] | None = None,
    read_bytes: Callable[[str], bytes] = read_file,
    fsync: Callable[[int], None] = no_fsync,
) -> cli.CliEnv:
    """An environment with no process, clock or log behind it: files are read from disk and paths resolve through
    `realpaths`, else to themselves."""
    table = dict(realpaths or {})
    tools = LaunchTools(
        popen=refuse,
        read_bytes=read_bytes,
        realpath=lambda path: table.get(path, path),
        read_chunks=refuse,
        open_log=refuse,
        monotonic=refuse,
        sleep=refuse,
    )
    machine_side = MachineTools(launch=tools, list_pids=refuse, start_thread=refuse)
    return cli.CliEnv(tools=tools, machine_tools=machine_side, now=fixed_now, fsync=fsync)


def host_env(host: FakeHost, *, realpaths: Mapping[str, str] | None = None) -> cli.CliEnv:
    host.realpaths.update(realpaths or {})
    tools = host.tools()
    return cli.CliEnv(
        tools=tools,
        machine_tools=MachineTools(launch=tools, list_pids=refuse, start_thread=refuse),
        now=fixed_now,
        fsync=no_fsync,
        client_factory=host.client,
    )


def freeze(gold_dir: Path) -> FrozenInputs:
    """What `load_frozen` gives over the standard H5 input files and `gold_dir`."""
    return freeze_inputs(
        str(gold_dir), prompts=H5_PROMPTS, cap_sessions=CAP_SESSIONS, probe_items=PROBE_ITEMS, warmup=WARMUP
    )


@dataclasses.dataclass(frozen=True)
class Dirs:
    inputs: Path
    gold: Path
    out: Path


def make_dirs(
    root: Path, gold_writer: Callable[[Path], Path] = synthetic_gold, *, files: Mapping[str, bytes] | None = None
) -> Dirs:
    inputs = root / "inputs"
    write_h5_inputs(inputs, files)
    gold = gold_writer(root / "g")
    out = root / "out"
    out.mkdir()
    return Dirs(inputs, gold, out)


def stderr_of(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().err


def error_of(result: cli.CommandResult) -> str:
    assert result.exit_code == 1
    assert result.fields == (("error", result.fields[0][1]),)
    return result.fields[0][1]


def assert_no_path(text: str, tmp_path: Path) -> None:
    assert str(tmp_path) not in text and tmp_path.as_posix() not in text
    assert "/" not in text and chr(92) not in text


def freeze_gold_argv(dirs: Dirs, *extra: str) -> list[str]:
    return [
        "freeze",
        "gold",
        "--inputs-dir",
        str(dirs.inputs),
        "--gold-dir",
        str(dirs.gold),
        "--out-dir",
        str(dirs.out),
        *extra,
    ]


def entry_config_data(store_parent: Path | str, run_name: str) -> dict[str, Any]:
    return {
        "store_parent": str(store_parent),
        "run_name": run_name,
        "conditions": launch_config_data()["conditions"],
        "candidates": launch_config_data()["candidates"],
        "operator": dict(VALID_OPERATOR),
        "machine": {
            "nvidia_smi_path": MACHINE_CONFIG.nvidia_smi_path,
            "contention_argv": list(MACHINE_CONFIG.contention_argv),
            "contention_env": dict(MACHINE_CONFIG.contention_env),
            "contention_lead_sec": MACHINE_CONFIG.contention_lead_sec,
        },
    }


def launch_config_data() -> dict[str, Any]:
    return {
        "conditions": {
            "binary_path": CONDITIONS.binary_path,
            "home": CONDITIONS.home,
            "port": CONDITIONS.port,
            "context": CONDITIONS.context,
            "kv_type": CONDITIONS.kv_type,
        },
        "candidates": [
            {
                "candidate_id": row.candidate_id,
                "gguf_path": row.gguf_path,
                "gguf_sha256": row.gguf_sha256,
                "source_revision": row.source_revision,
                "quantization_commands": row.quantization_commands,
            }
            for row in SCHEDULE_CANDIDATES
        ],
    }


def write_json(path: Path, data: Any) -> Path:
    path.write_text(json.dumps(data) + "\n", encoding="ascii")
    return path


# ---------------------------------------------------------------- constants and the real environment


def test_the_closed_sets_and_suffixes() -> None:
    assert frozenset(RULES) | {"none"} == cli.RULE_VALUES
    assert frozenset({"complete", "pre_run_stopped", "runs_exhausted", "incomplete"}) == cli.END_VALUES
    assert len(cli.REAL_GOLD_SUFFIXES) == 2
    assert {suffix.rsplit("/", 1)[0] for suffix in cli.REAL_GOLD_SUFFIXES} == {"app/evaluation/" + "gold"}
    assert [suffix.rsplit("/", 1)[1] for suffix in cli.REAL_GOLD_SUFFIXES] == ["v1.0", "v0.4"]
    assert cli.FROZEN_DIR_SUFFIX == "app/evaluation/frozen"
    assert issubclass(cli.GuardRefused, ValueError)


def test_real_env_builds_the_real_tools_and_a_utc_clock() -> None:
    env = cli.real_env()
    assert env.tools == LaunchTools()
    assert env.machine_tools == MachineTools(launch=LaunchTools())
    assert env.fsync is cli.os.fsync
    assert env.client_factory is None
    assert env.now().utcoffset() is not None and env.now().utcoffset().total_seconds() == 0  # type: ignore[union-attr]


def test_cli_env_and_command_result_are_frozen() -> None:
    result = cli.CommandResult("run", 0, ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.exit_code = 1  # type: ignore[misc]
    env = plain_env()
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.fsync = no_fsync  # type: ignore[misc]


# ---------------------------------------------------------------- C-1 the parser


def test_c1_the_parser_has_the_four_subcommands_under_its_prog() -> None:
    parser = cli._parser()
    assert parser.prog == "bakeoff-cli"
    inputs = parser.parse_args(["freeze", "inputs", "--inputs-dir", "a", "--out", "b"])
    assert (inputs.command, inputs.target, inputs.inputs_dir, inputs.out) == ("freeze", "inputs", "a", "b")
    gold = parser.parse_args(["freeze", "gold", "--inputs-dir", "a", "--gold-dir", "g", "--out-dir", "o"])
    assert (gold.target, gold.gold_dir, gold.out_dir, gold.cap_sessions, gold.check_only) == (
        "gold",
        "g",
        "o",
        None,
        False,
    )
    run = parser.parse_args(
        [
            "run",
            "--config",
            "c",
            "--inputs-dir",
            "i",
            "--gold-dir",
            "g",
            "--cap-sessions",
            "s",
            "--expected-freeze",
            "e",
        ]
    )
    assert (run.command, run.config, run.cap_sessions, run.expected_freeze) == ("run", "c", "s", "e")
    context = parser.parse_args(["context", "--launch-config", "l", "--inputs-dir", "i", "--server-log-dir", "d"])
    assert (context.command, context.gold_dir, context.cap_sessions, context.sources) == ("context", None, None, False)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["score"],
        ["freeze"],
        ["freeze", "inputs", "--inputs-dir", "a"],
        ["freeze", "inputs", "--out", "b"],
        ["freeze", "gold", "--inputs-dir", "a", "--gold-dir", "g"],
        ["freeze", "gold", "--inputs-dir", "a", "--out-dir", "o"],
        ["freeze", "gold", "--inputs-dir", "a", "--gold-dir", "g", "--out-dir", "o", "--check-only"],
        ["freeze", "gold", "--inputs-dir", "a", "--gold-dir", "g", "--out-dir", "o", "--expected-freeze", "e"],
        ["run", "--config", "c", "--inputs-dir", "i", "--gold-dir", "g", "--cap-sessions", "s"],
        ["run", "--config", "c", "--inputs-dir", "i", "--gold-dir", "g", "--expected-freeze", "e"],
        ["run", "--inputs-dir", "i", "--gold-dir", "g", "--cap-sessions", "s", "--expected-freeze", "e"],
        ["context", "--launch-config", "l", "--inputs-dir", "i", "--sources"],
        ["context", "--inputs-dir", "i", "--server-log-dir", "d", "--sources"],
        ["context", "--launch-config", "l", "--inputs-dir", "i", "--server-log-dir", "d"],
        ["context", "--launch-config", "l", "--inputs-dir", "i", "--server-log-dir", "d", "--cap-sessions", "s"],
        ["freeze", "inputs", "--inputs", "a", "--out", "b"],
    ],
)
@pytest.mark.parametrize("guarded", [True, False])
def test_c1_a_usage_error_exits_2_before_anything_is_read(argv: list[str], guarded: bool) -> None:
    reads = Reads()
    with pytest.raises(SystemExit) as caught:
        cli.dispatch(argv, plain_env(read_bytes=reads), guarded=guarded)
    assert caught.value.code == 2
    assert reads.paths == []


# ---------------------------------------------------------------- C-2 report and void go to entry unchanged


class EntryRecorder:
    def __init__(self, code: int) -> None:
        self.code = code
        self.calls: list[Any] = []

    def __call__(self, argv: Any = None) -> int:
        self.calls.append(argv)
        return self.code


def never_parse() -> Any:
    raise AssertionError("cli's own parser must not see report or void")


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "--run-dir", "runs/dryrun-1", "--gold-dir", "g", "--shared-memory", "x=rose", "--unknown", "z"],
        ["report", "--gold-dir", "g", "--run-dir", "runs/run_1", "--expected-freeze", "e.json"],
        ["report", "--run-dir=runs/run_1", "--gold-dir=g", "--expected-freeze=e.json"],
        ["void", "--run-dir", "runs/run_1", "--unit", "qwen3-4b.run1.a1", "--detail", "operator fault"],
        ["void", "--not-an-option"],
    ],
)
@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_c2_report_and_void_reach_entry_main_with_argv_unchanged(
    argv: list[str], code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = EntryRecorder(code)
    monkeypatch.setattr(entry, "main", recorder)
    monkeypatch.setattr(cli, "_parser", never_parse)
    given = list(argv)
    result = cli.dispatch(given, plain_env(), guarded=True)
    assert result == cli.CommandResult(argv[0], code, ())
    assert recorder.calls == [argv]
    assert type(recorder.calls[0]) is list and recorder.calls[0] is not given
    assert given == argv


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "--run-dir", "runs/run_1", "--gold-dir", "g"],
        ["report", "--run-dir", "runs/xdryrun-1", "--gold-dir", "g"],
        ["report", "--run-dir", "runs/dryrun-1", "--run", "runs/run_1", "--gold-dir", "g"],
        ["report", "--run-dir=runs/dryrun-1", "--run-d=runs/run_1", "--gold-dir", "g"],
    ],
)
def test_c2_a_real_report_without_the_expected_freeze_is_refused(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    recorder = EntryRecorder(0)
    monkeypatch.setattr(entry, "main", recorder)
    result = cli.dispatch(argv, plain_env(), guarded=True)
    assert error_of(result) == "GuardRefused"
    assert result.command == "report"
    assert recorder.calls == []
    err = stderr_of(capsys)
    assert "AMB-154" in err
    assert_no_path(err.split("GuardRefused: ", 1)[1], tmp_path)


def test_c2_the_report_guard_reads_the_resolved_run_dir_name(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = EntryRecorder(3)
    monkeypatch.setattr(entry, "main", recorder)
    argv = ["report", "--run-dir", "link", "--gold-dir", "g"]
    assert cli.dispatch(argv, plain_env(realpaths={"link": "/w/runs/dryrun-7"}), guarded=True).exit_code == 3
    refused = cli.dispatch(argv, plain_env(realpaths={"link": "/w/dryrun-7/run_1"}), guarded=True)
    assert error_of(refused) == "GuardRefused"
    assert recorder.calls == [argv]


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "--run-dir", "runs/run_1", "--gold-dir", "g", "--expected=e.json"],
        ["report", "--run-dir", "runs/run_1", "--gold-dir", "g", "--expected", "e.json"],
    ],
)
def test_c2_the_report_guard_reads_an_abbreviated_expected_freeze_as_entry_does(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard reads argv with entry's own parser, so an abbreviation entry accepts counts for the guard too."""
    recorder = EntryRecorder(0)
    monkeypatch.setattr(entry, "main", recorder)
    assert cli.dispatch(argv, plain_env(), guarded=True) == cli.CommandResult("report", 0, ())
    assert recorder.calls == [argv]


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "--gold-dir", "g"],
        ["report", "--run-dir=runs/run_1", "--expected-freeze=e.json"],
        ["report", "--run-dir", "runs/dryrun-1", "--gold-dir", "g", "--shared-memory", "x=y"],
    ],
)
def test_c2_a_report_argv_entry_refuses_is_a_usage_error_before_entry_main(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = EntryRecorder(0)
    monkeypatch.setattr(entry, "main", recorder)
    with pytest.raises(SystemExit) as raised:
        cli.dispatch(argv, plain_env(), guarded=True)
    assert raised.value.code == 2
    assert recorder.calls == []


# ---------------------------------------------------------------- C-3 freeze inputs


def freeze_inputs_argv(inputs: Path, out: Path | str) -> list[str]:
    return ["freeze", "inputs", "--inputs-dir", str(inputs), "--out", str(out)]


@pytest.mark.parametrize("guarded", [True, False])
def test_c3_freeze_inputs_writes_the_hash_file_and_its_fields(tmp_path: Path, guarded: bool) -> None:
    files = write_h5_inputs(tmp_path / "inputs", manifest=False)
    out = tmp_path / "INPUTS.sha256"
    syncs = Syncs()
    result = cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", out), plain_env(fsync=syncs), guarded=guarded)
    data = out.read_bytes()
    assert data == inputs_manifest_bytes(files)
    assert data == frozen.inputs_manifest(files)
    assert len(syncs.fds) == 1
    hashes = frozen.gold_free_hashes(frozen.parse_draft_inputs(files))
    assert result == cli.CommandResult(
        "freeze inputs",
        0,
        (
            ("prompts_sha256", hashes["prompts_sha256"]),
            ("repair_sha256", hashes["repair_sha256"]),
            ("probe_sha256", hashes["probe_sha256"]),
            ("inputs_manifest_sha256", hashlib.sha256(data).hexdigest()),
            ("file_count", "7"),
            ("warmup_item", WARMUP.item_id),
        ),
    )
    assert whitelisted(result)


def test_c3_freeze_inputs_refuses_an_existing_file_and_leaves_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_h5_inputs(tmp_path / "inputs", manifest=False)
    out = tmp_path / "INPUTS.sha256"
    out.write_bytes(b"kept\n")
    result = cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", out), plain_env(), guarded=False)
    assert error_of(result) == "FileExistsError"
    assert out.read_bytes() == b"kept\n"
    assert "FileExistsError" in stderr_of(capsys)


def test_c3_freeze_inputs_refuses_a_onedrive_path_and_a_missing_parent(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path / "inputs", manifest=False)
    synced = tmp_path / ("One" + "Drive")
    synced.mkdir()
    env = plain_env(realpaths={"OUT": str(synced / "INPUTS.sha256")})
    assert error_of(cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", "OUT"), env, guarded=False)) == "StoreError"
    assert list(synced.iterdir()) == []
    missing = tmp_path / "absent" / "INPUTS.sha256"
    result = cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", missing), plain_env(), guarded=False)
    assert error_of(result) == "StoreError"
    assert not (tmp_path / "absent").exists()


def test_c3_freeze_inputs_refuses_a_bad_input_file_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = write_h5_inputs(tmp_path / "inputs", manifest=False)
    (tmp_path / "inputs" / "prompts" / "repair.txt").write_bytes(files["prompts/repair.txt"] + b"x")
    out = tmp_path / "INPUTS.sha256"
    result = cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", out), plain_env(), guarded=False)
    assert error_of(result) == "FrozenInputError"
    assert not out.exists()
    assert "prompts/repair.txt" in stderr_of(capsys)


def test_c3_a_missing_input_file_is_an_os_error_and_nothing_is_written(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path / "inputs", manifest=False)
    (tmp_path / "inputs" / "warmup.json").unlink()
    out = tmp_path / "INPUTS.sha256"
    result = cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", out), plain_env(), guarded=False)
    assert error_of(result) == "FileNotFoundError"
    assert not out.exists()


# ---------------------------------------------------------------- C-4 freeze gold


def freeze_gold_fields(inputs: FrozenInputs, gold_dir: Path, expected: bytes) -> tuple[tuple[str, str], ...]:
    gold = load_gold_set(gold_dir)
    hashes = inputs.hashes
    sessions: list[tuple[str, str]] = []
    for spec in CAP_SESSIONS:
        rows = str(frozen.cap_session_rows(gold, spec.source_ids))
        sessions += [
            ("session", spec.session_id),
            ("session_rows", rows),
            ("session_sources", ",".join(spec.source_ids)),
        ]
    return (
        ("gold_version", "v1.0"),
        ("gold_manifest_sha256", canonical_sha256(dict(inputs.gold_manifest))),
        *((key, getattr(hashes, key)) for key in HASH_KEYS),
        ("warmup_sha256", hashes.warmup_sha256),
        ("request_count", str(len(inputs.all_requests()))),
        *sessions,
        ("overlap_equal", "0"),
        ("overlap_contained", "0"),
        ("delimiter_hits", "0"),
        ("expected_freeze_sha256", hashlib.sha256(expected).hexdigest()),
    )


class WriteOrder:
    """Wraps `records.write_new_file` and lists the base name of each file it writes."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.names: list[str] = []
        real = cli.records.write_new_file

        def write(path: str, data: bytes, **options: Any) -> str:
            self.names.append(Path(path).name)
            return real(path, data, **options)

        monkeypatch.setattr(cli.records, "write_new_file", write)


def test_c4_freeze_gold_writes_the_cap_sessions_then_the_expected_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirs = make_dirs(tmp_path)
    order = WriteOrder(monkeypatch)
    syncs = Syncs()
    result = cli.dispatch(freeze_gold_argv(dirs), plain_env(fsync=syncs), guarded=True)
    inputs = freeze(dirs.gold)
    expected = frozen.expected_freeze_bytes(inputs)
    assert order.names == ["cap-sessions.json", "EXPECTED-FREEZE.json"]
    assert len(syncs.fds) == 2
    assert sorted(path.name for path in dirs.out.iterdir()) == ["EXPECTED-FREEZE.json", "cap-sessions.json"]
    assert (dirs.out / "cap-sessions.json").read_bytes() == frozen.cap_sessions_bytes("v1.0", CAP_SESSIONS)
    assert (dirs.out / "EXPECTED-FREEZE.json").read_bytes() == expected
    assert result == cli.CommandResult("freeze gold", 0, freeze_gold_fields(inputs, dirs.gold, expected))
    assert whitelisted(result)


def test_c4_with_a_cap_sessions_file_only_the_expected_freeze_is_written(tmp_path: Path) -> None:
    dirs = make_dirs(tmp_path)
    cap = tmp_path / "given-cap-sessions.json"
    cap.write_bytes(frozen.cap_sessions_bytes("v1.0", CAP_SESSIONS))
    result = cli.dispatch(freeze_gold_argv(dirs, "--cap-sessions", str(cap)), plain_env(), guarded=True)
    assert result.exit_code == 0
    assert [path.name for path in dirs.out.iterdir()] == ["EXPECTED-FREEZE.json"]
    assert (dirs.out / "EXPECTED-FREEZE.json").read_bytes() == frozen.expected_freeze_bytes(freeze(dirs.gold))


def test_c4_check_only_writes_nothing_and_passes_on_the_same_bytes(tmp_path: Path) -> None:
    dirs = make_dirs(tmp_path)
    assert cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True).exit_code == 0
    copy = tmp_path / "copy"
    copy.mkdir()
    for name in ("cap-sessions.json", "EXPECTED-FREEZE.json"):
        (copy / name).write_bytes((dirs.out / name).read_bytes())
    empty = tmp_path / "empty"
    empty.mkdir()
    check = dataclasses.replace(dirs, out=empty)
    extra = ("--cap-sessions", str(copy / "cap-sessions.json"), "--check-only")
    result = cli.dispatch(
        freeze_gold_argv(check, *extra, "--expected-freeze", str(copy / "EXPECTED-FREEZE.json")),
        plain_env(),
        guarded=True,
    )
    assert result.exit_code == 0
    assert result.fields[-1] == (
        "expected_freeze_sha256",
        hashlib.sha256((copy / "EXPECTED-FREEZE.json").read_bytes()).hexdigest(),
    )
    assert list(empty.iterdir()) == []
    alone = cli.dispatch(
        freeze_gold_argv(check, "--check-only", "--expected-freeze", str(copy / "EXPECTED-FREEZE.json")),
        plain_env(),
        guarded=True,
    )
    assert alone.exit_code == 0
    assert list(empty.iterdir()) == []


@pytest.mark.parametrize("name", ["EXPECTED-FREEZE.json", "cap-sessions.json"])
@pytest.mark.parametrize("change", ["byte", "space"])
def test_c4_check_only_fails_on_a_one_byte_change(tmp_path: Path, name: str, change: str) -> None:
    dirs = make_dirs(tmp_path)
    assert cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True).exit_code == 0
    copy = tmp_path / "copy"
    copy.mkdir()
    for each in ("cap-sessions.json", "EXPECTED-FREEZE.json"):
        (copy / each).write_bytes((dirs.out / each).read_bytes())
    data = (copy / name).read_bytes()
    if change == "byte" and name == "cap-sessions.json":
        changed = data.replace(b"syn_1", b"syn_5", 1)
    elif change == "byte":
        position = re.search(rb"[0-9a-f]{64}", data).start()  # type: ignore[union-attr]
        replacement = b"1" if data[position : position + 1] == b"0" else b"0"
        changed = data[:position] + replacement + data[position + 1 :]
    else:
        changed = data[:-1] + b" \n"
    (copy / name).write_bytes(changed)
    empty = tmp_path / "empty"
    empty.mkdir()
    argv = freeze_gold_argv(
        dataclasses.replace(dirs, out=empty),
        "--cap-sessions",
        str(copy / "cap-sessions.json"),
        "--check-only",
        "--expected-freeze",
        str(copy / "EXPECTED-FREEZE.json"),
    )
    result = cli.dispatch(argv, plain_env(), guarded=True)
    assert result.exit_code == 1
    assert result.fields[0][0] == "error"
    assert result.fields[0][1] in {"FrozenInputError", "ReportError"}
    assert list(empty.iterdir()) == []


@pytest.mark.parametrize(("ending", "passes"), [(b"\r\n", True), (b"\r", False)], ids=["crlf", "lone-cr"])
def test_c4_check_only_passes_on_a_crlf_checkout_and_fails_on_a_lone_cr(
    tmp_path: Path, ending: bytes, passes: bool
) -> None:
    """The committed copies are compared over their LF bytes, as every other reader of them does, so a CRLF checkout
    still verifies; a lone CR is not a line end and still fails."""
    dirs = make_dirs(tmp_path)
    assert cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True).exit_code == 0
    copy = tmp_path / "copy"
    copy.mkdir()
    for each in ("cap-sessions.json", "EXPECTED-FREEZE.json"):
        (copy / each).write_bytes((dirs.out / each).read_bytes().replace(b"\n", ending))
    empty = tmp_path / "empty"
    empty.mkdir()
    argv = freeze_gold_argv(
        dataclasses.replace(dirs, out=empty),
        "--cap-sessions",
        str(copy / "cap-sessions.json"),
        "--check-only",
        "--expected-freeze",
        str(copy / "EXPECTED-FREEZE.json"),
    )
    result = cli.dispatch(argv, plain_env(), guarded=True)
    if passes:
        assert result.exit_code == 0
        assert result.fields[-1] == (
            "expected_freeze_sha256",
            hashlib.sha256((dirs.out / "EXPECTED-FREEZE.json").read_bytes()).hexdigest(),
        )
    else:
        assert result.exit_code == 1
        assert result.fields[0][1] in {"FrozenInputError", "ReportError"}
    assert list(empty.iterdir()) == []


def overlap_gold(root: Path, texts: list[str]) -> Path:
    """`synthetic_gold`'s four conversations of 20 rows, plus `syn_x` whose segments hold `texts` and no item."""
    conversations, items = gold_rows()
    conversations.append(make_conversation("syn_x", texts))
    return write_version(root, conversations, items, name="v1.0")


def test_c4_a_synthetic_text_equal_to_gold_text_is_refused_with_nothing_written(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "ZQXCANARY base rate"
    dirs = make_dirs(tmp_path, lambda root: overlap_gold(root, [PROBE_ITEMS[0].claim, canary]))
    result = cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True)
    assert error_of(result) == "FrozenInputError"
    assert list(dirs.out.iterdir()) == []
    err = stderr_of(capsys)
    assert "equal=1" in err
    assert "ZQXCANARY" not in err and PROBE_ITEMS[0].claim not in err


def test_c4_containment_and_delimiter_hits_are_counted_not_refused(tmp_path: Path) -> None:
    contained = "A gold line of exactly forty characters."
    assert len(contained) == frozen.CONTAINED_MIN_CHARS
    first = PROBE_ITEMS[0]
    sources = (ProbeSource("src_1", f"Before it. {contained} After it."), *first.sources[1:])
    probe = (ProbeItem(first.item_id, first.claim, sources), *PROBE_ITEMS[1:])
    texts = [f"{H5_PROBE_DELIMITERS[1]} in a gold line", contained, contained[1:]]
    dirs = make_dirs(tmp_path, lambda root: overlap_gold(root, texts), files=h5_input_bytes(probe_items=probe))
    result = cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True)
    assert result.exit_code == 0
    fields = dict(result.fields)
    assert (fields["overlap_equal"], fields["overlap_contained"], fields["delimiter_hits"]) == ("0", "1", "1")
    assert sorted(path.name for path in dirs.out.iterdir()) == ["EXPECTED-FREEZE.json", "cap-sessions.json"]


def test_c4_a_gold_set_that_is_not_v1_0_is_refused(tmp_path: Path) -> None:
    dirs = make_dirs(tmp_path, lambda root: synthetic_gold(root, name="v0.9"))
    result = cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True)
    assert error_of(result) == "InputsError"
    assert list(dirs.out.iterdir()) == []


def test_c4_a_tampered_input_file_is_refused_before_gold_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dirs = make_dirs(tmp_path)
    (dirs.inputs / "probe.json").write_bytes(
        (dirs.inputs / "probe.json").read_bytes().replace(b"number 1.", b"number 9.")
    )

    def no_gold(*_args: Any) -> Any:
        raise AssertionError("gold was read")

    monkeypatch.setattr(cli, "load_gold_set", no_gold)
    monkeypatch.setattr(frozen, "load_gold_set", no_gold)
    result = cli.dispatch(freeze_gold_argv(dirs), plain_env(), guarded=True)
    assert error_of(result) == "FrozenInputError"
    assert list(dirs.out.iterdir()) == []


# ---------------------------------------------------------------- C-5 to C-8 run, over the machine fakes


@dataclasses.dataclass
class Schedule:
    root: Path
    dirs: Dirs
    host: FakeHost
    popen: FakeMachinePopen
    env: cli.CliEnv
    config: Path

    @property
    def run_dir(self) -> Path:
        return self.root / "runs" / json.loads(self.config.read_text(encoding="ascii"))["run_name"]

    def run_argv(self, **changes: str) -> list[str]:
        options = {
            "--config": str(self.config),
            "--inputs-dir": str(self.dirs.inputs),
            "--gold-dir": str(self.dirs.gold),
            "--cap-sessions": str(self.dirs.out / "cap-sessions.json"),
            "--expected-freeze": str(self.dirs.out / "EXPECTED-FREEZE.json"),
            **changes,
        }
        return ["run", *(part for pair in options.items() for part in pair)]


def schedule(
    root: Path, scripts: Mapping[str, ServerScript] | None = None, *, run_name: str = "dryrun-1", **popen_options: Any
) -> Schedule:
    """The standard H5 inputs, `write_h4_gold`, frozen by `freeze gold` through `dispatch`, every timed launch
    answering, and an entry config for `<root>/runs/<run_name>`."""
    dirs = make_dirs(root, write_h4_gold)
    inputs = freeze(dirs.gold)
    answers = answering_scripts(inputs, load_gold_set(dirs.gold))
    answers.update(scripts or {})
    host = FakeHost(inputs, answers, proc_status=proc_status())
    threads = FakeThreads()
    install_machine(host, threads)
    popen = FakeMachinePopen(host.clock, **popen_options)
    env = cli.CliEnv(
        tools=host.tools(),
        machine_tools=machine_tools(host, popen, threads),
        now=fixed_now,
        fsync=no_fsync,
        client_factory=host.client,
    )
    (root / "runs").mkdir()
    config = write_json(root / "config.json", entry_config_data(root / "runs", run_name))
    frozen_result = cli.dispatch(
        ["freeze", "gold", "--inputs-dir", str(dirs.inputs), "--gold-dir", str(dirs.gold), "--out-dir", str(dirs.out)],
        env,
        guarded=True,
    )
    assert frozen_result.exit_code == 0
    return Schedule(root, dirs, host, popen, env, config)


def test_c5_run_completes_a_synthetic_schedule_and_its_report_checks_the_freeze(tmp_path: Path) -> None:
    rig = schedule(tmp_path)
    result = cli.dispatch(rig.run_argv(), rig.env, guarded=True)
    inputs = freeze(rig.dirs.gold)
    assert result == cli.CommandResult(
        "run", 0, (("run", "dryrun-1"), ("stopped", "no"), ("request_count", str(len(inputs.all_requests()))))
    )
    assert whitelisted(result)
    view = load_run(str(rig.run_dir))
    assert schedule_end(view) == "complete"
    assert dict(view.manifest["hashes"]["requests"]) == dict(inputs.hashes.requests)
    assert rig.popen.running() == []
    expected = parse_expected_freeze((rig.dirs.out / "EXPECTED-FREEZE.json").read_bytes())
    report_inputs = ReportInputs(
        shared_memory=MappingProxyType({}),
        shared_memory_evidence_sha256=None,
        deadline_override_sec=None,
        rerun_allowance_override_sec=None,
        reopening_reason=None,
        expected=expected,
    )
    outcome = entry.score_and_report(
        str(rig.run_dir),
        str(rig.dirs.gold),
        report_inputs,
        (),
        tools=LaunchTools(),
        now=fixed_now,
        fsync=no_fsync,
        load_evidence=machine.load_evidence,
        resamples=RESAMPLES,
    )
    assert outcome.end == "complete" and outcome.rule in RULES
    report = json.loads(Path(outcome.paths[-1]).read_bytes())
    assert report["gold_manifest_checked"] is True


def test_c8_a_blocked_pre_run_exits_3_as_pre_run_stopped(tmp_path: Path) -> None:
    scripts = {unit_of(RunKey(c, "pre_run", None, 1)): ServerScript(spawn_error=OSError()) for c in CANDIDATE_IDS}
    rig = schedule(tmp_path, scripts)
    result = cli.dispatch(rig.run_argv(), rig.env, guarded=True)
    inputs = freeze(rig.dirs.gold)
    assert result == cli.CommandResult(
        "run",
        3,
        (("run", "dryrun-1"), ("stopped", "pre_run_stopped"), ("request_count", str(len(inputs.all_requests())))),
    )
    assert entry.RECORDS_ONLY_EXIT == 3
    assert schedule_end(load_run(str(rig.run_dir))) == "pre_run_stopped"


def test_c8_exhausted_runs_exit_3_with_the_unit(tmp_path: Path) -> None:
    rig = schedule(tmp_path, apps={f"qwen3-4b.run1.a{n}": apps_output(9001) for n in (1, 2, 3)})
    result = cli.dispatch(rig.run_argv(), rig.env, guarded=True)
    inputs = freeze(rig.dirs.gold)
    assert result == cli.CommandResult(
        "run",
        3,
        (
            ("run", "dryrun-1"),
            ("stopped", "runs_exhausted"),
            ("unit", "qwen3-4b.run1.a3"),
            ("request_count", str(len(inputs.all_requests()))),
        ),
    )
    assert whitelisted(result)


def test_c6_a_tampered_input_file_refuses_the_run_with_no_directory(tmp_path: Path) -> None:
    rig = schedule(tmp_path)
    path = rig.dirs.inputs / "prompts" / "probe-system.txt"
    path.write_bytes(path.read_bytes().replace(b"Judge", b"Weigh"))
    result = cli.dispatch(rig.run_argv(), rig.env, guarded=True)
    assert error_of(result) == "FrozenInputError"
    assert list((tmp_path / "runs").iterdir()) == []
    assert rig.host.events == []


def test_c6_a_hash_mismatch_against_the_expected_freeze_refuses_the_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = schedule(tmp_path)
    data = json.loads((rig.dirs.out / "EXPECTED-FREEZE.json").read_bytes())
    data["hashes"]["probe_sha256"] = "0" * 64
    other = write_json(tmp_path / "other-freeze.json", data)
    result = cli.dispatch(rig.run_argv(**{"--expected-freeze": str(other)}), rig.env, guarded=True)
    assert error_of(result) == "FrozenInputError"
    assert "hashes.probe_sha256" in stderr_of(capsys)
    assert list((tmp_path / "runs").iterdir()) == []
    assert rig.host.events == []


def test_c6_a_malformed_expected_freeze_and_a_bad_config_are_refusals(tmp_path: Path) -> None:
    rig = schedule(tmp_path)
    bad = tmp_path / "bad-freeze.json"
    bad.write_bytes(b"{}\n")
    assert (
        error_of(cli.dispatch(rig.run_argv(**{"--expected-freeze": str(bad)}), rig.env, guarded=True)) == "ReportError"
    )
    data = entry_config_data(tmp_path / "runs", "dryrun-1")
    data["operator"]["namespace_eth0_check"] = "failed"
    config = write_json(tmp_path / "bad-config.json", data)
    assert error_of(cli.dispatch(rig.run_argv(**{"--config": str(config)}), rig.env, guarded=True)) == "ReportError"
    assert list((tmp_path / "runs").iterdir()) == []


def test_c6_a_missing_expected_freeze_is_a_usage_error_with_no_directory(tmp_path: Path) -> None:
    rig = schedule(tmp_path)
    argv = rig.run_argv()
    position = argv.index("--expected-freeze")
    with pytest.raises(SystemExit) as caught:
        cli.dispatch(argv[:position] + argv[position + 2 :], rig.env, guarded=True)
    assert caught.value.code == 2
    assert list((tmp_path / "runs").iterdir()) == []


# ---------------------------------------------------------------- C-6 the real-versus-dry guards (AMB-151)


class GuardPassed(Exception):
    """Raised by the gold-step recorder: the guards let the call through."""


@pytest.fixture
def gold_step(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Replace the gold step, so a call the guards let through stops before anything is read."""
    calls: list[tuple[Any, ...]] = []

    def record(*args: Any) -> Any:
        calls.append(args)
        raise GuardPassed

    monkeypatch.setattr(frozen, "load_frozen", record)
    monkeypatch.setattr(cli, "load_gold_set", record)
    return calls


@dataclasses.dataclass(frozen=True)
class Fake:
    """Fake resolved paths under `tmp_path/fake`, which never exist."""

    base: str
    suffix: str = cli.REAL_GOLD_SUFFIXES[0]

    @property
    def real_gold(self) -> str:
        return f"{self.base}/{self.suffix}"

    @property
    def dry_gold(self) -> str:
        return f"{self.base}/work/v1.0"

    @property
    def frozen_dir(self) -> str:
        return f"{self.base}/{cli.FROZEN_DIR_SUFFIX}"

    def inside(self, name: str) -> str:
        return f"{self.frozen_dir}/{name}"

    def outside(self, name: str) -> str:
        return f"{self.base}/work/{name}"


def fake(tmp_path: Path, suffix: str = cli.REAL_GOLD_SUFFIXES[0]) -> Fake:
    return Fake(f"{tmp_path.as_posix()}/fake", suffix)


def realpaths_for(paths: Fake, *, real: bool, inputs: str, cap: str, expected: str) -> dict[str, str]:
    where = {"frozen": paths.frozen_dir, "inside": None, "outside": None}
    return {
        "GOLD": paths.real_gold if real else paths.dry_gold,
        "INPUTS": where["frozen"] if inputs == "frozen" else paths.outside("inputs"),
        "CAP": paths.inside("cap-sessions.json") if cap == "inside" else paths.outside("cap-sessions.json"),
        "EXPECTED": paths.inside("EXPECTED-FREEZE.json") if expected == "inside" else paths.outside("x.json"),
    }


def assert_guard(
    result_or_passed: Callable[[], cli.CommandResult],
    refused: bool,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    if refused:
        result = result_or_passed()
        assert error_of(result) == "GuardRefused"
        err = stderr_of(capsys)
        assert "AMB-151" in err
        assert_no_path(err.split("GuardRefused: ", 1)[1], tmp_path)
    else:
        with pytest.raises(GuardPassed):
            result_or_passed()


RUN_GUARD_CASES = [
    # real, run name, inputs, cap sessions, expected freeze, refused
    (True, "run_1", "frozen", "inside", "inside", False),
    (True, "dryrun-1", "frozen", "inside", "inside", True),
    (True, "run_1", "outside", "inside", "inside", True),
    (True, "run_1", "frozen", "outside", "inside", True),
    (True, "run_1", "frozen", "inside", "outside", True),
    (False, "dryrun-1", "outside", "outside", "outside", False),
    (False, "dryrun-1", "frozen", "outside", "outside", False),
    (False, "run_1", "outside", "outside", "outside", True),
    (False, "dryrun-1", "outside", "inside", "outside", True),
    (False, "dryrun-1", "outside", "outside", "inside", True),
]


@pytest.mark.parametrize("suffix", cli.REAL_GOLD_SUFFIXES)
@pytest.mark.parametrize(("real", "run_name", "inputs", "cap", "expected", "refused"), RUN_GUARD_CASES)
def test_c6_the_run_guards_in_both_directions(
    tmp_path: Path,
    gold_step: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    suffix: str,
    real: bool,
    run_name: str,
    inputs: str,
    cap: str,
    expected: str,
    refused: bool,
) -> None:
    (tmp_path / "runs").mkdir()
    config = write_json(tmp_path / "config.json", entry_config_data(tmp_path / "runs", run_name))
    paths = fake(tmp_path, suffix)
    env = plain_env(realpaths=realpaths_for(paths, real=real, inputs=inputs, cap=cap, expected=expected))
    argv = ["run", "--config", str(config), "--inputs-dir", "INPUTS", "--gold-dir", "GOLD"]
    argv += ["--cap-sessions", "CAP", "--expected-freeze", "EXPECTED"]
    assert_guard(lambda: cli.dispatch(argv, env, guarded=True), refused, capsys, tmp_path)
    assert list((tmp_path / "runs").iterdir()) == []
    if not refused:
        resolve = env.tools.realpath
        assert gold_step[0][:3] == (resolve("INPUTS"), resolve("GOLD"), resolve("CAP"))


@pytest.mark.parametrize(
    "form",
    [
        lambda path: path.replace("/", chr(92)),
        lambda path: path.upper(),
        lambda path: path + "/",
        lambda path: path.replace("/", chr(92)).lower(),
    ],
)
@pytest.mark.parametrize("suffix", cli.REAL_GOLD_SUFFIXES)
def test_c6_a_real_gold_dir_is_known_by_its_suffix_in_either_separator_and_case(
    tmp_path: Path,
    gold_step: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    form: Callable[[str], str],
    suffix: str,
) -> None:
    (tmp_path / "runs").mkdir()
    config = write_json(tmp_path / "config.json", entry_config_data(tmp_path / "runs", "dryrun-1"))
    paths = fake(tmp_path, suffix)
    realpaths = realpaths_for(paths, real=True, inputs="frozen", cap="outside", expected="outside")
    realpaths["GOLD"] = form(paths.real_gold)
    argv = ["run", "--config", str(config), "--inputs-dir", "INPUTS", "--gold-dir", "GOLD"]
    argv += ["--cap-sessions", "CAP", "--expected-freeze", "EXPECTED"]
    assert_guard(lambda: cli.dispatch(argv, plain_env(realpaths=realpaths), guarded=True), True, capsys, tmp_path)
    assert gold_step == []


FREEZE_GOLD_GUARD_CASES = [
    # real, inputs, cap sessions (None: not given), check-only expected freeze (None: no check), refused
    (True, "frozen", None, None, False),
    (True, "frozen", "outside", None, False),
    (True, "outside", None, None, True),
    (True, "frozen", "inside", "inside", False),
    (True, "frozen", "outside", "inside", True),
    (True, "frozen", "inside", "outside", True),
    (True, "frozen", None, "inside", True),
    (False, "outside", "outside", None, False),
    (False, "frozen", "outside", "outside", False),
    (False, "outside", None, "outside", False),
    (False, "outside", "inside", None, True),
    (False, "outside", "outside", "inside", True),
]


@pytest.mark.parametrize("suffix", cli.REAL_GOLD_SUFFIXES)
@pytest.mark.parametrize(("real", "inputs", "cap", "expected", "refused"), FREEZE_GOLD_GUARD_CASES)
def test_c6_the_freeze_gold_guards_in_both_directions(
    tmp_path: Path,
    gold_step: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    suffix: str,
    real: bool,
    inputs: str,
    cap: str | None,
    expected: str | None,
    refused: bool,
) -> None:
    realpaths = realpaths_for(fake(tmp_path, suffix), real=real, inputs=inputs, cap=cap or "", expected=expected or "")
    argv = ["freeze", "gold", "--inputs-dir", "INPUTS", "--gold-dir", "GOLD", "--out-dir", str(tmp_path / "out")]
    if cap is not None:
        argv += ["--cap-sessions", "CAP"]
    if expected is not None:
        argv += ["--check-only", "--expected-freeze", "EXPECTED"]
    assert_guard(lambda: cli.dispatch(argv, plain_env(realpaths=realpaths), guarded=True), refused, capsys, tmp_path)


CONTEXT_GUARD_CASES = [
    # real, inputs, cap sessions (None: not given), refused
    (True, "frozen", None, False),
    (True, "frozen", "inside", False),
    (True, "outside", None, True),
    (False, "outside", None, False),
    (False, "frozen", "outside", False),
    (False, "outside", "inside", True),
]


@pytest.mark.parametrize("suffix", cli.REAL_GOLD_SUFFIXES)
@pytest.mark.parametrize(("real", "inputs", "cap", "refused"), CONTEXT_GUARD_CASES)
def test_c6_the_context_gold_guards_in_both_directions(
    tmp_path: Path,
    gold_step: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    suffix: str,
    real: bool,
    inputs: str,
    cap: str | None,
    refused: bool,
) -> None:
    config = write_json(tmp_path / "launch.json", launch_config_data())
    (tmp_path / "logs").mkdir()
    realpaths = realpaths_for(fake(tmp_path, suffix), real=real, inputs=inputs, cap=cap or "", expected="")
    argv = ["context", "--launch-config", str(config), "--inputs-dir", "INPUTS", "--server-log-dir"]
    argv += [str(tmp_path / "logs"), "--gold-dir", "GOLD"]
    if cap is not None:
        argv += ["--cap-sessions", "CAP"]
    assert_guard(lambda: cli.dispatch(argv, plain_env(realpaths=realpaths), guarded=True), refused, capsys, tmp_path)
    assert list((tmp_path / "logs").iterdir()) == []


def sibling(paths: Fake, name: str | None = None) -> str:
    """A folder whose path ends with the frozen suffix as text but not at a path segment, and a file in it."""
    folder = f"{paths.base}/x{cli.FROZEN_DIR_SUFFIX}"
    return folder if name is None else f"{folder}/{name}"


def guard_argv(tmp_path: Path, command: str, *, run_name: str = "run_1") -> list[str]:
    """One gold step over the short argument names, reading both files where the subcommand takes them."""
    if command == "run":
        (tmp_path / "runs").mkdir(exist_ok=True)
        config = write_json(tmp_path / "config.json", entry_config_data(tmp_path / "runs", run_name))
        argv = ["run", "--config", str(config), "--inputs-dir", "INPUTS", "--gold-dir", "GOLD"]
        return [*argv, "--cap-sessions", "CAP", "--expected-freeze", "EXPECTED"]
    if command == "freeze gold":
        argv = ["freeze", "gold", "--inputs-dir", "INPUTS", "--gold-dir", "GOLD", "--out-dir", str(tmp_path / "out")]
        return [*argv, "--cap-sessions", "CAP", "--check-only", "--expected-freeze", "EXPECTED"]
    config = write_json(tmp_path / "launch.json", launch_config_data())
    (tmp_path / "logs").mkdir(exist_ok=True)
    argv = ["context", "--launch-config", str(config), "--inputs-dir", "INPUTS", "--server-log-dir"]
    return [*argv, str(tmp_path / "logs"), "--gold-dir", "GOLD", "--cap-sessions", "CAP"]


@pytest.mark.parametrize("command", ["run", "freeze gold", "context"])
def test_c6_a_sibling_of_the_frozen_inputs_is_not_the_frozen_inputs_for_a_real_gold_set(
    tmp_path: Path, gold_step: list[tuple[Any, ...]], capsys: pytest.CaptureFixture[str], command: str
) -> None:
    paths = fake(tmp_path)
    realpaths = realpaths_for(paths, real=True, inputs="frozen", cap="inside", expected="inside")
    realpaths["INPUTS"] = sibling(paths)
    env = plain_env(realpaths=realpaths)
    assert_guard(lambda: cli.dispatch(guard_argv(tmp_path, command), env, guarded=True), True, capsys, tmp_path)
    assert gold_step == []


@pytest.mark.parametrize("moved", ["CAP", "EXPECTED"])
@pytest.mark.parametrize("command", ["run", "freeze gold"])
def test_c6_a_real_file_in_a_sibling_of_the_frozen_inputs_is_not_inside_them(
    tmp_path: Path, gold_step: list[tuple[Any, ...]], capsys: pytest.CaptureFixture[str], command: str, moved: str
) -> None:
    paths = fake(tmp_path)
    realpaths = realpaths_for(paths, real=True, inputs="frozen", cap="inside", expected="inside")
    realpaths[moved] = sibling(paths, realpaths[moved].rsplit("/", 1)[1])
    result = cli.dispatch(guard_argv(tmp_path, command), plain_env(realpaths=realpaths), guarded=True)
    assert error_of(result) == "GuardRefused"
    assert "--cap-sessions and --expected-freeze to resolve inside" in stderr_of(capsys)
    assert gold_step == []


@pytest.mark.parametrize("command", ["run", "freeze gold", "context"])
def test_c6_a_dry_file_in_a_sibling_of_the_frozen_inputs_is_accepted(
    tmp_path: Path, gold_step: list[tuple[Any, ...]], command: str
) -> None:
    paths = fake(tmp_path)
    realpaths = realpaths_for(paths, real=False, inputs="outside", cap="outside", expected="outside")
    realpaths["CAP"] = sibling(paths, "cap-sessions.json")
    realpaths["EXPECTED"] = sibling(paths, "EXPECTED-FREEZE.json")
    argv = guard_argv(tmp_path, command, run_name="dryrun-1")
    with pytest.raises(GuardPassed):
        cli.dispatch(argv, plain_env(realpaths=realpaths), guarded=True)


@pytest.mark.parametrize(("where", "check_only"), [("frozen", False), ("under", False), ("frozen", True)])
def test_c6_a_dry_freeze_gold_may_not_write_into_the_frozen_inputs(
    tmp_path: Path,
    gold_step: list[tuple[Any, ...]],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    check_only: bool,
) -> None:
    """S2 §3.12 and AMB-151: dry-run hash files never sit where they would be committed. With `--check-only`
    nothing is written, so the out dir is not checked."""
    writes: list[tuple[Any, ...]] = []
    monkeypatch.setattr(cli.records, "write_new_file", lambda *args, **_kwargs: writes.append(args))
    paths = fake(tmp_path)
    realpaths = realpaths_for(paths, real=False, inputs="outside", cap="outside", expected="outside")
    realpaths["OUT"] = paths.frozen_dir if where == "frozen" else paths.inside("drafts")
    argv = ["freeze", "gold", "--inputs-dir", "INPUTS", "--gold-dir", "GOLD", "--out-dir", "OUT"]
    if check_only:
        checked = [*argv, "--check-only", "--expected-freeze", "EXPECTED"]
        with pytest.raises(GuardPassed):
            cli.dispatch(checked, plain_env(realpaths=realpaths), guarded=True)
        return
    result = cli.dispatch(argv, plain_env(realpaths=realpaths), guarded=True)
    assert error_of(result) == "GuardRefused"
    err = stderr_of(capsys)
    assert "--out-dir" in err and "AMB-151" in err
    assert_no_path(err.split("GuardRefused: ", 1)[1], tmp_path)
    assert gold_step == [] and writes == []


# ---------------------------------------------------------------- C-7 unguarded use runs no gold step (AMB-159)


@pytest.mark.parametrize(
    "argv",
    [
        ["freeze", "gold", "--inputs-dir", "i", "--gold-dir", "dryrun-g", "--out-dir", "o"],
        [
            "run",
            "--config",
            "c",
            "--inputs-dir",
            "i",
            "--gold-dir",
            "g",
            "--cap-sessions",
            "s",
            "--expected-freeze",
            "e",
        ],
        ["context", "--launch-config", "l", "--inputs-dir", "i", "--server-log-dir", "d", "--gold-dir", "g"],
        [
            "context",
            "--launch-config",
            "l",
            "--inputs-dir",
            "i",
            "--server-log-dir",
            "d",
            "--gold-dir",
            "g",
            "--sources",
        ],
        ["report", "--run-dir", "runs/dryrun-1", "--gold-dir", "g", "--expected-freeze", "e"],
        ["void", "--run-dir", "runs/dryrun-1", "--unit", "qwen3-4b.run1.a1", "--detail", "x"],
        ["report"],
    ],
)
def test_c7_unguarded_use_refuses_every_gold_step_whatever_the_path(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    recorder = EntryRecorder(0)
    monkeypatch.setattr(entry, "main", recorder)
    reads = Reads()
    result = cli.dispatch(argv, plain_env(read_bytes=reads), guarded=False)
    assert error_of(result) == "GuardRefused"
    assert result.command == {"freeze": "freeze gold"}.get(argv[0], argv[0])
    assert recorder.calls == [] and reads.paths == []
    err = stderr_of(capsys)
    assert "AMB-159" in err
    assert_no_path(err.split("GuardRefused: ", 1)[1], tmp_path)


def test_c7_unguarded_use_runs_freeze_inputs_and_context_without_gold(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path / "inputs")
    out = tmp_path / "INPUTS.sha256"
    assert cli.dispatch(freeze_inputs_argv(tmp_path / "inputs", out), plain_env(), guarded=False).exit_code == 0
    host = FakeHost(freeze(synthetic_gold(tmp_path / "g")))
    config = write_json(tmp_path / "launch.json", launch_config_data())
    (tmp_path / "logs").mkdir()
    argv = ["context", "--launch-config", str(config), "--inputs-dir", str(tmp_path / "inputs")]
    argv += ["--server-log-dir", str(tmp_path / "logs"), "--sources"]
    result = cli.dispatch(argv, host_env(host), guarded=False)
    assert result.exit_code == 0
    assert whitelisted(result)


# ---------------------------------------------------------------- C-9 and C-10 context (AMB-150)


def context_rig(
    tmp_path: Path, *, counts: Mapping[str, int] | None = None, scripts: Any = None
) -> tuple[FakeHost, Dirs]:
    dirs = make_dirs(tmp_path)
    host = FakeHost(freeze(dirs.gold), scripts, counts=counts)
    (tmp_path / "logs").mkdir()
    write_json(tmp_path / "launch.json", launch_config_data())
    return host, dirs


def context_argv(tmp_path: Path, dirs: Dirs, *extra: str) -> list[str]:
    argv = ["context", "--launch-config", str(tmp_path / "launch.json"), "--inputs-dir", str(dirs.inputs)]
    return [*argv, "--server-log-dir", str(tmp_path / "logs"), *extra]


def launch_name(candidate_id: str) -> str:
    return f"context-{CANDIDATE_SLUGS[candidate_id]}-{STAMP}"


def groups(fields: tuple[tuple[str, str], ...], first: str, keys: tuple[str, ...]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for key, value in fields:
        if key == first:
            found.append({key: value})
        elif key in keys and found and key not in found[-1]:
            found[-1][key] = value
    return found


CHECK_KEYS = ("check", "check_label", "input_tokens", "uses", "limit", "fits")


FORM_LABELS = ("request", "repair_bound", "request", "repair_bound")


def test_c9_with_gold_every_frozen_request_is_counted_in_both_forms(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path, counts={"syn_2": 11_000})
    result = cli.dispatch(context_argv(tmp_path, dirs, "--gold-dir", str(dirs.gold)), host_env(host), guarded=True)
    inputs = freeze(dirs.gold)
    assert result.command == "context" and result.exit_code == 3
    assert whitelisted(result)
    assert [value for key, value in result.fields if key == "candidate"] == list(CANDIDATE_IDS)
    checks = groups(result.fields, "check", CHECK_KEYS)
    assert all(set(check) == set(CHECK_KEYS) for check in checks)
    items = [frozen_request.item_id for frozen_request in inputs.all_requests()]
    assert [(check["check"], check["check_label"]) for check in checks] == [
        (item, label) for item in items for label in FORM_LABELS
    ] * 3
    kinds = {frozen_request.item_id: frozen_request.request.kind for frozen_request in inputs.all_requests()}
    for check in checks:
        count = 11_000 if check["check"] == "syn_2" else 100
        max_tokens = MAX_TOKENS[kinds[check["check"]]]
        reserved = max_tokens if check["check_label"] == "repair_bound" else 0
        uses = count + reserved + max_tokens
        assert check["input_tokens"] == str(count)
        assert (check["uses"], check["limit"]) == (str(uses), str(CONDITIONS.context * 9 // 10))
        assert check["fits"] == ("true" if uses <= CONDITIONS.context * 9 // 10 else "false")
    assert {check["fits"] for check in checks if check["check"] == "syn_2"} == {"false"}


def record_counts(host: FakeHost, *, unconstrained: Mapping[str, int] | None = None) -> list[bytes]:
    """Keep every count body in order; `unconstrained` gives an item's count for its unconstrained form only."""
    bodies: list[bytes] = []
    overrides = dict(unconstrained or {})

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == COUNT_PATH:
            bodies.append(request.content)
            body = json.loads(request.content)
            frozen_request = host.items.get(body["messages"][1]["content"])
            if frozen_request is not None and "response_format" not in body and frozen_request.item_id in overrides:
                return input_tokens_response(overrides[frozen_request.item_id])
        return FakeHost.handle(host, request)

    host.handle = handle  # type: ignore[method-assign]
    return bodies


def test_c_c_each_request_gets_four_checks_in_the_fixed_form_order(tmp_path: Path) -> None:
    """HB-1, as the runner's pre-run check: constrained count, constrained repair bound, unconstrained count,
    unconstrained repair bound, by the exact bytes counted."""
    host, dirs = context_rig(tmp_path)
    bodies = record_counts(host)
    result = cli.dispatch(context_argv(tmp_path, dirs, "--gold-dir", str(dirs.gold)), host_env(host), guarded=True)
    assert result.exit_code == 0 and whitelisted(result)
    inputs = freeze(dirs.gold)
    repair = inputs.prompts.repair
    expected = [
        body
        for frozen_request in inputs.all_requests()
        for request in (frozen_request.request, frozen_request.unconstrained)
        for body in (
            request.body_bytes(),
            repair_request(request, invalid_output="", error_text=CUT_OFF_TEXT, template=repair).body_bytes(),
        )
    ]
    assert bodies == expected * 3
    assert len(groups(result.fields, "check", CHECK_KEYS)) == 4 * len(inputs.all_requests()) * 3
    assert ["response_format" in json.loads(body) for body in bodies[:4]] == [True, True, False, False]


def test_c_c_an_unconstrained_only_misfit_exits_3(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    record_counts(host, unconstrained={"syn_2": 11_000})
    result = cli.dispatch(context_argv(tmp_path, dirs, "--gold-dir", str(dirs.gold)), host_env(host), guarded=True)
    assert result.exit_code == 3
    assert whitelisted(result)
    checks = groups(result.fields, "check", CHECK_KEYS)
    misfits = [(index % 4, check["check"]) for index, check in enumerate(checks) if check["fits"] == "false"]
    assert misfits == [(2, "syn_2"), (3, "syn_2")] * 3
    syn_2_counts = [check["input_tokens"] for check in checks if check["check"] == "syn_2"]
    assert syn_2_counts == ["100", "100", "11000", "11000"] * 3
    assert {value for key, value in result.fields if key == "check_label"} == {"request", "repair_bound"}
    lines = wrapper.render(result)
    assert len(lines) == 1 + len(result.fields) and not [line for line in lines if line.startswith("withheld")]


def test_c9_all_fitting_exits_0_and_the_cap_sessions_are_built_in_memory(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    result = cli.dispatch(context_argv(tmp_path, dirs, "--gold-dir", str(dirs.gold)), host_env(host), guarded=True)
    assert result.exit_code == 0
    assert {check["check"] for check in groups(result.fields, "check", CHECK_KEYS)} >= {"cap_1", "cap_4"}
    assert any(path.endswith("INPUTS.sha256") for path in host.files.reads)
    assert not [path for path in host.files.reads if path.endswith(("EXPECTED-FREEZE.json", "cap-sessions.json"))]


def test_c9_a_cap_sessions_file_is_read_and_checked_when_given(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    cap = tmp_path / "given-cap-sessions.json"
    cap.write_bytes(frozen.cap_sessions_bytes("v1.0", CAP_SESSIONS).replace(b"syn_1", b"syn_5", 1))
    argv = context_argv(tmp_path, dirs, "--gold-dir", str(dirs.gold), "--cap-sessions", str(cap))
    assert error_of(cli.dispatch(argv, host_env(host), guarded=True)) == "FrozenInputError"
    assert host.events == []


def count_by_length(host: FakeHost) -> None:
    """Make each token count the length of the body counted, so a source's count is a difference of lengths."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == COUNT_PATH:
            return input_tokens_response(len(request.content))
        return FakeHost.handle(host, request)

    host.handle = handle  # type: ignore[method-assign]


def test_c9_sources_give_each_source_s_count_delta_per_candidate(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    count_by_length(host)
    result = cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True)
    assert result.exit_code == 0
    assert whitelisted(result)
    deltas = frozen.source_delta_requests(frozen.load_draft_inputs(str(dirs.inputs), read_file))
    expected = [
        {
            "item": delta.item_id,
            "source": delta.source_id,
            "candidate": candidate_id,
            "source_tokens": str(len(delta.full.body_bytes()) - len(delta.emptied.body_bytes())),
        }
        for candidate_id in CANDIDATE_IDS
        for delta in deltas
    ]
    assert len(expected) == 300
    assert groups(result.fields, "item", ("source", "candidate", "source_tokens")) == expected
    assert all(int(row["source_tokens"]) > 0 for row in expected)
    assert not groups(result.fields, "check", CHECK_KEYS)


def test_c9_a_launch_config_with_no_operator_section_is_accepted(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    assert "operator" not in json.loads((tmp_path / "launch.json").read_text(encoding="ascii"))
    assert cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True).exit_code == 0


def _drop(*path: Any) -> Callable[[dict[str, Any]], str]:
    def change(data: dict[str, Any]) -> str:
        target = data
        for part in path[:-1]:
            target = target[part]
        del target[path[-1]]
        return str(path[-1])

    return change


def _add(*path: Any, key: str) -> Callable[[dict[str, Any]], str]:
    def change(data: dict[str, Any]) -> str:
        target = data
        for part in path:
            target = target[part]
        target[key] = "x"
        return key

    return change


@pytest.mark.parametrize(
    "change",
    [
        _add(key="operator"),
        _add(key="machine"),
        _drop("candidates"),
        _drop("conditions"),
        _add("conditions", key="ready_deadline_sec"),
        _drop("conditions", "kv_type"),
        _drop("candidates", 1, "gguf_sha256"),
        _add("candidates", 2, key="argv"),
    ],
)
def test_c9_a_launch_config_key_set_refusal_names_the_key(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: Callable[[dict[str, Any]], str]
) -> None:
    host, dirs = context_rig(tmp_path)
    data = launch_config_data()
    key = change(data)
    write_json(tmp_path / "launch.json", data)
    result = cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True)
    assert error_of(result) == "ConfigError"
    assert key in stderr_of(capsys)
    assert host.events == []


@pytest.mark.parametrize(
    ("data", "error"),
    [
        (b"[]\n", "ConfigError"),
        (b'{"conditions": {}, "conditions": {}}\n', "StoreError"),
        (None, "ConfigError"),
        ("two", "ConfigError"),
        ("swapped", "ConfigError"),
    ],
)
def test_c9_a_launch_config_value_refusal_starts_nothing(tmp_path: Path, data: Any, error: str) -> None:
    host, dirs = context_rig(tmp_path)
    config = launch_config_data()
    if data is None:
        config["conditions"]["port"] = "18080"
        write_json(tmp_path / "launch.json", config)
    elif data == "two":
        config["candidates"] = config["candidates"][:2]
        write_json(tmp_path / "launch.json", config)
    elif data == "swapped":
        config["candidates"] = [config["candidates"][1], config["candidates"][0], config["candidates"][2]]
        write_json(tmp_path / "launch.json", config)
    else:
        (tmp_path / "launch.json").write_bytes(data)
    result = cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True)
    assert error_of(result) == error
    assert host.events == []


def test_c9_a_onedrive_or_missing_server_log_dir_is_refused_before_any_popen(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    synced = tmp_path / ("One" + "Drive") / "logs"
    env = host_env(host, realpaths={str(tmp_path / "logs"): str(synced)})
    assert error_of(cli.dispatch(context_argv(tmp_path, dirs, "--sources"), env, guarded=True)) == "StoreError"
    assert host.events == []
    host, dirs = context_rig(tmp_path / "second")
    argv = context_argv(tmp_path / "second", dirs, "--sources")
    argv[argv.index("--server-log-dir") + 1] = str(tmp_path / "second" / "absent")
    assert error_of(cli.dispatch(argv, host_env(host), guarded=True)) == "FileNotFoundError"
    assert host.events == []


def test_c9_a_server_check_error_exits_1_and_stops_the_server(tmp_path: Path) -> None:
    first = launch_name(CANDIDATE_IDS[0])
    scripts = {first: ServerScript(counts={"warmup": httpx.Response(500, content=b"{}")})}
    host, dirs = context_rig(tmp_path, scripts=scripts)
    result = cli.dispatch(context_argv(tmp_path, dirs, "--gold-dir", str(dirs.gold)), host_env(host), guarded=True)
    assert result == cli.CommandResult("context", 1, (("error", "ServerCheckError"),))
    assert host.events == [("popen", first), ("terminate", first)]
    assert host.closed == 1


def test_c10_each_candidate_gets_one_timed_launch_with_its_own_log_and_is_stopped(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    assert cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True).exit_code == 0
    names = [launch_name(candidate_id) for candidate_id in CANDIDATE_IDS]
    assert host.events == [event for name in names for event in (("popen", name), ("terminate", name))]
    assert sorted(path.name for path in (tmp_path / "logs").iterdir()) == sorted(f"{name}.log" for name in names)
    for name, candidate in zip(names, SCHEDULE_CANDIDATES, strict=True):
        args, options = host.launches[name]
        assert VERBOSITY_ENV not in options["env"]
        assert args[args.index("-m") + 1] == candidate.gguf_path
    assert host.clients == [(f"http://127.0.0.1:{CONDITIONS.port}", CONDITIONS.context)] * 3
    assert host.closed == 3


def test_c10_an_existing_log_starts_nothing(tmp_path: Path) -> None:
    host, dirs = context_rig(tmp_path)
    (tmp_path / "logs" / f"{launch_name(CANDIDATE_IDS[0])}.log").write_bytes(b"")
    result = cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True)
    assert error_of(result) == "FileExistsError"
    assert host.events == []


def test_c10_a_launch_failure_stops_the_server_and_propagates(tmp_path: Path) -> None:
    second = launch_name(CANDIDATE_IDS[1])
    host, dirs = context_rig(tmp_path, scripts={second: ServerScript(props={"build_info": "b2-other"})})
    with pytest.raises(cli.launch.LaunchCheckFailed):
        cli.dispatch(context_argv(tmp_path, dirs, "--sources"), host_env(host), guarded=True)
    first = launch_name(CANDIDATE_IDS[0])
    assert host.events == [("popen", first), ("terminate", first), ("popen", second), ("terminate", second)]


# ---------------------------------------------------------------- C-11 main


def test_c11_main_prints_only_key_value_lines_and_returns_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[Any] = []
    env = plain_env()

    def fake_dispatch(argv: Any, given: cli.CliEnv, *, guarded: bool) -> cli.CommandResult:
        seen.append((list(argv), given, guarded))
        return cli.CommandResult("freeze inputs", 3, (("file_count", "7"), ("warmup_item", "warmup")))

    monkeypatch.setattr(cli, "dispatch", fake_dispatch)
    monkeypatch.setattr(cli, "real_env", lambda: env)
    assert cli.main(["freeze", "inputs"]) == 3
    assert seen == [(["freeze", "inputs"], env, False)]
    captured = capsys.readouterr()
    assert captured.out == "file_count: 7\nwarmup_item: warmup\n"
    assert captured.err == ""


def test_c11_main_reads_sys_argv_when_given_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_dispatch(argv: Any, given: cli.CliEnv, *, guarded: bool) -> cli.CommandResult:
        seen.append(list(argv))
        return cli.CommandResult("run", 0, ())

    monkeypatch.setattr(cli, "dispatch", fake_dispatch)
    monkeypatch.setattr(cli, "real_env", plain_env)
    monkeypatch.setattr(cli.sys, "argv", ["bakeoff-cli", "freeze", "inputs"])
    assert cli.main() == 0
    assert seen == [["freeze", "inputs"]]


def test_c11_main_runs_freeze_inputs_with_the_real_environment(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    files = write_h5_inputs(tmp_path / "inputs", manifest=False)
    out = tmp_path / "INPUTS.sha256"
    assert cli.main(freeze_inputs_argv(tmp_path / "inputs", out)) == 0
    assert out.read_bytes() == inputs_manifest_bytes(files)
    lines = capsys.readouterr().out.splitlines()
    assert [line.split(": ", 1)[0] for line in lines] == [
        "prompts_sha256",
        "repair_sha256",
        "probe_sha256",
        "inputs_manifest_sha256",
        "file_count",
        "warmup_item",
    ]
    assert all(re.fullmatch(r"[a-z_0-9]+: \S+", line) for line in lines)


def test_c11_main_prints_a_refusal_as_one_error_line_and_its_message_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "run",
                "--config",
                "c",
                "--inputs-dir",
                "i",
                "--gold-dir",
                "g",
                "--cap-sessions",
                "s",
                "--expected-freeze",
                "e",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == "error: GuardRefused\n"
    assert "AMB-159" in captured.err
