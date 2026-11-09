"""Test helpers for the H4a tests (H4a brief section 9; S2 §3.12). Data is synthetic, never gold rows.

`make_view` builds the `RunView` of a whole schedule in memory: three candidates, runs 1 to 3 in rotated order, each
with a warm-up, one single per gold conversation and 4 session-runs of a cap call and 20 probes, then each candidate's
unconstrained pass and contended session-run. Every call is a `CallRecord` built here, valid by default, with request
hashes the manifest holds. `store_view` writes such a view through a real `RunStore`, so `load_run` gives it back.

The gold set adds a fifth conversation to `_runner_fixtures.gold_rows()`, with one out-of-scope row, so that gate S's
out-of-scope leak rate is defined and a candidate can pass every gate. No nvidia-smi text, gpu.csv or host.json is
written: machine evidence is built as `AttemptEvidence` values, with the evidence figures (37/37 layers, 2 graph
splits, CUDA buffers 2,375.91 / 1,728.00 / 89.01 MiB, RssAnon 269,428 kB).
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.evaluation.bakeoff.client import (
    INVALID_KINDS,
    MAX_TOKENS,
    TIMEOUT_SEC,
    AttemptRecord,
    CallRecord,
    ContextCheck,
    Modalities,
    RequestKind,
    ThinkingCheck,
    Usage,
)
from app.evaluation.bakeoff.evidence import (
    AttemptEvidence,
    ContentionRecord,
    EvidenceLoader,
    GpuSample,
    HostRecord,
    ProcSample,
)
from app.evaluation.bakeoff.extraction import ExtractedClaim, Extraction
from app.evaluation.bakeoff.gold import GoldSet, load_gold_set
from app.evaluation.bakeoff.launch import PINNED_BUILD_INFO, StartupLog, frozen_argv
from app.evaluation.bakeoff.probe import ProbeOutput
from app.evaluation.bakeoff.records import (
    RUNS,
    FaultRecord,
    LaunchRecord,
    Phase,
    PreRunRecord,
    RunKey,
    RunManifest,
    RunRecord,
    RunStore,
    RunView,
    StoredCall,
    VoidRecord,
    _line,
    log_name,
    record_loads,
)
from app.evaluation.bakeoff.runner import _THINKING_MODE
from app.evaluation.bakeoff.schemas import EXTRACTION_SCHEMA_SHA256, PROBE_SCHEMA_SHA256
from app.evaluation.tests._fixtures import make_conversation, make_item, write_version
from app.evaluation.tests._runner_fixtures import (
    CONDITIONS,
    NOW,
    SCHEDULE_CANDIDATES,
    VALID_OPERATOR,
    fixed_now,
    gold_rows,
    no_fsync,
)

SINGLE: RequestKind = "single_conversation_extraction"
CAP: RequestKind = "cap_session_extraction"
PROBE: RequestKind = "reasoning_probe"
STAMP = NOW.isoformat()
WARMUP_ID = "warmup"
CAP_IDS = tuple(f"cap_{number}" for number in range(1, 5))
PROBE_IDS = tuple(f"probe_{number:02d}" for number in range(1, 21))
SECOND = 1_000_000_000
# The pre-run start-up figures of the 2026-11-07 evidence (Qwen3-4B): 37/37 layers, 2 graph splits, CUDA buffers.
STARTUP = StartupLog(4, 1, 12288, 18080, 37, 37, 2, 2, 2375.91, 1728.0, None, 89.01)
BUFFER_SUM = 4192.92  # 2375.91 + 1728.00 + 89.01 MiB
# Named outcomes: the failure kind of each attempt, None for a valid one.
OUTCOMES: Mapping[str, tuple[str | None, ...]] = {
    "valid": (None,),
    "repaired": ("invalid_output", None),
    "invalid": ("invalid_output", "invalid_output"),
    "cut_off": ("cut_off", "cut_off"),
    "cut_off_repaired": ("cut_off", None),
    "timeout": ("timeout", "timeout"),
    "timeout_then_valid": ("timeout", None),
    "server_error": ("server_error", "server_error"),
}

Override = Callable[[RunKey, str, int | None, int | None, str], CallRecord | None]


def write_h4_gold(root: Path) -> Path:
    """`gold_rows()` plus `syn_5`: one in-scope checkable row and one out-of-scope row, as version v1.0."""
    conversations, items = gold_rows()
    extra = make_conversation("syn_5", ["syn_5 line 1 states a fact.", "syn_5 line 2 is about a company."])
    items = [
        *items,
        make_item("syn_5_01", extra, 1, "CHECKABLE", "GENERAL"),
        make_item("syn_5_02", extra, 2, "CHECKABLE", "OTHER"),
    ]
    return write_version(root, [*conversations, extra], items, name="v1.0")


def h4_gold(root: Path) -> GoldSet:
    return load_gold_set(write_h4_gold(root))


def request_sha(item_id: str, constrained: bool, repair: bool = False) -> str:
    """The synthetic hash of a frozen request, or of its repair."""
    return hashlib.sha256(f"{item_id}|{constrained}|{repair}".encode("ascii")).hexdigest()


def _attempt(
    kind: RequestKind,
    constrained: bool,
    number: int,
    failure: str | None,
    *,
    sha: str,
    retry_type: str,
    wall_ns: int,
    content: str | None,
    cited: tuple[str, ...] | None,
    unknown: tuple[str, ...],
    thinking: bool,
) -> AttemptRecord:
    responded = failure is None or failure in INVALID_KINDS
    finish = "length" if failure == "cut_off" else "stop"
    probe_cites = kind == PROBE and finish == "stop" and responded
    fields: dict[str, Any] = {
        "request_kind": kind,
        "constrained": constrained,
        "attempt": number,
        "retry_type": retry_type,
        "request_sha256": sha,
        "max_tokens": MAX_TOKENS[kind],
        "timeout_sec": TIMEOUT_SEC[kind],
        "wall_ns": wall_ns,
        "http_status": 200 if responded else (500 if failure == "server_error" else None),
        "finish_reason": finish if responded else None,
        "usage": Usage(100, 10, 0) if responded else None,
        "timings": None,
        "server_model": "synthetic-model" if responded else None,
        "system_fingerprint": PINNED_BUILD_INFO if responded else None,
        "content": (content if content is not None else "{}") if responded else None,
        "reasoning_content": "synthetic thought" if responded and thinking else None,
        "thinking": ThinkingCheck(thinking, False) if responded else None,
        "cited_source_ids": cited if probe_cites else None,
        "unknown_source_ids": unknown if probe_cites else (),
        "failure_kind": failure,
        "error_text": "" if failure is None else f"synthetic {failure}",
        "server_error_type": "server_error" if failure == "server_error" else None,
    }
    return AttemptRecord(**fields)


def make_call(
    item_id: str,
    kind: RequestKind,
    outcome: str | Sequence[str | None] = "valid",
    *,
    constrained: bool = True,
    wall_ns: Sequence[int] = (SECOND, SECOND),
    output: Extraction | None = None,
    cited: tuple[str, ...] = ("src_1",),
    unknown: tuple[str, ...] = (),
    thinking: bool = False,
    content: str | None = None,
) -> CallRecord:
    """One call. `outcome` names a row of `OUTCOMES` or gives each attempt's failure kind. `cited` and `unknown` are
    the first attempt's citations; a later valid attempt cites `src_1`. `output` is the valid extraction."""
    failures = OUTCOMES[outcome] if isinstance(outcome, str) else tuple(outcome)
    attempts = []
    for number, failure in enumerate(failures, start=1):
        first = attempts[0] if attempts else None
        if first is None:
            retry, sha = "none", request_sha(item_id, constrained)
        elif first.failure_kind in INVALID_KINDS:
            retry, sha = "repair", request_sha(item_id, constrained, repair=True)
        else:
            retry, sha = "same_request", first.request_sha256
        attempts.append(
            _attempt(
                kind,
                constrained,
                number,
                failure,
                sha=sha,
                retry_type=retry,
                wall_ns=wall_ns[number - 1],
                content=content if number == 1 else None,
                cited=cited if number == 1 else ("src_1",),
                unknown=unknown if number == 1 else (),
                thinking=thinking,
            )
        )
    last = attempts[-1]
    checks: tuple[ContextCheck, ...] = ()
    if last.retry_type == "repair":
        tokens = MAX_TOKENS[kind]
        checks = (ContextCheck("repair", last.request_sha256, 100, 0, tokens, 100 + tokens, 12288, 11059, True, 1000),)
    result: Extraction | ProbeOutput | None = None
    if last.failure_kind is None:
        if kind == PROBE:
            result = ProbeOutput("SUPPORTED", "synthetic reason", last.cited_source_ids or ())
        else:
            result = output if output is not None else Extraction(item_id, (), ())
    return CallRecord(
        item_id=item_id,
        request_kind=kind,
        constrained=constrained,
        attempts=tuple(attempts),
        context_checks=checks,
        outcome=last.failure_kind or "valid",
        output=result,
        total_wall_ns=sum(attempt.wall_ns for attempt in attempts),
    )


def perfect_output(gold: GoldSet, source_id: str) -> Extraction:
    """One claim per in-scope checkable row of the conversation, on its own segment: recall and precision 1."""
    rows = [item for item in gold.in_scope_checkable() if item.source_id == source_id]
    claims = tuple(ExtractedClaim(index, "synthetic claim", (row.segment_id,)) for index, row in enumerate(rows))
    return Extraction(source_id, claims, ())


def cap_output(session_id: str, claims: int = 12) -> Extraction:
    return Extraction(
        session_id, tuple(ExtractedClaim(n, "synthetic claim", (f"seg_{n + 1:03d}",)) for n in range(claims)), ()
    )


def default_call(gold: GoldSet, key: RunKey, role: str, item_id: str) -> CallRecord:
    """The clean call at a place: singles find every row in 1 s, caps hold 12 claims in 40 s, probes take 1 s."""
    constrained = key.phase != "unconstrained"
    if role == "warmup":
        return make_call(WARMUP_ID, SINGLE, constrained=constrained)
    if role == "single":
        return make_call(item_id, SINGLE, constrained=constrained, output=perfect_output(gold, item_id))
    if role == "cap_session":
        return make_call(item_id, CAP, constrained=constrained, wall_ns=(40 * SECOND,), output=cap_output(item_id))
    return make_call(item_id, PROBE, constrained=constrained)


def _places(gold: GoldSet, phase: Phase) -> list[tuple[str, str | None, int | None, int | None, str]]:
    """Role, session id, session index, position and item id of each call of one unit, as the runner orders them."""
    places: list[tuple[str, str | None, int | None, int | None, str]] = [("warmup", None, None, None, WARMUP_ID)]
    if phase == "contended":
        places.append(("cap_session", CAP_IDS[0], 1, None, CAP_IDS[0]))
        return places + [("probe", CAP_IDS[0], 1, n, item) for n, item in enumerate(PROBE_IDS, start=1)]
    places += [("single", None, None, n, item) for n, item in enumerate(gold.conversation_ids(), start=1)]
    for index, cap in enumerate(CAP_IDS, start=1):
        places.append(("cap_session", cap, index, None, cap))
        if phase == "timed_run":
            places += [("probe", cap, index, n, item) for n, item in enumerate(PROBE_IDS, start=1)]
    if phase == "unconstrained":
        places += [("probe", None, None, n, item) for n, item in enumerate(PROBE_IDS, start=1)]
    return places


def manifest_dict(gold: GoldSet, operator: Mapping[str, str] = VALID_OPERATOR) -> dict[str, Any]:
    """The manifest as `load_run` gives it back: the stored record, `schema`, `record` and `stamp` included."""
    items = (WARMUP_ID, *gold.conversation_ids(), *CAP_IDS, *PROBE_IDS)
    hashes: dict[str, Any] = dict.fromkeys(
        ("prompts_sha256", "repair_sha256", "probe_sha256", "cap_sessions_sha256", "cap_requests_sha256"), "e" * 64
    )
    hashes["warmup_sha256"] = request_sha(WARMUP_ID, True)
    hashes["requests"] = {item: request_sha(item, True) for item in items}
    hashes["requests_unconstrained"] = {item: request_sha(item, False) for item in items}
    manifest = RunManifest(
        gold_version=gold.version,
        gold_manifest=dict(gold.manifest),
        hashes=hashes,
        extraction_schema_sha256=EXTRACTION_SCHEMA_SHA256,
        probe_schema_sha256=PROBE_SCHEMA_SHA256,
        sampling={"temperature": 0.0, "seed": 42, "top_k": 1, "repeat_penalty": 1.0},
        max_tokens=dict(MAX_TOKENS),
        timeout_sec=dict(TIMEOUT_SEC),
        thinking_mode=_THINKING_MODE,
        conditions={
            "binary_path": CONDITIONS.binary_path,
            "context": CONDITIONS.context,
            "kv_type": CONDITIONS.kv_type,
            "port": CONDITIONS.port,
            "ready_deadline_sec": CONDITIONS.ready_deadline_sec,
        },
        binary_sha256="d" * 64,
        pinned_build_info=PINNED_BUILD_INFO,
        interfaces=("lo",),
        candidates=tuple(
            {
                "candidate_id": candidate.candidate_id,
                "gguf_path": candidate.gguf_path,
                "gguf_sha256": candidate.gguf_sha256,
                "source_revision": candidate.source_revision,
                "quantization_commands": candidate.quantization_commands,
                "argv": list(frozen_argv(CONDITIONS, candidate)),
            }
            for candidate in SCHEDULE_CANDIDATES
        ),
        operator=dict(operator),
    )
    return record_loads(_line("manifest", manifest, stamp=STAMP)[:-1])


def launch_record(key: RunKey, startup: StartupLog = STARTUP) -> LaunchRecord:
    candidate = next(row for row in SCHEDULE_CANDIDATES if row.candidate_id == key.candidate)
    return LaunchRecord(
        stamp=STAMP,
        key=key,
        mode="pre_run" if key.phase == "pre_run" else "timed",
        argv=frozen_argv(CONDITIONS, candidate),
        env_names=("HOME", "PATH"),
        log_name=log_name(key),
        pid=4242,
        ready_sec=16.25,
        n_ctx=CONDITIONS.context,
        total_slots=1,
        build_info=PINNED_BUILD_INFO,
        model_path=candidate.gguf_path,
        chat_template_sha256="f" * 64,
        template_parser="qwen3_coder" if key.candidate == "Qwen/Qwen3.5-4B" else "auto",
        modalities=Modalities(False, False, False),
        startup=startup,
        exit_code=0,
        stopped_how="terminated",
    )


def make_view(
    gold: GoldSet,
    *,
    override: Override | None = None,
    not_loaded: Iterable[str] = (),
    thinking: Mapping[str, Any] | None = None,
    voids: Mapping[tuple[str, Phase, int | None], int] | None = None,
    operator: Mapping[str, str] = VALID_OPERATOR,
    startup: StartupLog = STARTUP,
    marked: bool = True,
) -> RunView:
    """A whole schedule. `override(key, role, session_index, position, item_id)` may replace any call. `voids` gives
    how many attempts of a unit are void (a `hook_failed` fault, a void and a run record with no call) before the
    complete one; 3 leaves the unit with no complete attempt. With `marked`, every counted unit is marked."""
    skipped = set(not_loaded)
    pre_run = tuple(
        PreRunRecord(
            STAMP,
            candidate.candidate_id,
            "not_loaded" if candidate.candidate_id in skipped else "ready",
            candidate.gguf_sha256,
            None,
            None if candidate.candidate_id in skipped else (thinking or {}).get(candidate.candidate_id, "off"),
            (),
            (),
        )
        for candidate in SCHEDULE_CANDIDATES
    )
    ready = [record.candidate for record in pre_run if record.status == "ready"]
    launches = [launch_record(RunKey(candidate, "pre_run", None, 1), startup) for candidate in ready]
    calls: list[StoredCall] = []
    runs: list[RunRecord] = []
    faults: list[FaultRecord] = []
    void_records: list[VoidRecord] = []

    def unit(candidate: str, phase: Phase, run: int | None) -> None:
        voided = (voids or {}).get((candidate, phase, run), 0)
        for attempt in range(1, voided + 1):
            key = RunKey(candidate, phase, run, attempt)
            faults.append(FaultRecord(len(faults) + 1, STAMP, key, "hook_failed", None, "before_timed_run: Synthetic"))
            void_records.append(VoidRecord(STAMP, key, len(faults)))
            runs.append(RunRecord(STAMP, key, "void", 0))
        if voided >= 3:
            return
        key = RunKey(candidate, phase, run, voided + 1)
        for role, session_id, index, position, item_id in _places(gold, phase):
            call = None if override is None else override(key, role, index, position, item_id)
            call = call or default_call(gold, key, role, item_id)
            calls.append(StoredCall(len(calls) + 1, STAMP, key, role, session_id, index, position, call))
        runs.append(RunRecord(STAMP, key, "complete", len(_places(gold, phase))))
        launches.append(launch_record(key, startup))

    for run in RUNS:
        shift = (run - 1) % len(ready) if ready else 0
        for candidate in ready[shift:] + ready[:shift]:
            unit(candidate, "timed_run", run)
    for candidate in ready:
        unit(candidate, "unconstrained", None)
        unit(candidate, "contended", None)
    view = RunView(
        manifest=manifest_dict(gold, operator),
        pre_run=pre_run,
        launches=tuple(launches),
        calls=tuple(calls),
        runs=tuple(runs),
        faults=tuple(faults),
        voids=tuple(void_records),
        scored=frozenset(),
        truncated=(),
    )
    return mark_all(view) if marked else view


def mark_all(view: RunView) -> RunView:
    """Every complete, unvoided attempt of a run unit marked as scored."""
    voided = {void.key for void in view.voids}
    keys = {run.key for run in view.runs if run.status == "complete" and run.key not in voided}
    return dataclasses.replace(view, scored=frozenset(keys))


def at(view: RunView, key: RunKey, role: str, session_index: int | None, position: int | None) -> StoredCall:
    return next(
        stored
        for stored in view.calls
        if (stored.key, stored.role, stored.session_index, stored.position) == (key, role, session_index, position)
    )


def with_call(
    view: RunView, key: RunKey, role: str, session_index: int | None, position: int | None, call: CallRecord
) -> RunView:
    """`view` with the call at one place replaced."""
    old = at(view, key, role, session_index, position)
    new = dataclasses.replace(old, call=call)
    return dataclasses.replace(view, calls=tuple(new if stored is old else stored for stored in view.calls))


def store_view(view: RunView, parent: Path, name: str = "run_1") -> str:
    """Write `view` through a real `RunStore` under `parent`, so `load_run` of the result equals `view`."""
    fields = {key: value for key, value in view.manifest.items() if key not in ("schema", "record", "stamp")}
    fields["interfaces"] = tuple(fields["interfaces"])
    fields["candidates"] = tuple(fields["candidates"])
    store = RunStore.create(str(parent), name, RunManifest(**fields), now=fixed_now, fsync=no_fsync)
    for record in view.pre_run:
        store.append_pre_run(record)
    for launch in view.launches:
        store.append_launch(launch)
    for stored in view.calls:
        store.append_call(
            stored.key,
            stored.role,
            session_id=stored.session_id,
            session_index=stored.session_index,
            position=stored.position,
            call=stored.call,
        )
    for run in view.runs:
        store.append_run(run)
    attached = RunStore.attach(store.path, now=fixed_now, fsync=no_fsync)
    for fault in view.faults:
        attached.log_fault(fault.key, fault.kind, item_id=fault.item_id, detail=fault.detail)
    for void in view.voids:
        attached.void_run(void.key, void.fault_id)
    for key in view.scored:
        attached.mark_scored(key)
    return store.path


def contention_record(ended_before_stop_ms: int | None = None) -> ContentionRecord:
    """The together session's audio command; with a figure, it ended that many ms before the stop."""
    ended = ended_before_stop_ms is not None
    how = None if ended else "terminated"
    return ContentionRecord("c" * 64, 5.0, 0 if ended else None, ended, ended_before_stop_ms, how)


def attempt_evidence(
    key: RunKey,
    *,
    used: Sequence[int] = (1000, *(5304,) * 20),
    free: int = 7899,
    rss_kib: Sequence[int] = (269_428,),
    pswpout: tuple[int, int] = (1204, 1204),
    contention: ContentionRecord | None = None,
    elapsed_ms: int | None = None,
    clocks: str = "1530 MHz",
    temperature: str = "45",
    power: str = "[N/A]",
) -> AttemptEvidence:
    """One attempt's evidence: GPU samples 50 ms apart from `used` (the first is the baseline, and memory.free falls
    as memory.used rises), one RSS reading per `rss_kib`, and the swap counter before and after."""
    gpu = tuple(
        GpuSample(50 * n, value, free - (value - used[0]), clocks, clocks, temperature, power)
        for n, value in enumerate(used)
    )
    proc = tuple(ProcSample(100 * n, kib) for n, kib in enumerate(rss_kib))
    elapsed = 50 * len(used) if elapsed_ms is None else elapsed_ms
    host = HostRecord(key, 0, pswpout[0], pswpout[1], elapsed, "terminated", True, None, contention)
    return AttemptEvidence(key, gpu, proc, host)


def passing_evidence(view: RunView, **options: Any) -> dict[RunKey, AttemptEvidence | None]:
    """Clean evidence for every hooked attempt with a run record, voided ones included: a 4,304 MiB rise."""
    evidence: dict[RunKey, AttemptEvidence | None] = {}
    for run in view.runs:
        if run.key.phase in ("timed_run", "contended"):
            contention = contention_record() if run.key.phase == "contended" else None
            evidence[run.key] = attempt_evidence(run.key, contention=contention, **options)
    return evidence


def loader(mapping: Mapping[RunKey, AttemptEvidence | None]) -> EvidenceLoader:
    """An `EvidenceLoader` over a table; a key it does not hold has no host record."""

    def load(_run_dir: str, key: RunKey, _read_bytes: Callable[[str], bytes]) -> AttemptEvidence | None:
        return mapping.get(key)

    return load
