"""The machine sampler and checks around each hooked attempt (bake-off harness H4b).

This module reads the GPU, the model server's /proc status and the swap counter for gate M, writes what it read beside
the server log and loads it back into `evidence`'s types, which are all that `gates` and `report` read.

Implements, from the signed D6 criteria note S2:

- S2 §3.1, timing: clocks, temperature and power are logged during timing, and no CUDA process other than
  llama-server runs. `gpu_argv` logs all three with memory, as raw text, and `apps_argv` lists the compute processes.
- S2 §6, gate M: the rise in `memory.used`, the server's peak private memory (`RssAnon`), no swap, and the together
  session. `parse_gpu_log`, `parse_rss_anon` and `parse_pswpout` read them, and `check_sample_rate` applies the floor
  of 10 samples a second.
- S2 §3.13, runs: voided runs are listed in the report, so evidence is written for every hooked attempt, and
  `load_evidence` gives a voided attempt's evidence back when its logs can be read.
- S2 §9, report: logs hold no prompts or transcripts. The contention command's output is discarded and only its argv
  hash is kept.

`machine` alone imports `threading`, for the /proc thread of `_start_thread`. It starts processes only through
`launch.ServerProcess`, writes only its own logs through `LaunchTools.open_log`, and reaches every file and clock
through the injected tools: the one exception is `_list_pids`, which lists /proc.
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from .evidence import (
    MIN_SAMPLES_PER_SEC,
    AttemptEvidence,
    ContentionRecord,
    GpuSample,
    HostRecord,
    ProcSample,
    sample_rate,
)
from .launch import (
    CLEAN_PATH,
    LOG_POLL_SEC,
    Candidate,
    ConfigError,
    LaunchTools,
    RunConditions,
    ServerProcess,
    ServerStopFailed,
    SpawnFailed,
    _is_clean_absolute,
    _server_log,
    cmdline_bytes,
    frozen_argv,
    validate_candidates,
)
from .records import (
    MachineLogKind,
    RunKey,
    StoreError,
    _exact,
    _unit_name,
    machine_log_name,
    record_json,
    record_loads,
)
from .schemas import canonical_sha256

GPU_FIELDS = ("timestamp", "memory.used", "memory.free", "clocks.gr", "clocks.mem", "temperature.gpu", "power.draw")
APPS_FIELDS = ("pid", "process_name", "used_memory")
# the form captured by the operator on 2026-11-10 inside the namespace, command as in apps_argv (capture P0)
APPS_HEADER = "pid, process_name, used_gpu_memory [MiB]"
SAMPLE_INTERVAL_MS = 50  # target 20 Hz, twice the floor
SAMPLER_READY_SEC = 10.0
APPS_DEADLINE_SEC = 30.0
PROC_INTERVAL_SEC = 0.1
THREAD_JOIN_SEC = 5.0
# the form captured by the operator on 2026-11-10 inside the namespace, command as in gpu_argv (capture P0)
TIMESTAMP_PATTERN = re.compile(r"\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}\.\d{3}", re.ASCII)  # fullmatch only

_LOOP_FLAG = f"--loop-ms={SAMPLE_INTERVAL_MS}"
_FIELD_SEPARATOR = ", "
_MIB = re.compile(r"([0-9]+) MiB")  # fullmatch only
_RSS_ANON_LINE = re.compile(rb"RssAnon:[ \t]+([0-9]+) kB")  # fullmatch on one line
_PSWPOUT_LINE = re.compile(rb"pswpout ([0-9]+)")  # fullmatch on one line
_PROC_ROW = re.compile(r"([0-9]+),([0-9]+)")  # fullmatch on one proc.csv row
_VMSTAT = "/proc/vmstat"
_HOOKED = ("timed_run", "contended")
_HOST_SCHEMA = 1
_PROC_HEADER = "elapsed_ms,rss_anon_kib"
_HOST_KEYS = (
    "record",
    "schema",
    "key",
    "apps_lines",
    "pswpout_before",
    "pswpout_after",
    "elapsed_ms",
    "sampler_stopped_how",
    "server_pid_found",
    "problem",
    "contention",
)
_CONTENTION_KEYS = (
    "command_sha256",
    "lead_sec",
    "exit_code",
    "ended_before_stop",
    "ended_before_stop_ms",
    "stopped_how",
)


class MachineFault(RuntimeError):
    """A machine measurement failed. The runner records only the class name, so a message holds no path or argv."""


class CudaCheckFailed(MachineFault):
    """The compute-apps query did not start, did not exit 0 in time, could not be stopped, or its header differs."""


class OtherCudaProcess(MachineFault):
    """The compute-apps query listed a process before the server started (S2 §3.1)."""


class SamplerFailed(MachineFault):
    """The GPU sampler did not start, gave no complete first line in time, or could not be stopped."""


class SamplerDied(MachineFault):
    """The GPU sampler exited by itself before its stop."""


class UnparsableSample(MachineFault):
    """A complete GPU line whose form, timestamp or memory field does not parse."""


class SampleClockBackwards(MachineFault):
    """A GPU line is stamped earlier than the line before it."""


class SampleRateTooLow(MachineFault):
    """The GPU samples fall below the floor of S2 §6 (AMB-108)."""


class ServerNotFound(MachineFault):
    """The server's pid was never seen in /proc, or two processes matched its command line."""


class NoRssSample(MachineFault):
    """The server's pid was found but no `RssAnon` reading was taken."""


class ProcSamplerFailed(MachineFault):
    """The /proc thread was still running after its join, its step raised, or it could not be started."""


class ContentionFailed(MachineFault):
    """The together session's command did not start, exited non-zero during the lead, or could not be stopped."""


@dataclass(frozen=True, slots=True)
class MachineConfig:
    """The operator's machine settings. The contention argv is held in memory only; only its hash is ever written."""

    nvidia_smi_path: str
    contention_argv: tuple[str, ...]
    contention_env: Mapping[str, str]
    contention_lead_sec: float

    def __post_init__(self) -> None:
        path = self.nvidia_smi_path
        if not _is_clean_absolute(path) or path.split("/")[-1] != "nvidia-smi":
            raise ConfigError("MachineConfig.nvidia_smi_path must be a clean absolute path to nvidia-smi")
        argv = self.contention_argv
        if (
            not isinstance(argv, tuple)
            or not argv
            or not all(isinstance(part, str) and part for part in argv)
            or not _is_clean_absolute(argv[0])
        ):
            raise ConfigError("MachineConfig.contention_argv must be a non-empty tuple of str with an absolute argv[0]")
        env = self.contention_env
        if not isinstance(env, Mapping) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in env.items()
        ):
            raise ConfigError("MachineConfig.contention_env must map str to str")
        lead = self.contention_lead_sec
        if not isinstance(lead, int | float) or isinstance(lead, bool) or not _finite_seconds(lead):
            raise ConfigError("MachineConfig.contention_lead_sec must be a finite number of seconds, at least 0")
        object.__setattr__(self, "contention_env", MappingProxyType(dict(env)))


def _finite_seconds(value: float) -> bool:
    """A finite number at least 0. An int too large for a float is not finite, so it never raises OverflowError."""
    try:
        number = float(value)
    except OverflowError:
        return False
    return math.isfinite(number) and number >= 0


def _list_pids() -> tuple[int, ...]:
    """The pids /proc lists, in order. The only directory listing in this module."""
    return tuple(sorted(int(name) for name in os.listdir("/proc") if name.isascii() and name.isdigit()))


def _start_thread(step: Callable[[], bool], interval: float) -> Callable[[float], str | None]:
    """Run `step` every `interval` seconds on a daemon thread until it returns False or the stopper is called.

    The stopper sets the event and joins with its timeout. It returns "still running" if the thread is alive, the
    class name of the exception `step` raised, or None. Nothing reaches `threading.excepthook`."""
    event = threading.Event()
    failures: list[str] = []

    def run() -> None:
        try:
            while not event.is_set() and step():
                event.wait(interval)
        except Exception as exc:
            failures.append(type(exc).__name__)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    def stop(timeout: float) -> str | None:
        event.set()
        thread.join(timeout)
        if thread.is_alive():
            return "still running"
        return failures[0] if failures else None

    return stop


@dataclass(frozen=True, slots=True)
class MachineTools:
    """Every process, file, clock and thread this module touches; a test replaces each."""

    launch: LaunchTools
    list_pids: Callable[[], tuple[int, ...]] = _list_pids
    start_thread: Callable[[Callable[[], bool], float], Callable[[float], str | None]] = _start_thread


def gpu_argv(config: MachineConfig) -> tuple[str, ...]:
    """The GPU sampler: one line per sample, no header, every `SAMPLE_INTERVAL_MS`."""
    return (config.nvidia_smi_path, "--query-gpu=" + ",".join(GPU_FIELDS), "--format=csv,noheader", _LOOP_FLAG)


def apps_argv(config: MachineConfig) -> tuple[str, ...]:
    """The one-shot compute-apps query: a header, then one line per CUDA process."""
    return (config.nvidia_smi_path, "--query-compute-apps=" + ",".join(APPS_FIELDS), "--format=csv")


def _stamp_ms(text: str) -> int:
    """An nvidia-smi timestamp in whole ms on a day-ordinal scale. The stamp is local time with no zone, so only
    differences between stamps mean anything."""
    if not isinstance(text, str) or TIMESTAMP_PATTERN.fullmatch(text) is None:
        raise UnparsableSample("a timestamp is not in the captured form")
    year, month, day = int(text[0:4]), int(text[5:7]), int(text[8:10])
    hour, minute, second, milli = int(text[11:13]), int(text[14:16]), int(text[17:19]), int(text[20:23])
    try:
        datetime(year, month, day, hour, minute, second)
    except ValueError:
        raise UnparsableSample("a timestamp is not a valid date and time") from None
    return ((date(year, month, day).toordinal() * 24 + hour) * 60 + minute) * 60_000 + second * 1000 + milli


def _raw_field(text: str) -> bool:
    """A non-memory field: non-empty printable ASCII with no comma and no surrounding space; `[N/A]` passes."""
    return (
        text != ""
        and text == text.strip(" ")
        and all(" " <= char <= "~" for char in text)
        and "," not in text
        and text.isascii()
    )


def _gpu_fields(line: str) -> tuple[int, int, int, tuple[str, str, str, str]]:
    """The absolute stamp in ms, `memory.used`, `memory.free` and the four raw fields of one line."""
    if not isinstance(line, str) or not line.isascii():
        raise UnparsableSample("a GPU line is not ASCII")
    fields = line.split(_FIELD_SEPARATOR)
    if len(fields) != len(GPU_FIELDS):
        raise UnparsableSample(f"a GPU line has {len(fields)} fields, not {len(GPU_FIELDS)}")
    stamp = _stamp_ms(fields[0])
    used, free = _MIB.fullmatch(fields[1]), _MIB.fullmatch(fields[2])
    if used is None or free is None:
        raise UnparsableSample("a memory field is not a whole number of MiB")
    clocks_gr, clocks_mem, temperature, power = fields[3:]
    if not all(_raw_field(text) for text in (clocks_gr, clocks_mem, temperature, power)):
        raise UnparsableSample("a clock, temperature or power field is empty or not printable")
    try:
        used_mib, free_mib = int(used.group(1)), int(free.group(1))
    except ValueError:  # more digits than the interpreter converts
        raise UnparsableSample("a memory field is out of range") from None
    return stamp, used_mib, free_mib, (clocks_gr, clocks_mem, temperature, power)


def parse_gpu_line(line: str, first_ms: int | None) -> GpuSample:
    """One complete GPU line. `t_ms` is relative to `first_ms`, the first line's stamp, or 0 when it is None."""
    stamp, used, free, raw = _gpu_fields(line)
    return GpuSample(0 if first_ms is None else stamp - first_ms, used, free, *raw)


def parse_gpu_log(raw: bytes) -> tuple[GpuSample, ...]:
    """Every LF-terminated line of a gpu.csv; the bytes after the last LF are a cut tail and are ignored.

    Every line is parsed before the order is checked, so `UnparsableSample` wins over `SampleClockBackwards`. Equal
    stamps are accepted."""
    parsed = []
    for line in raw.split(b"\n")[:-1]:
        if not line or not line.isascii() or b"\r" in line:
            raise UnparsableSample("a GPU line is empty, not ASCII or holds a CR")
        parsed.append(_gpu_fields(line.decode("ascii")))
    for previous, current in zip(parsed, parsed[1:], strict=False):
        if current[0] < previous[0]:
            raise SampleClockBackwards("a GPU line is stamped before the line above it")
    if not parsed:
        return ()
    first = parsed[0][0]
    return tuple(GpuSample(stamp - first, used, free, *fields) for stamp, used, free, fields in parsed)


def check_sample_rate(samples: tuple[GpuSample, ...], elapsed_ms: int) -> None:
    """S2 §6's floor of `MIN_SAMPLES_PER_SEC`: in every whole second of the samples, and in total against the harness
    time (AMB-108)."""
    rate = sample_rate(samples, elapsed_ms)
    if rate.min_per_whole_second is not None and rate.min_per_whole_second < MIN_SAMPLES_PER_SEC:
        raise SampleRateTooLow(f"{rate.min_per_whole_second} samples in a whole second")
    if rate.samples < MIN_SAMPLES_PER_SEC * (elapsed_ms // 1000):
        raise SampleRateTooLow(f"{rate.samples} samples in {elapsed_ms} ms")


# NEEDS-OWNER(AMB-108): the 10 Hz floor on the server sampler is the harness plan's reading; S2 sets it for audio only
def _check_server_rate(samples: tuple[GpuSample, ...], elapsed_ms: int) -> None:
    check_sample_rate(samples, elapsed_ms)


def apps_process_lines(raw: bytes) -> int:
    """The number of process lines after the captured header. No column is read."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise CudaCheckFailed("the compute-apps output is not ASCII") from None
    if not text.startswith(APPS_HEADER + "\n"):
        raise CudaCheckFailed("the compute-apps output does not start with the captured header")
    return sum(1 for line in text[len(APPS_HEADER) + 1 :].split("\n") if line)


def parse_rss_anon(status: bytes) -> int | None:
    """The `RssAnon` kB of a /proc status, or None when it has no such line, as for an exited process."""
    lines = [line for line in status.split(b"\n") if line.startswith(b"RssAnon:")]
    if not lines:
        return None
    match = _RSS_ANON_LINE.fullmatch(lines[0])
    if len(lines) > 1 or match is None:
        raise MachineFault("the RssAnon line is repeated or malformed")
    try:
        return int(match.group(1))
    except ValueError:  # more digits than the interpreter converts
        raise MachineFault("the RssAnon value is out of range") from None


def parse_pswpout(vmstat: bytes) -> int:
    """The swap-out counter of /proc/vmstat, from its one `pswpout` line."""
    lines = [line for line in vmstat.split(b"\n") if line.split(b" ")[0] == b"pswpout"]
    match = _PSWPOUT_LINE.fullmatch(lines[0]) if len(lines) == 1 else None
    if match is None:
        raise MachineFault("vmstat has no single well-formed pswpout line")
    try:
        return int(match.group(1))
    except ValueError:  # more digits than the interpreter converts
        raise MachineFault("the pswpout value is out of range") from None


def _proc_csv(samples: tuple[ProcSample, ...]) -> bytes:
    """The proc.csv of an attempt: a header, then one `elapsed_ms,rss_anon_kib` row per reading."""
    rows = "".join(f"{sample.elapsed_ms},{sample.rss_anon_kib}\n" for sample in samples)
    return (_PROC_HEADER + "\n" + rows).encode("ascii")


def _host_json(record: HostRecord) -> bytes:
    """The host.json of an attempt: one record line, from plain dicts only."""
    contention = record.contention
    body: dict[str, Any] = {
        "record": "host",
        "schema": _HOST_SCHEMA,
        "key": _unit_name(record.key),
        "apps_lines": record.apps_lines,
        "pswpout_before": record.pswpout_before,
        "pswpout_after": record.pswpout_after,
        "elapsed_ms": record.elapsed_ms,
        "sampler_stopped_how": record.sampler_stopped_how,
        "server_pid_found": record.server_pid_found,
        "problem": record.problem,
        "contention": None
        if contention is None
        else {
            "command_sha256": contention.command_sha256,
            "lead_sec": contention.lead_sec,
            "exit_code": contention.exit_code,
            "ended_before_stop": contention.ended_before_stop,
            "ended_before_stop_ms": contention.ended_before_stop_ms,
            "stopped_how": contention.stopped_how,
        },
    }
    return record_json(body) + b"\n"


def _stop_quietly(process: ServerProcess) -> None:
    """Stop a process on a way out that already carries its own error, which a stop failure must not mask."""
    try:
        process.stop()
    except ServerStopFailed:
        pass


class _Attempt:
    """What the hooks hold for the one attempt between its before-hook and its after-hook. The /proc step is bound to
    its own attempt, so a step left over from an earlier attempt never writes into a later one."""

    __slots__ = (
        "key",
        "target",
        "apps_lines",
        "pswpout_before",
        "sampler",
        "t0",
        "stopper",
        "samples",
        "pid",
        "two_matches",
        "contention",
        "contention_exit_seen",
        "contention_record",
    )

    def __init__(
        self, key: RunKey, target: bytes, apps_lines: int, pswpout_before: int, sampler: ServerProcess, t0: float
    ) -> None:
        self.key = key
        self.target = target
        self.apps_lines = apps_lines
        self.pswpout_before = pswpout_before
        self.sampler = sampler
        self.t0 = t0
        self.stopper: Callable[[float], str | None] | None = None
        self.samples: list[ProcSample] = []
        self.pid: int | None = None
        self.two_matches = False
        self.contention: ServerProcess | None = None
        self.contention_exit_seen: float | None = None
        self.contention_record: ContentionRecord | None = None


class MachineHooks:
    """The runner's hooks with the real machine behind them (S2 §3.1, §6 gate M, §3.13, §9).

    Before each timed or contended attempt they check that no other CUDA process runs, read the swap counter, start the
    GPU sampler and wait for its first line, then start the /proc thread. After it they stop all of that, write
    proc.csv and host.json and raise the first problem found, which voids the attempt (AMB-108, AMB-109 under AMB-69).
    Each failure stops what the hook started. The contention hooks run the together session's command with its output
    discarded, and keep only its argv hash (AMB-125)."""

    def __init__(
        self,
        config: MachineConfig,
        run_dir: str,
        conditions: RunConditions,
        candidates: tuple[Candidate, ...],
        tools: MachineTools,
        fsync: Callable[[int], None],
    ) -> None:
        validate_candidates(candidates)
        self._config = config
        self._run_dir = run_dir
        self._targets = MappingProxyType(
            {candidate.candidate_id: cmdline_bytes(frozen_argv(conditions, candidate)) for candidate in candidates}
        )
        self._tools = tools
        self._fsync = fsync
        self._live: _Attempt | None = None

    def _path(self, key: RunKey, kind: MachineLogKind) -> str:
        return f"{self._run_dir}/{machine_log_name(key, kind)}"

    def before_timed_run(self, key: RunKey) -> None:
        if key.phase not in _HOOKED or self._live is not None:
            raise RuntimeError("before_timed_run is for a timed or contended attempt, once per attempt")
        apps_lines = self._check_apps(key)
        try:
            pswpout_before = parse_pswpout(self._tools.launch.read_bytes(_VMSTAT))
        except OSError:
            raise MachineFault("vmstat could not be read before the attempt") from None
        sampler: ServerProcess | None = None
        try:
            try:
                sampler = ServerProcess(
                    gpu_argv(self._config), {"PATH": CLEAN_PATH}, self._path(key, "gpu"), self._tools.launch
                )
            except SpawnFailed:
                raise SamplerFailed("the GPU sampler did not start") from None
            t0 = self._first_line(sampler)
            attempt = _Attempt(key, self._targets[key.candidate], apps_lines, pswpout_before, sampler, t0)
            try:
                stopper = self._tools.start_thread(lambda: self._step(attempt), PROC_INTERVAL_SEC)
            except Exception:
                raise ProcSamplerFailed("the /proc thread did not start") from None
            attempt.stopper = stopper
            self._live = attempt
        except BaseException:
            if sampler is not None:
                _stop_quietly(sampler)
            raise

    def _check_apps(self, key: RunKey) -> int:
        """The compute-apps query, stopped on every way out. Any process line raises: the server is not started yet."""
        try:
            apps = ServerProcess(
                apps_argv(self._config), {"PATH": CLEAN_PATH}, self._path(key, "apps"), self._tools.launch
            )
        except SpawnFailed:
            raise CudaCheckFailed("the compute-apps query did not start") from None
        try:
            code = apps.exit_within(APPS_DEADLINE_SEC)
        except BaseException:
            _stop_quietly(apps)
            raise
        try:
            apps.stop()
        except ServerStopFailed:
            raise CudaCheckFailed("the compute-apps query could not be stopped") from None
        if code != 0:
            raise CudaCheckFailed("the compute-apps query did not exit 0 in time")
        lines = apps_process_lines(_server_log(apps, self._tools.launch))
        if lines > 0:
            raise OtherCudaProcess(f"{lines} process lines")
        return lines

    def _first_line(self, sampler: ServerProcess) -> float:
        """The monotonic time at which the sampler's log first holds a complete line."""
        started = self._tools.launch.monotonic()
        while True:
            if b"\n" in _server_log(sampler, self._tools.launch):
                return self._tools.launch.monotonic()
            if sampler.poll() is not None:
                raise SamplerFailed("the GPU sampler exited before its first line")
            if self._tools.launch.monotonic() - started >= SAMPLER_READY_SEC:
                raise SamplerFailed("the GPU sampler gave no complete line in time")
            self._tools.launch.sleep(LOG_POLL_SEC)

    def _step(self, live: _Attempt) -> bool:
        """One pass of the /proc thread over its own attempt. It never reads the stop flag. It first notes when the
        contention command is seen to have exited; then it finds the server by its command line, once, and reads its
        `RssAnon`. False ends the thread: two matches, or the server gone."""
        process = live.contention
        if process is not None and live.contention_exit_seen is None and process.poll() is not None:
            live.contention_exit_seen = self._tools.launch.monotonic()
        if live.pid is None:
            found = []
            for pid in self._tools.list_pids():
                try:
                    cmdline = self._tools.launch.read_bytes(f"/proc/{pid}/cmdline")
                except (FileNotFoundError, ProcessLookupError):
                    continue
                if cmdline == live.target:
                    found.append(pid)
            if len(found) > 1:
                live.two_matches = True
                return False
            if not found:
                return True
            live.pid = found[0]
        try:
            status = self._tools.launch.read_bytes(f"/proc/{live.pid}/status")
        except (FileNotFoundError, ProcessLookupError):
            return False
        kib = parse_rss_anon(status)
        if kib is None:
            return False
        live.samples.append(ProcSample(int((self._tools.launch.monotonic() - live.t0) * 1000), kib))
        return True

    def after_timed_run(self, key: RunKey) -> None:
        live = self._live
        if live is None or live.key != key:
            raise RuntimeError("after_timed_run needs the before_timed_run of the same attempt")
        try:
            self._finish(live)
        finally:
            self._live = None
            try:
                if live.contention is not None:
                    _stop_quietly(live.contention)
            finally:
                _stop_quietly(live.sampler)

    def _finish(self, live: _Attempt) -> None:
        """Stop the thread and the sampler, write proc.csv and host.json, then raise the first problem."""
        stopper = live.stopper
        reason = None if stopper is None else stopper(THREAD_JOIN_SEC)
        proc = tuple(live.samples)
        if live.contention is not None:
            _stop_quietly(live.contention)
            live.contention = None
        t_end = self._tools.launch.monotonic()
        try:
            stopped_how: str = live.sampler.stop().how
        except ServerStopFailed:
            stopped_how = "stop_failed"
        elapsed_ms = int((t_end - live.t0) * 1000)
        try:
            pswpout_after = parse_pswpout(self._tools.launch.read_bytes(_VMSTAT))
        except (OSError, MachineFault):
            self._write(live.key, "proc", _proc_csv(proc))
            raise MachineFault("vmstat could not be read after the attempt") from None
        gpu_problem: MachineFault | None = None
        try:
            gpu = parse_gpu_log(_server_log(live.sampler, self._tools.launch))
        except (UnparsableSample, SampleClockBackwards) as exc:
            gpu, gpu_problem = (), exc
        problem = self._problem(live, reason, stopped_how, gpu_problem, gpu, elapsed_ms, proc)
        found = live.pid is not None and not live.two_matches
        contention = live.contention_record if live.key.phase == "contended" else None
        named = None if problem is None else type(problem).__name__
        if isinstance(problem, ProcSamplerFailed):
            named = f"ProcSamplerFailed: {reason}"
        record = HostRecord(
            live.key,
            live.apps_lines,
            live.pswpout_before,
            pswpout_after,
            elapsed_ms,
            stopped_how,
            found,
            named,
            contention,
        )
        self._write(live.key, "proc", _proc_csv(proc))
        self._write(live.key, "host", _host_json(record))
        if problem is not None:
            raise problem

    def _problem(
        self,
        live: _Attempt,
        reason: str | None,
        stopped_how: str,
        gpu_problem: MachineFault | None,
        gpu: tuple[GpuSample, ...],
        elapsed_ms: int,
        proc: tuple[ProcSample, ...],
    ) -> MachineFault | None:
        """The first problem of an attempt, in a fixed order."""
        if reason is not None:
            return ProcSamplerFailed("the /proc thread did not end cleanly")
        if stopped_how == "stop_failed":
            return SamplerFailed("the GPU sampler could not be stopped")
        if stopped_how == "already_exited":
            return SamplerDied("the GPU sampler exited before its stop")
        if gpu_problem is not None:
            return gpu_problem
        try:
            _check_server_rate(gpu, elapsed_ms)
        except SampleRateTooLow as exc:
            return exc
        if live.pid is None or live.two_matches:
            return ServerNotFound("the server's process was not found exactly once")
        if not proc:
            return NoRssSample("the server's process was found but never read")
        return None

    def _write(self, key: RunKey, kind: MachineLogKind, data: bytes) -> None:
        """One new log beside the server log: written, flushed and synced, and closed on every way out."""
        file = self._tools.launch.open_log(self._path(key, kind))
        try:
            file.write(data)
            file.flush()
            self._fsync(file.fileno())
        finally:
            file.close()

    def _contended(self, key: RunKey) -> _Attempt:
        live = self._live
        if live is None or live.key != key or key.phase != "contended":
            raise RuntimeError("the contention hooks run inside the contended attempt's own hooks")
        return live

    def start_contention(self, key: RunKey) -> None:
        live = self._contended(key)
        if live.contention is not None or live.contention_record is not None:
            raise RuntimeError("start_contention runs once per contended attempt")
        config = self._config
        try:
            process = ServerProcess(config.contention_argv, config.contention_env, None, self._tools.launch)
        except SpawnFailed:
            raise ContentionFailed("the together session's command did not start") from None
        try:
            self._tools.launch.sleep(config.contention_lead_sec)
            code = process.poll()
            if code is not None and code != 0:
                raise ContentionFailed("the together session's command exited non-zero during its lead")
            if code == 0:
                live.contention_exit_seen = self._tools.launch.monotonic()
            live.contention = process
        except BaseException:
            _stop_quietly(process)
            raise

    def stop_contention(self, key: RunKey) -> None:
        live = self._contended(key)
        process = live.contention
        if process is None:
            raise RuntimeError("stop_contention needs a running start_contention")
        config = self._config
        command_sha256 = canonical_sha256(list(config.contention_argv))
        lead_sec = float(config.contention_lead_sec)
        now = self._tools.launch.monotonic()
        try:
            result = process.stop()
        except ServerStopFailed:
            live.contention = None
            live.contention_record = ContentionRecord(command_sha256, lead_sec, None, False, None, None)
            raise ContentionFailed("the together session's command could not be stopped") from None
        live.contention = None
        seen = live.contention_exit_seen
        ended = result.how == "already_exited"
        ended_ms = int((now - seen) * 1000) if ended and seen is not None and seen <= now else None
        live.contention_record = ContentionRecord(
            command_sha256, lead_sec, result.exit_code, ended, ended_ms, None if ended else result.how
        )


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_optional(value: object, check: Callable[[object], bool]) -> bool:
    return value is None or check(value)


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _contention(value: object, unit: str) -> ContentionRecord | None:
    if value is None:
        return None
    data = _exact(value, _CONTENTION_KEYS, f"{unit} host.json contention")
    lead = data["lead_sec"]
    if not (
        _is_str(data["command_sha256"])
        and (_is_int(lead) or isinstance(lead, float))
        and _is_optional(data["exit_code"], _is_int)
        and isinstance(data["ended_before_stop"], bool)
        and _is_optional(data["ended_before_stop_ms"], _is_int)
        and _is_optional(data["stopped_how"], _is_str)
    ):
        raise StoreError(f"{unit} host.json contention: a field has the wrong type")
    try:
        lead_value = float(lead)
    except OverflowError:
        raise StoreError(f"{unit} host.json contention: lead_sec is out of range") from None
    if not math.isfinite(lead_value):
        raise StoreError(f"{unit} host.json contention: lead_sec is out of range")
    return ContentionRecord(
        data["command_sha256"],
        lead_value,
        data["exit_code"],
        data["ended_before_stop"],
        data["ended_before_stop_ms"],
        data["stopped_how"],
    )


def _host_record(raw: bytes, key: RunKey) -> HostRecord:
    """The strict reading of a host.json; any defect is a `StoreError` naming the unit."""
    unit = _unit_name(key)
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise StoreError(f"{unit} host.json is not one LF-terminated line")
    try:
        loaded = record_loads(raw[:-1])
    except StoreError:
        raise StoreError(f"{unit} host.json is not a stored record") from None
    data = _exact(loaded, _HOST_KEYS, f"{unit} host.json")
    if data["record"] != "host" or not _is_int(data["schema"]) or data["schema"] != _HOST_SCHEMA:
        raise StoreError(f"{unit} host.json is not a host record of schema {_HOST_SCHEMA}")
    if data["key"] != unit:
        raise StoreError(f"{unit} host.json belongs to another attempt")
    counts = ("apps_lines", "pswpout_before", "pswpout_after", "elapsed_ms")
    if not (
        all(_is_int(data[name]) for name in counts)
        and _is_str(data["sampler_stopped_how"])
        and isinstance(data["server_pid_found"], bool)
        and _is_optional(data["problem"], _is_str)
    ):
        raise StoreError(f"{unit} host.json: a field has the wrong type")
    return HostRecord(
        key,
        data["apps_lines"],
        data["pswpout_before"],
        data["pswpout_after"],
        data["elapsed_ms"],
        data["sampler_stopped_how"],
        data["server_pid_found"],
        data["problem"],
        _contention(data["contention"], unit),
    )


def _proc_samples(raw: bytes) -> tuple[ProcSample, ...] | None:
    """The readings of a proc.csv, or None when it is malformed."""
    if not raw.isascii():
        return None
    lines = raw.decode("ascii").split("\n")
    if lines[0] != _PROC_HEADER or lines[-1] != "":
        return None
    samples = []
    for line in lines[1:-1]:
        match = _PROC_ROW.fullmatch(line)
        if match is None:
            return None
        try:
            samples.append(ProcSample(int(match.group(1)), int(match.group(2))))
        except ValueError:  # more digits than the interpreter converts
            return None
    return tuple(samples)


def load_evidence(run_dir: str, key: RunKey, read_bytes: Callable[[str], bytes]) -> AttemptEvidence | None:
    """The evidence of one hooked attempt, as `evidence.EvidenceLoader`.

    None when the attempt left no host.json; then no other log of it is read. The host.json is always read strictly.
    A missing proc.csv or gpu.csv beside it is a `StoreError`. For an attempt with no problem (a counted one), a
    malformed log or a gpu.csv with no sample is a `StoreError` too. For a voided attempt (AMB-123), the evidence is
    given when both logs parse and hold a GPU sample, and None otherwise. Every refusal names the unit, never a path.
    """
    unit = _unit_name(key)
    try:
        host_raw = read_bytes(f"{run_dir}/{machine_log_name(key, 'host')}")
    except FileNotFoundError:
        return None
    host = _host_record(host_raw, key)
    try:
        proc_raw = read_bytes(f"{run_dir}/{machine_log_name(key, 'proc')}")
        gpu_raw = read_bytes(f"{run_dir}/{machine_log_name(key, 'gpu')}")
    except FileNotFoundError:
        raise StoreError(f"{unit}: a proc.csv or gpu.csv is missing beside its host.json") from None
    proc = _proc_samples(proc_raw)
    try:
        gpu: tuple[GpuSample, ...] | None = parse_gpu_log(gpu_raw)
    except MachineFault:
        gpu = None
    if proc is None or not gpu:
        if host.problem is not None:
            return None
        raise StoreError(f"{unit}: the proc.csv is malformed, or the gpu.csv does not parse or holds no sample")
    try:
        return AttemptEvidence(key, gpu, proc, host)
    except ValueError as exc:
        raise StoreError(f"{unit}: {exc}") from None
