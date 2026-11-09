"""The report body: H4 spec section 3 ("Report file format"), G4-36 to G4-40 and section 8's test_report row, without
the Markdown rows (H4a brief section 9, step A5; amendment 6) (S2 §3.13, §9, §10, Annex A).

Every run directory is a synthetic schedule from `_h4_fixtures.make_view`, scored through `gates.evaluate` and
`decision`, with machine evidence built as `AttemptEvidence` values. Margins use 200 resamples for speed, so the
decision blocks here are not S2 §8 conforming, which the report says. No gold row is used.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from app.evaluation.bakeoff import records
from app.evaluation.bakeoff.decision import (
    MARGIN_READING,
    MODEL_SETTING,
    REVISION_SETTING,
    RULE5_OPTIONS,
    RULES,
    Decision,
    decide,
    margin,
    revision_setting,
)
from app.evaluation.bakeoff.gates import (
    CandidateResult,
    ExpectedFreeze,
    OperatorValues,
    ReportInputs,
    evaluate,
    schedule_end,
)
from app.evaluation.bakeoff.gold import GoldSet
from app.evaluation.bakeoff.launch import CANDIDATE_IDS, CANDIDATE_SLUGS
from app.evaluation.bakeoff.records import (
    CARD_SETTINGS_FIELDS,
    OPERATOR_FIELDS,
    SECTION2_FIELDS,
    FaultRecord,
    RunKey,
    RunView,
    TruncatedLine,
    load_run,
    record_json,
    record_loads,
    report_numbers,
    write_report,
)
from app.evaluation.bakeoff.report import (
    BASELINE_LABEL,
    CARD_SETTINGS_LABEL,
    CAVEATS,
    OPEN_RULINGS,
    REPORT_KEYS,
    REPORT_SCHEMA,
    SCORED_FAULT_NOTE,
    AbandonedRun,
    build_report,
    check_paths,
    markdown_placeholder,
    refuse_paths,
    report_json,
    to_report_value,
)
from app.evaluation.bakeoff.runner import MAX_RUN_ATTEMPTS, DeterminismShare, determinism_shares
from app.evaluation.bakeoff.scoring import FIDELITY_PENDING, RunMetrics
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._h4_fixtures import (
    PROBE,
    SINGLE,
    STAMP,
    attempt_evidence,
    contention_record,
    h4_gold,
    make_call,
    make_view,
    passing_evidence,
    perfect_output,
    store_view,
)
from app.evaluation.tests._runner_fixtures import OPERATOR, VALID_OPERATOR, block_processes, no_fsync

Q4B, Q35, Q8B = CANDIDATE_IDS
RESAMPLES = 200
SHA = "a" * 64
MD_SHA = hashlib.sha256(markdown_placeholder()).hexdigest()
VALUES = OperatorValues.from_manifest(VALID_OPERATOR)
NO_INPUTS = ReportInputs(MappingProxyType({}), None, None, None, None, None)
HOME = "/ho" + "me/fixture-operator/"  # G4-38's synthetic home prefix, built so no source holds a home path
TIMED_Q4B = tuple(RunKey(Q4B, "timed_run", run, 1) for run in (1, 2, 3))


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


@pytest.fixture(scope="module")
def clean(base: RunView, gold: GoldSet) -> dict[str, Any]:
    return scored(base, gold)


def parts(
    view: RunView,
    gold: GoldSet,
    *,
    evidence: Mapping[RunKey, Any] | None = None,
    inputs: ReportInputs = NO_INPUTS,
    operator: OperatorValues = VALUES,
) -> tuple[dict[str, CandidateResult], dict[str, DeterminismShare], Decision]:
    results = evaluate(view, gold, passing_evidence(view) if evidence is None else evidence, operator, inputs)
    shares = determinism_shares(view)
    found, variant, _ = margin(results, shares, operator.bootstrap_seed, resamples=RESAMPLES)
    return results, shares, decide(results, found, variant)


def scored(
    view: RunView,
    gold: GoldSet,
    *,
    evidence: Mapping[RunKey, Any] | None = None,
    inputs: ReportInputs = NO_INPUTS,
    operator: OperatorValues = VALUES,
    decision: Decision | None = None,
    abandoned: tuple[AbandonedRun, ...] = (),
    needles: tuple[str, ...] = (),
    numbers: tuple[tuple[int, bool], ...] = (),
) -> dict[str, Any]:
    results, shares, outcome = parts(view, gold, evidence=evidence, inputs=inputs, operator=operator)
    return build_report(
        view,
        "complete",
        results,
        shares,
        outcome if decision is None else decision,
        operator,
        inputs,
        abandoned,
        SHA,
        needles,
        run_name="run_1",
        report_numbers=numbers,
    )


def records_only(
    view: RunView, end: Any = None, *, needles: tuple[str, ...] = (), numbers: tuple[tuple[int, bool], ...] = ()
) -> dict[str, Any]:
    return build_report(
        view,
        schedule_end(view) if end is None else end,
        None,
        None,
        None,
        None,
        NO_INPUTS,
        (),
        SHA,
        needles,
        run_name="run_1",
        report_numbers=numbers,
    )


def candidate(report: Mapping[str, Any], candidate_id: str) -> dict[str, Any]:
    return next(entry for entry in report["candidates"] if entry["candidate_id"] == candidate_id)


def fraction(value: Fraction) -> dict[str, str]:
    return to_report_value(value)


def keys_of(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys_of(item)


# ---------------------------------------------------------------- keys and fixed blocks


def test_the_report_keys_are_the_records_completion_keys() -> None:
    assert REPORT_KEYS == records._REPORT_KEYS
    assert REPORT_SCHEMA == records.SCHEMA_VERSION == 1
    assert len(CAVEATS) == 8
    # Caveat 6 keeps the signed S2 §9 text, its v0.4 per-domain counts included.
    assert CAVEATS[5] == (
        "No per-domain accuracy conclusions are drawn. In-scope CHECKABLE rows per domain in v0.4 are GENERAL 37, "
        "MARKET_FINANCE 34 and WEATHER 24, and leaks are reported by out-of-scope domain only."
    )


def test_a_scored_report_holds_every_key_but_the_markdown_hash(clean: dict[str, Any]) -> None:
    assert set(clean) == set(REPORT_KEYS) - {"markdown_sha256"}
    assert (clean["schema"], clean["record"], clean["run_name"], clean["manifest_sha256"]) == (
        1,
        "report",
        "run_1",
        SHA,
    )
    assert (clean["schedule_end"], clean["stop_reason"], clean["run_conditions_met"]) == ("complete", None, True)
    assert clean["caveats"] == list(CAVEATS)
    assert clean["open_rulings"] == list(OPEN_RULINGS)
    assert {4, 136, 137} <= set(clean["open_rulings"])
    assert clean["pending"] == {"AMB-4": FIDELITY_PENDING}
    assert [entry["candidate_id"] for entry in clean["candidates"]] == list(CANDIDATE_IDS)
    assert "stamp" not in set(keys_of(clean))


def test_claim_fidelity_is_pending_and_run_1_is_the_run_of_record(clean: dict[str, Any]) -> None:
    for entry in clean["candidates"]:
        assert sorted(entry["metrics"]) == ["1", "2", "3"]
        for run, metrics in entry["metrics"].items():
            assert metrics["claim_fidelity"] == FIDELITY_PENDING
            assert metrics["run_of_record"] is (run == "1")
            assert "conversation_scores" not in metrics
            names = {field.name for field in dataclasses.fields(RunMetrics)} - {"conversation_scores"}
            assert names <= set(metrics)


# ---------------------------------------------------------------- to_report_value


@pytest.mark.parametrize("value", ["text", "", True, False, 0, -3, 12, None])
def test_plain_values_stay_as_they_are(value: Any) -> None:
    assert to_report_value(value) == value and type(to_report_value(value)) is type(value)


@pytest.mark.parametrize(
    ("value", "exact", "approx"),
    [
        (Fraction(2, 3), "2/3", "0.6667"),
        (Fraction(1, 8), "1/8", "0.1250"),
        (Fraction(-1, 3), "-1/3", "-0.3333"),
        (Fraction(-3, 2), "-3/2", "-1.5000"),
        (Fraction(5), "5/1", "5.0000"),
        (Fraction(0), "0/1", "0.0000"),
        (Fraction(1, 20000), "1/20000", "0.0000"),  # half to even
        (Fraction(3, 20000), "3/20000", "0.0002"),  # half to even
        (Fraction(99999, 100000), "99999/100000", "1.0000"),
        (Fraction(-1, 30000), "-1/30000", "0.0000"),
    ],
)
def test_a_fraction_is_written_exactly_with_an_approximation(value: Fraction, exact: str, approx: str) -> None:
    assert to_report_value(value) == {"exact": exact, "approx": approx}


def test_int_keys_become_decimal_strings_and_a_mapping_proxy_is_an_object() -> None:
    assert to_report_value({1: "a", 2: Fraction(1, 2)}) == {"1": "a", "2": {"exact": "1/2", "approx": "0.5000"}}
    assert to_report_value(MappingProxyType({"x": MappingProxyType({3: True})})) == {"x": {"3": True}}


@pytest.mark.parametrize("key", [("syn_a", "seg_001"), 1.5, True, None, b"k", Fraction(1, 2)])
def test_a_key_that_is_not_str_or_int_is_refused(key: Any) -> None:
    with pytest.raises(TypeError):
        to_report_value({key: 1})


def test_a_key_that_encodes_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        to_report_value({1: "a", "1": "b"})


@dataclass(frozen=True, slots=True)
class _Inner:
    count: int
    share: Fraction


@dataclass(frozen=True, slots=True)
class _Outer:
    name: str
    inner: _Inner
    items: tuple[_Inner, ...]
    table: Mapping[int, str]


def test_a_nested_dataclass_becomes_an_object_of_its_fields_in_order() -> None:
    value = _Outer("x", _Inner(1, Fraction(1, 4)), (_Inner(2, Fraction(3)),), MappingProxyType({1: "a"}))
    encoded = to_report_value(value)
    assert list(encoded) == ["name", "inner", "items", "table"]
    assert encoded == {
        "name": "x",
        "inner": {"count": 1, "share": {"exact": "1/4", "approx": "0.2500"}},
        "items": [{"count": 2, "share": {"exact": "3/1", "approx": "3.0000"}}],
        "table": {"1": "a"},
    }


def test_a_tuple_keeps_its_order_and_a_set_is_sorted_by_its_encoded_form() -> None:
    assert to_report_value(("b", "a", "c")) == ["b", "a", "c"]
    assert to_report_value(frozenset({"b", "a", "c"})) == ["a", "b", "c"]
    assert to_report_value({3, 1, 2}) == [1, 2, 3]
    halves = to_report_value(frozenset({Fraction(1, 2), Fraction(1, 3)}))
    assert [item["exact"] for item in halves] == ["1/3", "1/2"]


def test_a_run_key_is_written_as_its_unit_name() -> None:
    assert to_report_value(RunKey(Q35, "timed_run", 2, 3)) == "qwen3.5-4b.run2.a3"
    assert to_report_value((RunKey(Q8B, "contended", None, 1),)) == ["qwen3-8b.contended.a1"]


@pytest.mark.parametrize("value", [1.0, [1], b"x", object(), 1j, range(2), {"nested": 0.5}, (1, [2])])
def test_any_other_type_is_refused(value: Any) -> None:
    with pytest.raises(TypeError):
        to_report_value(value)


def test_run_metrics_round_trip_through_the_record_json(gold: GoldSet, base: RunView) -> None:
    metrics = parts(base, gold)[0][Q4B].metrics[1]
    encoded = to_report_value(metrics)
    assert "conversation_scores" not in encoded
    assert set(encoded) == {field.name for field in dataclasses.fields(RunMetrics)} - {"conversation_scores"}
    assert record_loads(record_json(encoded)) == encoded
    assert metrics.f1 is not None and encoded["f1"] == fraction(metrics.f1)
    assert sorted(encoded["per_conversation"]) == sorted(gold.conversation_ids())
    assert encoded["failed_conversations"] == []


def test_a_failed_conversation_is_its_source_id_kind_and_claim_ids(gold: GoldSet) -> None:
    source = gold.conversation_ids()[0]

    def timeout(key: RunKey, role: str, index: Any, position: Any, item: str) -> Any:
        return make_call(item, SINGLE, "timeout") if (key, role, item) == (TIMED_Q4B[0], "single", source) else None

    report = scored(make_view(gold, override=timeout), gold)
    failed = candidate(report, Q4B)["metrics"]["1"]["failed_conversations"]
    assert failed == [{"source_id": source, "kind": "timeout", "out_of_scope_claim_ids": []}]


# ---------------------------------------------------------------- the files


def test_regenerating_a_report_gives_byte_identical_pairs(tmp_path: Path, gold: GoldSet, base: RunView) -> None:
    run_dir = store_view(base, tmp_path)
    paths = []
    for _ in range(2):
        view = load_run(run_dir)
        body = scored(view, gold)
        paths.append(write_report(run_dir, markdown_placeholder(), body, fsync=no_fsync))
        assert Path(paths[-1][1]).read_bytes() == report_json(body, MD_SHA)
    (md_1, json_1), (md_2, json_2) = paths
    assert Path(md_1).name == "report-1.md" and Path(json_2).name == "report-2.json"
    assert Path(md_1).read_bytes() == Path(md_2).read_bytes() == markdown_placeholder()
    assert Path(json_1).read_bytes() == Path(json_2).read_bytes()
    assert report_numbers(run_dir, lambda path: Path(path).read_bytes()) == ((1, True), (2, True))


def test_report_json_is_the_record_line_and_refuses_a_markdown_hash(clean: dict[str, Any]) -> None:
    line = report_json(clean, MD_SHA)
    assert line == record_json({**clean, "markdown_sha256": MD_SHA}) + b"\n"
    assert line.count(b"\n") == 1 and line.isascii()
    assert record_loads(line[:-1])["markdown_sha256"] == MD_SHA
    with pytest.raises(ValueError, match="markdown_sha256"):
        report_json({**clean, "markdown_sha256": MD_SHA}, MD_SHA)


def test_the_markdown_placeholder_is_fixed_ascii_with_no_path_or_stamp() -> None:
    text = markdown_placeholder()
    assert text == markdown_placeholder()
    assert text.isascii() and text.endswith(b"\n") and b"\r" not in text
    assert b"report-<n>.json" in text and b"S2 section 9" in text and b"H5" in text
    assert b"/" not in text and bytes([92]) not in text
    assert re.search(rb"[0-9]{4}-[0-9]{2}|[0-9]{2}:[0-9]{2}", text) is None


# ---------------------------------------------------------------- privacy (G4-38, AMB-137)


def home_view(view: RunView) -> tuple[RunView, tuple[str, ...]]:
    """`view` with the binary, every GGUF path and every model path under the synthetic home prefix."""
    manifest = copy.deepcopy(dict(view.manifest))
    binary = HOME + "llama/llama-server"
    manifest["conditions"]["binary_path"] = binary
    models: dict[str, str] = {}
    for row in manifest["candidates"]:
        models[row["candidate_id"]] = HOME + "models/" + CANDIDATE_SLUGS[row["candidate_id"]] + ".gguf"
        row["gguf_path"] = models[row["candidate_id"]]
        row["argv"][0], row["argv"][2] = binary, models[row["candidate_id"]]
    launches = tuple(
        dataclasses.replace(
            launch,
            argv=(binary, "-m", models[launch.key.candidate], *launch.argv[3:]),
            model_path=models[launch.key.candidate],
        )
        for launch in view.launches
    )
    view = dataclasses.replace(view, manifest=manifest, launches=launches)
    return view, (binary, *models.values(), HOME + "runs")


def test_no_path_text_or_model_output_reaches_the_report(gold: GoldSet) -> None:
    source = gold.conversation_ids()[0]

    def noisy(key: RunKey, role: str, index: Any, position: Any, item: str) -> Any:
        if key == TIMED_Q4B[0] and role == "single" and item == source:
            output = perfect_output(gold, item)
            return make_call(item, SINGLE, "repaired", output=output, content="fixture model answer text")
        if key == TIMED_Q4B[1] and role == "probe" and (index, position) == (2, 3):
            return make_call(item, PROBE, "valid", thinking=True)
        return None

    view, needles = home_view(make_view(gold, override=noisy))
    body = record_json(scored(view, gold, needles=needles)).decode("ascii")
    lowered = body.lower()
    for needle in needles:
        assert needle.lower() not in lowered
    assert ("/ho" + "me/") not in lowered
    for text in (
        "fixture model answer text",
        "synthetic invalid_output",
        "synthetic thought",
        "synthetic reason",
        "synthetic claim",
        "syn_5 line 1 states a fact.",
        "syn_5 line 2 is about a company.",
    ):
        assert text not in body


def test_every_launch_line_is_written_in_the_section_3_3_form(gold: GoldSet) -> None:
    view, needles = home_view(make_view(gold))
    report = scored(view, gold, needles=needles)
    for row, written in zip(view.manifest["candidates"], report["recorded"]["candidates"], strict=True):
        assert written["argv"][:3] == ["llama-server", "-m", "<file>"]
        assert written["argv"][3:] == row["argv"][3:]
        assert set(written) == {"candidate_id", "argv", "gguf_sha256", "source_revision", "quantization_commands"}
    assert report["decision"]["winner"]["launch_argv"][:3] == ["llama-server", "-m", "<file>"]
    assert "binary_path" not in report["recorded"]["conditions"]


@pytest.mark.parametrize(
    "text",
    [
        "/ho" + "me/somebody/notes",
        "/RO" + "OT/x",
        "/mnt/c/" + "Us" + "ers/somebody",
        "C:" + chr(92) + "Us" + "ers" + chr(92) + "somebody",
        "d:/" + "us" + "ers/somebody",
        "copied from /opt/llama/llama-server",
        "/MODELS/candidate.gguf",
    ],
)
def test_a_path_that_slipped_through_is_refused(base: RunView, text: str) -> None:
    manifest = copy.deepcopy(dict(base.manifest))
    manifest["operator"]["machine"] = text
    view = dataclasses.replace(base, manifest=manifest)
    with pytest.raises(ValueError, match="path"):
        records_only(view, "incomplete", needles=("/opt/llama/llama-server", "/models/candidate.gguf"))
    records_only(base, "incomplete", needles=("/opt/llama/llama-server", "/models/candidate.gguf"))


def test_a_needle_in_the_quantization_commands_is_refused(base: RunView) -> None:
    manifest = copy.deepcopy(dict(base.manifest))
    manifest["candidates"][1]["quantization_commands"] = "convert " + HOME + "src"
    with pytest.raises(ValueError):
        records_only(dataclasses.replace(base, manifest=manifest), "incomplete")


# ---------------------------------------------------------------- the pre-mark path walk (H4a addition to G4-38)


def manifest_needles(view: RunView) -> tuple[str, ...]:
    """The configured paths of a fixture manifest: the binary and every GGUF."""
    manifest = view.manifest
    return (manifest["conditions"]["binary_path"], *(row["gguf_path"] for row in manifest["candidates"]))


def with_operator(view: RunView, name: str, value: str) -> RunView:
    manifest = copy.deepcopy(dict(view.manifest))
    manifest["operator"][name] = value
    return dataclasses.replace(view, manifest=manifest)


def test_check_paths_passes_a_clean_run(base: RunView) -> None:
    check_paths(base, NO_INPUTS, (), ((1, True),), manifest_needles(base))


@pytest.mark.parametrize(
    "name", ["machine", SECTION2_FIELDS[Q35], CARD_SETTINGS_FIELDS[Q8B], "prompt_development_models", "gpu_profile"]
)
def test_check_paths_walks_every_operator_field(base: RunView, name: str) -> None:
    with pytest.raises(ValueError, match="path"):
        check_paths(with_operator(base, name, HOME + "notes"), NO_INPUTS, (), (), ())
    check_paths(with_operator(base, name, "plain text"), NO_INPUTS, (), (), ())


def test_check_paths_walks_the_reopening_reason(base: RunView) -> None:
    inputs = dataclasses.replace(NO_INPUTS, deadline_override_sec=Fraction(120), reopening_reason="see " + HOME + "x")
    with pytest.raises(ValueError, match="path"):
        check_paths(base, inputs, (), (), ())


def test_check_paths_walks_the_quantization_commands_against_the_needles(base: RunView) -> None:
    needles = manifest_needles(base)
    manifest = copy.deepcopy(dict(base.manifest))
    manifest["candidates"][2]["quantization_commands"] = "quantize " + needles[1]
    with pytest.raises(ValueError, match="path"):
        check_paths(dataclasses.replace(base, manifest=manifest), NO_INPUTS, (), (), needles)


def test_check_paths_walks_the_fault_details(base: RunView) -> None:
    fault = FaultRecord(1, STAMP, TIMED_Q4B[0], "operator", None, "copied " + manifest_needles(base)[0])
    with pytest.raises(ValueError, match="path"):
        check_paths(dataclasses.replace(base, faults=(fault,)), NO_INPUTS, (), (), manifest_needles(base))


def test_check_paths_refuses_a_malformed_launch_line_and_an_empty_needle(base: RunView) -> None:
    manifest = copy.deepcopy(dict(base.manifest))
    manifest["candidates"][0]["argv"] = ["llama-server", "--port", "8080"]
    with pytest.raises(ValueError, match="-m"):
        check_paths(dataclasses.replace(base, manifest=manifest), NO_INPUTS, (), (), ())
    with pytest.raises(ValueError, match="needle"):
        check_paths(base, NO_INPUTS, (), (), ("",))


@pytest.mark.parametrize(
    ("text", "refused"),
    [
        ("operator saw a stray process", False),
        ("x" * 200, False),
        (HOME + "notes", True),
        ("C:" + chr(92) + "Us" + "ers" + chr(92) + "somebody", True),
        ("see /models/candidate.gguf", True),
    ],
)
def test_refuse_paths_checks_one_operator_string(text: str, refused: bool) -> None:
    needles = ("/opt/llama/llama-server", "/models/candidate.gguf")
    if refused:
        with pytest.raises(ValueError, match="path"):
            refuse_paths(text, needles)
    else:
        refuse_paths(text, needles)
    with pytest.raises(ValueError, match="needle"):
        refuse_paths("plain text", ("",))


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"run_name": "../run_1"}, "run name"),
        ({"run_name": "runs/run_1"}, "run name"),
        ({"run_name": ""}, "run name"),
        ({"manifest_sha256": "A" * 64}, "manifest_sha256"),
        ({"manifest_sha256": "a" * 63}, "manifest_sha256"),
        ({"path_needles": ("",)}, "needle"),
    ],
)
def test_bad_identity_arguments_are_refused(base: RunView, change: dict[str, Any], match: str) -> None:
    arguments: dict[str, Any] = {"run_name": "run_1", "manifest_sha256": SHA, "path_needles": (), **change}
    with pytest.raises(ValueError, match=match):
        build_report(
            base,
            "incomplete",
            None,
            None,
            None,
            None,
            NO_INPUTS,
            (),
            arguments["manifest_sha256"],
            arguments["path_needles"],
            run_name=arguments["run_name"],
            report_numbers=(),
        )


def test_a_launch_line_without_its_model_flag_is_refused(base: RunView) -> None:
    manifest = copy.deepcopy(dict(base.manifest))
    manifest["candidates"][0]["argv"] = ["llama-server", "-c", "12288"]
    with pytest.raises(ValueError, match="-m"):
        records_only(dataclasses.replace(base, manifest=manifest), "incomplete")


# ---------------------------------------------------------------- recorded for every number


def test_the_recorded_block_is_an_allowlist(clean: dict[str, Any]) -> None:
    recorded = clean["recorded"]
    left_out = {*SECTION2_FIELDS.values(), *CARD_SETTINGS_FIELDS.values(), "prompt_development_models"}
    assert list(recorded["operator"]) == [name for name in OPERATOR_FIELDS if name not in left_out]
    assert recorded["operator"]["llama_cpp_commit"] == VALID_OPERATOR["llama_cpp_commit"]
    assert recorded["prompt_development_models"] == ["synthetic/model-a", "synthetic/model-b@0123abcd"]
    assert recorded["conditions"]["ready_deadline_sec"]["exact"].endswith("/1")
    assert recorded["sampling"]["temperature"] == fraction(Fraction(0))
    assert recorded["sampling"]["seed"] == 42
    assert recorded["interfaces"] == ["lo"]
    assert recorded["bootstrap"]["seed"] == VALUES.bootstrap_seed
    assert (
        recorded["bootstrap"]["python_version"] == clean["decision"]["margin"]["result"]["bootstrap"]["python_version"]
    )
    assert "AMB-3" in recorded["bootstrap"]["method"]
    assert recorded["gold_version"] == "v1.0"


def test_section_2_and_card_settings_appear_per_candidate(clean: dict[str, Any]) -> None:
    for index, candidate_id in enumerate(CANDIDATE_IDS):
        entry = candidate(clean, candidate_id)
        assert entry["section2"] == "passed"
        assert entry["card_settings"] == {
            "value": VALID_OPERATOR[CARD_SETTINGS_FIELDS[candidate_id]],
            "label": CARD_SETTINGS_LABEL,
        }
        assert entry["slug"] == CANDIDATE_SLUGS[candidate_id]
        assert entry["card_settings"]["value"].endswith(("one", "two", "three")[index])
    assert CARD_SETTINGS_LABEL == "recorded, not used (S2 §3.7)"


# ---------------------------------------------------------------- the decision block (G4-29)

PINS = {"rule 2": Q4B, "rule 3": Q35, "rule 4 via rule 3": Q35, "rule 4 (Qwen3-8B)": Q8B, "rule 5": None}


@pytest.mark.parametrize("rule", RULES)
def test_every_rule_reports_the_literal_margin_reading(gold: GoldSet, base: RunView, rule: Any) -> None:
    pinned = PINS[rule]
    outcome = Decision(rule, pinned, None, None, None, "no margin was computed", dict.fromkeys(CANDIDATE_IDS, True), ())
    block = scored(base, gold, decision=outcome)["decision"]
    assert block["margin_reading"] == MARGIN_READING == "literal"
    assert (block["rule"], block["pinned"]) == (rule, pinned)
    if pinned is None:
        assert block["settings"] is None and block["winner"] is None and block["changes_baseline"] is None
        assert block["options"] == list(RULE5_OPTIONS)
        return
    row = next(row for row in base.manifest["candidates"] if row["candidate_id"] == pinned)
    assert block["settings"] == {
        MODEL_SETTING: pinned,
        REVISION_SETTING: revision_setting(row["source_revision"], row["gguf_sha256"]),
    }
    assert all(isinstance(value, str) for value in block["settings"].values())
    assert block["winner"] == {
        "candidate_id": pinned,
        "source_revision": row["source_revision"],
        "gguf_sha256": row["gguf_sha256"],
        "llama_cpp_commit": VALID_OPERATOR["llama_cpp_commit"],
        "launch_argv": ["llama-server", "-m", "<file>", *row["argv"][3:]],
    }
    assert block["changes_baseline"] is (pinned != Q4B)
    assert block["options"] is None


def test_the_clean_decision_block_carries_the_margin_and_steps(gold: GoldSet, base: RunView) -> None:
    results, shares, outcome = parts(base, gold)
    block = scored(base, gold)["decision"]
    assert block["rule"] == "rule 2" and block["steps"] == list(outcome.steps)
    assert block["pinnable"] == dict.fromkeys(CANDIDATE_IDS, True)
    margin_block = block["margin"]
    assert (margin_block["variant"], margin_block["conforming"]) == (False, False)
    assert margin_block["note"] == "not S2 §8 conforming (resamples 200, needs 10,000)"
    bootstrap = margin_block["result"]["bootstrap"]
    assert (bootstrap["resamples"], bootstrap["seed"]) == (RESAMPLES, VALUES.bootstrap_seed)
    assert set(bootstrap) >= {"resamples", "seed", "confidence", "n_conversations", "python_version"}
    assert [c["number"] for c in margin_block["result"]["conditions"]] == [1, 2, 3, 4]
    assert block["provisional_note"] is not None and block["d5_note"] is None


def test_an_expected_freeze_marks_the_gold_manifest_checked(gold: GoldSet, base: RunView) -> None:
    expected = ExpectedFreeze("v1.0", MappingProxyType({}), MappingProxyType({}))
    report = scored(base, gold, inputs=dataclasses.replace(NO_INPUTS, expected=expected))
    assert report["gold_manifest_checked"] is True and report["decision"]["provisional_note"] is None


def test_the_binding_value_inputs_are_measured_figures_only(
    gold: GoldSet, base: RunView, clean: dict[str, Any]
) -> None:
    results = parts(base, gold)[0]
    binding = clean["binding_value_inputs"]
    assert "proposes no binding value" in binding["note"]
    for candidate_id, result in results.items():
        assert result.latency is not None
        for kind, dist in result.latency["attempts"].items():
            assert binding[candidate_id]["per_attempt"][kind] == {"p95_ns": dist.p95, "max_ns": dist.max}
        assert binding[candidate_id]["longest_by_kind"] == to_report_value(result.latency["longest_by_kind"])


# ---------------------------------------------------------------- candidates


def test_memory_sample_rate_is_the_sample_rate_of_each_counted_attempt(gold: GoldSet, base: RunView) -> None:
    evidence: dict[RunKey, Any] = {}
    for run in base.runs:
        if run.key.phase in ("timed_run", "contended"):
            used = (900, *(5204,) * (25 + 3 * (run.key.run or 0)))
            evidence[run.key] = attempt_evidence(run.key, used=used, elapsed_ms=1700)
    report = scored(base, gold, evidence=evidence)
    for candidate_id in CANDIDATE_IDS:
        attempts = candidate(report, candidate_id)["memory"]["attempts"]
        assert len(attempts) == 4
        for unit, entry in attempts.items():
            key = records.parse_unit_name(unit)
            assert entry["sample_rate"] == dataclasses.asdict(evidence[key].rate)
            assert set(entry["sample_rate"]) == {
                "samples",
                "elapsed_ms",
                "first_to_last_ms",
                "min_per_whole_second",
                "max_gap_ms",
            }


def test_the_baseline_is_reported_beside_the_idle_figure_and_voids_nothing(gold: GoldSet, base: RunView) -> None:
    evidence = passing_evidence(base)
    evidence[TIMED_Q4B[1]] = attempt_evidence(TIMED_Q4B[1], free=7800)
    report = scored(base, gold, evidence=evidence)
    entry = candidate(report, Q4B)
    memory = entry["memory"]
    assert memory["baseline_label"] == BASELINE_LABEL
    low = memory["attempts"]["qwen3-4b.run2.a1"]
    assert (low["baseline_used_mib"], low["baseline_free_mib"], low["idle_free_mib"]) == (1000, 7800, 7899)
    assert (low["baseline_free_minus_idle_mib"], low["baseline_at_idle"]) == (-99, False)
    idle = memory["attempts"]["qwen3-4b.run1.a1"]
    assert (idle["baseline_free_minus_idle_mib"], idle["baseline_at_idle"]) == (0, True)
    assert entry["gates"]["M"]["status"] == "pass"
    assert memory["voided"] == {} and report["reopening"]["voids"] == []


def test_a_voided_attempt_is_listed_with_its_readings_and_its_fault(gold: GoldSet) -> None:
    view = make_view(gold, voids={(Q4B, "timed_run", 2): 1, (Q8B, "contended", None): 1})
    evidence = passing_evidence(view)
    voided = RunKey(Q4B, "timed_run", 2, 1)
    evidence[voided] = attempt_evidence(voided, used=(1000, 9000), free=7700)
    report = scored(view, gold, evidence=evidence)
    memory = candidate(report, Q4B)["memory"]
    assert memory["voided"]["qwen3-4b.run2.a1"]["peak_used_mib"] == 9000
    assert memory["voided"]["qwen3-4b.run2.a1"]["baseline_at_idle"] is False
    assert "qwen3-4b.run2.a1" not in memory["attempts"] and "qwen3-4b.run2.a2" in memory["attempts"]
    assert candidate(report, Q4B)["voided"] == ["qwen3-4b.run2.a1"]
    assert report["reopening"]["voids"] == [
        {
            "unit": "qwen3-4b.run2.a1",
            "fault_id": 1,
            "fault_kind": "hook_failed",
            "detail": "before_timed_run: Synthetic",
        },
        {
            "unit": "qwen3-8b.contended.a1",
            "fault_id": 2,
            "fault_kind": "hook_failed",
            "detail": "before_timed_run: Synthetic",
        },
    ]
    assert report["reopening"]["faults_without_void"] == []


@pytest.mark.parametrize(
    ("entries", "digest"), [({"qwen3-4b": "rose", "qwen3-8b": "not_rising"}, "e" * 64), ({}, None)]
)
def test_each_candidates_shared_memory_value_appears_in_gate_m(
    gold: GoldSet, base: RunView, entries: dict[str, Any], digest: str | None
) -> None:
    inputs = dataclasses.replace(
        NO_INPUTS, shared_memory=MappingProxyType(entries), shared_memory_evidence_sha256=digest
    )
    report = scored(base, gold, inputs=inputs)
    for candidate_id in CANDIDATE_IDS:
        entry = candidate(report, candidate_id)
        value = entries.get(CANDIDATE_SLUGS[candidate_id], "not_observed")
        assert entry["shared_memory"] == value
        assert f"shared memory: {value}" in entry["gates"]["M"]["notes"]


def test_the_gate_table_holds_seven_gates_with_their_checks(clean: dict[str, Any]) -> None:
    for entry in clean["candidates"]:
        assert list(entry["gates"]) == ["L", "M", "J", "C", "T", "A", "S"]
        for name, gate in entry["gates"].items():
            assert gate["gate"] == name and gate["status"] == "pass"
            assert all(set(check) == {"name", "value", "limit", "op", "passed"} for check in gate["checks"])
        assert entry["probe_readings"] == {
            reading: [True, True] for reading in ("first_session", "every_session", "pooled", "per_item", "majority")
        }
        assert entry["probe_readings_disagree"] is False
        assert entry["thinking"] == {
            "pre_run": "off",
            "in_runs": {
                "run1.constrained_timed": 0,
                "run2.constrained_timed": 0,
                "run3.constrained_timed": 0,
                "contended": 0,
                "unconstrained": 0,
            },
            "summary": "no thinking seen in run calls",
        }


def test_thinking_seen_in_a_run_is_shown_prominently(gold: GoldSet) -> None:
    def thinking(key: RunKey, role: str, index: Any, position: Any, item: str) -> Any:
        hit = key == TIMED_Q4B[1] and role == "probe" and (index, position) == (3, 7)
        return make_call(item, PROBE, "valid", thinking=True) if hit else None

    entry = candidate(scored(make_view(gold, override=thinking), gold), Q4B)
    assert entry["thinking"]["in_runs"]["run2.constrained_timed"] == 1
    assert entry["thinking"]["summary"] == "thinking seen in runs: run2.constrained_timed 1 call(s)"
    assert entry["information_only"] == []


def test_a_candidate_that_did_not_load_reports_no_share(gold: GoldSet) -> None:
    report = scored(make_view(gold, not_loaded=(Q35,)), gold)
    entry = candidate(report, Q35)
    assert (entry["status"], entry["determinism"], entry["memory"], entry["metrics"]) == (
        "not_loaded",
        "did not load",
        None,
        {},
    )
    assert entry["information_only"] == ["gate_L"]
    assert candidate(report, Q4B)["determinism"]["compared"] > 0
    assert candidate(report, Q4B)["determinism"]["share"] == fraction(Fraction(0))
    assert Q35 not in report["binding_value_inputs"]


def test_the_together_session_reports_partial_and_absent(gold: GoldSet) -> None:
    view = make_view(gold, voids={(Q8B, "contended", None): 3})
    evidence = passing_evidence(view)
    contended = RunKey(Q35, "contended", None, 1)
    evidence[contended] = attempt_evidence(contended, contention=contention_record(20_000))
    report = scored(view, gold, evidence=evidence)
    partial = candidate(report, Q35)
    assert partial["contended"]["status"] == "partial" and partial["contended"]["ended_before_stop_ms"] == 20_000
    assert "contention partial: audio ended 20.000 s before stop" in partial["gates"]["M"]["notes"]
    absent = candidate(report, Q8B)
    assert absent["contended"]["status"] == "absent" and absent["contended"]["needs_repeat"] is True
    assert absent["gates"]["M"]["status"] == "not_measured"
    assert report["reopening"]["absent_ungated"] == ["qwen3-8b.contended"]


# ---------------------------------------------------------------- budgets and re-opening values (G4-14, AMB-143)


def test_the_budgets_in_force_come_from_the_manifest(clean: dict[str, Any]) -> None:
    budgets = clean["budgets"]
    assert (budgets["idle_free_mib"], budgets["audio_vram_mib"], budgets["gate_m_budget_mib"]) == (7899, 1000, 6387)
    assert budgets["headroom_mib"] == 512
    assert budgets["audio_vram_sample_rate"] == {"samples": 200, "min_per_whole_second": 10, "rate_ok": True}
    assert budgets["run_deadline_sec"] == budgets["run_deadline_manifest"] == fraction(Fraction(125))
    assert (budgets["run_deadline_source"], budgets["rerun_allowance_source"]) == ("manifest", "manifest")
    assert budgets["model_budget_sec"] == fraction(Fraction(125, 2))
    assert budgets["d5_recorded"] == {
        "run_deadline_sec": fraction(Fraction(125)),
        "rerun_allowance_sec": fraction(Fraction(25)),
    }
    assert budgets["d5_difference"] == {
        "differs": {"run_deadline_sec": False, "rerun_allowance_sec": False},
        "flagged": False,
    }
    assert budgets["reopening_reason"] is None and "allowance-only" in budgets["rerun_allowance_note"]


def test_a_manifest_deadline_of_130_is_flagged_not_refused(gold: GoldSet) -> None:
    operator = {**VALID_OPERATOR, "run_deadline_sec": "130"}
    report = scored(make_view(gold, operator=operator), gold, operator=OperatorValues.from_manifest(operator))
    assert report["budgets"]["d5_difference"]["flagged"] is True
    assert report["budgets"]["run_deadline_sec"] == fraction(Fraction(130))
    assert "AMB-143" in report["decision"]["d5_note"]


def test_a_deadline_reopening_records_its_source_original_and_reason(gold: GoldSet, base: RunView) -> None:
    inputs = dataclasses.replace(NO_INPUTS, deadline_override_sec=Fraction(120), reopening_reason="D5 changed")
    budgets = scored(base, gold, inputs=inputs)["budgets"]
    assert budgets["run_deadline_sec"] == fraction(Fraction(120))
    assert (budgets["run_deadline_source"], budgets["run_deadline_manifest"]) == ("reopening", fraction(Fraction(125)))
    assert budgets["model_budget_sec"] == fraction(Fraction(60))
    assert budgets["reopening_reason"] == "D5 changed"
    assert (
        budgets["d5_difference"]["differs"]["run_deadline_sec"] is True and budgets["d5_difference"]["flagged"] is False
    )


def test_an_allowance_only_reopening_changes_no_gate_or_rule(
    gold: GoldSet, base: RunView, clean: dict[str, Any]
) -> None:
    inputs = dataclasses.replace(NO_INPUTS, rerun_allowance_override_sec=Fraction(30), reopening_reason="allowance")
    report = scored(base, gold, inputs=inputs)
    assert report["budgets"]["rerun_allowance_source"] == "reopening"
    assert report["budgets"]["rerun_allowance_manifest"] == fraction(Fraction(25))
    assert report["budgets"]["rerun_allowance_sec"] == fraction(Fraction(30))
    for before, after in zip(clean["candidates"], report["candidates"], strict=True):
        assert before["gates"]["T"] == after["gates"]["T"]
    assert (report["decision"]["rule"], report["decision"]["pinned"]) == (
        clean["decision"]["rule"],
        clean["decision"]["pinned"],
    )


# ---------------------------------------------------------------- re-opening lists (G4-37, AMB-136)


def with_faults(view: RunView, *faults: tuple[RunKey, str]) -> RunView:
    added = tuple(
        FaultRecord(len(view.faults) + number, STAMP, key, "operator", None, detail)
        for number, (key, detail) in enumerate(faults, start=1)
    )
    return dataclasses.replace(view, faults=(*view.faults, *added))


def test_faults_without_a_void_and_faults_on_scored_units_are_listed(gold: GoldSet, base: RunView) -> None:
    view = with_faults(base, (RunKey(Q4B, "pre_run", None, 1), "operator note"), (TIMED_Q4B[0], "late fault"))
    report = scored(view, gold)
    assert report["reopening"]["faults_without_void"] == [
        {
            "fault_id": 1,
            "unit": "qwen3-4b.pre_run.a1",
            "kind": "operator",
            "item_id": None,
            "detail": "operator note",
            "unit_voided": False,
        }
    ]
    assert report["reopening"]["faults_on_scored_units"] == [
        {
            "fault_id": 2,
            "unit": "qwen3-4b.run1.a1",
            "kind": "operator",
            "item_id": None,
            "detail": "late fault",
            "note": SCORED_FAULT_NOTE,
        }
    ]
    restamped = dataclasses.replace(
        view, faults=tuple(dataclasses.replace(fault, stamp="2030-01-01T00:00:00+00:00") for fault in view.faults)
    )
    assert record_json(scored(restamped, gold)) == record_json(report)


def test_a_fault_on_a_cut_marker_counts_as_a_fault_on_a_scored_unit(gold: GoldSet, base: RunView) -> None:
    key = TIMED_Q4B[2]
    cut = dataclasses.replace(
        base, scored=base.scored - {key}, truncated=(TruncatedLine("qwen3-4b.run3.a1.marker", 1, 0),)
    )
    report = scored(with_faults(cut, (key, "after the marker")), gold)
    assert [entry["unit"] for entry in report["reopening"]["faults_on_scored_units"]] == ["qwen3-4b.run3.a1"]
    assert report["reopening"]["truncated"] == [
        {"file_name": "qwen3-4b.run3.a1.marker", "line_number": 1, "byte_count": 0}
    ]


def test_abandoned_directories_and_incomplete_report_numbers_are_listed(gold: GoldSet, base: RunView) -> None:
    abandoned = (
        AbandonedRun("bakeoff-1", "b" * 64, 9, 2, 1, None),
        AbandonedRun("bakeoff-0", None, 0, 0, 0, "unreadable: StoreError"),
    )
    report = scored(base, gold, abandoned=abandoned, numbers=((1, True), (2, False), (3, True), (4, False)))
    assert report["reopening"]["abandoned"] == [
        {"name": "bakeoff-1", "manifest_sha256": "b" * 64, "runs": 9, "voids": 2, "truncated": 1, "unreadable": None},
        {
            "name": "bakeoff-0",
            "manifest_sha256": None,
            "runs": 0,
            "voids": 0,
            "truncated": 0,
            "unreadable": "unreadable: StoreError",
        },
    ]
    assert report["reopening"]["incomplete_reports"] == [2, 4]
    assert report["reopening"]["absent_ungated"] == [] and report["reopening"]["truncated"] == []


@pytest.mark.parametrize(
    "fields",
    [
        ("../bakeoff-1", "b" * 64, 1, 0, 0, None),
        ("bakeoff/1", "b" * 64, 1, 0, 0, None),
        ("bakeoff-1", None, 0, 0, 0, None),
        ("bakeoff-1", "b" * 64, 1, 0, 0, "unreadable: OSError"),
        ("bakeoff-1", "B" * 64, 1, 0, 0, None),
    ],
)
def test_an_abandoned_run_is_named_and_described_exactly_once(fields: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        AbandonedRun(*fields)


# ---------------------------------------------------------------- records-only reports (G4-2, G4-43)


def _without_unconstrained(view: RunView) -> RunView:
    """Qwen3.5-4B's unconstrained pass with no run record: a crash before it ended."""
    key = RunKey(Q35, "unconstrained", None, 1)
    return dataclasses.replace(view, runs=tuple(run for run in view.runs if run.key != key))


def _blocked(view: RunView) -> RunView:
    blocked = dataclasses.replace(view.pre_run[0], status="blocked", blocked=("gguf_sha256",))
    return dataclasses.replace(view, pre_run=(blocked, *view.pre_run[1:]))


@pytest.mark.parametrize(
    ("build", "end", "reason"),
    [
        (lambda gold: _blocked(make_view(gold)), "pre_run_stopped", ["qwen3-4b.pre_run blocked: gguf_sha256"]),
        (
            lambda gold: make_view(gold, voids={(Q4B, "timed_run", 2): 3}),
            "runs_exhausted",
            ["qwen3-4b.run2: 3 void attempts (S2 §3.13)"],
        ),
        (
            lambda gold: dataclasses.replace(make_view(gold), truncated=(TruncatedLine("calls.jsonl", 9, 12),)),
            "incomplete",
            ["truncated: calls.jsonl line 9"],
        ),
        (
            lambda gold: _without_unconstrained(make_view(gold)),
            "incomplete",
            [f"qwen3.5-4b.unconstrained: neither a complete attempt nor {MAX_RUN_ATTEMPTS} voids"],
        ),
        (
            lambda gold: make_view(gold, operator={**VALID_OPERATOR, "cuda_process_check": "failed: one process"}),
            "complete",
            ["run condition not passed: cuda_process_check (S2 §3.1, §3.2; AMB-132)"],
        ),
    ],
)
def test_a_records_only_report_names_why_it_holds_no_decision(
    gold: GoldSet, build: Any, end: str, reason: list[str]
) -> None:
    view = build(gold)
    assert schedule_end(view) == end
    report = records_only(view)
    assert set(report) == set(REPORT_KEYS) - {"markdown_sha256"}
    assert (report["schedule_end"], report["stop_reason"]) == (end, reason)
    for name in ("budgets", "candidates", "decision", "binding_value_inputs"):
        assert report[name] is None, name
    assert report["gold_manifest_checked"] is False
    assert report["recorded"]["bootstrap"]["seed"] is None
    assert report["run_conditions_met"] is (end != "complete")
    assert report["caveats"] == list(CAVEATS)


def test_a_records_only_report_copies_the_raw_placeholder_operator_values(gold: GoldSet) -> None:
    view = make_view(gold, operator=OPERATOR)
    report = records_only(view)
    assert report["schedule_end"] == "complete" and report["run_conditions_met"] is False
    assert report["recorded"]["prompt_development_models"] == ["recorded"]
    assert set(report["recorded"]["operator"].values()) == {"recorded"}
    assert len(report["stop_reason"]) == 4


def test_a_records_only_report_lists_the_truncated_lines(gold: GoldSet) -> None:
    view = dataclasses.replace(make_view(gold), truncated=(TruncatedLine("calls.jsonl", 9, 12),))
    report = records_only(view, numbers=((1, False),))
    assert report["reopening"]["truncated"] == [{"file_name": "calls.jsonl", "line_number": 9, "byte_count": 12}]
    assert report["reopening"]["incomplete_reports"] == [1]


def test_scored_and_records_only_arguments_must_agree(gold: GoldSet, base: RunView) -> None:
    results, shares, outcome = parts(base, gold)
    with pytest.raises(ValueError, match="together"):
        records_only(base, "complete")
    with pytest.raises(ValueError, match="together"):
        build_report(
            base,
            "incomplete",
            results,
            shares,
            outcome,
            VALUES,
            NO_INPUTS,
            (),
            SHA,
            (),
            run_name="r",
            report_numbers=(),
        )
    with pytest.raises(ValueError, match="together"):
        build_report(
            base, "complete", results, shares, None, VALUES, NO_INPUTS, (), SHA, (), run_name="r", report_numbers=()
        )
