"""The gates: H4 spec G4-1 to G4-29 and G4-34, section 5's gate rows and the H4a brief's step A3 rows (S2 §3.6, §3.8,
§3.13, §5, §6, §9).

Every run directory is a `RunView` built in memory by `_h4_fixtures.make_view`, with synthetic calls and a synthetic
gold set, and every piece of machine evidence is an `AttemptEvidence` value. Nothing here touches a file but the gold
set under `tmp_path` and the one fixture round trip through a real `RunStore`.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from app.evaluation.bakeoff import gates
from app.evaluation.bakeoff import report as report_module
from app.evaluation.bakeoff.client import LLM_TIMEOUT_SEC, CallRecord
from app.evaluation.bakeoff.evidence import AttemptEvidence
from app.evaluation.bakeoff.gates import (
    C_MAX,
    D5_RERUN_ALLOWANCE_SEC,
    D5_RUN_DEADLINE_SEC,
    GATES,
    HEADROOM_MIB,
    J_AFTER_RETRY_MAX,
    J_FIRST_ATTEMPT_MAX,
    J_PROBE_MAX,
    MIN_CAP_CLAIMS,
    MODEL_SHARE,
    PASSED,
    PROBE_READINGS,
    RAM_CAP_BYTES,
    RUN_CONDITION_FIELDS,
    SINGLE_OVER_NS,
    SYSMEM_POLICY,
    Check,
    Gate,
    OperatorValues,
    ReportError,
    ReportInputs,
    check_records,
    check_report_inputs,
    counted_unit,
    evaluate,
    failed_run_conditions,
    gate_c,
    gate_j,
    gate_l,
    gate_m,
    gate_t,
    parse_expected_freeze,
    probe_readings,
    readings_disagree,
    schedule_end,
    session_runs,
    shared_memory_entries,
    summarise_field,
    units_to_mark,
)
from app.evaluation.bakeoff.gold import GoldSet
from app.evaluation.bakeoff.launch import CANDIDATE_IDS, StartupLog
from app.evaluation.bakeoff.records import (
    OPERATOR_FIELDS,
    RUNS,
    PreRunRecord,
    RunKey,
    RunManifest,
    RunRecord,
    RunView,
    StoredCall,
    StoreError,
    TruncatedLine,
    load_run,
    record_json,
)
from app.evaluation.bakeoff.runner import MAX_RUN_ATTEMPTS, ScheduleConfig, _manifest
from app.evaluation.bakeoff.scoring import gate_a, gate_s
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._h4_fixtures import (
    CAP,
    PROBE,
    SECOND,
    SINGLE,
    STAMP,
    STARTUP,
    at,
    attempt_evidence,
    cap_output,
    contention_record,
    h4_gold,
    launch_record,
    loader,
    make_call,
    make_view,
    manifest_dict,
    passing_evidence,
    perfect_output,
    request_sha,
    store_view,
    with_call,
)
from app.evaluation.tests._runner_fixtures import (
    CONDITIONS,
    OPERATOR,
    SCHEDULE_CANDIDATES,
    VALID_OPERATOR,
    block_processes,
    frozen_inputs,
)

Q4B, Q35, Q8B = CANDIDATE_IDS
VALUES = OperatorValues.from_manifest(VALID_OPERATOR)
NO_POLICY = dataclasses.replace(VALUES, sysmem_fallback="synthetic other setting")
NO_INPUTS = ReportInputs(MappingProxyType({}), None, None, None, None, None)
SHA = "a" * 64
TIMED = tuple(RunKey(Q4B, "timed_run", run, 1) for run in RUNS)
CONTENDED = RunKey(Q4B, "contended", None, 1)
UNCONSTRAINED = RunKey(Q4B, "unconstrained", None, 1)
PRE_LAUNCH = launch_record(RunKey(Q4B, "pre_run", None, 1))
_DEFAULT: Any = object()


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture(scope="module")
def gold(tmp_path_factory: pytest.TempPathFactory) -> GoldSet:
    return h4_gold(tmp_path_factory.mktemp("gold"))


@pytest.fixture(scope="module")
def base(gold: GoldSet) -> RunView:
    return make_view(gold)


def timed(candidate: str, run: int, attempt: int = 1) -> RunKey:
    return RunKey(candidate, "timed_run", run, attempt)


def inputs(**changes: Any) -> ReportInputs:
    return dataclasses.replace(NO_INPUTS, **changes)


def results(
    view: RunView,
    gold: GoldSet,
    evidence: dict[RunKey, AttemptEvidence | None] | None = None,
    operator: OperatorValues = VALUES,
    report_inputs: ReportInputs = NO_INPUTS,
) -> dict[str, gates.CandidateResult]:
    return evaluate(view, gold, passing_evidence(view) if evidence is None else evidence, operator, report_inputs)


def statuses(view: RunView, gold: GoldSet, **kwargs: Any) -> dict[str, dict[str, str]]:
    return {c: {g: r.gates[g].status for g in GATES} for c, r in results(view, gold, **kwargs).items()}


def override_at(
    key: RunKey, role: str, session_index: int | None, position: int | None, call: Callable[[str], CallRecord]
) -> Callable[..., CallRecord | None]:
    """A `make_view` override that replaces one place's call."""

    def override(k: RunKey, r: str, index: int | None, pos: int | None, item: str) -> CallRecord | None:
        return call(item) if (k, r, index, pos) == (key, role, session_index, position) else None

    return override


def with_sha(call: CallRecord, sha: str) -> CallRecord:
    first = dataclasses.replace(call.attempts[0], request_sha256=sha)
    return dataclasses.replace(call, attempts=(first, *call.attempts[1:]))


# ---------------------------------------------------------------- constants and fixtures


def test_the_constants() -> None:
    assert GATES == ("L", "M", "J", "C", "T", "A", "S")
    assert (J_FIRST_ATTEMPT_MAX, J_AFTER_RETRY_MAX, J_PROBE_MAX, C_MAX) == (3, 1, 1, 1)
    assert (MODEL_SHARE, HEADROOM_MIB, RAM_CAP_BYTES, MIN_CAP_CLAIMS) == (Fraction(1, 2), 512, 4_000_000_000, 10)
    assert SINGLE_OVER_NS == int(LLM_TIMEOUT_SEC) * SECOND
    assert (D5_RUN_DEADLINE_SEC, D5_RERUN_ALLOWANCE_SEC) == (125, 25)
    assert set(RUN_CONDITION_FIELDS) <= set(OPERATOR_FIELDS)
    assert PROBE_READINGS == ("first_session", "every_session", "pooled", "per_item", "majority")
    assert PASSED == "passed"


def test_valid_operator_passes_from_manifest_and_the_placeholders_are_refused() -> None:
    assert VALUES.budget_mib == 6387
    assert VALID_OPERATOR["sysmem_fallback"] == SYSMEM_POLICY  # H4a brief section 11.1 C8
    with pytest.raises(ReportError):
        OperatorValues.from_manifest(OPERATOR)


def test_the_fixture_view_reads_back_through_a_real_store(gold: GoldSet, tmp_path: Path) -> None:
    view = make_view(gold, voids={(Q8B, "contended", None): 1})
    assert load_run(store_view(view, tmp_path)) == view


def test_the_fixture_view_is_complete_clean_and_passes_every_gate(base: RunView, gold: GoldSet) -> None:
    assert schedule_end(base) == "complete"
    check_records(base, gold, None)
    found = results(base, gold)
    assert all(result.passes_every_gate and result.eligible for result in found.values())


def test_the_fixture_loader_gives_its_table_and_none_elsewhere(base: RunView) -> None:
    evidence = passing_evidence(base)
    load = loader(evidence)
    assert load("run_1", TIMED[0], lambda path: b"") is evidence[TIMED[0]]
    assert load("run_1", UNCONSTRAINED, lambda path: b"") is None
    assert len(evidence) == 12 and all(key.phase in ("timed_run", "contended") for key in evidence)


def test_check_and_gate_refuse_what_is_no_figure() -> None:
    with pytest.raises(TypeError):
        Check("x", 1.5, 1, "<=", True)
    with pytest.raises(ValueError):
        Check("x", 1, 1, "!=", True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Gate("X", "pass", (), ())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Gate("L", "skipped", (), ())  # type: ignore[arg-type]


# ---------------------------------------------------------------- G4-6: operator values


def operator_with(**changes: str) -> dict[str, str]:
    return {**VALID_OPERATOR, **changes}


@pytest.mark.parametrize("name", ["run_deadline_sec", "rerun_allowance_sec"])
@pytest.mark.parametrize(
    ("text", "value"), [("125", Fraction(125)), ("125.0", Fraction(125)), ("62.5", Fraction(125, 2))]
)
def test_g4_6_seconds_are_plain_decimals(name: str, text: str, value: Fraction) -> None:
    parsed = OperatorValues.from_manifest(operator_with(**{name: text}))
    assert getattr(parsed, name) == value


@pytest.mark.parametrize("name", ["run_deadline_sec", "rerun_allowance_sec"])
@pytest.mark.parametrize("text", ["1.25e2", "1e2", " 125", "125 ", "abc", "0", "0.0", "-1", "inf", "nan", "125.", ".5"])
def test_g4_6_bad_seconds_are_refused(name: str, text: str) -> None:
    with pytest.raises(ReportError):
        OperatorValues.from_manifest(operator_with(**{name: text}))


@pytest.mark.parametrize(
    "name",
    ["idle_free_vram_mib", "audio_pipeline_vram_mib", "audio_vram_samples", "audio_vram_min_per_sec", "bootstrap_seed"],
)
@pytest.mark.parametrize("text", ["125.0", "1.25e2", "1e2", " 125", "125 ", "abc", "-1", "+5", ""])
def test_g4_6_counts_are_digits_only(name: str, text: str) -> None:
    with pytest.raises(ReportError):
        OperatorValues.from_manifest(operator_with(**{name: text}))


@pytest.mark.parametrize(
    ("idle", "audio", "budget"), [("1513", "1000", 1), ("7899", "1000", 6387), ("7899", "0", 7387)]
)
def test_g4_16_the_budget(idle: str, audio: str, budget: int) -> None:
    parsed = OperatorValues.from_manifest(operator_with(idle_free_vram_mib=idle, audio_pipeline_vram_mib=audio))
    assert parsed.budget_mib == budget


@pytest.mark.parametrize(("idle", "audio"), [("1512", "1000"), ("0", "0"), ("512", "0")])
def test_g4_16_a_budget_of_0_or_less_is_refused(idle: str, audio: str) -> None:
    with pytest.raises(ReportError, match="budget"):
        OperatorValues.from_manifest(operator_with(idle_free_vram_mib=idle, audio_pipeline_vram_mib=audio))
    with pytest.raises(ReportError):
        _ = dataclasses.replace(VALUES, idle_free_mib=int(idle), audio_vram_mib=int(audio)).budget_mib


@pytest.mark.parametrize(("text", "accepted"), [("9", False), ("10", True), ("20", True)])
def test_g4_6_the_audio_rate_floor(text: str, accepted: bool) -> None:
    if accepted:
        assert OperatorValues.from_manifest(operator_with(audio_vram_min_per_sec=text)).audio_vram_min_per_sec == int(
            text
        )
    else:
        with pytest.raises(ReportError, match="at least 10"):
            OperatorValues.from_manifest(operator_with(audio_vram_min_per_sec=text))


@pytest.mark.parametrize(
    ("text", "entries"),
    [
        ("model-a", ("model-a",)),
        ("org/model-a@0123abcd;org/model-b", ("org/model-a@0123abcd", "org/model-b")),
        ("org/model@sha256:" + "a" * 64, ("org/model@sha256:" + "a" * 64,)),
    ],
)
def test_amb_135_prompt_development_models_are_a_list(text: str, entries: tuple[str, ...]) -> None:
    assert (
        OperatorValues.from_manifest(operator_with(prompt_development_models=text)).prompt_development_models == entries
    )


@pytest.mark.parametrize("text", ["", ";", "a;;b", "a;", "a b", "a@", "@rev", "a@b@c", "mod" + chr(233) + "le", " a"])
def test_amb_135_a_bad_model_list_is_refused(text: str) -> None:
    with pytest.raises(ReportError, match="prompt_development_models"):
        OperatorValues.from_manifest(operator_with(prompt_development_models=text))


@pytest.mark.parametrize(
    "name", ["section2_qwen3_4b", "section2_qwen3_5_4b", "card_settings_qwen3_8b", "cuda_process_check"]
)
def test_g4_6_named_fields_must_not_be_blank(name: str) -> None:
    with pytest.raises(ReportError, match=name):
        OperatorValues.from_manifest(operator_with(**{name: "  "}))


def test_g4_6_a_missing_field_is_refused() -> None:
    operator = dict(VALID_OPERATOR)
    del operator["bootstrap_seed"]
    with pytest.raises(ReportError, match="bootstrap_seed"):
        OperatorValues.from_manifest(operator)
    with pytest.raises(ReportError):
        OperatorValues.from_manifest(["not", "a", "mapping"])  # type: ignore[arg-type]


def test_g4_6_the_parsed_values() -> None:
    assert VALUES.section2 == dict.fromkeys(CANDIDATE_IDS, "passed")
    assert list(VALUES.card_settings) == list(CANDIDATE_IDS)
    assert VALUES.card_settings[Q35] == VALID_OPERATOR["card_settings_qwen3_5_4b"]
    assert VALUES.prompt_development_models == ("synthetic/model-a", "synthetic/model-b@0123abcd")
    assert (VALUES.bootstrap_seed, VALUES.audio_vram_samples, VALUES.audio_vram_min_per_sec) == (20261109, 200, 10)
    assert VALUES.run_conditions == dict.fromkeys(RUN_CONDITION_FIELDS, "passed")
    assert VALUES.run_conditions_met


def test_amb_143_a_deadline_other_than_d5_is_accepted_for_the_report_to_flag() -> None:
    parsed = OperatorValues.from_manifest(operator_with(run_deadline_sec="130"))
    assert parsed.run_deadline_sec == 130 != D5_RUN_DEADLINE_SEC


def test_g4_6_a_slug_shaped_key_is_refused_by_the_manifest(gold: GoldSet) -> None:
    fields = {k: v for k, v in manifest_dict(gold).items() if k not in ("schema", "record", "stamp")}
    operator = dict(VALID_OPERATOR)
    operator["section2_qwen3.5-4b"] = operator.pop("section2_qwen3_5_4b")
    with pytest.raises(StoreError):
        RunManifest(**{**fields, "operator": operator})


# ---------------------------------------------------------------- G4-43: run conditions


@pytest.mark.parametrize("name", RUN_CONDITION_FIELDS)
@pytest.mark.parametrize("value", ["failed", "Passed", " passed", "passed ", "not recorded: no capture"])
def test_g4_43_a_run_condition_not_exactly_passed_is_named(name: str, value: str) -> None:
    assert failed_run_conditions(operator_with(**{name: value})) == (name,)
    parsed = OperatorValues.from_manifest(operator_with(**{name: value}))
    assert not parsed.run_conditions_met


def test_g4_43_every_failing_field_is_named_and_a_missing_one_fails() -> None:
    assert failed_run_conditions(VALID_OPERATOR) == ()
    assert failed_run_conditions(OPERATOR) == RUN_CONDITION_FIELDS
    operator = dict(VALID_OPERATOR)
    del operator["namespace_lan_check"]
    assert failed_run_conditions(operator) == ("namespace_lan_check",)


# ---------------------------------------------------------------- G4-1 (b): the expected-freeze file


def freeze_bytes(gold: GoldSet, **changes: Any) -> bytes:
    manifest = manifest_dict(gold)
    body = {"gold_version": "v1.0", "gold_manifest": manifest["gold_manifest"], "hashes": manifest["hashes"]}
    return record_json({**body, **changes}) + b"\n"


def test_g4_1_the_expected_freeze_file_parses(gold: GoldSet) -> None:
    expected = parse_expected_freeze(freeze_bytes(gold))
    assert expected.gold_version == "v1.0"
    assert dict(expected.gold_manifest) == dict(gold.manifest)
    assert dict(expected.hashes) == manifest_dict(gold)["hashes"]


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        '{"gold_version": "v1.0' + chr(233) + '"}',
        b'{"gold_version": "v1.0", "gold_version": "v1.0", "gold_manifest": {}, "hashes": {}}',
        b'{"gold_version": "v1.0", "gold_manifest": {}}',
        b'{"gold_version": "v1.0", "gold_manifest": {}, "hashes": {}, "extra": 1}',
        b'{"gold_version": 1, "gold_manifest": {}, "hashes": {}}',
        b'{"gold_version": "v1.0", "gold_manifest": {"a": 1}, "hashes": {}}',
        b'{"gold_version": "v1.0", "gold_manifest": {}, "hashes": {}}',
    ],
)
def test_g4_1_a_malformed_freeze_file_is_refused(raw: bytes | str) -> None:
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    with pytest.raises(ReportError):
        parse_expected_freeze(data)


def test_g4_1_freeze_hashes_need_every_key_and_their_shape(gold: GoldSet) -> None:
    hashes = dict(manifest_dict(gold)["hashes"])
    del hashes["warmup_sha256"]
    with pytest.raises(ReportError):
        parse_expected_freeze(freeze_bytes(gold, hashes=hashes))
    hashes = {**manifest_dict(gold)["hashes"], "requests": "flat"}
    with pytest.raises(ReportError):
        parse_expected_freeze(freeze_bytes(gold, hashes=hashes))


# ---------------------------------------------------------------- G4-2: schedule end


def pre_run(candidate: str, status: str, **changes: Any) -> PreRunRecord:
    blocked = ("context",) if status == "blocked" else ()
    fields: dict[str, Any] = {"thinking": "off" if status == "ready" else None, **changes}
    return PreRunRecord(STAMP, candidate, status, SHA, None, fields["thinking"], (), blocked)  # type: ignore[arg-type]


def only_runs(view: RunView, keep: Callable[[RunKey], bool]) -> RunView:
    """`view` with the calls and run records of the units `keep` refuses dropped."""
    return dataclasses.replace(
        view,
        calls=tuple(stored for stored in view.calls if keep(stored.key)),
        runs=tuple(run for run in view.runs if keep(run.key)),
    )


def blocked(view: RunView) -> RunView:
    return dataclasses.replace(view, pre_run=(pre_run(Q4B, "blocked"), *view.pre_run[1:]))


TRUNCATED_CALLS = TruncatedLine("calls.jsonl", 900, 40)


def schedule_rows(gold: GoldSet) -> dict[str, tuple[RunView, str]]:
    base = make_view(gold)
    exhausted = make_view(gold, voids={(Q4B, "timed_run", 1): 3})
    first_only = dataclasses.replace(base, pre_run=(pre_run(Q4B, "not_loaded"),), calls=(), runs=(), launches=())
    third_void_cut = dataclasses.replace(exhausted, runs=tuple(r for r in exhausted.runs if r.key != timed(Q4B, 1, 3)))
    return {
        "clean": (base, "complete"),
        "blocked pre-run": (blocked(base), "pre_run_stopped"),
        "blocked plus a truncated tail": (
            dataclasses.replace(blocked(base), truncated=(TRUNCATED_CALLS,)),
            "pre_run_stopped",
        ),
        "all not_loaded": (make_view(gold, not_loaded=CANDIDATE_IDS), "pre_run_stopped"),
        "first not_loaded, second missing": (first_only, "incomplete"),
        "first not_loaded, second truncated": (
            dataclasses.replace(first_only, truncated=(TruncatedLine("pre_run.jsonl", 2, 30),)),
            "incomplete",
        ),
        "three voids": (exhausted, "runs_exhausted"),
        "three voids, later candidates absent": (
            only_runs(exhausted, lambda key: key.candidate == Q4B and key.phase == "timed_run" and key.run == 1),
            "runs_exhausted",
        ),
        "three voids plus a truncated tail": (
            dataclasses.replace(exhausted, truncated=(TRUNCATED_CALLS,)),
            "runs_exhausted",
        ),
        "third void's run record missing": (third_void_cut, "incomplete"),
        "third void's run record truncated": (
            dataclasses.replace(third_void_cut, truncated=(TruncatedLine("runs.jsonl", 40, 12),)),
            "incomplete",
        ),
        "truncated calls.jsonl": (dataclasses.replace(base, truncated=(TRUNCATED_CALLS,)), "incomplete"),
        "a cut scored marker": (
            dataclasses.replace(base, truncated=(TruncatedLine("qwen3-4b.run1.a1.marker", 1, 0),)),
            "complete",
        ),
        "a timed run with no record": (only_runs(base, lambda key: key != timed(Q8B, 3)), "incomplete"),
        "a timed run voided once, then complete": (
            make_view(gold, voids={(Q35, "timed_run", 2): 2}),
            "complete",
        ),
        "an ungated unit with no record": (
            only_runs(base, lambda key: key != RunKey(Q35, "contended", None, 1)),
            "incomplete",
        ),
        "an ungated unit voided three times": (make_view(gold, voids={(Q35, "contended", None): 3}), "complete"),
        "an ungated unit voided twice, no third record": (
            only_runs(
                make_view(gold, voids={(Q35, "unconstrained", None): 3}),
                lambda key: key != RunKey(Q35, "unconstrained", None, 3),
            ),
            "incomplete",
        ),
        "precedence: blocked, three voids and a cut tail": (
            dataclasses.replace(blocked(exhausted), truncated=(TRUNCATED_CALLS,)),
            "pre_run_stopped",
        ),
        "precedence: three voids and a missing unit": (
            only_runs(exhausted, lambda key: key != RunKey(Q8B, "contended", None, 1)),
            "runs_exhausted",
        ),
    }


SCHEDULE_ROWS = (
    "clean",
    "blocked pre-run",
    "blocked plus a truncated tail",
    "all not_loaded",
    "first not_loaded, second missing",
    "first not_loaded, second truncated",
    "three voids",
    "three voids, later candidates absent",
    "three voids plus a truncated tail",
    "third void's run record missing",
    "third void's run record truncated",
    "truncated calls.jsonl",
    "a cut scored marker",
    "a timed run with no record",
    "a timed run voided once, then complete",
    "an ungated unit with no record",
    "an ungated unit voided three times",
    "an ungated unit voided twice, no third record",
    "precedence: blocked, three voids and a cut tail",
    "precedence: three voids and a missing unit",
)


@pytest.fixture(scope="module")
def schedules(gold: GoldSet) -> dict[str, tuple[RunView, str]]:
    return schedule_rows(gold)


@pytest.mark.parametrize("row", SCHEDULE_ROWS)
def test_g4_2_schedule_end(schedules: dict[str, tuple[RunView, str]], row: str) -> None:
    view, end = schedules[row]
    assert schedule_end(view) == end


def test_g4_2_every_row_is_tested(schedules: dict[str, tuple[RunView, str]]) -> None:
    assert set(schedules) == set(SCHEDULE_ROWS)


# The trigger each records-only row's stop reason must name. report reads the schedule through the gates helpers, so
# the reason cannot disagree with `schedule_end` without one of these rows failing.
STOP_TRIGGERS = MappingProxyType(
    {
        "blocked pre-run": "qwen3-4b.pre_run blocked",
        "blocked plus a truncated tail": "qwen3-4b.pre_run blocked",
        "all not_loaded": "no candidate's pre-run was ready",
        "first not_loaded, second missing": "no pre-run record: qwen3.5-4b",
        "first not_loaded, second truncated": "truncated: pre_run.jsonl line 2",
        "three voids": "qwen3-4b.run1: 3 void attempts",
        "three voids, later candidates absent": "qwen3-4b.run1: 3 void attempts",
        "three voids plus a truncated tail": "qwen3-4b.run1: 3 void attempts",
        "third void's run record missing": "qwen3-4b.run1: no counted attempt",
        "third void's run record truncated": "truncated: runs.jsonl line 40",
        "truncated calls.jsonl": "truncated: calls.jsonl line 900",
        "a timed run with no record": "qwen3-8b.run3: no counted attempt",
        "an ungated unit with no record": "qwen3.5-4b.contended: neither a complete attempt",
        "an ungated unit voided twice, no third record": (
            f"qwen3.5-4b.unconstrained: neither a complete attempt nor {MAX_RUN_ATTEMPTS} voids"
        ),
        "precedence: blocked, three voids and a cut tail": "qwen3-4b.pre_run blocked",
        "precedence: three voids and a missing unit": "qwen3-4b.run1: 3 void attempts",
    }
)


@pytest.mark.parametrize("row", SCHEDULE_ROWS)
def test_g4_2_the_records_only_stop_reason_names_the_schedule_end_trigger(
    schedules: dict[str, tuple[RunView, str]], row: str
) -> None:
    view, end = schedules[row]
    if end == "complete":
        assert row not in STOP_TRIGGERS
        return
    body = report_module.build_report(
        view, end, None, None, None, None, NO_INPUTS, (), "a" * 64, (), run_name="run_1", report_numbers=()
    )
    reasons = body["stop_reason"]
    assert reasons and reasons != ["incomplete"]
    assert any(STOP_TRIGGERS[row] in reason for reason in reasons), reasons


def test_g4_2_ungated_units_end_at_the_runner_attempt_cap(gold: GoldSet) -> None:
    capped = make_view(gold, voids={(Q35, "contended", None): MAX_RUN_ATTEMPTS})
    assert schedule_end(capped) == "complete"
    short = only_runs(capped, lambda key: key != RunKey(Q35, "contended", None, MAX_RUN_ATTEMPTS))
    assert schedule_end(short) == "incomplete"


# ---------------------------------------------------------------- G4-1: integrity checks


def without_call(view: RunView, key: RunKey, role: str, index: int | None, position: int | None) -> RunView:
    """`view` with one stored call gone and its run record counting one call fewer."""
    gone = at(view, key, role, index, position)
    runs = tuple(dataclasses.replace(r, calls=r.calls - 1) if r.key == key else r for r in view.runs)
    return dataclasses.replace(view, calls=tuple(s for s in view.calls if s is not gone), runs=runs)


def moved(view: RunView, key: RunKey, role: str, index: int | None, place: int | None, **changes: Any) -> RunView:
    old = at(view, key, role, index, place)
    new = dataclasses.replace(old, **changes)
    return dataclasses.replace(view, calls=tuple(new if s is old else s for s in view.calls))


def manifest_with(view: RunView, **changes: Any) -> RunView:
    return dataclasses.replace(view, manifest={**view.manifest, **changes})


def integrity_rows(view: RunView, gold: GoldSet) -> dict[str, tuple[RunView, str]]:
    single = at(view, timed(Q4B, 1), "single", None, 1)
    unconstrained_single = at(view, UNCONSTRAINED, "single", None, 1)
    item = single.call.item_id
    digests = dict(view.manifest["gold_manifest"])
    digests["items.jsonl"] = "0" * 64
    counted = tuple(dataclasses.replace(r, calls=r.calls + 1) if r.key == timed(Q8B, 2) else r for r in view.runs)
    return {
        "manifest gold version v0.4": (manifest_with(view, gold_version="v0.4"), "gold version"),
        "manifest gold manifest differs": (manifest_with(view, gold_manifest=digests), "gold manifest"),
        "a constrained request hash wrong": (
            with_call(view, timed(Q4B, 1), "single", None, 1, with_sha(single.call, "0" * 64)),
            "frozen request",
        ),
        "an unconstrained request hash wrong": (
            with_call(view, UNCONSTRAINED, "single", None, 1, with_sha(unconstrained_single.call, "0" * 64)),
            "frozen request",
        ),
        "a constrained call carrying the unconstrained hash": (
            with_call(view, timed(Q4B, 1), "single", None, 1, with_sha(single.call, request_sha(item, False))),
            "frozen request",
        ),
        "an unconstrained call carrying the constrained hash": (
            with_call(
                view, UNCONSTRAINED, "single", None, 1, with_sha(unconstrained_single.call, request_sha(item, True))
            ),
            "frozen request",
        ),
        "a probe missing": (without_call(view, timed(Q35, 2), "probe", 3, 7), "1 missing"),
        "a cap call missing": (without_call(view, timed(Q35, 3), "cap_session", 4, None), "1 missing"),
        "a contended probe missing": (
            without_call(view, RunKey(Q8B, "contended", None, 1), "probe", 1, 20),
            "1 missing",
        ),
        "an unconstrained probe missing": (without_call(view, UNCONSTRAINED, "probe", None, 1), "1 missing"),
        "a place twice": (moved(view, timed(Q4B, 3), "probe", 2, 5, position=6), "twice"),
        "a run record miscounted": (dataclasses.replace(view, runs=counted), "counts"),
        "a single of another conversation": (
            with_call(view, timed(Q4B, 2), "single", None, 1, make_call("syn_9", SINGLE)),
            "one per gold conversation",
        ),
        "a probe in another session's id": (
            moved(view, timed(Q4B, 1), "probe", 1, 4, session_id="cap_2"),
            "session id",
        ),
        "two complete attempts of a run": (
            dataclasses.replace(view, runs=(*view.runs, RunRecord(STAMP, timed(Q4B, 1, 2), "complete", 0))),
            "2 complete",
        ),
        "two complete contended attempts": (
            dataclasses.replace(
                view, runs=(*view.runs, RunRecord(STAMP, RunKey(Q4B, "contended", None, 2), "complete", 0))
            ),
            "2 complete",
        ),
        "two run records of one attempt": (
            dataclasses.replace(view, runs=(*view.runs, view.runs[0])),
            "two run records",
        ),
    }


INTEGRITY_ROWS = (
    "manifest gold version v0.4",
    "manifest gold manifest differs",
    "a constrained request hash wrong",
    "an unconstrained request hash wrong",
    "a constrained call carrying the unconstrained hash",
    "an unconstrained call carrying the constrained hash",
    "a probe missing",
    "a cap call missing",
    "a contended probe missing",
    "an unconstrained probe missing",
    "a place twice",
    "a run record miscounted",
    "a single of another conversation",
    "a probe in another session's id",
    "two complete attempts of a run",
    "two complete contended attempts",
    "two run records of one attempt",
)


@pytest.fixture(scope="module")
def integrity(base: RunView, gold: GoldSet) -> dict[str, tuple[RunView, str]]:
    return integrity_rows(base, gold)


@pytest.mark.parametrize("row", INTEGRITY_ROWS)
def test_g4_1_each_refusal(integrity: dict[str, tuple[RunView, str]], gold: GoldSet, row: str) -> None:
    view, message = integrity[row]
    with pytest.raises(ReportError, match=message):
        check_records(view, gold, None)


def test_g4_1_every_row_is_tested(integrity: dict[str, tuple[RunView, str]]) -> None:
    assert set(integrity) == set(INTEGRITY_ROWS)


def test_g4_1_a_gold_set_of_another_version_is_refused(base: RunView, gold: GoldSet) -> None:
    other = dataclasses.replace(gold, version="v0.4")
    with pytest.raises(ReportError, match="gold set differs"):
        check_records(base, other, None)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("renamed copy", "gold manifest differs from the recorded freeze"),
        ("extra key", "gold manifest differs from the recorded freeze"),
        ("missing key", "gold manifest differs from the recorded freeze"),
        ("version", "recorded freeze is 'v0.4'"),
        ("request hash", "request hashes differ"),
    ],
)
def test_g4_1_the_expected_freeze_is_compared_exactly(base: RunView, gold: GoldSet, change: str, message: str) -> None:
    manifest = dict(base.manifest["gold_manifest"])
    hashes = dict(base.manifest["hashes"])
    version = "v1.0"
    if change == "renamed copy":
        manifest["items.jsonl"] = "0" * 64
    elif change == "extra key":
        manifest["notes.txt"] = "0" * 64
    elif change == "missing key":
        del manifest["items.csv"]
    elif change == "version":
        version = "v0.4"
    else:
        hashes["requests"] = {**hashes["requests"], "probe_01": "0" * 64}
    expected = parse_expected_freeze(freeze_bytes(gold, gold_version=version, gold_manifest=manifest, hashes=hashes))
    with pytest.raises(ReportError, match=message):
        check_records(base, gold, expected)


def test_g4_1_an_exact_expected_freeze_passes(base: RunView, gold: GoldSet) -> None:
    check_records(base, gold, parse_expected_freeze(freeze_bytes(gold)))


def test_g4_1_check_records_marks_nothing_on_disk(gold: GoldSet, tmp_path: Path) -> None:
    view = make_view(gold, marked=False)
    loaded = load_run(path := store_view(view, tmp_path))
    single = at(loaded, timed(Q4B, 1), "single", None, 1).call
    bad = with_call(loaded, timed(Q4B, 1), "single", None, 1, with_sha(single, "0" * 64))
    with pytest.raises(ReportError):
        check_records(bad, gold, None)
    assert list(Path(path, "scored").iterdir()) == []


def test_g4_1_a_candidate_that_did_not_load_is_not_checked(gold: GoldSet) -> None:
    check_records(make_view(gold, not_loaded=(Q35,)), gold, None)


# ---------------------------------------------------------------- G4-3, G4-4: units to mark and the guard


def test_g4_3_units_to_mark_in_order(base: RunView) -> None:
    expected = [
        key
        for candidate in CANDIDATE_IDS
        for key in (
            *(timed(candidate, run) for run in RUNS),
            RunKey(candidate, "unconstrained", None, 1),
            RunKey(candidate, "contended", None, 1),
        )
    ]
    assert list(units_to_mark(base)) == expected


def test_g4_4_counted_units_skip_voids_and_absent_units(gold: GoldSet) -> None:
    view = make_view(
        gold, voids={(Q4B, "contended", None): 2, (Q8B, "unconstrained", None): 3, (Q35, "timed_run", 1): 1}
    )
    assert counted_unit(view, Q4B, "contended") == RunKey(Q4B, "contended", None, 3)
    assert counted_unit(view, Q8B, "unconstrained") is None
    marks = units_to_mark(view)
    assert timed(Q35, 1, 2) in marks and timed(Q35, 1, 1) not in marks
    assert RunKey(Q8B, "unconstrained", None, 3) not in marks and len(marks) == 14
    with pytest.raises(ValueError):
        counted_unit(view, Q4B, "timed_run")


def test_g4_3_evaluate_refuses_an_unmarked_unit(gold: GoldSet) -> None:
    view = make_view(gold, marked=False)
    with pytest.raises(ReportError, match="not marked"):
        results(view, gold)
    marked = make_view(gold)
    one_short = dataclasses.replace(marked, scored=marked.scored - {RunKey(Q8B, "contended", None, 1)})
    with pytest.raises(ReportError, match="qwen3-8b.contended.a1 is not marked"):
        results(one_short, gold)


def test_amb_91_a_cut_marker_counts_as_marked(base: RunView, gold: GoldSet) -> None:
    key = RunKey(Q8B, "contended", None, 1)
    cut = dataclasses.replace(
        base, scored=base.scored - {key}, truncated=(TruncatedLine("qwen3-8b.contended.a1.marker", 1, 0),)
    )
    assert results(cut, gold)[Q8B].passes_every_gate


def test_evaluate_refuses_a_blocked_pre_run(base: RunView, gold: GoldSet) -> None:
    with pytest.raises(ReportError, match="blocked"):
        results(blocked(base), gold)


# ---------------------------------------------------------------- session-runs


def test_session_runs_of_a_timed_run_and_the_contended_unit(base: RunView) -> None:
    sessions = session_runs(base.counted_calls(Q4B, 1), 1)
    assert [(s.run, s.session_index, s.session_id) for s in sessions] == [(1, n, f"cap_{n}") for n in range(1, 5)]
    assert all([p.position for p in s.probes] == list(range(1, 21)) for s in sessions)
    assert (sessions[0].total_ns, sessions[0].claims, sessions[0].cap_valid) == (60 * SECOND, 12, True)
    contended = session_runs(tuple(s for s in base.calls if s.key == CONTENDED and s.role != "warmup"), None)
    assert [(s.run, s.session_index, len(s.probes)) for s in contended] == [(None, 1, 20)]


def test_session_runs_refuse_orphan_probes_and_two_caps(base: RunView) -> None:
    calls = base.counted_calls(Q4B, 1)
    with pytest.raises(ReportError, match="no cap-session call"):
        session_runs(tuple(s for s in calls if not (s.role == "cap_session" and s.session_index == 2)), 1)
    cap = next(s for s in calls if s.role == "cap_session")
    with pytest.raises(ReportError, match="two cap-session calls"):
        session_runs((*calls, cap), 1)


# ---------------------------------------------------------------- G4-8, G4-9: gate J


def singles_of(*outcomes: str) -> tuple[StoredCall, ...]:
    return tuple(
        StoredCall(n, STAMP, TIMED[0], "single", None, None, n, make_call(f"syn_{n}", SINGLE, outcome))
        for n, outcome in enumerate(outcomes, start=1)
    )


def probes_of(*outcomes: str, **options: Any) -> tuple[StoredCall, ...]:
    return tuple(
        StoredCall(n, STAMP, TIMED[0], "probe", "cap_1", 1, n, make_call(f"probe_{n:02d}", PROBE, outcome, **options))
        for n, outcome in enumerate(outcomes, start=1)
    )


CLEAN_PROBES = probes_of(*["valid"] * 20)


@pytest.mark.parametrize(
    ("singles", "passes"),
    [
        (("valid",) * 5, True),
        (("repaired", "repaired", "timeout_then_valid", "valid", "valid"), True),  # G4-8's example: 3 and 0
        (("repaired", "repaired", "timeout_then_valid", "cut_off_repaired", "valid"), False),  # 4 first attempts
        (("repaired", "repaired", "timeout", "valid", "valid"), True),  # 3 first attempts plus 1 after retry
        (("repaired", "repaired", "repaired", "timeout", "valid"), False),  # one more first attempt
        (("repaired", "repaired", "timeout", "invalid", "valid"), False),  # one more after retry
        (("invalid", "valid", "valid", "valid", "valid"), True),
        (("invalid", "invalid", "valid", "valid", "valid"), False),
        (("cut_off", "cut_off", "valid", "valid", "valid"), False),
        (("timeout", "timeout", "valid", "valid", "valid"), False),
        (("server_error", "server_error", "valid", "valid", "valid"), False),
        (("server_error", "valid", "valid", "valid", "valid"), True),
    ],
)
def test_g4_8_gate_j_singles(singles: tuple[str, ...], passes: bool) -> None:
    gate = gate_j(singles_of(*singles), CLEAN_PROBES, len(singles))
    assert (gate.gate, gate.status) == ("J", "pass" if passes else "fail")


@pytest.mark.parametrize("kind", ["invalid", "cut_off", "timeout", "server_error"])
@pytest.mark.parametrize(("failures", "passes"), [(1, True), (2, False)])
def test_g4_9_gate_j_probes(kind: str, failures: int, passes: bool) -> None:
    probes = probes_of(*[kind] * failures, *["valid"] * (20 - failures))
    gate = gate_j(singles_of("valid"), probes, 1)
    assert gate.status == ("pass" if passes else "fail")
    assert gate.checks[-1] == Check("probe_failures", failures, 1, "<=", passes)


def test_g4_8_gate_j_names_its_counts() -> None:
    gate = gate_j(singles_of("repaired", "timeout", "valid"), CLEAN_PROBES, 4)
    assert [(c.name, c.value, c.limit, c.passed) for c in gate.checks] == [
        ("singles", 3, 4, False),
        ("single_first_attempt_failures", 2, J_FIRST_ATTEMPT_MAX, True),
        ("single_failures_after_retry", 1, J_AFTER_RETRY_MAX, True),
        ("probe_failures", 0, 1, True),
    ]
    assert gate.status == "fail"


# ---------------------------------------------------------------- G4-10, G4-12: gate C


@pytest.mark.parametrize(
    ("outcome", "options", "fails_c", "fails_j"),
    [
        ("valid", {}, False, False),
        ("valid", {"cited": ()}, True, False),  # cites none (AMB-27)
        ("repaired", {"cited": ("src_9",), "unknown": ("src_9",)}, True, False),  # F86: a junk id, then repaired
        ("timeout_then_valid", {}, True, False),  # no response, so no citation
        ("cut_off_repaired", {}, True, False),
        ("repaired", {}, False, False),  # an invalid first attempt that cites a bundle id
        ("timeout", {}, True, True),
    ],
)
def test_g4_10_the_gate_c_test_on_a_first_attempt(
    outcome: str, options: dict[str, Any], fails_c: bool, fails_j: bool
) -> None:
    one = probes_of(outcome, **options)
    two = probes_of(outcome, outcome, **options)
    assert gate_c(one).checks[0].value == int(fails_c)
    assert gate_c(one).status == "pass"
    assert gate_c(two).status == ("fail" if fails_c else "pass")
    assert gate_j(singles_of("valid"), two, 1).status == ("fail" if fails_j else "pass")


def test_g4_12_gate_c_on_the_unconstrained_pass_is_reported_only(gold: GoldSet) -> None:
    def override(key: RunKey, role: str, index: int | None, position: int | None, item: str) -> CallRecord | None:
        if key == UNCONSTRAINED and role == "probe" and position is not None and position <= 14:
            return make_call(item, PROBE, "repaired", constrained=False, cited=() if position <= 3 else ("src_1",))
        return None

    result = results(make_view(gold, override=override), gold)[Q4B]
    block = result.unconstrained
    assert block is not None
    assert (block["label"], block["probe_c_failures"], block["probe_c_passes"]) == ("reported, not gated", 3, False)
    validity = block["raw_validity"][PROBE]
    assert (validity["valid_first_attempts"], validity["n"]) == (6, 20)
    assert validity["first_attempt_failures"] == {"invalid_output": 14, "cut_off": 0, "timeout": 0, "server_error": 0}
    assert block["raw_validity"][SINGLE]["valid_first_attempts"] == 5
    assert result.gates["C"].status == "pass" and result.passes_every_gate


# ---------------------------------------------------------------- G4-11: AMB-66 readings


def failing_probes(gold: GoldSet, layout: frozenset[tuple[int, int]]) -> RunView:
    """Run 1 of Qwen3-4B with the probes at (set, item) timing out on both attempts, so J and C move together."""

    def override(key: RunKey, role: str, index: int | None, position: int | None, item: str) -> CallRecord | None:
        if key == TIMED[0] and role == "probe" and (index, position) in layout:
            return make_call(item, PROBE, "timeout")
        return None

    return make_view(gold, override=override)


AMB_66_ROWS = [
    # layout of (set, item) failures: first, every, pooled, per_item, majority
    ({(1, 1), (1, 2)}, (False, False, True, False, True)),
    ({(1, 3), (3, 1), (3, 2)}, (True, False, True, False, True)),  # G4-11's example
    ({(1, 1), (2, 2), (2, 3), (3, 4), (4, 5)}, (True, False, False, False, True)),
    ({(1, 1), (2, 2)}, (True, True, True, False, True)),
    ({(1, 1), (2, 1), (3, 1), (2, 2), (3, 2), (4, 2)}, (True, False, False, False, False)),
    ({(1, 1)}, (True, True, True, True, True)),
    ({(1, 4), (2, 5), (2, 6)}, (True, False, True, False, True)),  # section 5: 1 in session 1, 2 in session 2
]


@pytest.mark.parametrize(("layout", "expected"), AMB_66_ROWS)
def test_g4_11_each_reading(gold: GoldSet, layout: set[tuple[int, int]], expected: tuple[bool, ...]) -> None:
    view = failing_probes(gold, frozenset(layout))
    readings = probe_readings(session_runs(view.counted_calls(Q4B, 1), 1))
    assert readings == {reading: (passes, passes) for reading, passes in zip(PROBE_READINGS, expected, strict=True)}
    assert readings_disagree(readings) == (len(set(expected)) > 1)


@pytest.mark.parametrize(
    ("layout", "j", "flagged"),
    [
        ({(1, 3), (3, 1), (3, 2)}, "pass", True),
        ({(1, 4), (2, 5), (2, 6)}, "pass", True),
        ({(1, 1)}, "pass", False),
        ({(1, 1), (1, 2)}, "fail", True),
    ],
)
def test_amb_66_gates_j_and_c_read_the_first_session_and_flag_a_disagreement(
    gold: GoldSet, layout: set[tuple[int, int]], j: str, flagged: bool
) -> None:
    result = results(failing_probes(gold, frozenset(layout)), gold)[Q4B]
    assert (result.gates["J"].status, result.gates["C"].status) == (j, j)
    note = "AMB-66 reading changes gate J or C"
    assert (note in result.gates["J"].notes, note in result.gates["C"].notes) == (flagged, flagged)
    assert set(result.probe_readings) == set(PROBE_READINGS)


# ---------------------------------------------------------------- G4-13, G4-14: gate T


def sessions_with(gold: GoldSet, override: Callable[..., CallRecord | None]) -> tuple[gates.SessionRun, ...]:
    view = make_view(gold, override=override)
    return tuple(session for run in RUNS for session in session_runs(view.counted_calls(Q4B, run), run))


def cap_at(run: int, index: int, call: Callable[[str], CallRecord]) -> Callable[..., CallRecord | None]:
    return override_at(timed(Q4B, run), "cap_session", index, None, call)


@pytest.mark.parametrize(
    ("cap", "passes"),
    [
        (lambda item: make_call(item, CAP, wall_ns=(42_500_000_000,), output=cap_output(item)), True),  # 62.5 s
        (lambda item: make_call(item, CAP, wall_ns=(42_500_000_001,), output=cap_output(item)), False),
        (lambda item: make_call(item, CAP, wall_ns=(40 * SECOND,), output=cap_output(item, 9)), False),
        (lambda item: make_call(item, CAP, wall_ns=(40 * SECOND,), output=cap_output(item, 10)), True),
        (lambda item: make_call(item, CAP, wall_ns=(40 * SECOND,), output=cap_output(item, 11)), True),
        (lambda item: make_call(item, CAP, "invalid", wall_ns=(10 * SECOND, 10 * SECOND)), False),
        (lambda item: make_call(item, CAP, "timeout", wall_ns=(10 * SECOND, 10 * SECOND)), False),  # retry timed out
        (
            lambda item: make_call(item, CAP, "repaired", wall_ns=(10 * SECOND, 10 * SECOND), output=cap_output(item)),
            True,
        ),
    ],
    ids=["62.5 s", "62.5 s + 1 ns", "9 claims", "10 claims", "11 claims", "invalid", "retry timeout", "repaired"],
)
def test_g4_13_one_session_run(gold: GoldSet, cap: Callable[[str], CallRecord], passes: bool) -> None:
    gate = gate_t(sessions_with(gold, cap_at(2, 3, cap)), Fraction(125))
    assert gate.status == ("pass" if passes else "fail")
    failed = [check.name for check in gate.checks if not check.passed]
    assert bool(failed) != passes and all(name.startswith("run2.session3.") for name in failed)


def test_g4_13_a_failed_probe_s_time_counts_and_j_is_untouched(gold: GoldSet) -> None:
    probe = override_at(
        timed(Q4B, 2), "probe", 3, 7, lambda item: make_call(item, PROBE, "timeout", wall_ns=(30 * SECOND,) * 2)
    )
    view = make_view(gold, override=probe)
    result = results(view, gold)[Q4B]
    assert result.gates["T"].status == "fail"
    assert [c.name for c in result.gates["T"].checks if not c.passed] == ["run2.session3.twice_total_ns"]
    assert result.gates["J"].status == "pass"
    assert result.latency is not None
    assert result.latency["session_runs"]["run2.session3"]["total_ns"] == 119 * SECOND


def test_g4_13_twelve_session_runs_are_needed(gold: GoldSet, base: RunView) -> None:
    sessions = tuple(s for run in RUNS for s in session_runs(base.counted_calls(Q4B, run), run))
    assert gate_t(sessions, Fraction(125)).status == "pass"
    gate = gate_t(sessions[:11], Fraction(125))
    assert gate.status == "fail" and gate.checks[0] == Check("session_runs", 11, 12, "==", False)


def test_g4_14_a_deadline_override_recomputes_gate_t(gold: GoldSet) -> None:
    slow = cap_at(1, 2, lambda item: make_call(item, CAP, wall_ns=(41 * SECOND,), output=cap_output(item)))  # 61 s
    view = make_view(gold, override=slow)
    assert results(view, gold)[Q4B].gates["T"].status == "pass"
    reopened = results(view, gold, report_inputs=inputs(deadline_override_sec=Fraction(120), reopening_reason="D5"))
    assert reopened[Q4B].gates["T"].status == "fail"
    assert reopened[Q4B].gates["T"].notes == ("model budget: 60 s per session-run",)


def test_g4_14_an_allowance_change_changes_no_gate_t_result(base: RunView, gold: GoldSet) -> None:
    before = results(base, gold)
    after = results(base, gold, report_inputs=inputs(rerun_allowance_override_sec=Fraction(30), reopening_reason="x"))
    assert {c: r.gates for c, r in before.items()} == {c: r.gates for c, r in after.items()}


# ---------------------------------------------------------------- G4-7: gate L


@pytest.mark.parametrize(
    ("section2", "status", "passes"),
    [
        ("passed", "ready", True),
        ("failed: licence not read at pinned revision", "ready", False),
        ("Passed", "ready", False),
        ("", "ready", False),
        ("passed", "not_loaded", False),
    ],
)
def test_g4_7_gate_l(section2: str, status: str, passes: bool) -> None:
    operator = dataclasses.replace(VALUES, section2={**VALUES.section2, Q35: section2})
    gate = gate_l(operator, Q35, pre_run(Q35, status))
    assert gate.status == ("pass" if passes else "fail")
    assert gate.checks[0] == Check("section2", section2, "passed", "==", section2 == "passed")
    assert ("not loaded" in " ".join(gate.notes)) == (status == "not_loaded")


def test_g4_7_a_gate_l_failure_is_information_only_and_every_figure_is_computed(base: RunView, gold: GoldSet) -> None:
    operator = OperatorValues.from_manifest(operator_with(section2_qwen3_5_4b="failed: licence not read"))
    result = results(base, gold, operator=operator)[Q35]
    assert result.gates["L"].status == "fail" and result.information_only == ("gate_L",)
    assert not result.eligible and not result.passes_every_gate
    assert set(result.metrics) == {1, 2, 3} and all(result.gates[g].status == "pass" for g in GATES[1:])


@pytest.mark.parametrize("candidate", CANDIDATE_IDS)
def test_c17_a_candidate_that_did_not_load(gold: GoldSet, candidate: str) -> None:
    result = results(make_view(gold, not_loaded=(candidate,)), gold)
    assert list(result) == list(CANDIDATE_IDS)
    loaded = result[candidate]
    assert (loaded.status, loaded.information_only, loaded.eligible) == ("not_loaded", ("gate_L",), False)
    assert loaded.gates["L"].status == "fail"
    assert all(loaded.gates[g].status == "not_measured" for g in GATES[1:])
    assert (dict(loaded.metrics), loaded.memory, loaded.contended) == ({}, None, None)
    assert all(r.passes_every_gate for c, r in result.items() if c != candidate)


# ---------------------------------------------------------------- G4-24: thinking (AMB-116)


def test_g4_24_a_pre_run_verdict_of_seen_makes_the_candidate_information_only(gold: GoldSet) -> None:
    result = results(make_view(gold, thinking={Q4B: "seen"}), gold)[Q4B]
    assert result.information_only == ("thinking",) and result.thinking_pre_run == "seen"
    assert result.passes_every_gate and not result.eligible


@pytest.mark.parametrize(
    ("key", "role", "index", "position", "count"),
    [
        (timed(Q4B, 2), "probe", 3, 1, "run2.constrained_timed"),
        (timed(Q4B, 1), "single", None, 2, "run1.constrained_timed"),
        (CONTENDED, "probe", 1, 5, "contended"),
        (UNCONSTRAINED, "probe", None, 5, "unconstrained"),
    ],
)
def test_amb_116_thinking_in_a_run_is_counted_and_changes_no_eligibility(
    gold: GoldSet, key: RunKey, role: str, index: int | None, position: int | None, count: str
) -> None:
    constrained = key.phase != "unconstrained"
    kind = PROBE if role == "probe" else SINGLE

    def call(item: str) -> CallRecord:
        output = None if kind == PROBE else perfect_output(gold, item)
        return make_call(item, kind, constrained=constrained, thinking=True, output=output)

    result = results(make_view(gold, override=override_at(key, role, index, position, call)), gold)[Q4B]
    expected = {f"run{run}.constrained_timed": 0 for run in RUNS} | {"contended": 0, "unconstrained": 0}
    assert dict(result.thinking_in_runs) == {**expected, count: 1}
    assert result.eligible and result.thinking_pre_run == "off"


# ---------------------------------------------------------------- G4-23: gates A and S


def test_g4_23_gates_a_and_s_are_h1_s_gates(base: RunView, gold: GoldSet) -> None:
    def empty(key: RunKey, role: str, index: int | None, position: int | None, item: str) -> CallRecord | None:
        return make_call(item, SINGLE) if key == TIMED[0] and role == "single" else None

    for view in (base, make_view(gold, override=empty)):
        result = results(view, gold)[Q4B]
        for name, h1 in (("A", gate_a(result.metrics[1])), ("S", gate_s(result.metrics[1]))):
            gate = result.gates[name]
            assert gate.status == ("pass" if h1.passed else "fail")
            assert [(c.name, c.value, c.limit, c.op, c.passed) for c in gate.checks] == [
                (c.name, c.value, c.limit, c.op, c.passed) for c in h1.checks
            ]
    degraded = results(make_view(gold, override=empty), gold)[Q4B]
    assert degraded.gates["A"].status == "fail" and degraded.metrics[1].recall == 0
    assert degraded.metrics[2].recall == 1  # runs 2 and 3 are scored, not gated


def test_g4_5_a_single_that_timed_out_scores_as_empty_output(gold: GoldSet) -> None:
    view = make_view(
        gold, override=override_at(TIMED[0], "single", None, 3, lambda item: make_call(item, SINGLE, "timeout"))
    )
    metrics = results(view, gold)[Q4B].metrics[1]
    assert [(f.source_id, f.kind) for f in metrics.failed_conversations] == [("syn_3", "timeout")]
    assert metrics.n_checkable_units == 81 and metrics.found_units == 61


# ---------------------------------------------------------------- G4-16 to G4-22: gate M


def evidence_of(rise: int = 4304, **options: Any) -> dict[RunKey, AttemptEvidence | None]:
    return {key: attempt_evidence(key, used=(1000, 1000 + rise), **options) for key in TIMED}


def m_gate(
    *,
    timed_evidence: Any = _DEFAULT,
    contended: Any = _DEFAULT,
    present: bool = True,
    launch: Any = PRE_LAUNCH,
    operator: OperatorValues = VALUES,
    shared: str = "not_observed",
    sha: str | None = None,
) -> Gate:
    if timed_evidence is _DEFAULT:
        timed_evidence = evidence_of()
    if contended is _DEFAULT:
        contended = attempt_evidence(CONTENDED, contention=contention_record())
    return gate_m(timed_evidence, contended, present, launch, operator, shared, sha)  # type: ignore[arg-type]


def check_of(gate: Gate, name: str) -> Check:
    return next(check for check in gate.checks if check.name == name)


@pytest.mark.parametrize(
    ("operator", "budget"), [(VALUES, 6387), (dataclasses.replace(VALUES, audio_vram_mib=0), 7387)]
)
@pytest.mark.parametrize(("extra", "status"), [(0, "pass"), (1, "fail")])
def test_g4_17_the_rise_against_the_budget(operator: OperatorValues, budget: int, extra: int, status: str) -> None:
    gate = m_gate(timed_evidence=evidence_of(budget + extra), operator=operator)
    assert gate.status == status
    assert check_of(gate, "server_rise_mib") == Check("server_rise_mib", budget + extra, budget, "<=", not extra)


def test_g4_17_the_peak_over_runs_is_the_gate_figure() -> None:
    evidence = {
        TIMED[0]: attempt_evidence(TIMED[0], used=(1000, 4000, 2000)),
        TIMED[1]: attempt_evidence(TIMED[1], used=(500, 5900)),
        TIMED[2]: attempt_evidence(TIMED[2], used=(1000, 5000)),
    }
    assert check_of(m_gate(timed_evidence=evidence), "server_rise_mib").value == 5400


def test_amb_123_a_voided_attempt_with_the_highest_peak_is_listed_never_counted(gold: GoldSet) -> None:
    view = make_view(gold, voids={(Q4B, "timed_run", 2): 1})
    evidence = passing_evidence(view)
    evidence[timed(Q4B, 2, 1)] = attempt_evidence(timed(Q4B, 2, 1), used=(1000, 9999))
    result = results(view, gold, evidence)[Q4B]
    assert result.gates["M"].status == "pass"
    assert check_of(result.gates["M"], "server_rise_mib").value == 4304
    assert result.memory is not None and result.memory["voided"]["qwen3-4b.run2.a1"]["rise_mib"] == 8999
    assert "qwen3-4b.run2.a2" in result.memory["attempts"]
    assert result.voided == (timed(Q4B, 2, 1),)


@pytest.mark.parametrize(("rise", "status"), [(4192, "fail"), (4193, "pass")])
def test_g4_18_a_rise_below_the_cuda_buffers_fails(rise: int, status: str) -> None:
    gate = m_gate(timed_evidence=evidence_of(rise))
    assert gate.status == status
    assert check_of(gate, "rise_vs_cuda_buffers_mib").limit == Fraction("4192.92")


def test_g4_18_buffers_count_the_recurrent_state() -> None:
    startup = dataclasses.replace(
        STARTUP, cuda_model_mib=2572.86, cuda_kv_mib=384.0, cuda_recurrent_mib=50.25, cuda_compute_mib=86.02
    )
    gate = m_gate(launch=launch_record(RunKey(Q4B, "pre_run", None, 1), startup))
    assert check_of(gate, "rise_vs_cuda_buffers_mib").limit == Fraction("3093.13")


@pytest.mark.parametrize(("offloaded", "status"), [(37, "pass"), (36, "fail")])
def test_g4_18_every_layer_on_the_gpu(offloaded: int, status: str) -> None:
    launch = launch_record(RunKey(Q4B, "pre_run", None, 1), dataclasses.replace(STARTUP, layers_offloaded=offloaded))
    assert m_gate(launch=launch).status == status


@pytest.mark.parametrize(
    "launch",
    [None, launch_record(RunKey(Q4B, "pre_run", None, 1), StartupLog(3, 1, 12288, 18080, *(None,) * 8))],
    ids=["no pre-run launch", "default verbosity"],
)
def test_g4_18_no_buffer_figures_is_not_measured(launch: Any) -> None:
    gate = m_gate(launch=launch)
    assert gate.status == "not_measured"


@pytest.mark.parametrize(("kib", "status"), [(3_906_250, "pass"), (3_906_251, "fail")])
def test_g4_19_the_ram_cap(kib: int, status: str) -> None:
    timed_evidence = evidence_of()
    timed_evidence[TIMED[1]] = attempt_evidence(TIMED[1], used=(1000, 5304), rss_kib=(200_000, kib, 100_000))
    gate = m_gate(timed_evidence=timed_evidence)
    assert gate.status == status
    assert check_of(gate, "rss_anon_bytes.qwen3-4b.run2.a1").value == kib * 1024


def test_g4_19_no_rss_sample_is_not_measured() -> None:
    timed_evidence = evidence_of()
    timed_evidence[TIMED[2]] = attempt_evidence(TIMED[2], used=(1000, 5304), rss_kib=())
    assert m_gate(timed_evidence=timed_evidence).status == "not_measured"


def test_g4_20_swap_in_a_timed_attempt_fails_and_names_it() -> None:
    timed_evidence = evidence_of()
    timed_evidence[TIMED[1]] = attempt_evidence(TIMED[1], used=(1000, 5304), pswpout=(1204, 1216))
    gate = m_gate(timed_evidence=timed_evidence)
    assert gate.status == "fail"
    assert check_of(gate, "swap_delta.qwen3-4b.run2.a1") == Check("swap_delta.qwen3-4b.run2.a1", 12, 0, "==", False)


def test_g4_17_a_counted_attempt_with_no_evidence_is_not_measured() -> None:
    timed_evidence = evidence_of()
    timed_evidence[TIMED[0]] = None
    gate = m_gate(timed_evidence=timed_evidence)
    assert gate.status == "not_measured"
    assert check_of(gate, "server_rise_mib").value is None
    assert "no host evidence for qwen3-4b.run1.a1: not measured" in gate.notes


def test_g4_17_a_measured_failure_outranks_a_missing_figure() -> None:
    timed_evidence = evidence_of(rise=7000)
    gate = m_gate(timed_evidence=timed_evidence, present=False, contended=None)
    assert gate.status == "fail"


def test_g4_17_the_loader_s_none_reaches_the_gate_through_evaluate(base: RunView, gold: GoldSet) -> None:
    evidence = passing_evidence(base)
    evidence[timed(Q8B, 3)] = None
    found = results(base, gold, evidence)
    assert found[Q8B].gates["M"].status == "not_measured" and not found[Q8B].passes_every_gate
    assert found[Q4B].gates["M"].status == "pass"
    assert found[Q8B].memory is not None and found[Q8B].memory["attempts"]["qwen3-8b.run3.a1"] == {"evidence": False}


@pytest.mark.parametrize(
    ("operator", "shared", "sha", "status"),
    [
        (NO_POLICY, "not_rising", SHA, "pass"),
        (NO_POLICY, "not_rising", None, "not_measured"),  # no counter log hash
        (NO_POLICY, "rose", SHA, "fail"),
        (NO_POLICY, "not_observed", SHA, "not_measured"),
        (VALUES, "rose", SHA, "pass"),  # the policy text is machine-wide
        (VALUES, "not_observed", None, "pass"),
        (dataclasses.replace(VALUES, sysmem_fallback=" Prefer No Sysmem Fallback "), "rose", None, "pass"),
        (
            dataclasses.replace(VALUES, sysmem_fallback="prefer no sysmem fallback"),
            "not_observed",
            None,
            "not_measured",
        ),
    ],
)
def test_g4_21_shared_memory(operator: OperatorValues, shared: str, sha: str | None, status: str) -> None:
    gate = m_gate(operator=operator, shared=shared, sha=sha)
    assert gate.status == status
    assert f"shared memory: {shared}" in gate.notes


def test_g4_21_shared_memory_is_judged_per_candidate(base: RunView, gold: GoldSet) -> None:
    entries = shared_memory_entries([("qwen3-8b", "rose"), ("qwen3-4b", "not_rising"), ("qwen3.5-4b", "not_rising")])
    found = statuses(
        base, gold, operator=NO_POLICY, report_inputs=inputs(shared_memory=entries, shared_memory_evidence_sha256=SHA)
    )
    assert {c: s["M"] for c, s in found.items()} == {Q4B: "pass", Q35: "pass", Q8B: "fail"}
    missing = shared_memory_entries([("qwen3-4b", "not_rising"), ("qwen3.5-4b", "not_rising")])
    found = statuses(
        base, gold, operator=NO_POLICY, report_inputs=inputs(shared_memory=missing, shared_memory_evidence_sha256=SHA)
    )
    assert {c: s["M"] for c, s in found.items()} == {Q4B: "pass", Q35: "pass", Q8B: "not_measured"}


def test_g4_22_an_absent_together_session_is_not_measured_and_needs_a_repeat() -> None:
    gate = m_gate(contended=None, present=False)
    assert gate.status == "not_measured"
    assert check_of(gate, "together_session.swap_delta").value is None
    assert any("together session absent" in note and "needs a repeat" in note for note in gate.notes)


def test_g4_22_a_together_session_with_no_host_evidence_is_not_measured() -> None:
    gate = m_gate(contended=None, present=True)
    assert gate.status == "not_measured"
    assert any("without host evidence" in note for note in gate.notes)


def test_g4_22_a_together_session_with_no_rss_sample_is_not_measured() -> None:
    gate = m_gate(contended=attempt_evidence(CONTENDED, rss_kib=(), contention=contention_record()))
    assert gate.status == "not_measured"
    assert any("no RSS sample" in note for note in gate.notes)


def test_g4_22_swap_in_the_together_session_fails_naming_it() -> None:
    gate = m_gate(contended=attempt_evidence(CONTENDED, pswpout=(10, 11), contention=contention_record()))
    assert gate.status == "fail"
    assert not check_of(gate, "swap_delta.qwen3-4b.contended.a1").passed


def test_g4_22_a_clean_together_session_passes_and_its_combined_rise_is_reported() -> None:
    gate = m_gate(contended=attempt_evidence(CONTENDED, used=(500, 8000), contention=contention_record()))
    assert gate.status == "pass"  # 7,500 MiB is above idle free - 512, but the combined rise is reported only
    assert any("combined rise 7500 MiB against idle free - 512 = 7387 MiB" in note for note in gate.notes)


def test_g4_22_a_partial_together_session_still_counts_and_is_labelled() -> None:
    gate = m_gate(contended=attempt_evidence(CONTENDED, contention=contention_record(20_000)))
    assert gate.status == "pass"
    assert "contention partial: audio ended 20.000 s before stop" in gate.notes
    swapping = attempt_evidence(CONTENDED, pswpout=(1, 2), contention=contention_record(1_500))
    gate = m_gate(contended=swapping)
    assert gate.status == "fail" and "contention partial: audio ended 1.500 s before stop" in gate.notes


def test_g4_22_three_contended_voids_through_evaluate(gold: GoldSet) -> None:
    view = make_view(gold, voids={(Q8B, "contended", None): 3})
    result = results(view, gold)[Q8B]
    assert result.gates["M"].status == "not_measured" and not result.passes_every_gate
    assert result.gates["T"].status == "pass"
    assert result.contended is not None
    assert (result.contended["status"], result.contended["needs_repeat"]) == ("absent", True)
    assert result.contended["voided"] == ("qwen3-8b.contended.a1", "qwen3-8b.contended.a2", "qwen3-8b.contended.a3")


def test_g4_22_the_contended_figures_are_reported_not_gated(base: RunView, gold: GoldSet) -> None:
    evidence = passing_evidence(base)
    evidence[RunKey(Q35, "contended", None, 1)] = attempt_evidence(
        RunKey(Q35, "contended", None, 1), used=(700, 6000), contention=contention_record(3_000)
    )
    block = results(base, gold, evidence)[Q35].contended
    assert block is not None
    assert block["status"] == "partial" and block["label"] == "reported, not gated"
    assert (block["session_total_ns"], block["per_row_ns"], block["claims"]) == (60 * SECOND, 3 * SECOND, 12)
    assert (block["cap_valid"], block["within_model_budget"], block["evidence"]) == (True, True, True)
    assert (block["combined_rise_mib"], block["combined_limit_mib"], block["ended_before_stop_ms"]) == (
        5300,
        7387,
        3000,
    )


# ---------------------------------------------------------------- G4-14, G4-21: report inputs


def test_g4_21_valid_inputs_pass(base: RunView) -> None:
    entries = shared_memory_entries([("qwen3-4b", "rose"), ("qwen3-8b", "not_observed")])
    check_report_inputs(base, VALUES, inputs(shared_memory=entries, shared_memory_evidence_sha256=SHA))
    check_report_inputs(base, VALUES, NO_INPUTS)


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"qwen3-9b": "rose"}, "no candidate slug"),
        ({"Qwen/Qwen3-4B": "rose"}, "no candidate slug"),
        ({"qwen3.5-4b": "rose"}, "not ready"),
        ({"qwen3-4b": "risen"}, "not one of"),
    ],
)
def test_g4_21_bad_slugs_are_refused(gold: GoldSet, entries: dict[str, str], message: str) -> None:
    view = make_view(gold, not_loaded=(Q35,))
    with pytest.raises(ReportError, match=message):
        check_report_inputs(view, VALUES, inputs(shared_memory=entries))


def test_g4_21_a_duplicate_slug_or_a_bad_value_is_refused() -> None:
    with pytest.raises(ReportError, match="twice"):
        shared_memory_entries([("qwen3-4b", "rose"), ("qwen3-4b", "not_rising")])
    with pytest.raises(ReportError, match="not one of"):
        shared_memory_entries([("qwen3-4b", "fell")])
    assert dict(shared_memory_entries([("qwen3-4b", "rose")])) == {"qwen3-4b": "rose"}


def test_g4_21_a_bad_evidence_hash_is_refused(base: RunView) -> None:
    with pytest.raises(ReportError, match="64 lowercase hex"):
        check_report_inputs(base, VALUES, inputs(shared_memory_evidence_sha256="A" * 64))


@pytest.mark.parametrize(
    ("deadline", "allowance", "reason", "message"),
    [
        (Fraction(120), None, "D5 changed", None),
        (None, Fraction(30), "D5 changed", None),
        (Fraction(120), Fraction(30), "D5 changed", None),
        (Fraction(125), Fraction(30), "D5 changed", None),  # one value differs
        (Fraction("125.0"), None, "D5 changed", "no supplied value differs"),  # amendment 3: 125.0 against 125
        (Fraction(125), Fraction(25), "D5 changed", "no supplied value differs"),
        (Fraction(120), None, None, "needs --reopening-reason"),
        (Fraction(120), None, "   ", "needs --reopening-reason"),
        (None, None, "D5 changed", "needs --deadline-sec"),
        (Fraction(0), None, "D5 changed", "above 0"),
        (Fraction(-1), None, "D5 changed", "above 0"),
    ],
)
def test_g4_14_the_reopening_values(
    base: RunView, deadline: Fraction | None, allowance: Fraction | None, reason: str | None, message: str | None
) -> None:
    given = inputs(deadline_override_sec=deadline, rerun_allowance_override_sec=allowance, reopening_reason=reason)
    if message is None:
        check_report_inputs(base, VALUES, given)
    else:
        with pytest.raises(ReportError, match=message):
            check_report_inputs(base, VALUES, given)


# ---------------------------------------------------------------- G4-34: clocks, temperature and power


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            ("1530 MHz", "1545 MHz", "1500 MHz"),
            {"n": 3, "readable": 3, "min": 1500, "median": 1530, "max": 1545, "units": ("MHz",)},
        ),
        (
            ("45", "47", "46", "48"),
            {"n": 4, "readable": 4, "min": 45, "median": Fraction(93, 2), "max": 48, "units": ()},
        ),
        (
            ("12.34 W",),
            {
                "n": 1,
                "readable": 1,
                "min": Fraction("12.34"),
                "median": Fraction("12.34"),
                "max": Fraction("12.34"),
                "units": ("W",),
            },
        ),
        (("[N/A]", "[N/A]"), {"n": 2, "readable": 0, "not_readable": "not readable (2 of 2)"}),
        (
            ("[N/A]", "30 W", "10 W", "Unknown Error"),
            {
                "n": 4,
                "readable": 2,
                "min": 10,
                "median": 20,
                "max": 30,
                "units": ("W",),
                "not_readable": "not readable (2 of 4)",
            },
        ),
        (("-5", "1e3", ""), {"n": 3, "readable": 0, "not_readable": "not readable (3 of 3)"}),
        ((), {"n": 0, "readable": 0}),
    ],
    ids=["numeric", "even count", "decimal", "all N/A", "mixed", "not decimals", "empty"],
)
def test_g4_34_summarise_field(values: tuple[str, ...], expected: dict[str, Any]) -> None:
    assert dict(summarise_field(values)) == expected


def test_g4_34_the_power_summary_reaches_the_memory_block(base: RunView, gold: GoldSet) -> None:
    memory = results(base, gold)[Q4B].memory
    assert memory is not None
    attempt = memory["attempts"]["qwen3-4b.run1.a1"]
    assert attempt["power"] == {"n": 21, "readable": 0, "not_readable": "not readable (21 of 21)"}
    assert attempt["clocks_gr"]["median"] == 1530


# ---------------------------------------------------------------- reported-only figures (G4-15, G4-36, G4-40)


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (list(range(1, 21)), (20, 10, 19, 20)),
        ([7], (1, 7, 7, 7)),
        ([3, 1, 2], (3, 2, 3, 3)),
        (list(range(1, 101)), (100, 50, 95, 100)),
        (list(range(1, 102)), (101, 51, 96, 101)),
    ],
)
def test_amb_119_nearest_rank_distributions(values: list[int], expected: tuple[int, ...]) -> None:
    figures = gates._distribution(values)
    assert figures is not None and (figures.n, figures.median, figures.p95, figures.max) == expected
    assert gates._distribution([]) is None


def test_g4_15_latency_figures(gold: GoldSet) -> None:
    slow_first = override_at(
        TIMED[0],
        "single",
        None,
        1,
        lambda item: make_call(item, SINGLE, wall_ns=(29 * SECOND,), output=perfect_output(gold, item)),
    )
    retried = override_at(
        timed(Q4B, 2),
        "single",
        None,
        2,
        lambda item: make_call(
            item, SINGLE, "timeout_then_valid", wall_ns=(30 * SECOND, 1_400_000_000), output=perfect_output(gold, item)
        ),
    )

    def both(*args: Any) -> CallRecord | None:
        return slow_first(*args) or retried(*args)

    latency = results(make_view(gold, override=both), gold)[Q4B].latency
    assert latency is not None
    assert latency["singles_over_30s"] == 1  # 31.4 s
    assert latency["longest_by_kind"][SINGLE]["unit"] == "qwen3-4b.run2.a1"
    assert latency["longest_by_kind"][SINGLE]["attempt_ns"] == (30 * SECOND, 1_400_000_000)
    assert latency["longest"]["role"] == "cap_session" and latency["longest"]["total_ns"] == 40 * SECOND
    singles = latency["calls"][SINGLE]
    assert (singles.n, singles.median, singles.max) == (15, SECOND, 31_400_000_000)
    assert latency["attempts"][SINGLE].n == 16
    assert latency["calls"][PROBE].n == 240 and latency["calls"][CAP].n == 12
    assert len(latency["session_runs"]) == 12
    assert latency["session_runs"]["run1.session1"]["per_row_ns"] == Fraction(3 * SECOND)


def test_g4_36_cut_offs_fences_junk_ids_and_graph_splits(gold: GoldSet) -> None:
    def override(key: RunKey, role: str, index: int | None, position: int | None, item: str) -> CallRecord | None:
        if key == TIMED[0] and role == "single" and position == 1:
            return make_call(item, SINGLE, "cut_off")
        if key == TIMED[0] and role == "single" and position == 2:
            return make_call(item, SINGLE, content="  " + "`" * 3 + "json\n{}", output=perfect_output(gold, item))
        if key == timed(Q4B, 3) and role == "probe" and index == 2 and position == 3:
            return make_call(item, PROBE, "repaired", cited=("src_9",), unknown=("src_9",))
        if key == UNCONSTRAINED and role == "cap_session" and index == 1:
            return make_call(item, CAP, "cut_off_repaired", constrained=False, output=cap_output(item))
        return None

    result = results(make_view(gold, override=override, startup=dataclasses.replace(STARTUP, graph_splits_tg=3)), gold)[
        Q4B
    ]
    assert result.cutoffs["run1.singles.attempts"] == 2 and result.cutoffs["run1.singles.calls"] == 1
    assert result.cutoffs["timed_run.single_conversation_extraction.calls"] == 1
    assert result.cutoffs["unconstrained.cap_session_extraction.attempts"] == 1
    assert result.cutoffs["unconstrained.cap_session_extraction.calls"] == 0
    assert result.runtime["fences"] == {"timed_run": 1, "unconstrained": 0, "contended": 0}
    assert result.runtime["junk_ids"] == {"timed_run": 1, "unconstrained": 0, "contended": 0}
    assert (result.runtime["graph_splits"], result.runtime["graph_splits_label"]) == (3, "operations on the CPU")
    assert result.runtime["template_parser"] == "auto"
    plain = results(make_view(gold), gold)[Q35].runtime
    assert (plain["graph_splits"], plain["graph_splits_label"], plain["template_parser"]) == (2, None, "qwen3_coder")


def test_g4_17_the_memory_block(base: RunView, gold: GoldSet) -> None:
    evidence = passing_evidence(base)
    result = results(base, gold, evidence)[Q4B]
    memory = result.memory
    assert memory is not None
    assert list(memory["attempts"]) == [
        "qwen3-4b.run1.a1",
        "qwen3-4b.run2.a1",
        "qwen3-4b.run3.a1",
        "qwen3-4b.contended.a1",
    ]
    attempt = memory["attempts"]["qwen3-4b.run2.a1"]
    figures = evidence[timed(Q4B, 2)]
    assert figures is not None
    assert attempt["sample_rate"] == figures.rate
    assert (attempt["baseline_used_mib"], attempt["baseline_free_mib"], attempt["idle_free_mib"]) == (1000, 7899, 7899)
    assert (attempt["peak_used_mib"], attempt["rise_mib"], attempt["swap_delta"]) == (5304, 4304, 0)
    assert attempt["peak_rss_anon_bytes"] == 269_428 * 1024
    assert (memory["context"], memory["kv_type"], memory["layers_offloaded"], memory["layers_total"]) == (
        12288,
        "f16",
        37,
        37,
    )
    assert (memory["server_rise_mib"], memory["budget_mib"], memory["cuda_buffers_mib"]) == (
        4304,
        6387,
        Fraction("4192.92"),
    )
    assert memory["voided"] == {}


def test_g4_29_card_settings_never_reach_a_request_or_the_argv(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    other = {**VALID_OPERATOR, **dict.fromkeys(("card_settings_qwen3_4b", "card_settings_qwen3_8b"), "other card text")}
    manifests = [
        _manifest(
            frozen,
            ScheduleConfig(SCHEDULE_CANDIDATES, CONDITIONS, operator, "/x", "run_1"),
            SCHEDULE_CANDIDATES,
            ("lo",),
            SHA,
        )
        for operator in (VALID_OPERATOR, other)
    ]
    assert manifests[0].hashes == manifests[1].hashes
    assert manifests[0].candidates == manifests[1].candidates
    assert manifests[0].operator != manifests[1].operator
