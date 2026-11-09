"""The bake-off report body (bake-off harness H4a).

This module turns what `gates`, `decision` and the records hold into the report of record. It is pure: `entry`
loads the run, scores it and passes the results in; `records.write_report` writes the pair. The body is built from an
allowlist of fields, never by copying a manifest, launch record or props object whole, and every value goes through
`to_report_value`, so the JSON holds only ids, counts, hashes, exact figures, fault kinds and fault details.

Implements, from the signed D6 criteria note S2:

- S2 §9, report: "Recorded for every number", the per-candidate figures, the decision with its rule under the literal
  margin reading, the proposed binding-value inputs and the caveats. "Logs hold no prompts or transcripts beyond the
  synthetic gold text": no utterance, claim text, model output, error text, path or stamp reaches the body, and a walk
  over every string refuses the report if a configured path or a home-directory path slipped in.
- S2 §3.13, runs: voided runs are listed with their fault, never counted; a schedule that did not complete gets a
  records-only report, with no score, gate or decision.
- S2 §10, re-opening: the deadline and re-run allowance in force are recorded with their source and the manifest
  originals, and the re-opening lists (voids, absent units, abandoned directories, faults without a void, faults on
  scored units, incomplete report numbers, truncated lines) are always given.
- Annex A, pre-run record: the operator's recorded values, the bootstrap seed and method, and the D5 values beside
  the values in force.

The Markdown file of each pair is a fixed placeholder here; `report-<n>.json` is the report of record, and the
rendering is H5's.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from .decision import MARGIN_READING, RULE5_OPTIONS, Decision, settings
from .evidence import MIN_SAMPLES_PER_SEC
from .gates import (
    _UNGATED,
    D5_RERUN_ALLOWANCE_SEC,
    D5_RUN_DEADLINE_SEC,
    HEADROOM_MIB,
    MODEL_SHARE,
    CandidateResult,
    OperatorValues,
    ReportInputs,
    ScheduleEnd,
    _complete_keys,
    _ready,
    _void_runs,
    failed_run_conditions,
    readings_disagree,
)
from .launch import CANDIDATE_IDS, CANDIDATE_SLUGS
from .records import (
    _RUN_NAME,
    CARD_SETTINGS_FIELDS,
    OPERATOR_FIELDS,
    RUNS,
    SECTION2_FIELDS,
    FaultRecord,
    RunKey,
    RunView,
    _unit_name,
    record_json,
)
from .runner import MAX_RUN_ATTEMPTS, DeterminismShare
from .scoring import FIDELITY_PENDING, RunMetrics
from .stats import MultiRunBootstrap

REPORT_SCHEMA = 1
REPORT_KEYS: tuple[str, ...] = (
    "schema",
    "record",
    "run_name",
    "manifest_sha256",
    "markdown_sha256",
    "schedule_end",
    "stop_reason",
    "run_conditions_met",
    "gold_manifest_checked",
    "recorded",
    "budgets",
    "candidates",
    "decision",
    "reopening",
    "binding_value_inputs",
    "caveats",
    "open_rulings",
    "pending",
)
# S2 §9 "Caveats", as fixed text.
CAVEATS: tuple[str, ...] = (
    "The set is synthetic and English only.",
    "ASR style is unmeasured, so recall may not transfer to real calls.",
    "There is one labeller (intra-rater check only).",
    "Verdict quality is not measured.",
    "Urdu and Arabic are not measured (U2; N4b L4 not measured).",
    (
        "No per-domain accuracy conclusions are drawn. In-scope CHECKABLE rows per domain in v0.4 are GENERAL 37, "
        "MARKET_FINANCE 34 and WEATHER 24, and leaks are reported by out-of-scope domain only."
    ),
    "Nothing here bears on the safety of regulated domains.",
    "In a few approved rows, a market professional states a false value (the guide's realism note).",
)
# The NEEDS-OWNER readings the H4a harness implements and the owner has still to rule (H1 to H4 specs, section 7).
OPEN_RULINGS: tuple[int, ...] = (1, 2, 3, 4, 26, 27, 28, 29, 30, 66, 69, 106, 116, 129, 130, 134, 135, 136, 137, 139)
BOOTSTRAP_METHOD = "paired percentile bootstrap over conversations, nearest-rank lower bound (H1 AMB-3)"
CARD_SETTINGS_LABEL = "recorded, not used (S2 §3.7)"
SCORED_FAULT_NOTE = "fault on a scored unit: no void possible (H3 refuses void_run once the marker exists)"
BASELINE_LABEL = "baseline reported beside the idle figure; nothing voids on it (S2 §3.1; AMB-110)"
ALLOWANCE_NOTE = (
    "the re-run allowance does not enter gate T's budget (deadline x 1/2), so an allowance-only re-opening gives the "
    "same gate T and S2 §8 outcome (S2 §6 gate T, §10)"
)
BINDING_NOTE = "measured inputs only; the report proposes no binding value (S2 §9; AMB-120)"
LLAMA_SERVER = "llama-server"
MODEL_FILE = "<file>"

_SHA256 = re.compile(r"[0-9a-f]{64}")  # fullmatch only
# Home-directory forms, matched on casefolded text. Built in pieces so that no source file holds such a path.
_SEPARATOR = "[" + chr(92) * 2 + "/]"
_HOME_FORMS = re.compile(
    "|".join(
        ("/ho" + "me/", "/ro" + "ot/", "/mnt/[a-z]/us" + "ers/", "[a-z]:" + _SEPARATOR + "+us" + "ers" + _SEPARATOR)
    )
)
_LEFT_OUT = frozenset(
    {*SECTION2_FIELDS.values(), *CARD_SETTINGS_FIELDS.values(), "prompt_development_models"}
)  # reported per candidate, or under their own heading


@dataclass(frozen=True, slots=True)
class AbandonedRun:
    """A directory named with `--abandoned`, loaded read-only from the same parent (S2 §3.13, §10; G4-37). Either its
    manifest hash and counts, or `unreadable: <ErrorClass>`."""

    name: str
    manifest_sha256: str | None
    runs: int
    voids: int
    truncated: int
    unreadable: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _RUN_NAME.fullmatch(self.name) is None:
            raise ValueError(f"an abandoned run is named by its run name only, not {self.name!r}")
        if (self.unreadable is None) == (self.manifest_sha256 is None):
            raise ValueError("an abandoned run has a manifest hash or an unreadable reason, exactly one")
        if self.manifest_sha256 is not None and _SHA256.fullmatch(self.manifest_sha256) is None:
            raise ValueError("an abandoned run's manifest hash must be 64 lowercase hex characters")


# ---------------------------------------------------------------- encoding


def _approx(value: Fraction) -> str:
    """Four decimals, rounded half to even, from exact integers."""
    scaled = round(value * 10_000)
    whole, part = divmod(abs(scaled), 10_000)
    return f"{'-' if scaled < 0 else ''}{whole}.{part:04d}"


def _key(key: object) -> str:
    if isinstance(key, bool) or not isinstance(key, str | int):
        raise TypeError(f"a report key must be str or int, not {type(key).__name__}")
    return key if isinstance(key, str) else str(key)


def _fields(x: Any) -> dict[str, Any]:
    """A dataclass's fields in field order. `RunMetrics.conversation_scores` is left out: the aggregates and
    `per_conversation` carry every S2 §5 metric, and per-claim detail can be rebuilt from the stored run (spec (h))."""
    skipped = ("conversation_scores",) if isinstance(x, RunMetrics) else ()
    return {field.name: getattr(x, field.name) for field in dataclasses.fields(x) if field.name not in skipped}


def to_report_value(x: Any) -> Any:
    """The JSON form of one report value (spec section 3, "JSON encoding"). str, bool, int and None stay; a Fraction
    becomes `{"exact": "p/q", "approx": "0.xxxx"}`; a mapping becomes an object with str or decimal int keys; a
    `RunKey` becomes its unit name; a dataclass becomes an object of its fields (a `RunMetrics` without
    `conversation_scores`); a tuple becomes a list in order; a set becomes a list sorted by the encoded form. Anything
    else, float and list included, raises `TypeError`: there is no `str()` fallback."""
    if x is None or isinstance(x, str | bool | int):
        return x
    if isinstance(x, Fraction):
        return {"exact": f"{x.numerator}/{x.denominator}", "approx": _approx(x)}
    if isinstance(x, RunKey):
        return _unit_name(x)
    if isinstance(x, Mapping):
        encoded: dict[str, Any] = {}
        for key, value in x.items():
            name = _key(key)
            if name in encoded:
                raise ValueError(f"the report key {name!r} appears twice")
            encoded[name] = to_report_value(value)
        return encoded
    if dataclasses.is_dataclass(x) and not isinstance(x, type):
        return {name: to_report_value(value) for name, value in _fields(x).items()}
    if isinstance(x, tuple):
        return [to_report_value(item) for item in x]
    if isinstance(x, frozenset | set):
        return sorted((to_report_value(item) for item in x), key=record_json)
    raise TypeError(f"{type(x).__name__} has no report form")


def report_json(report: Mapping[str, Any], markdown_sha256: str) -> bytes:
    """The bytes `records.write_report` writes for `report`: one record line, ending in LF."""
    if "markdown_sha256" in report:
        raise ValueError("the report body must not hold markdown_sha256")
    return record_json({**report, "markdown_sha256": markdown_sha256}) + b"\n"


def markdown_placeholder() -> bytes:
    """The fixed Markdown file of every report pair: ASCII, LF, no path and no stamp, so its hash never varies."""
    return (
        b"# Bake-off report\n"
        b"\n"
        b"The report of record is report-<n>.json beside this file, with the same number n (S2 section 9).\n"
        b"Its markdown_sha256 field is the SHA-256 of this file.\n"
        b"\n"
        b"This Markdown file is a fixed placeholder. The rendered report (gate table, decision line and sign-off\n"
        b"block) is built by milestone H5.\n"
    )


# ---------------------------------------------------------------- privacy


def _strings(value: Any) -> Iterator[str]:
    """Every string in an encoded value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _check_needles(needles: tuple[str, ...]) -> None:
    if any(not isinstance(needle, str) or not needle for needle in needles):
        raise ValueError("a path needle must be a non-empty string")


def _refuse_paths(body: Mapping[str, Any], needles: tuple[str, ...]) -> None:
    """The belt-and-braces walk (G4-38): no configured path and no home-directory form in any string."""
    folded = tuple(needle.casefold() for needle in needles)
    for text in _strings(body):
        lowered = text.casefold()
        if any(needle in lowered for needle in folded) or _HOME_FORMS.search(lowered):
            raise ValueError("a path reached the report body; nothing is written (S2 §9)")


def refuse_paths(text: str, needles: tuple[str, ...]) -> None:
    """G4-38 for one operator string before it is stored: `ValueError` when `text` holds a path needle or a
    home-directory form. `entry` runs it on a `void --detail`, which every later report copies (H4a addition)."""
    _check_needles(needles)
    if not isinstance(text, str):
        raise ValueError("the text to check must be a string")
    _refuse_paths({"detail": text}, needles)


def _launch_line(argv: Any) -> tuple[str, ...]:
    """The S2 §3.3 form of a launch line: element 0 and the model file named, every other element copied."""
    # NEEDS-OWNER(AMB-137): the launch line is written in S2 §3.3 form, llama-server -m <file>
    words = tuple(argv)
    if not words or not all(isinstance(word, str) for word in words) or "-m" not in words[1:-1]:
        raise ValueError("a launch line must hold its binary and '-m <file>'")
    model = words.index("-m", 1) + 1
    return tuple(
        LLAMA_SERVER if index == 0 else MODEL_FILE if index == model else word for index, word in enumerate(words)
    )


def _manifest_value(value: Any) -> Any:
    """A loaded manifest value with lists as tuples and each float as the exact decimal it was written as."""
    if isinstance(value, float):
        return Fraction(repr(value))
    if isinstance(value, list | tuple):
        return tuple(_manifest_value(item) for item in value)
    if isinstance(value, Mapping):
        return {key: _manifest_value(item) for key, item in value.items()}
    return value


# ---------------------------------------------------------------- blocks


def _stop_reason(view: RunView, end: ScheduleEnd, failed: tuple[str, ...], scored: bool) -> tuple[str, ...] | None:
    """Why the report is records-only (G4-2, G4-43), or None for a scored report. It reads the schedule through the
    same `gates` helpers as `gates.schedule_end`, so the reason it gives names the trigger that ended the schedule."""
    if scored:
        return None
    if end == "pre_run_stopped":
        blocked = tuple(
            f"{CANDIDATE_SLUGS[record.candidate]}.pre_run blocked: {check}"
            for record in view.pre_run
            if record.status == "blocked"
            for check in record.blocked
        )
        return blocked or ("no candidate's pre-run was ready",)
    if end == "runs_exhausted":
        return tuple(
            f"{CANDIDATE_SLUGS[candidate]}.run{run}: {count} void attempts (S2 §3.13)"
            for candidate in CANDIDATE_IDS
            for run in RUNS
            if (count := _void_runs(view, candidate, "timed_run", run)) >= MAX_RUN_ATTEMPTS
        )
    if end == "incomplete":
        planned = [candidate["candidate_id"] for candidate in view.manifest["candidates"]]
        seen = {record.candidate for record in view.pre_run}
        reasons = [
            f"truncated: {line.file_name} line {line.line_number}"
            for line in view.truncated
            if not line.file_name.endswith(".marker")
        ]
        reasons += [f"no pre-run record: {CANDIDATE_SLUGS[c]}" for c in CANDIDATE_IDS if c in planned and c not in seen]
        for candidate in _ready(view):
            slug = CANDIDATE_SLUGS[candidate]
            reasons += [
                f"{slug}.run{run}: no counted attempt"
                for run in RUNS
                if not _complete_keys(view, candidate, "timed_run", run)
            ]
            reasons += [
                f"{slug}.{phase}: neither a complete attempt nor {MAX_RUN_ATTEMPTS} voids"
                for phase in _UNGATED
                if not _complete_keys(view, candidate, phase, None)
                and _void_runs(view, candidate, phase, None) < MAX_RUN_ATTEMPTS
            ]
        return tuple(reasons) or ("incomplete",)
    return tuple(f"run condition not passed: {name} (S2 §3.1, §3.2; AMB-132)" for name in failed)


def _recorded(view: RunView, operator: OperatorValues | None, decision: Decision | None) -> dict[str, Any]:
    """S2 §9 "Recorded for every number", from an allowlist: no path, no stamp (spec section 3)."""
    manifest = view.manifest
    raw = manifest["operator"]
    models = raw["prompt_development_models"].split(";") if operator is None else operator.prompt_development_models
    bootstrap = None if decision is None or decision.margin is None else decision.margin.bootstrap
    first = bootstrap.mean if isinstance(bootstrap, MultiRunBootstrap) else bootstrap
    return {
        "operator": {name: raw[name] for name in OPERATOR_FIELDS if name not in _LEFT_OUT},
        "prompt_development_models": tuple(models),
        "pinned_build_info": manifest["pinned_build_info"],
        "binary_sha256": manifest["binary_sha256"],
        "interfaces": _manifest_value(manifest["interfaces"]),
        "conditions": {
            name: _manifest_value(value) for name, value in manifest["conditions"].items() if name != "binary_path"
        },
        "candidates": tuple(
            {
                "candidate_id": candidate["candidate_id"],
                "argv": _launch_line(candidate["argv"]),
                "gguf_sha256": candidate["gguf_sha256"],
                "source_revision": candidate["source_revision"],
                "quantization_commands": _manifest_value(candidate["quantization_commands"]),
            }
            for candidate in manifest["candidates"]
        ),
        "sampling": _manifest_value(manifest["sampling"]),
        "max_tokens": _manifest_value(manifest["max_tokens"]),
        "timeout_sec": _manifest_value(manifest["timeout_sec"]),
        "thinking_mode": manifest["thinking_mode"],
        "hashes": _manifest_value(manifest["hashes"]),
        "extraction_schema_sha256": manifest["extraction_schema_sha256"],
        "probe_schema_sha256": manifest["probe_schema_sha256"],
        "gold_version": manifest["gold_version"],
        "gold_manifest": _manifest_value(manifest["gold_manifest"]),
        "bootstrap": {
            "seed": None if operator is None else operator.bootstrap_seed,
            "method": BOOTSTRAP_METHOD,
            "python_version": None if first is None else first.python_version,
        },
    }


def _budgets(operator: OperatorValues, inputs: ReportInputs) -> dict[str, Any]:
    """The values in force, with their sources and the manifest originals (S2 §6, §7, §10; G4-14, G4-16; AMB-143)."""
    deadline = operator.run_deadline_sec if inputs.deadline_override_sec is None else inputs.deadline_override_sec
    allowance = (
        operator.rerun_allowance_sec
        if inputs.rerun_allowance_override_sec is None
        else inputs.rerun_allowance_override_sec
    )
    reason = inputs.reopening_reason
    differs = {
        "run_deadline_sec": deadline != D5_RUN_DEADLINE_SEC,
        "rerun_allowance_sec": allowance != D5_RERUN_ALLOWANCE_SEC,
    }
    return {
        "idle_free_mib": operator.idle_free_mib,
        "audio_vram_mib": operator.audio_vram_mib,
        "audio_vram_sample_rate": {
            "samples": operator.audio_vram_samples,
            "min_per_whole_second": operator.audio_vram_min_per_sec,
            "rate_ok": operator.audio_vram_min_per_sec >= MIN_SAMPLES_PER_SEC,
        },
        "headroom_mib": HEADROOM_MIB,
        "gate_m_budget_mib": operator.budget_mib,
        "run_deadline_sec": deadline,
        "run_deadline_source": "manifest" if inputs.deadline_override_sec is None else "reopening",
        "run_deadline_manifest": operator.run_deadline_sec,
        "rerun_allowance_sec": allowance,
        "rerun_allowance_source": "manifest" if inputs.rerun_allowance_override_sec is None else "reopening",
        "rerun_allowance_manifest": operator.rerun_allowance_sec,
        "rerun_allowance_note": ALLOWANCE_NOTE,
        "reopening_reason": reason,
        "model_share": MODEL_SHARE,
        "model_budget_sec": deadline * MODEL_SHARE,
        "d5_recorded": {"run_deadline_sec": D5_RUN_DEADLINE_SEC, "rerun_allowance_sec": D5_RERUN_ALLOWANCE_SEC},
        "d5_difference": {
            "differs": differs,
            "flagged": any(differs.values()) and reason is None,
        },
    }


def _d5_note(budgets: Mapping[str, Any]) -> str | None:
    if not budgets["d5_difference"]["flagged"]:
        return None
    return (
        f"a value in force differs from D5's {D5_RUN_DEADLINE_SEC} s run deadline or {D5_RERUN_ALLOWANCE_SEC} s"
        " re-run allowance with no reopening reason (AMB-143)"
    )


def _thinking(result: CandidateResult) -> dict[str, Any]:
    """S2 §3.6: the pre-run verdict decides; thinking seen in run calls is counted and shown prominently (AMB-116)."""
    seen = {kind: count for kind, count in result.thinking_in_runs.items() if count}
    summary = (
        "thinking seen in runs: " + "; ".join(f"{kind} {count} call(s)" for kind, count in sorted(seen.items()))
        if seen
        else "no thinking seen in run calls"
    )
    return {"pre_run": result.thinking_pre_run, "in_runs": result.thinking_in_runs, "summary": summary}


def _attempt_memory(entry: Mapping[str, Any]) -> dict[str, Any]:
    """AMB-110: the attempt's baseline memory.used and memory.free beside the idle figure, reported only."""
    if not entry.get("evidence"):
        return dict(entry)
    below = entry["baseline_free_mib"] - entry["idle_free_mib"]
    return {**entry, "baseline_free_minus_idle_mib": below, "baseline_at_idle": below >= 0}


def _memory(memory: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if memory is None:
        return None
    return {
        **memory,
        "attempts": {unit: _attempt_memory(entry) for unit, entry in memory["attempts"].items()},
        "voided": {unit: _attempt_memory(entry) for unit, entry in memory["voided"].items()},
        "baseline_label": BASELINE_LABEL,
    }


def _metrics(result: CandidateResult) -> dict[str, Any]:
    """Every encodable `RunMetrics` field for runs 1 to 3; run 1 is the run of record (S2 §3.13; AMB-4)."""
    return {
        str(run): {
            **_fields(metrics),
            "run_of_record": run == 1,
            "claim_fidelity": FIDELITY_PENDING,
        }
        for run, metrics in sorted(result.metrics.items())
    }


def _determinism(share: DeterminismShare | None) -> Any:
    if share is None:
        return "did not load"
    return {"differing": share.differing, "compared": share.compared, "share": share.share}


def _candidate(
    result: CandidateResult, share: DeterminismShare | None, operator: OperatorValues, inputs: ReportInputs
) -> dict[str, Any]:
    candidate = result.candidate
    slug = CANDIDATE_SLUGS[candidate]
    return {
        "candidate_id": candidate,
        "slug": slug,
        "status": result.status,
        "information_only": result.information_only,
        "thinking": _thinking(result),
        "section2": operator.section2[candidate],
        "card_settings": {"value": operator.card_settings[candidate], "label": CARD_SETTINGS_LABEL},
        "gates": result.gates,
        "shared_memory": inputs.shared_memory.get(slug, "not_observed"),
        "metrics": _metrics(result),
        "determinism": _determinism(share),
        "voided": result.voided,
        "cutoffs": result.cutoffs,
        "probe_readings": result.probe_readings,
        "probe_readings_disagree": bool(result.probe_readings) and readings_disagree(result.probe_readings),
        "memory": _memory(result.memory),
        "latency": result.latency,
        "contended": result.contended,
        "unconstrained": result.unconstrained,
        "runtime": result.runtime,
    }


def _decision(view: RunView, decision: Decision, budgets: Mapping[str, Any], checked: bool) -> dict[str, Any]:
    """S2 §9 "The decision" (G4-29): the rule under the literal margin reading, the settings and the winner."""
    pinned = decision.pinned
    winner: dict[str, Any] | None = None
    values: Mapping[str, str] | None = None
    if pinned is not None:
        row = next(candidate for candidate in view.manifest["candidates"] if candidate["candidate_id"] == pinned)
        values = settings(pinned, row["source_revision"], row["gguf_sha256"])
        winner = {
            "candidate_id": pinned,
            "source_revision": row["source_revision"],
            "gguf_sha256": row["gguf_sha256"],
            "llama_cpp_commit": view.manifest["operator"]["llama_cpp_commit"],
            "launch_argv": _launch_line(row["argv"]),
        }
    return {
        "rule": decision.rule,
        "pinned": pinned,
        "margin_reading": MARGIN_READING,
        "settings": values,
        "winner": winner,
        "changes_baseline": decision.changes_baseline,
        "margin": {
            "variant": decision.variant,
            "conforming": decision.margin_conforming,
            "note": decision.margin_note,
            "result": decision.margin,
        },
        "pinnable": decision.pinnable,
        "steps": decision.steps,
        "options": RULE5_OPTIONS if pinned is None else None,
        "d5_note": _d5_note(budgets),
        "provisional_note": None
        if checked
        else "provisional: the gold manifest was not checked against the recorded v1.0 values (AMB-139)",
    }


def _reopening(
    view: RunView, abandoned: tuple[AbandonedRun, ...], report_numbers: tuple[tuple[int, bool], ...]
) -> dict[str, Any]:
    """The re-opening lists (S2 §3.13, §8, §10; G4-37)."""
    faults = {fault.fault_id: fault for fault in view.faults}
    named = {void.fault_id for void in view.voids}
    voided = {void.key for void in view.voids}
    unvoided = [fault for fault in view.faults if fault.fault_id not in named]

    def fault_entry(fault: FaultRecord) -> dict[str, Any]:
        return {
            "fault_id": fault.fault_id,
            "unit": fault.key,
            "kind": fault.kind,
            "item_id": fault.item_id,
            "detail": fault.detail,
        }

    # NEEDS-OWNER(AMB-136): no before/after claim; the owner judges §8 amendment eligibility
    on_scored = tuple({**fault_entry(f), "note": SCORED_FAULT_NOTE} for f in unvoided if view.marked(f.key))
    return {
        "voids": tuple(
            {
                "unit": void.key,
                "fault_id": void.fault_id,
                "fault_kind": None if void.fault_id not in faults else faults[void.fault_id].kind,
                "detail": None if void.fault_id not in faults else faults[void.fault_id].detail,
            }
            for void in view.voids
        ),
        "absent_ungated": tuple(
            f"{CANDIDATE_SLUGS[candidate]}.{phase}"
            for candidate in _ready(view)
            for phase in _UNGATED
            if not _complete_keys(view, candidate, phase, None)
        ),
        "abandoned": abandoned,
        "faults_without_void": tuple(
            {**fault_entry(f), "unit_voided": f.key in voided} for f in unvoided if not view.marked(f.key)
        ),
        "faults_on_scored_units": on_scored,
        "incomplete_reports": tuple(number for number, complete in report_numbers if not complete),
        "truncated": view.truncated,
    }


def _binding_inputs(results: Mapping[str, CandidateResult]) -> dict[str, Any]:
    """S2 §9 "Proposed binding values": the measured inputs only (G4-40; AMB-120)."""
    inputs: dict[str, Any] = {"note": BINDING_NOTE}
    for candidate, result in results.items():
        if result.latency is None:
            continue
        inputs[candidate] = {
            "per_attempt": {
                kind: None if dist is None else {"p95_ns": dist.p95, "max_ns": dist.max}
                for kind, dist in result.latency["attempts"].items()
            },
            "longest_by_kind": result.latency["longest_by_kind"],
        }
    return inputs


def build_report(
    view: RunView,
    end: ScheduleEnd,
    results: Mapping[str, CandidateResult] | None,
    shares: Mapping[str, DeterminismShare] | None,
    decision: Decision | None,
    operator: OperatorValues | None,
    inputs: ReportInputs,
    abandoned: tuple[AbandonedRun, ...],
    manifest_sha256: str,
    path_needles: tuple[str, ...],
    *,
    run_name: str,
    report_numbers: tuple[tuple[int, bool], ...],
) -> dict[str, Any]:
    """The encoded report body, every key of `REPORT_KEYS` but `markdown_sha256`, which `write_report` adds.

    A complete schedule whose run conditions were met is scored: `results`, `shares`, `decision` and `operator` are
    all given. Anything else is records-only and all four are None (G4-2, G4-43). `run_name` is the run directory's
    name alone, and `report_numbers` is `records.report_numbers` of the run directory, for the incomplete ones. These
    two keywords are an H4a addition to the spec §3 signature: this pure module cannot read the directory for them.
    A string holding a path needle or a home-directory form raises `ValueError` (G4-38)."""
    if not isinstance(run_name, str) or _RUN_NAME.fullmatch(run_name) is None:
        raise ValueError(f"the report names its run directory by run name only, not {run_name!r}")
    if not isinstance(manifest_sha256, str) or _SHA256.fullmatch(manifest_sha256) is None:
        raise ValueError("manifest_sha256 must be 64 lowercase hex characters")
    _check_needles(path_needles)
    failed = failed_run_conditions(view.manifest["operator"])
    scored = end == "complete" and not failed
    given = [value is not None for value in (results, shares, decision, operator)]
    if any(given) != scored or not (all(given) or not any(given)):
        raise ValueError("results, shares, decision and operator are given together, for a scored report only")
    checked = scored and inputs.expected is not None
    candidates = budgets = decision_block = binding = None
    if results is not None and shares is not None and decision is not None and operator is not None:
        budgets = _budgets(operator, inputs)
        candidates = tuple(
            _candidate(results[candidate], shares.get(candidate), operator, inputs)
            for candidate in CANDIDATE_IDS
            if candidate in results
        )
        decision_block = _decision(view, decision, budgets, checked)
        binding = _binding_inputs(results)
    body = {
        "schema": REPORT_SCHEMA,
        "record": "report",
        "run_name": run_name,
        "manifest_sha256": manifest_sha256,
        "schedule_end": end,
        "stop_reason": _stop_reason(view, end, failed, scored),
        "run_conditions_met": not failed,
        "gold_manifest_checked": checked,
        "recorded": _recorded(view, operator, decision),
        "budgets": budgets,
        "candidates": candidates,
        "decision": decision_block,
        "reopening": _reopening(view, abandoned, report_numbers),
        "binding_value_inputs": binding,
        "caveats": CAVEATS,
        "open_rulings": OPEN_RULINGS,
        "pending": {"AMB-4": FIDELITY_PENDING},
    }
    encoded = to_report_value(body)
    _refuse_paths(encoded, path_needles)
    return encoded


def check_paths(
    view: RunView,
    inputs: ReportInputs,
    abandoned: tuple[AbandonedRun, ...],
    report_numbers: tuple[tuple[int, bool], ...],
    needles: tuple[str, ...],
) -> None:
    """The G4-38 walk over every string a scored report copies from the records and the operator, run by `entry`
    before any marker exists (H4a addition): the recorded allowlist with its launch lines, every operator field (the
    section 2 and card fields reach the candidate blocks), the reopening reason and the re-opening lists. A path
    raises `ValueError`; a malformed launch line raises `ValueError`, and an unencodable value `TypeError`. Strings
    `gates.evaluate` derives after marking are walked again by `build_report`."""
    _check_needles(needles)
    raw = view.manifest["operator"]
    value = to_report_value(
        {
            "recorded": _recorded(view, None, None),
            "operator_all": {name: _manifest_value(raw[name]) for name in OPERATOR_FIELDS},
            "reopening_reason": inputs.reopening_reason,
            "reopening": _reopening(view, abandoned, report_numbers),
        }
    )
    _refuse_paths(value, needles)
