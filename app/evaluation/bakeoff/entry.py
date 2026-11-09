"""The bake-off's report and void commands (bake-off harness H4a).

This is the one module that orchestrates the H4 steps over a stored run directory, and the only one that sets scored
markers. `main` alone builds the real tools, the real sync and the real clock; everything below it takes them as
arguments. Output goes through `sys.stdout.write` and `sys.stderr.write`, never `print`.

Implements, from the signed D6 criteria note S2:

- S2 §3.12, frozen inputs and outputs: the run directory is resolved and refused when it is under OneDrive before
  anything is read, the gold set is checked against the manifest (and against the recorded freeze, when the operator
  gives it) before any marker exists, and the report pair is new, never overwritten.
- S2 §3.13, runs: a schedule that did not complete gets a records-only report and no marker. Otherwise every counted
  unit is marked before any of its scores is computed ("logged before its scores are computed"), and the operator may
  void a recorded, unmarked attempt with `void` before the first report.
- S2 §3.2 and §11 item 5, pre-run checks: a manifest whose run-condition checks are not exactly "passed" gets a
  records-only report naming them.
- S2 §9, report: `report-<n>.json` is the report of record; the Markdown file of each pair is a fixed placeholder
  until H5 renders it.

Exit codes: 0 a scored report was written, or `void` succeeded; 3 a records-only report was written; 1 either a
refusal, with nothing written, or a failure after writing began, which stderr tells apart and names what may be left;
2 a usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from types import MappingProxyType

from . import decision, gates, report, runner, stats
from .evidence import AttemptEvidence, EvidenceLoader
from .gold import GoldSetError, load_gold_set
from .launch import LaunchTools
from .records import (
    _RUN_NAME,
    MAX_FAULT_DETAIL,
    FaultRecord,
    RunKey,
    RunStore,
    RunView,
    StoreError,
    _unit_name,
    load_run,
    machine_log_name,
    parse_unit_name,
    refuse_onedrive,
    report_numbers,
    write_report,
)

RECORDS_ONLY_EXIT = 3
_HOOKED = ("timed_run", "contended")  # the phases whose attempts carry machine evidence (S2 §6, gate M)
_SECONDS = re.compile(r"[0-9]+([.][0-9]+)?")  # fullmatch only: G4-6's plain decimal
_VOID_WARNING = (
    "warning: a counted unit that is now void leaves the schedule incomplete, with no resume (AMB-72); "
    "the next report is records-only\n"
)
_VOID_HELP = (
    "Void one recorded, unscored attempt with an operator fault (S2 §3.13). Use it only after the schedule process "
    "has ended: an attempt with no run record is refused, so a live attempt is never touched."
)
# What a failure after writing began may leave, by stage (S2 §3.13: a set marker is never removed).
_AFTER_WRITE = MappingProxyType(
    {
        "marking": "scored/ markers set before the failure are kept (S2 §3.13), and any report-<n> files left are "
        "incomplete; the next report takes a new number",
        "report": "no marker is set; any report-<n> files left are incomplete, and the next report takes a new number",
        "void": "the operator fault may be logged without its void; the next report lists it as a fault with no void",
    }
)


class _AfterWriteError(Exception):
    """A failure once writing began: `stage` names what may be left (a key of `_AFTER_WRITE`) and `cause` is the
    original error. It is not a `ReportError`, so `main` never reports it as a refusal that wrote nothing."""

    def __init__(self, stage: str, cause: BaseException) -> None:
        super().__init__(stage, cause)
        self.stage = stage
        self.cause = cause


@dataclass(frozen=True, slots=True)
class ReportOutcome:
    """What one `report` call wrote: the resolved run directory, the pair's paths (Markdown first, in write order),
    the schedule end, and the rule and pin, which are None for a records-only report."""

    run_dir: str
    paths: tuple[str, ...]
    end: gates.ScheduleEnd
    rule: str | None
    pinned: str | None


def _no_machine_evidence(run_dir: str, key: RunKey, read_bytes: Callable[[str], bytes]) -> AttemptEvidence | None:
    """The H4a evidence loader. No attempt has evidence, so gate M is not measured (G4-17, G4-22); a host record that
    is present is refused, because this build cannot read it (H4b brings the loader)."""
    try:
        read_bytes(f"{run_dir}/{machine_log_name(key, 'host')}")
    except FileNotFoundError:
        return None
    raise gates.ReportError(
        f"machine evidence present for {_unit_name(key)}; this H4a build has no evidence loader (H4b)"
    )


def _hooked_keys(view: RunView) -> tuple[RunKey, ...]:
    """Every timed and contended attempt with a run record, voided ones included: voided readings are listed."""
    return tuple(dict.fromkeys(run.key for run in view.runs if run.key.phase in _HOOKED))


def _abandoned(parent: str, name: str, read_bytes: Callable[[str], bytes]) -> report.AbandonedRun:
    """One `--abandoned` directory, loaded read-only from the run's own parent (G4-37). The name is checked first, so
    no path part reaches `load_run`."""
    if not isinstance(name, str) or _RUN_NAME.fullmatch(name) is None:
        raise gates.ReportError(f"--abandoned {name!r} is not a run name ({_RUN_NAME.pattern})")
    path = f"{parent}/{name}"
    try:
        view = load_run(path)
        digest = hashlib.sha256(read_bytes(f"{path}/manifest.json")).hexdigest()
    except (StoreError, OSError) as exc:
        return report.AbandonedRun(name, None, 0, 0, 0, f"unreadable: {type(exc).__name__}")
    return report.AbandonedRun(name, digest, len(view.runs), len(view.voids), len(view.truncated), None)


def _path_needles(view: RunView, run_dir: str) -> tuple[str, ...]:
    """The configured paths no report string may hold (G4-38): the binary, every GGUF, every model path a launch
    recorded, and the run's parent unless it is a filesystem root, which every "/" would match. Empty ones are
    dropped."""
    manifest = view.manifest
    found = [
        manifest["conditions"]["binary_path"],
        *(candidate["gguf_path"] for candidate in manifest["candidates"]),
        *(launch.model_path for launch in view.launches),
    ]
    parent = os.path.dirname(run_dir)
    if parent and os.path.dirname(parent) != parent:
        found.append(parent)
    return tuple(dict.fromkeys(needle for needle in found if isinstance(needle, str) and needle))


def score_and_report(
    run_dir: str,
    gold_dir: str,
    report_inputs: gates.ReportInputs,
    abandoned: tuple[str, ...],
    *,
    tools: LaunchTools,
    now: Callable[[], datetime],
    fsync: Callable[[int], None],
    load_evidence: EvidenceLoader,
    resamples: int = stats.BOOTSTRAP_RESAMPLES,
) -> ReportOutcome:
    """Score a stored run and write its report pair (G4-3, G4-42). Refusals raise `ReportError` or `StoreError`
    before any marker or report exists: the integrity checks, the report inputs, the G4-38 path walk over the strings
    the report copies (`report.check_paths`, an H4a step before marking) and the evidence load all come before
    `RunStore.attach`. A failure from the first marker on, or from `write_report` on the records-only path, raises
    `_AfterWriteError`: markers set before it are kept (S2 §3.13), and any report files it left are incomplete and
    their number stays used. `resamples` exists for tests: the operator path is always `stats.BOOTSTRAP_RESAMPLES`."""
    run_dir = refuse_onedrive(run_dir, realpath=tools.realpath)
    view = load_run(run_dir)
    manifest_sha256 = hashlib.sha256(tools.read_bytes(f"{run_dir}/manifest.json")).hexdigest()
    parent = os.path.dirname(run_dir)
    abandoned_runs = tuple(_abandoned(parent, name, tools.read_bytes) for name in abandoned)
    end = gates.schedule_end(view)
    failed = gates.failed_run_conditions(view.manifest["operator"])

    def write(
        view: RunView,
        results: dict[str, gates.CandidateResult] | None = None,
        shares: dict[str, runner.DeterminismShare] | None = None,
        outcome: decision.Decision | None = None,
        operator: gates.OperatorValues | None = None,
    ) -> ReportOutcome:
        try:
            body = report.build_report(
                view,
                end,
                results,
                shares,
                outcome,
                operator,
                report_inputs,
                abandoned_runs,
                manifest_sha256,
                _path_needles(view, run_dir),
                run_name=os.path.basename(run_dir),
                report_numbers=report_numbers(run_dir, tools.read_bytes),
            )
        except ValueError as exc:
            raise gates.ReportError(f"the report body was refused: {exc}") from None
        try:
            paths = write_report(run_dir, report.markdown_placeholder(), body, fsync=fsync, realpath=tools.realpath)
        except (StoreError, OSError) as exc:
            raise _AfterWriteError("report" if operator is None else "marking", exc) from exc
        rule = None if outcome is None else outcome.rule
        return ReportOutcome(run_dir, paths, end, rule, None if outcome is None else outcome.pinned)

    if end != "complete" or failed:
        return write(view)
    operator = gates.OperatorValues.from_manifest(view.manifest["operator"])
    try:
        gold = load_gold_set(gold_dir)
    except (GoldSetError, OSError) as exc:
        raise gates.ReportError(f"the gold set cannot be loaded: {type(exc).__name__}: {exc}") from None
    gates.check_records(view, gold, report_inputs.expected)
    gates.check_report_inputs(view, operator, report_inputs)
    numbers = report_numbers(run_dir, tools.read_bytes)
    try:
        report.check_paths(view, report_inputs, abandoned_runs, numbers, _path_needles(view, run_dir))
    except (ValueError, TypeError) as exc:
        raise gates.ReportError(f"the report would be refused, so nothing is marked: {exc}") from None
    evidence = {key: load_evidence(run_dir, key, tools.read_bytes) for key in _hooked_keys(view)}
    store = RunStore.attach(run_dir, now=now, fsync=fsync, realpath=tools.realpath)
    try:
        for key in gates.units_to_mark(view):
            if not view.marked(key):
                store.mark_scored(key)
        view = load_run(run_dir)
        results = gates.evaluate(view, gold, evidence, operator, report_inputs)
        shares = runner.determinism_shares(view)
        found, variant, _why_not = decision.margin(results, shares, operator.bootstrap_seed, resamples=resamples)
        return write(view, results, shares, decision.decide(results, found, variant), operator)
    except (gates.ReportError, StoreError, OSError) as exc:
        raise _AfterWriteError("marking", exc) from exc


def _void_unit(
    run_dir: str,
    unit: str,
    detail: str,
    *,
    tools: LaunchTools,
    now: Callable[[], datetime],
    fsync: Callable[[int], None],
) -> FaultRecord:
    """Void one attempt with an operator fault (G4-44). Every refusal comes before any write, so a refused call leaves
    `faults.jsonl` and `voids.jsonl` unchanged. The last refusal is a detail holding a path: every later report copies
    it, so G4-38 would refuse each of them (an H4a addition to G4-44's list). A failure once the fault is being logged
    raises `_AfterWriteError`."""
    run_dir = refuse_onedrive(run_dir, realpath=tools.realpath)
    key = parse_unit_name(unit)
    view = load_run(run_dir)
    if key.phase == "pre_run":
        raise StoreError(f"{unit} is a pre-run: it has no run to void")
    if not any(run.key == key for run in view.runs):
        raise StoreError(f"{unit} has no run record: only a recorded attempt of an ended schedule can be voided")
    if any(void.key == key for void in view.voids):
        raise StoreError(f"{unit} is already void")
    if view.marked(key):
        raise StoreError(f"{unit} is marked as scored, so it can no longer be voided (S2 §3.13)")
    if not detail.isascii() or not detail.strip() or len(detail) > MAX_FAULT_DETAIL:
        raise StoreError(f"--detail must be non-blank ASCII of at most {MAX_FAULT_DETAIL} characters")
    try:
        report.refuse_paths(detail, _path_needles(view, run_dir))
    except ValueError:
        raise StoreError("--detail must not hold a path (S2 §9)") from None
    store = RunStore.attach(run_dir, now=now, fsync=fsync, realpath=tools.realpath)
    try:
        fault = store.log_fault(key, "operator", item_id=None, detail=detail)
        store.void_run(key, fault.fault_id)
    except (StoreError, OSError) as exc:
        raise _AfterWriteError("void", exc) from exc
    return fault


def _seconds(text: str) -> Fraction:
    """A `--deadline-sec` or `--rerun-allowance-sec` value: G4-6's plain decimal, exact, above 0 (G4-14)."""
    if _SECONDS.fullmatch(text) is None or Fraction(text) <= 0:
        raise argparse.ArgumentTypeError(f"{text!r} is not a plain decimal above 0, such as 120 or 62.5")
    return Fraction(text)


def _shared_memory(text: str) -> tuple[str, str]:
    """One `--shared-memory SLUG=VALUE` entry. The slug is checked against the run later (G4-21)."""
    slug, sign, value = text.partition("=")
    if not sign or not slug or value not in gates.SHARED_MEMORY_VALUES:
        raise argparse.ArgumentTypeError(f"{text!r} is not SLUG=VALUE with VALUE one of {gates.SHARED_MEMORY_VALUES}")
    return slug, value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bakeoff", description="Score a stored bake-off run, or void one attempt.")
    commands = parser.add_subparsers(dest="command", required=True)
    scoring = commands.add_parser("report", help="Score a stored run and write its report pair (S2 §3.13, §9).")
    scoring.add_argument("--run-dir", required=True)
    scoring.add_argument("--gold-dir", required=True)
    scoring.add_argument("--shared-memory", action="append", default=[], type=_shared_memory, metavar="SLUG=VALUE")
    scoring.add_argument("--shared-memory-evidence-sha256")
    scoring.add_argument("--deadline-sec", type=_seconds)
    scoring.add_argument("--rerun-allowance-sec", type=_seconds)
    scoring.add_argument("--reopening-reason")
    scoring.add_argument("--abandoned", action="append", default=[], metavar="NAME")
    scoring.add_argument("--expected-freeze", metavar="FILE")
    voiding = commands.add_parser("void", help=_VOID_HELP, description=_VOID_HELP)
    voiding.add_argument("--run-dir", required=True)
    voiding.add_argument("--unit", required=True, metavar="<slug>.<unit>.a<n>")
    voiding.add_argument("--detail", required=True)
    return parser


def _report_inputs(args: argparse.Namespace, tools: LaunchTools) -> gates.ReportInputs:
    expected = None
    if args.expected_freeze is not None:
        expected = gates.parse_expected_freeze(tools.read_bytes(args.expected_freeze))
    return gates.ReportInputs(
        shared_memory=gates.shared_memory_entries(args.shared_memory),
        shared_memory_evidence_sha256=args.shared_memory_evidence_sha256,
        deadline_override_sec=args.deadline_sec,
        rerun_allowance_override_sec=args.rerun_allowance_sec,
        reopening_reason=args.reopening_reason,
        expected=expected,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """The `report` and `void` commands. A usage error exits through argparse with status 2."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "report":
        reopening = args.deadline_sec is not None or args.rerun_allowance_sec is not None
        reason = args.reopening_reason is not None and bool(args.reopening_reason.strip())
        if reopening != reason:
            parser.error("--reopening-reason is required exactly when --deadline-sec or --rerun-allowance-sec is given")
    tools = LaunchTools()
    try:
        if args.command == "void":
            fault = _void_unit(
                args.run_dir,
                args.unit,
                args.detail,
                tools=tools,
                now=lambda: datetime.now(timezone.utc),
                fsync=os.fsync,
            )
            sys.stdout.write(f"void: {args.unit} with operator fault {fault.fault_id}\n")
            sys.stdout.write(_VOID_WARNING)
            return 0
        outcome = score_and_report(
            args.run_dir,
            args.gold_dir,
            _report_inputs(args, tools),
            tuple(args.abandoned),
            tools=tools,
            now=lambda: datetime.now(timezone.utc),
            fsync=os.fsync,
            load_evidence=_no_machine_evidence,
        )
    except _AfterWriteError as exc:
        cause = exc.cause
        sys.stderr.write(f"failed after writing began: {type(cause).__name__}: {cause}; {_AFTER_WRITE[exc.stage]}\n")
        return 1
    except (gates.ReportError, StoreError, OSError) as exc:
        sys.stderr.write(f"refused, nothing written: {type(exc).__name__}: {exc}\n")
        return 1
    rule = outcome.rule or "none (records-only report)"
    sys.stdout.write(
        f"end: {outcome.end}; rule: {rule}; pinned: {outcome.pinned or 'none'}; reports: {', '.join(outcome.paths)}\n"
    )
    return 0 if outcome.rule is not None else RECORDS_ONLY_EXIT
