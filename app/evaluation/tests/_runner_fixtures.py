"""Test helpers and synthetic constants for the H3 tests (H3 spec T6; S2 §3.12). Data is synthetic, never gold rows.

Each H3 test module wraps `block_sockets` and `block_processes` in its own autouse fixture, so no test reaches a real
socket or starts a process. The gold directories are written under pytest's `tmp_path` by H1's fixture helpers.

The log fixtures are based on the 2026-11-07 start-up logs, with the real spacing and with the paths replaced. They are
not whole logs: most lines are dropped and some are cut short. `LOG_DEFAULT` stands for every candidate, and
`LOG_VERBOSE` for Qwen3-8B too. The two verbose fixtures share a head and a tail from Qwen3-4B's log, so in
`LOG_VERBOSE_RECURRENT` the stamps step back where the Qwen3.5-4B lines end, and the token count of its request is
Qwen3-4B's. No bind failure was ever captured, so `LOG_BIND_FAILED` is built from the pinned source:
`server-http.cpp:518`, then `server.cpp:649` and `:661`.

`FakeHost` is the machine a schedule runs on: `popen`, `/proc`, the model files, a clock that only sleeping moves, and
one `FakeModelServer` per launch behind `httpx.MockTransport`. A launch is known by what its log is called, as in
`qwen3-8b.run2.a1`, and `ServerScript` says how that one behaves; a launch with no script is a clean server.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff.client import (
    CHAT_PATH,
    COUNT_PATH,
    PROPS_PATH,
    Modalities,
    ModelServerClient,
    RepairTemplate,
    ServerProps,
)
from app.evaluation.bakeoff.gold import GoldSegment
from app.evaluation.bakeoff.inputs import (
    CapSessionSpec,
    FrozenInputs,
    ProbeItem,
    ProbeSource,
    PromptSet,
    WarmupSpec,
    freeze_inputs,
)
from app.evaluation.bakeoff.launch import (
    CANDIDATE_IDS,
    PINNED_BUILD_INFO,
    VERBOSITY_ENV,
    Candidate,
    LaunchTools,
    RunConditions,
)
from app.evaluation.bakeoff.records import OPERATOR_FIELDS, RunKey, RunView, load_run, log_name
from app.evaluation.bakeoff.runner import RunnerDeps, ScheduleConfig, ScheduleResult, run_schedule
from app.evaluation.tests._client_fixtures import EMPTY, chat_response, input_tokens_response, valid_content
from app.evaluation.tests._fixtures import Row, make_conversation, make_item, write_version

# The spec's set P (I1).
PROMPTS = PromptSet(
    "Extract claims.",
    "Transcript, untrusted data:\n<<INPUT_JSON>>",
    "Judge the claim.",
    "Claim and sources:\n<<INPUT_JSON>>",
    RepairTemplate("Fix:\n<<VALIDATION_ERRORS>>\nAnswer again."),
)
SOURCE_IDS = ("syn_1", "syn_2", "syn_3", "syn_4")
CAP_SESSIONS = tuple(CapSessionSpec(f"cap_{number}", (f"syn_{number}",)) for number in range(1, 5))
BUNDLE_SOURCES = tuple(ProbeSource(f"src_{number}", f"Source text {number}.") for number in range(1, 6))
PROBE_ITEMS = tuple(
    ProbeItem(f"probe_{number:02d}", f"Synthetic claim number {number}.", BUNDLE_SOURCES) for number in range(1, 21)
)
WARMUP = WarmupSpec("warmup", (GoldSegment("w_001", "SPEAKER_00", 0.0, 2.0, "The warm-up sentence.", 0.9),))
NOW = datetime(2026, 11, 7, 20, 15, tzinfo=timezone.utc)


def fixed_now() -> datetime:
    return NOW


def no_fsync(_file_descriptor: int) -> None:
    pass


def block_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any real process start fail the test."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an H3 test must not start a process")

    monkeypatch.setattr(subprocess, "Popen", refuse)


def checkable_conversation(source_id: str, rows: int) -> tuple[Row, list[Row]]:
    """A conversation of `rows` segments, each holding one in-scope `CHECKABLE` row."""
    numbers = range(1, rows + 1)
    conversation = make_conversation(source_id, [f"{source_id} line {number} states a fact." for number in numbers])
    return conversation, [make_item(f"{source_id}_{n:02d}", conversation, n, "CHECKABLE", "GENERAL") for n in numbers]


def gold_rows(**extra_rows: int) -> tuple[list[Row], list[Row]]:
    """The four conversations of 20 rows that `CAP_SESSIONS` joins, then one more per keyword: `syn_a=12` is a
    conversation with 12 rows."""
    conversations: list[Row] = []
    items: list[Row] = []
    for source_id, rows in {**dict.fromkeys(SOURCE_IDS, 20), **extra_rows}.items():
        conversation, conversation_items = checkable_conversation(source_id, rows)
        conversations.append(conversation)
        items += conversation_items
    return conversations, items


def synthetic_gold(root: Path, *, name: str = "v1.0", **extra_rows: int) -> Path:
    """A gold version directory under `root`, by default named `v1.0`."""
    return write_version(root, *gold_rows(**extra_rows), name=name)


def frozen_inputs(root: Path, **extra_rows: int) -> FrozenInputs:
    """The standard inputs, frozen from a synthetic gold set."""
    return freeze_inputs(
        str(synthetic_gold(root, **extra_rows)),
        prompts=PROMPTS,
        cap_sessions=CAP_SESSIONS,
        probe_items=PROBE_ITEMS,
        warmup=WARMUP,
    )


BINARY_PATH = "/opt/llama/llama-server"
SERVER_HOME = "/data/harness"
MODEL_PATH = "/models/candidate.gguf"
PORT = 18080
CONDITIONS = RunConditions(BINARY_PATH, SERVER_HOME, PORT)
CANDIDATE = Candidate(CANDIDATE_IDS[0], MODEL_PATH, "a" * 64, "rev-4b", "convert; quantize Q4_K_M")
CANDIDATES = (
    CANDIDATE,
    Candidate(CANDIDATE_IDS[1], "/models/candidate-35.gguf", "b" * 64, "rev-35", "convert; quantize Q4_K_M"),
    Candidate(CANDIDATE_IDS[2], "/models/candidate-8b.gguf", "c" * 64, "rev-8b", "convert; quantize Q4_K_M"),
)
TEMPLATE_TEXT = "{%- if enable_thinking is false %}<think>\n\n</think>{%- endif %}"
# Qwen3-4B and Qwen3-8B share one template. Qwen3.5-4B's is another, and only it holds the `qwen3_coder` parser's tags.
CODER_TEMPLATE_TEXT = TEMPLATE_TEXT + "<tool_call><function=x><parameter=y>"
PROPS: dict[str, Any] = {
    "default_generation_settings": {"params": {"seed": 4294967295}, "n_ctx": 12288},
    "total_slots": 1,
    "model_alias": MODEL_PATH,
    "model_ftype": "Q4_K - Medium",
    "model_path": MODEL_PATH,
    "modalities": {"vision": False, "video": False, "audio": False},
    "media_marker": "<__media_synthetic__>",
    "endpoint_props": False,
    "chat_template": TEMPLATE_TEXT,
    "build_info": PINNED_BUILD_INFO,
    "is_sleeping": False,
}

LOG_DEFAULT = """\
0.01.062.983 I srv  llama_server: initializing ...
0.01.063.356 I cmn  common_param: common_params_print_info: verbosity = 3 (adjust with the `-lv N` CLI arg)
0.01.065.833 W srv  server_setup: security: no API key is set and CORS allows all origins
0.01.076.513 I srv    load_model: loading model '/models/candidate.gguf'
0.01.627.171 W load: control-looking token: 128247 '</s>' was not control-type; this is probably a bug in the model.
0.15.900.966 I cmn          init: llama threadpool init, n_threads = 16
0.16.140.116 I srv    load_model: initializing, n_slots = 1, n_ctx_slot = 12288, kv_unified = 'false'
0.16.143.645 W srv          init: --cache-idle-slots requires --cache-ram, disabling
0.16.170.964 I srv  server_run_i: model loaded
0.16.171.456 I srv  server_on_li: listening on http://127.0.0.1:18080
0.17.524.845 I slot get_availabl: id  0 | task -1 | selected slot by LRU, n_tokens = 0, t_last = -1
"""
_LOG_VERBOSE_HEAD = """\
0.04.806.108 I ggml_cuda_init: found 1 CUDA devices (Total VRAM: 8123 MiB):
0.04.806.322 I   Device 0: NVIDIA GeForce RTX 5060 Laptop GPU, compute capability 12.0, VMM: yes, VRAM: 8123 MiB
0.04.848.381 I srv  llama_server: initializing ...
0.04.849.417 I cmn  common_param: common_params_print_info: build 1 (27b7a8a) with GNU 15.2.0 for Linux x86_64
0.04.849.422 I cmn  common_param: common_params_print_info: verbosity = 4 (adjust with the `-lv N` CLI arg)
0.08.913.405 W srv  server_setup: security: no API key is set and CORS allows all origins
0.08.976.323 I srv    load_model: loading model '/models/candidate.gguf'
"""
_LOG_VERBOSE_TAIL = """\
0.36.317.817 I srv    load_model: initializing, n_slots = 1, n_ctx_slot = 12288, kv_unified = 'false'
0.36.319.959 I slot   load_model: id  0 | task -1 | new slot, n_ctx = 12288
0.36.320.122 I srv    load_model: context checkpoints disabled
0.36.322.921 W srv          init: --cache-idle-slots requires --cache-ram, disabling
0.36.477.967 I srv          init: init: chat template, example_format: '<|im_start|>system
You are a helpful assistant<|im_end|>
<|im_start|>user
Hello<|im_end|>
<|im_start|>assistant
'
0.36.484.389 I srv  server_run_i: model loaded
0.36.484.436 I srv  server_on_li: listening on http://127.0.0.1:18080
0.36.487.970 I srv  update_slots: all slots are idle
\ttop_k = 1, top_p = 0.950, min_p = 0.050, xtc_probability = 0.000, xtc_threshold = 0.100, temp = 0.000
0.43.741.446 I slot   operator(): id  0 | task 0 | new prompt, n_ctx_slot = 12288, n_keep = 0, task.n_tokens = 214
"""
LOG_VERBOSE = (
    _LOG_VERBOSE_HEAD
    + """\
0.28.702.128 I load_tensors: offloading output layer to GPU
0.28.702.322 I load_tensors: offloading 35 repeating layers to GPU
0.28.702.325 I load_tensors: offloaded 37/37 layers to GPU
0.28.702.364 I load_tensors:   CPU_Mapped model buffer size =   304.28 MiB
0.28.702.369 I load_tensors:        CUDA0 model buffer size =  2375.91 MiB
0.33.835.055 I llama_context: n_ctx_seq             = 12288
0.33.860.343 I llama_context:  CUDA_Host  output buffer size =     0.58 MiB
0.34.197.157 I llama_kv_cache:      CUDA0 KV buffer size =  1728.00 MiB
0.34.197.601 I llama_kv_cache: size = 1728.00 MiB ( 12288 cells,  36 layers,  1/1 seqs), K (f16):  864.00 MiB
0.34.197.684 I sched_reserve: reserving ...
0.34.900.748 I sched_reserve:      CUDA0 compute buffer size =    89.01 MiB
0.34.900.927 I sched_reserve:  CUDA_Host compute buffer size =    32.02 MiB
0.34.900.940 I sched_reserve: graph: nodes = 1269, splits = 2, input objects = 4, input tensors = 9
0.34.900.955 I sched_reserve: reserve took 703.25 ms, sched copies = 1
"""
    + _LOG_VERBOSE_TAIL
)
LOG_VERBOSE_RECURRENT = (
    _LOG_VERBOSE_HEAD
    + """\
0.30.098.597 I load_tensors: offloading output layer to GPU
0.30.098.750 I load_tensors: offloading 32 repeating layers to GPU
0.30.098.754 I load_tensors: offloaded 34/34 layers to GPU
0.30.098.795 I load_tensors:   CPU_Mapped model buffer size =   497.31 MiB
0.30.098.800 I load_tensors:        CUDA0 model buffer size =  2572.86 MiB
0.35.451.020 I llama_kv_cache:      CUDA0 KV buffer size =   384.00 MiB
0.35.467.060 I llama_memory_recurrent:      CUDA0 RS buffer size =    50.25 MiB
0.35.467.162 I llama_memory_recurrent: size =   50.25 MiB (     1 cells,  32 layers,  1 seqs  0 rs_seq)
0.36.010.053 I sched_reserve:      CUDA0 compute buffer size =    86.02 MiB
0.36.010.218 I sched_reserve:  CUDA_Host compute buffer size =    32.02 MiB
0.36.010.231 I sched_reserve: graph: nodes = 1905, splits = 2, input objects = 4, input tensors = 10
0.37.459.406 I cmn  common_conte: the context does not support partial sequence removal
"""
    + _LOG_VERBOSE_TAIL
)
LOG_BIND_FAILED = """\
0.00.102.111 I srv  llama_server: initializing ...
0.00.102.340 I cmn  common_param: common_params_print_info: verbosity = 3 (adjust with the `-lv N` CLI arg)
0.00.118.902 E srv         start: couldn't bind HTTP server socket, hostname: 127.0.0.1, port: 18080
0.00.118.931 I srv    operator(): operator(): cleaning up before exit...
0.00.118.950 E srv  server_run_i: exiting due to HTTP server error
"""


def props_response(**changes: Any) -> httpx.Response:
    """The pinned server's `/props` for `CANDIDATE`, with top-level keys replaced."""
    return httpx.Response(200, content=json.dumps({**PROPS, **changes}).encode("utf-8"))


def loading_response() -> httpx.Response:
    """The pinned server's answer on any route while the model loads."""
    body = {"error": {"message": "Loading model", "type": "unavailable_error", "code": 503}}
    return httpx.Response(503, content=json.dumps(body).encode("utf-8"))


def server_props(**changes: Any) -> ServerProps:
    """What `fetch_props` gives for `PROPS`, with fields replaced: every check passes on it unchanged."""
    fields: dict[str, Any] = {
        "n_ctx": 12288,
        "total_slots": 1,
        "model_alias": MODEL_PATH,
        "model_path": MODEL_PATH,
        "build_info": PINNED_BUILD_INFO,
        "chat_template": TEMPLATE_TEXT,
        "modalities": Modalities(False, False, False),
    }
    return ServerProps(**{**fields, **changes})


class FakeTime:
    """`LaunchTools.monotonic` and `sleep` on one clock that only sleeping moves."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeProcess:
    """What `popen` returns. `polls` are what `poll()` gives in turn, and then it gives `exit_code`, which is `None`
    while the process runs. `waits` are what `wait()` does in turn: `None` times out and a code ends the process."""

    def __init__(
        self,
        *,
        pid: int = 4242,
        polls: Iterable[int | None] = (),
        exit_code: int | None = None,
        waits: Iterable[int | None] = (0,),
        on_signal: Callable[[str], None] = lambda _name: None,
    ) -> None:
        self.pid = pid
        self.polls = list(polls)
        self.exit_code = exit_code
        self.waits = list(waits)
        self.on_signal = on_signal
        self.signals: list[str] = []
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        if self.polls:
            self.exit_code = self.polls.pop(0)
        return self.exit_code

    def _signal(self, name: str) -> None:
        if self.exit_code is not None:
            raise AssertionError(f"{name} sent to a process that already exited")
        self.signals.append(name)
        self.on_signal(name)

    def terminate(self) -> None:
        self._signal("terminate")

    def kill(self) -> None:
        self._signal("kill")

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        step = self.waits.pop(0)
        if step is None:
            raise subprocess.TimeoutExpired("llama-server", timeout or 0.0)
        self.exit_code = step
        return step


class FakePopen:
    """`LaunchTools.popen`. Each call records its arguments, refuses a shell, writes `log` to the stdout file and gives
    the next process; a step that is an exception is raised instead."""

    def __init__(self, *steps: FakeProcess | BaseException, log: str = LOG_DEFAULT) -> None:
        self.steps = list(steps)
        self.log = log
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> FakeProcess:
        if kwargs.get("shell") is not False:
            raise AssertionError("the server must start with no shell")
        self.calls.append((args, kwargs))
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        kwargs["stdout"].write(self.log.encode("utf-8"))
        return step


class FakeFiles:
    """`LaunchTools.read_bytes` over a table. A value is bytes, an exception to raise, or a list of those read in turn,
    its last one repeating. A path outside the table is read from disk, as a log under `tmp_path` is, but a `/proc`
    path outside the table is an `AssertionError`, so no test reads the real one."""

    def __init__(self, table: Mapping[str, Any] | None = None) -> None:
        self.table = dict(table or {})
        self.reads: list[str] = []

    def __call__(self, path: str) -> bytes:
        self.reads.append(path)
        if path not in self.table:
            if path.startswith("/proc"):
                raise AssertionError(f"a test must not read the real {path}")
            return Path(path).read_bytes()
        step = self.table[path]
        if isinstance(step, list):
            step = step.pop(0) if len(step) > 1 else step[0]
        if isinstance(step, BaseException):
            raise step
        return step


def refuse_real_path(path: str) -> str:
    raise AssertionError(f"this test must not resolve {path}")


def refuse_real_chunks(path: str) -> Iterable[bytes]:
    raise AssertionError(f"this test must not hash {path}")


def launch_tools(
    popen: FakePopen | None = None,
    files: FakeFiles | None = None,
    clock: FakeTime | None = None,
    *,
    realpath: Callable[[str], str] = refuse_real_path,
    read_chunks: Callable[[str], Iterable[bytes]] = refuse_real_chunks,
) -> LaunchTools:
    """Tools with nothing real behind them but `open_log`, which writes under `tmp_path`."""
    clock = clock or FakeTime()
    return LaunchTools(
        popen=popen or FakePopen(),
        read_bytes=files or FakeFiles(),
        realpath=realpath,
        read_chunks=read_chunks,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


# `/proc/net/dev` as the kernel lays it out, 16 counters to an interface. The smoke kept only the interface names, so
# this shape comes from the format and not from a saved file.
NET_HEADER = (
    b"Inter-|   Receive                                                |  Transmit\n"
    b" face |bytes    packets errs drop fifo frame compressed multicast"
    b"|bytes    packets errs drop fifo colls carrier compressed\n"
)
NET_LO = (
    b"    lo:       0       0    0    0    0     0          0         0"
    b"        0       0    0    0    0     0       0          0\n"
)
NET_ETH0 = b"  eth0: 1 2 0 0 0 0 0 0 3 4 0 0 0 0 0 0\n"
NET_DEV_LOOPBACK = NET_HEADER + NET_LO
BINARY_BYTES = b"synthetic server binary"
MODEL_BYTES = (b"synthetic model one", b"synthetic model two", b"synthetic model three")
SCHEDULE_CANDIDATES = tuple(
    dataclasses.replace(candidate, gguf_sha256=hashlib.sha256(content).hexdigest())
    for candidate, content in zip(CANDIDATES, MODEL_BYTES, strict=True)
)
OPERATOR = dict.fromkeys(OPERATOR_FIELDS, "recorded")
DEFAULT_COUNT = 100


def unit_of(key: RunKey) -> str:
    """What a launch is known by: `qwen3-8b.run2.a1`."""
    return Path(log_name(key)).stem


def small_inputs(inputs: FrozenInputs, *, singles: int = 2, sessions: int = 2, probes: int = 2) -> FrozenInputs:
    """`inputs` cut down, so that a whole schedule is quick where a test pins no count. The runner takes frozen inputs
    as they come, and the freeze alone enforces 4 sessions of 20 probes."""
    return dataclasses.replace(
        inputs,
        singles=inputs.singles[:singles],
        cap_sessions=inputs.cap_sessions[:sessions],
        probes=inputs.probes[:probes],
    )


def _play(step: Any, request: httpx.Request) -> httpx.Response:
    """One scripted step, as in `ScriptedServer`: a response, a request-error class, an exception or a callable."""
    if isinstance(step, type) and issubclass(step, httpx.RequestError):
        raise step("scripted", request=request)
    if isinstance(step, BaseException):
        raise step
    if isinstance(step, httpx.Response):
        return step
    return step(request)


@dataclass
class ServerScript:
    """How one launch behaves; the defaults are a clean server.

    `chat` maps an item id to the steps that item's completions play, in turn, with `None` for the normal answer and
    the normal answer once they run out. `counts` maps an item id to a token count or a step. `props_states` are the
    steps `/props` plays before it answers. `die_at` is the chat request that drops the connection and ends the process,
    and `die_after` the chat request after whose answer the process ends; `die_polls` holds the exit back that many
    polls.
    """

    spawn_error: BaseException | None = None
    polls: tuple[int | None, ...] = ()
    props_states: tuple[Any, ...] = ()
    props: Mapping[str, Any] = field(default_factory=dict)
    log: str | None = None
    cmdline: bytes | None = None
    chat: dict[str, list[Any]] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    die_at: int | None = None
    die_after: int | None = None
    die_code: int = -11
    die_polls: int = 0
    waits: tuple[int | None, ...] = (0,)


class FakeModelServer:
    """One launch's server on the three H2 routes: what `httpx.MockTransport` calls while that launch runs. A dead
    server refuses every connection. `requests` is every request it saw, and `chats` the body of every completion."""

    def __init__(
        self,
        script: ServerScript,
        process: FakeProcess,
        items: Mapping[str, Any],
        counts: Mapping[str, int],
        *,
        model_path: str,
        context: int,
        template: str,
    ) -> None:
        self.script = script
        self.process = process
        self.items = items
        self.counts = counts
        self.model_path = model_path
        self.context = context
        self.template = template
        self.props_states = list(script.props_states)
        self.requests: list[httpx.Request] = []
        self.chats: list[dict[str, Any]] = []
        self.dead = False

    def die(self) -> None:
        self.dead = True
        if self.script.die_polls:
            self.process.polls = [None] * self.script.die_polls + [self.script.die_code]
        else:
            self.process.exit_code = self.script.die_code

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.dead:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == PROPS_PATH:
            return self._props(request)
        body = json.loads(request.content)
        frozen = self.items.get(body["messages"][1]["content"])
        if request.url.path == COUNT_PATH:
            return self._count(request, frozen)
        if request.url.path == CHAT_PATH:
            return self._chat(request, body, frozen)
        raise AssertionError(f"unexpected path {request.url.path}")

    def _props(self, request: httpx.Request) -> httpx.Response:
        if self.props_states:
            return _play(self.props_states.pop(0), request)
        settings = {"params": {"seed": 4294967295}, "n_ctx": self.context}
        fields = {
            "default_generation_settings": settings,
            "model_path": self.model_path,
            "model_alias": self.model_path,
            "chat_template": self.template,
        }
        return props_response(**{**fields, **self.script.props})

    def _count(self, request: httpx.Request, frozen: Any) -> httpx.Response:
        item_id = None if frozen is None else frozen.item_id
        value = self.script.counts.get(item_id, self.counts.get(item_id, DEFAULT_COUNT))
        return input_tokens_response(value) if isinstance(value, int) else _play(value, request)

    def _chat(self, request: httpx.Request, body: dict[str, Any], frozen: Any) -> httpx.Response:
        self.chats.append(body)
        number = len(self.chats)
        if number == self.script.die_at:
            self.die()
            raise httpx.RemoteProtocolError("Server disconnected", request=request)
        steps = self.script.chat.get(None if frozen is None else frozen.item_id)
        step = steps.pop(0) if steps else None
        if step is None:
            response = chat_response(EMPTY if frozen is None else valid_content(frozen.request.kind))
        else:
            response = _play(step, request)
        if number == self.script.die_after:
            self.die()
        return response


class _TrackedClient(ModelServerClient):
    """The real client over a fake transport, which says when it is closed."""

    def __init__(self, *args: Any, on_close: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._on_close = on_close

    def close(self) -> None:
        super().close()
        self._on_close()


class FakeHost:
    """The machine under one schedule. `events` lists `(what, launch)` in order, hooks included, for tests of the order
    of things. `servers` and `processes` are by launch, and `hashed` lists the files read through `read_chunks`."""

    def __init__(
        self,
        inputs: FrozenInputs,
        scripts: Mapping[str, ServerScript] | None = None,
        *,
        counts: Mapping[str, int] | None = None,
        realpaths: Mapping[str, str] | None = None,
        net_dev: bytes = NET_DEV_LOOPBACK,
    ) -> None:
        self.scripts = dict(scripts or {})
        self.counts = dict(counts or {})
        self.realpaths = dict(realpaths or {})
        self.files = FakeFiles({"/proc/net/dev": net_dev})
        self.chunks = {BINARY_PATH: BINARY_BYTES}
        self.chunks.update(zip((row.gguf_path for row in SCHEDULE_CANDIDATES), MODEL_BYTES, strict=True))
        self.clock = FakeTime()
        self.items = {frozen.request.messages[-1].content: frozen for frozen in inputs.all_requests()}
        self.events: list[tuple[str, str]] = []
        self.servers: dict[str, FakeModelServer] = {}
        self.processes: dict[str, FakeProcess] = {}
        self.launches: dict[str, tuple[list[str], dict[str, Any]]] = {}
        self.hashed: list[str] = []
        self.clients: list[tuple[str, int]] = []
        self.closed = 0
        self._server: FakeModelServer | None = None

    def tools(self) -> LaunchTools:
        return LaunchTools(
            popen=self.popen,
            read_bytes=self.files,
            realpath=lambda path: self.realpaths.get(path, path),
            read_chunks=self.read_chunks,
            monotonic=self.clock.monotonic,
            sleep=self.clock.sleep,
        )

    def read_chunks(self, path: str) -> Iterable[bytes]:
        self.hashed.append(path)
        if path not in self.chunks:
            raise FileNotFoundError(path)
        return [self.chunks[path]]

    def popen(self, args: list[str], **kwargs: Any) -> FakeProcess:
        if kwargs.get("shell") is not False:
            raise AssertionError("the server must start with no shell")
        name = Path(kwargs["stdout"].name).stem
        script = self.scripts.get(name, ServerScript())
        self.launches[name] = (args, kwargs)
        self.events.append(("popen", name))
        if script.spawn_error is not None:
            raise script.spawn_error
        kwargs["stdout"].write(self._log(name, args, kwargs["env"], script).encode("utf-8"))
        process = FakeProcess(
            pid=4000 + len(self.launches),
            polls=script.polls,
            waits=script.waits,
            on_signal=lambda signal: self.events.append((signal, name)),
        )
        cmdline = b"".join(argument.encode("utf-8") + b"\0" for argument in args)
        self.files.table[f"/proc/{process.pid}/cmdline"] = cmdline if script.cmdline is None else script.cmdline
        self.processes[name] = process
        self._server = FakeModelServer(
            script,
            process,
            self.items,
            self.counts,
            model_path=args[args.index("-m") + 1],
            context=int(args[args.index("-c") + 1]),
            template=CODER_TEMPLATE_TEXT if name.startswith("qwen3.5") else TEMPLATE_TEXT,
        )
        self.servers[name] = self._server
        return process

    def _log(self, name: str, args: list[str], env: Mapping[str, str], script: ServerScript) -> str:
        """The log a clean launch writes: its own context, and the recurrent buffer only on Qwen3.5-4B."""
        if script.log is not None:
            return script.log
        recurrent = name.startswith("qwen3.5")
        log = (LOG_VERBOSE_RECURRENT if recurrent else LOG_VERBOSE) if VERBOSITY_ENV in env else LOG_DEFAULT
        return log.replace("n_ctx_slot = 12288,", f"n_ctx_slot = {args[args.index('-c') + 1]},")

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self._server is None:
            raise httpx.ConnectError("refused", request=request)
        return self._server(request)

    def client(self, base_url: str, n_ctx: int) -> ModelServerClient:
        self.clients.append((base_url, n_ctx))
        transport = httpx.MockTransport(self.handle)
        return _TrackedClient(base_url, transport=transport, n_ctx=n_ctx, on_close=self._count_close)

    def _count_close(self) -> None:
        self.closed += 1


class RecordingHooks:
    """`RunHooks` that write each call into `events` as `(hook, launch)`. `fail` gives what to raise, if anything."""

    def __init__(
        self,
        events: list[tuple[str, str]],
        fail: Callable[[str, RunKey], BaseException | None] = lambda _name, _key: None,
    ) -> None:
        self.events = events
        self.fail = fail

    def _record(self, name: str, key: RunKey) -> None:
        self.events.append((name, unit_of(key)))
        failure = self.fail(name, key)
        if failure is not None:
            raise failure

    def before_timed_run(self, key: RunKey) -> None:
        self._record("before_timed_run", key)

    def after_timed_run(self, key: RunKey) -> None:
        self._record("after_timed_run", key)

    def start_contention(self, key: RunKey) -> None:
        self._record("start_contention", key)

    def stop_contention(self, key: RunKey) -> None:
        self._record("stop_contention", key)


class Enough(BaseException):
    """Ends a schedule where a test has seen what it needs. A hook's `Exception` is a fault, and this is not one."""


def stop_before(unit: str) -> Callable[[str, RunKey], BaseException | None]:
    """A `fail` that ends the schedule as the timed launch `unit` is about to start."""

    def fail(name: str, key: RunKey) -> BaseException | None:
        return Enough() if name == "before_timed_run" and unit_of(key) == unit else None

    return fail


@dataclass
class ScheduleRig:
    """One schedule, ready to run, and every fake behind it."""

    inputs: FrozenInputs
    config: ScheduleConfig
    host: FakeHost
    hooks: RecordingHooks
    deps: RunnerDeps

    @property
    def run_dir(self) -> str:
        return f"{self.config.store_parent}/{self.config.run_name}"

    def run(self) -> ScheduleResult:
        return run_schedule(self.inputs, self.config, self.deps)

    def run_to_the_stop(self) -> RunView:
        """Run until `stop_before` ends it, and give what is stored."""
        with pytest.raises(Enough):
            self.run()
        return self.view()

    def view(self) -> RunView:
        return load_run(self.run_dir)


def schedule_rig(
    inputs: FrozenInputs,
    root: Path,
    scripts: Mapping[str, ServerScript] | None = None,
    *,
    conditions: RunConditions = CONDITIONS,
    fail: Callable[[str, RunKey], BaseException | None] = lambda _name, _key: None,
    **host_options: Any,
) -> ScheduleRig:
    """A schedule over `inputs` into `<root>/run_1`, on a clean `FakeHost` but for `scripts`."""
    host = FakeHost(inputs, scripts, **host_options)
    hooks = RecordingHooks(host.events, fail)
    config = ScheduleConfig(SCHEDULE_CANDIDATES, conditions, OPERATOR, str(root), "run_1")
    deps = RunnerDeps(hooks=hooks, client_factory=host.client, tools=host.tools(), now=fixed_now, fsync=no_fsync)
    return ScheduleRig(inputs, config, host, hooks, deps)
