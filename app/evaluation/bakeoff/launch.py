"""The launch of the model server and its start-up checks (bake-off harness H3).

This module starts the pinned `llama-server` on the frozen line, waits for it and proves that what runs is what the
note froze. It holds no prompt text and judges no candidate: a failed check raises, and the runner says what follows.

Implements, from the signed D6 criteria note S2:

- S2 §1, candidates: "The field is closed." The three candidates are fixed here, in that order, each with a GGUF path
  and its expected SHA-256, and no other model can be configured.
- S2 §3.2, environment: "GGUF files are read from the WSL ext4 filesystem, not from `/mnt/c`", checked after resolving
  the path (AMB-62). The server and harness share a namespace with only the loopback interface, which `/proc/net/dev`
  shows; the namespace's other checks stay operator steps. Inside the namespace `/sys/class/net` still lists the
  host's devices, so it is never read.
- S2 §3.3, launch line: `frozen_argv` is the frozen line, and `/proc/<pid>/cmdline` must equal it byte for byte. Any
  `LLAMA_ARG_*` variable could change a setting the command line does not show, so the server gets a clean environment
  built from nothing, and a log saying it read a config file is refused (AMB-61). The log gives layers offloaded,
  context per slot, graph splits and the CUDA model, KV, recurrent and compute buffers. Graph splits above 2 are
  recorded, never a failure.
- S2 §3.5, context: the context and KV type are run conditions, one set for every launch, so raising the context is
  configuration and nothing else here holds its default.
- S2 §11 item 5: "It is started, and it answers one non-gold request", and "The section 3.3 start-up log checks are
  recorded". `launch` does the start and the checks; the runner sends the request and stores the result.
- Annex A: the pre-run record's start-up log items, the launch line, and the network-namespace check result.

The pinned build logs layers, buffers and graph splits only at verbosity 4, under which the first calls on the 4B models
took 2 to 3 times as long. So the pre-run launch sets `LLAMA_ARG_LOG_VERBOSITY=4` in its environment, timed launches do
not, and the launch line is the frozen one in both. Each log's own verbosity line proves which one reached the server.

The harness runs on Linux and its tests run on Windows, so a Linux path follows `PurePosixPath` rules on any OS, and
every process, file and clock is reached through `LaunchTools`, which a test replaces.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, BinaryIO, Literal

from .client import (
    CONTEXT_TOKENS,
    THINK_TAGS,
    ModelServerClient,
    ServerCheckError,
    ServerProps,
    _is_count,
    _is_number,
    _is_sha256,
)

LaunchMode = Literal["pre_run", "timed"]
CANDIDATE_IDS = ("Qwen/Qwen3-4B", "Qwen/Qwen3.5-4B", "Qwen/Qwen3-8B")
CANDIDATE_SLUGS = MappingProxyType(
    {"Qwen/Qwen3-4B": "qwen3-4b", "Qwen/Qwen3.5-4B": "qwen3.5-4b", "Qwen/Qwen3-8B": "qwen3-8b"}
)
BINARY_NAME = "llama-server"
PINNED_BUILD_INFO = "b1-27b7a8a"  # as the pinned build reports it, in `/props` and every system_fingerprint
HOST = "127.0.0.1"
DEFAULT_KV_TYPE = "f16"
KV_TYPES = ("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1")
CONTEXT_MULTIPLE = 256  # the server pads the context up to this, so any other value could never equal `<ctx>`
CLEAN_PATH = "/usr/bin:/bin"
VERBOSITY_ENV = "LLAMA_ARG_LOG_VERBOSITY"
VERBOSE_LEVEL = 4
DEFAULT_LEVEL = 3
READY_DEADLINE_SEC = 600.0  # four times the slowest measured load, 149.7 s
READY_POLL_SEC = 1.0
LOG_SETTLE_SEC = 10.0
LOG_POLL_SEC = 0.1
STOP_GRACE_SEC = 30.0
EXIT_SETTLE_SEC = 5.0
HASH_CHUNK_BYTES = 1 << 20
BIND_FAILED_TEXT = "couldn't bind HTTP server socket"

_LAUNCH_MODES = ("pre_run", "timed")
_PROC = PurePosixPath("/proc")
_NET_DEV = str(_PROC / "net" / "dev")
_CODER_TAGS = ("<tool_call>", "<function=", "<parameter=")
_CODER_EXCLUDED = "'<tool_call><function=' ~ tool_call.name ~ '>'"

# The start-up log lines, each matched against one whole line. `[0-9]` matters: `\d` accepts non-ASCII digits. Every
# line used is info level, and a multi-line entry leaves unprefixed lines that match nothing.
_PREFIX = r"[0-9]+[.][0-9]{2}[.][0-9]{3}[.][0-9]{3} I "
_MIB = r" +([0-9]+[.][0-9]{2}) MiB"
_PAIR = r"[0-9]+(?: / [0-9]+)?"
_VERBOSITY = r"cmn +common_param: common_params_print_info: verbosity = ([0-9]+) [(]adjust with the `-lv N` CLI arg[)]"
_SLOTS = r"srv +load_model: initializing, n_slots = ([0-9]+), n_ctx_slot = ([0-9]+), kv_unified = '(?:true|false)'"
_GRAPH = (
    r"sched_reserve: graph(?: [(]pp bs=[0-9]+, tg bs=[0-9]+[)])?: "
    rf"nodes = {_PAIR}, splits = ([0-9]+)(?: / ([0-9]+))?, input objects = {_PAIR}, input tensors = {_PAIR}"
)
_LISTENING = re.compile(_PREFIX + r"srv +server_on_li: listening on http://127[.]0[.]0[.]1:([0-9]+)")
_LOG_LINES = (
    ("CONFIG", re.compile(_PREFIX + r"using config file: (.*)")),
    ("VERBOSITY", re.compile(_PREFIX + _VERBOSITY)),
    ("SLOTS", re.compile(_PREFIX + _SLOTS)),
    ("LISTENING", _LISTENING),
    ("OFFLOADED", re.compile(_PREFIX + r"load_tensors: offloaded ([0-9]+)/([0-9]+) layers to GPU")),
    ("MODEL", re.compile(_PREFIX + r"load_tensors: +CUDA[0-9]+ model buffer size =" + _MIB)),
    ("KV", re.compile(_PREFIX + r"llama_kv_cache: +CUDA[0-9]+ KV buffer size =" + _MIB)),
    ("RECURRENT", re.compile(_PREFIX + r"llama_memory_recurrent: +CUDA[0-9]+ RS buffer size =" + _MIB)),
    ("COMPUTE", re.compile(_PREFIX + r"sched_reserve: +CUDA[0-9]+ compute buffer size =" + _MIB)),
    ("GRAPH", re.compile(_PREFIX + _GRAPH)),
)


class ConfigError(ValueError):
    """A candidate or run condition that the note does not allow (S2 §1, §3.3, §3.5)."""


class ModelFileError(RuntimeError):
    """A candidate's model file failed its pre-run check, so it is not launched (S2 §1, §3.2)."""

    def __init__(self, check: Literal["model_location", "model_hash"]) -> None:
        super().__init__(check)
        self.check = check


class LaunchError(RuntimeError):
    """A launch failed. This is a run condition, never a model failure, and `str(exc)` is the fault's detail."""


class SpawnFailed(LaunchError):
    """`popen` raised, so no process exists."""

    def __init__(self, error_class: str) -> None:
        super().__init__(error_class)
        self.error_class = error_class

    def __str__(self) -> str:
        return f"spawn failed: {self.error_class}"


class ServerExited(LaunchError):
    """The process ended during the launch. `bind_failed` says its log holds the bind line, so the port failed before
    any model load (AMB-86)."""

    def __init__(self, exit_code: int, bind_failed: bool) -> None:
        super().__init__(exit_code, bind_failed)
        self.exit_code = exit_code
        self.bind_failed = bind_failed

    def __str__(self) -> str:
        return f"server exited with code {self.exit_code}"


class ReadyTimeout(LaunchError):
    """The process lives but did not answer in time. That is not "does not load" (AMB-60)."""

    def __init__(self, deadline_sec: float) -> None:
        super().__init__(deadline_sec)
        self.deadline_sec = deadline_sec

    def __str__(self) -> str:
        return f"not ready after {self.deadline_sec:g} s"


class LaunchCheckFailed(LaunchError):
    """A start-up check failed. `check` names it, and `detail` holds names and numbers only, never log or server text,
    so that it can go into a fault record (S2 §9)."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(check, detail)
        self.check = check
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.check}: {self.detail}"


class ServerStopFailed(LaunchError):
    """Both the terminate and the kill timed out."""

    def __str__(self) -> str:
        return "still running after kill"


def _is_clean_absolute(path: object) -> bool:
    """An absolute Linux path with no `.` or `..` part, read from the text because `PurePosixPath` drops a `.`."""
    return isinstance(path, str) and PurePosixPath(path).is_absolute() and not {".", ".."} & set(path.split("/"))


def _is_filled(value: object) -> bool:
    return isinstance(value, str) and value.strip() != ""


@dataclass(frozen=True, slots=True)
class Candidate:
    """One candidate of S2 §1, as Annex A records it. `gguf_path` is the Linux path given to `-m`, unresolved."""

    candidate_id: str
    gguf_path: str
    gguf_sha256: str
    source_revision: str
    quantization_commands: str

    def __post_init__(self) -> None:
        if self.candidate_id not in CANDIDATE_IDS:
            raise ConfigError(f"Candidate.candidate_id must be one of {CANDIDATE_IDS}, not {self.candidate_id!r}")
        if not _is_clean_absolute(self.gguf_path):
            raise ConfigError(f"Candidate.gguf_path must be absolute with no . or .. part, not {self.gguf_path!r}")
        if not _is_sha256(self.gguf_sha256):
            raise ConfigError("Candidate.gguf_sha256 must be 64 lowercase hex characters")
        for name in ("source_revision", "quantization_commands"):
            if not _is_filled(getattr(self, name)):
                raise ConfigError(f"Candidate.{name} must contain a non-whitespace character")


def validate_candidates(candidates: tuple[Candidate, ...]) -> tuple[Candidate, ...]:
    """`candidates` unchanged, if they are exactly S2 §1's three, in that order, on three files."""
    if not isinstance(candidates, tuple) or not all(isinstance(candidate, Candidate) for candidate in candidates):
        raise ConfigError("candidates must be a tuple of Candidate values")
    if tuple(candidate.candidate_id for candidate in candidates) != CANDIDATE_IDS:
        raise ConfigError(f"candidates must be exactly {CANDIDATE_IDS}, in that order")
    if len({candidate.gguf_path for candidate in candidates}) != len(CANDIDATE_IDS):
        raise ConfigError("every candidate needs its own GGUF path")
    if len({candidate.gguf_sha256 for candidate in candidates}) != len(CANDIDATE_IDS):
        raise ConfigError("every candidate needs its own GGUF hash")
    return candidates


@dataclass(frozen=True, slots=True)
class RunConditions:
    """The one set of conditions behind every launch (S2 §3.3, §3.5). `home` is the server's `HOME`. `context` must be
    a multiple of 256, because the server pads it and "equal to `<ctx>`" could otherwise never hold."""

    binary_path: str
    home: str
    port: int
    context: int = CONTEXT_TOKENS
    kv_type: str = DEFAULT_KV_TYPE
    ready_deadline_sec: float = READY_DEADLINE_SEC

    def __post_init__(self) -> None:
        path = self.binary_path
        if not _is_clean_absolute(path) or str(PurePosixPath(path)) != path or PurePosixPath(path).name != BINARY_NAME:
            raise ConfigError(f"RunConditions.binary_path must be a plain absolute path to {BINARY_NAME}, not {path!r}")
        if not isinstance(self.home, str) or not PurePosixPath(self.home).is_absolute():
            raise ConfigError(f"RunConditions.home must be absolute, not {self.home!r}")
        if not _is_count(self.port) or not 1 <= self.port <= 65535:
            raise ConfigError(f"RunConditions.port must be an int from 1 to 65535, not {self.port!r}")
        if not _is_count(self.context) or self.context < CONTEXT_TOKENS or self.context % CONTEXT_MULTIPLE != 0:
            raise ConfigError(
                f"RunConditions.context must be an int of at least {CONTEXT_TOKENS} and a multiple of "
                f"{CONTEXT_MULTIPLE}, not {self.context!r}"
            )
        if self.kv_type not in KV_TYPES:
            raise ConfigError(f"RunConditions.kv_type must be one of {KV_TYPES}, not {self.kv_type!r}")
        if not _is_number(self.ready_deadline_sec) or self.ready_deadline_sec <= 0:
            raise ConfigError("RunConditions.ready_deadline_sec must be a finite number above 0")


@dataclass(frozen=True, slots=True)
class StartupLog:
    """What a start-up log gives the pre-run record (S2 §3.3). At default verbosity only the first four fields exist."""

    verbosity: int
    n_slots: int
    n_ctx_slot: int
    port: int
    layers_offloaded: int | None
    layers_total: int | None
    graph_splits_pp: int | None
    graph_splits_tg: int | None
    cuda_model_mib: float | None
    cuda_kv_mib: float | None
    cuda_recurrent_mib: float | None
    cuda_compute_mib: float | None

    @property
    def graph_splits(self) -> int | None:
        if self.graph_splits_pp is None or self.graph_splits_tg is None:
            return None
        return max(self.graph_splits_pp, self.graph_splits_tg)


@dataclass(frozen=True, slots=True)
class StopResult:
    """How a process ended. `how` is what it took."""

    exit_code: int
    how: Literal["already_exited", "terminated", "killed"]


def _popen(*args: Any, **kwargs: Any) -> Any:
    return subprocess.Popen(*args, **kwargs)


def _read_bytes(path: str) -> bytes:
    return Path(path).read_bytes()


def _realpath(path: str) -> str:
    return os.path.realpath(path)


def _read_chunks(path: str) -> Iterator[bytes]:
    with Path(path).open("rb") as file:
        while chunk := file.read(HASH_CHUNK_BYTES):
            yield chunk


def _open_log(path: str) -> BinaryIO:
    return Path(path).open("xb")


def _monotonic() -> float:
    return time.monotonic()


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


@dataclass(frozen=True, slots=True)
class LaunchTools:
    """Every process, file and clock this module touches. Each default reaches the real call through its module when it
    is called, so nothing here keeps a reference that a test's block could miss."""

    popen: Callable[..., Any] = _popen
    read_bytes: Callable[[str], bytes] = _read_bytes
    realpath: Callable[[str], str] = _realpath
    read_chunks: Callable[[str], Iterable[bytes]] = _read_chunks
    open_log: Callable[[str], BinaryIO] = _open_log
    monotonic: Callable[[], float] = _monotonic
    sleep: Callable[[float], None] = _sleep


def frozen_argv(conditions: RunConditions, candidate: Candidate) -> tuple[str, ...]:
    """The frozen launch line of S2 §3.3 with its values filled in. No mode changes it, and no flag is ever added."""
    kv_type = conditions.kv_type
    return (
        conditions.binary_path,
        "-m",
        candidate.gguf_path,
        "-c",
        str(conditions.context),
        "-np",
        "1",
        "-ngl",
        "999",
        "-fa",
        "on",
        "-ctk",
        kv_type,
        "-ctv",
        kv_type,
        "-b",
        "2048",
        "-ub",
        "512",
        "--jinja",
        "--host",
        HOST,
        "--port",
        str(conditions.port),
        "-fit",
        "off",
        "--cache-ram",
        "0",
        "-ctxcp",
        "0",
    )


def cmdline_bytes(argv: tuple[str, ...]) -> bytes:
    """What `/proc/<pid>/cmdline` holds for `argv`: each argument followed by one NUL."""
    return b"".join(argument.encode("utf-8") + b"\0" for argument in argv)


def base_url(conditions: RunConditions) -> str:
    return f"http://{HOST}:{conditions.port}"


def _check_mode(mode: str) -> None:
    if mode not in _LAUNCH_MODES:
        raise ValueError(f"mode must be one of {_LAUNCH_MODES}, not {mode!r}")


def server_environment(conditions: RunConditions, mode: LaunchMode) -> dict[str, str]:
    """The server's whole environment, built from nothing and never filtered from this process's own: the build acts on
    well over a hundred variables that leave no trace on the command line. Verbose logging is for the pre-run only,
    because it slows timed calls."""
    _check_mode(mode)
    environment = {"HOME": conditions.home, "PATH": CLEAN_PATH}
    if mode == "pre_run":
        environment[VERBOSITY_ENV] = str(VERBOSE_LEVEL)
    return environment


class ServerProcess:
    """One running server. Building it starts it: an argv list, no shell, nothing on stdin, and stdout and stderr in one
    new log file, because every log entry goes to stderr. With no log path, stdout and stderr go to `DEVNULL` and no
    file is created; such a process has no log to read (`_server_log`). Whoever builds one stops it, in a `finally`."""

    def __init__(self, argv: tuple[str, ...], env: Mapping[str, str], log_path: str | None, tools: LaunchTools) -> None:
        self._log_path = log_path
        self._tools = tools
        self._stopped: StopResult | None = None
        self._stop_failed: ServerStopFailed | None = None
        if log_path is None:
            self._process = self._spawn(argv, env, subprocess.DEVNULL, subprocess.DEVNULL)
            return
        log = tools.open_log(log_path)  # first: a log that exists raises FileExistsError and starts nothing
        try:
            self._process = self._spawn(argv, env, log, subprocess.STDOUT)
        finally:
            log.close()

    def _spawn(self, argv: tuple[str, ...], env: Mapping[str, str], stdout: Any, stderr: Any) -> Any:
        try:
            return self._tools.popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=dict(env),
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            raise SpawnFailed(type(exc).__name__) from None

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def log_path(self) -> str | None:
        return self._log_path

    def poll(self) -> int | None:
        return self._process.poll()

    def exit_within(self, seconds: float) -> int | None:
        """The exit code, once there is one, or `None` at the limit: after a dropped connection the socket can close
        before the process is gone."""
        started = self._tools.monotonic()
        while True:
            code = self.poll()
            if code is not None or self._tools.monotonic() - started >= seconds:
                return code
            self._tools.sleep(LOG_POLL_SEC)

    def stop(self) -> StopResult:
        """Terminate, then kill. A process that already exited gets no signal. A second call repeats the outcome and
        signals nothing, a `ServerStopFailed` included."""
        if self._stop_failed is not None:
            raise self._stop_failed
        if self._stopped is None:
            try:
                self._stopped = self._stop()
            except ServerStopFailed as exc:
                self._stop_failed = exc
                raise
        return self._stopped

    def _stop(self) -> StopResult:
        code = self.poll()
        if code is not None:
            return StopResult(code, "already_exited")
        self._process.terminate()
        try:
            return StopResult(self._process.wait(timeout=STOP_GRACE_SEC), "terminated")
        except subprocess.TimeoutExpired:
            self._process.kill()
        try:
            return StopResult(self._process.wait(timeout=STOP_GRACE_SEC), "killed")
        except subprocess.TimeoutExpired:
            raise ServerStopFailed from None


def _server_log(process: ServerProcess, tools: LaunchTools) -> bytes:
    """The server log's bytes. A process started without a log has none, and asking for it is a harness bug: a plain
    `RuntimeError`, not a `LaunchError`, so the runner never records it as a launch fault."""
    if process.log_path is None:
        raise RuntimeError("server started without a log")
    return tools.read_bytes(process.log_path)


def _exited(process: ServerProcess, exit_code: int, tools: LaunchTools) -> ServerExited:
    return ServerExited(exit_code, BIND_FAILED_TEXT.encode("ascii") in _server_log(process, tools))


def wait_ready(
    process: ServerProcess, client: ModelServerClient, conditions: RunConditions, tools: LaunchTools
) -> tuple[ServerProps, float]:
    """Poll `/props` until the model answers, and return it with the seconds it took. The socket binds before the model
    loads, and until then the server answers 503 on every route, so `/props` is as good a readiness probe as `/health`
    and it brings what the checks need (AMB-59). An exit is a launch failure. A slow load is not: one took 149.7 s, and
    a process past the deadline is a `ReadyTimeout`, never "does not load" (AMB-60)."""
    started = tools.monotonic()
    while True:
        code = process.poll()
        if code is not None:
            raise _exited(process, code, tools)
        try:
            return client.fetch_props(), tools.monotonic() - started
        except ServerCheckError as exc:
            if exc.http_status not in (None, 503):
                raise LaunchCheckFailed("props", f"ServerCheckError http_status={exc.http_status}") from None
        if tools.monotonic() - started >= conditions.ready_deadline_sec:
            raise ReadyTimeout(conditions.ready_deadline_sec)
        tools.sleep(READY_POLL_SEC)


def _log_lines(text: str) -> list[str]:
    """The finished lines of a log that may still be growing. A CR ends a line as an LF does, so line endings are folded
    first (AMB-95); whatever follows the last of them is a line in progress and is dropped."""
    folded = text.replace("\r\n", "\n").replace("\r", "\n")
    return folded[: folded.rfind("\n") + 1].split("\n")


def read_settled_log(process: ServerProcess, tools: LaunchTools) -> str:
    """The log, once its listening line is written. The server is ready before its last start-up lines are logged, and
    that line comes after every line the parser needs, because one first-in first-out worker writes them all."""
    started = tools.monotonic()
    while True:
        code = process.poll()
        if code is not None:
            raise _exited(process, code, tools)
        text = _server_log(process, tools).decode("utf-8", errors="replace")
        if any(_LISTENING.fullmatch(line) for line in _log_lines(text)):
            return text
        if tools.monotonic() - started >= LOG_SETTLE_SEC:
            raise LaunchCheckFailed("log", "no listening line")
        tools.sleep(LOG_POLL_SEC)


def parse_startup_log(text: str) -> StartupLog:
    """What a start-up log says, at either verbosity (S2 §3.3). This reads and does not judge, but for three things: the
    server read a config file, which the clean environment cannot prevent and the command line would not show (AMB-61);
    a line the log holds once appears twice, so the record could be the wrong one; or a line of every log is missing."""
    found: dict[str, re.Match[str]] = {}
    for line in _log_lines(text):
        for name, pattern in _LOG_LINES:
            match = pattern.fullmatch(line)
            if match is None:
                continue
            if name == "CONFIG":
                raise LaunchCheckFailed("config_file", "config file in use")
            if name in found:
                raise LaunchCheckFailed("log", f"{name} line repeated")
            found[name] = match
    for name in ("VERBOSITY", "SLOTS", "LISTENING"):
        if name not in found:
            raise LaunchCheckFailed("log", f"{name} line missing")

    def mib(name: str) -> float | None:
        return float(found[name].group(1)) if name in found else None

    offloaded = found.get("OFFLOADED")
    graph = found.get("GRAPH")
    return StartupLog(
        verbosity=int(found["VERBOSITY"].group(1)),
        n_slots=int(found["SLOTS"].group(1)),
        n_ctx_slot=int(found["SLOTS"].group(2)),
        port=int(found["LISTENING"].group(1)),
        layers_offloaded=None if offloaded is None else int(offloaded.group(1)),
        layers_total=None if offloaded is None else int(offloaded.group(2)),
        graph_splits_pp=None if graph is None else int(graph.group(1)),
        graph_splits_tg=None if graph is None else int(graph.group(2) or graph.group(1)),
        cuda_model_mib=mib("MODEL"),
        cuda_kv_mib=mib("KV"),
        cuda_recurrent_mib=mib("RECURRENT"),
        cuda_compute_mib=mib("COMPUTE"),
    )


def _check_cmdline(process: ServerProcess, argv: tuple[str, ...], tools: LaunchTools) -> None:
    """S2 §3.3: bytes for equality, with no parsing or normalising. A process that died reads empty or not at all, and
    that is an exit, so it is looked for before any difference is called a check failure."""
    expected = cmdline_bytes(argv)
    try:
        actual = tools.read_bytes(str(_PROC / str(process.pid) / "cmdline"))
    except OSError:
        actual = b""
    if actual == expected:
        return
    code = process.poll()
    if code is not None:
        raise _exited(process, code, tools)
    if not actual:
        raise LaunchCheckFailed("cmdline", "no command line")
    pairs = enumerate(zip(actual, expected, strict=False))
    first = next((offset for offset, pair in pairs if pair[0] != pair[1]), min(len(actual), len(expected)))
    argument = expected[:first].count(b"\0")  # the NULs before the first different byte
    raise LaunchCheckFailed("cmdline", f"differs at argument {argument}")


def _check_startup(startup: StartupLog, conditions: RunConditions, mode: LaunchMode) -> None:
    """The log checks. A graph-split count above 2 is recorded and never fails here (S2 §3.3), and layers short of N of
    N fail because under `-ngl 999` the line says N of N by construction, whatever the memory (AMB-75)."""
    if startup.n_slots != 1:
        raise LaunchCheckFailed("slots", f"log n_slots={startup.n_slots} expected=1")
    if startup.n_ctx_slot != conditions.context:
        raise LaunchCheckFailed("context", f"log n_ctx_slot={startup.n_ctx_slot} expected={conditions.context}")
    if startup.port != conditions.port:
        raise LaunchCheckFailed("port", f"log port={startup.port} expected={conditions.port}")
    level = VERBOSE_LEVEL if mode == "pre_run" else DEFAULT_LEVEL
    if startup.verbosity != level:
        raise LaunchCheckFailed("verbosity", f"log verbosity={startup.verbosity} expected={level}")
    if mode == "timed":
        return
    for name, value in (
        ("OFFLOADED", startup.layers_offloaded),
        ("GRAPH", startup.graph_splits_pp),
        ("MODEL", startup.cuda_model_mib),
        ("KV", startup.cuda_kv_mib),
        ("COMPUTE", startup.cuda_compute_mib),
    ):
        if value is None:
            raise LaunchCheckFailed("log", f"{name} line missing")
    if startup.layers_offloaded != startup.layers_total:
        raise LaunchCheckFailed("layers", f"{startup.layers_offloaded} of {startup.layers_total}")


def check_props(props: ServerProps, conditions: RunConditions, candidate: Candidate) -> None:
    """The `/props` checks, on every launch, from the response that ended the ready wait. A template without both think
    tags fails: the thinking check could not vouch for it, and that says nothing about the model (AMB-64)."""
    if props.n_ctx != conditions.context:
        raise LaunchCheckFailed("context", f"props n_ctx={props.n_ctx} expected={conditions.context}")
    if props.total_slots != 1:
        raise LaunchCheckFailed("slots", f"props total_slots={props.total_slots} expected=1")
    if props.build_info != PINNED_BUILD_INFO:
        raise LaunchCheckFailed("build", f"build_info differs from {PINNED_BUILD_INFO}")
    if props.model_path != candidate.gguf_path:
        raise LaunchCheckFailed("model_path", "model_path differs from the launched path")
    if props.modalities.on:
        raise LaunchCheckFailed("modalities", f"on: {', '.join(props.modalities.on)}")
    if not all(tag in props.chat_template for tag in THINK_TAGS):
        raise LaunchCheckFailed("think_tags", "the template lacks a think tag")


def verify_launch(
    process: ServerProcess,
    props: ServerProps,
    conditions: RunConditions,
    candidate: Candidate,
    mode: LaunchMode,
    tools: LaunchTools,
) -> StartupLog:
    """Every start-up check, in one order for both modes, each free of model work: the command line, the log, `/props`.
    The first failure raises. The model file and the interfaces are checked once, elsewhere (AMB-84)."""
    _check_mode(mode)
    _check_cmdline(process, frozen_argv(conditions, candidate), tools)
    startup = parse_startup_log(read_settled_log(process, tools))
    _check_startup(startup, conditions, mode)
    check_props(props, conditions, candidate)
    return startup


def template_parser(chat_template: str) -> Literal["auto", "qwen3_coder"]:
    """Which output parser the pinned server picks for a template (H2 AMB-49). A record only, never a failure."""
    if all(tag in chat_template for tag in _CODER_TAGS) and _CODER_EXCLUDED not in chat_template:
        return "qwen3_coder"
    return "auto"


def sha256_file(path: str, tools: LaunchTools) -> str:
    """A file's SHA-256, read in chunks: the model files are 2.5 to 5 GB. An `OSError` propagates."""
    digest = hashlib.sha256()
    for chunk in tools.read_chunks(path):
        digest.update(chunk)
    return digest.hexdigest()


def _on_wsl_filesystem(path: str) -> bool:
    """Absolute, with `mnt` not its first part. Parts are compared, not prefixes, and case is kept: this is Linux."""
    pure = PurePosixPath(path)
    return pure.is_absolute() and pure.parts[1:2] != ("mnt",)


def check_model_file(candidate: Candidate, tools: LaunchTools) -> str:
    """S2 §3.2 and §1, on the pre-run only: the file is off `/mnt` after resolving (AMB-62), and its hash matches. The
    computed hash is returned. A file that cannot be read cannot match, so it fails as a mismatch does, and the other
    candidates are still visited (AMB-94)."""
    if not all(_on_wsl_filesystem(path) for path in (candidate.gguf_path, tools.realpath(candidate.gguf_path))):
        raise ModelFileError("model_location")
    try:
        digest = sha256_file(candidate.gguf_path, tools)
    except OSError:
        raise ModelFileError("model_hash") from None
    if digest != candidate.gguf_sha256:
        raise ModelFileError("model_hash")
    return digest


def interface_names(raw: bytes) -> tuple[str, ...]:
    """The interfaces in the bytes of `/proc/net/dev`: two header lines, then one line per interface."""
    names: list[str] = []
    for line in raw.decode("utf-8", errors="replace").split("\n")[2:]:
        if not line.strip():
            continue
        name, colon, _counters = line.partition(":")
        if not colon:
            raise LaunchCheckFailed("interfaces", "a line of /proc/net/dev has no colon")
        names.append(name.strip())
    return tuple(names)


def check_loopback_only(tools: LaunchTools) -> tuple[str, ...]:
    """S2 §3.2: the namespace has only the loopback interface. The names are returned for the manifest (Annex A)."""
    names = interface_names(tools.read_bytes(_NET_DEV))
    if names != ("lo",):
        raise LaunchCheckFailed("interfaces", f"found: {', '.join(names) or 'none'}")
    return names
