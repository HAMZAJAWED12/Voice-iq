"""The public launch helper `launch.LaunchedServer`: H5 rows L-1 to L-6 (S2 §3.2, §3.3, §11 item 5).

No test starts a process or opens a socket. `FakeHost` stands for the machine: `popen`, `/proc`, the model files and
one `FakeModelServer` per launch behind `httpx.MockTransport`. Server logs are real files under `tmp_path`.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.bakeoff import launch as launch_module
from app.evaluation.bakeoff.client import ModelServerClient
from app.evaluation.bakeoff.inputs import FrozenInputs
from app.evaluation.bakeoff.launch import (
    Candidate,
    LaunchCheckFailed,
    LaunchMode,
    LaunchTools,
    ModelFileError,
    ReadyTimeout,
    RunConditions,
    ServerStopFailed,
    base_url,
    frozen_argv,
    server_environment,
)
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._h5_fixtures import h5_frozen_inputs
from app.evaluation.tests._runner_fixtures import (
    BINARY_PATH,
    CONDITIONS,
    NET_DEV_LOOPBACK,
    NET_ETH0,
    PORT,
    SCHEDULE_CANDIDATES,
    SERVER_HOME,
    FakeHost,
    ServerScript,
    block_processes,
    loading_response,
)

CANDIDATE = SCHEDULE_CANDIDATES[0]
PRE_RUN = "qwen3-4b.pre_run.a1"
TIMED = "qwen3-4b.run1.a1"
UNITS: dict[str, str] = {"pre_run": PRE_RUN, "timed": TIMED}


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> FrozenInputs:
    return h5_frozen_inputs(tmp_path_factory.mktemp("gold"))


def make_host(inputs: FrozenInputs, scripts: dict[str, ServerScript] | None = None, **options: Any) -> FakeHost:
    return FakeHost(inputs, scripts, **options)


def launched(
    host: FakeHost,
    tmp_path: Path,
    *,
    mode: LaunchMode = "pre_run",
    conditions: RunConditions = CONDITIONS,
    candidate: Candidate = CANDIDATE,
    tools: LaunchTools | None = None,
    client_factory: Any = None,
) -> Any:
    """A helper over `host`, with its log under `tmp_path` named after the launch."""
    return launch_module.LaunchedServer(
        conditions,
        candidate,
        mode=mode,
        log_path=str(tmp_path / f"{UNITS[mode]}.log"),
        tools=host.tools() if tools is None else tools,
        client_factory=host.client if client_factory is None else client_factory,
    )


def logs(tmp_path: Path) -> list[str]:
    return sorted(path.name for path in tmp_path.glob("*.log"))


# ---------------------------------------------------------------- L-1 order


def test_l1_a_clean_launch_gives_the_checked_server_and_stops_it_on_exit(inputs: FrozenInputs, tmp_path: Path) -> None:
    host = make_host(inputs)
    with launched(host, tmp_path) as server:
        assert isinstance(server.client, ModelServerClient)
        assert server.props.model_path == CANDIDATE.gguf_path
        assert server.props.n_ctx == CONDITIONS.context
        assert server.startup.verbosity == 4
        assert server.startup.layers_offloaded == server.startup.layers_total
        assert server.model_sha256 == CANDIDATE.gguf_sha256
        assert server.ready_sec == 0.0
        assert host.events == [("popen", PRE_RUN)]
        assert host.closed == 0
    assert host.events == [("popen", PRE_RUN), ("terminate", PRE_RUN)]
    assert host.closed == 1
    assert logs(tmp_path) == [f"{PRE_RUN}.log"]


def test_l1_the_loopback_check_then_the_model_file_check_come_before_the_spawn(
    inputs: FrozenInputs, tmp_path: Path
) -> None:
    host = make_host(inputs)
    base = host.tools()
    seen: list[tuple[str, str]] = []

    def read_bytes(path: str) -> bytes:
        seen.append(("read", path))
        return base.read_bytes(path)

    def read_chunks(path: str) -> Any:
        seen.append(("hash", path))
        return base.read_chunks(path)

    def popen(args: list[str], **kwargs: Any) -> Any:
        seen.append(("popen", args[0]))
        return base.popen(args, **kwargs)

    tools = dataclasses.replace(base, read_bytes=read_bytes, read_chunks=read_chunks, popen=popen)
    with launched(host, tmp_path, tools=tools):
        pass
    assert seen[:3] == [("read", "/proc/net/dev"), ("hash", CANDIDATE.gguf_path), ("popen", BINARY_PATH)]


def test_l1_a_failed_loopback_check_starts_nothing_and_hashes_nothing(inputs: FrozenInputs, tmp_path: Path) -> None:
    host = make_host(inputs, net_dev=NET_DEV_LOOPBACK + NET_ETH0)
    with pytest.raises(LaunchCheckFailed) as caught:
        with launched(host, tmp_path):
            raise AssertionError("the body must not run")
    assert caught.value.check == "interfaces"
    assert host.launches == {}
    assert host.hashed == []
    assert host.clients == []
    assert logs(tmp_path) == []


@pytest.mark.parametrize(
    ("changes", "check"),
    [({"gguf_sha256": "d" * 64}, "model_hash"), ({"gguf_path": "/mnt/c/models/candidate.gguf"}, "model_location")],
)
def test_l1_a_failed_model_file_check_starts_nothing(
    inputs: FrozenInputs, tmp_path: Path, changes: dict[str, str], check: str
) -> None:
    host = make_host(inputs)
    with pytest.raises(ModelFileError) as caught:
        with launched(host, tmp_path, candidate=dataclasses.replace(CANDIDATE, **changes)):
            raise AssertionError("the body must not run")
    assert caught.value.check == check
    assert host.launches == {}
    assert host.clients == []
    assert logs(tmp_path) == []


def test_l1_an_existing_log_starts_nothing(inputs: FrozenInputs, tmp_path: Path) -> None:
    host = make_host(inputs)
    (tmp_path / f"{PRE_RUN}.log").write_bytes(b"kept")
    with pytest.raises(FileExistsError):
        with launched(host, tmp_path):
            raise AssertionError("the body must not run")
    assert host.launches == {}
    assert host.clients == []
    assert (tmp_path / f"{PRE_RUN}.log").read_bytes() == b"kept"


# ---------------------------------------------------------------- L-2 argv and environment


@pytest.mark.parametrize("mode", ["pre_run", "timed"])
def test_l2_the_argv_is_the_frozen_line_and_the_environment_is_built_from_nothing(
    inputs: FrozenInputs, tmp_path: Path, mode: LaunchMode
) -> None:
    host = make_host(inputs)
    with launched(host, tmp_path, mode=mode) as server:
        assert server.startup.verbosity == (4 if mode == "pre_run" else 3)
    args, kwargs = host.launches[UNITS[mode]]
    assert args == list(frozen_argv(CONDITIONS, CANDIDATE))
    assert kwargs["env"] == server_environment(CONDITIONS, mode)
    assert kwargs["shell"] is False
    assert host.clients == [(base_url(CONDITIONS), CONDITIONS.context)]


def test_l2_the_context_comes_from_the_conditions(inputs: FrozenInputs, tmp_path: Path) -> None:
    host = make_host(inputs)
    conditions = RunConditions(BINARY_PATH, SERVER_HOME, PORT, context=16_384)
    with launched(host, tmp_path, conditions=conditions) as server:
        assert server.props.n_ctx == 16_384
        assert server.startup.n_ctx_slot == 16_384
    args, _kwargs = host.launches[PRE_RUN]
    assert args == list(frozen_argv(conditions, CANDIDATE))
    assert host.clients == [(base_url(conditions), 16_384)]


# ---------------------------------------------------------------- L-3 every way out closes and stops


@pytest.mark.parametrize("error", [RuntimeError("in the body"), KeyboardInterrupt()])
def test_l3_an_exception_in_the_body_closes_the_client_and_stops_the_process(
    inputs: FrozenInputs, tmp_path: Path, error: BaseException
) -> None:
    host = make_host(inputs)
    with pytest.raises(type(error)):
        with launched(host, tmp_path):
            raise error
    assert host.closed == 1
    assert host.events == [("popen", PRE_RUN), ("terminate", PRE_RUN)]


@pytest.mark.parametrize(
    ("script", "error"),
    [
        (ServerScript(props={"build_info": "other"}), LaunchCheckFailed),
        (ServerScript(props_states=(KeyboardInterrupt(),)), KeyboardInterrupt),
        (ServerScript(cmdline=b"other\0"), LaunchCheckFailed),
    ],
)
def test_l3_a_failure_after_the_spawn_closes_the_client_and_stops_the_process(
    inputs: FrozenInputs, tmp_path: Path, script: ServerScript, error: type[BaseException]
) -> None:
    host = make_host(inputs, {PRE_RUN: script})
    with pytest.raises(error):
        with launched(host, tmp_path):
            raise AssertionError("the body must not run")
    assert host.closed == 1
    assert host.events == [("popen", PRE_RUN), ("terminate", PRE_RUN)]


@pytest.mark.parametrize("error", [RuntimeError("no client"), KeyboardInterrupt()])
def test_l3_a_failing_client_factory_still_stops_the_process(
    inputs: FrozenInputs, tmp_path: Path, error: BaseException
) -> None:
    host = make_host(inputs)

    def factory(_base_url: str, _n_ctx: int) -> ModelServerClient:
        raise error

    with pytest.raises(type(error)):
        with launched(host, tmp_path, client_factory=factory):
            raise AssertionError("the body must not run")
    assert host.events == [("popen", PRE_RUN), ("terminate", PRE_RUN)]


# ---------------------------------------------------------------- L-4 a failed stop


def test_l4_a_failed_stop_on_exit_propagates_after_the_client_is_closed(inputs: FrozenInputs, tmp_path: Path) -> None:
    host = make_host(inputs, {PRE_RUN: ServerScript(waits=(None, None))})
    with pytest.raises(ServerStopFailed):
        with launched(host, tmp_path):
            pass
    assert host.closed == 1
    assert host.events == [("popen", PRE_RUN), ("terminate", PRE_RUN), ("kill", PRE_RUN)]


# ---------------------------------------------------------------- L-5 a ready timeout


def test_l5_a_ready_timeout_closes_the_client_and_stops_the_process(inputs: FrozenInputs, tmp_path: Path) -> None:
    host = make_host(inputs, {PRE_RUN: ServerScript(props_states=tuple(loading_response() for _ in range(5)))})
    conditions = RunConditions(BINARY_PATH, SERVER_HOME, PORT, ready_deadline_sec=2.0)
    with pytest.raises(ReadyTimeout):
        with launched(host, tmp_path, conditions=conditions):
            raise AssertionError("the body must not run")
    assert host.closed == 1
    assert host.events == [("popen", PRE_RUN), ("terminate", PRE_RUN)]


# ---------------------------------------------------------------- L-6 the log path is required


@pytest.mark.parametrize("log_path", ["", None])
def test_l6_an_empty_log_path_is_refused_before_anything_starts(
    inputs: FrozenInputs, tmp_path: Path, log_path: Any
) -> None:
    host = make_host(inputs)
    with pytest.raises(ValueError, match="log_path"):
        launch_module.LaunchedServer(
            CONDITIONS, CANDIDATE, mode="pre_run", log_path=log_path, tools=host.tools(), client_factory=host.client
        )
    assert host.files.reads == []
    assert host.hashed == []
    assert host.launches == {}
    assert host.clients == []
    assert list(tmp_path.iterdir()) == []
