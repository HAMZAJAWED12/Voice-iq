"""The demo's live command line (demo specification; S2 §3.2, §3.3, §3.12): the launch config, the Qwen/Qwen3-4B-only
rule, the launch through `launch.LaunchedServer`, the output directory and the exclusive-create writes.

No test starts a process or opens a socket. The H3 `FakeHost` stands for the machine: `popen`, `/proc`, the model file
and one `FakeModelServer` behind `httpx.MockTransport`. Outputs and the server log are real files under `tmp_path`.
"""

from __future__ import annotations

import dataclasses
import html
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.evaluation.bakeoff.launch import CANDIDATE_IDS, Candidate, frozen_argv, server_environment
from app.evaluation.bakeoff.records import record_json, record_loads
from app.evaluation.demo import __main__ as demo_main
from app.evaluation.demo.render import LABEL
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._demo_fixtures import DEMO_PROMPTS, extraction_json, ok, probe_json, write_fixtures
from app.evaluation.tests._h5_fixtures import h5_input_bytes, write_h5_inputs
from app.evaluation.tests._runner_fixtures import (
    CONDITIONS,
    NET_DEV_LOOPBACK,
    NET_ETH0,
    SCHEDULE_CANDIDATES,
    FakeHost,
    ServerScript,
    block_processes,
    no_fsync,
)

NOW = datetime(2026, 11, 28, 21, 5, tzinfo=timezone.utc)
STEM = "demo-20261128T210500Z"
UNIT = f"{STEM}.server"
CANDIDATE = SCHEDULE_CANDIDATES[0]


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


class _NoFrozenRequests:
    """`FakeHost` maps a chat body to a frozen bake-off request; the demo sends none, so every answer is scripted."""

    def all_requests(self) -> tuple[()]:
        return ()


def launch_config(*candidates: Candidate) -> bytes:
    """The `context` launch config shape, by default with the one candidate the demo runs."""
    conditions = {name: getattr(CONDITIONS, name) for name in ("binary_path", "home", "port", "context", "kv_type")}
    rows = [dataclasses.asdict(candidate) for candidate in candidates or (CANDIDATE,)]
    return record_json({"candidates": rows, "conditions": conditions})


@dataclasses.dataclass
class Rig:
    host: FakeHost
    root: Path

    @property
    def out(self) -> Path:
        return self.root / "out"

    def env(self) -> demo_main.DemoEnv:
        return demo_main.DemoEnv(
            tools=self.host.tools(), now=lambda: NOW, fsync=no_fsync, client_factory=self.host.client
        )

    def run(self, config: bytes | None = None) -> int:
        (self.root / "launch.json").write_bytes(launch_config() if config is None else config)
        argv = ["--launch-config", str(self.root / "launch.json"), "--inputs-dir", str(self.root / "frozen")]
        return demo_main.main([*argv, "--out-dir", str(self.out)], self.env())

    def written(self) -> list[str]:
        return sorted(path.name for path in self.out.iterdir())


def make_rig(tmp_path: Path, chat: tuple[Any, ...] = (), **host_options: Any) -> Rig:
    write_h5_inputs(tmp_path / "frozen", h5_input_bytes(prompts=DEMO_PROMPTS))
    (tmp_path / "out").mkdir()
    host = FakeHost(_NoFrozenRequests(), {UNIT: ServerScript(chat={None: list(chat)})}, **host_options)
    return Rig(host, tmp_path)


# ---------------------------------------------------------------- a live run


def test_a_live_run_launches_the_checked_server_and_writes_the_record_and_the_page(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    claim = extraction_json([("The storm closed the harbour for thirty-one hours.", ["t1_s02"])])
    rig = make_rig(tmp_path, chat=(ok(claim), ok(probe_json("CONTRADICTED", "The notice gives 26 hours.", "src_1"))))
    assert rig.run() == 0
    args, kwargs = rig.host.launches[UNIT]
    assert args == list(frozen_argv(CONDITIONS, CANDIDATE))
    assert kwargs["env"] == server_environment(CONDITIONS, "timed")
    assert rig.host.clients == [("http://127.0.0.1:18080", CONDITIONS.context)]
    assert rig.host.events == [("popen", UNIT), ("terminate", UNIT)] and rig.host.closed == 1
    assert len(rig.host.servers[UNIT].chats) == 4  # three extractions and one probe
    assert rig.written() == [f"{STEM}.html", f"{STEM}.json", f"{STEM}.server.log"]
    record = record_loads((rig.out / f"{STEM}.json").read_bytes()[:-1])
    assert (record["run_kind"], record["model_id"], record["pinned"]) == ("live", "Qwen/Qwen3-4B", False)
    assert (record["gguf_sha256"], record["source_revision"]) == (CANDIDATE.gguf_sha256, CANDIDATE.source_revision)
    assert (record["llama_build"], record["date"]) == ("b1-27b7a8a", "2026-11-28T21:05:00Z")
    assert record["launch_line"] == shlex.join(frozen_argv(CONDITIONS, CANDIDATE))
    assert record["prompts_sha256"] == DEMO_PROMPTS.sha256()
    assert [row["verdict"] for item in record["transcripts"] for row in item["claims"]] == ["CONTRADICTED"]
    page = (rig.out / f"{STEM}.html").read_text(encoding="utf-8")
    assert html.escape(LABEL) in page and "live local run" in page and "not pinned" in page
    printed = capsys.readouterr().out
    assert f"{STEM}.json" in printed and f"{STEM}.html" in printed


# ---------------------------------------------------------------- refusals before anything starts


@pytest.mark.parametrize("candidate_id", CANDIDATE_IDS[1:])
def test_any_other_candidate_is_refused_before_anything_starts(
    tmp_path: Path, candidate_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = make_rig(tmp_path)
    assert rig.run(launch_config(SCHEDULE_CANDIDATES[CANDIDATE_IDS.index(candidate_id)])) == 1
    assert "refused: ConfigError" in capsys.readouterr().err
    assert (rig.host.launches, rig.host.hashed, rig.written()) == ({}, [], [])
    assert "/proc/net/dev" not in rig.host.files.reads


@pytest.mark.parametrize(
    "config",
    [
        launch_config(*SCHEDULE_CANDIDATES),
        record_json({"candidates": [], "conditions": {}}),
        record_json({"candidates": [dataclasses.asdict(CANDIDATE)], "conditions": {"port": 18080}}),
        b"not json",
    ],
    ids=["three-candidates", "no-candidate", "conditions-keys", "not-json"],
)
def test_a_launch_config_of_another_shape_is_refused(tmp_path: Path, config: bytes) -> None:
    rig = make_rig(tmp_path)
    assert rig.run(config) == 1
    assert (rig.host.launches, rig.host.hashed, rig.written()) == ({}, [], [])


@pytest.mark.parametrize("resolved", ["/data/OneDrive/demo-out", "/mnt/d/demo-out", "/mnt"])
def test_an_output_directory_under_onedrive_or_mnt_is_refused(
    tmp_path: Path, resolved: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = make_rig(tmp_path, realpaths={str(tmp_path / "out"): resolved})
    assert rig.run() == 1
    assert "refused: StoreError" in capsys.readouterr().err
    assert (rig.host.launches, rig.host.hashed, rig.written()) == ({}, [], [])


@pytest.mark.parametrize(
    ("options", "candidate", "error"),
    [
        ({"net_dev": NET_DEV_LOOPBACK + NET_ETH0}, CANDIDATE, "LaunchCheckFailed"),
        ({}, dataclasses.replace(CANDIDATE, gguf_sha256="d" * 64), "ModelFileError"),
        ({}, dataclasses.replace(CANDIDATE, gguf_path="/mnt/c/models/candidate.gguf"), "ModelFileError"),
    ],
    ids=["not-loopback-only", "model-hash", "model-on-mnt"],
)
def test_the_loopback_and_model_file_checks_come_before_the_spawn(
    tmp_path: Path, options: dict[str, Any], candidate: Candidate, error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = make_rig(tmp_path, **options)
    assert rig.run(launch_config(candidate)) == 1
    assert f"refused: {error}" in capsys.readouterr().err
    assert (rig.host.launches, rig.host.clients, rig.written()) == ({}, [], [])


@pytest.mark.parametrize("suffix", [".json", ".html", ".server.log"])
def test_an_existing_output_file_is_never_overwritten_and_starts_nothing(
    tmp_path: Path, suffix: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each output name is checked before the launch, so a clash costs no model hash and loses no run."""
    rig = make_rig(tmp_path)
    (rig.out / f"{STEM}{suffix}").write_bytes(b"kept")
    assert rig.run() == 1
    assert "refused: StoreError" in capsys.readouterr().err
    assert (rig.out / f"{STEM}{suffix}").read_bytes() == b"kept"
    assert (rig.host.launches, rig.host.hashed, rig.written()) == ({}, [], [f"{STEM}{suffix}"])


@pytest.mark.parametrize("kind", ["missing", "a-file"])
def test_an_output_directory_that_is_not_a_directory_starts_nothing(
    tmp_path: Path, kind: str, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = make_rig(tmp_path)
    rig.out.rmdir()
    if kind == "a-file":
        rig.out.write_bytes(b"kept")
    assert rig.run() == 1
    assert "refused: StoreError" in capsys.readouterr().err
    assert (rig.host.launches, rig.host.hashed) == ({}, [])
    assert "/proc/net/dev" not in rig.host.files.reads


def test_broken_frozen_inputs_are_refused_before_the_launch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rig = make_rig(tmp_path)
    changed = tmp_path / "frozen" / "prompts" / "probe-system.txt"
    changed.write_bytes(changed.read_bytes() + b"changed")
    assert rig.run() == 1
    assert "refused: FrozenInputError" in capsys.readouterr().err
    assert (rig.host.launches, rig.host.hashed, rig.written()) == ({}, [], [])


def test_broken_fixtures_are_refused_before_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rig = make_rig(tmp_path)
    fixtures = write_fixtures(tmp_path / "fixtures")
    (tmp_path / "fixtures" / "bundles.json").write_bytes(b"not json")
    monkeypatch.setattr(demo_main, "FIXTURES_DIR", fixtures)
    assert rig.run() == 1
    assert "refused: FrozenInputError" in capsys.readouterr().err
    assert (rig.host.launches, rig.host.hashed, rig.written()) == ({}, [], [])


# ---------------------------------------------------------------- the client and the command line


def test_the_real_client_is_the_loopback_harness_client_with_trust_env_off() -> None:
    client = demo_main.loopback_client("http://127.0.0.1:18080", 21_504)
    try:
        assert (client.base_url, client.n_ctx) == ("http://127.0.0.1:18080", 21_504)
        assert client._http.trust_env is False
    finally:
        client.close()
    with pytest.raises(ValueError, match="127.0.0.1"):
        demo_main.loopback_client("http://localhost:18080", 21_504)
    assert demo_main.real_env().client_factory is demo_main.loopback_client


def test_a_missing_option_is_a_usage_error(tmp_path: Path) -> None:
    rig = make_rig(tmp_path)
    with pytest.raises(SystemExit) as caught:
        demo_main.main(["--out-dir", str(rig.out)], rig.env())
    assert caught.value.code == 2
    assert rig.host.launches == {}
