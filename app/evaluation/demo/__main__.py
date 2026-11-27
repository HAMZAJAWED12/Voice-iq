"""The demo's live command line: one local run on Qwen/Qwen3-4B, not pinned, with no bake-off behind it.

    python -m app.evaluation.demo --launch-config <file> --inputs-dir <frozen inputs dir> --out-dir <dir>

In isolated mode (`-I`, which implies `-P`), neither the current directory nor PYTHONPATH is on sys.path, so
`python -I -m app.evaluation.demo` cannot find the package. The isolated form puts the checkout on the path itself:

    python -I -B -c "import runpy, sys; sys.path.insert(0, '<checkout>'); runpy.run_module('app.evaluation.demo',
    run_name='__main__', alter_sys=True)" --launch-config <file> --inputs-dir <dir> --out-dir <dir>

(one line in use; `main` reads `sys.argv[1:]`, so the options pass through).

The launch config has the `context` step's shape (AMB-150), the run conditions and the candidates, with exactly one
candidate: Qwen/Qwen3-4B. Any other candidate is refused before anything starts. The server starts only through
`launch.LaunchedServer`, which checks the loopback-only namespace and the model file first, then starts the frozen
launch line in an environment built from nothing and runs every start-up check (S2 §3.2, §3.3). The client is the
harness's own, on http://127.0.0.1:<port>, with `trust_env` off. The output directory must be a directory outside
OneDrive and `/mnt` (S2 §3.12); the server log, the record and the page are created new in it, never overwritten.
Their names are checked before the launch, so a clash costs no model hash and no run; a file that appears during the
run is still refused by the exclusive create, which then leaves the files already written. Every file is read
through the launch tools, and nothing reads the environment.

Exit codes: 0 done; 1 a refusal, with the class name and message on stderr; 2 a usage error, raised by argparse.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from app.evaluation.bakeoff import launch, records
from app.evaluation.bakeoff.client import ModelServerClient
from app.evaluation.bakeoff.frozen import FrozenInputError
from app.evaluation.demo import pipeline, render

FIXTURES_DIR = str(Path(__file__).resolve().parent / "fixtures")
_STAMP = "%Y%m%dT%H%M%SZ"
_DATE = "%Y-%m-%dT%H:%M:%SZ"
_LAUNCH_KEYS = ("conditions", "candidates")
_CONDITION_KEYS = ("binary_path", "home", "port", "context", "kv_type")
_CANDIDATE_KEYS = ("candidate_id", "gguf_path", "gguf_sha256", "source_revision", "quantization_commands")
_REFUSALS = (
    pipeline.DemoError,
    FrozenInputError,
    records.StoreError,
    launch.ConfigError,
    launch.LaunchError,
    launch.ModelFileError,
    OSError,
)


def loopback_client(url: str, n_ctx: int) -> ModelServerClient:
    """The harness client on the loopback URL the launch gives: it refuses any other URL, and its transport reads no
    proxy, netrc or certificate setting from the environment (`trust_env` off)."""
    return ModelServerClient(url, n_ctx=n_ctx)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DemoEnv:
    """Every file, process, clock and client the command line reaches, and the two checks of the output names."""

    tools: launch.LaunchTools
    now: Callable[[], datetime]
    fsync: Callable[[int], None]
    client_factory: Callable[[str, int], ModelServerClient]
    is_dir: Callable[[str], bool] = os.path.isdir
    exists: Callable[[str], bool] = os.path.lexists


def real_env() -> DemoEnv:
    return DemoEnv(launch.LaunchTools(), _utc_now, os.fsync, loopback_client)


def _exact(value: object, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise launch.ConfigError(f"the launch config is refused: {where} must hold exactly the keys {list(keys)}")
    return value


def parse_launch_config(raw: bytes) -> tuple[launch.RunConditions, launch.Candidate]:
    """The run conditions and the one candidate. The launch constructors check the values."""
    top = _exact(records.record_loads(raw), _LAUNCH_KEYS, "the top level")
    rows = top["candidates"]
    if not isinstance(rows, list) or len(rows) != 1:
        raise launch.ConfigError(f"the launch config is refused: candidates must list {pipeline.MODEL_ID} alone")
    conditions = launch.RunConditions(**_exact(top["conditions"], _CONDITION_KEYS, "conditions"))
    candidate = launch.Candidate(**_exact(rows[0], _CANDIDATE_KEYS, "candidates[0]"))
    if candidate.candidate_id != pipeline.MODEL_ID:
        raise launch.ConfigError(f"the demo runs {pipeline.MODEL_ID} only, not {candidate.candidate_id}")
    return conditions, candidate


def check_out_dir(path: str, realpath: Callable[[str], str], is_dir: Callable[[str], bool]) -> str:
    """The resolved output directory, refused under OneDrive or under `/mnt`, where WSL mounts the Windows drives, or
    when it is not a directory."""
    resolved = records.refuse_onedrive(path, realpath=realpath)
    if PurePosixPath(resolved).parts[:2] == ("/", "mnt"):
        raise records.StoreError("the output directory is under /mnt: outputs are written outside the Windows drives")
    if not is_dir(resolved):
        raise records.StoreError(f"the output directory {resolved} is not a directory")
    return resolved


def run(args: argparse.Namespace, env: DemoEnv) -> tuple[str, str]:
    """Check and load everything, launch, run the demo, stop the server, then write the record and the page. Their
    resolved paths are returned."""
    read, realpath = env.tools.read_bytes, env.tools.realpath
    out_dir = check_out_dir(args.out_dir, realpath, env.is_dir)
    conditions, candidate = parse_launch_config(read(args.launch_config))
    prompts = pipeline.load_prompts(args.inputs_dir, read)
    fixtures = pipeline.load_fixtures(FIXTURES_DIR, read)
    started = env.now()
    base = f"{out_dir.rstrip('/' + chr(92))}/demo-{started.strftime(_STAMP)}"
    for name in (f"{base}.server.log", f"{base}.json", f"{base}.html"):
        if env.exists(name):
            raise records.StoreError(f"the output file {name} exists: outputs are created new, never overwritten")
    server = launch.LaunchedServer(
        conditions,
        candidate,
        mode="timed",
        log_path=f"{base}.server.log",
        tools=env.tools,
        client_factory=env.client_factory,
    )
    with server:
        transcripts = pipeline.run_demo(server.client, prompts, fixtures)
        header = render.RunHeader(
            run_kind="live",
            model_id=candidate.candidate_id,
            source_revision=candidate.source_revision,
            gguf_sha256=server.model_sha256,
            prompts_sha256=prompts.sha256(),
            llama_build=server.props.build_info,
            launch_line=shlex.join(launch.frozen_argv(conditions, candidate)),
            date=started.strftime(_DATE),
        )
    demo = render.DemoRun(header, fixtures.sha256, transcripts)
    record = records.write_new_file(f"{base}.json", render.record_bytes(demo), fsync=env.fsync, realpath=realpath)
    page = records.write_new_file(f"{base}.html", render.render_page(demo), fsync=env.fsync, realpath=realpath)
    return record, page


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.demo",
        description="One local demo run on Qwen/Qwen3-4B, not pinned: no bake-off, no pin.",
        allow_abbrev=False,
    )
    parser.add_argument("--launch-config", required=True, help="the context launch config with one candidate")
    parser.add_argument("--inputs-dir", required=True, help="the frozen inputs directory, with INPUTS.sha256")
    parser.add_argument("--out-dir", required=True, help="an existing directory outside OneDrive and /mnt")
    return parser


def main(argv: Sequence[str] | None = None, env: DemoEnv | None = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else list(argv))
    try:
        record, page = run(args, real_env() if env is None else env)
    except _REFUSALS as exc:
        sys.stderr.write(f"refused: {type(exc).__name__}: {exc}" + chr(10))
        return 1
    sys.stdout.write(f"record: {record}" + chr(10) + f"page: {page}" + chr(10))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
