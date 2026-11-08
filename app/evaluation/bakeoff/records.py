"""The stored records of a bake-off run (bake-off harness H3).

A schedule's results live in one new directory, as append-only ASCII JSONL. This module writes and reads them. It judges
nothing: H4 scores from these records.

Implements, from the signed D6 criteria note S2:

- S2 §3.12, data: "Outputs are written outside OneDrive, or with sync paused." The store refuses any path under
  OneDrive after resolving it (AMB-81) and offers no sync-paused override. The run directory is new and created
  exclusively, so results are never overwritten, and each record is flushed and synced, so a crash loses at most the
  line in progress. The reader reports a truncated last line instead of hiding it.
- S2 §3.13, runs: "A run is void only for an infrastructure fault logged before its scores are computed." A void names
  a logged fault of that run and is refused once the run's scored marker exists. H3 provides the marker and H4 sets it
  before it computes any score of the unit. "Voided runs are listed in the report", so a voided attempt's records stay,
  and `RunView.counted_calls` alone says what counts.
- S2 §9, report: the run manifest holds what "Recorded for every number" and Annex A ask for that the harness can know,
  plus the operator's environment fields. "Logs hold no prompts or transcripts beyond the synthetic gold text": the
  fault and void logs hold none at all (AMB-71), and `calls.jsonl` keeps model outputs and request hashes, not requests.
- S2 §10, re-opening: "Gate T is recomputed from the stored measurements, with no new runs", so a stored call reloads
  into the same typed `CallRecord`, exactly, or the record is not written.

AMB-52 takes option (a): records are ASCII-escaped JSON, so a lone surrogate that an escape put into a valid output is
stored as its escape and reads back unchanged. `canonical_json` stays as it is for hashes and wire bytes, and validity
and the gates do not change.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal, TypeGuard, TypeVar

from .client import (
    REQUEST_KINDS,
    AttemptRecord,
    CallRecord,
    ContextCheck,
    Modalities,
    ThinkingCheck,
    Timings,
    Usage,
    _is_count,
)
from .extraction import ExtractedClaim, Extraction, ExtractionInvalid, NonCheckableEntry, _load_json
from .launch import _LAUNCH_MODES, CANDIDATE_IDS, CANDIDATE_SLUGS, LaunchMode, StartupLog
from .probe import ProbeOutput
from .schemas import _check_keys

SCHEMA_VERSION = 1
RUNS = (1, 2, 3)
MAX_FAULT_DETAIL = 200
Phase = Literal["pre_run", "timed_run", "unconstrained", "contended"]
PHASES = ("pre_run", "timed_run", "unconstrained", "contended")
CallRole = Literal["warmup", "single", "cap_session", "probe"]
CALL_ROLES = ("warmup", "single", "cap_session", "probe")
PreRunStatus = Literal["ready", "not_loaded", "blocked"]
ThinkingVerdict = Literal["off", "seen", "no_evidence"]
FaultKind = Literal[
    "client_fault",
    "server_exit",
    "cached_tokens",
    "launch_failed",
    "ready_timeout",
    "launch_check",
    "hook_failed",
    "server_not_stopped",
    "operator",
]
FAULT_KINDS = (
    "client_fault",
    "server_exit",
    "cached_tokens",
    "launch_failed",
    "ready_timeout",
    "launch_check",
    "hook_failed",
    "server_not_stopped",
    "operator",
)
OPERATOR_FIELDS = (
    "machine",
    "wsl_kernel",
    "cuda_version",
    "wsl_networking_mode",
    "wsl_localhost_forwarding",
    "wslconfig_memory",
    "wslconfig_swap",
    "gpu_driver",
    "windows_power_mode",
    "gpu_profile",
    "sysmem_fallback",
    "namespace_outbound_check",
    "namespace_eth0_check",
    "namespace_lan_check",
    "llama_cpp_commit",
    "idle_free_vram_mib",
    "audio_pipeline_vram_mib",
    "run_deadline_sec",
    "rerun_allowance_sec",
    "bootstrap_seed",
)

_MANIFEST = "manifest.json"
_PRE_RUN = "pre_run.jsonl"
_LAUNCHES = "launches.jsonl"
_CALLS = "calls.jsonl"
_RUNS = "runs.jsonl"
_FAULTS = "faults.jsonl"
_VOIDS = "voids.jsonl"
_RECORD_FILES = (_PRE_RUN, _LAUNCHES, _CALLS, _RUNS, _FAULTS, _VOIDS)
_LOGS = "logs"
_SCORED = "scored"
_RUN_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")  # fullmatch only
_PATH_PARTS = re.compile(r"[/\\]")
_ONEDRIVE_PREFIXES = ("onedrive", "onedri~")  # the second is the folder's 8.3 short name
_PRE_RUN_STATUSES = ("ready", "not_loaded", "blocked")
_THINKING_VERDICTS = ("off", "seen", "no_evidence")
_RUN_STATUSES = ("complete", "void")
_SAMPLING_KEYS = ("temperature", "seed", "top_k", "repeat_penalty")
_CONDITION_KEYS = ("binary_path", "context", "kv_type", "port", "ready_deadline_sec")
_CANDIDATE_KEYS = ("candidate_id", "gguf_path", "gguf_sha256", "source_revision", "quantization_commands", "argv")
_HASH_KEYS = (
    "prompts_sha256",
    "repair_sha256",
    "probe_sha256",
    "cap_sessions_sha256",
    "cap_requests_sha256",
    "warmup_sha256",
    "requests",
    "requests_unconstrained",
)

# The valid tags of a stored call: phase, role, request kind, constrained, last session index, last position. A last
# place of 0 means that tag is not set. S2 §6: "There are 4 frozen sessions" and "20 reasoning-probe calls".
_SINGLE = "single_conversation_extraction"
_CAP = "cap_session_extraction"
_PROBE = "reasoning_probe"
_ANY = float("inf")
_TAGS = (
    ("pre_run", "warmup", _SINGLE, True, 0, 0),
    ("timed_run", "warmup", _SINGLE, True, 0, 0),
    ("timed_run", "single", _SINGLE, True, 0, _ANY),
    ("timed_run", "cap_session", _CAP, True, 4, 0),
    ("timed_run", "probe", _PROBE, True, 4, 20),
    ("unconstrained", "warmup", _SINGLE, False, 0, 0),
    ("unconstrained", "single", _SINGLE, False, 0, _ANY),
    ("unconstrained", "cap_session", _CAP, False, 4, 0),
    ("unconstrained", "probe", _PROBE, False, 0, 20),
    ("contended", "warmup", _SINGLE, True, 0, 0),
    ("contended", "cap_session", _CAP, True, 1, 0),
    ("contended", "probe", _PROBE, True, 1, 20),
)

_T = TypeVar("_T")


class StoreError(RuntimeError):
    """A record cannot be stored or read, or a store rule refuses the action."""


def record_json(value: object) -> bytes:
    """The stored form of `value`: ASCII, sorted keys, no spaces, so one record is one line. Unlike `canonical_json` it
    holds a lone surrogate, as its JSON escape (AMB-52). A non-str key or a non-JSON type raises `TypeError`, NaN and
    infinities raise `ValueError`."""
    _check_keys(value)
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return text.encode("ascii")


def record_loads(raw: bytes) -> Any:
    """The value of one stored line, under H1's JSON rules, so repeated keys and NaN are refused. Lone surrogates are
    kept. A non-ASCII byte is corruption, since `record_json` never writes one."""
    try:
        return _load_json(raw.decode("ascii"))
    except (UnicodeDecodeError, ExtractionInvalid) as exc:
        raise StoreError(f"not a stored record: {exc}") from None


def refuse_onedrive(path: str, *, realpath: Callable[[str], str] = os.path.realpath) -> str:
    """The resolved path, if no part of it is a OneDrive folder (S2 §3.12; AMB-81). Parts are split on both separators,
    since a Windows path can reach this check, and compared after casefolding. Nothing allows a refused path."""
    resolved = realpath(path)
    if any(part.casefold().startswith(_ONEDRIVE_PREFIXES) for part in _PATH_PARTS.split(resolved)):
        raise StoreError(f"{resolved} is under OneDrive: outputs are written outside it (S2 §3.12)")
    return resolved


def _is_int(value: object) -> TypeGuard[int]:
    """An int. A `bool` is an `int` in Python, so it is refused by name."""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True, slots=True)
class RunKey:
    """Which launch of which unit a record belongs to. `run` is set on a timed run only. `run_attempt` counts the
    launches of one unit: a voided unit repeats with the next number (AMB-69)."""

    candidate: str
    phase: Phase
    run: int | None
    run_attempt: int

    def __post_init__(self) -> None:
        if self.candidate not in CANDIDATE_IDS:
            raise ValueError(f"RunKey.candidate must be one of {CANDIDATE_IDS}, not {self.candidate!r}")
        if self.phase not in PHASES:
            raise ValueError(f"RunKey.phase must be one of {PHASES}, not {self.phase!r}")
        if not _is_int(self.run_attempt) or self.run_attempt < 1:
            raise ValueError("RunKey.run_attempt must be an int >= 1")
        if self.phase == "timed_run":
            if not _is_int(self.run) or self.run not in RUNS:
                raise ValueError(f"RunKey.run of a timed run must be one of {RUNS}")
        elif self.run is not None:
            raise ValueError("RunKey.run is set on a timed run only")
        if self.phase == "pre_run" and self.run_attempt != 1:
            raise ValueError("RunKey.run_attempt of a pre-run must be 1")


def _unit_name(key: RunKey) -> str:
    """`<slug>.<unit>.a<n>`, the stem of a launch's log file and of a unit's scored marker."""
    unit = key.phase if key.run is None else f"run{key.run}"
    return f"{CANDIDATE_SLUGS[key.candidate]}.{unit}.a{key.run_attempt}"


def log_name(key: RunKey) -> str:
    """The server log of a launch, relative to the run directory."""
    return f"{_LOGS}/{_unit_name(key)}.log"


def _check_place(name: str, value: object, last: float) -> None:
    """A 1-based place up to `last`, or `None` when `last` is 0."""
    valid = value is None if last == 0 else _is_int(value) and 1 <= value <= last
    if not valid:
        raise ValueError(f"StoredCall.{name} is {value!r}, which this role and phase do not allow")


@dataclass(frozen=True, slots=True)
class StoredCall:
    """One stored call: its place in the schedule and the full `CallRecord`. `sequence` is assigned by the store, 1, 2,
    3, ... `session_index` is the 1-based place in the frozen list, and `position` the 1-based place in the singles or
    the frozen probe order. A probe's `call.item_id` is the probe item id, so two sessions' records of one item differ
    only by `session_id`, `session_index` and `sequence`. Warm-ups carry role `warmup` and are never scored."""

    sequence: int
    stamp: str
    key: RunKey
    role: CallRole
    session_id: str | None
    # NEEDS-OWNER(AMB-66): gates J and C read run 1's first session-run; the tags keep every reading
    session_index: int | None
    position: int | None
    call: CallRecord

    def __post_init__(self) -> None:
        if not _is_int(self.sequence) or self.sequence < 1:
            raise ValueError("StoredCall.sequence must be an int >= 1")
        if not isinstance(self.key, RunKey) or not isinstance(self.call, CallRecord):
            raise ValueError("StoredCall.key must be a RunKey and StoredCall.call a CallRecord")
        tags = next((row for row in _TAGS if row[:2] == (self.key.phase, self.role)), None)
        if tags is None:
            raise ValueError(f"StoredCall: phase {self.key.phase} has no role {self.role!r}")
        _, _, kind, constrained, last_session, last_position = tags
        expected = (kind, constrained)
        actual = (self.call.request_kind, self.call.constrained)
        if actual != expected:
            raise ValueError(f"StoredCall: role {self.role} on phase {self.key.phase} needs {expected}, not {actual}")
        _check_place("session_index", self.session_index, last_session)
        _check_place("position", self.position, last_position)
        if last_session == 0:
            valid_session = self.session_id is None
        else:
            valid_session = isinstance(self.session_id, str) and self.session_id != ""
        if not valid_session:
            raise ValueError(f"StoredCall.session_id is {self.session_id!r}, which this role and phase do not allow")
        if self.role == "cap_session" and self.session_id != self.call.item_id:
            raise ValueError("StoredCall.session_id of a cap session must equal its call's item_id")


@dataclass(frozen=True, slots=True)
class LaunchRecord:
    """One launch that passed its checks, written after it stopped (S2 §3.3, Annex A). `env_names` holds sorted names
    only and `log_name` is relative to the run directory. Template text, `/props` defaults and environment values are
    never stored, so `chat_template_sha256` stands for the template."""

    stamp: str
    key: RunKey
    mode: LaunchMode
    argv: tuple[str, ...]
    env_names: tuple[str, ...]
    log_name: str
    pid: int
    ready_sec: float
    n_ctx: int
    total_slots: int
    build_info: str
    model_path: str
    chat_template_sha256: str
    template_parser: str
    modalities: Modalities
    startup: StartupLog
    exit_code: int
    stopped_how: str

    def __post_init__(self) -> None:
        if self.mode not in _LAUNCH_MODES:
            raise ValueError(f"LaunchRecord.mode must be one of {_LAUNCH_MODES}, not {self.mode!r}")
        if self.log_name != log_name(self.key):
            raise ValueError(f"LaunchRecord.log_name must be {log_name(self.key)}, not {self.log_name!r}")
        if list(self.env_names) != sorted(self.env_names):
            raise ValueError("LaunchRecord.env_names must be sorted")


@dataclass(frozen=True, slots=True)
class PreRunRecord:
    """One candidate's pre-run, whatever its outcome (S2 §3.3, §3.5, §3.6, §11 item 5). `context_checks` holds every
    check made, and `blocked` the names of the checks that stop the runs, never their text."""

    stamp: str
    candidate: str
    status: PreRunStatus
    gguf_sha256: str | None
    exit_code: int | None
    thinking: ThinkingVerdict | None
    context_checks: tuple[ContextCheck, ...]
    blocked: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.candidate not in CANDIDATE_IDS:
            raise ValueError(f"PreRunRecord.candidate must be one of {CANDIDATE_IDS}, not {self.candidate!r}")
        if self.status not in _PRE_RUN_STATUSES:
            raise ValueError(f"PreRunRecord.status must be one of {_PRE_RUN_STATUSES}, not {self.status!r}")
        if self.thinking is not None and self.thinking not in _THINKING_VERDICTS:
            raise ValueError(f"PreRunRecord.thinking must be None or one of {_THINKING_VERDICTS}")
        if (self.status == "blocked") != bool(self.blocked):
            raise ValueError("PreRunRecord.blocked names checks exactly when the status is blocked")


@dataclass(frozen=True, slots=True)
class RunRecord:
    """How one attempt of a unit ended: `complete` when every planned call is stored and no fault was logged for it,
    `void` otherwise. A missing record means the attempt never finished. `calls` is the number stored for it."""

    stamp: str
    key: RunKey
    status: Literal["complete", "void"]
    calls: int

    def __post_init__(self) -> None:
        if self.status not in _RUN_STATUSES:
            raise ValueError(f"RunRecord.status must be one of {_RUN_STATUSES}, not {self.status!r}")
        if not _is_count(self.calls):
            raise ValueError("RunRecord.calls must be an int >= 0")


@dataclass(frozen=True, slots=True)
class FaultRecord:
    """One logged infrastructure fault; alone it voids nothing. `detail` holds names and numbers, never a request, a
    message, model output, server error text or a log line (S2 §9; AMB-71), so it is short plain ASCII."""

    fault_id: int
    stamp: str
    key: RunKey
    kind: FaultKind
    item_id: str | None
    detail: str

    def __post_init__(self) -> None:
        if not _is_int(self.fault_id) or self.fault_id < 1:
            raise StoreError("FaultRecord.fault_id must be an int >= 1")
        if self.kind not in FAULT_KINDS:
            raise StoreError(f"FaultRecord.kind must be one of {FAULT_KINDS}, not {self.kind!r}")
        if self.item_id is not None and not isinstance(self.item_id, str):
            raise StoreError("FaultRecord.item_id must be a string or None")
        if not isinstance(self.detail, str) or not self.detail.isascii() or len(self.detail) > MAX_FAULT_DETAIL:
            raise StoreError(f"FaultRecord.detail must be ASCII and at most {MAX_FAULT_DETAIL} characters")


@dataclass(frozen=True, slots=True)
class VoidRecord:
    """A voided attempt, naming the fault that voids it (S2 §3.13). It is listed, never deleted."""

    stamp: str
    key: RunKey
    fault_id: int

    def __post_init__(self) -> None:
        if not _is_int(self.fault_id) or self.fault_id < 1:
            raise ValueError("VoidRecord.fault_id must be an int >= 1")


@dataclass(frozen=True, slots=True)
class _ScoredMarker:
    """The one line of a scored marker (S2 §3.13)."""

    stamp: str
    key: RunKey


def _exact(data: object, names: Collection[str], where: str) -> dict[str, Any]:
    """A copy of `data`, which must hold exactly `names`."""
    if not isinstance(data, Mapping):
        raise StoreError(f"{where} must be an object")
    missing = sorted(set(names) - set(data))
    unexpected = sorted(set(data) - set(names))
    if missing or unexpected:
        raise StoreError(f"{where}: missing keys {missing}, unexpected keys {unexpected}")
    return dict(data)


@dataclass(frozen=True, slots=True)
class RunManifest:
    """What S2 §9 and Annex A ask for that the harness can know, and the operator's environment fields. Every mapping
    is a plain dict and every key set is exact, so a run with a missing value never gets a directory. The manifest
    stores each candidate's expected GGUF hash; the pre-run confirms it."""

    gold_version: str
    gold_manifest: Mapping[str, str]
    hashes: Mapping[str, Any]
    extraction_schema_sha256: str
    probe_schema_sha256: str
    sampling: Mapping[str, Any]
    max_tokens: Mapping[str, int]
    timeout_sec: Mapping[str, float]
    thinking_mode: str
    conditions: Mapping[str, Any]
    binary_sha256: str
    pinned_build_info: str
    interfaces: tuple[str, ...]
    candidates: tuple[Mapping[str, Any], ...]
    operator: Mapping[str, str]

    def __post_init__(self) -> None:
        operator = _exact(self.operator, OPERATOR_FIELDS, "RunManifest.operator")
        for name, value in operator.items():
            if not isinstance(value, str) or not value.strip():
                raise StoreError(f"RunManifest.operator.{name} must contain a non-whitespace character")
        _exact(self.hashes, _HASH_KEYS, "RunManifest.hashes")
        _exact(self.sampling, _SAMPLING_KEYS, "RunManifest.sampling")
        _exact(self.max_tokens, REQUEST_KINDS, "RunManifest.max_tokens")
        _exact(self.timeout_sec, REQUEST_KINDS, "RunManifest.timeout_sec")
        _exact(self.conditions, _CONDITION_KEYS, "RunManifest.conditions")
        for candidate in self.candidates:
            _exact(candidate, _CANDIDATE_KEYS, "RunManifest.candidates")


@dataclass(frozen=True, slots=True)
class TruncatedLine:
    """Bytes after a log's last line feed: the line a crash left unfinished, never parsed."""

    file_name: str
    line_number: int
    byte_count: int


def call_to_dict(call: CallRecord) -> dict[str, Any]:
    return dataclasses.asdict(call)


def _names(record: Any) -> tuple[str, ...]:
    return tuple(field.name for field in dataclasses.fields(record))


def _fields(data: object, record: Any) -> dict[str, Any]:
    return _exact(data, _names(record), record.__name__)


def _build(record: Any, fields: dict[str, Any]) -> Any:
    """`record` from `fields`. A value its checks refuse is a tampered or corrupt record."""
    try:
        return record(**fields)
    except (TypeError, ValueError) as exc:
        raise StoreError(f"{record.__name__}: {exc}") from None


def _plain(record: Any, data: object) -> Any:
    return _build(record, _fields(data, record))


def _items(value: object, decode: Callable[[Any], _T]) -> tuple[_T, ...]:
    if not isinstance(value, list | tuple):
        raise StoreError("expected an array")
    return tuple(decode(item) for item in value)


def _texts(value: object) -> tuple[str, ...]:
    """An array as a tuple. The record's own checks refuse anything but strings."""
    return _items(value, lambda item: item)


def _context_check(data: object) -> ContextCheck:
    return _plain(ContextCheck, data)


def _attempt(data: object) -> AttemptRecord:
    fields = _fields(data, AttemptRecord)
    for name, record in (("usage", Usage), ("timings", Timings), ("thinking", ThinkingCheck)):
        if fields[name] is not None:
            fields[name] = _plain(record, fields[name])
    if fields["cited_source_ids"] is not None:
        fields["cited_source_ids"] = _texts(fields["cited_source_ids"])
    fields["unknown_source_ids"] = _texts(fields["unknown_source_ids"])
    return _build(AttemptRecord, fields)


def _with_segment_ids(record: Any, data: object) -> Any:
    fields = _fields(data, record)
    fields["segment_ids"] = _texts(fields["segment_ids"])
    return _build(record, fields)


def _extraction(data: object) -> Extraction:
    fields = _fields(data, Extraction)
    fields["claims"] = _items(fields["claims"], lambda item: _with_segment_ids(ExtractedClaim, item))
    fields["non_checkable"] = _items(fields["non_checkable"], lambda item: _with_segment_ids(NonCheckableEntry, item))
    return _build(Extraction, fields)


def _probe_output(data: object) -> ProbeOutput:
    fields = _fields(data, ProbeOutput)
    fields["cited_source_ids"] = _texts(fields["cited_source_ids"])
    return _build(ProbeOutput, fields)


def call_from_dict(data: Mapping[str, Any]) -> CallRecord:
    """The typed record back from `call_to_dict`'s form, with every list a tuple again. The key sets are exact at every
    level and H2's checks run on every value, so a tampered record raises `StoreError`."""
    fields = _fields(data, CallRecord)
    fields["attempts"] = _items(fields["attempts"], _attempt)
    fields["context_checks"] = _items(fields["context_checks"], _context_check)
    if fields["output"] is not None:
        parse = _probe_output if fields["request_kind"] == "reasoning_probe" else _extraction
        fields["output"] = parse(fields["output"])
    return _build(CallRecord, fields)


def _line(record: str, value: Any, **extra: str) -> bytes:
    """One stored line: `schema`, `record`, then the fields of `value`, in ASCII, ending in one LF."""
    return record_json({"schema": SCHEMA_VERSION, "record": record, **extra, **dataclasses.asdict(value)}) + b"\n"


def _unwrapped(data: object, record: str, names: tuple[str, ...]) -> dict[str, Any]:
    """A line's fields, with `schema` and `record` checked and dropped, `stamp` checked and `key` typed."""
    fields = _exact(data, ("schema", "record", *names), record)
    schema = fields.pop("schema")
    if type(schema) is not int or schema != SCHEMA_VERSION or fields.pop("record") != record:
        raise StoreError(f"not a schema {SCHEMA_VERSION} {record} record")
    if not isinstance(fields["stamp"], str):
        raise StoreError(f"{record}: stamp must be a string")
    if "key" in fields:
        fields["key"] = _plain(RunKey, fields["key"])
    return fields


def _stored_call(data: object) -> StoredCall:
    fields = _unwrapped(data, "call", _names(StoredCall))
    fields["call"] = call_from_dict(fields["call"])
    return _build(StoredCall, fields)


def _launch(data: object) -> LaunchRecord:
    fields = _unwrapped(data, "launch", _names(LaunchRecord))
    fields["argv"] = _texts(fields["argv"])
    fields["env_names"] = _texts(fields["env_names"])
    fields["modalities"] = _plain(Modalities, fields["modalities"])
    fields["startup"] = _plain(StartupLog, fields["startup"])
    return _build(LaunchRecord, fields)


def _pre_run(data: object) -> PreRunRecord:
    fields = _unwrapped(data, "pre_run", _names(PreRunRecord))
    fields["context_checks"] = _items(fields["context_checks"], _context_check)
    fields["blocked"] = _texts(fields["blocked"])
    return _build(PreRunRecord, fields)


def _run(data: object) -> RunRecord:
    return _build(RunRecord, _unwrapped(data, "run", _names(RunRecord)))


def _fault(data: object) -> FaultRecord:
    return _build(FaultRecord, _unwrapped(data, "fault", _names(FaultRecord)))


def _void(data: object) -> VoidRecord:
    return _build(VoidRecord, _unwrapped(data, "void", _names(VoidRecord)))


def _scored_key(data: object) -> RunKey:
    return _build(_ScoredMarker, _unwrapped(data, "scored", _names(_ScoredMarker))).key


def _manifest(data: Any) -> dict[str, Any]:
    """The whole stored object, once its shape is checked by rebuilding the `RunManifest`."""
    fields = _unwrapped(data, "manifest", ("stamp", *_names(RunManifest)))
    del fields["stamp"]
    fields["interfaces"] = _texts(fields["interfaces"])
    fields["candidates"] = _items(fields["candidates"], lambda item: item)
    _build(RunManifest, fields)
    return dict(data)


def read_log(path: str, decode: Callable[[Any], _T]) -> tuple[tuple[_T, ...], TruncatedLine | None]:
    """Every complete line of a stored file, decoded, and the truncated last line if there is one.

    Only LF-terminated lines count: bytes after the last LF are reported, never parsed, even if they are valid JSON. A
    complete line that is empty or does not decode raises `StoreError` naming the file and line, and so does a
    `calls.jsonl` whose sequence is not 1, 2, 3, ...
    """
    name = Path(path).name
    try:
        lines = Path(path).read_bytes().split(b"\n")
    except FileNotFoundError:
        raise StoreError(f"{name} is missing from the run directory") from None
    tail = lines.pop()
    records: list[_T] = []
    for number, line in enumerate(lines, start=1):
        try:
            if not line:
                raise StoreError("empty line")
            value = record_loads(line)
            if name == _CALLS and (not isinstance(value, dict) or value.get("sequence") != number):
                raise StoreError(f"the sequence number must be {number}")
            records.append(decode(value))
        except (StoreError, TypeError, ValueError, KeyError) as exc:
            raise StoreError(f"{name} line {number}: {exc}") from None
    return tuple(records), (TruncatedLine(name, len(lines) + 1, len(tail)) if tail else None)


class RunStore:
    """One run directory, made by `create` or reopened by `attach`. Every line is written in one go, flushed and synced,
    by binary appends with no handle kept, so there is nothing to close. Records are never rewritten or deleted."""

    def __init__(self, path: str, *, now: Callable[[], datetime], fsync: Callable[[int], None], writer: bool) -> None:
        self._path = path
        self._now = now
        self._fsync = fsync
        self._writer = writer
        self._sequence = 0

    @classmethod
    def create(
        cls,
        parent: str,
        name: str,
        manifest: RunManifest,
        *,
        now: Callable[[], datetime],
        fsync: Callable[[int], None] = os.fsync,
        realpath: Callable[[str], str] = os.path.realpath,
    ) -> RunStore:
        """A new run directory `<parent>/<name>`, exclusively: one that exists, empty or not, raises `StoreError`, and
        `parent` must exist. There is no default parent, because the repo itself lives under OneDrive."""
        if not isinstance(name, str) or _RUN_NAME.fullmatch(name) is None:
            raise StoreError(f"run name {name!r} must match {_RUN_NAME.pattern}")
        store = cls(refuse_onedrive(f"{parent}/{name}", realpath=realpath), now=now, fsync=fsync, writer=True)
        line = _line("manifest", manifest, stamp=store.stamp())  # first, so a bad value leaves no directory
        root = Path(store.path)
        try:
            root.mkdir(exist_ok=False)
            (root / _LOGS).mkdir()
            (root / _SCORED).mkdir()
            store._write_new(root / _MANIFEST, line)
            for file_name in _RECORD_FILES:
                with (root / file_name).open("xb"):
                    pass
        except FileExistsError:
            raise StoreError(f"{store.path} exists: a run directory is always new (S2 §3.12)") from None
        return store

    @classmethod
    def attach(
        cls,
        path: str,
        *,
        now: Callable[[], datetime],
        fsync: Callable[[int], None] = os.fsync,
        realpath: Callable[[str], str] = os.path.realpath,
    ) -> RunStore:
        """An existing run directory, for `log_fault`, `void_run` and `mark_scored` only. A truncated tail in the fault
        or void log is refused, because a new line would merge with the fragment."""
        store = cls(refuse_onedrive(path, realpath=realpath), now=now, fsync=fsync, writer=False)
        if not Path(store.path, _MANIFEST).is_file():
            raise StoreError(f"{store.path} is not a run directory: it holds no {_MANIFEST}")
        store._complete(_FAULTS, _fault)
        store._complete(_VOIDS, _void)
        return store

    @property
    def path(self) -> str:
        return self._path

    def stamp(self) -> str:
        """The injected clock's time. It is stored as given, so a clock that goes backwards is no fault (AMB-85): order
        comes from the sequence numbers, and no measurement reads a stamp."""
        moment = self._now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise StoreError("the clock must give an aware datetime")
        return moment.isoformat()

    def server_log_path(self, key: RunKey) -> str:
        return str(Path(self._path, log_name(key)))

    def append_call(
        self,
        key: RunKey,
        role: CallRole,
        *,
        session_id: str | None,
        session_index: int | None,
        position: int | None,
        call: CallRecord,
    ) -> StoredCall:
        """Store one call under the next sequence number. The line is read back first: a record that would not reload
        into the same typed value is refused, and nothing is written."""
        self._require_writer()
        stored = StoredCall(self._sequence + 1, self.stamp(), key, role, session_id, session_index, position, call)
        line = _line("call", stored)
        if _stored_call(record_loads(line)) != stored:
            raise StoreError(f"call {call.item_id} would not read back unchanged, so it is not stored")
        self._append(_CALLS, line)
        self._sequence = stored.sequence
        return stored

    def append_launch(self, record: LaunchRecord) -> None:
        self._require_writer()
        self._append(_LAUNCHES, _line("launch", record))

    def append_pre_run(self, record: PreRunRecord) -> None:
        self._require_writer()
        self._append(_PRE_RUN, _line("pre_run", record))

    def append_run(self, record: RunRecord) -> None:
        self._require_writer()
        self._append(_RUNS, _line("run", record))

    def log_fault(self, key: RunKey, kind: FaultKind, *, item_id: str | None, detail: str) -> FaultRecord:
        """Log a fault under the next fault id. `detail` comes from a fixed template (S2 §9; AMB-71)."""
        faults = self._complete(_FAULTS, _fault)
        record = FaultRecord(len(faults) + 1, self.stamp(), key, kind, item_id, detail)
        self._append(_FAULTS, _line("fault", record))
        return record

    def void_run(self, key: RunKey, fault_id: int) -> VoidRecord:
        """Void one attempt (S2 §3.13). It needs a fault logged for that attempt, before its scored marker exists."""
        if key.phase == "pre_run":
            raise StoreError("a pre-run has no run to void: its fault blocks instead")
        if not any(fault.fault_id == fault_id and fault.key == key for fault in self._complete(_FAULTS, _fault)):
            raise StoreError(f"fault {fault_id} is not logged for this attempt")
        if self._marker(key).exists():
            raise StoreError("this attempt's scores are computed, so it can no longer be voided (S2 §3.13)")
        if any(void.key == key for void in self._complete(_VOIDS, _void)):
            raise StoreError("this attempt is already void")
        record = VoidRecord(self.stamp(), key, fault_id)
        self._append(_VOIDS, _line("void", record))
        return record

    def mark_scored(self, key: RunKey) -> None:
        """Set a unit's scored marker. H4 calls this before it computes any score of the unit; the runner never does."""
        if any(void.key == key for void in self._complete(_VOIDS, _void)):
            raise StoreError("a void attempt cannot be scored")
        runs, _tail = read_log(str(Path(self._path, _RUNS)), _run)
        if not any(run.key == key and run.status == "complete" for run in runs):
            raise StoreError("only a complete attempt can be scored")
        try:
            self._write_new(self._marker(key), _line("scored", _ScoredMarker(self.stamp(), key)))
        except FileExistsError:
            raise StoreError("this attempt is already marked as scored") from None

    def _require_writer(self) -> None:
        if not self._writer:
            raise StoreError("an attached store only logs faults, voids runs and sets scored markers")

    def _marker(self, key: RunKey) -> Path:
        return Path(self._path, _SCORED, f"{_unit_name(key)}.marker")

    def _complete(self, file_name: str, decode: Callable[[Any], _T]) -> tuple[_T, ...]:
        """A log that a new line can safely follow."""
        records, tail = read_log(str(Path(self._path, file_name)), decode)
        if tail is not None:
            raise StoreError(f"{tail.file_name} line {tail.line_number} is truncated: a new line would merge with it")
        return records

    def _write(self, file: BinaryIO, line: bytes) -> None:
        file.write(line)
        file.flush()
        self._fsync(file.fileno())

    def _write_new(self, path: Path, line: bytes) -> None:
        with path.open("xb") as file:
            self._write(file, line)

    def _append(self, file_name: str, line: bytes) -> None:
        with Path(self._path, file_name).open("ab") as file:
            self._write(file, line)


@dataclass(frozen=True, slots=True)
class RunView:
    """Everything one run directory holds, typed. `truncated` lists every line a crash left unfinished."""

    manifest: Mapping[str, Any]
    pre_run: tuple[PreRunRecord, ...]
    launches: tuple[LaunchRecord, ...]
    calls: tuple[StoredCall, ...]
    runs: tuple[RunRecord, ...]
    faults: tuple[FaultRecord, ...]
    voids: tuple[VoidRecord, ...]
    scored: frozenset[RunKey]
    truncated: tuple[TruncatedLine, ...]

    def counted_attempt(self, candidate: str, run: int) -> int:
        """The attempt that is this run for every gate: the one complete, unvoided timed attempt, or `StoreError`."""
        voided = {void.key for void in self.voids}
        attempts = [
            record.key.run_attempt
            for record in self.runs
            if (record.key.candidate, record.key.phase, record.key.run) == (candidate, "timed_run", run)
            and record.status == "complete"
            and record.key not in voided
        ]
        if len(attempts) != 1:
            raise StoreError(f"{candidate} run {run} has {len(attempts)} complete, unvoided attempts, not exactly one")
        return attempts[0]

    def counted_calls(self, candidate: str, run: int) -> tuple[StoredCall, ...]:
        """The calls of that attempt, in order, warm-ups left out. A voided attempt's calls stay stored, uncounted."""
        key = RunKey(candidate, "timed_run", run, self.counted_attempt(candidate, run))
        return tuple(stored for stored in self.calls if stored.key == key and stored.role != "warmup")


def _read_markers(root: Path) -> tuple[frozenset[RunKey], tuple[TruncatedLine, ...]]:
    """The scored keys. A marker cut short by a crash is reported, and still blocks a void through its file."""
    keys: set[RunKey] = set()
    cut: list[TruncatedLine] = []
    try:
        markers = sorted((root / _SCORED).iterdir())
    except FileNotFoundError:
        raise StoreError(f"{_SCORED} is missing from the run directory") from None
    for marker in markers:
        records, tail = read_log(str(marker), _scored_key)
        if not records:
            cut.append(tail or TruncatedLine(marker.name, 1, 0))
        elif len(records) == 1 and tail is None and marker.name == f"{_unit_name(records[0])}.marker":
            keys.add(records[0])
        else:
            raise StoreError(f"{marker.name} is not a scored marker")
    return frozenset(keys), tuple(cut)


def load_run(path: str) -> RunView:
    """Read a run directory back. A corrupt complete line raises `StoreError`; truncated tails are collected."""
    root = Path(path)
    truncated: list[TruncatedLine] = []

    def read(file_name: str, decode: Callable[[Any], _T]) -> tuple[_T, ...]:
        records, tail = read_log(str(root / file_name), decode)
        if tail is not None:
            truncated.append(tail)
        return records

    manifests = read(_MANIFEST, _manifest)
    if len(manifests) != 1:
        raise StoreError(f"{_MANIFEST} must hold exactly one complete record")
    pre_run = read(_PRE_RUN, _pre_run)
    launches = read(_LAUNCHES, _launch)
    calls = read(_CALLS, _stored_call)
    runs = read(_RUNS, _run)
    faults = read(_FAULTS, _fault)
    voids = read(_VOIDS, _void)
    scored, cut_markers = _read_markers(root)
    return RunView(
        manifest=manifests[0],
        pre_run=pre_run,
        launches=launches,
        calls=calls,
        runs=runs,
        faults=faults,
        voids=voids,
        scored=scored,
        truncated=(*truncated, *cut_markers),
    )
