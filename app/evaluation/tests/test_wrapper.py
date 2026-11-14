"""The whitelisting wrapper `wrapper`: H5 rows W-1 to W-15 (S2 §3.12; AMB-154, AMB-158, AMB-159).

The proof uses a canary, a row-like text that must never reach the console: no substring of it six characters long
may appear there. No test points the real descriptors 1 and 2 of this process at a log for longer than one call:
each call either gets its own console and error descriptors, files under `tmp_path`, or asks for the restore. A test
that checks the production route (no restore) runs on those injected descriptors and puts `sys.stdout` and
`sys.stderr` back through `monkeypatch`. Gold sets are synthetic, under `tmp_path`; servers and machine are fakes.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import json
import os
import re
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.bakeoff import cli, entry, records, wrapper
from app.evaluation.bakeoff.decision import RULES
from app.evaluation.bakeoff.gold import load_gold_set
from app.evaluation.bakeoff.inputs import FrozenInputs, freeze_inputs
from app.evaluation.bakeoff.launch import CANDIDATE_IDS, LaunchTools
from app.evaluation.bakeoff.machine import MachineTools
from app.evaluation.bakeoff.records import FAULT_KINDS
from app.evaluation.bakeoff.schemas import canonical_sha256
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._fixtures import make_conversation, make_item, write_version
from app.evaluation.tests._h4_fixtures import (
    MACHINE_CONFIG,
    FakeMachinePopen,
    FakeThreads,
    answering_scripts,
    install_machine,
    machine_tools,
    proc_status,
)
from app.evaluation.tests._h5_fixtures import H5_PROMPTS, write_h5_inputs
from app.evaluation.tests._runner_fixtures import (
    CAP_SESSIONS,
    CONDITIONS,
    PROBE_ITEMS,
    SCHEDULE_CANDIDATES,
    VALID_OPERATOR,
    WARMUP,
    FakeHost,
    block_processes,
    fixed_now,
    gold_rows,
    no_fsync,
    synthetic_gold,
)

CANARY = "The base rate rose to 5.25% on 3 May, said ZQXCANARY"
AT = datetime(2026, 11, 15, 9, 30, 1, 123456, tzinfo=timezone.utc)
STAMP = "20261115T093001123456Z"
RESAMPLES = 200


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


# ---------------------------------------------------------------- helpers


def at() -> datetime:
    return AT


def assert_no_canary(text: str | bytes) -> None:
    shown = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    for start in range(len(CANARY) - 5):
        assert CANARY[start : start + 6] not in shown, CANARY[start : start + 6]


def refuse(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("this test must not reach this tool")


def plain_env(realpaths: Mapping[str, str] | None = None) -> cli.CliEnv:
    """Files are read from disk and logs created on disk; paths resolve through `realpaths`, else to themselves."""
    table = dict(realpaths or {})
    tools = LaunchTools(
        popen=refuse,
        realpath=lambda path: table.get(path, path),
        read_chunks=refuse,
        monotonic=refuse,
        sleep=refuse,
    )
    return cli.CliEnv(
        tools=tools,
        machine_tools=MachineTools(launch=tools, list_pids=refuse, start_thread=refuse),
        now=fixed_now,
        fsync=no_fsync,
    )


@dataclasses.dataclass
class Console:
    """Two descriptors standing for this process's 1 and 2, on files under `tmp_path`, in binary mode."""

    out_path: Path
    err_path: Path
    out_fd: int
    err_fd: int

    @property
    def fds(self) -> tuple[int, int]:
        return (self.out_fd, self.err_fd)

    def out(self) -> bytes:
        return self.out_path.read_bytes()

    def err(self) -> bytes:
        return self.err_path.read_bytes()


@pytest.fixture
def console(tmp_path: Path) -> Iterator[Console]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    out_path, err_path = tmp_path / "console-out", tmp_path / "console-err"
    screen = Console(out_path, err_path, os.open(out_path, flags), os.open(err_path, flags))
    yield screen
    os.close(screen.out_fd)
    os.close(screen.err_fd)


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    path = tmp_path / "logs"
    path.mkdir()
    return path


def wrapped(argv: list[str], env: cli.CliEnv, screen: Console, logs: Path, *, restore: bool = True) -> int:
    return wrapper.run_wrapped(argv, log_dir=str(logs), env=env, stamp=at, fds=screen.fds, restore=restore)


def only_log(logs: Path) -> bytes:
    found = list(logs.iterdir())
    assert len(found) == 1
    return found[0].read_bytes()


def lines_of(screen: Console) -> list[str]:
    return screen.out().decode("ascii").split("\n")[:-1]


class FakeDispatch:
    """Stands for `cli.dispatch`: records each call, then runs `body` in place of a subcommand."""

    def __init__(self, body: Callable[[], cli.CommandResult]) -> None:
        self.body = body
        self.calls: list[tuple[list[str], cli.CliEnv, bool]] = []

    def __call__(self, argv: Any, env: cli.CliEnv, *, guarded: bool) -> cli.CommandResult:
        self.calls.append((list(argv), env, guarded))
        return self.body()


def install(monkeypatch: pytest.MonkeyPatch, body: Callable[[], cli.CommandResult]) -> FakeDispatch:
    fake = FakeDispatch(body)
    monkeypatch.setattr(cli, "dispatch", fake)
    return fake


def keep_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    """Put `sys.stdout` and `sys.stderr` back at teardown, for the calls that leave them on the log."""
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)


def freeze(gold_dir: Path) -> FrozenInputs:
    return freeze_inputs(
        str(gold_dir), prompts=H5_PROMPTS, cap_sessions=CAP_SESSIONS, probe_items=PROBE_ITEMS, warmup=WARMUP
    )


def canary_gold(root: Path, *, bad: bool = False) -> Path:
    """`gold_rows()` plus `syn_5`, whose first segment text is the canary. With `bad`, one row carries the canary as
    its claim id and as its domain, a choice value the loader refuses by echoing it."""
    conversations, items = gold_rows()
    extra = make_conversation("syn_5", [CANARY, "syn_5 line 2 is about a company."])
    if bad:
        added = [make_item(CANARY, extra, 1, "CHECKABLE", CANARY)]
    else:
        added = [
            make_item("syn_5_01", extra, 1, "CHECKABLE", "GENERAL"),
            make_item("syn_5_02", extra, 2, "CHECKABLE", "OTHER"),
        ]
    return write_version(root, [*conversations, extra], [*items, *added], name="v1.0")


@dataclasses.dataclass(frozen=True)
class Dirs:
    inputs: Path
    gold: Path
    out: Path

    def freeze_gold(self) -> list[str]:
        return [
            "freeze",
            "gold",
            "--inputs-dir",
            str(self.inputs),
            "--gold-dir",
            str(self.gold),
            "--out-dir",
            str(self.out),
        ]


def make_dirs(root: Path, gold_writer: Callable[[Path], Path] = synthetic_gold) -> Dirs:
    write_h5_inputs(root / "inputs")
    out = root / "out"
    out.mkdir()
    return Dirs(root / "inputs", gold_writer(root / "g"), out)


# ---------------------------------------------------------------- the closed sets

_ID = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}"
_COUNT = r"0|[1-9][0-9]{0,9}"
_SHA256 = r"[0-9a-f]{64}"
EXPECTED_ERRORS = frozenset(
    {
        "GuardRefused",
        "FrozenInputError",
        "InputsError",
        "GoldSetError",
        "ManifestError",
        "GoldDataError",
        "ReportError",
        "StoreError",
        "ConfigError",
        "ModelFileError",
        "SpawnFailed",
        "ServerExited",
        "ReadyTimeout",
        "LaunchCheckFailed",
        "ServerStopFailed",
        "ServerCheckError",
        "ContextBudgetExceeded",
        "OSError",
        "FileNotFoundError",
        "FileExistsError",
        "PermissionError",
        "RuntimeError",
        "KeyboardInterrupt",
        "ValueError",
        "TypeError",
    }
)
# The H5a brief's whitelist table, written out: every key and its value shape.
EXPECTED_GRAMMAR: Mapping[str, str | frozenset[str]] = {
    "command": frozenset({"freeze inputs", "freeze gold", "run", "context", "report", "void"}),
    "log": r"wrapper-[0-9]{8}T[0-9]{12}Z-[a-z]+(-[a-z]+)?\.log",
    "exit": r"0|1|2|3|130",
    "error": EXPECTED_ERRORS | {"usage", "other"},
    **dict.fromkeys(("run", "unit", "item", "source", "check", "session", "warmup_item", "fault_id"), _ID),
    **dict.fromkeys(("candidate", "pinned"), frozenset({*CANDIDATE_IDS, "none"})),
    "session_sources": rf"{_ID}(,{_ID}){{0,63}}",
    "stopped": frozenset({"no", "pre_run_stopped", "runs_exhausted"}),
    "gold_version": r"v[0-9]+\.[0-9]+",
    **dict.fromkeys(
        (
            "prompts_sha256",
            "repair_sha256",
            "probe_sha256",
            "cap_sessions_sha256",
            "cap_requests_sha256",
            "warmup_sha256",
            "inputs_manifest_sha256",
            "expected_freeze_sha256",
            "gold_manifest_sha256",
        ),
        _SHA256,
    ),
    **dict.fromkeys(
        (
            "file_count",
            "request_count",
            "session_rows",
            "overlap_equal",
            "overlap_contained",
            "delimiter_hits",
            "input_tokens",
            "uses",
            "limit",
            "source_tokens",
            "withheld",
            "unparsed_lines",
        ),
        _COUNT,
    ),
    "fits": frozenset({"true", "false"}),
    "check_label": frozenset({"request", "repair_bound"}),
    "fraction": r"-?(0|[1-9][0-9]{0,17})(/[1-9][0-9]{0,17})?",
    "fault": frozenset(FAULT_KINDS),
    "rule": frozenset({*RULES, "none"}),
    "end": frozenset({"complete", "pre_run_stopped", "runs_exhausted", "incomplete"}),
    "report_file": r"report-[1-9][0-9]{0,5}\.(json|md)",
}


def test_w9_the_field_grammar_is_exactly_the_whitelist_table() -> None:
    assert type(wrapper.FIELD_GRAMMAR).__name__ == "mappingproxy"
    assert set(wrapper.FIELD_GRAMMAR) == set(EXPECTED_GRAMMAR)
    assert len(wrapper.FIELD_GRAMMAR) == 45
    for key, expected in EXPECTED_GRAMMAR.items():
        grammar = wrapper.FIELD_GRAMMAR[key]
        if isinstance(expected, frozenset):
            assert grammar == expected, key
        else:
            assert isinstance(grammar, re.Pattern) and grammar.pattern == expected, key


def test_the_error_names_and_closed_sets_come_from_their_modules() -> None:
    assert wrapper.ERROR_NAMES == EXPECTED_ERRORS
    assert wrapper.FIELD_GRAMMAR["rule"] == cli.RULE_VALUES
    assert wrapper.FIELD_GRAMMAR["end"] == cli.END_VALUES
    assert wrapper.FIELD_GRAMMAR["fault"] == frozenset(records.FAULT_KINDS)
    assert {"PreRunStopped", "RunsExhausted"}.isdisjoint(wrapper.ERROR_NAMES)


@pytest.mark.parametrize(
    ("key", "good", "bad"),
    [
        ("run", ["dryrun-1", "a" * 64, "qwen3-4b.run1.a1", "x:y"], ["a run", "a" * 65, "-x", "", "x/y", "dryrun-1\n"]),
        ("prompts_sha256", ["0" * 64], ["0" * 63, "A" * 64, "0" * 65]),
        ("request_count", ["0", "29", "9" * 10], ["-1", "01", "1" * 11, "1.0"]),
        (
            "session_sources",
            ["syn_1", ",".join(f"s{n}" for n in range(64))],
            [",".join(f"s{n}" for n in range(65)), "a,"],
        ),
        ("fraction", ["0", "-3/4", "12"], ["1/0", "--1", "0/1x", "1.5"]),
        (
            "log",
            [f"wrapper-{STAMP}-freeze-gold.log", f"wrapper-{STAMP}-run.log"],
            [f"wrapper-{STAMP}-a-b-c.log", "x.log"],
        ),
        ("gold_version", ["v1.0", "v12.34"], ["1.0", "v1", "v1.0/x"]),
        ("report_file", ["report-1.json", "report-999999.md"], ["report-0.json", "report-1.txt", "x/report-1.json"]),
        ("exit", ["0", "1", "2", "3", "130"], ["4", "-1", "13"]),
        ("rule", ["rule 2", "none"], ["rule 9", "none (records-only report)"]),
        ("pinned", ["Qwen/Qwen3-8B", "none"], ["qwen3-8b"]),
    ],
)
def test_w9_each_grammar_takes_its_shape_only(key: str, good: list[str], bad: list[str]) -> None:
    for value in good:
        assert wrapper.render(cli.CommandResult("run", 0, ((key, value),))) == ("command: run", f"{key}: {value}")
    for value in bad:
        assert wrapper.render(cli.CommandResult("run", 0, ((key, value),))) == ("command: run", "withheld: 1"), value


# ---------------------------------------------------------------- W-9 and W-10 render


def test_w9_a_value_that_fails_its_grammar_is_withheld_and_counted() -> None:
    result = cli.CommandResult(
        "run",
        0,
        (
            ("run", "a run"),
            ("prompts_sha256", "a" * 63),
            ("request_count", "-1"),
            ("path", "/w/x"),
            ("rule", "rule 9"),
            ("stopped", "no"),
        ),
    )
    assert wrapper.render(result) == ("command: run", "stopped: no", "withheld: 5")


def test_render_shows_valid_fields_in_order_with_no_withheld_line() -> None:
    result = cli.CommandResult("freeze inputs", 0, (("file_count", "7"), ("warmup_item", "warmup")))
    assert wrapper.render(result) == ("command: freeze inputs", "file_count: 7", "warmup_item: warmup")


def test_render_withholds_an_unknown_command_and_non_string_values() -> None:
    result = cli.CommandResult(CANARY, 0, (("run", 7), (CANARY, "x")))  # type: ignore[arg-type]
    rendered = wrapper.render(result)
    assert rendered == ("withheld: 3",)
    assert_no_canary("\n".join(rendered))


def test_w10_an_unknown_error_name_is_withheld_and_a_listed_one_shown() -> None:
    assert wrapper.render(cli.CommandResult("run", 1, (("error", "GoldDataError"),))) == (
        "command: run",
        "error: GoldDataError",
    )
    assert wrapper.render(cli.CommandResult("run", 1, (("error", "SecretError"),))) == ("command: run", "withheld: 1")


# ---------------------------------------------------------------- W-8 entry lines


def report_line(*paths: str, rule: str = "rule 2", pinned: str = "Qwen/Qwen3-4B", end: str = "complete") -> str:
    return f"end: {end}; rule: {rule}; pinned: {pinned}; reports: {', '.join(paths)}\n"


def test_w8_the_report_line_gives_its_fields_and_report_basenames_only() -> None:
    line = report_line(f"/w/{CANARY}/reports/report-1.json")
    fields = wrapper.entry_fields("report", line)
    assert fields == (
        ("end", "complete"),
        ("rule", "rule 2"),
        ("pinned", "Qwen/Qwen3-4B"),
        ("report_file", "report-1.json"),
    )


def test_w8_the_records_only_rule_reads_none_and_both_report_files_are_named() -> None:
    line = report_line("/w/r/reports/report-3.md", "/w/r/reports/report-3.json", rule="none (records-only report)")
    fields = wrapper.entry_fields("report", line.replace("pinned: Qwen/Qwen3-4B", "pinned: none"))
    assert fields == (
        ("end", "complete"),
        ("rule", "none"),
        ("pinned", "none"),
        ("report_file", "report-3.md"),
        ("report_file", "report-3.json"),
    )
    windows = wrapper.entry_fields("report", report_line(chr(92).join(("C:", "w", "reports", "report-2.json"))))
    assert windows[-1] == ("report_file", "report-2.json")


@pytest.mark.parametrize(
    "line",
    [
        "something else\n",
        report_line("/w/report-1.json", rule="rule 9"),
        report_line("/w/report-1.json", pinned="Qwen/Other"),
        report_line("/w/report-1.json", end="finished"),
        report_line("/w/report-1.json", f"/w/{CANARY}.txt"),
        report_line(""),
        "x" + report_line("/w/report-1.json"),
        f"{CANARY}\n",
        "void: qwen3-4b.run1.a1 with operator fault 3\n",
    ],
)
def test_w8_a_line_of_any_other_shape_is_counted_never_shown(line: str) -> None:
    assert wrapper.entry_fields("report", line) == (("unparsed_lines", "1"),)


def test_w8_the_void_line_gives_unit_and_fault_and_the_warning_is_unparsed() -> None:
    captured = (
        "void: qwen3-4b.run1.a1 with operator fault 3\n"
        "warning: a counted unit that is now void leaves the schedule incomplete, with no resume (AMB-72); "
        "the next report is records-only\n"
    )
    assert wrapper.entry_fields("void", captured) == (
        ("unit", "qwen3-4b.run1.a1"),
        ("fault_id", "3"),
        ("unparsed_lines", "1"),
    )
    assert wrapper.entry_fields("void", report_line("/w/report-1.json")) == (("unparsed_lines", "1"),)


def test_w8_no_output_gives_no_field() -> None:
    assert wrapper.entry_fields("report", "") == ()
    assert len(wrapper.ENTRY_LINES) == 2


def test_w8_through_the_wrapper_a_canary_path_prints_only_the_report_file(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    def body() -> cli.CommandResult:
        sys.stdout.write(report_line(f"/w/{CANARY}/reports/report-1.json"))
        sys.stdout.write("an unexpected line\n")
        sys.stderr.write(report_line("/w/reports/report-7.json"))
        return cli.CommandResult("report", 0, ())

    install(monkeypatch, body)
    code = wrapped(["report", "--run-dir", "runs/dryrun-1", "--gold-dir", "g"], plain_env(), console, log_dir)
    assert code == 0
    assert lines_of(console) == [
        f"log: wrapper-{STAMP}-report.log",
        "command: report",
        "end: complete",
        "rule: rule 2",
        "pinned: Qwen/Qwen3-4B",
        "report_file: report-1.json",
        "unparsed_lines: 1",
        "exit: 0",
    ]
    assert_no_canary(console.out())
    assert CANARY.encode("ascii") in only_log(log_dir)


def test_w8_run_output_is_never_parsed(monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path) -> None:
    def body() -> cli.CommandResult:
        sys.stdout.write(f"/w/{CANARY}/runs/dryrun-1\n")
        return cli.CommandResult("run", 0, (("run", "dryrun-1"), ("stopped", "no")))

    install(monkeypatch, body)
    assert wrapped(["run"], plain_env(), console, log_dir) == 0
    assert lines_of(console) == [
        f"log: wrapper-{STAMP}-run.log",
        "command: run",
        "run: dryrun-1",
        "stopped: no",
        "exit: 0",
    ]
    assert CANARY.encode("ascii") in only_log(log_dir)


# ---------------------------------------------------------------- W-1 to W-7: the canary never reaches the console


def test_w1_a_gold_error_echoing_the_canary_shows_only_its_class(
    tmp_path: Path, console: Console, log_dir: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    dirs = make_dirs(tmp_path, functools.partial(canary_gold, bad=True))
    assert wrapped(dirs.freeze_gold(), plain_env(), console, log_dir) == 1
    assert console.out() == (
        f"log: wrapper-{STAMP}-freeze-gold.log\ncommand: freeze gold\nerror: GoldDataError\nexit: 1\n".encode("ascii")
    )
    assert console.err() == b""
    assert_no_canary(console.out())
    captured = capfd.readouterr()
    assert_no_canary(captured.out)
    assert_no_canary(captured.err)
    log = only_log(log_dir)
    assert CANARY.encode("ascii") in log and b"GoldDataError" in log
    assert list(dirs.out.iterdir()) == []


def entry_config(store_parent: Path, run_name: str) -> dict[str, Any]:
    return {
        "store_parent": str(store_parent),
        "run_name": run_name,
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
        "operator": dict(VALID_OPERATOR),
        "machine": {
            "nvidia_smi_path": MACHINE_CONFIG.nvidia_smi_path,
            "contention_argv": list(MACHINE_CONFIG.contention_argv),
            "contention_env": dict(MACHINE_CONFIG.contention_env),
            "contention_lead_sec": MACHINE_CONFIG.contention_lead_sec,
        },
    }


def test_w2_freeze_run_and_report_succeed_with_no_canary_on_the_console(
    tmp_path: Path, console: Console, log_dir: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    dirs = make_dirs(tmp_path, canary_gold)
    inputs = freeze(dirs.gold)
    host = FakeHost(inputs, answering_scripts(inputs, load_gold_set(dirs.gold)), proc_status=proc_status())
    threads = FakeThreads()
    install_machine(host, threads)
    popen = FakeMachinePopen(host.clock)
    env = cli.CliEnv(
        tools=host.tools(),
        machine_tools=machine_tools(host, popen, threads),
        now=fixed_now,
        fsync=no_fsync,
        client_factory=host.client,
    )
    monkeypatch.setattr(entry, "score_and_report", functools.partial(entry.score_and_report, resamples=RESAMPLES))
    (tmp_path / "runs").mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(entry_config(tmp_path / "runs", "dryrun-1")) + "\n", encoding="ascii")
    expected = str(dirs.out / "EXPECTED-FREEZE.json")

    assert wrapped(dirs.freeze_gold(), env, console, log_dir) == 0
    run_argv = ["run", "--config", str(config), "--inputs-dir", str(dirs.inputs), "--gold-dir", str(dirs.gold)]
    run_argv += ["--cap-sessions", str(dirs.out / "cap-sessions.json"), "--expected-freeze", expected]
    assert wrapped(run_argv, env, console, log_dir) == 0
    report_argv = ["report", "--run-dir", str(tmp_path / "runs" / "dryrun-1"), "--gold-dir", str(dirs.gold)]
    assert wrapped([*report_argv, "--expected-freeze", expected], env, console, log_dir) == 0

    shown = lines_of(console)
    assert_no_canary(console.out())
    captured = capfd.readouterr()
    assert_no_canary(captured.out + captured.err)
    assert [line for line in shown if line.startswith("command: ")] == [
        "command: freeze gold",
        "command: run",
        "command: report",
    ]
    assert "run: dryrun-1" in shown and "stopped: no" in shown and "end: complete" in shown
    rules = [line.split(": ", 1)[1] for line in shown if line.startswith("rule: ")]
    assert len(rules) == 1 and rules[0] in RULES
    assert [line for line in shown if line.startswith("report_file: ")] == [
        "report_file: report-1.md",
        "report_file: report-1.json",
    ]
    assert not any(line.startswith(("withheld: ", "unparsed_lines: ", "error: ")) for line in shown)
    assert sorted(path.name for path in log_dir.iterdir()) == [
        f"wrapper-{STAMP}-freeze-gold.log",
        f"wrapper-{STAMP}-report.log",
        f"wrapper-{STAMP}-run.log",
    ]


class CanaryError(Exception):
    """A class name the whitelist does not know."""


@pytest.mark.parametrize(("raised", "name"), [(RuntimeError(CANARY), "RuntimeError"), (CanaryError(CANARY), "other")])
def test_w3_w10_an_exception_shows_its_listed_class_name_or_other(
    raised: Exception, name: str, monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    def body() -> cli.CommandResult:
        raise raised

    install(monkeypatch, body)
    assert wrapped(["run", "--config", CANARY], plain_env(), console, log_dir) == 1
    assert lines_of(console) == [f"log: wrapper-{STAMP}-run.log", "command: run", f"error: {name}", "exit: 1"]
    assert_no_canary(console.out() + console.err())
    log = only_log(log_dir)
    assert b"Traceback" in log and CANARY.encode("ascii") in log and type(raised).__name__.encode("ascii") in log


def test_w4_thread_output_during_dispatch_reaches_the_log_only(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    monkeypatch.setattr(threading, "excepthook", threading.__excepthook__)

    def write() -> None:
        sys.stderr.write(f"{CANARY} from a thread\n")

    def fail() -> None:
        raise RuntimeError(f"{CANARY} raised in a thread")

    def body() -> cli.CommandResult:
        for target in (write, fail):
            thread = threading.Thread(target=target)
            thread.start()
            thread.join()
        return cli.CommandResult("run", 0, (("run", "dryrun-1"),))

    install(monkeypatch, body)
    assert wrapped(["run"], plain_env(), console, log_dir) == 0
    assert_no_canary(console.out() + console.err())
    log = only_log(log_dir)
    assert b"from a thread" in log and b"raised in a thread" in log


def test_w5_descriptor_writes_during_dispatch_reach_the_log_only(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    def body() -> cli.CommandResult:
        os.write(console.out_fd, f"{CANARY} on one\n".encode("ascii"))
        os.write(console.err_fd, f"{CANARY} on two\n".encode("ascii"))
        return cli.CommandResult("run", 0, (("run", "dryrun-1"),))

    install(monkeypatch, body)
    assert wrapped(["run"], plain_env(), console, log_dir) == 0
    assert lines_of(console) == [f"log: wrapper-{STAMP}-run.log", "command: run", "run: dryrun-1", "exit: 0"]
    assert console.err() == b""
    log = only_log(log_dir)
    assert b"on one" in log and b"on two" in log


def test_w5_on_the_real_descriptors_with_the_restore_writes_reach_the_log_only(
    monkeypatch: pytest.MonkeyPatch, log_dir: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    def body() -> cli.CommandResult:
        os.write(1, f"{CANARY} on one\n".encode("ascii"))
        os.write(2, f"{CANARY} on two\n".encode("ascii"))
        sys.stdout.write(f"{CANARY} on stdout\n")
        sys.stderr.write(f"{CANARY} on stderr\n")
        return cli.CommandResult("run", 0, (("run", "dryrun-1"),))

    install(monkeypatch, body)
    streams = (sys.stdout, sys.stderr)
    code = wrapper.run_wrapped(["run"], log_dir=str(log_dir), env=plain_env(), stamp=at, restore=True)
    assert code == 0
    assert (sys.stdout, sys.stderr) == streams
    captured = capfd.readouterr()
    assert captured.out.splitlines() == [f"log: wrapper-{STAMP}-run.log", "command: run", "run: dryrun-1", "exit: 0"]
    assert captured.err == ""
    log = only_log(log_dir)
    for where in (b"on one", b"on two", b"on stdout", b"on stderr"):
        assert where in log


def test_w6_a_usage_error_with_the_canary_in_a_value_shows_usage_and_exit_2(
    console: Console, log_dir: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    argv = ["freeze", "inputs", "--inputs-dir", "a", "--out", "b", CANARY]
    assert wrapped(argv, plain_env(), console, log_dir) == 2
    assert lines_of(console) == [
        f"log: wrapper-{STAMP}-freeze-inputs.log",
        "command: freeze inputs",
        "error: usage",
        "exit: 2",
    ]
    captured = capfd.readouterr()
    assert_no_canary(console.out() + captured.out.encode() + captured.err.encode())
    assert CANARY.encode("ascii") in only_log(log_dir)


@pytest.mark.parametrize(("code", "shown"), [(0, ["exit: 0"]), (None, ["exit: 0"]), (2, ["error: usage", "exit: 2"])])
def test_a_system_exit_gives_its_code(
    code: int | None, shown: list[str], monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    def body() -> cli.CommandResult:
        raise SystemExit(code)

    install(monkeypatch, body)
    assert wrapped(["context"], plain_env(), console, log_dir) == (code or 0)
    assert lines_of(console) == [f"log: wrapper-{STAMP}-context.log", "command: context", *shown]


def test_w7_a_keyboard_interrupt_gives_exit_130(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    def body() -> cli.CommandResult:
        raise KeyboardInterrupt(CANARY)

    install(monkeypatch, body)
    assert wrapped(["run"], plain_env(), console, log_dir) == 130
    assert lines_of(console) == [
        f"log: wrapper-{STAMP}-run.log",
        "command: run",
        "error: KeyboardInterrupt",
        "exit: 130",
    ]
    assert_no_canary(console.out())


def test_an_unknown_command_is_withheld_and_named_other_in_the_log_name(console: Console, log_dir: Path) -> None:
    assert wrapped([CANARY], plain_env(), console, log_dir) == 2
    assert lines_of(console) == [f"log: wrapper-{STAMP}-other.log", "error: usage", "withheld: 1", "exit: 2"]
    assert_no_canary(console.out())


def test_the_wrapper_dispatches_guarded_with_a_copy_of_argv(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    fake = install(monkeypatch, lambda: cli.CommandResult("freeze inputs", 0, (("file_count", "7"),)))
    env = plain_env()
    argv = ["freeze", "inputs", "--inputs-dir", "a", "--out", "b"]
    assert wrapped(argv, env, console, log_dir) == 0
    assert fake.calls == [(argv, env, True)]
    assert fake.calls[0][0] is not argv


# ---------------------------------------------------------------- W-11 log placement


def test_w11_the_log_name_matches_its_grammar_and_is_created_exclusively(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    opened: list[str] = []
    real = LaunchTools().open_log

    def open_log(path: str) -> Any:
        opened.append(path)
        return real(path)

    env = plain_env()
    env = dataclasses.replace(env, tools=dataclasses.replace(env.tools, open_log=open_log))
    install(monkeypatch, lambda: cli.CommandResult("run", 0, ()))
    assert wrapped(["run"], env, console, log_dir) == 0
    name = f"wrapper-{STAMP}-run.log"
    assert wrapper.FIELD_GRAMMAR["log"].fullmatch(name)  # type: ignore[union-attr]
    assert [Path(path) for path in opened] == [log_dir / name]


def test_w11_an_existing_log_is_refused_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    fake = install(monkeypatch, lambda: cli.CommandResult("run", 0, ()))
    (log_dir / f"wrapper-{STAMP}-run.log").write_bytes(b"kept")
    assert wrapped(["run"], plain_env(), console, log_dir) == 1
    assert console.out() == b"error: FileExistsError\nexit: 1\n"
    assert fake.calls == []
    assert only_log(log_dir) == b"kept"


def test_w11_a_onedrive_log_dir_is_refused_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    fake = install(monkeypatch, lambda: cli.CommandResult("run", 0, ()))
    env = plain_env({str(log_dir): "/data/OneDrive/wrapper-logs"})
    assert wrapped(["run"], env, console, log_dir) == 1
    assert console.out() == b"error: StoreError\nexit: 1\n"
    assert fake.calls == [] and list(log_dir.iterdir()) == []


def test_w11_a_missing_log_dir_is_refused_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, console: Console, tmp_path: Path
) -> None:
    fake = install(monkeypatch, lambda: cli.CommandResult("run", 0, ()))
    assert wrapped(["run"], plain_env(), console, tmp_path / "absent") == 1
    assert console.out() == b"error: FileNotFoundError\nexit: 1\n"
    assert fake.calls == []


# ---------------------------------------------------------------- W-12 a real report needs the recorded freeze


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "--run-dir", "runs/run_1", "--gold-dir", "g"],
        ["report", "--run-dir", "runs/xdryrun-1", "--gold-dir", "g"],
        ["report", "--run-dir", "runs/dryrun-1", "--run", "runs/run_1", "--gold-dir", "g"],
        ["report", "--run-dir=runs/dryrun-1", "--run-d=runs/run_1", "--gold-dir", "g"],
    ],
)
def test_w12_a_real_report_without_the_expected_freeze_is_refused_before_dispatch(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    fake = install(monkeypatch, lambda: cli.CommandResult("report", 0, ()))
    assert wrapped(argv, plain_env(), console, log_dir) == 1
    assert lines_of(console) == [
        f"log: wrapper-{STAMP}-report.log",
        "command: report",
        "error: GuardRefused",
        "exit: 1",
    ]
    assert fake.calls == []
    assert b"AMB-154" in only_log(log_dir)


@pytest.mark.parametrize(
    ("argv", "realpaths"),
    [
        (["report", "--run-dir", "runs/dryrun-1", "--gold-dir", "g"], {}),
        (["report", "--run-dir", "link", "--gold-dir", "g"], {"link": "/w/runs/dryrun-2"}),
        (["report", "--run-dir", "runs/run_1", "--gold-dir", "g", "--expected-freeze", "e.json"], {}),
        (["report", "--run-dir", "runs/run_1", "--gold-dir", "g", "--expected=e.json"], {}),
        (["report", "--run-dir", "runs/run_1", "--gold-dir", "g", "--expected", "e.json"], {}),
        (["void", "--run-dir", "runs/run_1", "--unit", "u", "--detail", "d"], {}),
    ],
)
def test_w12_a_dry_report_a_report_with_the_freeze_and_void_reach_dispatch(
    argv: list[str], realpaths: dict[str, str], monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    fake = install(monkeypatch, lambda: cli.CommandResult(argv[0], 0, ()))
    assert wrapped(argv, plain_env(realpaths), console, log_dir) == 0
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["report", "--gold-dir", "g"],
        ["report", "--run-dir", "runs/dryrun-1", "--gold-dir", "g", "--shared-memory", "x=y"],
    ],
)
def test_w12_a_report_argv_entry_refuses_is_a_usage_error_before_dispatch(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    """The report guard reads argv with entry's own parser, so what entry would refuse stops before dispatch."""
    fake = install(monkeypatch, lambda: cli.CommandResult("report", 0, ()))
    assert wrapped(argv, plain_env(), console, log_dir) == 2
    assert lines_of(console) == [f"log: wrapper-{STAMP}-report.log", "command: report", "error: usage", "exit: 2"]
    assert fake.calls == []


# ---------------------------------------------------------------- W-13 the restore


class Dups:
    """Records every descriptor `os.dup` hands out."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.fds: list[int] = []
        real = os.dup

        def dup(fd: int) -> int:
            copy = real(fd)
            self.fds.append(copy)
            return copy

        monkeypatch.setattr(os, "dup", dup)


def is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return True
    return False


def body_of(kind: str) -> Callable[[], cli.CommandResult]:
    def body() -> cli.CommandResult:
        if kind == "exception":
            raise RuntimeError(CANARY)
        if kind == "exit":
            raise SystemExit(2)
        return cli.CommandResult("run", 0, ())

    return body


@pytest.mark.parametrize("kind", ["success", "exception", "exit"])
def test_w13_with_the_restore_descriptors_and_streams_come_back_and_copies_close(
    kind: str, monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    install(monkeypatch, body_of(kind))
    dups = Dups(monkeypatch)
    streams = (sys.stdout, sys.stderr)
    wrapped(["run"], plain_env(), console, log_dir, restore=True)
    assert (sys.stdout, sys.stderr) == streams
    assert len(dups.fds) == 2 and all(is_closed(fd) for fd in dups.fds)
    before = only_log(log_dir)
    os.write(console.out_fd, b"after on one\n")
    os.write(console.err_fd, b"after on two\n")
    assert console.out().endswith(b"after on one\n")
    assert console.err() == b"after on two\n"
    assert only_log(log_dir) == before


def test_w13_without_the_restore_both_descriptors_stay_on_the_log(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    keep_streams(monkeypatch)
    streams = (sys.stdout, sys.stderr)
    install(monkeypatch, body_of("success"))
    dups = Dups(monkeypatch)
    assert wrapped(["run"], plain_env(), console, log_dir, restore=False) == 0
    assert sys.stdout is not streams[0] and sys.stderr is not streams[1]
    assert len(dups.fds) == 1 and is_closed(dups.fds[0])
    os.write(console.out_fd, b"late on one\n")
    os.write(console.err_fd, b"late on two\n")
    sys.stdout.write("late on stdout\n")
    sys.stderr.write("late on stderr\n")
    log = only_log(log_dir)
    for late in (b"late on one", b"late on two", b"late on stdout", b"late on stderr"):
        assert late in log
    assert console.out() == f"log: wrapper-{STAMP}-run.log\ncommand: run\nexit: 0\n".encode("ascii")
    assert console.err() == b""


# ---------------------------------------------------------------- W-14 the golden console


def test_w14_the_console_of_one_freeze_gold_is_byte_exact(tmp_path: Path, console: Console, log_dir: Path) -> None:
    dirs = make_dirs(tmp_path)
    assert wrapped(dirs.freeze_gold(), plain_env(), console, log_dir) == 0
    inputs = freeze(dirs.gold)
    hashes = inputs.hashes
    expected_sha = hashlib.sha256((dirs.out / "EXPECTED-FREEZE.json").read_bytes()).hexdigest()
    sessions = "".join(
        f"session: {spec.session_id}\nsession_rows: 20\nsession_sources: {','.join(spec.source_ids)}\n"
        for spec in CAP_SESSIONS
    )
    assert console.out().decode("ascii") == (
        f"log: wrapper-{STAMP}-freeze-gold.log\n"
        "command: freeze gold\n"
        "gold_version: v1.0\n"
        f"gold_manifest_sha256: {canonical_sha256(dict(inputs.gold_manifest))}\n"
        f"prompts_sha256: {hashes.prompts_sha256}\n"
        f"repair_sha256: {hashes.repair_sha256}\n"
        f"probe_sha256: {hashes.probe_sha256}\n"
        f"cap_sessions_sha256: {hashes.cap_sessions_sha256}\n"
        f"cap_requests_sha256: {hashes.cap_requests_sha256}\n"
        f"warmup_sha256: {hashes.warmup_sha256}\n"
        f"request_count: {len(inputs.all_requests())}\n"
        f"{sessions}"
        "overlap_equal: 0\n"
        "overlap_contained: 0\n"
        "delimiter_hits: 0\n"
        f"expected_freeze_sha256: {expected_sha}\n"
        "exit: 0\n"
    )
    assert console.err() == b""


# ---------------------------------------------------------------- W-15 after dispatch


class Unraisable:
    def __del__(self) -> None:
        raise RuntimeError(f"{CANARY} at collection")


def test_w15_output_after_dispatch_returns_reaches_the_log_only(
    monkeypatch: pytest.MonkeyPatch, console: Console, log_dir: Path
) -> None:
    keep_streams(monkeypatch)
    monkeypatch.setattr(sys, "unraisablehook", sys.__unraisablehook__)
    go = threading.Event()
    held: list[object] = []
    late: list[threading.Thread] = []

    def write_late() -> None:
        go.wait(10)
        sys.stderr.write(f"{CANARY} after dispatch\n")

    def body() -> cli.CommandResult:
        thread = threading.Thread(target=write_late)
        thread.start()
        late.append(thread)
        held.append(Unraisable())
        return cli.CommandResult("run", 0, ())

    install(monkeypatch, body)
    assert wrapped(["run"], plain_env(), console, log_dir, restore=False) == 0
    go.set()
    late[0].join(10)
    held.clear()
    assert_no_canary(console.out() + console.err())
    log = only_log(log_dir)
    assert b"after dispatch" in log and b"at collection" in log


# ---------------------------------------------------------------- main


def test_main_hands_the_cli_argv_and_log_dir_to_run_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []
    env = plain_env()

    def fake_run(argv: Any, **options: Any) -> int:
        seen.append((list(argv), options))
        return 3

    monkeypatch.setattr(wrapper, "run_wrapped", fake_run)
    monkeypatch.setattr(cli, "real_env", lambda: env)
    assert wrapper.main(["--log-dir", "/data/logs", "--", "freeze", "inputs", "--out", "--"]) == 3
    assert len(seen) == 1
    argv, options = seen[0]
    assert argv == ["freeze", "inputs", "--out", "--"]
    assert options["log_dir"] == "/data/logs" and options["env"] is env
    assert set(options) == {"log_dir", "env", "stamp"}
    stamp = options["stamp"]()
    assert stamp.utcoffset() is not None and stamp.utcoffset().total_seconds() == 0


def test_main_reads_sys_argv_when_given_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_run(argv: Any, **options: Any) -> int:
        seen.append(list(argv))
        return 0

    monkeypatch.setattr(wrapper, "run_wrapped", fake_run)
    monkeypatch.setattr(cli, "real_env", plain_env)
    monkeypatch.setattr(sys, "argv", ["wrapper", "--log-dir", "d", "--", "run"])
    assert wrapper.main() == 0
    assert seen == [["run"]]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--log-dir"],
        ["--log-dir", "d"],
        ["--log-dir", "d", "run"],
        ["--logdir", "d", "--", "run"],
        ["--log-dir=d", "--", "run"],
    ],
)
def test_main_refuses_any_other_shape_with_usage_and_exit_2(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(wrapper, "run_wrapped", refuse)
    monkeypatch.setattr(cli, "real_env", refuse)
    assert wrapper.main(argv) == 2
    captured = capfd.readouterr()
    assert captured.out == "error: usage\nexit: 2\n"
    assert captured.err == ""
