"""The schedule of a bake-off (bake-off harness H3).

This module runs everything a candidate is measured on, in the order the note fixes, and stores every call. It scores
nothing and holds no prompt text: `inputs` froze the requests, `launch` starts and checks the server, `records` writes,
and H4 reads the records.

Implements, from the signed D6 criteria note S2:

- S2 §1, candidates: "Every candidate that loads is run". A candidate that does not load is recorded and left out. Any
  other failed pre-run check stops the schedule before any run, once every candidate has had its pre-run, because one
  context must suit every tokenizer (AMB-64, AMB-65).
- S2 §3.1, machine: hooks before and after each timed run, so that H4's sampler and the operator's check that no other
  CUDA process is running can attach. H3 implements neither (AMB-73).
- S2 §3.5, context: `check_context` and `repair_bound` on every frozen request, in both forms, on each candidate's own
  server, because tokenizers differ. `fits` False stops before any run. The context comes from configuration, so
  raising it for every candidate needs no code change.
- S2 §3.6, thinking: "The pre-run check confirms that no thinking text appears", read from the warm-up's attempts. Text
  seen means information only: the candidate runs, and H4 applies the rule. A check that could not look is no evidence,
  which is a failed precondition and says nothing about the model.
- S2 §3.8, diagnostic pass: one unconstrained pass per candidate, on its own launch, each frozen request once (AMB-67).
  It is "reported but not gated", so it stays out of the determinism share.
- S2 §3.13, runs: run 1 of every candidate in rotated order, then run 2, then run 3, with one launch each, each starting
  with one discarded warm-up. "A run is void only for an infrastructure fault logged before its scores are computed":
  a `ClientFault`, a server that exited, a nonzero cache count, a failed launch or a failed hook. A void unit repeats on
  a fresh launch (AMB-69). `determinism_shares` is "the share of requests whose output differs from run 1", exactly.
- S2 §6, gates: a run holds every single conversation, then every session-run: the cap-session call, then 20 probe
  calls in fixed order. "Timeouts and server errors count as failures", so from a live server they are stored and the
  run goes on (AMB-70). One contended session-run per candidate runs through an operator hook (AMB-68).

Every call is stored before the next one is sent. Only `ClientFault`, `LaunchError`, `ModelFileError` and hook
exceptions are caught, so any other exception is a harness bug: it propagates, the server is still stopped, and the
attempt gets no `RunRecord`. There is no resume (AMB-72).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from typing import Any, Protocol

from .client import (
    MAX_TOKENS,
    REPEAT_PENALTY,
    SEED,
    TEMPERATURE,
    TIMEOUT_SEC,
    TOP_K,
    CallRecord,
    ClientFault,
    ContextBudgetExceeded,
    ContextCheck,
    ModelServerClient,
    ServerCheckError,
    ServerProps,
)
from .inputs import FrozenInputs, FrozenRequest
from .launch import (
    CANDIDATE_IDS,
    EXIT_SETTLE_SEC,
    PINNED_BUILD_INFO,
    Candidate,
    LaunchCheckFailed,
    LaunchError,
    LaunchMode,
    LaunchTools,
    ModelFileError,
    ReadyTimeout,
    RunConditions,
    ServerExited,
    ServerProcess,
    ServerStopFailed,
    SpawnFailed,
    StartupLog,
    StopResult,
    base_url,
    check_loopback_only,
    check_model_file,
    frozen_argv,
    server_environment,
    sha256_file,
    template_parser,
    validate_candidates,
    verify_launch,
    wait_ready,
)
from .records import (
    RUNS,
    CallRole,
    FaultKind,
    LaunchRecord,
    Phase,
    PreRunRecord,
    PreRunStatus,
    RunKey,
    RunManifest,
    RunRecord,
    RunStore,
    RunView,
    StoredCall,
    ThinkingVerdict,
    log_name,
)
from .schemas import EXTRACTION_SCHEMA_SHA256, PROBE_SCHEMA_SHA256, canonical_sha256

MAX_RUN_ATTEMPTS = 3

_THINKING_MODE = "off: chat_template_kwargs.enable_thinking=false"
_Place = tuple[str, int | None, int | None]  # role, session index, position


class RunHooks(Protocol):
    """What the operator and H4 attach to a schedule. There is no default: a real schedule passes all four. `key` names
    the attempt. An `Exception` from any of them is a fault that voids that attempt."""

    def before_timed_run(self, key: RunKey) -> None: ...

    def after_timed_run(self, key: RunKey) -> None: ...

    def start_contention(self, key: RunKey) -> None: ...

    def stop_contention(self, key: RunKey) -> None: ...


@dataclass(frozen=True, slots=True)
class ScheduleConfig:
    """What one schedule runs. `store_parent` has no default, because the repo itself lives under OneDrive."""

    candidates: tuple[Candidate, ...]
    conditions: RunConditions
    operator: Mapping[str, str]
    store_parent: str
    run_name: str


@dataclass(frozen=True, slots=True)
class RunnerDeps:
    """Every outside thing the schedule touches, each injected. `client_factory` takes the base URL and the context."""

    hooks: RunHooks
    client_factory: Callable[[str, int], ModelServerClient]
    tools: LaunchTools
    now: Callable[[], datetime]
    fsync: Callable[[int], None]


class PreRunStopped(RuntimeError):
    """A pre-run check failed or no candidate is ready, so no run started (S2 §3.5, §11). The records say why."""

    def __init__(self, run_dir: str, pre_run: tuple[PreRunRecord, ...]) -> None:
        super().__init__(run_dir, pre_run)
        self.run_dir = run_dir
        self.pre_run = pre_run

    def __str__(self) -> str:
        return f"no run started: the pre-run records are in {self.run_dir}"


class RunsExhausted(RuntimeError):
    """A timed run was void on every attempt. Gate T needs every session-run, so the schedule stops for the owner, with
    everything kept (AMB-69). `key` is the last attempt's."""

    def __init__(self, key: RunKey, run_dir: str) -> None:
        super().__init__(key, run_dir)
        self.key = key
        self.run_dir = run_dir

    def __str__(self) -> str:
        return f"{self.key.candidate} run {self.key.run} was void {self.key.run_attempt} times: see {self.run_dir}"


class DeterminismError(ValueError):
    """Two runs cannot be compared request by request: a missing counterpart is never skipped (H1 AMB-25)."""


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """How a schedule ended. `ran` holds the candidates that loaded, and `voided` every attempt void, in order."""

    run_dir: str
    pre_run: tuple[PreRunRecord, ...]
    ran: tuple[str, ...]
    voided: tuple[RunKey, ...]


@dataclass(frozen=True, slots=True)
class DeterminismShare:
    """S2 §3.13 for one candidate: `differing` of the `compared` request pairs of runs 2 and 3 differ from run 1."""

    candidate: str
    differing: int
    compared: int

    def __post_init__(self) -> None:
        if self.compared < 1 or not 0 <= self.differing <= self.compared:
            raise DeterminismError(f"{self.candidate}: {self.differing} of {self.compared} pairs is no share")

    @property
    def share(self) -> Fraction:
        """Exact, as `stats.determinism_variant` takes it."""
        return Fraction(self.differing, self.compared)


def rotation(candidates: tuple[Candidate, ...], run: int) -> tuple[Candidate, ...]:
    """S2 §3.13's "rotated order" (AMB-63): the ready candidates, in S2 §1 order, turned left by one for each run after
    the first. A repeat of a voided run never moves it."""
    if not candidates:
        return ()
    shift = (run - 1) % len(candidates)
    return candidates[shift:] + candidates[:shift]


def thinking_verdict(call: CallRecord) -> ThinkingVerdict:
    """S2 §3.6 on every attempt of one call. Text seen refutes "no thinking text appears", whatever the other attempt
    did. Short of that, an attempt with no response was never checked, which must not read as checked and clean."""
    if call.thinking_seen:
        return "seen"
    return "no_evidence" if any(attempt.thinking is None for attempt in call.attempts) else "off"


@dataclass(frozen=True, slots=True)
class _Step:
    """One planned call and its tags (D8)."""

    role: CallRole
    frozen: FrozenRequest
    session_id: str | None = None
    session_index: int | None = None
    position: int | None = None


def _session_steps(inputs: FrozenInputs, cap: FrozenRequest, session_index: int) -> Iterator[_Step]:
    """One session-run: the cap-session call, then the probes in frozen order (S2 §6 gate T). The session id is the cap
    call's item id (AMB-79)."""
    yield _Step("cap_session", cap, cap.item_id, session_index)
    for position, probe in enumerate(inputs.probes, start=1):
        yield _Step("probe", probe, cap.item_id, session_index, position)


def _single_steps(inputs: FrozenInputs) -> Iterator[_Step]:
    yield _Step("warmup", inputs.warmup)
    for position, single in enumerate(inputs.singles, start=1):
        yield _Step("single", single, position=position)


def _timed_steps(inputs: FrozenInputs) -> Iterator[_Step]:
    """A timed run: the warm-up, every single conversation, then every session-run. There is no separate probe pass, so
    a run holds 20 probe calls per session, and tags keep each of them apart (AMB-66)."""
    yield from _single_steps(inputs)
    for session_index, cap in enumerate(inputs.cap_sessions, start=1):
        yield from _session_steps(inputs, cap, session_index)


def _unconstrained_steps(inputs: FrozenInputs) -> Iterator[_Step]:
    """Each frozen request once (AMB-67), so a probe belongs to no session here and carries its position only."""
    yield from _single_steps(inputs)
    for session_index, cap in enumerate(inputs.cap_sessions, start=1):
        yield _Step("cap_session", cap, cap.item_id, session_index)
    for position, probe in enumerate(inputs.probes, start=1):
        yield _Step("probe", probe, position=position)


@dataclass(slots=True)
class _Attempt:
    """One launch of a unit, as far as it has got. `first_fault` is what a void names, `calls` what its `RunRecord`
    counts, and `launch` its `LaunchRecord`, once it has stopped. The fields from `loaded` down feed a `PreRunRecord`;
    `blocked` holds the check names that stop the runs."""

    key: RunKey
    candidate: Candidate
    mode: LaunchMode
    calls: int = 0
    first_fault: int | None = None
    launch: LaunchRecord | None = None
    loaded: bool = False
    exit_code: int | None = None
    thinking: ThinkingVerdict | None = None
    context_checks: list[ContextCheck] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Live:
    """A server that passed its checks, and the client that talks to it."""

    attempt: _Attempt
    process: ServerProcess
    client: ModelServerClient


@dataclass(frozen=True, slots=True)
class _Verified:
    """What a launch's `LaunchRecord` needs from its checks."""

    props: ServerProps
    ready_sec: float
    startup: StartupLog


class _Faulted(Exception):
    """A fault of this attempt is logged, so nothing more is sent on this launch. `kind` is the fault's."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def _launch_fault(exc: LaunchError, port: int) -> tuple[FaultKind, str, str]:
    """D12's kind and detail for a failed launch, and the name U3 blocks a pre-run with. `str(exc)` is names and numbers
    only, so no log or server text reaches the fault log (S2 §9; AMB-71)."""
    if isinstance(exc, ServerExited):
        if exc.bind_failed:
            return "launch_failed", f"port {port} in use", "port"
        return "launch_failed", str(exc), "server_exit"
    if isinstance(exc, ReadyTimeout):
        return "ready_timeout", str(exc), "ready_timeout"
    if isinstance(exc, LaunchCheckFailed):
        return "launch_check", str(exc), exc.check
    return "launch_failed", str(exc), "spawn"


def _client_fault_detail(exc: ClientFault) -> str:
    """Never `str(exc)`: a `ServerCheckError` holds the server's own error text (S2 §9; AMB-71)."""
    if isinstance(exc, ContextBudgetExceeded):
        return f"ContextBudgetExceeded uses={exc.check.uses} limit={exc.check.limit}"
    if isinstance(exc, ServerCheckError):
        return f"ServerCheckError path={exc.path} http_status={exc.http_status}"
    return type(exc).__name__


def _cached_detail(call: CallRecord) -> str | None:
    """S2 §3.3: every request sends `cache_prompt: false`, so a nonzero cache count on any attempt is an infrastructure
    fault (AMB-80). `None` is no count at all, not a fault. One call gives at most one fault: the first attempt's."""
    for attempt in call.attempts:
        cached = None if attempt.usage is None else attempt.usage.cached_tokens
        cache_n = None if attempt.timings is None else attempt.timings.cache_n
        if (cached or 0) > 0 or (cache_n or 0) > 0:
            return f"cached_tokens={cached} cache_n={cache_n} attempt={attempt.attempt}"
    return None


def _pre_run_status(attempt: _Attempt) -> PreRunStatus:
    if attempt.blocked:
        return "blocked"
    return "ready" if attempt.loaded else "not_loaded"


def _manifest(
    inputs: FrozenInputs,
    config: ScheduleConfig,
    candidates: tuple[Candidate, ...],
    interfaces: tuple[str, ...],
    binary_sha256: str,
) -> RunManifest:
    """S2 §9 and Annex A, as far as the harness can know them. Everything is a plain dict or list, because `record_json`
    refuses a mapping proxy. A bad operator value is refused here, so it never gets a directory."""
    conditions = config.conditions
    hashes = {item.name: getattr(inputs.hashes, item.name) for item in dataclasses.fields(inputs.hashes)}
    return RunManifest(
        gold_version=inputs.gold_version,
        gold_manifest=dict(inputs.gold_manifest),
        hashes={name: dict(value) if isinstance(value, Mapping) else value for name, value in hashes.items()},
        extraction_schema_sha256=EXTRACTION_SCHEMA_SHA256,
        probe_schema_sha256=PROBE_SCHEMA_SHA256,
        sampling={"temperature": TEMPERATURE, "seed": SEED, "top_k": TOP_K, "repeat_penalty": REPEAT_PENALTY},
        max_tokens=dict(MAX_TOKENS),
        timeout_sec=dict(TIMEOUT_SEC),
        thinking_mode=_THINKING_MODE,
        conditions={
            "binary_path": conditions.binary_path,
            "context": conditions.context,
            "kv_type": conditions.kv_type,
            "port": conditions.port,
            "ready_deadline_sec": conditions.ready_deadline_sec,
        },
        binary_sha256=binary_sha256,
        pinned_build_info=PINNED_BUILD_INFO,
        interfaces=interfaces,
        candidates=tuple(
            {
                "candidate_id": candidate.candidate_id,
                "gguf_path": candidate.gguf_path,
                "gguf_sha256": candidate.gguf_sha256,
                "source_revision": candidate.source_revision,
                "quantization_commands": candidate.quantization_commands,
                "argv": list(frozen_argv(conditions, candidate)),
            }
            for candidate in candidates
        ),
        operator=dict(config.operator),
    )


class _Schedule:
    """What every unit of one `run_schedule` shares. `voided` lists every attempt void so far."""

    def __init__(self, inputs: FrozenInputs, config: ScheduleConfig, deps: RunnerDeps, store: RunStore) -> None:
        self._inputs = inputs
        self._conditions = config.conditions
        self._deps = deps
        self._store = store
        self.voided: list[RunKey] = []

    def pre_run(self, candidate: Candidate) -> PreRunRecord:
        """One candidate's pre-run, on the verbose launch: it gets one record whatever happens, and no hook fires. Its
        timing is never used, because verbose logging slows the calls. A server that will not stop holds the port and
        the GPU, so `ServerStopFailed` ends the schedule even here, once the record is written (AMB-100)."""
        attempt = _Attempt(RunKey(candidate.candidate_id, "pre_run", None, 1), candidate, "pre_run")
        gguf_sha256: str | None = None
        stuck: ServerStopFailed | None = None
        try:
            gguf_sha256 = check_model_file(candidate, self._deps.tools)
            self._launch(attempt, self._pre_run_work)
        except ModelFileError as exc:
            attempt.blocked.append(exc.check)
        except ServerStopFailed as exc:
            attempt.blocked.append("server_not_stopped")
            stuck = exc
        finally:
            self._store_launch(attempt)
        record = PreRunRecord(
            stamp=self._store.stamp(),
            candidate=candidate.candidate_id,
            status=_pre_run_status(attempt),
            gguf_sha256=gguf_sha256,
            exit_code=attempt.exit_code,
            thinking=attempt.thinking,
            context_checks=tuple(attempt.context_checks),
            blocked=tuple(attempt.blocked),
        )
        self._store.append_pre_run(record)
        if stuck is not None:
            raise stuck
        return record

    def timed_run(self, candidate: Candidate, run: int) -> None:
        """One run of one candidate. Gate T and the variant need all three runs (S2 §6, §3.13), so with no complete
        attempt the schedule stops."""
        last = self._repeat(candidate, "timed_run", run, self._timed_work)
        if last is not None:
            raise RunsExhausted(last, self._store.path)

    def unconstrained(self, candidate: Candidate) -> None:
        """The diagnostic pass (S2 §3.8). It is not gated, so a unit with no complete attempt stays absent."""
        self._repeat(candidate, "unconstrained", None, self._unconstrained_work)

    def contended(self, candidate: Candidate) -> None:
        """The contended session-run (S2 §6). It is not gated, so a unit with no complete attempt stays absent."""
        self._repeat(candidate, "contended", None, self._contended_work)

    def _repeat(
        self, candidate: Candidate, phase: Phase, run: int | None, work: Callable[[_Live], None]
    ) -> RunKey | None:
        """The attempts of one unit, at once and in place, so the rotation holds and nobody chooses by result. The key
        of the last attempt comes back when none completed, and `None` when one did."""
        key: RunKey | None = None
        # NEEDS-OWNER(AMB-69): a voided unit repeats at once, 3 attempts at most; a third timed void stops the schedule
        for run_attempt in range(1, MAX_RUN_ATTEMPTS + 1):
            key = RunKey(candidate.candidate_id, phase, run, run_attempt)
            if self._run_attempt(key, candidate, work):
                return None
        return key

    def _run_attempt(self, key: RunKey, candidate: Candidate, work: Callable[[_Live], None]) -> bool:
        """One attempt, ending in its `RunRecord`, and whether it is complete. The hooks sit outside the process, so a
        sampler sees the whole rise and the operator's check sees no CUDA process at all (AMB-73), and the after-hook
        fires on every way out once the before-hook has returned. The first fault is the one the void names; a later
        one is logged and voids nothing more. A harness bug leaves here at once, with no record (U22)."""
        attempt = _Attempt(key, candidate, "timed")
        hooked = key.phase != "unconstrained"
        entered = not hooked or self._hook(attempt, "before_timed_run")
        stuck: ServerStopFailed | None = None
        try:
            if entered:
                self._launch(attempt, work)
        except ServerStopFailed as exc:
            stuck = exc
        finally:
            if hooked and entered:
                self._hook(attempt, "after_timed_run")
            self._store_launch(attempt)
        complete = attempt.first_fault is None
        if attempt.first_fault is not None:
            self._store.void_run(key, attempt.first_fault)
            self.voided.append(key)
        self._store.append_run(RunRecord(self._store.stamp(), key, "complete" if complete else "void", attempt.calls))
        if stuck is not None:
            raise stuck
        return complete

    def _launch(self, attempt: _Attempt, work: Callable[[_Live], None]) -> None:
        """Start, check and stop one server around `work`. Once the process exists it is stopped on every way out,
        `KeyboardInterrupt` included. Nothing is repeated from the pre-run but the free checks (AMB-84)."""
        conditions = self._conditions
        tools = self._deps.tools
        try:
            process = ServerProcess(
                frozen_argv(conditions, attempt.candidate),
                server_environment(conditions, attempt.mode),
                self._store.server_log_path(attempt.key),
                tools,
            )
        except SpawnFailed as exc:
            self._launch_failed(attempt, exc)
            return
        verified: _Verified | None = None
        try:
            with self._deps.client_factory(base_url(conditions), conditions.context) as client:
                props, ready_sec = wait_ready(process, client, conditions, tools)
                attempt.loaded = True
                startup = verify_launch(process, props, conditions, attempt.candidate, attempt.mode, tools)
                verified = _Verified(props, ready_sec, startup)
                live = _Live(attempt, process, client)
                work(live)
                self._last_poll(live)
        except _Faulted as stopped:
            attempt.blocked.append(stopped.kind)
        except LaunchError as exc:
            self._launch_failed(attempt, exc)
        finally:
            self._stop(attempt, process, verified)

    def _launch_failed(self, attempt: _Attempt, exc: LaunchError) -> None:
        """A failed launch is a fault of this attempt, but for one case: a pre-run server that dies while loading is no
        fault, because that candidate does not load. A bind failure is not that case: the port failed before any load
        (AMB-86)."""
        if isinstance(exc, ServerExited) and attempt.mode == "pre_run" and not attempt.loaded and not exc.bind_failed:
            return
        kind, detail, check = _launch_fault(exc, self._conditions.port)
        self._fault(attempt, kind, None, detail)
        attempt.blocked.append(check)

    def _stop(self, attempt: _Attempt, process: ServerProcess, verified: _Verified | None) -> None:
        """Stop the server, and build its `LaunchRecord` if it passed its checks. A server that ended by itself leaves
        its exit code for the pre-run record (AMB-99)."""
        try:
            stopped = process.stop()
        except ServerStopFailed as exc:
            self._fault(attempt, "server_not_stopped", None, str(exc))
            raise
        if stopped.how == "already_exited":
            attempt.exit_code = stopped.exit_code
        if verified is not None:
            attempt.launch = self._launch_record(attempt, process, verified, stopped)

    def _store_launch(self, attempt: _Attempt) -> None:
        """After the stop and the after-hook, on every way out, even a harness bug's (AMB-102)."""
        if attempt.launch is not None:
            self._store.append_launch(attempt.launch)

    def _launch_record(
        self, attempt: _Attempt, process: ServerProcess, verified: _Verified, stopped: StopResult
    ) -> LaunchRecord:
        """Names and hashes only: the template text, the `/props` defaults and the environment's values stay out."""
        props = verified.props
        return LaunchRecord(
            stamp=self._store.stamp(),
            key=attempt.key,
            mode=attempt.mode,
            argv=frozen_argv(self._conditions, attempt.candidate),
            env_names=tuple(sorted(server_environment(self._conditions, attempt.mode))),
            log_name=log_name(attempt.key),
            pid=process.pid,
            ready_sec=verified.ready_sec,
            n_ctx=props.n_ctx,
            total_slots=props.total_slots,
            build_info=props.build_info,
            model_path=props.model_path,
            chat_template_sha256=canonical_sha256(props.chat_template),
            template_parser=template_parser(props.chat_template),
            modalities=props.modalities,
            startup=verified.startup,
            exit_code=stopped.exit_code,
            stopped_how=stopped.how,
        )

    def _pre_run_work(self, live: _Live) -> None:
        """The warm-up, S2 §3.6 on its attempts, then S2 §3.5 on every frozen request. A thinking block lets the context
        checks run, so that one pre-run shows all it can; a fault ends it at once. Originals are not recounted later."""
        attempt = live.attempt
        call = self._send(live, _Step("warmup", self._inputs.warmup))
        attempt.thinking = thinking_verdict(call)
        if attempt.thinking == "no_evidence":
            attempt.blocked.append("thinking")
        repair = self._inputs.prompts.repair
        for frozen in self._inputs.all_requests():
            for request in (frozen.request, frozen.unconstrained):
                try:
                    _keep_check(attempt, live.client.check_context(request))
                    _keep_check(attempt, live.client.repair_bound(request, repair))
                except ClientFault as exc:
                    raise self._client_fault(live, frozen.item_id, exc) from None

    def _timed_work(self, live: _Live) -> None:
        for step in _timed_steps(self._inputs):
            self._send(live, step)

    def _unconstrained_work(self, live: _Live) -> None:
        for step in _unconstrained_steps(self._inputs):
            self._send(live, step)

    def _contended_work(self, live: _Live) -> None:
        """The first frozen session and its probes, the same for every candidate (AMB-68). The warm-up comes before the
        operator's hook, and H3 never starts the audio pipeline itself."""
        self._send(live, _Step("warmup", self._inputs.warmup))
        if not self._hook(live.attempt, "start_contention"):
            raise _Faulted("hook_failed")
        try:
            for step in _session_steps(self._inputs, self._inputs.cap_sessions[0], 1):
                self._send(live, step)
        finally:
            self._hook(live.attempt, "stop_contention")

    def _send(self, live: _Live, step: _Step) -> CallRecord:
        """One call, stored before anything else happens. A `ClientFault` has no `CallRecord`, so its attempt is not
        stored (AMB-87). A failed call from a live server is a model failure, and the run goes on (AMB-70)."""
        attempt = live.attempt
        frozen = step.frozen
        constrained = attempt.key.phase != "unconstrained"
        request = frozen.request if constrained else frozen.unconstrained
        template = self._inputs.prompts.repair
        try:
            if request.kind == "reasoning_probe":
                call = live.client.probe(
                    request, item_id=frozen.item_id, bundle_source_ids=frozen.ids, repair_template=template
                )
            else:
                call = live.client.extract(
                    request, item_id=frozen.item_id, request_segment_ids=frozen.ids, repair_template=template
                )
        except ClientFault as exc:
            raise self._client_fault(live, frozen.item_id, exc) from None
        self._store.append_call(
            attempt.key,
            step.role,
            session_id=step.session_id,
            session_index=step.session_index,
            position=step.position,
            call=call,
        )
        attempt.calls += 1
        hashes = self._inputs.hashes
        sent_as_frozen = hashes.requests if constrained else hashes.requests_unconstrained
        if call.attempts[0].request_sha256 != sent_as_frozen[frozen.item_id]:
            raise RuntimeError(f"{frozen.item_id} was not sent as frozen (S2 §3.10)")  # a harness bug, so no fault
        detail = _cached_detail(call)
        if detail is not None:
            raise self._fault(attempt, "cached_tokens", frozen.item_id, detail)
        self._check_exit(live, call)
        return call

    def _check_exit(self, live: _Live, call: CallRecord) -> None:
        """A server that exits mid-run is an infrastructure fault, although H2 records its failed call as a server
        error. Only the last attempt counts, because a later answer proves the process alive. After a dropped connection
        the socket can close before the process is gone, so that case gets a grace; nothing else waits (AMB-74)."""
        last = call.attempts[-1]
        if last.failure_kind not in ("timeout", "server_error"):
            return
        dropped = last.failure_kind == "server_error" and last.http_status is None
        code = live.process.exit_within(EXIT_SETTLE_SEC) if dropped else live.process.poll()
        if code is not None:
            raise self._fault(live.attempt, "server_exit", call.item_id, f"server exited with code {code}")

    def _last_poll(self, live: _Live) -> None:
        """The fault window runs until the harness begins to stop the server (AMB-78)."""
        code = live.process.poll()
        if code is not None:
            raise self._fault(live.attempt, "server_exit", None, f"server exited with code {code}")

    def _client_fault(self, live: _Live, item_id: str, exc: ClientFault) -> _Faulted:
        """A run condition, never a model failure. A fault with no HTTP status may be the server dying, so it gets the
        same grace as a dropped connection; either way it is one fault."""
        status = exc.http_status if isinstance(exc, ServerCheckError) else None
        code = live.process.exit_within(EXIT_SETTLE_SEC) if status is None else live.process.poll()
        if code is not None:
            return self._fault(live.attempt, "server_exit", item_id, f"server exited with code {code}")
        return self._fault(live.attempt, "client_fault", item_id, _client_fault_detail(exc))

    def _hook(self, attempt: _Attempt, name: str) -> bool:
        """Call one hook, and say whether it returned. A failure is a fault of this attempt."""
        try:
            getattr(self._deps.hooks, name)(attempt.key)
        except Exception as exc:
            self._fault(attempt, "hook_failed", None, f"{name}: {type(exc).__name__}")
            return False
        return True

    def _fault(self, attempt: _Attempt, kind: FaultKind, item_id: str | None, detail: str) -> _Faulted:
        """Log one fault, and give what stops the launch for the caller to raise. A fault alone voids nothing."""
        record = self._store.log_fault(attempt.key, kind, item_id=item_id, detail=detail)
        if attempt.first_fault is None:
            attempt.first_fault = record.fault_id
        return _Faulted(kind)


def _keep_check(attempt: _Attempt, check: ContextCheck) -> None:
    """`fits` False is an answer, not an error: the remaining checks still run, and the block is named once."""
    attempt.context_checks.append(check)
    if not check.fits and "context" not in attempt.blocked:
        attempt.blocked.append("context")


def run_schedule(inputs: FrozenInputs, config: ScheduleConfig, deps: RunnerDeps) -> ScheduleResult:
    """One whole schedule, into one new run directory. Everything that can be refused is refused before the directory
    exists, so running outside the namespace leaves nothing behind.

    Raises `PreRunStopped` when a pre-run check fails, `RunsExhausted` when a timed run is void on every attempt, and
    `ServerStopFailed` when a server will not stop. In each case the directory keeps everything stored so far.
    """
    tools = deps.tools
    candidates = validate_candidates(config.candidates)
    interfaces = check_loopback_only(tools)
    binary_sha256 = sha256_file(config.conditions.binary_path, tools)
    manifest = _manifest(inputs, config, candidates, interfaces, binary_sha256)
    store = RunStore.create(
        config.store_parent, config.run_name, manifest, now=deps.now, fsync=deps.fsync, realpath=tools.realpath
    )
    schedule = _Schedule(inputs, config, deps, store)
    pre_run = tuple(schedule.pre_run(candidate) for candidate in candidates)
    ready = tuple(candidate for candidate, record in zip(candidates, pre_run, strict=True) if record.status == "ready")
    if not ready or any(record.status == "blocked" for record in pre_run):
        raise PreRunStopped(store.path, pre_run)
    for run in RUNS:
        for candidate in rotation(ready, run):
            schedule.timed_run(candidate, run)
    for candidate in ready:
        schedule.unconstrained(candidate)
        schedule.contended(candidate)
    return ScheduleResult(
        store.path, pre_run, tuple(candidate.candidate_id for candidate in ready), tuple(schedule.voided)
    )


def _places(view: RunView, candidate: str, run: int) -> dict[_Place, StoredCall]:
    """The counted calls of one run by their place in it. `counted_calls` raises `StoreError` for a run with no counted
    attempt, and leaves out warm-ups, the unconstrained pass and the contended session-run by construction."""
    places: dict[_Place, StoredCall] = {}
    for stored in view.counted_calls(candidate, run):
        place = (stored.role, stored.session_index, stored.position)
        if place in places:
            raise DeterminismError(f"{candidate} run {run} holds {place} twice")
        places[place] = stored
    return places


def _raw(call: CallRecord) -> tuple[tuple[Any, ...], ...]:
    """H1 AMB-25: the raw text of every attempt and its failure kind. Wall times and usage are not output."""
    return tuple((attempt.content, attempt.reasoning_content, attempt.failure_kind) for attempt in call.attempts)


def _request_of(call: CallRecord) -> tuple[str, str]:
    return call.item_id, call.attempts[0].request_sha256


def _differing(candidate: str, run: int, first: Mapping[_Place, StoredCall], other: Mapping[_Place, StoredCall]) -> int:
    """How many pairs of `other` differ from run 1. A pair that is missing or not the same request is an error, never a
    silent skip, because either would make the share look more deterministic than it is."""
    unmatched = sorted(first.keys() ^ other.keys(), key=repr)
    if unmatched:
        raise DeterminismError(f"{candidate} run {run}: {unmatched[0]} has no counterpart")
    differing = 0
    for place, stored in first.items():
        counterpart = other[place].call
        if _request_of(stored.call) != _request_of(counterpart):
            raise DeterminismError(f"{candidate} run {run}: {place} is not the same request as in run 1")
        differing += _raw(stored.call) != _raw(counterpart)
    return differing


def determinism_shares(view: RunView) -> dict[str, DeterminismShare]:
    """S2 §3.13's share for every candidate with timed records, in S2 §1 order (H1 AMB-25, B9). A probe counts once per
    session, so `compared` is 2 x (N + 4 + 80) on a whole schedule (AMB-77). Voided runs do not count."""
    keys = [run.key for run in view.runs] + [stored.key for stored in view.calls]
    timed = {key.candidate for key in keys if key.phase == "timed_run"}
    shares: dict[str, DeterminismShare] = {}
    for candidate in CANDIDATE_IDS:
        if candidate in timed:
            first, *later = (_places(view, candidate, run) for run in RUNS)
            pairs = zip(RUNS[1:], later, strict=True)
            differing = sum(_differing(candidate, run, first, places) for run, places in pairs)
            shares[candidate] = DeterminismShare(candidate, differing, len(first) * len(later))
    return shares
