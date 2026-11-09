"""Schedule end, integrity checks and the seven gates of a bake-off (bake-off harness H4a).

This module reads a loaded run directory and decides nothing it cannot prove from the records. It is pure: `entry`
loads the run, the gold set and the evidence, sets the scored markers between `check_records` and `evaluate`, and
writes the report. Gate figures are exact `Fraction` or `int`.

Implements, from the signed D6 criteria note S2:

- S2 §3.13, runs: `schedule_end` is read from the records alone, and anything but `complete` gets a records-only
  report. `check_records` refuses a run whose records are not the frozen plan before any marker exists. "Gate M uses
  the peak over all runs"; gates A, S, J and C and the quality metric of record use run 1; voided runs are listed and
  never counted. `evaluate` refuses any unit whose scored marker is missing.
- S2 §5, metrics: runs 1 to 3 are scored with H1's `score_run` on the counted single-conversation calls.
- S2 §6, gates: L, M, J, C, T, A and S, as the note and its owner rows fix them. Gate M reads machine evidence through
  the pure `evidence` types and never the `machine` module.
- S2 §3.6, thinking: the pre-run verdict decides eligibility; thinking seen in run calls is counted and reported.
- S2 §3.8, diagnostic pass: gate C on the unconstrained pass and raw validity are reported, never gated.
- S2 §9, report: every reported-only figure (latency, cut-offs, fences, junk ids, graph splits, clocks, temperature
  and power, the together session) is built here, so the report only encodes it.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType
from typing import Any, Literal, cast

from .client import REQUEST_KINDS, CallRecord
from .evidence import MIN_SAMPLES_PER_SEC, AttemptEvidence
from .extraction import FAILURE_KINDS, Extraction
from .gold import GoldSet
from .inputs import CAP_SESSION_COUNT, CAP_SESSION_ROWS, GOLD_VERSION, PROBE_ITEM_COUNT
from .launch import CANDIDATE_IDS, CANDIDATE_SLUGS
from .records import (
    _HASH_KEYS,
    CARD_SETTINGS_FIELDS,
    RUNS,
    SECTION2_FIELDS,
    LaunchRecord,
    Phase,
    PreRunRecord,
    RunKey,
    RunView,
    StoredCall,
    StoreError,
    ThinkingVerdict,
    _unit_name,
    record_loads,
)
from .runner import MAX_RUN_ATTEMPTS
from .scoring import GateResult, RunMetrics, gate_a, gate_s, score_run

GateName = Literal["L", "M", "J", "C", "T", "A", "S"]
GATES: tuple[GateName, ...] = ("L", "M", "J", "C", "T", "A", "S")
GateStatus = Literal["pass", "fail", "not_measured"]
ScheduleEnd = Literal["complete", "pre_run_stopped", "runs_exhausted", "incomplete"]
ProbeReading = Literal["first_session", "every_session", "pooled", "per_item", "majority"]
PROBE_READINGS: tuple[ProbeReading, ...] = ("first_session", "every_session", "pooled", "per_item", "majority")
SharedMemory = Literal["not_rising", "rose", "not_observed"]
SHARED_MEMORY_VALUES: tuple[SharedMemory, ...] = ("not_rising", "rose", "not_observed")

J_FIRST_ATTEMPT_MAX = 3  # S2 §6 gate J [owner, §12 row 11]
J_AFTER_RETRY_MAX = 1
J_PROBE_MAX = 1
C_MAX = 1  # S2 §6 gate C [owner, §12 row 12]
MODEL_SHARE = Fraction(1, 2)  # S2 §6 gate T: the model's share of the run deadline
HEADROOM_MIB = 512  # S2 §6 gate M
RAM_CAP_BYTES = 4_000_000_000  # S2 §6 gate M, 4 GB read as 4 x 10^9 bytes (AMB-112)
MIN_CAP_CLAIMS = 10  # S2 §6 gate T
SINGLE_OVER_NS = 30_000_000_000  # client.LLM_TIMEOUT_SEC, S2 §6 "over 30 s"
SYSMEM_POLICY = "Prefer No Sysmem Fallback"  # Annex A
PASSED = "passed"
RUN_CONDITION_FIELDS = (
    "cuda_process_check",
    "namespace_outbound_check",
    "namespace_eth0_check",
    "namespace_lan_check",
)  # S2 §3.1, §3.2 (AMB-132)
D5_RUN_DEADLINE_SEC = Fraction(125)  # PHASE-0 §1 row D5, §7 (AMB-143)
D5_RERUN_ALLOWANCE_SEC = Fraction(25)

_COUNT = re.compile(r"[0-9]+")  # fullmatch only
_DECIMAL = re.compile(r"[0-9]+([.][0-9]+)?")  # fullmatch only
_MODEL_ENTRY = re.compile(r"[!-?A-~]+(@[!-?A-~]+)?")  # fullmatch only: printable ASCII, no space, at most one "@"
_NUMERIC = re.compile(r"([0-9]+(?:[.][0-9]+)?) ?([A-Za-z%]+)?")  # fullmatch only: a decimal with an optional unit
_SHA256 = re.compile(r"[0-9a-f]{64}")  # fullmatch only
_FENCE = "`" * 3
_MAJORITY = 3  # AMB-66 "majority": failing in at least 3 of the 4 sets
_UNGATED: tuple[Phase, ...] = ("unconstrained", "contended")
_CHECK_OPS = (">=", "<=", "==", "<")
_GATE_STATUSES = ("pass", "fail", "not_measured")
_FREEZE_KEYS = ("gold_version", "gold_manifest", "hashes")
_REPORTED = "reported, not gated"


class ReportError(RuntimeError):
    """An integrity or operator-value refusal. Raised before marking, nothing is marked or written; `entry` reports a
    failure after marking began apart from these refusals."""


Value = Fraction | int | str | None


@dataclass(frozen=True, slots=True)
class Check:
    """One comparison of a gate. A float is refused, as `scoring` refuses it: figures are exact."""

    name: str
    value: Value
    limit: Value
    op: Literal[">=", "<=", "==", "<"]
    passed: bool

    def __post_init__(self) -> None:
        if isinstance(self.value, float) or isinstance(self.limit, float):
            raise TypeError(f"{self.name}: gate figures must be exact, not float")
        if self.op not in _CHECK_OPS:
            raise ValueError(f"Check.op must be one of {_CHECK_OPS}, not {self.op!r}")

    @property
    def measured(self) -> bool:
        return self.value is not None and self.limit is not None


@dataclass(frozen=True, slots=True)
class Gate:
    gate: GateName
    status: GateStatus
    checks: tuple[Check, ...]
    notes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.gate not in GATES or self.status not in _GATE_STATUSES:
            raise ValueError(f"Gate {self.gate!r} with status {self.status!r} is not a gate result")


def _check(name: str, value: Value, limit: Value, op: Literal[">=", "<=", "==", "<"]) -> Check:
    """An inclusive bound or an equality on exact values; a missing value or limit never passes."""
    if value is None or limit is None:
        return Check(name, value, limit, op, False)
    if op == "==":
        passed = value == limit
    elif isinstance(value, str) or isinstance(limit, str):
        raise TypeError(f"{name}: text can only be compared for equality")
    elif op == ">=":
        passed = value >= limit
    elif op == "<=":
        passed = value <= limit
    else:
        passed = value < limit
    return Check(name, value, limit, op, passed)


def _gate(gate: GateName, checks: Iterable[Check], notes: Iterable[str] = ()) -> Gate:
    checks = tuple(checks)
    return Gate(gate, "pass" if all(check.passed for check in checks) else "fail", checks, tuple(notes))


def _not_measured(gate: GateName, note: str) -> Gate:
    return Gate(gate, "not_measured", (), (note,))


# ---------------------------------------------------------------- operator values and report inputs


def _text(operator: Mapping[str, str], name: str) -> str:
    value = operator.get(name)
    if not isinstance(value, str):
        raise ReportError(f"operator.{name} is missing or not text")
    return value


def _filled(operator: Mapping[str, str], name: str) -> str:
    value = _text(operator, name)
    if not value.strip():
        raise ReportError(f"operator.{name} must not be blank")
    return value


def _count(operator: Mapping[str, str], name: str) -> int:
    value = _text(operator, name)
    if _COUNT.fullmatch(value) is None:
        raise ReportError(f"operator.{name} must be digits only, not {value!r} (G4-6)")
    return int(value)


def _seconds(operator: Mapping[str, str], name: str) -> Fraction:
    value = _text(operator, name)
    if _DECIMAL.fullmatch(value) is None or Fraction(value) <= 0:
        raise ReportError(f"operator.{name} must be a plain decimal above 0, not {value!r} (G4-6)")
    return Fraction(value)


def _models(value: str) -> tuple[str, ...]:
    entries = tuple(value.split(";"))
    if not value or not all(entry and entry.isascii() and _MODEL_ENTRY.fullmatch(entry) for entry in entries):
        raise ReportError(
            "operator.prompt_development_models must be a ';'-separated list of <model id>[@<revision>] (S2 §3.10)"
        )
    return entries


@dataclass(frozen=True, slots=True)
class OperatorValues:
    """The operator fields the gates read, parsed exactly (S2 §3.7, §3.10, §6, §7, Annex A; G4-6)."""

    idle_free_mib: int
    audio_vram_mib: int
    audio_vram_samples: int
    audio_vram_min_per_sec: int
    run_deadline_sec: Fraction
    rerun_allowance_sec: Fraction
    bootstrap_seed: int
    section2: Mapping[str, str]
    card_settings: Mapping[str, str]
    prompt_development_models: tuple[str, ...]
    sysmem_fallback: str
    run_conditions: Mapping[str, str]

    @classmethod
    def from_manifest(cls, operator: Mapping[str, str]) -> OperatorValues:
        """The values, or `ReportError` naming the first one that breaks its pattern (G4-6)."""
        if not isinstance(operator, Mapping):
            raise ReportError("the manifest's operator values must be an object")
        idle = _count(operator, "idle_free_vram_mib")
        audio = _count(operator, "audio_pipeline_vram_mib")
        if idle - audio - HEADROOM_MIB <= 0:
            raise ReportError(f"the gate M budget {idle} - {audio} - {HEADROOM_MIB} MiB must be above 0 (G4-16)")
        min_per_sec = _count(operator, "audio_vram_min_per_sec")
        if min_per_sec < MIN_SAMPLES_PER_SEC:
            raise ReportError(f"operator.audio_vram_min_per_sec must be at least {MIN_SAMPLES_PER_SEC} (S2 §6)")
        _filled(operator, "cuda_process_check")
        # NEEDS-OWNER(AMB-135): the prompt-development models come from an operator field, not the freeze manifest
        models = _models(_text(operator, "prompt_development_models"))
        return cls(
            idle_free_mib=idle,
            audio_vram_mib=audio,
            audio_vram_samples=_count(operator, "audio_vram_samples"),
            audio_vram_min_per_sec=min_per_sec,
            run_deadline_sec=_seconds(operator, "run_deadline_sec"),
            rerun_allowance_sec=_seconds(operator, "rerun_allowance_sec"),
            bootstrap_seed=_count(operator, "bootstrap_seed"),
            section2=MappingProxyType({cid: _filled(operator, SECTION2_FIELDS[cid]) for cid in CANDIDATE_IDS}),
            card_settings=MappingProxyType(
                {cid: _filled(operator, CARD_SETTINGS_FIELDS[cid]) for cid in CANDIDATE_IDS}
            ),
            prompt_development_models=models,
            sysmem_fallback=_text(operator, "sysmem_fallback"),
            run_conditions=MappingProxyType({name: _filled(operator, name) for name in RUN_CONDITION_FIELDS}),
        )

    @property
    def budget_mib(self) -> int:
        """Idle free VRAM minus the audio pipeline's VRAM minus 512 MiB (G4-16)."""
        budget = self.idle_free_mib - self.audio_vram_mib - HEADROOM_MIB
        if budget <= 0:
            raise ReportError(f"the gate M budget is {budget} MiB: it must be above 0")
        return budget

    @property
    def run_conditions_met(self) -> bool:
        return not failed_run_conditions(self.run_conditions)


def failed_run_conditions(operator: Mapping[str, str]) -> tuple[str, ...]:
    """The run-condition fields whose raw value is not exactly "passed" (G4-43, AMB-132), in field order."""
    return tuple(name for name in RUN_CONDITION_FIELDS if operator.get(name) != PASSED)


@dataclass(frozen=True, slots=True)
class ExpectedFreeze:
    """The v1.0 values recorded at the freeze (S2 §3.12, §11 item 1; AMB-139)."""

    gold_version: str
    gold_manifest: Mapping[str, str]
    hashes: Mapping[str, Any]


def _plain(value: Any) -> Any:
    """Mappings as dicts, all the way down, so a frozen copy compares with a loaded manifest."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _is_text_map(value: object) -> bool:
    return isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def parse_expected_freeze(raw: bytes) -> ExpectedFreeze:
    """The expected-freeze file, with exact keys at every level, or `ReportError` (G4-1 (b))."""
    try:
        data = record_loads(raw)
    except StoreError as exc:
        raise ReportError(f"the expected-freeze file is not one JSON object: {exc}") from None
    if not isinstance(data, dict) or set(data) != set(_FREEZE_KEYS):
        raise ReportError(f"the expected-freeze file must hold exactly the keys {list(_FREEZE_KEYS)}")
    hashes = data["hashes"]
    valid = (
        isinstance(data["gold_version"], str)
        and _is_text_map(data["gold_manifest"])
        and isinstance(hashes, dict)
        and set(hashes) == set(_HASH_KEYS)
        and all(
            _is_text_map(hashes[name]) if name.startswith("requests") else isinstance(hashes[name], str)
            for name in _HASH_KEYS
        )
    )
    if not valid:
        raise ReportError("the expected-freeze file's gold_manifest or hashes do not have the freeze's shape")
    return ExpectedFreeze(
        data["gold_version"],
        MappingProxyType(dict(data["gold_manifest"])),
        MappingProxyType({name: _plain(hashes[name]) for name in _HASH_KEYS}),
    )


@dataclass(frozen=True, slots=True)
class ReportInputs:
    """What the operator supplies at report time (G4-14, G4-21). `shared_memory` is keyed by candidate slug."""

    shared_memory: Mapping[str, SharedMemory]
    shared_memory_evidence_sha256: str | None
    deadline_override_sec: Fraction | None
    rerun_allowance_override_sec: Fraction | None
    reopening_reason: str | None
    expected: ExpectedFreeze | None


def shared_memory_entries(pairs: Iterable[tuple[str, str]]) -> Mapping[str, SharedMemory]:
    """The `--shared-memory` entries by slug. A slug given twice is a `ReportError` (G4-21), so a mapping never hides
    the second value."""
    entries: dict[str, SharedMemory] = {}
    for slug, value in pairs:
        if slug in entries:
            raise ReportError(f"--shared-memory names {slug} twice (G4-21)")
        if value not in SHARED_MEMORY_VALUES:
            raise ReportError(f"--shared-memory {slug}={value} is not one of {SHARED_MEMORY_VALUES}")
        entries[slug] = cast(SharedMemory, value)
    return MappingProxyType(entries)


def _ready(view: RunView) -> tuple[str, ...]:
    ready = {record.candidate for record in view.pre_run if record.status == "ready"}
    return tuple(candidate for candidate in CANDIDATE_IDS if candidate in ready)


def check_report_inputs(view: RunView, operator: OperatorValues, inputs: ReportInputs) -> None:
    """The report inputs, before any marker: the shared-memory slugs (G4-21) and the re-opening values (G4-14). A
    re-opening fires only on a change (S2 §10), so the supplied values are compared exactly with the manifest's."""
    ready = {CANDIDATE_SLUGS[candidate] for candidate in _ready(view)}
    for slug, value in inputs.shared_memory.items():
        if slug not in CANDIDATE_SLUGS.values():
            raise ReportError(f"--shared-memory names {slug!r}, which is no candidate slug (G4-21)")
        if slug not in ready:
            raise ReportError(f"--shared-memory names {slug}, whose pre-run was not ready (G4-21)")
        if value not in SHARED_MEMORY_VALUES:
            raise ReportError(f"--shared-memory {slug}={value!r} is not one of {SHARED_MEMORY_VALUES}")
    digest = inputs.shared_memory_evidence_sha256
    if digest is not None and _SHA256.fullmatch(digest) is None:
        raise ReportError("--shared-memory-evidence-sha256 must be 64 lowercase hex characters")
    supplied = [
        (value, recorded)
        for value, recorded in (
            (inputs.deadline_override_sec, operator.run_deadline_sec),
            (inputs.rerun_allowance_override_sec, operator.rerun_allowance_sec),
        )
        if value is not None
    ]
    reason = inputs.reopening_reason
    if not supplied:
        if reason is not None:
            raise ReportError("--reopening-reason needs --deadline-sec or --rerun-allowance-sec (G4-14)")
        return
    if not all(isinstance(value, Fraction) and value > 0 for value, _ in supplied):
        raise ReportError("a re-opening value must be an exact number above 0 (G4-14)")
    if reason is None or not reason.strip():
        raise ReportError("a re-opening value needs --reopening-reason (G4-14)")
    if all(value == recorded for value, recorded in supplied):
        raise ReportError("no supplied value differs from the manifest: S2 §10 re-opens only on a change (G4-14)")


# ---------------------------------------------------------------- schedule end and integrity


def _void_keys(view: RunView) -> frozenset[RunKey]:
    return frozenset(void.key for void in view.voids)


def _complete_keys(view: RunView, candidate: str, phase: Phase, run: int | None) -> list[RunKey]:
    voided = _void_keys(view)
    return [
        record.key
        for record in view.runs
        if (record.key.candidate, record.key.phase, record.key.run) == (candidate, phase, run)
        and record.status == "complete"
        and record.key not in voided
    ]


def _void_runs(view: RunView, candidate: str, phase: Phase, run: int | None) -> int:
    return sum(
        (record.key.candidate, record.key.phase, record.key.run) == (candidate, phase, run) and record.status == "void"
        for record in view.runs
    )


def schedule_end(view: RunView) -> ScheduleEnd:
    """How the schedule ended, from the records alone (G4-2). The first that holds wins: `pre_run_stopped`, then
    `runs_exhausted`, then `incomplete`, then `complete`."""
    planned = tuple(candidate["candidate_id"] for candidate in view.manifest["candidates"])
    pre_run = {record.candidate: record for record in view.pre_run}
    if any(record.status == "blocked" for record in view.pre_run):
        return "pre_run_stopped"
    if all(candidate in pre_run for candidate in planned) and not _ready(view):
        return "pre_run_stopped"
    exhausted = Counter(
        (record.key.candidate, record.key.run)
        for record in view.runs
        if record.key.phase == "timed_run" and record.status == "void"
    )
    if any(count >= MAX_RUN_ATTEMPTS for count in exhausted.values()):
        return "runs_exhausted"
    if any(not line.file_name.endswith(".marker") for line in view.truncated):
        return "incomplete"
    if any(candidate not in pre_run for candidate in planned):
        return "incomplete"
    for candidate in _ready(view):
        if any(not _complete_keys(view, candidate, "timed_run", run) for run in RUNS):
            return "incomplete"
        for phase in _UNGATED:
            if (
                not _complete_keys(view, candidate, phase, None)
                and _void_runs(view, candidate, phase, None) < MAX_RUN_ATTEMPTS
            ):
                return "incomplete"
    return "complete"


def counted_unit(view: RunView, candidate: str, phase: Phase) -> RunKey | None:
    """The one complete, unvoided attempt of an ungated unit, or None when the unit is absent (G4-4)."""
    if phase not in _UNGATED:
        raise ValueError(f"counted_unit reads the ungated phases {_UNGATED} only, not {phase!r}")
    keys = _complete_keys(view, candidate, phase, None)
    if len(keys) > 1:
        raise ReportError(f"{candidate} {phase} has {len(keys)} complete, unvoided attempts, not one")
    return keys[0] if keys else None


def _counted_key(view: RunView, candidate: str, run: int) -> RunKey:
    try:
        return RunKey(candidate, "timed_run", run, view.counted_attempt(candidate, run))
    except StoreError as exc:
        raise ReportError(str(exc)) from None


def _unit_calls(view: RunView, key: RunKey) -> tuple[StoredCall, ...]:
    """A unit's stored calls in order, warm-ups left out."""
    return tuple(stored for stored in view.calls if stored.key == key and stored.role != "warmup")


def units_to_mark(view: RunView) -> tuple[RunKey, ...]:
    """Runs 1 to 3 of each ready candidate, then its counted unconstrained and contended units (G4-3)."""
    keys: list[RunKey] = []
    for candidate in _ready(view):
        keys += [_counted_key(view, candidate, run) for run in RUNS]
        keys += [key for phase in _UNGATED if (key := counted_unit(view, candidate, phase)) is not None]
    return tuple(keys)


def _plan(phase: Phase, conversations: int) -> set[tuple[str, int | None, int | None]]:
    """The places of one unit (AMB-66, AMB-67, AMB-68): role, session index, position."""
    probes = range(1, PROBE_ITEM_COUNT + 1)
    if phase == "contended":
        return {("cap_session", 1, None), *(("probe", 1, position) for position in probes)}
    places: set[tuple[str, int | None, int | None]] = {
        ("single", None, position) for position in range(1, conversations + 1)
    }
    places |= {("cap_session", index, None) for index in range(1, CAP_SESSION_COUNT + 1)}
    if phase == "unconstrained":
        return places | {("probe", None, position) for position in probes}
    return places | {("probe", index, position) for index in range(1, CAP_SESSION_COUNT + 1) for position in probes}


def _check_unit(key: RunKey, calls: tuple[StoredCall, ...], gold: GoldSet, hashes: Mapping[str, Any]) -> None:
    """G4-1 (c) and (d) for one counted unit."""
    unit = _unit_name(key)
    places: list[tuple[str, int | None, int | None]] = [(s.role, s.session_index, s.position) for s in calls]
    if len(set(places)) != len(places):
        raise ReportError(f"{unit}: a place appears twice in the stored calls")
    plan = _plan(key.phase, len(gold.conversation_ids()))
    missing, extra = len(plan - set(places)), len(set(places) - plan)
    if missing or extra:
        raise ReportError(f"{unit}: the stored calls differ from the plan: {missing} missing, {extra} unexpected")
    singles = sorted(stored.call.item_id for stored in calls if stored.role == "single")
    if key.phase != "contended" and singles != list(gold.conversation_ids()):
        raise ReportError(f"{unit}: the single calls are not one per gold conversation")
    caps = {stored.session_index: stored.session_id for stored in calls if stored.role == "cap_session"}
    if any(stored.role == "probe" and stored.session_id != caps.get(stored.session_index) for stored in calls):
        raise ReportError(f"{unit}: a probe's session id is not its session's cap call")
    for stored in calls:
        table = hashes["requests"] if stored.call.constrained else hashes["requests_unconstrained"]
        if stored.call.attempts[0].request_sha256 != table.get(stored.call.item_id):
            raise ReportError(
                f"{unit} {stored.call.item_id}: the first attempt was not the frozen request of its kind (S2 §3.10)"
            )


def check_records(view: RunView, gold: GoldSet, expected: ExpectedFreeze | None) -> None:
    """The integrity checks that come before any marker (G4-1), or `ReportError`."""
    manifest = view.manifest
    if manifest["gold_version"] != GOLD_VERSION:
        raise ReportError(f"the manifest's gold version is {manifest['gold_version']!r}, not {GOLD_VERSION} (S2 §3.12)")
    if gold.version != manifest["gold_version"] or dict(gold.manifest) != manifest["gold_manifest"]:
        raise ReportError("the gold set differs from the manifest's gold version or gold manifest")
    # NEEDS-OWNER(AMB-139): no expected-freeze file -> gold_manifest_checked false, decision provisional
    if expected is not None:
        if expected.gold_version != GOLD_VERSION:
            raise ReportError(f"the recorded freeze is {expected.gold_version!r}, not {GOLD_VERSION}")
        if _plain(expected.gold_manifest) != manifest["gold_manifest"]:
            raise ReportError("gold manifest differs from the recorded freeze")
        if _plain(expected.hashes) != manifest["hashes"]:
            raise ReportError("the request hashes differ from the recorded freeze")
    stored = Counter(call.key for call in view.calls)
    seen: set[RunKey] = set()
    for record in view.runs:
        if record.key in seen:
            raise ReportError(f"{_unit_name(record.key)} has two run records")
        seen.add(record.key)
        if record.calls != stored[record.key]:
            raise ReportError(
                f"{_unit_name(record.key)}: the run record counts {record.calls} calls, {stored[record.key]} are stored"
            )
    for candidate in _ready(view):
        for run in RUNS:
            key = _counted_key(view, candidate, run)
            _check_unit(key, view.counted_calls(candidate, run), gold, manifest["hashes"])
        for phase in _UNGATED:
            unit_key = counted_unit(view, candidate, phase)
            if unit_key is not None:
                _check_unit(unit_key, _unit_calls(view, unit_key), gold, manifest["hashes"])


# ---------------------------------------------------------------- session-runs and the run gates


@dataclass(frozen=True, slots=True)
class SessionRun:
    """One cap-session call and its session's probes, in position order (S2 §6 gate T). `run` is None for the
    contended session-run."""

    run: int | None
    session_index: int
    session_id: str
    cap: StoredCall
    probes: tuple[StoredCall, ...]

    @property
    def total_ns(self) -> int:
        """Every call's total, retries and failed probes included (client.py: `total_wall_ns`)."""
        return self.cap.call.total_wall_ns + sum(probe.call.total_wall_ns for probe in self.probes)

    @property
    def claims(self) -> int | None:
        """The valid cap output's claims, duplicates included (AMB-117), or None when the cap call failed."""
        output = self.cap.call.output
        return len(output.claims) if isinstance(output, Extraction) else None

    @property
    def cap_valid(self) -> bool:
        return self.cap.call.outcome == "valid"


def session_runs(calls: tuple[StoredCall, ...], run: int | None) -> tuple[SessionRun, ...]:
    """The session-runs of one unit's calls, by session index."""
    caps: dict[int, StoredCall] = {}
    probes: dict[int, list[StoredCall]] = {}
    for stored in calls:
        index = stored.session_index
        if index is None:
            continue
        if stored.role == "cap_session":
            if index in caps:
                raise ReportError(f"session {index} holds two cap-session calls")
            caps[index] = stored
        elif stored.role == "probe":
            probes.setdefault(index, []).append(stored)
    if set(probes) - set(caps):
        raise ReportError("a session's probes have no cap-session call")
    return tuple(
        SessionRun(
            run,
            index,
            caps[index].session_id or "",
            caps[index],
            tuple(sorted(probes.get(index, ()), key=lambda stored: stored.position or 0)),
        )
        for index in sorted(caps)
    )


def gate_l(operator: OperatorValues, candidate_id: str, pre_run: PreRunRecord) -> Gate:
    """Gate L (S2 §2, §6; AMB-115): the operator's section 2 result is exactly "passed", and the pinned build loads
    the model ("converts and runs it")."""
    section2 = operator.section2[candidate_id]
    notes = ("not loaded: the pinned build did not run it (S2 section 2)",) if pre_run.status == "not_loaded" else ()
    return _gate(
        "L",
        (_check("section2", section2, PASSED, "=="), _check("pre_run_status", pre_run.status, "ready", "==")),
        notes,
    )


def gate_j(singles: tuple[StoredCall, ...], probes: tuple[StoredCall, ...], n_conversations: int) -> Gate:
    """Gate J (S2 §6 [owner, §12 row 11]): over run 1's singles, at most 3 first attempts fail and at most 1 call
    fails after its retry; over the gated probes, at most 1 call fails. Every failure kind counts."""
    return _gate(
        "J",
        (
            _check("singles", len(singles), n_conversations, "=="),
            _check(
                "single_first_attempt_failures",
                sum(s.call.first_attempt_failed for s in singles),
                J_FIRST_ATTEMPT_MAX,
                "<=",
            ),
            _check(
                "single_failures_after_retry", sum(s.call.outcome != "valid" for s in singles), J_AFTER_RETRY_MAX, "<="
            ),
            _check("probe_failures", sum(p.call.outcome != "valid" for p in probes), J_PROBE_MAX, "<="),
        ),
    )


def _c_failed(call: CallRecord) -> bool:
    """Gate C's test on a first attempt (H2 R35; AMB-27): it cites nothing, or an id outside the bundle."""
    first = call.attempts[0]
    return not first.cited_source_ids or bool(first.unknown_source_ids)


def gate_c(probes: tuple[StoredCall, ...]) -> Gate:
    """Gate C (S2 §6 [owner, §12 row 12]): at most 1 probe first attempt fails the citation test. A first attempt
    that timed out cites nothing, so it fails even when its retry is valid."""
    failures = sum(_c_failed(probe.call) for probe in probes)
    return _gate("C", (_check("probe_first_attempt_failures", failures, C_MAX, "<="),))


def gate_t(sessions: tuple[SessionRun, ...], deadline_sec: Fraction) -> Gate:
    """Gate T (S2 §6 [owner, §12 row 17]; AMB-117, AMB-118): all 12 session-runs of runs 1 to 3 have a valid cap call
    with at least 10 claims and a total within the model's share of the deadline, exactly and inclusively."""
    expected = CAP_SESSION_COUNT * len(RUNS)
    budget = deadline_sec * 10**9
    checks = [_check("session_runs", len(sessions), expected, "==")]
    for session in sessions:
        prefix = f"run{session.run}.session{session.session_index}"
        checks += [
            _check(f"{prefix}.cap_outcome", session.cap.call.outcome, "valid", "=="),
            _check(f"{prefix}.claims", session.claims, MIN_CAP_CLAIMS, ">="),
            _check(f"{prefix}.twice_total_ns", 2 * session.total_ns, budget, "<="),
        ]
    return _gate("T", checks, (f"model budget: {deadline_sec * MODEL_SHARE} s per session-run",))


def _seconds_text(ms: int) -> str:
    return f"{ms // 1000}.{ms % 1000:03d}"


def gate_m(
    timed: Mapping[RunKey, AttemptEvidence | None],
    contended: AttemptEvidence | None,
    contended_present: bool,
    pre_run_launch: LaunchRecord | None,
    operator: OperatorValues,
    shared_memory: SharedMemory,
    evidence_sha256: str | None,
) -> Gate:
    """Gate M (S2 §6 [owner, §12 row 10]; G4-16 to G4-22). It fails on any measured check that fails, is
    `not_measured` when a check has no figure, and passes only when every check is measured and passes."""
    budget = operator.budget_mib
    notes: list[str] = []
    missing = [_unit_name(key) for key, evidence in timed.items() if evidence is None]
    if missing:
        notes.append(f"no host evidence for {', '.join(missing)}: not measured")
    measured = [evidence for evidence in timed.values() if evidence is not None]
    # NEEDS-OWNER(AMB-106): the gate reads the uncontended runs' rise; the contended rise is reported only
    rise = max(evidence.rise_mib for evidence in measured) if measured and not missing else None
    startup = None if pre_run_launch is None else pre_run_launch.startup
    layers = (None, None) if startup is None else (startup.layers_offloaded, startup.layers_total)
    buffers: Fraction | None = None
    if startup is not None and None not in (startup.cuda_model_mib, startup.cuda_kv_mib, startup.cuda_compute_mib):
        parts = (startup.cuda_model_mib, startup.cuda_kv_mib, startup.cuda_recurrent_mib or 0, startup.cuda_compute_mib)
        buffers = sum((Fraction(str(part)) for part in parts), Fraction(0))
    checks = [
        _check("server_rise_mib", rise, budget, "<="),
        _check("layers_offloaded", layers[0], layers[1], "=="),
        _check("rise_vs_cuda_buffers_mib", rise, buffers, ">="),
    ]
    for key, evidence in timed.items():
        unit = _unit_name(key)
        checks.append(_check(f"rss_anon_bytes.{unit}", _rss(evidence), RAM_CAP_BYTES, "<="))
        checks.append(_check(f"swap_delta.{unit}", _swap_delta(evidence), 0, "=="))
    if contended_present and contended is not None:
        unit = _unit_name(contended.key)
        checks.append(_check(f"rss_anon_bytes.{unit}", _rss(contended), RAM_CAP_BYTES, "<="))
        checks.append(_check(f"swap_delta.{unit}", _swap_delta(contended), 0, "=="))
        limit = operator.idle_free_mib - HEADROOM_MIB
        notes.append(
            f"together session combined rise {contended.rise_mib} MiB against idle free - 512 = {limit} MiB:"
            " reported beside gate M, not gated"
        )
        if contended.peak_rss_anon_bytes is None:
            notes.append("together session has no RSS sample: infrastructure gap, the contended unit needs a repeat")
        record = contended.host.contention
        if record is not None and record.ended_before_stop:
            ms = record.ended_before_stop_ms
            ended = "an unknown time" if ms is None else _seconds_text(ms)
            notes.append(f"contention partial: audio ended {ended} s before stop")
    else:
        _no_together_session(checks, notes, contended_present)
    notes.append(f"shared memory: {shared_memory}")
    if operator.sysmem_fallback.strip() == SYSMEM_POLICY:
        checks.append(_check("sysmem_fallback", operator.sysmem_fallback.strip(), SYSMEM_POLICY, "=="))
    elif shared_memory == "rose":
        checks.append(_check("shared_memory", "rose", "not_rising", "=="))
    elif shared_memory == "not_rising" and evidence_sha256 is not None:
        checks.append(_check("shared_memory", "not_rising", "not_rising", "=="))
    else:
        checks.append(_check("shared_memory", None, "not_rising", "=="))
        notes.append("shared memory not observed: the policy text or a not_rising entry with the counter log hash")
    failed = any(check.measured and not check.passed for check in checks)
    status: GateStatus = "fail" if failed else "pass" if all(check.measured for check in checks) else "not_measured"
    return Gate("M", status, tuple(checks), tuple(notes))


def _no_together_session(checks: list[Check], notes: list[str], present: bool) -> None:
    """G4-22: the contended unit is absent after MAX_RUN_ATTEMPTS voids, or its counted attempt left no host
    evidence."""
    # NEEDS-OWNER(AMB-106): absent or evidence-less together session -> swap/RAM not_measured, M not passed (see AMB-69)
    checks.append(_check("together_session.rss_anon_bytes", None, RAM_CAP_BYTES, "<="))
    checks.append(_check("together_session.swap_delta", None, 0, "=="))
    cause = "without host evidence" if present else "absent"
    notes.append(f"together session {cause}: infrastructure gap, the contended unit needs a repeat (AMB-106, AMB-69)")


def _rss(evidence: AttemptEvidence | None) -> int | None:
    return None if evidence is None else evidence.peak_rss_anon_bytes


def _swap_delta(evidence: AttemptEvidence | None) -> int | None:
    return None if evidence is None else evidence.host.pswpout_after - evidence.host.pswpout_before


def _reading_passes(reading: ProbeReading, failed: Mapping[tuple[int, int], bool]) -> bool:
    sets = range(1, CAP_SESSION_COUNT + 1)
    items = range(1, PROBE_ITEM_COUNT + 1)
    per_set = {index: sum(failed.get((index, item), False) for item in items) for index in sets}
    per_item = {item: sum(failed.get((index, item), False) for index in sets) for item in items}
    if reading == "first_session":
        return per_set[1] <= J_PROBE_MAX
    if reading == "every_session":
        return all(count <= J_PROBE_MAX for count in per_set.values())
    if reading == "pooled":
        return sum(per_set.values()) <= J_PROBE_MAX * CAP_SESSION_COUNT
    if reading == "per_item":
        return sum(count > 0 for count in per_item.values()) <= J_PROBE_MAX
    return sum(count >= _MAJORITY for count in per_item.values()) <= J_PROBE_MAX


def probe_readings(run1: tuple[SessionRun, ...]) -> dict[ProbeReading, tuple[bool, bool]]:
    """AMB-66's five readings over run 1's 80 probes, each as (J probe part passes, C passes). Reported only: the
    gates read `first_session` (G4-11)."""
    j_failed = {(s.session_index, p.position or 0): p.call.outcome != "valid" for s in run1 for p in s.probes}
    c_failed = {(s.session_index, p.position or 0): _c_failed(p.call) for s in run1 for p in s.probes}
    return {
        reading: (_reading_passes(reading, j_failed), _reading_passes(reading, c_failed)) for reading in PROBE_READINGS
    }


def readings_disagree(readings: Mapping[ProbeReading, tuple[bool, bool]]) -> bool:
    """Whether some AMB-66 reading gives another (J, C) result than another one: the report flags it (G4-11)."""
    return len(set(readings.values())) > 1


def summarise_field(values: tuple[str, ...]) -> Mapping[str, Any]:
    """Clocks, temperature or power over one attempt's samples, reported only (S2 §3.1; G4-34; AMB-124). A value is
    readable when it is a decimal with an optional unit; anything else, `[N/A]` included, is counted as not
    readable."""
    numbers: list[Fraction] = []
    units: set[str] = set()
    for value in values:
        match = _NUMERIC.fullmatch(value.strip())
        if match is None:
            continue
        numbers.append(Fraction(match.group(1)))
        if match.group(2):
            units.add(match.group(2))
    summary: dict[str, Any] = {"n": len(values), "readable": len(numbers)}
    if numbers:
        ordered = sorted(numbers)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        summary.update(min=ordered[0], median=median, max=ordered[-1], units=tuple(sorted(units)))
    if len(numbers) < len(values):
        summary["not_readable"] = f"not readable ({len(values) - len(numbers)} of {len(values)})"
    return summary


# ---------------------------------------------------------------- reported-only figures


@dataclass(frozen=True, slots=True)
class Distribution:
    """Nearest-rank figures over call or attempt totals, in ns (AMB-119)."""

    n: int
    median: int
    p95: int
    max: int


def _distribution(values: Iterable[int]) -> Distribution | None:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return None
    return Distribution(n, ordered[(n + 1) // 2 - 1], ordered[(95 * n + 99) // 100 - 1], ordered[-1])


def _longest(calls: Iterable[StoredCall]) -> Mapping[str, Any] | None:
    """The call with the longest total, retry included, so a retried call beats a longer single first attempt."""
    longest: StoredCall | None = None
    for stored in calls:
        if longest is None or stored.call.total_wall_ns > longest.call.total_wall_ns:
            longest = stored
    if longest is None:
        return None
    return {
        "unit": _unit_name(longest.key),
        "item_id": longest.call.item_id,
        "role": longest.role,
        "session_index": longest.session_index,
        "position": longest.position,
        "total_ns": longest.call.total_wall_ns,
        "attempt_ns": tuple(attempt.wall_ns for attempt in longest.call.attempts),
    }


def _latency(timed: tuple[StoredCall, ...], sessions: tuple[SessionRun, ...]) -> Mapping[str, Any]:
    """Gate T's reported figures (G4-15, G4-40; AMB-119): per kind over runs 1 to 3, using call totals with retries,
    and per-attempt figures beside them."""
    by_kind = {kind: tuple(stored for stored in timed if stored.call.request_kind == kind) for kind in REQUEST_KINDS}
    return {
        "calls": {kind: _distribution(s.call.total_wall_ns for s in calls) for kind, calls in by_kind.items()},
        "attempts": {
            kind: _distribution(a.wall_ns for s in calls for a in s.call.attempts) for kind, calls in by_kind.items()
        },
        "singles_over_30s": sum(s.role == "single" and s.call.total_wall_ns > SINGLE_OVER_NS for s in timed),
        "longest": _longest(timed),
        "longest_by_kind": {kind: _longest(calls) for kind, calls in by_kind.items()},
        "session_runs": {
            f"run{session.run}.session{session.session_index}": {
                "total_ns": session.total_ns,
                "per_row_ns": Fraction(session.total_ns, CAP_SESSION_ROWS),
                "claims": session.claims,
                "cap_outcome": session.cap.call.outcome,
            }
            for session in sessions
        },
    }


def _cutoffs(groups: Mapping[str, tuple[StoredCall, ...]], run1_singles: tuple[StoredCall, ...]) -> dict[str, int]:
    """Cut-off attempts and calls whose final outcome is cut off, per phase and kind, with run 1's singles apart
    (S2 §3.8; G4-36; AMB-122)."""
    counts: dict[str, int] = {}
    for phase, calls in groups.items():
        for kind in REQUEST_KINDS:
            of_kind = [stored.call for stored in calls if stored.call.request_kind == kind]
            counts[f"{phase}.{kind}.attempts"] = sum(call.cut_off_attempts for call in of_kind)
            counts[f"{phase}.{kind}.calls"] = sum(call.outcome == "cut_off" for call in of_kind)
    counts["run1.singles.attempts"] = sum(stored.call.cut_off_attempts for stored in run1_singles)
    counts["run1.singles.calls"] = sum(stored.call.outcome == "cut_off" for stored in run1_singles)
    return counts


def _runtime(launch: LaunchRecord | None, groups: Mapping[str, tuple[StoredCall, ...]]) -> Mapping[str, Any]:
    """The runtime block (G4-36; AMB-121): parser and graph splits from the pre-run launch, fences and junk ids from
    first attempts."""
    splits = None if launch is None else launch.startup.graph_splits
    return {
        "template_parser": None if launch is None else launch.template_parser,
        "graph_splits": splits,
        "graph_splits_label": "operations on the CPU" if splits is not None and splits > 2 else None,
        "fences": {
            phase: sum((s.call.attempts[0].content or "").lstrip().startswith(_FENCE) for s in calls)
            for phase, calls in groups.items()
        },
        "junk_ids": {
            phase: sum(s.role == "probe" and bool(s.call.attempts[0].unknown_source_ids) for s in calls)
            for phase, calls in groups.items()
        },
    }


def _raw_validity(calls: tuple[StoredCall, ...]) -> Mapping[str, Any]:
    validity: dict[str, Any] = {}
    for kind in REQUEST_KINDS:
        firsts = [stored.call.attempts[0] for stored in calls if stored.call.request_kind == kind]
        validity[kind] = {
            "valid_first_attempts": sum(attempt.failure_kind is None for attempt in firsts),
            "n": len(firsts),
            "first_attempt_failures": {name: sum(a.failure_kind == name for a in firsts) for name in FAILURE_KINDS},
        }
    return validity


def _unconstrained(key: RunKey | None, calls: tuple[StoredCall, ...]) -> Mapping[str, Any] | None:
    """Gate C on the unconstrained pass and raw validity per kind, reported, not gated (S2 §3.8; G4-12)."""
    if key is None:
        return None
    failures = sum(_c_failed(stored.call) for stored in calls if stored.role == "probe")
    return {
        "unit": _unit_name(key),
        "label": _REPORTED,
        "probe_c_failures": failures,
        "probe_c_passes": failures <= C_MAX,
        "raw_validity": _raw_validity(calls),
    }


def _contended(
    view: RunView,
    candidate: str,
    key: RunKey | None,
    evidence: AttemptEvidence | None,
    operator: OperatorValues,
    deadline_sec: Fraction,
) -> Mapping[str, Any]:
    """The together session (G4-15, G4-22): its timing and figures, reported, not gated, or its absence."""
    voided = tuple(
        _unit_name(void.key) for void in view.voids if void.key.candidate == candidate and void.key.phase == "contended"
    )
    if key is None:
        return {"status": "absent", "unit": None, "voided": voided, "needs_repeat": True, "label": _REPORTED}
    sessions = session_runs(_unit_calls(view, key), None)
    session = sessions[0] if sessions else None
    record = None if evidence is None else evidence.host.contention
    partial = record is not None and record.ended_before_stop
    total = None if session is None else session.total_ns
    return {
        "status": "partial" if partial else "present",
        "unit": _unit_name(key),
        "voided": voided,
        "label": _REPORTED,
        "session_total_ns": total,
        "per_row_ns": None if total is None else Fraction(total, CAP_SESSION_ROWS),
        "claims": None if session is None else session.claims,
        "cap_valid": None if session is None else session.cap_valid,
        "within_model_budget": None if total is None else 2 * total <= deadline_sec * 10**9,
        "evidence": evidence is not None,
        "needs_repeat": evidence is None or evidence.peak_rss_anon_bytes is None,
        "combined_rise_mib": None if evidence is None else evidence.rise_mib,
        "combined_limit_mib": operator.idle_free_mib - HEADROOM_MIB,
        "ended_before_stop_ms": record.ended_before_stop_ms if partial and record is not None else None,
    }


def _attempt_memory(evidence: AttemptEvidence | None, operator: OperatorValues) -> Mapping[str, Any]:
    """One attempt's machine figures (S2 §6, §9; AMB-110, AMB-124). The baseline is reported beside the idle figure
    and nothing voids on it."""
    if evidence is None:
        return {"evidence": False}
    gpu = evidence.gpu
    return {
        "evidence": True,
        "baseline_used_mib": evidence.baseline_used_mib,
        "baseline_free_mib": evidence.baseline_free_mib,
        "idle_free_mib": operator.idle_free_mib,
        "peak_used_mib": evidence.peak_used_mib,
        "rise_mib": evidence.rise_mib,
        "peak_rss_anon_bytes": evidence.peak_rss_anon_bytes,
        "swap_delta": _swap_delta(evidence),
        "sample_rate": evidence.rate,
        "clocks_gr": summarise_field(tuple(sample.clocks_gr for sample in gpu)),
        "clocks_mem": summarise_field(tuple(sample.clocks_mem for sample in gpu)),
        "temperature": summarise_field(tuple(sample.temperature for sample in gpu)),
        "power": summarise_field(tuple(sample.power for sample in gpu)),
    }


def _memory(
    view: RunView,
    candidate: str,
    counted: tuple[RunKey, ...],
    evidence: Mapping[RunKey, AttemptEvidence | None],
    launch: LaunchRecord | None,
    gate: Gate,
    operator: OperatorValues,
) -> Mapping[str, Any]:
    """Per counted hooked attempt, then the voided ones, listed and never counted (AMB-123)."""
    voided = [
        void.key
        for void in view.voids
        if void.key.candidate == candidate and void.key.phase in ("timed_run", "contended")
    ]
    checks = {check.name: check for check in gate.checks}
    startup = None if launch is None else launch.startup
    return {
        "attempts": {_unit_name(key): _attempt_memory(evidence.get(key), operator) for key in counted},
        "voided": {_unit_name(key): _attempt_memory(evidence.get(key), operator) for key in voided},
        "context": view.manifest["conditions"]["context"],
        "kv_type": view.manifest["conditions"]["kv_type"],
        "layers_offloaded": None if startup is None else startup.layers_offloaded,
        "layers_total": None if startup is None else startup.layers_total,
        "server_rise_mib": checks["server_rise_mib"].value,
        "budget_mib": operator.budget_mib,
        "cuda_buffers_mib": checks["rise_vs_cuda_buffers_mib"].limit,
    }


# ---------------------------------------------------------------- evaluate


@dataclass(frozen=True, slots=True)
class CandidateResult:
    """Everything the decision and the report read about one candidate (S2 §1, §6, §9)."""

    candidate: str
    status: Literal["ran", "not_loaded"]
    information_only: tuple[str, ...]
    thinking_pre_run: ThinkingVerdict | None
    thinking_in_runs: Mapping[str, int]
    metrics: Mapping[int, RunMetrics]
    gates: Mapping[GateName, Gate]
    probe_readings: Mapping[ProbeReading, tuple[bool, bool]]
    sessions: tuple[SessionRun, ...]
    memory: Mapping[str, Any] | None
    latency: Mapping[str, Any] | None
    cutoffs: Mapping[str, int]
    unconstrained: Mapping[str, Any] | None
    contended: Mapping[str, Any] | None
    runtime: Mapping[str, Any]
    voided: tuple[RunKey, ...]

    @property
    def passes_every_gate(self) -> bool:
        """All seven gates pass; `not_measured` is never a pass (G4-25)."""
        return all(gate in self.gates and self.gates[gate].status == "pass" for gate in GATES)

    @property
    def eligible(self) -> bool:
        """It ran, gate L passed and nothing makes it information only (S2 §1, §2, §3.6; G4-25)."""
        return self.status == "ran" and self.gates["L"].status == "pass" and not self.information_only


def _wrap(result: GateResult) -> Gate:
    """H1's gate A or S as a `Gate`; a None figure fails (G4-23)."""
    checks = tuple(Check(c.name, c.value, c.limit, c.op, c.passed) for c in result.checks)
    return Gate(result.gate, "pass" if result.passed else "fail", checks, ())


def _not_loaded(candidate: str, pre_run: PreRunRecord, operator: OperatorValues) -> CandidateResult:
    """S2 §1, §2: a candidate the pinned build did not load fails L, and every other gate is not measured (G4-7)."""
    gates: dict[GateName, Gate] = {"L": gate_l(operator, candidate, pre_run)}
    gates.update({gate: _not_measured(gate, "did not load") for gate in GATES[1:]})
    return CandidateResult(
        candidate=candidate,
        status="not_loaded",
        information_only=("gate_L",),
        thinking_pre_run=pre_run.thinking,
        thinking_in_runs=MappingProxyType({}),
        metrics=MappingProxyType({}),
        gates=MappingProxyType(gates),
        probe_readings=MappingProxyType({}),
        sessions=(),
        memory=None,
        latency=None,
        cutoffs=MappingProxyType({}),
        unconstrained=None,
        contended=None,
        runtime=MappingProxyType({}),
        voided=(),
    )


def _thinking_count(calls: Iterable[StoredCall]) -> int:
    return sum(stored.call.thinking_seen for stored in calls)


def _ran(
    view: RunView,
    gold: GoldSet,
    evidence: Mapping[RunKey, AttemptEvidence | None],
    operator: OperatorValues,
    inputs: ReportInputs,
    pre_run: PreRunRecord,
) -> CandidateResult:
    candidate = pre_run.candidate
    keys = {run: _counted_key(view, candidate, run) for run in RUNS}
    unconstrained_key = counted_unit(view, candidate, "unconstrained")
    contended_key = counted_unit(view, candidate, "contended")
    for key in (*keys.values(), unconstrained_key, contended_key):
        if key is not None and not view.marked(key):
            raise ReportError(f"{_unit_name(key)} is not marked as scored: scores come after the marker (S2 §3.13)")
    calls = {run: view.counted_calls(candidate, run) for run in RUNS}
    singles = {run: tuple(s for s in calls[run] if s.role == "single") for run in RUNS}
    metrics = {run: score_run(gold, {s.call.item_id: s.call.extraction_result() for s in singles[run]}) for run in RUNS}
    sessions = tuple(session for run in RUNS for session in session_runs(calls[run], run))
    run1 = tuple(session for session in sessions if session.run == 1)
    # NEEDS-OWNER(AMB-66): gates J and C read run 1's first session-run; the other readings are reported
    gated_probes = tuple(
        s for s in calls[1] if s.role == "probe" and s.session_index == 1 and 1 <= (s.position or 0) <= PROBE_ITEM_COUNT
    )
    readings = probe_readings(run1)
    reading_notes = ("AMB-66 reading changes gate J or C",) if readings_disagree(readings) else ()
    deadline = operator.run_deadline_sec if inputs.deadline_override_sec is None else inputs.deadline_override_sec
    launch = next((r for r in view.launches if r.key == RunKey(candidate, "pre_run", None, 1)), None)
    contended_evidence = None if contended_key is None else evidence.get(contended_key)
    gate_m_result = gate_m(
        {key: evidence.get(key) for key in keys.values()},
        contended_evidence,
        contended_key is not None,
        launch,
        operator,
        inputs.shared_memory.get(CANDIDATE_SLUGS[candidate], "not_observed"),
        inputs.shared_memory_evidence_sha256,
    )
    j = gate_j(singles[1], gated_probes, len(gold.conversation_ids()))
    c = gate_c(gated_probes)
    gates: dict[GateName, Gate] = {
        "L": gate_l(operator, candidate, pre_run),
        "M": gate_m_result,
        "J": Gate(j.gate, j.status, j.checks, j.notes + reading_notes),
        "C": Gate(c.gate, c.status, c.checks, c.notes + reading_notes),
        "T": gate_t(sessions, deadline),
        "A": _wrap(gate_a(metrics[1])),
        "S": _wrap(gate_s(metrics[1])),
    }
    information_only = ["gate_L"] if gates["L"].status != "pass" else []
    # NEEDS-OWNER(AMB-116): the pre-run verdict alone decides eligibility; thinking in runs is counted and reported
    if pre_run.thinking == "seen":
        information_only.append("thinking")
    unconstrained_calls = () if unconstrained_key is None else _unit_calls(view, unconstrained_key)
    contended_calls = () if contended_key is None else _unit_calls(view, contended_key)
    thinking = {f"run{run}.constrained_timed": _thinking_count(calls[run]) for run in RUNS}
    thinking.update(contended=_thinking_count(contended_calls), unconstrained=_thinking_count(unconstrained_calls))
    timed = tuple(s for run in RUNS for s in calls[run])
    groups = {"timed_run": timed, "unconstrained": unconstrained_calls, "contended": contended_calls}
    counted_hooked = (*keys.values(), *(() if contended_key is None else (contended_key,)))
    return CandidateResult(
        candidate=candidate,
        status="ran",
        information_only=tuple(information_only),
        thinking_pre_run=pre_run.thinking,
        thinking_in_runs=MappingProxyType(thinking),
        metrics=MappingProxyType(metrics),
        gates=MappingProxyType(gates),
        probe_readings=MappingProxyType(readings),
        sessions=sessions,
        memory=_memory(view, candidate, counted_hooked, evidence, launch, gate_m_result, operator),
        latency=_latency(timed, sessions),
        cutoffs=MappingProxyType(_cutoffs(groups, singles[1])),
        unconstrained=_unconstrained(unconstrained_key, unconstrained_calls),
        contended=_contended(view, candidate, contended_key, contended_evidence, operator, deadline),
        runtime=_runtime(launch, groups),
        voided=tuple(void.key for void in view.voids if void.key.candidate == candidate),
    )


def evaluate(
    view: RunView,
    gold: GoldSet,
    evidence: Mapping[RunKey, AttemptEvidence | None],
    operator: OperatorValues,
    inputs: ReportInputs,
) -> dict[str, CandidateResult]:
    """Every candidate with a pre-run record, in S2 §1 order. It reads only marked units: an unmarked one is a
    `ReportError` (G4-3)."""
    results: dict[str, CandidateResult] = {}
    for pre_run in sorted(view.pre_run, key=lambda record: CANDIDATE_IDS.index(record.candidate)):
        if pre_run.status == "blocked":
            raise ReportError(f"{pre_run.candidate}'s pre-run is blocked: the schedule did not complete")
        if pre_run.status == "not_loaded":
            results[pre_run.candidate] = _not_loaded(pre_run.candidate, pre_run, operator)
        else:
            results[pre_run.candidate] = _ran(view, gold, evidence, operator, inputs, pre_run)
    return results
