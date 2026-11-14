"""The whitelisting wrapper: every gold-touching CLI step runs through here, with all of its output in a host-only log
and only checked fields on the console.

Implements, from the signed D6 criteria note S2:

- S2 §3.12, data: gold stays on the host. `run_wrapped` creates a new log outside OneDrive through the launch tools,
  points file descriptors 1 and 2 at it and replaces `sys.stdout` and `sys.stderr` with a tee into it, so output from
  Python, from C code, from threads and from child processes reaches the log alone (AMB-158). It then calls
  `cli.dispatch` as its only guarded caller (AMB-159). The console, a saved copy of descriptor 1, gets only
  `key: value` lines whose key is in `FIELD_GRAMMAR` and whose value matches that key's grammar: ids, counts, hashes,
  Fractions, fault kinds and closed sets, never a path, a message or a row. Any other field is withheld and counted
  (AMB-154). An exception shows only as its class name when `ERROR_NAMES` lists it, and as `other` otherwise; its
  message and arguments go to the log. The stdout of `report` and `void` is matched line by line, in full, against
  `ENTRY_LINES`; a line of any other shape is counted, never shown. A report on a run whose directory name does not
  start with the dry-run prefix needs `--expected-freeze`, and is refused before dispatch without it (AMB-154).

The descriptors stay on the log until the process exits, so late output (a thread still running after an exception,
an unraisable exception, interpreter shutdown) goes there too; only tests ask for the restore. Output written before
the redirect (interpreter start-up, an import error here, a log directory that cannot be resolved) is not covered; no
gold has been read at that point.
"""

from __future__ import annotations

import io
import os
import re
import sys
import traceback
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime, timezone
from types import MappingProxyType
from typing import BinaryIO, TextIO, cast

from . import cli, launch, records

_STAMP = "%Y%m%dT%H%M%S%fZ"
_COMMANDS = ("freeze inputs", "freeze gold", "run", "context", "report", "void")
_ENTRY_COMMANDS = ("report", "void")
_NO_RULE = "none (records-only report)"  # entry's text for a records-only report; shown as `none`
_ID = r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}"
_COUNT = r"0|[1-9][0-9]{0,9}"
_SHA256 = r"[0-9a-f]{64}"

ERROR_NAMES: frozenset[str] = frozenset(
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
# The exact whitelist (AMB-154): a key not listed here, or a value that fails its key's grammar, is withheld.
FIELD_GRAMMAR: Mapping[str, re.Pattern[str] | frozenset[str]] = MappingProxyType(
    {
        "command": frozenset(_COMMANDS),
        "log": re.compile(r"wrapper-[0-9]{8}T[0-9]{12}Z-[a-z]+(-[a-z]+)?\.log"),
        "exit": re.compile(r"0|1|2|3|130"),
        "error": ERROR_NAMES | {"usage", "other"},
        **dict.fromkeys(
            ("run", "unit", "item", "source", "check", "session", "warmup_item", "fault_id"), re.compile(_ID)
        ),
        **dict.fromkeys(("candidate", "pinned"), frozenset({*launch.CANDIDATE_IDS, "none"})),
        "session_sources": re.compile(rf"{_ID}(,{_ID}){{0,63}}"),
        "stopped": frozenset({"no", "pre_run_stopped", "runs_exhausted"}),
        "gold_version": re.compile(r"v[0-9]+\.[0-9]+"),
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
            re.compile(_SHA256),
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
            re.compile(_COUNT),
        ),
        "fits": frozenset({"true", "false"}),
        "check_label": frozenset({"request", "repair_bound"}),
        "fraction": re.compile(r"-?(0|[1-9][0-9]{0,17})(/[1-9][0-9]{0,17})?"),
        "fault": frozenset(records.FAULT_KINDS),
        "rule": cli.RULE_VALUES,
        "end": cli.END_VALUES,
        "report_file": re.compile(r"report-[1-9][0-9]{0,5}\.(json|md)"),
    }
)


def _one_of(values: Iterable[str]) -> str:
    return "|".join(re.escape(value) for value in sorted(values))


# A report file's base name at the end of one path in entry's ", "-joined list. A path may itself hold ", ", so the
# base names are found, not split out; a run directory can never add a value outside the `report_file` grammar.
_REPORT_FILE = re.compile(r"(?:^|[/\\])(report-[1-9][0-9]{0,5}\.(?:json|md))(?=, |$)")
# Full-line grammars of the stdout lines `entry` writes for `report` and for `void`, in that order.
ENTRY_LINES: tuple[re.Pattern[str], ...] = (
    re.compile(
        f"end: (?P<end>{_one_of(cli.END_VALUES)}); "
        f"rule: (?P<rule>{_one_of((*(cli.RULE_VALUES - {'none'}), _NO_RULE))}); "
        f"pinned: (?P<pinned>{_one_of((*launch.CANDIDATE_IDS, 'none'))}); "
        r"reports: (?P<reports>(?:.*[/\\])?report-[1-9][0-9]{0,5}\.(?:json|md))"
    ),
    re.compile(r"void: (?P<unit>[^ ]+) with operator fault (?P<fault_id>[1-9][0-9]*)"),
)


class _Tee(io.TextIOBase):
    """A text stream that writes each string to the log at once, and keeps a copy of what it wrote when given one.
    `fileno` names the descriptor it stands for, which points at the same log."""

    def __init__(self, log: BinaryIO, fd: int, copy: io.StringIO | None) -> None:
        super().__init__()
        self._log = log
        self._fd = fd
        self._copy = copy

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._log.write(text.encode("utf-8", "backslashreplace"))
        self._log.flush()
        if self._copy is not None:
            self._copy.write(text)
        return len(text)

    def fileno(self) -> int:
        return self._fd


def _valid(key: object, value: object) -> bool:
    if not isinstance(key, str) or not isinstance(value, str):
        return False
    grammar = FIELD_GRAMMAR.get(key)
    if grammar is None:
        return False
    if isinstance(grammar, frozenset):
        return value in grammar
    return grammar.fullmatch(value) is not None


def _lines(pairs: Sequence[tuple[str, str]], exit_code: int | None = None) -> tuple[str, ...]:
    """The pairs that pass the whitelist, then `withheld: <n>` if any did not, then `exit: <n>` when given."""
    shown = [f"{key}: {value}" for key, value in pairs if _valid(key, value)]
    withheld = len(pairs) - len(shown)
    tail: list[str] = []
    if exit_code is not None:
        if _valid("exit", str(exit_code)):
            tail.append(f"exit: {exit_code}")
        else:
            withheld += 1
    if withheld:
        shown.append(f"withheld: {withheld}")
    return (*shown, *tail)


def render(result: cli.CommandResult) -> tuple[str, ...]:
    """The console lines of one result: `command`, then each field that passes the whitelist, then `withheld: <n>`
    if any did not (AMB-154). No value is ever shown that failed its grammar."""
    return _lines((("command", result.command), *result.fields))


def entry_fields(command: str, captured_stdout: str) -> tuple[tuple[str, str], ...]:
    """The fields of `entry`'s stdout for `report` or `void`. Each line must match its command's `ENTRY_LINES`
    grammar in full; the report list gives each report file's base name only. Any other line, the void warning
    included, is counted as `unparsed_lines` and never shown. The values still pass the whitelist when rendered."""
    pattern = ENTRY_LINES[_ENTRY_COMMANDS.index(command)] if command in _ENTRY_COMMANDS else None
    lines = captured_stdout.split("\n")
    if lines[-1] == "":
        lines.pop()
    fields: list[tuple[str, str]] = []
    unparsed = 0
    for line in lines:
        found = None if pattern is None else pattern.fullmatch(line)
        if found is None:
            unparsed += 1
        elif command == "report":
            rule = "none" if found["rule"] == _NO_RULE else found["rule"]
            fields += (("end", found["end"]), ("rule", rule), ("pinned", found["pinned"]))
            fields += (("report_file", name) for name in _REPORT_FILE.findall(found["reports"]))
        else:
            fields += (("unit", found["unit"]), ("fault_id", found["fault_id"]))
    if unparsed:
        fields.append(("unparsed_lines", str(unparsed)))
    return tuple(fields)


def _command(argv: list[str]) -> str:
    """The subcommand's result name, read from argv alone; "" when argv names none."""
    if argv[:1] == ["freeze"] and argv[1:2] in (["inputs"], ["gold"]):
        return f"freeze {argv[1]}"
    return argv[0] if argv[:1] and argv[0] in ("run", "context", "report", "void") else ""


def _error_name(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if name in ERROR_NAMES else "other"


def _failed(command: str, exc: BaseException) -> cli.CommandResult:
    """The result of an exception: `SystemExit` keeps its code, and reads `usage` when that is not 0;
    `KeyboardInterrupt` exits 130; anything else exits 1. Only the class name is kept."""
    if isinstance(exc, SystemExit):
        code = exc.code
        number = 0 if code is None else int(code) if isinstance(code, int) else 1
        return cli.CommandResult(command, number, () if number == 0 else (("error", "usage"),))
    number = 130 if isinstance(exc, KeyboardInterrupt) else 1
    return cli.CommandResult(command, number, (("error", _error_name(exc)),))


def _dispatched(argv: list[str], command: str, env: cli.CliEnv, captured: io.StringIO) -> cli.CommandResult:
    """The guarded call, with every exception's traceback written to the redirected stderr."""
    try:
        if command == "report":
            cli.check_report(argv, env)
        result = cli.dispatch(argv, env, guarded=True)
    except BaseException as exc:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        return _failed(command, exc)
    fields = result.fields
    if command in _ENTRY_COMMANDS:
        fields = (*fields, *entry_fields(command, captured.getvalue()))
    return cli.CommandResult(command, result.exit_code, fields)


def _write_all(fd: int, lines: Sequence[str]) -> None:
    data = "".join(f"{line}\n" for line in lines).encode("ascii")
    while data:
        data = data[os.write(fd, data) :]


def run_wrapped(
    argv: Sequence[str],
    *,
    log_dir: str,
    env: cli.CliEnv,
    stamp: Callable[[], datetime],
    fds: tuple[int, int] = (1, 2),
    restore: bool = False,
) -> int:
    """Run one CLI subcommand with its output in a new log under `log_dir`, and write only the checked console lines
    to the saved copy of `fds[0]`: `log`, `command`, the fields, `withheld` and `exit`. Returns the exit code.
    `fds` and `restore` exist for tests: in production the descriptors and streams stay on the log (AMB-158)."""
    given = list(argv)
    command = _command(given)
    out_fd, err_fd = fds
    name = f"wrapper-{stamp().strftime(_STAMP)}-{command.replace(' ', '-') or 'other'}.log"
    try:
        resolved = records.refuse_onedrive(log_dir, realpath=env.tools.realpath).rstrip("/" + chr(92))
        log = env.tools.open_log(f"{resolved}/{name}")
    except Exception as exc:
        _write_all(out_fd, _lines((("error", _error_name(exc)),), 1))
        return 1
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.flush()
    console = os.dup(out_fd)
    saved_err = os.dup(err_fd) if restore else None
    streams = (sys.stdout, sys.stderr)
    os.dup2(log.fileno(), out_fd)
    os.dup2(log.fileno(), err_fd)
    captured = io.StringIO()
    sys.stdout = cast(TextIO, _Tee(log, out_fd, captured))
    sys.stderr = cast(TextIO, _Tee(log, err_fd, None))
    try:
        result = _dispatched(given, command, env, captured)
        _write_all(console, _lines((("log", name), ("command", result.command), *result.fields), result.exit_code))
    finally:
        if saved_err is None:
            os.close(console)
        else:
            os.dup2(console, out_fd)
            os.dup2(saved_err, err_fd)
            os.close(console)
            os.close(saved_err)
            sys.stdout, sys.stderr = streams
            log.close()
    return result.exit_code


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def main(argv: Sequence[str] | None = None) -> int:
    """`--log-dir DIR -- <cli argv>`, read by hand. Any other shape writes `error: usage` and `exit: 2` to the
    console and runs nothing."""
    given = list(sys.argv[1:] if argv is None else argv)
    if len(given) < 3 or given[0] != "--log-dir" or given[2] != "--":
        _write_all(1, _lines((("error", "usage"),), 2))
        return 2
    return run_wrapped(given[3:], log_dir=given[1], env=cli.real_env(), stamp=_utc_now)


if __name__ == "__main__":
    raise SystemExit(main())
