"""Hygiene of the harness package: H1 spec T1, T2, T4 and G8, H2 spec R48, and H3 spec T4 to T7.

S2 §3.12: "Gold rows go only to the host's model server, never to a hosted model, and that includes any scoring or
matching step." The scoring core therefore imports only the standard library and pydantic, and runs with the network
switched off. Only `client` may import httpx, and it talks only to http://127.0.0.1:<port> (S2 §3.2).

T2: these tests run on the 3.12 `.venv`, so 3.11+ syntax, 3.11+ names and PEP 701 f-strings are checked here. What an
AST cannot see, such as a 3.11+ attribute reached through a variable, is left to
`python -m mypy --python-version 3.10 app/evaluation/bakeoff`.
"""

from __future__ import annotations

import ast
import io
import re
import socket
import tokenize
from pathlib import Path

import pytest

from app.evaluation.bakeoff.extraction import parse_extraction
from app.evaluation.bakeoff.gold import load_gold_set
from app.evaluation.bakeoff.scoring import score_run
from app.evaluation.bakeoff.stats import paired_bootstrap
from app.evaluation.tests._fixtures import fixture_x, output_json, run_xm, run_xp, write_version

PACKAGE = "app.evaluation.bakeoff"
BAKEOFF = Path(__file__).resolve().parents[1] / "bakeoff"
H2_MODULES = ("schemas", "probe", "client")
H3_MODULES = ("inputs", "launch", "records", "runner")
MODULES = ("__init__", "gold", "extraction", "scoring", "stats", *H2_MODULES, *H3_MODULES)
ALLOWED = frozenset(
    {
        "__future__",
        "collections",
        "collections.abc",
        "csv",
        "dataclasses",
        "datetime",
        "fractions",
        "hashlib",
        "io",
        "json",
        "math",
        "os",
        "pathlib",
        "platform",
        "random",
        "re",
        "types",
        "typing",
        "pydantic",
    }
)
EXTRA_ALLOWED = {"client": frozenset({"httpx", "time"}), "launch": frozenset({"subprocess", "time"})}


def _source(module: str) -> str:
    return (BAKEOFF / f"{module}.py").read_text(encoding="utf-8")


def _imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.add(f"{PACKAGE}.{node.module}" if node.module else PACKAGE)
            else:
                names.add(node.module or "")
    return names


def _own(name: str) -> bool:
    return name == PACKAGE or name.startswith(PACKAGE + ".")


def _disallowed(source: str) -> set[str]:
    return {name for name in _imports(source) if name not in ALLOWED and not _own(name)}


def _disallowed_for(module: str, source: str) -> set[str]:
    return _disallowed(source) - EXTRA_ALLOWED.get(module, frozenset())


@pytest.mark.parametrize("module", MODULES)
def test_imports_are_on_the_allowlist(module: str) -> None:
    assert _disallowed_for(module, _source(module)) == set()


def test_the_check_catches_a_network_client() -> None:
    scoring = _source("scoring")
    assert _disallowed(scoring + "\nimport httpx\n") == {"httpx"}
    assert _disallowed(scoring + "\nfrom urllib import request\n") == {"urllib"}
    assert _disallowed(scoring + "\ndef f():\n    import socket\n") == {"socket"}
    assert _disallowed(scoring + "\nimport numpy as np\n") == {"numpy"}
    assert _disallowed("from . import scoring\nfrom .gold import GoldSet\n") == set()


@pytest.mark.parametrize("module", [module for module in MODULES if module != "client"])
def test_only_the_client_may_import_httpx(module: str) -> None:
    assert _disallowed_for(module, _source(module) + "\nimport httpx\n") == {"httpx"}


def test_the_client_source_passes_with_httpx_and_time() -> None:
    source = _source("client")
    assert {"httpx", "time"} <= _imports(source)
    assert _disallowed_for("client", source) == set()


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("import requests", "requests"),
        ("import socket", "socket"),
        ("from urllib import request", "urllib"),
        ("import subprocess", "subprocess"),
    ],
)
def test_the_client_may_import_no_other_way_out(line: str, name: str) -> None:
    assert _disallowed_for("client", _source("client") + f"\n{line}\n") == {name}


@pytest.mark.parametrize("module", H2_MODULES)
def test_h2_modules_touch_no_files_and_print_nothing(module: str) -> None:
    source = _source(module)
    assert not _imports(source) & {"os", "pathlib", "io", "logging"}
    assert "print" not in {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}


def _attributes(source: str) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}


def _called_attributes(source: str) -> set[str]:
    """The names called as attributes, as `fsync` in `os.fsync(handle)`."""
    return {
        node.func.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def _open_modes(source: str) -> set[object]:
    """The mode of every `.open(...)` call; one that is not a literal shows up as `None`."""
    modes: set[object] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            mode = node.args[0] if node.args else None
            modes.add(mode.value if isinstance(mode, ast.Constant) else None)
    return modes


@pytest.mark.parametrize("module", [module for module in MODULES if module != "launch"])
def test_only_launch_may_import_subprocess(module: str) -> None:
    assert _disallowed_for(module, _source(module) + "\nimport subprocess\n") == {"subprocess"}


def test_the_launch_source_passes_with_subprocess_and_time() -> None:
    source = _source("launch")
    assert {"subprocess", "time"} <= _imports(source)
    assert _disallowed_for("launch", source) == set()


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("import httpx", "httpx"),
        ("import socket", "socket"),
        ("from urllib import request", "urllib"),
        ("import logging", "logging"),
    ],
)
def test_launch_may_import_no_other_way_out(line: str, name: str) -> None:
    assert _disallowed_for("launch", _source("launch") + f"\n{line}\n") == {name}


def test_launch_uses_only_the_process_calls_the_spec_names() -> None:
    reached = {
        node.attr
        for node in ast.walk(ast.parse(_source("launch")))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "subprocess"
    }
    assert reached == {"DEVNULL", "Popen", "STDOUT", "TimeoutExpired"}


# T5, "Only `launch` starts a process": the ways `os` starts one without going through `subprocess.Popen`.
_OS_PROCESS_STARTS = re.compile(r"system|popen|startfile|fork|forkpty|posix_spawnp?|exec[lv]p?e?|spawn[lv]p?e?")


def _os_process_starts(source: str) -> set[str]:
    """Process starts reached as `os.<name>` or imported with `from os import`; `tools.popen` is not one."""
    tree = ast.parse(source)
    reached = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os"
    }
    reached |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and not node.level and node.module == "os"
        for alias in node.names
    }
    return {name for name in reached if _OS_PROCESS_STARTS.fullmatch(name)}


@pytest.mark.parametrize("module", MODULES)
def test_no_module_starts_a_process_through_os(module: str) -> None:
    assert _os_process_starts(_source(module)) == set()


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("os.system('x')", "system"),
        ("os.popen('x')", "popen"),
        ("os.execv('x', ['x'])", "execv"),
        ("os.spawnv(0, 'x', ['x'])", "spawnv"),
        ("os.fork()", "fork"),
        ("os.posix_spawn('x', ['x'], {})", "posix_spawn"),
        ("from os import system", "system"),
    ],
)
def test_the_os_process_check_catches_each_form(line: str, name: str) -> None:
    assert _os_process_starts(f"import os\n{line}\n") == {name}
    legal = "import os\ntools.popen(argv, shell=False)\nos.fsync(1)\nos.path.realpath('x')\n"
    assert _os_process_starts(legal) == set()


# `replace` is not listed: `str.replace` is legal, and it is how a template is filled.
_FILE_ATTRIBUTES = frozenset(
    {
        "open",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
        "mkdir",
        "unlink",
        "rmdir",
        "rename",
        "touch",
        "fsync",
        "remove",
        "listdir",
        "scandir",
        "iterdir",
        "glob",
    }
)
# D4: `remove`, `replace`, `removedirs` and `ftruncate` are other names for unlink, rename, rmdir and truncate; records
# uses no `str.replace`, `dataclasses.replace` or `list.remove`.
_RECORDS_NEVER = frozenset(
    {
        "unlink",
        "remove",
        "rename",
        "replace",
        "rmdir",
        "removedirs",
        "truncate",
        "ftruncate",
        "write_text",
        "write_bytes",
    }
)


def test_inputs_reaches_files_only_through_the_gold_loader() -> None:
    source = _source("inputs")
    assert not _imports(source) & {"os", "io", "logging"}
    assert not _attributes(source) & _FILE_ATTRIBUTES
    pathlib_names = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib"
        for alias in node.names
    }
    assert pathlib_names == {"Path"}


def test_the_runner_reaches_no_file_itself() -> None:
    source = _source("runner")
    assert not _imports(source) & {"os", "pathlib", "io", "logging"}
    # T4's list but for `fsync`: `deps.fsync` is the store's injected sync, which the runner hands on and never calls.
    assert not _attributes(source) & (_FILE_ATTRIBUTES - {"fsync"})
    assert "fsync" not in _called_attributes(source)


def test_the_fsync_check_reads_calls_only() -> None:
    assert "fsync" in _called_attributes("deps.fsync(1)\n")
    assert "fsync" not in _called_attributes("store = create(fsync=deps.fsync)\n")


def test_the_runner_never_sets_a_scored_marker() -> None:
    assert "mark_scored" not in _attributes(_source("runner"))


def test_records_never_rewrites_or_deletes_a_file() -> None:
    source = _source("records")
    assert not _attributes(source) & _RECORDS_NEVER
    assert _open_modes(source) <= {"xb", "ab", "rb"}
    assert {"xb", "ab"} <= _open_modes(source)


def test_launch_creates_the_server_log_and_writes_nothing_else() -> None:
    source = _source("launch")
    assert _open_modes(source) == {"xb", "rb"}
    assert not _attributes(source) & (_FILE_ATTRIBUTES - {"open", "read_bytes"} | {"truncate"})


@pytest.mark.parametrize(
    ("snippet", "modes"),
    [
        ("Path('x').open('xb')\n", {"xb"}),
        ("Path('x').open('wb')\n", {"wb"}),
        ("Path('x').open(mode)\n", {None}),
        ("Path('x').open()\n", {None}),
    ],
)
def test_the_open_mode_check_reads_every_call(snippet: str, modes: set[object]) -> None:
    assert _open_modes(snippet) == modes


# L5, "no H3 module reads `os.environ`": the ways to read this process's environment through `os`. `environb` and
# `getenvb` exist on Linux only. `putenv` writes and is not listed: the server's environment is always an explicit dict.
_ENVIRONMENT_READS = frozenset({"environ", "environb", "getenv", "getenvb"})


def _environment_reads(source: str) -> set[str]:
    """L5: each name in `_ENVIRONMENT_READS` reached as an attribute, as in `os.getenv`, or imported from `os`."""
    tree = ast.parse(source)
    found = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    found |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "os"
        for alias in node.names
    }
    return found & _ENVIRONMENT_READS


@pytest.mark.parametrize("module", H3_MODULES)
def test_h3_modules_read_no_environment_and_print_nothing(module: str) -> None:
    source = _source(module)
    assert _environment_reads(source) == set()
    assert not _imports(source) & {"logging", f"{PACKAGE}.scoring", f"{PACKAGE}.stats"}
    assert "print" not in {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("import os\nos.environ['A']\n", "environ"),
        ("import os\nos.environb[b'A']\n", "environb"),
        ("import os\nos.getenv('A')\n", "getenv"),
        ("import os\nos.getenvb(b'A')\n", "getenvb"),
        ("from os import environ\n", "environ"),
        ("from os import environ as e\n", "environ"),
        ("from os import getenv\n", "getenv"),
        ("from os import getenv as g\n", "getenv"),
    ],
)
def test_the_environment_check_catches_each_read(snippet: str, name: str) -> None:
    assert _environment_reads(snippet) == {name}


_MUTABLE_NODES = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp)
_MUTABLE_CALLS = frozenset({"list", "dict", "set", "bytearray", "defaultdict", "deque"})


def _module_level_mutables(source: str) -> list[int]:
    found: list[int] = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            value = node.value
            called = (
                isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in _MUTABLE_CALLS
            )
            if isinstance(value, _MUTABLE_NODES) or called:
                found.append(node.lineno)
    return found


def _default_calls(source: str) -> list[int]:
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            defaults = [*node.args.defaults, *(value for value in node.args.kw_defaults if value is not None)]
            found += [value.lineno for value in defaults if any(isinstance(n, ast.Call) for n in ast.walk(value))]
    return found


# Section 2: "No module-level mutable state, no print, no logging, no call in a default argument (ruff B)". The repo's
# ruff config ignores B008, and no ruff rule checks module-level state, so these two checks stand in.
@pytest.mark.parametrize("module", H3_MODULES)
def test_h3_modules_keep_no_module_state_and_call_nothing_in_a_default(module: str) -> None:
    source = _source(module)
    assert _module_level_mutables(source) == []
    assert _default_calls(source) == []


@pytest.mark.parametrize("snippet", ["X = []", "X: dict[str, int] = {}", "X = set()", "X = [n for n in ()]"])
def test_the_module_state_check_catches_each_form(snippet: str) -> None:
    assert _module_level_mutables(snippet) == [1]


@pytest.mark.parametrize("snippet", ["def f(x=g()): pass", "def f(*, x=time.monotonic()): pass", "f = lambda x=g(): x"])
def test_the_default_call_check_catches_each_form(snippet: str) -> None:
    assert _default_calls(snippet) == [1]


def test_h3_import_direction() -> None:
    assert {name for name in _imports(_source("inputs")) if _own(name)} == {
        f"{PACKAGE}.client",
        f"{PACKAGE}.gold",
        f"{PACKAGE}.probe",
        f"{PACKAGE}.schemas",
    }
    assert {name for name in _imports(_source("launch")) if _own(name)} == {f"{PACKAGE}.client"}
    assert {name for name in _imports(_source("records")) if _own(name)} == {
        f"{PACKAGE}.client",
        f"{PACKAGE}.extraction",
        f"{PACKAGE}.launch",
        f"{PACKAGE}.probe",
        f"{PACKAGE}.schemas",
    }
    assert {name for name in _imports(_source("runner")) if _own(name)} == {
        f"{PACKAGE}.client",
        f"{PACKAGE}.inputs",
        f"{PACKAGE}.launch",
        f"{PACKAGE}.records",
        f"{PACKAGE}.schemas",
    }


def test_the_package_init_is_one_docstring() -> None:
    tree = ast.parse(_source("__init__"))
    assert len(tree.body) == 1
    assert ast.get_docstring(tree)


def _machine_paths(text: str) -> list[str]:
    """T7. Case is ignored, because Windows and a WSL drive mount reach the users folder whatever the case. The needles
    are built so that this file never matches itself."""
    needles = ("/ho" + "me/", "/us" + "ers/", "wsl" + ".localhost", "wsl" + "$")
    lowered = text.lower()
    found = [needle for needle in needles if needle in lowered]
    return found + re.findall("[A-Za-z]:" + r"[/\\]+Users", text, flags=re.IGNORECASE)


@pytest.mark.parametrize(
    "path",
    [
        *(BAKEOFF / f"{module}.py" for module in H3_MODULES),
        *(
            Path(__file__).parent / name
            for name in (
                "_runner_fixtures.py",
                "test_inputs.py",
                "test_launch.py",
                "test_records.py",
                "test_runner_prerun.py",
                "test_runner_schedule.py",
                "test_runner_faults.py",
                "test_determinism.py",
            )
        ),
    ],
    ids=lambda path: path.name,
)
def test_h3_files_hold_no_machine_path(path: Path) -> None:
    assert _machine_paths(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "text",
    [
        "/ho" + "me/somebody/models",
        "/mnt/c/Us" + "ers/somebody",
        "C:" + chr(92) + "Users" + chr(92) + "somebody",
        "C:/" + "Users/somebody",
        "//wsl" + ".localhost/Ubuntu/x",
        chr(92) * 2 + "wsl" + "$" + chr(92) + "Ubuntu",
        "/mnt/c/" + "users/somebody",
        "c:" + chr(92) + "users" + chr(92) + "somebody",
        "C:" + chr(92) + "USERS" + chr(92) + "x",
    ],
)
def test_the_machine_path_check_catches_each_form(text: str) -> None:
    assert _machine_paths(text)


# T6. H2's modules are pinned by `test_r46_every_h2_test_module_blocks_sockets` in test_client_request.py.
_NO_SOCKETS_OR_PROCESSES = (
    "@pytest.fixture(autouse=True)\n"
    "def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:\n"
    "    block_sockets(monkeypatch)\n"
    "    block_processes(monkeypatch)\n"
)


def test_every_h3_test_module_refuses_sockets_and_processes() -> None:
    tests = Path(__file__).parent
    named = ("test_inputs.py", "test_launch.py", "test_records.py", "test_determinism.py")
    modules = [*(tests / name for name in named), *sorted(tests.glob("test_runner*.py"))]
    assert len(modules) >= 7
    for module in modules:
        assert _NO_SOCKETS_OR_PROCESSES in module.read_text(encoding="utf-8"), module.name


def test_import_direction() -> None:
    for module in ("gold", "extraction", "stats"):
        assert {name for name in _imports(_source(module)) if _own(name)} == set(), module
    assert {name for name in _imports(_source("scoring")) if _own(name)} == {
        f"{PACKAGE}.gold",
        f"{PACKAGE}.extraction",
        f"{PACKAGE}.stats",
    }
    assert {name for name in _imports(_source("__init__")) if _own(name)} == set()
    assert {name for name in _imports(_source("schemas")) if _own(name)} == set()
    assert {name for name in _imports(_source("probe")) if _own(name)} == {f"{PACKAGE}.extraction"}
    assert {name for name in _imports(_source("client")) if _own(name)} == {
        f"{PACKAGE}.extraction",
        f"{PACKAGE}.probe",
        f"{PACKAGE}.schemas",
    }


@pytest.mark.parametrize("module", MODULES)
def test_no_dynamic_code_or_imports(module: str) -> None:
    names = {node.id for node in ast.walk(ast.parse(_source(module))) if isinstance(node, ast.Name)}
    assert not names & {"__import__", "eval", "exec", "compile", "open"}


def test_the_core_runs_with_the_network_switched_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the scoring core must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9))
    gold = load_gold_set(write_version(tmp_path, *fixture_x()))
    parsed = parse_extraction(output_json([[2]]), source_id="syn_a", request_segment_ids=gold.segment_ids("syn_a"))
    assert parsed.claims[0].segment_ids == ("seg_002",)
    challenger = score_run(gold, run_xp(gold))
    default = score_run(gold, run_xm(gold))
    result = paired_bootstrap(challenger.per_conversation, default.per_conversation, seed=1, resamples=200)
    assert result.observed_difference == 1 - default.f1


def test_owner_markers_are_in_the_sources() -> None:
    sources = "\n".join(_source(module) for module in MODULES)
    for number in (*range(1, 5), *range(26, 31), 66, 69):
        assert f"NEEDS-OWNER(AMB-{number})" in sources
    assert "NEEDS-OWNER(AMB-52)" not in sources


@pytest.mark.parametrize(
    ("module", "citations"),
    [
        ("gold", ("§3.12", "§5.1", "§5.2", "§5.3")),
        ("extraction", ("§3.8", "§3.10", "PHASE-0 §2.2", "§5.3")),
        ("scoring", ("§5.1", "§5.2", "§5.3", "§6")),
        ("stats", ("§8", "§3.13")),
        ("schemas", ("§3.8", "§3.10", "§4", "PHASE-0 §2.2")),
        ("probe", ("§3.8", "§4", "§6", "PHASE-0 §2.2")),
        ("client", ("§3.2", "§3.3", "§3.5", "§3.6", "§3.7", "§3.8", "§3.9", "PHASE-0 §6.6", "PHASE-0 §7")),
        ("inputs", ("§3.10", "§3.11", "§3.12", "§3.13", "§4", "§11", "PHASE-0 §2.2")),
        ("launch", ("§1", "§3.2", "§3.3", "§3.5", "§11", "Annex A")),
        ("records", ("§3.12", "§3.13", "§9", "§10")),
        ("runner", ("§1", "§3.1", "§3.5", "§3.6", "§3.8", "§3.13", "§6")),
    ],
)
def test_module_docstrings_cite_their_sections(module: str, citations: tuple[str, ...]) -> None:
    docstring = ast.get_docstring(ast.parse(_source(module))) or ""
    assert "S2" in docstring
    for citation in citations:
        assert citation in docstring, citation


# G8: spans, claim text, call date, setting, speakers, timings, ASR confidence and model claim text never scored.
_UNREAD = frozenset(
    {
        "span_text",
        "claim_text",
        "call_date",
        "setting",
        "speaker",
        "speaker_label",
        "speaker_id",
        "drafted_by",
        "guide_version",
        "label_status",
        "segments",
        "start",
        "end",
        "confidence",
        "text",
    }
)


@pytest.mark.parametrize("module", ["scoring", "stats"])
def test_scoring_never_reads_unscored_fields(module: str) -> None:
    attributes = {node.attr for node in ast.walk(ast.parse(_source(module))) if isinstance(node, ast.Attribute)}
    assert not attributes & _UNREAD


@pytest.mark.parametrize("module", MODULES)
def test_no_python_311_features(module: str) -> None:
    tree = ast.parse(_source(module))
    banned_nodes = tuple(getattr(ast, name) for name in ("TryStar", "TypeAlias", "TypeVar") if hasattr(ast, name))
    assert not [node for node in ast.walk(tree) if isinstance(node, banned_nodes)]
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom | ast.Import) for alias in node.names
    }
    assert not imported & {"StrEnum", "Self", "override", "UTC", "tomllib", "ExceptionGroup", "TaskGroup"}


# T2: names that are new in 3.11 or 3.12, whether imported, used bare (builtins) or reached as attributes.
_PY311_NAMES = frozenset(
    {
        "BaseExceptionGroup",
        "ExceptionGroup",
        "LiteralString",
        "Never",
        "NotRequired",
        "Required",
        "Self",
        "StrEnum",
        "TaskGroup",
        "TypeAliasType",
        "TypeVarTuple",
        "UTC",
        "Unpack",
        "assert_never",
        "assert_type",
        "batched",
        "cbrt",
        "dataclass_transform",
        "exp2",
        "file_digest",
        "override",
        "reveal_type",
        "sumprod",
        "tomllib",
    }
)


def _names(tree: ast.AST) -> set[str]:
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import | ast.ImportFrom) for alias in node.names
    }
    return names


@pytest.mark.parametrize("module", MODULES)
def test_sources_parse_as_python_310_and_use_no_311_names(module: str) -> None:
    tree = ast.parse(_source(module), feature_version=(3, 10))  # rejects except*, type aliases and PEP 695 generics
    assert not _names(tree) & _PY311_NAMES


@pytest.mark.parametrize(
    "snippet",
    [
        "import datetime\nnow = datetime.UTC\n",
        "import typing\nx: typing.Self\n",
        "import hashlib\ndigest = hashlib.file_digest\n",
        "raise ExceptionGroup('x', [])\n",
        "from typing import Never\n",
    ],
)
def test_the_name_check_catches_attributes_and_builtins(snippet: str) -> None:
    assert _names(ast.parse(snippet)) & _PY311_NAMES


@pytest.mark.parametrize(
    "snippet",
    ["try:\n    pass\nexcept* ValueError:\n    pass\n", "type X = int\n", "def f[T](x: T) -> T:\n    return x\n"],
)
def test_feature_version_310_rejects_311_syntax(snippet: str) -> None:
    with pytest.raises(SyntaxError):
        ast.parse(snippet, feature_version=(3, 10))


_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_BACKSLASH = chr(92)


def _quote(token_text: str) -> str:
    body = token_text.lstrip("rRbBuUfF")
    return body[:3] if body[:3] in ("'''", '"""') else body[:1]


def _pep701_uses(source: str) -> list[str]:
    """f-string forms that only 3.12 accepts (PEP 701). ast.parse(feature_version=(3, 10)) accepts them on 3.12, and
    so do ruff and mypy, but 3.10 and 3.11 refuse them at import: a string inside a replacement field that reuses an
    enclosing f-string's quote, and a backslash, comment or line break inside a replacement field."""
    if _FSTRING_START is None:  # before 3.12 the tokenizer, and so the import, already refuses these forms
        return []
    found: list[str] = []
    stack: list[list] = []  # [quote, open braces] for each open f-string, innermost last
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        where = f"line {tok.start[0]}"
        in_field = any(braces > 0 for _, braces in stack)
        if in_field and tok.type in (tokenize.STRING, _FSTRING_START):
            inner = _quote(tok.string)
            for outer, _ in stack:
                if inner[0] == outer[0] and (len(outer) == 1 or len(inner) == 3):
                    found.append(f"{where}: a string in a replacement field reuses the f-string quote {outer}")
            if _BACKSLASH in tok.string:
                found.append(f"{where}: a backslash in a replacement field")
        elif tok.type == _FSTRING_MIDDLE and any(braces > 0 for _, braces in stack[:-1]) and _BACKSLASH in tok.string:
            found.append(f"{where}: a backslash in a replacement field")
        elif in_field and tok.type == tokenize.COMMENT:
            found.append(f"{where}: a comment in a replacement field")
        elif tok.type in (tokenize.NL, tokenize.NEWLINE) and any(b > 0 and len(q) == 1 for q, b in stack):
            found.append(f"{where}: a line break in a replacement field of a single-quoted f-string")
        if tok.type == _FSTRING_START:
            stack.append([_quote(tok.string), 0])
        elif tok.type == _FSTRING_END:
            stack.pop()
        elif tok.type == tokenize.OP and stack and tok.string in ("{", "}"):
            stack[-1][1] += 1 if tok.string == "{" else -1
    return found


@pytest.mark.parametrize("module", MODULES)
def test_no_pep_701_f_strings(module: str) -> None:
    assert _pep701_uses(_source(module)) == []


@pytest.mark.skipif(_FSTRING_START is None, reason="before 3.12 the parser itself refuses these forms")
@pytest.mark.parametrize(
    "snippet",
    [
        'x = f"{d["k"]}"\n',
        "x = f'{d['k']}'\n",
        'x = f"{f"{y}"}"\n',
        'x = f"""{d["""k"""]}"""\n',
        "x = f\"{'" + _BACKSLASH + "n'.join(y)}\"\n",
        'x = f"{y  # a comment\n}"\n',
        'x = f"{y\n}"\n',
    ],
)
def test_the_pep_701_check_catches_3_12_only_f_strings(snippet: str) -> None:
    ast.parse(snippet)  # valid on this interpreter, so only the check can catch it
    assert _pep701_uses(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "x = f\"{d['k']}\"\n",
        'x = f"""{d["k"]}"""\n',
        "x = f\"{f'{y}'}\"\n",
        'x = f"{y:>{w}} {y!r} {y=} {{z}}"\n',
        'x = f"""{\n    y\n}"""\n',
        'x = f"' + _BACKSLASH + 'n{y}"\n',
        'x = (f"{a}"\n     f"{b}")\n',
    ],
)
def test_the_pep_701_check_passes_310_f_strings(snippet: str) -> None:
    ast.parse(snippet)
    assert _pep701_uses(snippet) == []
