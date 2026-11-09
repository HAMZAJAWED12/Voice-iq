"""The report and void commands: H4 spec section 4 G4-3, G4-14, G4-21, G4-37, G4-42 to G4-44 and section 8's
test_entry row, cut to the H4a subset (H4a brief sections 4 and 9, step A6; amendments 2, 3 and 4) (S2 §3.2,
§3.12, §3.13, §9, §11).

Run directories come from two sources. `schedule_rig` runs a whole schedule over `frozen_inputs` with the H3 fakes and
`RecordingHooks`, so it holds no machine evidence and the H4a loader leaves gate M not measured. `_h4_fixtures`'s
`make_view` builds a schedule that can pass every gate when an injected loader supplies clean evidence. No gold row is
used, no process is started and no socket is opened.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
import os
import shutil
from collections.abc import Callable, Mapping
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from app.evaluation.bakeoff import entry as entry_module
from app.evaluation.bakeoff import gates as gates_module
from app.evaluation.bakeoff import report as report_module
from app.evaluation.bakeoff import stats
from app.evaluation.bakeoff.entry import (
    RECORDS_ONLY_EXIT,
    ReportOutcome,
    _no_machine_evidence,
    _path_needles,
    main,
    score_and_report,
)
from app.evaluation.bakeoff.evidence import AttemptEvidence
from app.evaluation.bakeoff.gates import ReportError, ReportInputs, units_to_mark
from app.evaluation.bakeoff.gold import GoldSet, load_gold_set
from app.evaluation.bakeoff.inputs import FrozenInputs
from app.evaluation.bakeoff.launch import CANDIDATE_IDS, CANDIDATE_SLUGS, LaunchTools, ServerStopFailed
from app.evaluation.bakeoff.records import (
    RunKey,
    RunStore,
    RunView,
    StoreError,
    load_run,
    machine_log_name,
    record_json,
    report_numbers,
)
from app.evaluation.bakeoff.runner import RunsExhausted
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._h4_fixtures import (
    loader,
    make_view,
    passing_evidence,
    store_view,
    write_h4_gold,
)
from app.evaluation.tests._runner_fixtures import (
    SCHEDULE_CANDIDATES,
    VALID_OPERATOR,
    ServerScript,
    block_processes,
    fixed_now,
    frozen_inputs,
    no_fsync,
    schedule_rig,
    small_inputs,
    synthetic_gold,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
RESAMPLES = 200
NO_INPUTS = ReportInputs(MappingProxyType({}), None, None, None, None, None)
RUN_1 = "qwen3-4b.run1.a1"
REPORT_NAMES = ["report-1.md", "report-1.json"]
HOME_FORM = "/ho" + "me/fixture-operator/"  # built in pieces, so no source holds a home path


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


# ---------------------------------------------------------------- fixtures and helpers


@dataclasses.dataclass(frozen=True)
class Rig:
    """A complete schedule run with the H3 fakes, and the frozen inputs and gold directory behind it."""

    inputs: FrozenInputs
    gold_dir: Path
    run_dir: Path


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Rig:
    root = tmp_path_factory.mktemp("rig")
    with pytest.MonkeyPatch.context() as patch:
        block_sockets(patch)
        block_processes(patch)
        inputs = frozen_inputs(root)
        (root / "runs").mkdir()
        schedule = schedule_rig(inputs, root / "runs", operator=VALID_OPERATOR)
        schedule.run()
    return Rig(inputs, root / "v1.0", Path(schedule.run_dir))


@pytest.fixture(scope="module")
def h4(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, GoldSet]:
    """`_h4_fixtures`' gold directory and its loaded set."""
    directory = write_h4_gold(tmp_path_factory.mktemp("h4gold"))
    return directory, load_gold_set(directory)


def copy_run(source: Path, parent: Path) -> Path:
    """A fresh copy of a stored run directory, so a test may mark and report in it."""
    target = parent / "run_1"
    shutil.copytree(source, target)
    return target


def stored(view: RunView, parent: Path, name: str = "run_1") -> Path:
    return Path(store_view(view, parent, name))


def score(
    run_dir: Path | str,
    gold_dir: Path | str,
    *,
    inputs: ReportInputs = NO_INPUTS,
    abandoned: tuple[str, ...] = (),
    load_evidence: Any = _no_machine_evidence,
    tools: LaunchTools | None = None,
) -> ReportOutcome:
    return score_and_report(
        str(run_dir),
        str(gold_dir),
        inputs,
        abandoned,
        tools=LaunchTools() if tools is None else tools,
        now=fixed_now,
        fsync=no_fsync,
        load_evidence=load_evidence,
        resamples=RESAMPLES,
    )


def body(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_bytes())


def json_of(run_dir: Path, number: int = 1) -> dict[str, Any]:
    return body(str(run_dir / "reports" / f"report-{number}.json"))


def markers(run_dir: Path) -> list[str]:
    return sorted(path.name for path in (run_dir / "scored").iterdir())


def logs(run_dir: Path) -> tuple[bytes, bytes]:
    return (run_dir / "faults.jsonl").read_bytes(), (run_dir / "voids.jsonl").read_bytes()


def candidate(report: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    return next(entry for entry in report["candidates"] if entry["candidate_id"] == candidate_id)


def fraction(value: Fraction) -> Any:
    return report_module.to_report_value(value)


def report_argv(run_dir: Path, gold_dir: Path | str, *extra: str) -> list[str]:
    return ["report", "--run-dir", str(run_dir), "--gold-dir", str(gold_dir), *extra]


def clean_dir(h4: tuple[Path, GoldSet], parent: Path, **options: Any) -> Path:
    """A whole, unmarked `make_view` schedule stored under `parent`."""
    return stored(make_view(h4[1], marked=False, **options), parent)


# ---------------------------------------------------------------- signature (amendment 2)


def test_score_and_report_defaults_to_the_s2_resample_count() -> None:
    parameters = inspect.signature(score_and_report).parameters
    assert parameters["resamples"].default is stats.BOOTSTRAP_RESAMPLES
    assert parameters["resamples"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["load_evidence"].default is inspect.Parameter.empty
    assert parameters["load_evidence"].kind is inspect.Parameter.KEYWORD_ONLY
    assert [name for name, p in parameters.items() if p.kind is inspect.Parameter.KEYWORD_ONLY] == [
        "tools",
        "now",
        "fsync",
        "load_evidence",
        "resamples",
    ]


# ---------------------------------------------------------------- a schedule_rig directory and the H4a loader


def test_a_rig_schedule_scores_to_rule_5_with_every_gate_m_not_measured(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = copy_run(rig.run_dir, tmp_path)
    before = load_run(str(run_dir))
    events: list[tuple[str, Any]] = []
    real_mark = RunStore.mark_scored
    real_evaluate = gates_module.evaluate

    def mark(self: RunStore, key: RunKey) -> None:
        events.append(("mark", key))
        real_mark(self, key)

    def evaluate(*args: Any, **kwargs: Any) -> Any:
        events.append(("evaluate", None))
        return real_evaluate(*args, **kwargs)

    def load(run: str, key: RunKey, read_bytes: Callable[[str], bytes]) -> AttemptEvidence | None:
        events.append(("evidence", key))
        return _no_machine_evidence(run, key, read_bytes)

    monkeypatch.setattr(RunStore, "mark_scored", mark)
    monkeypatch.setattr(gates_module, "evaluate", evaluate)
    outcome = score(run_dir, rig.gold_dir, load_evidence=load)

    assert (outcome.end, outcome.rule, outcome.pinned) == ("complete", "rule 5", None)
    assert outcome.run_dir == os.path.realpath(run_dir)
    assert [Path(path).name for path in outcome.paths] == REPORT_NAMES
    kinds = [kind for kind, _ in events]
    assert kinds == ["evidence"] * 12 + ["mark"] * 15 + ["evaluate"]
    hooked = [run.key for run in before.runs if run.key.phase in ("timed_run", "contended")]
    assert [key for kind, key in events if kind == "evidence"] == hooked
    assert [key for kind, key in events if kind == "mark"] == list(units_to_mark(before))
    assert len(markers(run_dir)) == 15
    report = json_of(run_dir)
    assert report["schedule_end"] == "complete" and report["run_conditions_met"] is True
    for candidate_id in CANDIDATE_IDS:
        assert candidate(report, candidate_id)["gates"]["M"]["status"] == "not_measured"
    assert (report["decision"]["rule"], report["decision"]["pinned"], report["decision"]["settings"]) == (
        "rule 5",
        None,
        None,
    )
    assert report["gold_manifest_checked"] is False


def test_scoring_twice_marks_nothing_new_and_gives_identical_pairs(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = copy_run(rig.run_dir, tmp_path)
    first = score(run_dir, rig.gold_dir)
    marked: list[RunKey] = []
    real_mark = RunStore.mark_scored

    def mark(self: RunStore, key: RunKey) -> None:
        marked.append(key)
        real_mark(self, key)

    monkeypatch.setattr(RunStore, "mark_scored", mark)
    second = score(run_dir, rig.gold_dir)
    assert marked == []
    assert [Path(path).name for path in second.paths] == ["report-2.md", "report-2.json"]
    for one, two in zip(first.paths, second.paths, strict=True):
        assert Path(one).read_bytes() == Path(two).read_bytes()
    assert json_of(run_dir, 2)["reopening"]["incomplete_reports"] == []


def test_a_gold_mismatch_is_refused_with_nothing_marked(rig: Rig, tmp_path: Path) -> None:
    run_dir = copy_run(rig.run_dir, tmp_path)
    other = synthetic_gold(tmp_path / "other", syn_a=3)
    with pytest.raises(ReportError):
        score(run_dir, other)
    assert markers(run_dir) == []
    assert not (run_dir / "reports").exists()


def test_a_hand_placed_host_record_is_refused_with_nothing_marked(rig: Rig, tmp_path: Path) -> None:
    run_dir = copy_run(rig.run_dir, tmp_path)
    (run_dir / machine_log_name(RunKey(Q4B, "timed_run", 1, 1), "host")).write_bytes(b"{}\n")
    with pytest.raises(ReportError, match="machine evidence present for qwen3-4b.run1.a1; .* no evidence loader"):
        score(run_dir, rig.gold_dir)
    assert markers(run_dir) == []
    assert not (run_dir / "reports").exists()


def test_the_h4a_loader_reads_only_the_host_record() -> None:
    key = RunKey(Q35, "contended", None, 2)
    seen: list[str] = []

    def missing(path: str) -> bytes:
        seen.append(path)
        raise FileNotFoundError(path)

    assert _no_machine_evidence("/runs/run_1", key, missing) is None
    assert seen == ["/runs/run_1/logs/qwen3.5-4b.contended.a2.host.json"]
    with pytest.raises(ReportError, match="qwen3.5-4b.contended.a2"):
        _no_machine_evidence("/runs/run_1", key, lambda _path: b"{}")


def test_three_contended_voids_list_the_unit_as_needing_a_repeat(rig: Rig, tmp_path: Path) -> None:
    def fail(name: str, key: RunKey) -> BaseException | None:
        return RuntimeError("synthetic") if name == "start_contention" and key.candidate == Q8B else None

    schedule = schedule_rig(rig.inputs, tmp_path, operator=VALID_OPERATOR, fail=fail)
    schedule.run()
    run_dir = Path(schedule.run_dir)
    outcome = score(run_dir, rig.gold_dir)
    assert (outcome.end, outcome.rule) == ("complete", "rule 5")
    report = json_of(run_dir)
    absent = candidate(report, Q8B)
    assert absent["contended"]["status"] == "absent" and absent["contended"]["needs_repeat"] is True
    assert absent["gates"]["M"]["status"] == "not_measured"
    assert report["reopening"]["absent_ungated"] == ["qwen3-8b.contended"]
    assert [(void["unit"], void["fault_kind"]) for void in report["reopening"]["voids"]] == [
        (f"qwen3-8b.contended.a{n}", "hook_failed") for n in (1, 2, 3)
    ]


# ---------------------------------------------------------------- a make_view directory with a passing loader


def test_clean_evidence_ends_at_rule_2_at_the_s2_resample_count(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    view = make_view(h4[1], marked=False)
    run_dir = stored(view, tmp_path)
    outcome = score_and_report(
        str(run_dir),
        str(h4[0]),
        NO_INPUTS,
        (),
        tools=LaunchTools(),
        now=fixed_now,
        fsync=no_fsync,
        load_evidence=loader(passing_evidence(view)),
    )
    assert (outcome.end, outcome.rule, outcome.pinned) == ("complete", "rule 2", Q4B)
    assert [Path(path).name for path in outcome.paths] == REPORT_NAMES
    assert all(Path(path).parent == Path(outcome.run_dir) / "reports" for path in outcome.paths)
    report = json_of(run_dir)
    assert (report["decision"]["rule"], report["decision"]["pinned"]) == ("rule 2", Q4B)
    assert report["decision"]["margin"]["conforming"] is True
    assert all(candidate(report, c)["gates"]["M"]["status"] == "pass" for c in CANDIDATE_IDS)
    assert len(markers(run_dir)) == 15


def test_a_relative_run_dir_is_resolved_once_and_used_everywhere(
    h4: tuple[Path, GoldSet], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    stored(make_view(h4[1], marked=False, not_loaded=CANDIDATE_IDS), tmp_path, "bakeoff-1")
    resolved = str(run_dir)
    tools = LaunchTools(realpath=lambda path: resolved if path == "relative/run_1" else path)
    needles: list[tuple[str, ...]] = []
    real_build = report_module.build_report

    def build(*args: Any, **kwargs: Any) -> Any:
        needles.append(args[9])
        return real_build(*args, **kwargs)

    monkeypatch.setattr(report_module, "build_report", build)
    view = make_view(h4[1], marked=False)
    outcome = score(
        "relative/run_1", h4[0], abandoned=("bakeoff-1",), tools=tools, load_evidence=loader(passing_evidence(view))
    )
    assert outcome.run_dir == resolved
    assert all(Path(path).parent == run_dir / "reports" for path in outcome.paths)
    report = json_of(run_dir)
    assert report["run_name"] == "run_1"
    assert report["reopening"]["abandoned"][0]["unreadable"] is None
    assert os.path.dirname(resolved) in needles[0] and "" not in needles[0]


def test_empty_path_needles_are_dropped(h4: tuple[Path, GoldSet]) -> None:
    view = make_view(h4[1])
    manifest = copy.deepcopy(dict(view.manifest))
    manifest["conditions"]["binary_path"] = ""
    needles = _path_needles(dataclasses.replace(view, manifest=manifest), "/runs/run_1")
    assert needles == (*dict.fromkeys(row.gguf_path for row in SCHEDULE_CANDIDATES), "/runs")
    assert _path_needles(view, "/runs/run_1")[0] == view.manifest["conditions"]["binary_path"]


def test_a_run_dir_under_onedrive_is_refused_before_anything_is_read() -> None:
    def refuse(path: str) -> bytes:
        raise AssertionError(f"read {path}")

    tools = LaunchTools(read_bytes=refuse, realpath=lambda _path: "/data/" + "OneDrive" + "/run_1")
    with pytest.raises(StoreError, match="OneDrive"):
        score("run_1", "gold", tools=tools)


# ---------------------------------------------------------------- records-only reports (exit 3)


def test_all_pre_runs_not_loaded_gives_a_records_only_report(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = clean_dir(h4, tmp_path, not_loaded=CANDIDATE_IDS)
    assert main(report_argv(run_dir, tmp_path / "no-gold-is-read")) == RECORDS_ONLY_EXIT == 3
    report = json_of(run_dir)
    assert report["schedule_end"] == "pre_run_stopped"
    assert report["stop_reason"] == ["no candidate's pre-run was ready"]
    assert (report["candidates"], report["decision"], report["budgets"]) == (None, None, None)
    assert markers(run_dir) == []
    out = capsys.readouterr().out
    assert out.startswith("end: pre_run_stopped; rule: none (records-only report); pinned: none; reports: ")
    assert out.count("\n") == 1


def test_runs_exhausted_gives_a_records_only_report(rig: Rig, tmp_path: Path) -> None:
    def fail(name: str, key: RunKey) -> BaseException | None:
        hit = name == "before_timed_run" and (key.candidate, key.run) == (Q4B, 1)
        return RuntimeError("synthetic") if hit else None

    schedule = schedule_rig(small_inputs(rig.inputs, singles=1, sessions=1, probes=1), tmp_path, fail=fail)
    with pytest.raises(RunsExhausted):
        schedule.run()
    run_dir = Path(schedule.run_dir)
    assert main(report_argv(run_dir, rig.gold_dir)) == 3
    report = json_of(run_dir)
    assert report["schedule_end"] == "runs_exhausted"
    assert report["stop_reason"] == ["qwen3-4b.run1: 3 void attempts (S2 §3.13)"]
    assert len(report["reopening"]["voids"]) == 3
    assert markers(run_dir) == []


def test_a_truncated_calls_log_gives_an_incomplete_records_only_report(
    h4: tuple[Path, GoldSet], tmp_path: Path
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    lines = (run_dir / "calls.jsonl").read_bytes().count(b"\n")
    with (run_dir / "calls.jsonl").open("ab") as file:
        file.write(b'{"sequence":')
    assert main(report_argv(run_dir, h4[0])) == 3
    report = json_of(run_dir)
    assert report["schedule_end"] == "incomplete"
    assert report["stop_reason"] == [f"truncated: calls.jsonl line {lines + 1}"]
    assert report["reopening"]["truncated"] == [
        {"file_name": "calls.jsonl", "line_number": lines + 1, "byte_count": 12}
    ]
    assert markers(run_dir) == []


def test_a_stop_failure_gives_an_incomplete_report_listing_its_fault_and_void(rig: Rig, tmp_path: Path) -> None:
    tiny = small_inputs(rig.inputs, singles=1, sessions=1, probes=1)
    schedule = schedule_rig(tiny, tmp_path, {RUN_1: ServerScript(waits=(None, None))})
    with pytest.raises(ServerStopFailed):
        schedule.run()
    run_dir = Path(schedule.run_dir)
    assert main(report_argv(run_dir, rig.gold_dir)) == 3
    report = json_of(run_dir)
    assert report["schedule_end"] == "incomplete"
    assert report["reopening"]["voids"] == [
        {"unit": RUN_1, "fault_id": 1, "fault_kind": "server_not_stopped", "detail": "still running after kill"}
    ]


def test_a_run_condition_not_passed_gives_a_records_only_report(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    run_dir = clean_dir(h4, tmp_path, operator={**VALID_OPERATOR, "namespace_lan_check": "failed"})
    assert main(report_argv(run_dir, h4[0])) == 3
    report = json_of(run_dir)
    assert (report["schedule_end"], report["run_conditions_met"]) == ("complete", False)
    assert report["stop_reason"] == ["run condition not passed: namespace_lan_check (S2 §3.1, §3.2; AMB-132)"]
    assert report["decision"] is None
    assert markers(run_dir) == []


# ---------------------------------------------------------------- the report command (exit 0, 1 and 2)


def test_the_report_command_scores_and_exits_0(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    assert main(report_argv(run_dir, h4[0])) == 0
    out = capsys.readouterr().out
    real = os.path.realpath(run_dir)
    md, js = (os.path.join(real, "reports", name) for name in REPORT_NAMES)
    assert out == f"end: complete; rule: rule 5; pinned: none; reports: {md}, {js}\n"
    assert len(markers(run_dir)) == 15
    assert json_of(run_dir)["decision"]["margin"]["conforming"] is True


def test_a_deadline_reopening_recomputes_gate_t(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    run_dir = clean_dir(h4, tmp_path)
    assert main(report_argv(run_dir, h4[0], "--deadline-sec", "120", "--reopening-reason", "D5 changed")) == 0
    budgets = json_of(run_dir)["budgets"]
    assert budgets["run_deadline_sec"] == fraction(Fraction(120))
    assert (budgets["run_deadline_source"], budgets["run_deadline_manifest"]) == ("reopening", fraction(Fraction(125)))
    assert budgets["model_budget_sec"] == fraction(Fraction(60))
    assert (budgets["rerun_allowance_source"], budgets["reopening_reason"]) == ("manifest", "D5 changed")


def test_an_allowance_only_reopening_changes_no_gate_t_rule_or_winner(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    view = make_view(h4[1], marked=False)
    run_dir = stored(view, tmp_path)
    evidence = loader(passing_evidence(view))
    before = score(run_dir, h4[0], load_evidence=evidence)
    inputs = dataclasses.replace(NO_INPUTS, rerun_allowance_override_sec=Fraction(30), reopening_reason="allowance")
    after = score(run_dir, h4[0], inputs=inputs, load_evidence=evidence)
    assert (before.rule, before.pinned) == (after.rule, after.pinned) == ("rule 2", Q4B)
    first, second = json_of(run_dir, 1), json_of(run_dir, 2)
    for one, two in zip(first["candidates"], second["candidates"], strict=True):
        assert one["gates"]["T"] == two["gates"]["T"]
    assert second["decision"]["settings"] == first["decision"]["settings"]
    budgets = second["budgets"]
    assert (budgets["rerun_allowance_source"], budgets["run_deadline_source"]) == ("reopening", "manifest")
    assert (budgets["rerun_allowance_sec"], budgets["rerun_allowance_manifest"]) == (
        fraction(Fraction(30)),
        fraction(Fraction(25)),
    )
    assert budgets["reopening_reason"] == "allowance"


def test_both_reopening_values_are_recorded(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    run_dir = clean_dir(h4, tmp_path)
    argv = report_argv(
        run_dir, h4[0], "--deadline-sec", "62.5", "--rerun-allowance-sec", "30", "--reopening-reason", "R"
    )
    assert main(argv) == 0
    budgets = json_of(run_dir)["budgets"]
    assert (budgets["run_deadline_source"], budgets["rerun_allowance_source"]) == ("reopening", "reopening")
    assert budgets["run_deadline_sec"] == fraction(Fraction(125, 2))


@pytest.mark.parametrize(
    "extra",
    [
        ("--deadline-sec", "120"),
        ("--rerun-allowance-sec", "30"),
        ("--reopening-reason", "R"),
        ("--deadline-sec", "120", "--reopening-reason", "   "),
        ("--deadline-sec", "-1", "--reopening-reason", "R"),
        ("--deadline-sec", "0", "--reopening-reason", "R"),
        ("--deadline-sec", "inf", "--reopening-reason", "R"),
        ("--deadline-sec", "nan", "--reopening-reason", "R"),
        ("--deadline-sec", "1e2", "--reopening-reason", "R"),
        ("--rerun-allowance-sec", "1.25e2", "--reopening-reason", "R"),
        ("--deadline-sec", " 120", "--reopening-reason", "R"),
        ("--shared-memory", "qwen3-8b=maybe"),
        ("--shared-memory", "qwen3-8b"),
        ("--shared-memory", "=rose"),
    ],
)
def test_flag_errors_are_usage_errors_before_anything_is_read(tmp_path: Path, extra: tuple[str, ...]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(report_argv(tmp_path / "no-run", tmp_path / "no-gold", *extra))
    assert caught.value.code == 2


@pytest.mark.parametrize("subcommand", [[], ["run"], ["sampler-check"], ["audio-vram"], ["void", "--run-dir", "d"]])
def test_only_report_and_void_exist_and_need_their_arguments(subcommand: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        main(subcommand)
    assert caught.value.code == 2


@pytest.mark.parametrize(
    "extra",
    [
        ("--deadline-sec", "125.0", "--reopening-reason", "R"),
        ("--deadline-sec", "125", "--reopening-reason", "R"),
        ("--rerun-allowance-sec", "25", "--reopening-reason", "R"),
        ("--deadline-sec", "125", "--rerun-allowance-sec", "25.00", "--reopening-reason", "R"),
    ],
)
def test_no_changed_value_is_refused_with_exit_1_and_nothing_written(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...]
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    assert main(report_argv(run_dir, h4[0], *extra)) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused, nothing written: ReportError: ")
    assert "no supplied value differs from the manifest" in err
    assert markers(run_dir) == []
    assert not (run_dir / "reports").exists()


def test_valid_shared_memory_entries_reach_the_report(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    run_dir = clean_dir(h4, tmp_path)
    entries = {"qwen3-4b": "not_rising", "qwen3.5-4b": "rose"}
    flags = [part for slug, value in entries.items() for part in ("--shared-memory", f"{slug}={value}")]
    assert main(report_argv(run_dir, h4[0], *flags, "--shared-memory-evidence-sha256", "c" * 64)) == 0
    report = json_of(run_dir)
    for candidate_id in CANDIDATE_IDS:
        expected = entries.get(CANDIDATE_SLUGS[candidate_id], "not_observed")
        assert candidate(report, candidate_id)["shared_memory"] == expected


@pytest.mark.parametrize(
    ("flags", "not_loaded", "message"),
    [
        (("--shared-memory", "qwen9=rose"), (), "no candidate slug"),
        (("--shared-memory", "qwen3-8b=rose", "--shared-memory", "qwen3-8b=not_rising"), (), "twice"),
        (("--shared-memory", "qwen3-8b=rose"), (Q8B,), "not ready"),
        (("--shared-memory-evidence-sha256", "not-a-hash"), (), "64 lowercase hex"),
    ],
)
def test_bad_shared_memory_entries_are_refused_with_nothing_marked(
    h4: tuple[Path, GoldSet],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    flags: tuple[str, ...],
    not_loaded: tuple[str, ...],
    message: str,
) -> None:
    run_dir = clean_dir(h4, tmp_path, not_loaded=not_loaded)
    assert main(report_argv(run_dir, h4[0], *flags)) == 1
    assert message in capsys.readouterr().err
    assert markers(run_dir) == []
    assert not (run_dir / "reports").exists()


def expected_freeze(view: RunView, **changes: Any) -> bytes:
    data = {
        "gold_version": "v1.0",
        "gold_manifest": dict(view.manifest["gold_manifest"]),
        "hashes": copy.deepcopy(dict(view.manifest["hashes"])),
    }
    data.update(changes)
    return record_json(data) + b"\n"


def test_an_expected_freeze_file_is_read_and_checked(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    run_dir = clean_dir(h4, tmp_path)
    freeze = tmp_path / "expected.json"
    freeze.write_bytes(expected_freeze(load_run(str(run_dir))))
    assert main(report_argv(run_dir, h4[0], "--expected-freeze", str(freeze))) == 0
    assert json_of(run_dir)["gold_manifest_checked"] is True


@pytest.mark.parametrize("kind", ["digest", "malformed", "missing"])
def test_a_bad_expected_freeze_file_is_refused(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    freeze = tmp_path / "expected.json"
    view = load_run(str(run_dir))
    if kind == "digest":
        first = sorted(view.manifest["gold_manifest"])[0]
        freeze.write_bytes(expected_freeze(view, gold_manifest={**view.manifest["gold_manifest"], first: "0" * 64}))
    elif kind == "malformed":
        freeze.write_bytes(b"[]\n")
    assert main(report_argv(run_dir, h4[0], "--expected-freeze", str(freeze))) == 1
    assert capsys.readouterr().err.startswith("refused, nothing written: ")
    assert markers(run_dir) == []


# ---------------------------------------------------------------- --abandoned (G4-37)


def test_abandoned_directories_are_listed_readable_or_not(h4: tuple[Path, GoldSet], tmp_path: Path) -> None:
    run_dir = clean_dir(h4, tmp_path, not_loaded=CANDIDATE_IDS)
    other = stored(make_view(h4[1], marked=False, voids={(Q4B, "timed_run", 1): 1}), tmp_path, "bakeoff-1")
    score(run_dir, h4[0], abandoned=("bakeoff-1", "bakeoff-0"))
    listed = json_of(run_dir)["reopening"]["abandoned"]
    view = load_run(str(other))
    assert listed == [
        {
            "name": "bakeoff-1",
            "manifest_sha256": hashlib.sha256((other / "manifest.json").read_bytes()).hexdigest(),
            "runs": len(view.runs),
            "voids": 1,
            "truncated": 0,
            "unreadable": None,
        },
        {
            "name": "bakeoff-0",
            "manifest_sha256": None,
            "runs": 0,
            "voids": 0,
            "truncated": 0,
            "unreadable": "unreadable: StoreError",
        },
    ]


@pytest.mark.parametrize("name", ["../run_1", "runs/run_1", "", ".hidden", "run 1"])
def test_an_abandoned_name_that_is_not_a_run_name_is_refused(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    run_dir = clean_dir(h4, tmp_path, not_loaded=CANDIDATE_IDS)
    with pytest.raises(ReportError, match="not a run name"):
        score(run_dir, h4[0], abandoned=(name,))
    assert main(report_argv(run_dir, h4[0], "--abandoned", name)) == 1
    assert "not a run name" in capsys.readouterr().err
    assert not (run_dir / "reports").exists()


# ---------------------------------------------------------------- void (G4-44)


@pytest.fixture
def void_dir(h4: tuple[Path, GoldSet], tmp_path: Path) -> Path:
    """Run 2 of Qwen3-4B has a void first attempt, and Qwen3.5-4B's run 1 is marked."""
    view = make_view(h4[1], marked=False, voids={(Q4B, "timed_run", 2): 1})
    return stored(dataclasses.replace(view, scored=frozenset({RunKey(Q35, "timed_run", 1, 1)})), tmp_path)


@pytest.mark.parametrize(
    ("unit", "detail", "message"),
    [
        ("qwen3.5-4b.run1.a1", "operator note", "marked as scored"),
        ("qwen3-4b.run2.a1", "operator note", "already void"),
        ("qwen3-4b.pre_run.a1", "operator note", "pre-run"),
        ("qwen3-4b.run1.a2", "operator note", "no run record"),
        ("qwen3-4b.run1.a1", "caf" + chr(233), "non-blank ASCII"),
        ("qwen3-4b.run1.a1", "x" * 201, "non-blank ASCII"),
        ("qwen3-4b.run1.a1", "   ", "non-blank ASCII"),
        ("qwen3-4b.run4.a1", "operator note", "not a unit name"),
        ("qwen3-4b.pre_run.a1", "caf" + chr(233), "pre-run"),
        ("qwen3.5-4b.run1.a1", "caf" + chr(233), "marked as scored"),
        ("qwen3-4b.run1.a1", "see " + HOME_FORM + "notes", "must not hold a path"),
        ("qwen3-4b.run1.a1", "see C:" + chr(92) + "Us" + "ers" + chr(92) + "somebody", "must not hold a path"),
        ("qwen3-4b.run1.a1", "copied " + SCHEDULE_CANDIDATES[1].gguf_path, "must not hold a path"),
        ("qwen3.5-4b.run1.a1", "see " + HOME_FORM + "notes", "marked as scored"),
    ],
)
def test_void_refusals_leave_the_fault_and_void_logs_unchanged(
    void_dir: Path, capsys: pytest.CaptureFixture[str], unit: str, detail: str, message: str
) -> None:
    before = logs(void_dir)
    assert main(["void", "--run-dir", str(void_dir), "--unit", unit, "--detail", detail]) == 1
    assert message in capsys.readouterr().err
    assert logs(void_dir) == before


@pytest.mark.parametrize("detail", ["operator saw a stray process", "x" * 200])
def test_void_logs_an_operator_fault_and_the_next_report_is_records_only(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], detail: str
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    assert main(["void", "--run-dir", str(run_dir), "--unit", RUN_1, "--detail", detail]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == f"void: {RUN_1} with operator fault 1"
    assert "AMB-72" in out and "records-only" in out
    view = load_run(str(run_dir))
    key = RunKey(Q4B, "timed_run", 1, 1)
    assert [(f.fault_id, f.key, f.kind, f.item_id, f.detail) for f in view.faults] == [
        (1, key, "operator", None, detail)
    ]
    assert [(v.key, v.fault_id) for v in view.voids] == [(key, 1)]
    assert main(report_argv(run_dir, h4[0])) == 3
    report = json_of(run_dir)
    assert report["schedule_end"] == "incomplete"
    assert report["stop_reason"] == ["qwen3-4b.run1: no counted attempt"]
    assert report["reopening"]["voids"] == [{"unit": RUN_1, "fault_id": 1, "fault_kind": "operator", "detail": detail}]
    assert markers(run_dir) == []


def test_a_missing_run_dir_is_a_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(report_argv(tmp_path / "no-run", tmp_path / "no-gold")) == 1
    assert main(["void", "--run-dir", str(tmp_path / "no-run"), "--unit", RUN_1, "--detail", "x"]) == 1
    assert capsys.readouterr().err.count("refused, nothing written: StoreError") == 2


def test_a_refused_path_detail_leaves_later_reports_possible(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    before = logs(run_dir)
    argv = ["void", "--run-dir", str(run_dir), "--unit", RUN_1, "--detail", "see " + HOME_FORM + "notes"]
    assert main(argv) == 1
    assert "must not hold a path" in capsys.readouterr().err
    assert logs(run_dir) == before
    assert main(report_argv(run_dir, h4[0])) == 0
    assert json_of(run_dir)["reopening"]["faults_without_void"] == []


def test_a_unit_whose_scored_marker_is_cut_cannot_be_voided(void_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (void_dir / "scored" / f"{RUN_1}.marker").write_bytes(b"")
    before = logs(void_dir)
    assert main(["void", "--run-dir", str(void_dir), "--unit", RUN_1, "--detail", "operator note"]) == 1
    assert "marked as scored" in capsys.readouterr().err
    assert logs(void_dir) == before


# ---------------------------------------------------------------- cut markers and scoring again


def test_rescoring_with_a_cut_marker_sets_no_marker_again(
    rig: Rig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = copy_run(rig.run_dir, tmp_path)
    score(run_dir, rig.gold_dir)
    marker = run_dir / "scored" / f"{RUN_1}.marker"
    marker.write_bytes(b"")
    marked: list[RunKey] = []
    real_mark = RunStore.mark_scored

    def mark(self: RunStore, key: RunKey) -> None:
        marked.append(key)
        real_mark(self, key)

    monkeypatch.setattr(RunStore, "mark_scored", mark)
    assert main(report_argv(run_dir, rig.gold_dir)) == 0
    assert marked == []
    assert sorted(path.name for path in (run_dir / "reports").iterdir()) == [
        "report-1.json",
        "report-1.md",
        "report-2.json",
        "report-2.md",
    ]
    assert marker.read_bytes() == b""
    assert len(markers(run_dir)) == 15
    assert {"file_name": f"{RUN_1}.marker", "line_number": 1, "byte_count": 0} in json_of(run_dir, 2)["reopening"][
        "truncated"
    ]


# ---------------------------------------------------------------- refusals before any marker


@pytest.mark.parametrize(
    ("operator", "gold", "message"),
    [
        ({**VALID_OPERATOR, "bootstrap_seed": "recorded"}, "real", "operator.bootstrap_seed must be digits only"),
        (VALID_OPERATOR, "missing", "the gold set cannot be loaded: "),
    ],
)
def test_a_scored_path_refusal_before_marking_writes_nothing(
    h4: tuple[Path, GoldSet],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    operator: dict[str, str],
    gold: str,
    message: str,
) -> None:
    run_dir = clean_dir(h4, tmp_path, operator=operator)
    gold_dir = h4[0] if gold == "real" else tmp_path / "no-gold"
    with pytest.raises(ReportError, match=message):
        score(run_dir, gold_dir)
    assert main(report_argv(run_dir, gold_dir)) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused, nothing written: ReportError: ") and message in err
    assert markers(run_dir) == []
    assert not (run_dir / "reports").exists()


def store_state(run_dir: Path) -> tuple[tuple[bytes, bytes], list[str], bool]:
    return logs(run_dir), markers(run_dir), (run_dir / "reports").exists()


@pytest.mark.parametrize("route", ["operator", "reason", "quantization"])
def test_a_path_the_report_would_copy_is_refused_before_any_marker(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], route: str
) -> None:
    extra: tuple[str, ...] = ()
    if route == "operator":
        run_dir = clean_dir(h4, tmp_path, operator={**VALID_OPERATOR, "machine": HOME_FORM + "x"})
    elif route == "reason":
        run_dir = clean_dir(h4, tmp_path)
        extra = ("--deadline-sec", "130", "--reopening-reason", "notes in " + HOME_FORM + "x")
    else:
        view = make_view(h4[1], marked=False)
        manifest = copy.deepcopy(dict(view.manifest))
        manifest["candidates"][2]["quantization_commands"] = "quantize " + SCHEDULE_CANDIDATES[2].gguf_path
        run_dir = stored(dataclasses.replace(view, manifest=manifest), tmp_path)
    before = store_state(run_dir)
    assert main(report_argv(run_dir, h4[0], *extra)) == 1
    err = capsys.readouterr().err
    assert err.startswith("refused, nothing written: ReportError: ")
    assert "nothing is marked" in err and "a path reached the report body" in err
    assert store_state(run_dir) == before == (before[0], [], False)


# ---------------------------------------------------------------- failures after writing began (exit 1)


def assert_after_write(err: str, *phrases: str) -> None:
    assert err.startswith("failed after writing began: ")
    assert "nothing written" not in err
    for phrase in phrases:
        assert phrase in err


def test_a_report_body_refused_after_marking_keeps_the_markers_and_says_so(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = clean_dir(h4, tmp_path)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError("a path reached the report body; synthetic")

    monkeypatch.setattr(report_module, "build_report", refuse)
    assert main(report_argv(run_dir, h4[0])) == 1
    assert_after_write(capsys.readouterr().err, "ReportError", "the report body was refused", "markers")
    assert len(markers(run_dir)) == 15
    assert not (run_dir / "reports").exists()


def test_a_failure_in_evaluate_after_marking_says_markers_are_kept(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = clean_dir(h4, tmp_path)

    def evaluate(*_args: Any, **_kwargs: Any) -> Any:
        raise ReportError("synthetic unmarked unit")

    monkeypatch.setattr(gates_module, "evaluate", evaluate)
    assert main(report_argv(run_dir, h4[0])) == 1
    assert_after_write(capsys.readouterr().err, "ReportError: synthetic unmarked unit", "markers set before")
    assert len(markers(run_dir)) == 15


def test_a_failure_part_way_through_marking_says_markers_may_be_set(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    real_mark = RunStore.mark_scored
    calls: list[RunKey] = []

    def mark(self: RunStore, key: RunKey) -> None:
        calls.append(key)
        if len(calls) == 3:
            raise OSError("synthetic marker failure")
        real_mark(self, key)

    monkeypatch.setattr(RunStore, "mark_scored", mark)
    assert main(report_argv(run_dir, h4[0])) == 1
    assert_after_write(capsys.readouterr().err, "OSError: synthetic marker failure", "markers set before")
    assert len(markers(run_dir)) == 2
    assert not (run_dir / "reports").exists()


def failing_first_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """`write_report` with a sync that fails on the Markdown file, so the pair is left incomplete."""
    real = entry_module.write_report

    def write(run_dir: str, md: bytes, json_body: Any, **options: Any) -> tuple[str, str]:
        def sync(_fd: int) -> None:
            raise OSError("synthetic sync failure")

        return real(run_dir, md, json_body, **{**options, "fsync": sync})

    monkeypatch.setattr(entry_module, "write_report", write)


@pytest.mark.parametrize(
    ("not_loaded", "phrase", "marked"),
    [((), "markers set before", 15), (CANDIDATE_IDS, "no marker is set", 0)],
)
def test_a_report_write_failing_part_way_leaves_an_incomplete_number(
    h4: tuple[Path, GoldSet],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    not_loaded: tuple[str, ...],
    phrase: str,
    marked: int,
) -> None:
    run_dir = clean_dir(h4, tmp_path, not_loaded=not_loaded)
    with monkeypatch.context() as patch:
        failing_first_sync(patch)
        assert main(report_argv(run_dir, h4[0])) == 1
    assert_after_write(capsys.readouterr().err, "OSError: synthetic sync failure", phrase, "incomplete")
    assert len(markers(run_dir)) == marked
    assert (run_dir / "reports" / "report-1.md").exists()
    assert report_numbers(str(run_dir), lambda path: Path(path).read_bytes()) == ((1, False),)
    assert main(report_argv(run_dir, h4[0])) == (0 if marked else RECORDS_ONLY_EXIT)
    assert json_of(run_dir, 2)["reopening"]["incomplete_reports"] == [1]


def test_a_void_failing_after_its_fault_is_logged_says_so(
    h4: tuple[Path, GoldSet], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = clean_dir(h4, tmp_path)
    voids = logs(run_dir)[1]

    def void_run(self: RunStore, key: RunKey, fault_id: int) -> None:
        raise OSError("synthetic void failure")

    with monkeypatch.context() as patch:
        patch.setattr(RunStore, "void_run", void_run)
        assert main(["void", "--run-dir", str(run_dir), "--unit", RUN_1, "--detail", "operator note"]) == 1
    assert_after_write(capsys.readouterr().err, "OSError: synthetic void failure", "fault may be logged")
    view = load_run(str(run_dir))
    assert [(fault.key, fault.kind) for fault in view.faults] == [(RunKey(Q4B, "timed_run", 1, 1), "operator")]
    assert logs(run_dir)[1] == voids
    assert main(report_argv(run_dir, h4[0])) == 0
    assert [fault["unit"] for fault in json_of(run_dir)["reopening"]["faults_on_scored_units"]] == [RUN_1]


# ---------------------------------------------------------------- path needles


def test_a_filesystem_root_parent_is_no_needle(h4: tuple[Path, GoldSet]) -> None:
    view = make_view(h4[1])
    deeper = _path_needles(view, "/runs/run_1")
    assert deeper[-1] == "/runs"
    for run_dir in ("/run_1", "D:" + chr(92) + "run_1"):
        needles = _path_needles(view, run_dir)
        assert needles == deeper[:-1], run_dir
        assert not any(os.path.dirname(needle) == needle for needle in needles)
    report_module.check_paths(view, NO_INPUTS, (), (), _path_needles(view, "/run_1"))
