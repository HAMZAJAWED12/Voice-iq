"""The run CLI: freeze the gold-free inputs, freeze them against gold, start a schedule, and count contexts.

This is the only module that imports `entry`. `report` and `void` are handed to `entry.main` with their arguments
unchanged; `freeze inputs`, `freeze gold`, `run` and `context` are parsed here. Every result is a `CommandResult` of
typed `key: value` fields, never parsed text, so the whitelisting wrapper can check each value. Files are read only
through the injected tools, and the two hash-bearing files are written only through `records.write_new_file`.
Exception messages go to stderr alone, which the wrapper sends to its host-only log.

Implements, from the signed D6 criteria note S2:

- S2 §3.12, data: gold stays on the host. Used directly (`main`), only the gold-free steps run: `freeze inputs`, and
  `context` without a gold directory; every step that reads gold or a run directory runs only through the wrapper,
  whatever the path, because a copy of gold elsewhere would defeat a path check (AMB-159). Through the wrapper, a gold
  directory whose resolved path ends with a real gold suffix needs the committed frozen inputs, and a dry run keeps its
  hash files away from them (AMB-151); a real report needs the recorded freeze (AMB-154). No guard message names a
  path. Outputs are written outside OneDrive.
- S2 §3.2, environment: `context` starts each candidate's server through `launch.LaunchedServer`, on loopback, in
  timed mode with its own new log, and asks it only for token counts, never a generation (S2 §3.5; AMB-148, AMB-150).
  Its launch config holds the run conditions and the candidates alone, so it runs before the operator values exist.
- S2 §3.13, runs: `run` loads the entry config, the frozen inputs and the recorded freeze, compares them before any
  directory exists, and starts the schedule through `entry`, which never scores. A stopped schedule exits 3, the
  records-only code.
- S2 §11 items 1 and 6, frozen and hashed before the first run: `freeze inputs` writes `INPUTS.sha256`, and `freeze
  gold` builds the cap-session list (AMB-147), checks the overlap with gold (AMB-157), freezes, and writes the
  cap-session list and `EXPECTED-FREEZE.json`; with `--check-only` it writes nothing and compares committed copies
  byte for byte, over their LF bytes as every other reader of them does.

Exit codes: 0 done; 1 a refusal (the result holds only `error: <class name>`); 2 a usage error, raised by argparse as
`SystemExit`; 3 a stopped schedule, or a context that does not fit. Anything else propagates.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, get_args

from . import decision, entry, frozen, gates, launch, records
from .client import ContextCheck, ModelServerClient, ServerCheckError
from .gold import GoldSetError, load_gold_set
from .inputs import FrozenInputs, InputsError
from .machine import MachineTools
from .schemas import canonical_sha256

# The resolved-path endings of the real gold sets; tests build fake paths from these (AMB-151).
REAL_GOLD_SUFFIXES: tuple[str, ...] = ("app/evaluation/gold/v1.0", "app/evaluation/gold/v0.4")
FROZEN_DIR_SUFFIX = "app/evaluation/frozen"
RULE_VALUES: frozenset[str] = frozenset(decision.RULES) | {"none"}
END_VALUES: frozenset[str] = frozenset(get_args(gates.ScheduleEnd))
DRY_RUN_PREFIX = "dryrun-"
_STAMP = "%Y%m%dT%H%M%S%fZ"
_ENTRY_COMMANDS = ("report", "void")
# The context launch config: one JSON object in ASCII with exactly these keys at each level (AMB-150).
_LAUNCH_KEYS = ("conditions", "candidates")
_CONDITION_KEYS = ("binary_path", "home", "port", "context", "kv_type")
_CANDIDATE_KEYS = ("candidate_id", "gguf_path", "gguf_sha256", "source_revision", "quantization_commands")

_GUARD_WRAPPER = "this subcommand runs only through the wrapper (S2 §3.12; AMB-159)"
_GUARD_REAL_INPUTS = "a real gold set needs --inputs-dir to resolve to the committed frozen inputs (S2 §3.12; AMB-151)"
_GUARD_REAL_FILES = (
    "a real gold set needs --cap-sessions and --expected-freeze to resolve inside the committed frozen inputs "
    "(S2 §3.12; AMB-151)"
)
_GUARD_REAL_NAME = "a run on a real gold set may not take a dry-run name (S2 §3.12; AMB-151)"
_GUARD_DRY_NAME = "a run on any other gold set must take a dry-run name (S2 §3.12; AMB-151)"
_GUARD_DRY_FILES = (
    "a dry run may not read --cap-sessions or --expected-freeze from the committed frozen inputs (S2 §3.12; AMB-151)"
)
_GUARD_DRY_OUT = (
    "a dry run may not write its hash files through --out-dir into the committed frozen inputs (S2 §3.12; AMB-151)"
)
_GUARD_REPORT = "a report on a run that is not a dry run needs --expected-freeze (S2 §3.12; AMB-154)"


class GuardRefused(ValueError):
    """A guard refused the call before anything was read or written. The message names no path."""


@dataclass(frozen=True, slots=True)
class CliEnv:
    """Every tool, sync and clock the CLI hands on. `client_factory` None means the real loopback client."""

    tools: launch.LaunchTools
    machine_tools: MachineTools
    now: Callable[[], datetime]
    fsync: Callable[[int], None]
    client_factory: Callable[[str, int], ModelServerClient] | None = None


@dataclass(frozen=True, slots=True)
class CommandResult:
    """What one subcommand gives the console: its name, its exit code and `(key, value)` fields, each key one the
    wrapper's whitelist knows. A refusal holds only `("error", <class name>)`; `report` and `void` hold none."""

    command: str
    exit_code: int
    fields: tuple[tuple[str, str], ...]


_REFUSALS = (
    GuardRefused,
    frozen.FrozenInputError,
    InputsError,
    GoldSetError,
    gates.ReportError,
    records.StoreError,
    launch.ConfigError,
    OSError,
    ServerCheckError,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def real_env() -> CliEnv:
    """The real tools, the machine tools over them, the UTC clock and `os.fsync`. Only `main` calls this."""
    return CliEnv(
        tools=launch.LaunchTools(),
        machine_tools=MachineTools(launch=launch.LaunchTools()),
        now=_utc_now,
        fsync=os.fsync,
    )


def _loopback_client(url: str, n_ctx: int) -> ModelServerClient:
    """The real client for one context launch, on the loopback URL the launch gives (S2 §3.2)."""
    return ModelServerClient(url, n_ctx=n_ctx)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bakeoff-cli",
        description="Freeze the bake-off inputs, start a schedule, or count contexts (S2 §3.12, §3.13, §11).",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    freezing = commands.add_parser("freeze", help="Freeze the inputs.", allow_abbrev=False)
    targets = freezing.add_subparsers(dest="target", required=True)
    gold_free = targets.add_parser("inputs", help="Write INPUTS.sha256 over the gold-free files.", allow_abbrev=False)
    gold_free.add_argument("--inputs-dir", required=True)
    gold_free.add_argument("--out", required=True)
    with_gold = targets.add_parser(
        "gold", help="Freeze against gold and write the expected freeze.", allow_abbrev=False
    )
    with_gold.add_argument("--inputs-dir", required=True)
    with_gold.add_argument("--gold-dir", required=True)
    with_gold.add_argument("--out-dir", required=True)
    with_gold.add_argument("--cap-sessions")
    with_gold.add_argument("--check-only", action="store_true")
    with_gold.add_argument("--expected-freeze")
    running = commands.add_parser("run", help="Start one schedule; it never scores.", allow_abbrev=False)
    for option in ("--config", "--inputs-dir", "--gold-dir", "--cap-sessions", "--expected-freeze"):
        running.add_argument(option, required=True)
    counting = commands.add_parser("context", help="Count the frozen requests on each server.", allow_abbrev=False)
    counting.add_argument("--launch-config", required=True)
    counting.add_argument("--inputs-dir", required=True)
    counting.add_argument("--server-log-dir", required=True)
    counting.add_argument("--gold-dir")
    counting.add_argument("--cap-sessions")
    counting.add_argument("--sources", action="store_true")
    return parser


def _parse(argv: list[str]) -> argparse.Namespace:
    """The parsed arguments, with the cross-option rules argparse cannot state; each breach is a usage error."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "freeze" and args.target == "gold" and args.check_only != (args.expected_freeze is not None):
        parser.error("--check-only and --expected-freeze are given together or not at all")
    if args.command == "context":
        if args.cap_sessions is not None and args.gold_dir is None:
            parser.error("--cap-sessions needs --gold-dir")
        if args.gold_dir is None and not args.sources:
            parser.error("context needs --gold-dir, --sources or both")
    return args


def _norm(path: str) -> str:
    """A resolved path with one separator and one case, for suffix checks only."""
    return path.replace(chr(92), "/").rstrip("/").casefold()


def _is_real_gold(resolved: str) -> bool:
    return any(_norm(resolved).endswith(suffix.casefold()) for suffix in REAL_GOLD_SUFFIXES)


def _ends_at_segment(normed: str, suffix: str) -> bool:
    """`normed` ends with `suffix` at a path segment, so a sibling such as `.../xapp/evaluation/frozen` does not."""
    return normed == suffix or normed.endswith("/" + suffix)


def _is_frozen_dir(resolved: str) -> bool:
    return _ends_at_segment(_norm(resolved), FROZEN_DIR_SUFFIX)


def _inside_frozen(resolved: str) -> bool:
    parts = _norm(resolved).split("/")
    return any(_ends_at_segment("/".join(parts[:end]), FROZEN_DIR_SUFFIX) for end in range(1, len(parts)))


def _base_name(resolved: str) -> str:
    return resolved.replace(chr(92), "/").rstrip("/").rsplit("/", 1)[-1]


def _resolve(env: CliEnv, path: str | None) -> str | None:
    return None if path is None else env.tools.realpath(path)


def _check_gold_paths(
    real: bool,
    inputs_dir: str,
    files: Sequence[str | None],
    *,
    files_required: bool,
    run_name: str | None = None,
    out_dir: str | None = None,
) -> None:
    """The real-versus-dry rules of one subcommand (AMB-151), on resolved paths. `files` are the cap-session list and
    the expected freeze where the subcommand reads them; `files_required` says a real gold set needs both inside the
    frozen inputs. `run_name` is checked for `run` only, and `out_dir` for a `freeze gold` that writes."""
    if real:
        if run_name is not None and run_name.startswith(DRY_RUN_PREFIX):
            raise GuardRefused(_GUARD_REAL_NAME)
        if not _is_frozen_dir(inputs_dir):
            raise GuardRefused(_GUARD_REAL_INPUTS)
        if files_required and not all(path is not None and _inside_frozen(path) for path in files):
            raise GuardRefused(_GUARD_REAL_FILES)
        return
    if run_name is not None and not run_name.startswith(DRY_RUN_PREFIX):
        raise GuardRefused(_GUARD_DRY_NAME)
    if any(path is not None and _inside_frozen(path) for path in files):
        raise GuardRefused(_GUARD_DRY_FILES)
    if out_dir is not None and (_is_frozen_dir(out_dir) or _inside_frozen(out_dir)):
        raise GuardRefused(_GUARD_DRY_OUT)


def check_report(argv: list[str], env: CliEnv) -> None:
    """A report on a run whose resolved directory name does not start with the dry-run prefix needs
    `--expected-freeze` (AMB-154). The guard reads a copy of argv with entry's own parser, abbreviations and all, so
    it sees exactly the run dir and expected freeze that `entry.main` will act on. An argv that parser refuses is a
    usage error, `SystemExit(2)`, before dispatch. The wrapper also calls this before dispatch."""
    known, _rest = entry._parser().parse_known_args(list(argv))
    if known.command != "report" or known.expected_freeze is not None:
        return
    run_dir = _resolve(env, known.run_dir)
    if run_dir is None or not _base_name(run_dir).startswith(DRY_RUN_PREFIX):
        raise GuardRefused(_GUARD_REPORT)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _freeze_inputs(args: argparse.Namespace, env: CliEnv) -> CommandResult:
    files = frozen.read_input_files(args.inputs_dir, env.tools.read_bytes)
    draft = frozen.parse_draft_inputs(files)
    manifest = frozen.inputs_manifest(files)
    records.write_new_file(args.out, manifest, fsync=env.fsync, realpath=env.tools.realpath)
    hashes = frozen.gold_free_hashes(draft)
    return CommandResult(
        "freeze inputs",
        0,
        (
            *((name, hashes[name]) for name in ("prompts_sha256", "repair_sha256", "probe_sha256")),
            ("inputs_manifest_sha256", _sha256(manifest)),
            ("file_count", str(len(files))),
            ("warmup_item", draft.warmup.item_id),
        ),
    )


def _freeze_gold(args: argparse.Namespace, env: CliEnv) -> CommandResult:
    read = env.tools.read_bytes
    gold_dir = env.tools.realpath(args.gold_dir)
    inputs_dir = env.tools.realpath(args.inputs_dir)
    cap_path, expected_path = _resolve(env, args.cap_sessions), _resolve(env, args.expected_freeze)
    files = (cap_path, expected_path) if args.check_only else (cap_path,)
    written_to = None if args.check_only else env.tools.realpath(args.out_dir)
    _check_gold_paths(_is_real_gold(gold_dir), inputs_dir, files, files_required=args.check_only, out_dir=written_to)
    inputs, counts = frozen.load_frozen(inputs_dir, gold_dir, cap_path, read, env.tools.realpath)
    gold = load_gold_set(gold_dir)
    specs = frozen.build_cap_sessions(gold)
    cap_bytes = frozen.cap_sessions_bytes(gold.version, specs)
    expected_bytes = frozen.expected_freeze_bytes(inputs)
    if args.check_only:
        if cap_path is not None and frozen.lf_bytes(read(cap_path)) != cap_bytes:
            raise frozen.FrozenInputError(f"{frozen.CAP_SESSIONS_FILE} differs from the rebuilt list, byte for byte")
        recorded = b"" if expected_path is None else frozen.lf_bytes(read(expected_path))
        frozen.check_expected(inputs, gates.parse_expected_freeze(recorded))
        if recorded != expected_bytes:
            raise frozen.FrozenInputError(f"{frozen.EXPECTED_FREEZE_FILE} differs from the freeze, byte for byte")
    else:
        out_dir = args.out_dir.rstrip("/" + chr(92))
        if cap_path is None:
            records.write_new_file(
                f"{out_dir}/{frozen.CAP_SESSIONS_FILE}", cap_bytes, fsync=env.fsync, realpath=env.tools.realpath
            )
        records.write_new_file(
            f"{out_dir}/{frozen.EXPECTED_FREEZE_FILE}", expected_bytes, fsync=env.fsync, realpath=env.tools.realpath
        )
    hashes = inputs.hashes
    sessions = tuple(
        field
        for spec in specs
        for field in (
            ("session", spec.session_id),
            ("session_rows", str(frozen.cap_session_rows(gold, spec.source_ids))),
            ("session_sources", ",".join(spec.source_ids)),
        )
    )
    scalars = ("prompts_sha256", "repair_sha256", "probe_sha256", "cap_sessions_sha256", "cap_requests_sha256")
    return CommandResult(
        "freeze gold",
        0,
        (
            ("gold_version", inputs.gold_version),
            ("gold_manifest_sha256", canonical_sha256(dict(inputs.gold_manifest))),
            *((name, str(getattr(hashes, name))) for name in scalars),
            ("warmup_sha256", hashes.warmup_sha256),
            ("request_count", str(len(inputs.all_requests()))),
            *sessions,
            ("overlap_equal", str(counts.equal)),
            ("overlap_contained", str(counts.contained)),
            ("delimiter_hits", str(counts.delimiter_hits)),
            ("expected_freeze_sha256", _sha256(expected_bytes)),
        ),
    )


def _run(args: argparse.Namespace, env: CliEnv) -> CommandResult:
    read, realpath = env.tools.read_bytes, env.tools.realpath
    config = entry.load_entry_config(args.config, read)
    gold_dir, inputs_dir = realpath(args.gold_dir), realpath(args.inputs_dir)
    cap_path, expected_path = realpath(args.cap_sessions), realpath(args.expected_freeze)
    real = _is_real_gold(gold_dir)
    _check_gold_paths(real, inputs_dir, (cap_path, expected_path), files_required=True, run_name=config.run_name)
    inputs, _counts = frozen.load_frozen(inputs_dir, gold_dir, cap_path, read, realpath)
    frozen.check_expected(inputs, gates.parse_expected_freeze(read(expected_path)))
    schedule, deps = entry.build_deps(
        config,
        tools=env.tools,
        machine_tools=env.machine_tools,
        fsync=env.fsync,
        now=env.now,
        client_factory=env.client_factory,
    )
    outcome = entry.run_bakeoff(config, inputs, schedule, deps)
    stopped, _sep, unit = (outcome.stopped or "no").partition(": ")
    fields = [("run", config.run_name), ("stopped", stopped)]
    if unit:
        fields.append(("unit", unit))
    fields.append(("request_count", str(len(inputs.all_requests()))))
    return CommandResult("run", 0 if outcome.stopped is None else entry.RECORDS_ONLY_EXIT, tuple(fields))


def _exact_keys(value: object, names: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise launch.ConfigError(f"the launch config is refused: {where} must be an object")
    missing, unexpected = sorted(set(names) - set(value)), sorted(set(value) - set(names))
    if missing or unexpected:
        raise launch.ConfigError(
            f"the launch config is refused: {where}: missing keys {missing}, unexpected keys {unexpected}"
        )
    return value


def _launch_config(raw: bytes) -> tuple[launch.RunConditions, tuple[launch.Candidate, ...]]:
    """The context launch config: the run conditions and the three candidates in the entry config's shapes, with no
    operator or machine section (AMB-150). The launch constructors check the values; a key-set refusal names the key."""
    top = _exact_keys(records.record_loads(raw), _LAUNCH_KEYS, "the top level")
    rows = top["candidates"]
    if not isinstance(rows, list) or len(rows) != len(launch.CANDIDATE_IDS):
        raise launch.ConfigError("the launch config is refused: candidates must be a list of exactly 3 objects")
    try:
        conditions = launch.RunConditions(**_exact_keys(top["conditions"], _CONDITION_KEYS, "conditions"))
        candidates = tuple(
            launch.Candidate(**_exact_keys(row, _CANDIDATE_KEYS, f"candidates[{index}]"))
            for index, row in enumerate(rows)
        )
    except TypeError as exc:
        raise launch.ConfigError(f"the launch config is refused: {exc}") from None
    return conditions, launch.validate_candidates(candidates)


def _check_fields(check: ContextCheck) -> tuple[tuple[str, str], ...]:
    return (
        ("check_label", str(check.label)),
        ("input_tokens", str(check.input_tokens)),
        ("uses", str(check.uses)),
        ("limit", str(check.limit)),
        ("fits", "true" if check.fits else "false"),
    )


def _request_checks(client: ModelServerClient, inputs: FrozenInputs) -> tuple[list[tuple[str, str]], bool]:
    """Each frozen request's first-attempt count and its repair bound after a cut-off (S2 §3.5), in request order."""
    fields: list[tuple[str, str]] = []
    fits = True
    for frozen_request in inputs.all_requests():
        for check in (
            client.check_context(frozen_request.request),
            client.repair_bound(frozen_request.request, inputs.prompts.repair),
        ):
            fields += (("check", frozen_request.item_id), *_check_fields(check))
            fits = fits and check.fits
    return fields, fits


def _context(args: argparse.Namespace, env: CliEnv) -> CommandResult:
    read, realpath = env.tools.read_bytes, env.tools.realpath
    inputs_dir = realpath(args.inputs_dir)
    gold_dir, cap_path = _resolve(env, args.gold_dir), _resolve(env, args.cap_sessions)
    if gold_dir is not None:
        _check_gold_paths(_is_real_gold(gold_dir), inputs_dir, (cap_path,), files_required=False)
    conditions, candidates = _launch_config(read(args.launch_config))
    log_dir = records.refuse_onedrive(args.server_log_dir, realpath=realpath).rstrip("/" + chr(92))
    inputs: FrozenInputs | None = None
    if gold_dir is not None:
        inputs, _counts = frozen.load_frozen(inputs_dir, gold_dir, cap_path, read, realpath)
    deltas = frozen.source_delta_requests(frozen.load_draft_inputs(inputs_dir, read)) if args.sources else ()
    factory = env.client_factory if env.client_factory is not None else _loopback_client
    fields: list[tuple[str, str]] = []
    fits = True
    for candidate in candidates:
        slug = launch.CANDIDATE_SLUGS[candidate.candidate_id]
        log_path = f"{log_dir}/context-{slug}-{env.now().strftime(_STAMP)}.log"
        server = launch.LaunchedServer(
            conditions, candidate, mode="timed", log_path=log_path, tools=env.tools, client_factory=factory
        )
        with server:
            client = server.client
            fields.append(("candidate", candidate.candidate_id))
            if inputs is not None:
                checked, all_fit = _request_checks(client, inputs)
                fields += checked
                fits = fits and all_fit
            full_counts: dict[str, int] = {}
            for delta in deltas:
                if delta.item_id not in full_counts:
                    full_counts[delta.item_id] = client.check_context(delta.full).input_tokens
                emptied = client.check_context(delta.emptied).input_tokens
                fields += (
                    ("item", delta.item_id),
                    ("source", delta.source_id),
                    ("candidate", candidate.candidate_id),
                    ("source_tokens", str(full_counts[delta.item_id] - emptied)),
                )
    return CommandResult("context", 0 if fits else 3, tuple(fields))


def _dispatch(argv: list[str], env: CliEnv, *, guarded: bool) -> CommandResult:
    if argv and argv[0] in _ENTRY_COMMANDS:
        if not guarded:
            raise GuardRefused(_GUARD_WRAPPER)
        if argv[0] == "report":
            check_report(argv, env)
        return CommandResult(argv[0], entry.main(list(argv)), ())
    args = _parse(argv)
    gold_step = (args.command == "freeze" and args.target == "gold") or args.command == "run"
    if not guarded and (gold_step or (args.command == "context" and args.gold_dir is not None)):
        raise GuardRefused(_GUARD_WRAPPER)
    if args.command == "freeze":
        return _freeze_inputs(args, env) if args.target == "inputs" else _freeze_gold(args, env)
    return _run(args, env) if args.command == "run" else _context(args, env)


def _command_of(argv: list[str]) -> str:
    """The subcommand's result name from argv alone, for a refusal raised before or during parsing."""
    if argv[:2] in (["freeze", "inputs"], ["freeze", "gold"]):
        return " ".join(argv[:2])
    return argv[0] if argv else ""


def dispatch(argv: Sequence[str], env: CliEnv, *, guarded: bool) -> CommandResult:
    """Run one subcommand. `guarded` is True only from the wrapper (AMB-159). A refusal before writing becomes exit 1
    with the class name as its one field, and its message goes to stderr alone; a usage error raises `SystemExit(2)`;
    anything else propagates."""
    given = list(argv)
    try:
        return _dispatch(given, env, guarded=guarded)
    except _REFUSALS as exc:
        sys.stderr.write(f"refused: {type(exc).__name__}: {exc}\n")
        return CommandResult(_command_of(given), 1, (("error", type(exc).__name__),))


def main(argv: Sequence[str] | None = None) -> int:
    """Direct use: the gold-free steps only (AMB-159). Each field is printed as one `key: value` line."""
    result = dispatch(sys.argv[1:] if argv is None else argv, real_env(), guarded=False)
    for key, value in result.fields:
        sys.stdout.write(f"{key}: {value}\n")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
