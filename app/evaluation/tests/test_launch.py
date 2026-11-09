"""The launch and its start-up checks: H3 spec L1-L18 and rows X3, X5, X6, X14-X22, X30, X31, X37, X40, X45, X48, X49,
X51 (S2 §1, §3.2, §3.3, §3.5, §11 item 5, Annex A).

No test starts a process or reads `/proc`: `FakePopen` and `FakeProcess` stand for the server and `FakeFiles` for
`/proc`. Server logs are real files under `tmp_path`, written from fixtures based on the real start-up logs. The pinned
byte counts and hashes are the spec's, and the log values are the ones measured on 2026-11-07.
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import inspect
import math
import os
import pickle
import subprocess
import time
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest

from app.evaluation.bakeoff import launch as launch_module
from app.evaluation.bakeoff import records as records_module
from app.evaluation.bakeoff.client import CONTEXT_TOKENS, THINK_TAGS, Modalities, ModelServerClient
from app.evaluation.bakeoff.launch import (
    BINARY_NAME,
    BIND_FAILED_TEXT,
    CANDIDATE_IDS,
    CANDIDATE_SLUGS,
    CLEAN_PATH,
    CONTEXT_MULTIPLE,
    DEFAULT_KV_TYPE,
    DEFAULT_LEVEL,
    EXIT_SETTLE_SEC,
    HASH_CHUNK_BYTES,
    HOST,
    KV_TYPES,
    LOG_POLL_SEC,
    LOG_SETTLE_SEC,
    PINNED_BUILD_INFO,
    READY_DEADLINE_SEC,
    READY_POLL_SEC,
    STOP_GRACE_SEC,
    VERBOSE_LEVEL,
    VERBOSITY_ENV,
    Candidate,
    ConfigError,
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
    check_props,
    cmdline_bytes,
    frozen_argv,
    interface_names,
    parse_startup_log,
    read_settled_log,
    server_environment,
    sha256_file,
    template_parser,
    validate_candidates,
    verify_launch,
    wait_ready,
)
from app.evaluation.bakeoff.records import record_json, record_loads
from app.evaluation.tests._client_fixtures import block_sockets, error_response, make_client
from app.evaluation.tests._runner_fixtures import (
    BINARY_PATH,
    CANDIDATE,
    CANDIDATES,
    CONDITIONS,
    LOG_BIND_FAILED,
    LOG_DEFAULT,
    LOG_VERBOSE,
    LOG_VERBOSE_RECURRENT,
    MODEL_PATH,
    NET_ETH0,
    NET_HEADER,
    NET_LO,
    PORT,
    SERVER_HOME,
    FakeFiles,
    FakePopen,
    FakeProcess,
    FakeTime,
    block_processes,
    launch_tools,
    loading_response,
    props_response,
    server_props,
)

ARGV = frozen_argv(CONDITIONS, CANDIDATE)
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
STARTUP_DEFAULT = StartupLog(3, 1, 12288, PORT, None, None, None, None, None, None, None, None)
STARTUP_VERBOSE = StartupLog(4, 1, 12288, PORT, 37, 37, 2, 2, 2375.91, 1728.0, None, 89.01)
STARTUP_RECURRENT = StartupLog(4, 1, 12288, PORT, 34, 34, 2, 2, 2572.86, 384.0, 50.25, 86.02)
PROC_NET_DEV = "/proc/net/dev"


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@dataclasses.dataclass
class Rig:
    """One started `ServerProcess` and every fake behind it."""

    server: ServerProcess
    process: FakeProcess
    popen: FakePopen
    files: FakeFiles
    clock: FakeTime
    tools: LaunchTools


def rig(
    root: Path,
    process: FakeProcess | None = None,
    *,
    log: str = LOG_DEFAULT,
    cmdline: Any = None,
    argv: tuple[str, ...] = ARGV,
) -> Rig:
    """A started server whose command line reads back as `cmdline`, by default exactly `argv`."""
    process = process or FakeProcess()
    popen = FakePopen(process, log=log)
    files = FakeFiles({f"/proc/{process.pid}/cmdline": cmdline_bytes(argv) if cmdline is None else cmdline})
    clock = FakeTime()
    tools = launch_tools(popen, files, clock)
    server = ServerProcess(argv, server_environment(CONDITIONS, "timed"), str(root / "server.log"), tools)
    return Rig(server, process, popen, files, clock, tools)


def candidate(**changes: Any) -> Candidate:
    return dataclasses.replace(CANDIDATE, **changes)


def conditions(**changes: Any) -> RunConditions:
    return dataclasses.replace(CONDITIONS, **changes)


def swap(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new)


def without(text: str, marker: str) -> str:
    """`text` without its lines holding `marker`."""
    kept = [line for line in text.split("\n") if marker not in line]
    assert len(kept) < len(text.split("\n"))
    return "\n".join(kept)


def check_failed(run: Any, *args: Any) -> tuple[str, str]:
    """The check and detail that `run(*args)` fails with."""
    with pytest.raises(LaunchCheckFailed) as caught:
        run(*args)
    return caught.value.check, caught.value.detail


# ---------------------------------------------------------------- L1 the closed field


def test_l1_the_field_is_exactly_s2_s_three() -> None:
    assert CANDIDATE_IDS == ("Qwen/Qwen3-4B", "Qwen/Qwen3.5-4B", "Qwen/Qwen3-8B")
    assert dict(CANDIDATE_SLUGS) == {
        "Qwen/Qwen3-4B": "qwen3-4b",
        "Qwen/Qwen3.5-4B": "qwen3.5-4b",
        "Qwen/Qwen3-8B": "qwen3-8b",
    }
    with pytest.raises(TypeError):
        CANDIDATE_SLUGS["Qwen/Other"] = "other"  # type: ignore[index]


def test_l1_a_valid_candidate() -> None:
    assert dataclasses.asdict(CANDIDATE) == {
        "candidate_id": "Qwen/Qwen3-4B",
        "gguf_path": MODEL_PATH,
        "gguf_sha256": "a" * 64,
        "source_revision": "rev-4b",
        "quantization_commands": "convert; quantize Q4_K_M",
    }


@pytest.mark.parametrize(
    "candidate_id",
    [
        "Qwen/Qwen3-4B-Base",
        "Qwen/Qwen3-4B-Instruct-2507",
        "qwen/qwen3-4b",
        "Qwen/Qwen3-14B",
        "Other/Model",
        "",
        None,
        1,
    ],
)
def test_l1_no_other_model_can_be_configured(candidate_id: Any) -> None:
    with pytest.raises(ConfigError, match="candidate_id"):
        candidate(candidate_id=candidate_id)


@pytest.mark.parametrize(
    "path",
    [
        "/models/a/../b.gguf",
        "/models/./b.gguf",
        "/models/..",
        "/./b.gguf",
        "candidate.gguf",
        "models/candidate.gguf",
        "./candidate.gguf",
        "C:" + chr(92) + "models" + chr(92) + "b.gguf",
        "C:/models/b.gguf",
        "",
        None,
        3,
        b"/models/candidate.gguf",
    ],
)
def test_l1_the_path_is_absolute_with_no_dot_part(path: Any) -> None:
    with pytest.raises(ConfigError, match="gguf_path"):
        candidate(gguf_path=path)


@pytest.mark.parametrize("path", ["/models/candidate.gguf", "/m", "/data/a..b/c.d.gguf", "/data/.hidden/m.gguf"])
def test_l1_a_clean_path_passes_as_given(path: str) -> None:
    assert candidate(gguf_path=path).gguf_path == path


@pytest.mark.parametrize(
    "digest",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "", None, b"a" * 64, "a" * 64 + "\n", ABC_SHA256.upper()],
)
def test_l1_the_hash_is_64_lowercase_hex(digest: Any) -> None:
    with pytest.raises(ConfigError, match="gguf_sha256"):
        candidate(gguf_sha256=digest)


@pytest.mark.parametrize("name", ["source_revision", "quantization_commands"])
@pytest.mark.parametrize("value", ["", "   ", "\n\t", None, 1])
def test_l1_annex_a_s_values_are_not_blank(name: str, value: Any) -> None:
    with pytest.raises(ConfigError, match=name):
        candidate(**{name: value})


def test_l1_a_candidate_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        CANDIDATE.gguf_path = "/other.gguf"  # type: ignore[misc]
    assert not hasattr(CANDIDATE, "__dict__")


def test_l1_the_three_candidates_pass_unchanged() -> None:
    assert validate_candidates(CANDIDATES) is CANDIDATES


@pytest.mark.parametrize(
    "candidates",
    [
        pytest.param((), id="none"),
        pytest.param(CANDIDATES[:1], id="one"),
        pytest.param(CANDIDATES[:2], id="two"),
        pytest.param((CANDIDATES[1], CANDIDATES[0], CANDIDATES[2]), id="swapped"),
        pytest.param((CANDIDATES[2], CANDIDATES[1], CANDIDATES[0]), id="reversed"),
        pytest.param((*CANDIDATES, CANDIDATES[0]), id="four"),
        pytest.param((CANDIDATES[0], CANDIDATES[0], CANDIDATES[2]), id="one-twice"),
        pytest.param(list(CANDIDATES), id="a-list"),
        pytest.param((CANDIDATES[0], CANDIDATES[1], "Qwen/Qwen3-8B"), id="not-a-candidate"),
        pytest.param(
            (CANDIDATES[0], dataclasses.replace(CANDIDATES[1], gguf_path=MODEL_PATH), CANDIDATES[2]),
            id="one-path-twice",
        ),
        pytest.param(
            (CANDIDATES[0], CANDIDATES[1], dataclasses.replace(CANDIDATES[2], gguf_sha256=CANDIDATES[1].gguf_sha256)),
            id="one-hash-twice",
        ),
    ],
)
def test_l1_anything_else_is_refused(candidates: Any) -> None:
    with pytest.raises(ConfigError):
        validate_candidates(candidates)


# ---------------------------------------------------------------- L2 one set of conditions


def test_l2_the_defaults_are_s2_s() -> None:
    plain = RunConditions(BINARY_PATH, SERVER_HOME, PORT)
    assert (plain.context, plain.kv_type, plain.ready_deadline_sec) == (12288, "f16", 600.0)
    assert (CONTEXT_MULTIPLE, DEFAULT_KV_TYPE, BINARY_NAME) == (256, "f16", "llama-server")


@pytest.mark.parametrize("context", [12288, 12544, 16384, 40960])
def test_l2_a_raised_context_passes(context: int) -> None:
    assert conditions(context=context).context == context


@pytest.mark.parametrize("context", [12500, 12287, 12032, 8192, 0, -12288, True, 12288.0, "12288", None])
def test_l2_the_context_is_at_least_s2_s_and_a_multiple_of_256(context: Any) -> None:
    with pytest.raises(ConfigError, match="context"):
        conditions(context=context)


@pytest.mark.parametrize(
    "path",
    [
        "llama-server",
        "/opt/llama/llama-cli",
        "/opt/llama/llama-server.sh",
        "/opt/llama-server/",
        "/opt/x/../llama-server",
        "/opt/./llama-server",
        "/opt//llama-server",
        "./llama-server",
        "",
        None,
    ],
)
def test_l2_the_binary_is_llama_server_on_a_plain_path(path: Any) -> None:
    with pytest.raises(ConfigError, match="binary_path"):
        conditions(binary_path=path)


@pytest.mark.parametrize("home", ["data/harness", "", "~", None, 1])
def test_l2_home_is_absolute(home: Any) -> None:
    with pytest.raises(ConfigError, match="home"):
        conditions(home=home)


@pytest.mark.parametrize("port", [1, 18080, 65535])
def test_l2_a_port_in_range(port: int) -> None:
    assert conditions(port=port).port == port


@pytest.mark.parametrize("port", [0, 65536, -1, True, 18080.0, "18080", None])
def test_l2_a_port_out_of_range_or_of_the_wrong_type(port: Any) -> None:
    with pytest.raises(ConfigError, match="port"):
        conditions(port=port)


@pytest.mark.parametrize("kv_type", KV_TYPES)
def test_l2_every_kv_type_the_build_accepts(kv_type: str) -> None:
    assert conditions(kv_type=kv_type).kv_type == kv_type


def test_l2_the_kv_types_are_the_builds() -> None:
    assert KV_TYPES == ("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1")


@pytest.mark.parametrize("kv_type", ["q9", "F16", "", None])
def test_l2_any_other_kv_type_is_refused(kv_type: Any) -> None:
    with pytest.raises(ConfigError, match="kv_type"):
        conditions(kv_type=kv_type)


@pytest.mark.parametrize("deadline", [1, 0.5, 600.0])
def test_l2_a_positive_deadline(deadline: float) -> None:
    assert conditions(ready_deadline_sec=deadline).ready_deadline_sec == deadline


@pytest.mark.parametrize("deadline", [0, 0.0, -1.0, math.nan, math.inf, True, "600", None])
def test_l2_a_deadline_that_could_never_expire_properly_is_refused(deadline: Any) -> None:
    with pytest.raises(ConfigError, match="ready_deadline_sec"):
        conditions(ready_deadline_sec=deadline)


@pytest.mark.parametrize("module", ["inputs", "launch", "records", "runner"])
def test_l2_nothing_else_holds_the_context(module: str) -> None:
    source = Path(launch_module.__file__).with_name(f"{module}.py").read_text(encoding="utf-8")
    constants = {node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant)}
    assert CONTEXT_TOKENS not in constants


def test_l2_conditions_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        CONDITIONS.context = 16384  # type: ignore[misc]
    assert not hasattr(CONDITIONS, "__dict__")


# ---------------------------------------------------------------- L3 the frozen line


S2_LINE = (
    "llama-server -m <file> -c <ctx> -np 1 -ngl 999 -fa on -ctk <kv> -ctv <kv> -b 2048 -ub 512 --jinja "
    "--host 127.0.0.1 --port <p> -fit off --cache-ram 0 -ctxcp 0"
)


def test_l3_the_line_is_s2_s_with_its_values_filled_in() -> None:
    values = {"llama-server": BINARY_PATH, "<file>": MODEL_PATH, "<ctx>": "12288", "<kv>": "f16", "<p>": "18080"}
    assert ARGV == tuple(values.get(word, word) for word in S2_LINE.split())
    assert len(ARGV) == 30


def test_l3_the_thirty_strings_spelled_out() -> None:
    assert ARGV == (
        "/opt/llama/llama-server",
        "-m",
        "/models/candidate.gguf",
        "-c",
        "12288",
        "-np",
        "1",
        "-ngl",
        "999",
        "-fa",
        "on",
        "-ctk",
        "f16",
        "-ctv",
        "f16",
        "-b",
        "2048",
        "-ub",
        "512",
        "--jinja",
        "--host",
        "127.0.0.1",
        "--port",
        "18080",
        "-fit",
        "off",
        "--cache-ram",
        "0",
        "-ctxcp",
        "0",
    )


def test_l3_other_conditions_change_only_their_own_elements() -> None:
    other = frozen_argv(conditions(context=16384, kv_type="q8_0"), CANDIDATE)
    assert {index for index, pair in enumerate(zip(ARGV, other, strict=True)) if pair[0] != pair[1]} == {4, 12, 14}
    assert (other[4], other[12], other[14]) == ("16384", "q8_0", "q8_0")


def test_l3_no_mode_can_reach_the_line() -> None:
    assert list(inspect.signature(frozen_argv).parameters) == ["conditions", "candidate"]


def test_l3_the_path_is_the_configured_string() -> None:
    assert frozen_argv(CONDITIONS, candidate(gguf_path="/data/link.gguf"))[2] == "/data/link.gguf"


def test_l3_the_base_url_is_the_one_the_client_accepts() -> None:
    assert base_url(CONDITIONS) == "http://127.0.0.1:18080"
    assert HOST == "127.0.0.1"
    client = ModelServerClient(base_url(conditions(port=9)), transport=httpx.MockTransport(lambda _: props_response()))
    assert client.base_url == "http://127.0.0.1:9"
    assert client.fetch_props() == server_props()


# ---------------------------------------------------------------- L4 the command line


def verify(run: Rig, *, mode: LaunchMode = "timed", props: Any = None, at: RunConditions = CONDITIONS) -> StartupLog:
    return verify_launch(run.server, props or server_props(), at, CANDIDATE, mode, run.tools)


def test_l4_the_spec_s_bytes() -> None:
    raw = cmdline_bytes(ARGV)
    assert len(raw) == 185
    assert hashlib.sha256(raw).hexdigest() == "e02cfb417d5b9dbbfaba9dd53329b83402cd411e2bcf063df8fff7738a33bee1"
    assert raw.endswith(b"-ctxcp\0" + b"0\0")
    assert raw.count(b"\0") == 30


def test_l4_each_argument_is_utf_8_then_one_nul() -> None:
    assert cmdline_bytes(("a", chr(0xE9), "")) == b"a\0\xc3\xa9\0\0"
    assert cmdline_bytes(()) == b""


def test_l4_the_process_s_own_entry_is_read(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(pid=7))
    assert verify(run) == STARTUP_DEFAULT
    assert run.files.reads[0] == "/proc/7/cmdline"


@pytest.mark.parametrize(
    ("raw", "where"),
    [
        pytest.param(cmdline_bytes(ARGV)[:-1], 29, id="no-last-nul"),
        pytest.param(cmdline_bytes((*ARGV, "-lv", "4")), 30, id="an-extra-lv-4"),
        pytest.param(cmdline_bytes((*ARGV, "--verbose")), 30, id="x31-an-extra-trailing-flag"),
        pytest.param(cmdline_bytes(ARGV[:-1]), 29, id="a-missing-last-argument"),
        pytest.param(cmdline_bytes((*ARGV[:4], "8192", *ARGV[5:])), 4, id="x31-another-context"),
        pytest.param(cmdline_bytes(frozen_argv(conditions(port=18081), CANDIDATE)), 23, id="another-port"),
        pytest.param(cmdline_bytes(frozen_argv(conditions(kv_type="q8_0"), CANDIDATE)), 12, id="another-kv-type"),
        pytest.param(cmdline_bytes(("/usr/bin/llama-server", *ARGV[1:])), 0, id="another-binary"),
        pytest.param(cmdline_bytes(ARGV) + b"\0", 30, id="one-nul-too-many"),
        pytest.param(b" ".join(argument.encode("utf-8") for argument in ARGV), 0, id="spaces-for-nuls"),
        pytest.param(b"\0", 0, id="only-a-nul"),
    ],
)
def test_l4_any_difference_fails_at_its_first_argument(tmp_path: Path, raw: bytes, where: int) -> None:
    run = rig(tmp_path, cmdline=raw)
    assert check_failed(verify, run) == ("cmdline", f"differs at argument {where}")


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(b"", id="empty"),
        pytest.param(FileNotFoundError("gone"), id="no-file"),
        pytest.param(ProcessLookupError("gone"), id="no-process"),
        pytest.param(PermissionError("denied"), id="denied"),
    ],
)
def test_l4_nothing_to_read_with_the_process_alive(tmp_path: Path, read: Any) -> None:
    assert check_failed(verify, rig(tmp_path, cmdline=read)) == ("cmdline", "no command line")


@pytest.mark.parametrize(
    "read",
    [
        pytest.param(b"", id="x48-a-zombie-reads-empty"),
        pytest.param(FileNotFoundError("gone"), id="no-file"),
        pytest.param(cmdline_bytes(ARGV[:-2]), id="a-difference"),
    ],
)
def test_l4_a_process_that_exited_is_an_exit_never_a_cmdline_failure(tmp_path: Path, read: Any) -> None:
    run = rig(tmp_path, FakeProcess(exit_code=1), cmdline=read)
    with pytest.raises(ServerExited) as caught:
        verify(run)
    assert (caught.value.exit_code, caught.value.bind_failed) == (1, False)


def test_x6_a_stale_server_answered_while_ours_died_on_its_port(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(polls=[None, 1]), log=LOG_BIND_FAILED, cmdline=b"")
    client, _server = make_client(props_response())
    props, _seconds = wait_ready(run.server, client, CONDITIONS, run.tools)
    with pytest.raises(ServerExited) as caught:
        verify(run, props=props)
    assert (caught.value.exit_code, caught.value.bind_failed) == (1, True)


# ---------------------------------------------------------------- L5 the clean environment


def test_l5_the_two_environments_spelled_out() -> None:
    assert server_environment(CONDITIONS, "timed") == {"HOME": SERVER_HOME, "PATH": "/usr/bin:/bin"}
    assert server_environment(CONDITIONS, "pre_run") == {
        "HOME": SERVER_HOME,
        "PATH": "/usr/bin:/bin",
        "LLAMA_ARG_LOG_VERBOSITY": "4",
    }
    assert (CLEAN_PATH, VERBOSITY_ENV, VERBOSE_LEVEL, DEFAULT_LEVEL) == (
        "/usr/bin:/bin",
        "LLAMA_ARG_LOG_VERBOSITY",
        4,
        3,
    )


def test_l5_the_modes_are_the_literal_s() -> None:
    assert get_args(LaunchMode) == ("pre_run", "timed")


@pytest.mark.parametrize("mode", ["pre-run", "PRE_RUN", "", None])
def test_l5_any_other_mode_is_refused(mode: Any) -> None:
    with pytest.raises(ValueError):
        server_environment(CONDITIONS, mode)


@pytest.mark.parametrize(
    ("mode", "names"),
    [("timed", {"HOME", "PATH"}), ("pre_run", {"HOME", "PATH", "LLAMA_ARG_LOG_VERBOSITY"})],
)
def test_x30_nothing_inherited_reaches_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: LaunchMode, names: set[str]
) -> None:
    inherited = {
        "LLAMA_ARG_CTX_SIZE": "4096",
        "LLAMA_ARG_NO_MMAP": "1",
        "LLAMA_ARG_MMPROJ": "/x",
        "LLAMA_ARG_THINK": "auto",
        "GGML_CUDA_GRAPH_OPT": "1",
        "CUDA_VISIBLE_DEVICES": "1",
        "LD_PRELOAD": "/x",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    popen = FakePopen(FakeProcess())
    env = server_environment(CONDITIONS, mode)
    ServerProcess(ARGV, env, str(tmp_path / "server.log"), launch_tools(popen))
    assert set(popen.calls[0][1]["env"]) == names
    assert popen.calls[0][1]["env"] == env
    assert not set(inherited) & set(env)


# ---------------------------------------------------------------- L6 the process wrapper


def test_l6_the_spawn_s_arguments(tmp_path: Path) -> None:
    run = rig(tmp_path)
    ((args, kwargs),) = run.popen.calls
    assert type(args) is list
    assert args == list(ARGV)
    assert set(kwargs) == {"stdin", "stdout", "stderr", "env", "shell", "close_fds"}
    assert kwargs["shell"] is False
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["close_fds"] is True
    assert kwargs["env"] == {"HOME": SERVER_HOME, "PATH": "/usr/bin:/bin"}
    assert kwargs["stdout"].closed
    assert Path(run.server.log_path).read_text(encoding="utf-8") == LOG_DEFAULT


def test_l6_the_wrapper_s_properties(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(pid=99))
    assert run.server.pid == 99
    assert run.server.log_path == str(tmp_path / "server.log")
    assert run.server.poll() is None


def test_l6_the_environment_is_copied(tmp_path: Path) -> None:
    env = server_environment(CONDITIONS, "timed")
    popen = FakePopen(FakeProcess())
    ServerProcess(ARGV, env, str(tmp_path / "server.log"), launch_tools(popen))
    assert popen.calls[0][1]["env"] is not env


def test_l6_a_second_process_on_one_log_starts_nothing(tmp_path: Path) -> None:
    popen = FakePopen(FakeProcess(), FakeProcess())
    tools = launch_tools(popen)
    log_path = str(tmp_path / "server.log")
    ServerProcess(ARGV, {}, log_path, tools)
    with pytest.raises(FileExistsError):
        ServerProcess(ARGV, {}, log_path, tools)
    assert len(popen.calls) == 1
    assert Path(log_path).read_text(encoding="utf-8") == LOG_DEFAULT


@pytest.mark.parametrize(
    ("error", "name"),
    [
        pytest.param(PermissionError("denied"), "PermissionError", id="x51-permission-error"),
        pytest.param(FileNotFoundError("no binary"), "FileNotFoundError", id="no-binary"),
        pytest.param(OSError("exec format"), "OSError", id="os-error"),
    ],
)
def test_l6_an_os_error_from_popen_is_a_spawn_failure(tmp_path: Path, error: OSError, name: str) -> None:
    popen = FakePopen(error)
    with pytest.raises(SpawnFailed) as caught:
        ServerProcess(ARGV, {}, str(tmp_path / "server.log"), launch_tools(popen))
    assert caught.value.error_class == name
    assert str(caught.value) == f"spawn failed: {name}"
    assert popen.calls[0][1]["stdout"].closed


def test_l6_any_other_error_from_popen_is_a_harness_bug(tmp_path: Path) -> None:
    popen = FakePopen(ValueError("embedded null byte"))
    with pytest.raises(ValueError, match="embedded null byte"):
        ServerProcess(ARGV, {}, str(tmp_path / "server.log"), launch_tools(popen))
    assert popen.calls[0][1]["stdout"].closed


def test_l6_the_block_reaches_the_default_popen() -> None:
    with pytest.raises(AssertionError, match="must not start a process"):
        LaunchTools().popen(["/bin/false"], shell=False)


def test_l6_the_defaults_reach_the_real_calls_when_they_are_called(monkeypatch: pytest.MonkeyPatch) -> None:
    tools = LaunchTools()
    spawned: list[tuple[Any, Any]] = []
    slept: list[float] = []

    def popen(*args: Any, **kwargs: Any) -> str:
        spawned.append((args, kwargs))
        return "process"

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(os.path, "realpath", lambda path: f"{path}/resolved")
    monkeypatch.setattr(time, "monotonic", lambda: 7.5)
    monkeypatch.setattr(time, "sleep", slept.append)
    assert tools.popen(["x"], shell=False) == "process"
    assert spawned == [((["x"],), {"shell": False})]
    assert tools.realpath("/m") == "/m/resolved"
    assert tools.monotonic() == 7.5
    tools.sleep(0.25)
    assert slept == [0.25]


def test_l6_the_default_files(tmp_path: Path) -> None:
    tools = LaunchTools()
    path = tmp_path / "server.log"
    log = tools.open_log(str(path))
    log.write(b"line\n")
    log.close()
    assert tools.read_bytes(str(path)) == b"line\n"
    with pytest.raises(FileExistsError):
        tools.open_log(str(path))
    assert path.read_bytes() == b"line\n"


def test_l6_the_tools_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        LaunchTools().sleep = time.sleep  # type: ignore[misc]
    assert not hasattr(LaunchTools(), "__dict__")


# ---------------------------------------------------------------- L7 the ready wait


def test_l7_the_constants() -> None:
    assert (READY_DEADLINE_SEC, READY_POLL_SEC) == (600.0, 1.0)


def test_l7_the_evidence_s_poll_sequence(tmp_path: Path) -> None:
    run = rig(tmp_path)
    client, server = make_client(
        httpx.ConnectError, httpx.ConnectError, loading_response(), loading_response(), props_response()
    )
    assert wait_ready(run.server, client, CONDITIONS, run.tools) == (server_props(), 4.0)
    assert run.clock.sleeps == [1.0, 1.0, 1.0, 1.0]
    assert [request.url.path for request in server.requests] == ["/props"] * 5


def test_l7_ready_at_once_never_sleeps(tmp_path: Path) -> None:
    run = rig(tmp_path)
    client, _server = make_client(props_response())
    assert wait_ready(run.server, client, CONDITIONS, run.tools) == (server_props(), 0.0)
    assert run.clock.sleeps == []


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(httpx.ConnectError, id="refused"),
        pytest.param(httpx.ReadTimeout, id="timeout"),
        pytest.param(httpx.RemoteProtocolError, id="dropped"),
        pytest.param(httpx.Response(503), id="503-empty"),
    ],
)
def test_l7_no_answer_yet_means_keep_waiting(tmp_path: Path, step: Any) -> None:
    run = rig(tmp_path)
    client, _server = make_client(step, props_response())
    assert wait_ready(run.server, client, CONDITIONS, run.tools) == (server_props(), 1.0)


def test_x3_an_exit_during_the_wait_is_a_launch_failure(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(polls=[None, None, 1]))
    client, server = make_client(httpx.ConnectError, loading_response())
    with pytest.raises(ServerExited) as caught:
        wait_ready(run.server, client, CONDITIONS, run.tools)
    assert (caught.value.exit_code, caught.value.bind_failed) == (1, False)
    assert str(caught.value) == "server exited with code 1"
    assert len(server.requests) == 2


def test_x5_a_port_in_use_is_read_from_the_log(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(exit_code=1), log=LOG_BIND_FAILED)
    client, server = make_client()
    with pytest.raises(ServerExited) as caught:
        wait_ready(run.server, client, CONDITIONS, run.tools)
    assert (caught.value.exit_code, caught.value.bind_failed) == (1, True)
    assert BIND_FAILED_TEXT == "couldn't bind HTTP server socket"
    assert server.requests == []


def test_l7_a_slow_load_is_not_a_fault(tmp_path: Path) -> None:
    run = rig(tmp_path)
    client, _server = make_client(*(loading_response() for _ in range(149)), props_response())
    assert wait_ready(run.server, client, CONDITIONS, run.tools) == (server_props(), 149.0)
    assert run.process.signals == []


def test_x40_the_deadline_passes_with_the_process_alive(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(waits=[-15]))  # no handler is installed until the load ends, so SIGTERM kills (F72)
    client, server = make_client(*(loading_response() for _ in range(601)))
    try:
        with pytest.raises(ReadyTimeout) as caught:
            wait_ready(run.server, client, CONDITIONS, run.tools)
    finally:
        stopped = run.server.stop()
    assert caught.value.deadline_sec == 600.0
    assert str(caught.value) == "not ready after 600 s"
    assert run.clock.now == 600.0
    assert len(server.requests) == 601
    assert stopped == StopResult(-15, "terminated")
    assert run.process.signals == ["terminate"]


def test_l7_the_deadline_comes_from_the_conditions(tmp_path: Path) -> None:
    run = rig(tmp_path)
    client, server = make_client(*(loading_response() for _ in range(4)))
    with pytest.raises(ReadyTimeout) as caught:
        wait_ready(run.server, client, conditions(ready_deadline_sec=2.5), run.tools)
    assert str(caught.value) == "not ready after 2.5 s"
    assert (run.clock.now, len(server.requests)) == (3.0, 4)


@pytest.mark.parametrize(
    ("step", "status"),
    [
        pytest.param(lambda: error_response(500, "server_error", "SERVER-TEXT"), 500, id="http-500"),
        pytest.param(lambda: error_response(404, "not_found_error", "SERVER-TEXT"), 404, id="http-404"),
        pytest.param(lambda: httpx.Response(302, headers={"Location": "/x"}), 302, id="a-redirect"),
        pytest.param(lambda: httpx.Response(200, content=b"SERVER-TEXT"), 200, id="a-malformed-200"),
        pytest.param(lambda: props_response(total_slots="1"), 200, id="a-mistyped-200"),
        pytest.param(
            lambda: props_response(modalities={"vision": False, "video": False, "audio": False, "text": False}),
            200,
            id="x22-a-fourth-modality-key",
        ),
    ],
)
def test_l7_any_other_answer_is_a_failed_check(tmp_path: Path, step: Any, status: int) -> None:
    run = rig(tmp_path)
    client, server = make_client(step())
    with pytest.raises(LaunchCheckFailed) as caught:
        wait_ready(run.server, client, CONDITIONS, run.tools)
    assert (caught.value.check, caught.value.detail) == ("props", f"ServerCheckError http_status={status}")
    assert "SERVER-TEXT" not in str(caught.value)
    assert len(server.requests) == 1


# ---------------------------------------------------------------- L8 stopping


def test_l8_the_constants() -> None:
    assert (STOP_GRACE_SEC, EXIT_SETTLE_SEC, LOG_POLL_SEC) == (30.0, 5.0, 0.1)


def test_l8_a_clean_terminate(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(waits=[0]))
    assert run.server.stop() == StopResult(0, "terminated")
    assert run.process.signals == ["terminate"]
    assert run.process.wait_timeouts == [30.0]


def test_l8_a_kill_after_the_grace(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(waits=[None, -9]))
    assert run.server.stop() == StopResult(-9, "killed")
    assert run.process.signals == ["terminate", "kill"]
    assert run.process.wait_timeouts == [30.0, 30.0]


def test_l8_a_server_that_will_not_stop(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(waits=[None, None]))
    with pytest.raises(ServerStopFailed) as caught:
        run.server.stop()
    assert str(caught.value) == "still running after kill"
    assert run.process.signals == ["terminate", "kill"]


def test_l8_a_second_stop_of_a_server_that_will_not_stop_signals_nothing(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(waits=[None, None]))
    with pytest.raises(ServerStopFailed):
        run.server.stop()
    with pytest.raises(ServerStopFailed):
        run.server.stop()
    assert run.process.signals == ["terminate", "kill"]
    assert run.process.wait_timeouts == [30.0, 30.0]


def test_l8_an_exited_process_gets_no_signal(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(exit_code=-11))
    assert run.server.stop() == StopResult(-11, "already_exited")
    assert run.process.signals == []


@pytest.mark.parametrize(
    ("process", "expected", "signals"),
    [
        pytest.param(FakeProcess(waits=[0]), StopResult(0, "terminated"), ["terminate"], id="terminated"),
        pytest.param(FakeProcess(waits=[None, -9]), StopResult(-9, "killed"), ["terminate", "kill"], id="killed"),
        pytest.param(FakeProcess(exit_code=1), StopResult(1, "already_exited"), [], id="already-exited"),
    ],
)
def test_l8_a_second_stop_repeats_the_first_result(
    tmp_path: Path, process: FakeProcess, expected: StopResult, signals: list[str]
) -> None:
    run = rig(tmp_path, process)
    assert run.server.stop() == expected
    assert run.server.stop() == expected
    assert process.signals == signals


def test_l8_exit_within_finds_a_process_that_is_still_dying(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(polls=[*[None] * 20, -6]))
    assert run.server.exit_within(EXIT_SETTLE_SEC) == -6
    assert run.clock.sleeps == [0.1] * 20


def test_l8_exit_within_gives_an_exit_at_once(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(exit_code=3))
    assert run.server.exit_within(EXIT_SETTLE_SEC) == 3
    assert run.clock.sleeps == []


def test_l8_exit_within_gives_none_at_the_limit(tmp_path: Path) -> None:
    run = rig(tmp_path)
    assert run.server.exit_within(0.3) is None
    assert 0.3 <= run.clock.now < 0.3 + 2 * LOG_POLL_SEC
    assert run.process.signals == []


def test_l8_exit_within_zero_looks_once(tmp_path: Path) -> None:
    run = rig(tmp_path)
    assert run.server.exit_within(0.0) is None
    assert run.clock.sleeps == []


# ---------------------------------------------------------------- L9 the settled log


LISTENING_LINE = "0.16.171.456 I srv  server_on_li: listening on http://127.0.0.1:18080"
SLOT_LINE = "0.16.140.116 I srv    load_model: initializing, n_slots = 1, n_ctx_slot = 12288, kv_unified = 'false'"


def settled(root: Path, reads: list[Any], process: FakeProcess | None = None) -> tuple[Rig, Any]:
    """`read_settled_log` over a log that reads as `reads`, in turn."""
    run = rig(root, process)
    run.files.table[run.server.log_path] = reads
    return run, lambda: read_settled_log(run.server, run.tools)


def test_l9_the_constants() -> None:
    assert (LOG_SETTLE_SEC, LOG_POLL_SEC) == (10.0, 0.1)


def test_l9_the_spec_s_example(tmp_path: Path) -> None:
    first = f"{SLOT_LINE}\n".encode()
    second = f"{SLOT_LINE}\n{LISTENING_LINE}\n".encode()
    run, read = settled(tmp_path, [first, second])
    assert read() == second.decode()
    assert run.clock.sleeps == [0.1]
    assert run.files.reads == [run.server.log_path] * 2


def test_l9_a_listening_line_in_progress_does_not_count(tmp_path: Path) -> None:
    run, read = settled(tmp_path, [LISTENING_LINE.encode(), f"{LISTENING_LINE}\n".encode()])
    assert read() == f"{LISTENING_LINE}\n"
    assert run.clock.sleeps == [0.1]


def test_l9_a_cut_listening_line_does_not_count(tmp_path: Path) -> None:
    run, read = settled(tmp_path, [LISTENING_LINE[:-3].encode(), f"{LISTENING_LINE}\n".encode()])
    assert read() == f"{LISTENING_LINE}\n"
    assert run.clock.sleeps == [0.1]


def test_x6_no_listening_line_in_ten_seconds(tmp_path: Path) -> None:
    run, read = settled(tmp_path, [f"{SLOT_LINE}\n".encode()])
    assert check_failed(read) == ("log", "no listening line")
    assert LOG_SETTLE_SEC <= run.clock.now < LOG_SETTLE_SEC + 2 * LOG_POLL_SEC


def test_l9_an_exit_while_settling(tmp_path: Path) -> None:
    run, read = settled(tmp_path, [f"{SLOT_LINE}\n".encode()], FakeProcess(polls=[None, 1]))
    with pytest.raises(ServerExited) as caught:
        read()
    assert (caught.value.exit_code, caught.value.bind_failed) == (1, False)
    assert run.clock.sleeps == [0.1]


def test_x6_an_exit_while_settling_with_the_bind_line(tmp_path: Path) -> None:
    run = rig(tmp_path, FakeProcess(exit_code=1), log=LOG_BIND_FAILED)
    with pytest.raises(ServerExited) as caught:
        read_settled_log(run.server, run.tools)
    assert (caught.value.exit_code, caught.value.bind_failed) == (1, True)


def test_l9_bytes_that_are_not_utf_8_do_not_stop_the_read(tmp_path: Path) -> None:
    _run, read = settled(tmp_path, [b"\xff\xfe\n" + f"{LISTENING_LINE}\n".encode()])
    assert read() == chr(0xFFFD) * 2 + f"\n{LISTENING_LINE}\n"


# ---------------------------------------------------------------- L10, L11 parsing


GRAPH_LINE = "0.34.900.940 I sched_reserve: graph: nodes = 1269, splits = 2, input objects = 4, input tensors = 9"
GRAPH_LINE_TWO_FORMS = (
    "0.34.900.940 I sched_reserve: graph (pp bs=512, tg bs=1): nodes = 1269 / 1265, splits = 3 / 2, "
    "input objects = 4 / 4, input tensors = 9 / 9"
)


def test_l10_the_default_log() -> None:
    assert parse_startup_log(LOG_DEFAULT) == STARTUP_DEFAULT
    assert STARTUP_DEFAULT.graph_splits is None


def test_l10_the_verbose_log() -> None:
    assert parse_startup_log(LOG_VERBOSE) == STARTUP_VERBOSE
    assert STARTUP_VERBOSE.graph_splits == 2


def test_l10_the_verbose_log_with_the_recurrent_line() -> None:
    assert parse_startup_log(LOG_VERBOSE_RECURRENT) == STARTUP_RECURRENT


@pytest.mark.parametrize(
    ("line", "field", "value"),
    [
        ("0.34.900.940 I sched_reserve:      CUDA0 compute buffer size =    89.01 MiB", "cuda_compute_mib", 89.01),
        ("1.35.152.095 I load_tensors:        CUDA0 model buffer size =  4455.34 MiB", "cuda_model_mib", 4455.34),
        ("0.35.451.020 I llama_kv_cache:      CUDA0 KV buffer size =   384.00 MiB", "cuda_kv_mib", 384.0),
        (
            "0.35.467.060 I llama_memory_recurrent:      CUDA0 RS buffer size =    50.25 MiB",
            "cuda_recurrent_mib",
            50.25,
        ),
        ("0.30.098.754 I load_tensors: offloaded 34/34 layers to GPU", "layers_offloaded", 34),
        ("0.30.098.754 I load_tensors: offloaded 36/37 layers to GPU", "layers_total", 37),
        (
            "0.30.098.754 I sched_reserve:      CUDA1 compute buffer size = 12345678.00 MiB",
            "cuda_compute_mib",
            12345678.0,
        ),
    ],
)
def test_l10_one_line_gives_one_field(line: str, field: str, value: float) -> None:
    assert getattr(parse_startup_log(f"{LOG_DEFAULT}{line}\n"), field) == value


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("0.28.702.364 I load_tensors:   CPU_Mapped model buffer size =   304.28 MiB", id="cpu-mapped"),
        pytest.param("0.34.900.927 I sched_reserve:  CUDA_Host compute buffer size =    32.02 MiB", id="cuda-host"),
        pytest.param("0.33.860.343 I llama_context:  CUDA_Host  output buffer size =     0.58 MiB", id="output-buffer"),
        pytest.param(
            "0.34.197.601 I llama_kv_cache: size = 1728.00 MiB ( 12288 cells,  36 layers,  1/1 seqs)", id="kv-size"
        ),
        pytest.param("  Device 0: CUDA0 model buffer size =  2375.91 MiB", id="unprefixed"),
        pytest.param("load_tensors:        CUDA0 model buffer size =  2375.91 MiB", id="no-prefix"),
        pytest.param("0.28.702.369 W load_tensors:        CUDA0 model buffer size =  2375.91 MiB", id="warn-level"),
        pytest.param(
            "0.28.702.369 I load_tensors:        CUDA0 model buffer size =  2375.91 MiB ", id="trailing-space"
        ),
        pytest.param("x 0.28.702.369 I load_tensors:        CUDA0 model buffer size =  2375.91 MiB", id="leading-text"),
        pytest.param("0.28.702 I load_tensors:        CUDA0 model buffer size =  2375.91 MiB", id="short-stamp"),
        pytest.param("0.28.702.369 I load_tensors:        CUDA0 model buffer size =  2375.9 MiB", id="one-decimal"),
        pytest.param(
            "0.28.702.369 I load_tensors:        CUDA model buffer size =  2375.91 MiB", id="no-device-number"
        ),
        pytest.param(
            "0.28.702.369 I load_tensors: CUDA" + chr(0x0663) + " model buffer size = 1.00 MiB", id="arabic-3"
        ),
        pytest.param(chr(0x0663) + ".28.702.369 I load_tensors: offloaded 1/1 layers to GPU", id="arabic-3-in-stamp"),
        pytest.param(
            "0.43.741.446 I slot   operator(): id  0 | task 0 | new prompt, n_ctx_slot = 8192, n_keep = 0",
            id="new-prompt",
        ),
        pytest.param("0.36.319.959 I slot   load_model: id  0 | task -1 | new slot, n_ctx = 8192", id="new-slot"),
        pytest.param("0.33.835.055 I llama_context: n_ctx_seq             = 8192", id="n-ctx-seq"),
        pytest.param("0.04.849.417 I cmn  common_param: common_params_print_info: build 1 (27b7a8a)", id="build-line"),
        pytest.param("0.36.484.389 I srv  server_run_i: model loaded", id="model-loaded"),
        pytest.param("0.36.484.436 I srv  server_on_li: listening on http://0.0.0.0:18080", id="another-host"),
        pytest.param("0.01.076.513 I using config file (none)", id="not-the-config-line"),
    ],
)
def test_l10_other_lines_match_nothing(line: str) -> None:
    assert parse_startup_log(f"{LOG_DEFAULT}{line}\n") == STARTUP_DEFAULT


def test_l10_the_fixtures_keep_the_lines_a_parser_must_skip() -> None:
    for fixture in (LOG_VERBOSE, LOG_VERBOSE_RECURRENT):
        assert "CPU_Mapped model buffer size" in fixture
        assert "CUDA_Host compute buffer size" in fixture
        assert "You are a helpful assistant" in fixture
    assert "new prompt, n_ctx_slot = 12288" in LOG_VERBOSE
    assert "llama_memory_recurrent" in LOG_VERBOSE_RECURRENT
    assert "llama_memory_recurrent" not in LOG_VERBOSE
    assert "/models/candidate.gguf" in LOG_DEFAULT


@pytest.mark.parametrize("fixture", [LOG_DEFAULT, LOG_VERBOSE, LOG_VERBOSE_RECURRENT])
@pytest.mark.parametrize("ending", ["\r\n", "\r"], ids=["cr-lf", "lone-cr"])
def test_x16_line_endings_change_nothing(fixture: str, ending: str) -> None:
    assert parse_startup_log(fixture.replace("\n", ending)) == parse_startup_log(fixture)


@pytest.mark.parametrize("fixture", [LOG_DEFAULT, LOG_VERBOSE])
def test_l10_a_line_in_progress_is_not_read(fixture: str) -> None:
    marker = "listening on http://127.0.0.1:18080"
    in_progress = fixture[: fixture.index(marker) + len(marker)]
    assert check_failed(parse_startup_log, in_progress) == ("log", "LISTENING line missing")
    assert parse_startup_log(in_progress + "\n").port == PORT


@pytest.mark.parametrize(
    ("name", "marker"),
    [
        ("VERBOSITY", "verbosity = 3"),
        ("SLOTS", "n_slots = 1"),
        ("LISTENING", "listening on"),
    ],
)
def test_l10_a_line_of_every_log_is_required(name: str, marker: str) -> None:
    assert check_failed(parse_startup_log, without(LOG_DEFAULT, marker)) == ("log", f"{name} line missing")


def test_l10_an_empty_log_names_the_first_missing_line() -> None:
    assert check_failed(parse_startup_log, "") == ("log", "VERBOSITY line missing")


@pytest.mark.parametrize(
    ("name", "marker"),
    [
        ("VERBOSITY", "verbosity = 4"),
        ("SLOTS", "initializing, n_slots"),
        ("LISTENING", "listening on"),
        ("OFFLOADED", "offloaded 37/37"),
        ("MODEL", "CUDA0 model buffer"),
        ("KV", "CUDA0 KV buffer"),
        ("COMPUTE", "CUDA0 compute buffer"),
        ("GRAPH", "graph: nodes"),
    ],
)
def test_l10_a_repeated_line_is_refused(name: str, marker: str) -> None:
    line = next(line for line in LOG_VERBOSE.split("\n") if marker in line)
    assert check_failed(parse_startup_log, f"{LOG_VERBOSE}{line}\n") == ("log", f"{name} line repeated")


def test_x17_a_request_s_slot_line_is_ignored_and_a_second_load_line_is_not() -> None:
    new_prompt = "0.43.741.446 I slot   operator(): id  0 | task 0 | new prompt, n_ctx_slot = 8192, n_keep = 0"
    assert parse_startup_log(f"{LOG_DEFAULT}{new_prompt}\n").n_ctx_slot == 12288
    assert check_failed(parse_startup_log, f"{LOG_DEFAULT}{SLOT_LINE}\n") == ("log", "SLOTS line repeated")


def test_l10_a_second_device_repeats_a_line_and_fails_loud() -> None:
    second = "0.28.702.370 I load_tensors:        CUDA1 model buffer size =  1000.00 MiB"
    assert check_failed(parse_startup_log, f"{LOG_VERBOSE}{second}\n") == ("log", "MODEL line repeated")


@pytest.mark.parametrize("fixture", [LOG_DEFAULT, LOG_VERBOSE], ids=["default", "verbose"])
def test_x45_a_config_file_in_use_is_refused(fixture: str) -> None:
    line = "0.00.002.123 I using config file: /etc/llama.cpp/config.ini\n"
    assert check_failed(parse_startup_log, line + fixture) == ("config_file", "config file in use")
    assert check_failed(parse_startup_log, fixture + line) == ("config_file", "config file in use")


def test_x45_it_outranks_a_missing_line() -> None:
    line = "0.00.002.123 I using config file: /data/.config/llama.cpp/config.ini\n"
    assert check_failed(parse_startup_log, line) == ("config_file", "config file in use")


def test_l11_the_first_form_gives_one_number_twice() -> None:
    log = parse_startup_log(f"{LOG_DEFAULT}{GRAPH_LINE}\n")
    assert (log.graph_splits_pp, log.graph_splits_tg, log.graph_splits) == (2, 2, 2)


def test_x37_the_second_form_gives_two_numbers_and_their_maximum() -> None:
    log = parse_startup_log(f"{LOG_DEFAULT}{GRAPH_LINE_TWO_FORMS}\n")
    assert (log.graph_splits_pp, log.graph_splits_tg, log.graph_splits) == (3, 2, 3)


def test_l11_the_maximum_is_taken_either_way_round() -> None:
    line = GRAPH_LINE_TWO_FORMS.replace("splits = 3 / 2", "splits = 2 / 5")
    assert parse_startup_log(f"{LOG_DEFAULT}{line}\n").graph_splits == 5


def test_l11_zero_splits_are_a_number() -> None:
    line = GRAPH_LINE_TWO_FORMS.replace("splits = 3 / 2", "splits = 1 / 0")
    log = parse_startup_log(f"{LOG_DEFAULT}{line}\n")
    assert (log.graph_splits_pp, log.graph_splits_tg) == (1, 0)


# ---------------------------------------------------------------- L12 the log checks


def test_l12_a_timed_launch_gives_only_context_per_slot(tmp_path: Path) -> None:
    assert verify(rig(tmp_path, log=LOG_DEFAULT), mode="timed") == STARTUP_DEFAULT


def test_l12_a_pre_run_launch_gives_everything(tmp_path: Path) -> None:
    assert verify(rig(tmp_path, log=LOG_VERBOSE), mode="pre_run") == STARTUP_VERBOSE


def test_l12_the_recurrent_line_is_optional(tmp_path: Path) -> None:
    assert verify(rig(tmp_path, log=LOG_VERBOSE_RECURRENT), mode="pre_run") == STARTUP_RECURRENT


@pytest.mark.parametrize(
    ("log", "mode", "detail"),
    [
        pytest.param(LOG_DEFAULT, "pre_run", "log verbosity=3 expected=4", id="default-log-on-a-pre-run"),
        pytest.param(LOG_VERBOSE, "timed", "log verbosity=4 expected=3", id="verbose-log-on-a-timed-launch"),
    ],
)
def test_l12_the_log_proves_which_mode_reached_the_server(
    tmp_path: Path, log: str, mode: LaunchMode, detail: str
) -> None:
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(rig(tmp_path, log=log), mode=mode)
    assert (caught.value.check, caught.value.detail) == ("verbosity", detail)


@pytest.mark.parametrize(
    ("name", "marker"),
    [
        ("OFFLOADED", "offloaded 37/37"),
        ("GRAPH", "graph: nodes"),
        ("MODEL", "CUDA0 model buffer"),
        ("KV", "CUDA0 KV buffer"),
        ("COMPUTE", "CUDA0 compute buffer"),
    ],
)
def test_x15_a_missing_start_up_line_fails_a_pre_run(tmp_path: Path, name: str, marker: str) -> None:
    run = rig(tmp_path, log=without(LOG_VERBOSE, marker))
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(run, mode="pre_run")
    assert str(caught.value) == f"log: {name} line missing"


def test_x18_layers_short_of_n_of_n(tmp_path: Path) -> None:
    run = rig(tmp_path, log=swap(LOG_VERBOSE, "offloaded 37/37", "offloaded 36/37"))
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(run, mode="pre_run")
    assert (caught.value.check, caught.value.detail) == ("layers", "36 of 37")


@pytest.mark.parametrize(
    "line",
    [
        pytest.param("splits = 3,", id="x19-three-splits"),
        pytest.param("splits = 40,", id="forty-splits"),
    ],
)
def test_x19_graph_splits_above_2_are_recorded_and_never_fail(tmp_path: Path, line: str) -> None:
    startup = verify(rig(tmp_path, log=swap(LOG_VERBOSE, "splits = 2,", line)), mode="pre_run")
    assert startup.graph_splits == int(line.split()[2].rstrip(","))


def test_x37_the_second_graph_form_passes_a_pre_run(tmp_path: Path) -> None:
    run = rig(tmp_path, log=swap(LOG_VERBOSE, GRAPH_LINE, GRAPH_LINE_TWO_FORMS))
    assert verify(run, mode="pre_run").graph_splits == 3


@pytest.mark.parametrize(
    ("old", "new", "check", "detail"),
    [
        pytest.param("n_slots = 1", "n_slots = 2", "slots", "log n_slots=2 expected=1", id="two-slots"),
        pytest.param(
            "n_ctx_slot = 12288, kv",
            "n_ctx_slot = 8192, kv",
            "context",
            "log n_ctx_slot=8192 expected=12288",
            id="context",
        ),
        pytest.param("127.0.0.1:18080", "127.0.0.1:18081", "port", "log port=18081 expected=18080", id="another-port"),
    ],
)
@pytest.mark.parametrize(("log", "mode"), [(LOG_DEFAULT, "timed"), (LOG_VERBOSE, "pre_run")], ids=["timed", "pre-run"])
def test_l12_the_checks_of_both_modes(
    tmp_path: Path, log: str, mode: LaunchMode, old: str, new: str, check: str, detail: str
) -> None:
    run = rig(tmp_path, log=swap(log, old, new))
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(run, mode=mode)
    assert (caught.value.check, caught.value.detail) == (check, detail)


def test_x45_a_config_file_fails_a_launch(tmp_path: Path) -> None:
    run = rig(tmp_path, log="0.00.002.123 I using config file: /etc/llama.cpp/config.ini\n" + LOG_DEFAULT)
    assert check_failed(verify, run) == ("config_file", "config file in use")


# ---------------------------------------------------------------- L13, L14 /props and the template


def test_l13_a_clean_server_passes() -> None:
    check_props(server_props(), CONDITIONS, CANDIDATE)


@pytest.mark.parametrize(
    ("changes", "check", "detail"),
    [
        pytest.param({"n_ctx": 8192}, "context", "props n_ctx=8192 expected=12288", id="x20-wrong-context"),
        pytest.param({"total_slots": 2}, "slots", "props total_slots=2 expected=1", id="two-slots"),
        pytest.param(
            {"build_info": "b11936-27b7a8a0"}, "build", "build_info differs from b1-27b7a8a", id="x21-wrong-build"
        ),
        pytest.param({"build_info": ""}, "build", "build_info differs from b1-27b7a8a", id="empty-build"),
        pytest.param(
            {"model_path": "/models/other.gguf"},
            "model_path",
            "model_path differs from the launched path",
            id="another-model",
        ),
        pytest.param({"modalities": Modalities(True, False, False)}, "modalities", "on: vision", id="x22-vision"),
        pytest.param({"modalities": Modalities(False, True, False)}, "modalities", "on: video", id="video"),
        pytest.param({"modalities": Modalities(False, False, True)}, "modalities", "on: audio", id="audio"),
        pytest.param({"modalities": Modalities(True, False, True)}, "modalities", "on: vision, audio", id="two-on"),
        pytest.param({"chat_template": "no tags"}, "think_tags", "the template lacks a think tag", id="x49-no-tags"),
        pytest.param({"chat_template": "<think>"}, "think_tags", "the template lacks a think tag", id="x49-one-tag"),
        pytest.param({"chat_template": "</think>"}, "think_tags", "the template lacks a think tag", id="x49-other-tag"),
        pytest.param({"chat_template": ""}, "think_tags", "the template lacks a think tag", id="x49-empty"),
    ],
)
def test_l13_each_check(changes: dict[str, Any], check: str, detail: str) -> None:
    assert check_failed(check_props, server_props(**changes), CONDITIONS, CANDIDATE) == (check, detail)


def test_l13_the_pin_is_the_string_as_reported() -> None:
    assert PINNED_BUILD_INFO == "b1-27b7a8a"
    assert THINK_TAGS == ("<think>", "</think>")


def test_l13_the_model_path_is_compared_with_the_launched_string() -> None:
    other = candidate(gguf_path="/models/other.gguf")
    check_props(server_props(model_path="/models/other.gguf"), CONDITIONS, other)
    assert check_failed(check_props, server_props(), CONDITIONS, other)[0] == "model_path"


def test_x20_a_wrong_context_in_props_with_a_correct_log(tmp_path: Path) -> None:
    run = rig(tmp_path)
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(run, props=server_props(n_ctx=8192))
    assert str(caught.value) == "context: props n_ctx=8192 expected=12288"


def test_l13_the_details_hold_no_server_text() -> None:
    server_text = "SERVER-TEXT"
    for changes in ({"build_info": server_text}, {"model_path": server_text}, {"chat_template": server_text}):
        assert server_text not in check_failed(check_props, server_props(**changes), CONDITIONS, CANDIDATE)[1]


@pytest.mark.parametrize(
    ("template", "parser"),
    [
        pytest.param("{{ '<tool_call>' }}<think></think>", "auto", id="tool-call-tag-only"),
        pytest.param("<tool_call><function=x><parameter=y>", "qwen3_coder", id="all-three"),
        pytest.param("<function=x><parameter=y>", "auto", id="no-tool-call-tag"),
        pytest.param("<tool_call><parameter=y>", "auto", id="no-function-tag"),
        pytest.param("<tool_call><function=x>", "auto", id="no-parameter-tag"),
        pytest.param(
            "<tool_call><function=x><parameter=y> '<tool_call><function=' ~ tool_call.name ~ '>'",
            "auto",
            id="the-excluded-literal",
        ),
        pytest.param("", "auto", id="empty"),
    ],
)
def test_l14_template_parser(template: str, parser: str) -> None:
    assert template_parser(template) == parser


def test_l14_a_parser_is_a_record_never_a_failure() -> None:
    template = "<think></think><tool_call><function=x><parameter=y>"
    check_props(server_props(chat_template=template), CONDITIONS, CANDIDATE)


# ---------------------------------------------------------------- L15, L16 the model file


def model_tools(*, resolved: str | None = None, chunks: Any = (b"ab", b"c")) -> LaunchTools:
    """Tools whose `realpath` gives `resolved`, by default the path itself, and whose file reads as `chunks`."""

    def read_chunks(_path: str) -> Any:
        if isinstance(chunks, BaseException):
            raise chunks
        return iter(chunks)

    return launch_tools(realpath=lambda path: resolved or path, read_chunks=read_chunks)


def abc_candidate(path: str = MODEL_PATH) -> Candidate:
    return candidate(gguf_path=path, gguf_sha256=ABC_SHA256)


def test_l16_the_spec_s_example() -> None:
    assert sha256_file(MODEL_PATH, model_tools()) == ABC_SHA256
    assert check_model_file(abc_candidate(), model_tools()) == ABC_SHA256


def test_l16_a_mismatch_is_a_model_hash_failure() -> None:
    with pytest.raises(ModelFileError) as caught:
        check_model_file(candidate(gguf_sha256="b" * 64), model_tools())
    assert caught.value.check == "model_hash"
    assert str(caught.value) == "model_hash"


def test_l16_the_hash_covers_every_chunk_in_order() -> None:
    assert sha256_file(MODEL_PATH, model_tools(chunks=(b"a", b"b", b"c"))) == ABC_SHA256
    assert sha256_file(MODEL_PATH, model_tools(chunks=(b"c", b"b", b"a"))) != ABC_SHA256
    assert sha256_file(MODEL_PATH, model_tools(chunks=())) == hashlib.sha256(b"").hexdigest()


def test_l16_the_default_reader_goes_in_chunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert HASH_CHUNK_BYTES == 1 << 20
    path = tmp_path / "model.bin"
    path.write_bytes(b"abcdefghij")
    monkeypatch.setattr(launch_module, "HASH_CHUNK_BYTES", 4)
    assert list(LaunchTools().read_chunks(str(path))) == [b"abcd", b"efgh", b"ij"]
    assert sha256_file(str(path), LaunchTools()) == hashlib.sha256(b"abcdefghij").hexdigest()


def test_l16_an_os_error_propagates_from_sha256_file() -> None:
    with pytest.raises(FileNotFoundError):
        sha256_file(BINARY_PATH, model_tools(chunks=FileNotFoundError("no binary")))


@pytest.mark.parametrize("error", [FileNotFoundError("gone"), PermissionError("denied"), OSError("io error")])
def test_amb_94_a_model_file_that_cannot_be_read_cannot_match(error: OSError) -> None:
    with pytest.raises(ModelFileError) as caught:
        check_model_file(abc_candidate(), model_tools(chunks=error))
    assert caught.value.check == "model_hash"


def test_amb_94_a_read_that_fails_half_way_cannot_match() -> None:
    def chunks(_path: str) -> Any:
        yield b"ab"
        raise OSError("io error")

    with pytest.raises(ModelFileError) as caught:
        check_model_file(abc_candidate(), launch_tools(realpath=lambda path: path, read_chunks=chunks))
    assert caught.value.check == "model_hash"


@pytest.mark.parametrize(
    ("path", "resolved"),
    [
        pytest.param("/mnt/c/m.gguf", None, id="x14-mnt-c"),
        pytest.param("/mnt/wsl/m.gguf", None, id="x14-mnt-wsl"),
        pytest.param("/mnt/m.gguf", None, id="mnt-itself"),
        pytest.param("/data/m.gguf", "/mnt/c/m.gguf", id="x14-a-link-to-mnt"),
        pytest.param("/mnt/c/m.gguf", "/models/m.gguf", id="the-path-itself-under-mnt"),
        pytest.param("/data/m.gguf", "C:" + chr(92) + "m.gguf", id="x14-a-windows-path"),
        pytest.param("/data/m.gguf", "models/m.gguf", id="resolves-to-a-relative-path"),
        pytest.param("/data/m.gguf", "//mnt/c/m.gguf", id="two-leading-slashes"),
    ],
)
def test_x14_a_model_under_mnt_is_neither_hashed_nor_launched(path: str, resolved: str | None) -> None:
    tools = launch_tools(realpath=lambda given: resolved or given)  # read_chunks refuses, so a hash would fail the test
    with pytest.raises(ModelFileError) as caught:
        check_model_file(candidate(gguf_path=path), tools)
    assert caught.value.check == "model_location"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/models/m.gguf", id="x14-models"),
        pytest.param("/mntx/m.gguf", id="x14-a-prefix-is-not-a-part"),
        pytest.param("/data/mnt/c/m.gguf", id="x14-not-the-first-part"),
        pytest.param("/MNT/c/m.gguf", id="case-is-kept"),
    ],
)
def test_x14_a_model_elsewhere_passes(path: str) -> None:
    assert check_model_file(abc_candidate(path), model_tools()) == ABC_SHA256


def test_l15_the_path_is_checked_before_any_byte_is_read() -> None:
    order: list[str] = []

    def realpath(path: str) -> str:
        order.append("resolve")
        return path

    def read_chunks(_path: str) -> Any:
        order.append("hash")
        return iter((b"abc",))

    assert check_model_file(abc_candidate(), launch_tools(realpath=realpath, read_chunks=read_chunks)) == ABC_SHA256
    assert order == ["resolve", "hash"]


def test_l16_a_timed_launch_never_touches_the_model_file(tmp_path: Path) -> None:
    run = rig(tmp_path)  # `launch_tools` refuses both `realpath` and `read_chunks`
    assert verify(run) == STARTUP_DEFAULT


# ---------------------------------------------------------------- L17 the interfaces


def test_l17_only_loopback_and_only_proc_net_dev_is_read() -> None:
    files = FakeFiles({PROC_NET_DEV: NET_HEADER + NET_LO})
    assert check_loopback_only(launch_tools(files=files)) == ("lo",)
    assert files.reads == [PROC_NET_DEV]


@pytest.mark.parametrize(
    ("raw", "names"),
    [
        pytest.param(NET_HEADER + NET_LO, ("lo",), id="lo"),
        pytest.param(NET_HEADER + NET_LO + NET_ETH0, ("lo", "eth0"), id="two"),
        pytest.param(NET_HEADER, (), id="no-interface-line"),
        pytest.param(b"", (), id="empty"),
        pytest.param(NET_HEADER + b"\n  \n" + NET_LO + b"\n", ("lo",), id="blank-lines"),
        pytest.param(NET_HEADER + b"lo:0 0\n", ("lo",), id="no-space-after-the-colon"),
        pytest.param(NET_HEADER + b"  veth1a2b: 1 2\n", ("veth1a2b",), id="another-name"),
    ],
)
def test_l17_interface_names(raw: bytes, names: tuple[str, ...]) -> None:
    assert interface_names(raw) == names


def test_l17_a_line_with_no_colon_fails() -> None:
    assert check_failed(interface_names, NET_HEADER + b"    lo 0 0\n")[0] == "interfaces"


@pytest.mark.parametrize(
    ("raw", "detail"),
    [
        pytest.param(NET_HEADER + NET_LO + NET_ETH0, "found: lo, eth0", id="eth0-too"),
        pytest.param(NET_HEADER + NET_ETH0, "found: eth0", id="only-eth0"),
        pytest.param(NET_HEADER, "found: none", id="no-interface-line"),
    ],
)
def test_l17_anything_but_loopback_fails(raw: bytes, detail: str) -> None:
    tools = launch_tools(files=FakeFiles({PROC_NET_DEV: raw}))
    assert check_failed(check_loopback_only, tools) == ("interfaces", detail)


# ---------------------------------------------------------------- L18 the order of the checks


def test_l18_a_wrong_command_line_raises_before_the_log_is_read(tmp_path: Path) -> None:
    run = rig(tmp_path, cmdline=cmdline_bytes(ARGV[:-1]))
    assert check_failed(verify, run)[0] == "cmdline"
    assert run.files.reads == ["/proc/4242/cmdline"]


def test_l18_the_log_comes_before_props(tmp_path: Path) -> None:
    run = rig(tmp_path, log=swap(LOG_DEFAULT, "n_ctx_slot = 12288", "n_ctx_slot = 8192"))
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(run, props=server_props(n_ctx=8192))
    assert caught.value.detail == "log n_ctx_slot=8192 expected=12288"


def test_l18_the_check_order_is_the_same_in_both_modes(tmp_path: Path) -> None:
    log = swap(LOG_VERBOSE, "n_ctx_slot = 12288, kv", "n_ctx_slot = 8192, kv")
    run = rig(tmp_path, log=log)
    with pytest.raises(LaunchCheckFailed) as caught:
        verify(run, mode="pre_run", props=server_props(n_ctx=8192))
    assert caught.value.detail == "log n_ctx_slot=8192 expected=12288"


def test_l18_a_raised_context_reaches_every_check_with_no_other_change(tmp_path: Path) -> None:
    raised = conditions(context=16384)
    argv = frozen_argv(raised, CANDIDATE)
    assert argv[4] == "16384"
    run = rig(tmp_path, log=swap(LOG_DEFAULT, "n_ctx_slot = 12288", "n_ctx_slot = 16384"), argv=argv)
    assert verify(run, at=raised, props=server_props(n_ctx=16384)).n_ctx_slot == 16384


def test_l18_the_mode_is_checked_first(tmp_path: Path) -> None:
    run = rig(tmp_path)
    with pytest.raises(ValueError):
        verify(run, mode="pre-run")  # type: ignore[arg-type]
    assert run.files.reads == []


@pytest.mark.parametrize(
    ("mode", "log", "expected", "environment"),
    [
        pytest.param("timed", LOG_DEFAULT, STARTUP_DEFAULT, {"HOME", "PATH"}, id="timed"),
        pytest.param("pre_run", LOG_VERBOSE, STARTUP_VERBOSE, {"HOME", "PATH", VERBOSITY_ENV}, id="pre-run"),
        pytest.param(
            "pre_run", LOG_VERBOSE_RECURRENT, STARTUP_RECURRENT, {"HOME", "PATH", VERBOSITY_ENV}, id="recurrent"
        ),
    ],
)
def test_a_whole_launch(
    tmp_path: Path, mode: LaunchMode, log: str, expected: StartupLog, environment: set[str]
) -> None:
    process = FakeProcess(waits=[0])
    popen = FakePopen(process, log=log)
    files = FakeFiles({"/proc/4242/cmdline": cmdline_bytes(ARGV)})
    tools = launch_tools(popen, files)
    server = ServerProcess(ARGV, server_environment(CONDITIONS, mode), str(tmp_path / "server.log"), tools)
    client, http = make_client(httpx.ConnectError, loading_response(), props_response())
    props, seconds = wait_ready(server, client, CONDITIONS, tools)
    assert verify_launch(server, props, CONDITIONS, CANDIDATE, mode, tools) == expected
    assert server.stop() == StopResult(0, "terminated")
    assert seconds == 2.0
    assert len(http.requests) == 3
    assert set(popen.calls[0][1]["env"]) == environment
    assert popen.calls[0][0] == list(ARGV)
    assert files.reads == ["/proc/4242/cmdline", server.log_path]


# ---------------------------------------------------------------- the exceptions and the records


@pytest.mark.parametrize(
    "error",
    [
        SpawnFailed("PermissionError"),
        ServerExited(-11, True),
        ReadyTimeout(600.0),
        LaunchCheckFailed("context", "props n_ctx=8192 expected=12288"),
        ServerStopFailed(),
        ModelFileError("model_hash"),
    ],
    ids=lambda error: type(error).__name__,
)
@pytest.mark.parametrize("clone", [copy.copy, lambda error: pickle.loads(pickle.dumps(error))], ids=["copy", "pickle"])
def test_an_exception_survives_a_copy_with_every_field(error: Exception, clone: Any) -> None:
    again = clone(error)
    assert type(again) is type(error)
    assert (str(again), again.args, vars(again)) == (str(error), error.args, vars(error))


def test_the_exception_tree() -> None:
    assert issubclass(ConfigError, ValueError)
    assert not issubclass(ModelFileError, LaunchError)
    for error in (SpawnFailed, ServerExited, ReadyTimeout, LaunchCheckFailed, ServerStopFailed):
        assert issubclass(error, LaunchError)
    assert issubclass(LaunchError, RuntimeError)


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (SpawnFailed("PermissionError"), "spawn failed: PermissionError"),
        (ServerExited(-11, False), "server exited with code -11"),
        (ReadyTimeout(600.0), "not ready after 600 s"),
        (ReadyTimeout(90.5), "not ready after 90.5 s"),
        (LaunchCheckFailed("layers", "36 of 37"), "layers: 36 of 37"),
        (ServerStopFailed(), "still running after kill"),
    ],
)
def test_d12_str_is_the_fault_s_detail(error: LaunchError, detail: str) -> None:
    assert str(error) == detail


def test_a_record_holds_the_module_s_own_startup_log() -> None:
    assert records_module.StartupLog is StartupLog
    assert records_module.CANDIDATE_IDS is CANDIDATE_IDS
    assert records_module.CANDIDATE_SLUGS is CANDIDATE_SLUGS
    assert records_module.LaunchMode is LaunchMode


@pytest.mark.parametrize(
    "startup", [STARTUP_DEFAULT, STARTUP_VERBOSE, STARTUP_RECURRENT], ids=["default", "verbose", "recurrent"]
)
def test_a_parsed_log_stores_exactly(startup: StartupLog) -> None:
    stored = record_loads(record_json(dataclasses.asdict(startup)))
    assert StartupLog(**stored) == startup


def test_stop_result_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        StopResult(0, "terminated").exit_code = 1  # type: ignore[misc]


# ---------------------------------------------------------------- T6 the fakes themselves


def test_t6_the_fake_popen_refuses_a_shell() -> None:
    popen = FakePopen(FakeProcess())
    with pytest.raises(AssertionError, match="no shell"):
        popen(list(ARGV), shell=True, stdout=None)
    assert popen.calls == []


def test_t6_the_fake_process_refuses_a_signal_after_exit() -> None:
    process = FakeProcess(exit_code=1)
    with pytest.raises(AssertionError, match="terminate"):
        process.terminate()
    with pytest.raises(AssertionError, match="kill"):
        process.kill()


def test_t6_the_fake_process_ends_when_a_wait_gives_a_code() -> None:
    process = FakeProcess(waits=[None, 7])
    with pytest.raises(subprocess.TimeoutExpired):
        process.wait(timeout=1.0)
    assert process.poll() is None
    assert process.wait(timeout=2.0) == 7
    assert process.poll() == 7
    assert process.wait_timeouts == [1.0, 2.0]


def test_t6_the_fake_files_refuse_the_real_proc() -> None:
    with pytest.raises(AssertionError, match="real /proc/1/cmdline"):
        FakeFiles()("/proc/1/cmdline")


def test_t6_a_list_of_reads_goes_in_turn_and_its_last_repeats() -> None:
    files = FakeFiles({"/x": [b"1", OSError("once"), b"2"]})
    assert files("/x") == b"1"
    with pytest.raises(OSError, match="once"):
        files("/x")
    assert [files("/x"), files("/x")] == [b"2", b"2"]
    assert files.reads == ["/x"] * 4


def test_t6_the_fake_tools_refuse_a_real_file() -> None:
    tools = launch_tools()
    with pytest.raises(AssertionError, match="must not resolve"):
        tools.realpath(MODEL_PATH)
    with pytest.raises(AssertionError, match="must not hash"):
        tools.read_chunks(MODEL_PATH)


def test_t6_the_props_fixture_is_the_pinned_shape() -> None:
    client, _server = make_client(props_response())
    props = client.fetch_props()
    assert props == server_props()
    check_props(props, CONDITIONS, CANDIDATE)


@pytest.mark.parametrize("fixture", [LOG_DEFAULT, LOG_VERBOSE, LOG_VERBOSE_RECURRENT, LOG_BIND_FAILED])
def test_t6_the_log_fixtures_end_their_last_line_and_hold_no_cr(fixture: str) -> None:
    assert fixture.endswith("\n")
    assert "\r" not in fixture
