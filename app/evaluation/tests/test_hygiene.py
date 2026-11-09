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
from types import MappingProxyType

import pytest

from app.evaluation.bakeoff.extraction import parse_extraction
from app.evaluation.bakeoff.gold import load_gold_set
from app.evaluation.bakeoff.report import OPEN_RULINGS
from app.evaluation.bakeoff.scoring import score_run
from app.evaluation.bakeoff.stats import paired_bootstrap
from app.evaluation.tests._fixtures import fixture_x, output_json, run_xm, run_xp, write_version

PACKAGE = "app.evaluation.bakeoff"
BAKEOFF = Path(__file__).resolve().parents[1] / "bakeoff"
H2_MODULES = ("schemas", "probe", "client")
H3_MODULES = ("inputs", "launch", "records", "runner")
# H4 spec section 2, hygiene item 1: the step that creates each H4 module appends it here, so that every check over
# MODULES covers it from then on.
H4_MODULES: tuple[str, ...] = ("evidence", "gates", "decision", "report", "entry")
# H4a brief section 7, items 13 and 14: each step adds its new test module here.
H4_TEST_FILES: tuple[str, ...] = (
    "test_evidence.py",
    "test_gates.py",
    "test_decision.py",
    "test_report.py",
    "test_entry.py",
)
EARLIER_MODULES = ("__init__", "gold", "extraction", "scoring", "stats", *H2_MODULES, *H3_MODULES)
MODULES = (*EARLIER_MODULES, *H4_MODULES)
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
# H4 spec section 2, hygiene item 2: the entry point parses its command line and writes to stdout and stderr.
EXTRA_ALLOWED = {
    "client": frozenset({"httpx", "time"}),
    "launch": frozenset({"subprocess", "time"}),
    "entry": frozenset({"argparse", "sys"}),
}


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


# H4 spec section 2, hygiene item 9: `records` defines `mark_scored`, and only `entry` calls it, between the integrity
# checks and the scoring (S2 §3.13 "logged before its scores are computed").
@pytest.mark.parametrize("module", [module for module in MODULES if module not in ("records", "entry")])
def test_only_the_entry_sets_scored_markers(module: str) -> None:
    assert "mark_scored" not in _attributes(_source(module))


def test_the_entry_sets_the_scored_markers() -> None:
    assert "mark_scored" in _attributes(_source("entry"))
    assert "mark_scored" in _attributes("store.mark_scored(key)\n")
    assert "mark_scored" not in _attributes("marked = view.marked(key)\n")


@pytest.mark.parametrize("module", [module for module in MODULES if module != "entry"])
def test_only_the_entry_may_import_argparse_and_sys(module: str) -> None:
    """Hygiene item 2: the allowlist refuses argparse and sys everywhere but in `entry`."""
    assert _disallowed_for(module, _source(module) + "\nimport argparse\nimport sys\n") == {"argparse", "sys"}


def test_the_entry_source_passes_with_argparse_and_sys() -> None:
    source = _source("entry")
    assert {"argparse", "sys"} <= _imports(source)
    assert _disallowed_for("entry", source) == set()
    assert _disallowed_for("entry", source + "\nimport subprocess\n") == {"subprocess"}


def _entry_file_reaches(source: str) -> list[str]:
    """Hygiene item 8: the entry point reads files only through the injected tools, and passes the sync on uncalled;
    every write goes through `records`."""
    broken = []
    if not _attributes(source) & _FILE_ATTRIBUTES <= {"read_bytes", "fsync"}:
        broken.append("file attributes beyond read_bytes and fsync")
    if "fsync" in _called_attributes(source):
        broken.append("fsync called")
    if _open_modes(source) != set():
        broken.append("open called")
    return broken


def test_entry_reaches_files_only_by_reading() -> None:
    assert _entry_file_reaches(_source("entry")) == []


@pytest.mark.parametrize(
    ("snippet", "rule"),
    [
        ("Path(run_dir, 'x').write_bytes(b'x')\n", "file attributes beyond read_bytes and fsync"),
        ("os.remove(path)\n", "file attributes beyond read_bytes and fsync"),
        ("os.fsync(1)\n", "fsync called"),
        ("Path(run_dir, 'x').open('xb')\n", "file attributes beyond read_bytes and fsync"),
    ],
)
def test_the_entry_file_check_catches_each_reach(snippet: str, rule: str) -> None:
    assert rule in _entry_file_reaches(_source("entry") + "\n" + snippet)
    assert _entry_file_reaches("tools.read_bytes(path)\nstore = attach(path, fsync=os.fsync)\n") == []


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


@pytest.mark.parametrize("module", (*H3_MODULES, *H4_MODULES))
def test_h3_modules_read_no_environment_and_print_nothing(module: str) -> None:
    """H4 spec section 2, hygiene item 10: H4 modules too, but the scoring and stats ban is for H3 modules only."""
    source = _source(module)
    assert _environment_reads(source) == set()
    assert "logging" not in _imports(source)
    if module in H3_MODULES:
        assert not _imports(source) & {f"{PACKAGE}.scoring", f"{PACKAGE}.stats"}
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
@pytest.mark.parametrize("module", (*H3_MODULES, *H4_MODULES))
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


# H4 spec section 2, hygiene item 12. Every module H4 may add, created or not, so that the earlier modules' rule holds
# before the module exists. Each H4 step adds its module's row of allowed own-package imports.
H4_PLANNED = frozenset({"evidence", "gates", "decision", "report", "entry", "machine"})
H4_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "evidence": frozenset({"records"}),
    "gates": frozenset(
        {
            "client",
            "evidence",
            "extraction",
            "gold",
            "inputs",
            "launch",
            "records",
            "runner",
            "schemas",
            "scoring",
            "stats",
        }
    ),
    "decision": frozenset({"gates", "launch", "runner", "scoring", "stats"}),
    "report": frozenset({"decision", "evidence", "gates", "launch", "records", "runner", "scoring", "stats"}),
    "entry": frozenset(
        {"decision", "evidence", "gates", "gold", "inputs", "launch", "records", "report", "runner", "stats"}
    ),
}
# H4a brief section 5: these rows are exact, the others are subsets.
H4_EXACT_IMPORTS = frozenset({"evidence"})
# H4a brief section 12, amendment 1: evidence's standard-library imports.
EVIDENCE_STDLIB = frozenset({"__future__", "collections.abc", "dataclasses", "typing"})


def _own_modules(source: str) -> set[str]:
    """The package modules a source imports, by short name, in every form: `from . import gates` and
    `from app.evaluation.bakeoff import gates` both import `gates`, which `_imports` reports as the package alone."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                module = node.module
            elif node.module is not None and _own(node.module):
                module = node.module[len(PACKAGE) + 1 :] or None
            else:
                continue
            if module:
                names.add(module.split(".")[0])
            else:
                names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(
                alias.name[len(PACKAGE) + 1 :].split(".")[0]
                for alias in node.names
                if _own(alias.name) and alias.name != PACKAGE
            )
    return names


def test_h4_import_direction() -> None:
    assert set(H4_MODULES) <= H4_PLANNED
    assert set(H4_ALLOWED_IMPORTS) == set(H4_MODULES)
    for module in EARLIER_MODULES:
        assert not _own_modules(_source(module)) & H4_PLANNED, module
    for module in MODULES:
        assert "entry" not in _own_modules(_source(module)), module
    for module, allowed in H4_ALLOWED_IMPORTS.items():
        assert _own_modules(_source(module)) <= allowed, module
        if module in H4_EXACT_IMPORTS:
            assert _own_modules(_source(module)) == allowed, module


def test_evidence_imports_only_records_and_its_named_standard_modules() -> None:
    source = _source("evidence")
    assert _own_modules(source) == {"records"}
    assert {name for name in _imports(source) if not _own(name)} <= EVIDENCE_STDLIB


@pytest.mark.parametrize(
    ("line", "rule"),
    [
        ("from . import gates", "own-package imports are exactly records"),
        ("from .machine import load_evidence", "own-package imports are exactly records"),
        ("import os", "standard-library imports are within EVIDENCE_STDLIB"),
        ("from fractions import Fraction", "standard-library imports are within EVIDENCE_STDLIB"),
        ("import collections", "standard-library imports are within EVIDENCE_STDLIB"),
    ],
)
def test_the_evidence_import_check_catches_each_extra_import(line: str, rule: str) -> None:
    source = _source("evidence") + f"\n{line}\n"
    caught = (
        _own_modules(source) != {"records"}
        or not {name for name in _imports(source) if not _own(name)} <= EVIDENCE_STDLIB
    )
    assert caught, rule


@pytest.mark.parametrize(
    "line",
    [
        "from . import machine",
        "from .decision import decide",
        "from .report import build_report",
        "from . import entry",
    ],
)
def test_the_gates_row_catches_a_later_or_machine_import(line: str) -> None:
    """H4a brief section 5: gates never imports machine, decision, report or entry."""
    assert not _own_modules(_source("gates") + f"\n{line}\n") <= H4_ALLOWED_IMPORTS["gates"]
    assert _own_modules(_source("gates")) <= H4_ALLOWED_IMPORTS["gates"]


@pytest.mark.parametrize(
    "line",
    [
        "from . import machine",
        "from .evidence import AttemptEvidence",
        "from .records import RunKey",
        "from .client import CallRecord",
        "from .report import build_report",
        "from . import entry",
    ],
)
def test_the_decision_row_catches_an_import_outside_its_set(line: str) -> None:
    """H4a brief section 5: decision imports only from gates, launch, runner, scoring and stats."""
    assert not _own_modules(_source("decision") + f"\n{line}\n") <= H4_ALLOWED_IMPORTS["decision"]
    assert _own_modules(_source("decision")) <= H4_ALLOWED_IMPORTS["decision"]


@pytest.mark.parametrize(
    "line",
    [
        "from . import machine",
        "from .client import CallRecord",
        "from .gold import load_gold_set",
        "from .inputs import freeze_inputs",
        "from . import entry",
    ],
)
def test_the_report_row_catches_an_import_outside_its_set(line: str) -> None:
    """H4a brief section 5: report imports only from decision, evidence, gates, launch, records, runner, scoring and
    stats. It never loads gold or inputs, and never imports machine or entry."""
    assert not _own_modules(_source("report") + f"\n{line}\n") <= H4_ALLOWED_IMPORTS["report"]
    assert _own_modules(_source("report")) <= H4_ALLOWED_IMPORTS["report"]


# H4a brief section 5: only records writes reports, and only entry loads runs and sets scored markers. The list of
# report numbers reaches `build_report` as a value, which `entry` reads with `records.report_numbers`.
_REPORT_NEVER = frozenset({"write_report", "mark_scored", "load_run", "attach"})


def test_the_report_module_builds_the_body_and_writes_nothing() -> None:
    assert not _names(ast.parse(_source("report"))) & _REPORT_NEVER


@pytest.mark.parametrize(
    "snippet",
    ["from .records import write_report\n", "records.write_report(d, md, body)\n", "store.mark_scored(key)\n"],
)
def test_the_report_write_check_catches_each_reach(snippet: str) -> None:
    assert _names(ast.parse(snippet)) & _REPORT_NEVER


@pytest.mark.parametrize(
    "line",
    [
        "from . import machine",
        "from .client import ModelServerClient",
        "from .scoring import score_run",
        "from .extraction import parse_extraction",
        "from .schemas import canonical_sha256",
    ],
)
def test_the_entry_row_catches_an_import_outside_its_set(line: str) -> None:
    """H4a brief section 5: entry imports from decision, evidence, gates, gold, inputs, launch, records, report, runner
    and stats only. H4b adds client and machine."""
    assert not _own_modules(_source("entry") + f"\n{line}\n") <= H4_ALLOWED_IMPORTS["entry"]
    assert _own_modules(_source("entry")) <= H4_ALLOWED_IMPORTS["entry"]


def test_decision_calls_the_stats_rules_through_the_module() -> None:
    """The test_decision stubs replace `stats.determinism_variant` and `stats.margin_case1`, so decision must reach
    them as attributes of `stats`, never as names imported from it (spec section 8, the not-loaded rows)."""
    tree = ast.parse(_source("decision"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "stats" in imported
    assert not imported & {"determinism_variant", "margin_case1"}
    assert {"determinism_variant", "margin_case1"} <= _called_attributes(_source("decision"))
    assert "margin_case1" not in _called_attributes("from .stats import margin_case1\nmargin_case1(a, b)\n")


def test_the_exact_row_catches_a_missing_import() -> None:
    without_records = _source("evidence").replace("from .records import RunKey", "RunKey = object")
    assert _own_modules(without_records) == set()
    assert _own_modules(without_records) != H4_ALLOWED_IMPORTS["evidence"]


# H4a brief section 7, item 7: the pure H4 modules touch no file, process, thread, clock or log. `_FILE_ATTRIBUTES`
# names and the clock names count however they are reached.
_PURE_H4 = ("evidence", "gates", "decision", "report")
_PURE_NEVER_IMPORTS = frozenset({"os", "pathlib", "io", "subprocess", "threading", "time", "logging"})
_CLOCK_NAMES = frozenset({"stamp", "now", "monotonic"})


def _impure_reaches(source: str) -> set[str]:
    found = {name for name in _imports(source) if name.split(".")[0] in _PURE_NEVER_IMPORTS}
    return found | (_names(ast.parse(source)) & (_FILE_ATTRIBUTES | _CLOCK_NAMES))


@pytest.mark.parametrize("module", _PURE_H4)
def test_pure_h4_modules_touch_nothing(module: str) -> None:
    assert module in H4_MODULES
    assert _impure_reaches(_source(module)) == set()


@pytest.mark.parametrize("module", _PURE_H4)
def test_pure_h4_modules_never_import_machine(module: str) -> None:
    """H4a brief section 5: none of evidence, gates, decision and report imports machine, and this is permanent, so
    it holds whatever H4b adds to the import rows."""
    assert "machine" not in H4_ALLOWED_IMPORTS[module]
    assert "machine" not in _own_modules(_source(module))
    assert "machine" in _own_modules(_source(module) + "\nfrom .machine import load_evidence\n")


# H4a brief section 5, "Only records writes reports": records defines `write_report`, entry calls it once with the
# body that report builds, and no other module names it.
@pytest.mark.parametrize("module", [module for module in MODULES if module not in ("records", "entry")])
def test_only_the_entry_hands_a_report_to_records(module: str) -> None:
    assert "write_report" not in _names(ast.parse(_source(module)))


def test_the_entry_hands_the_report_to_records() -> None:
    assert "write_report" in _names(ast.parse(_source("entry")))
    assert "write_report" in _names(ast.parse("records.write_report(d, md, body)\n"))
    assert "write_report" not in _names(ast.parse("body = report.build_report(view)\n"))


def test_the_pure_check_passes_plain_value_code() -> None:
    legal = "from .records import RunKey\nfrom dataclasses import dataclass\npeak = max(s.used_mib for s in gpu)\n"
    assert _impure_reaches(legal) == set()


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("import os\n", "os"),
        ("import os.path\n", "os.path"),
        ("from pathlib import Path\n", "pathlib"),
        ("import threading\n", "threading"),
        ("from time import sleep\n", "time"),
        ("import logging\n", "logging"),
        ("tools.read_bytes(path)\n", "read_bytes"),
        ("deps.fsync(1)\n", "fsync"),
        ("x = call.stamp\n", "stamp"),
        ("x = clock.now()\n", "now"),
        ("x = monotonic()\n", "monotonic"),
    ],
)
def test_the_pure_check_catches_each_reach(snippet: str, name: str) -> None:
    assert name in _impure_reaches(snippet)


@pytest.mark.parametrize(
    ("snippet", "names"),
    [
        ("from . import gates\n", {"gates"}),
        ("from . import gates, report as r\n", {"gates", "report"}),
        ("from .gates import Gate\n", {"gates"}),
        ("from .records import RunKey\nimport os\n", {"records"}),
        (f"from {PACKAGE}.entry import main\n", {"entry"}),
        (f"from {PACKAGE} import decision\n", {"decision"}),
        (f"import {PACKAGE}.report\n", {"report"}),
        (f"import {PACKAGE}.evidence as evidence\n", {"evidence"}),
        ("import gates\nfrom report import x\nimport app.evaluation\n", set()),
        ("def f():\n    from . import machine\n", {"machine"}),
    ],
)
def test_the_own_module_check_reads_every_import_form(snippet: str, names: set[str]) -> None:
    assert _own_modules(snippet) == names


@pytest.mark.parametrize("line", ["from . import gates", "from .report import build_report", "from . import entry"])
def test_the_h4_direction_check_catches_an_earlier_module_importing_h4(line: str) -> None:
    assert _own_modules(_source("runner") + f"\n{line}\n") & H4_PLANNED
    assert not _own_modules(_source("runner")) & H4_PLANNED


def test_the_package_init_is_one_docstring() -> None:
    tree = ast.parse(_source("__init__"))
    assert len(tree.body) == 1
    assert ast.get_docstring(tree)


_STATED_IMPORTS = re.compile(r"((?:`\w+`(?:, | and )?)+) imports? (nothing from this package|(?:`\w+`(?:, | and )?)+)")


def _stated_imports(docstring: str) -> dict[str, set[str]]:
    """The import direction the package docstring states, as `<module>` imports `<a>`, `<b>` and `<c>`."""
    stated: dict[str, set[str]] = {}
    for subjects, objects in _STATED_IMPORTS.findall(" ".join(docstring.split())):
        for subject in re.findall(r"`(\w+)`", subjects):
            stated[subject] = set(re.findall(r"`(\w+)`", objects))
    return stated


def test_the_package_docstring_states_every_module_and_its_imports() -> None:
    """H4 spec section 2, item 4: the `__init__` module map and import direction are kept in step with the code."""
    docstring = ast.get_docstring(ast.parse(_source("__init__"))) or ""
    for module in MODULES[1:]:
        assert f"- `{module}`: " in docstring, module
    assert _stated_imports(docstring) == {module: _own_modules(_source(module)) for module in MODULES[1:]}
    assert "No H1 to H3 module imports an H4 module, and nothing imports\n`entry`." in docstring


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "`decision` imports `gates`, `launch`, `runner` and `stats`",
            "`decision` imports `gates`, `runner` and `stats`",
        ),
        ("`evidence` imports\n`records`", "`evidence` imports\n`records` and `launch`"),
        ("`gold`, `extraction`, `schemas` and `stats` import", "`gold`, `extraction` and `stats` import"),
    ],
)
def test_the_package_docstring_check_catches_a_drifted_sentence(old: str, new: str) -> None:
    docstring = ast.get_docstring(ast.parse(_source("__init__"))) or ""
    assert old in docstring
    actual = {module: _own_modules(_source(module)) for module in MODULES[1:]}
    assert _stated_imports(docstring.replace(old, new)) != actual


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
        *(BAKEOFF / f"{module}.py" for module in (*H3_MODULES, *H4_MODULES)),
        *(Path(__file__).parent / name for name in (*H4_TEST_FILES, "_h4_fixtures.py")),
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


@pytest.mark.parametrize("name", H4_TEST_FILES)
def test_every_h4_test_module_refuses_sockets_and_processes(name: str) -> None:
    assert _NO_SOCKETS_OR_PROCESSES in (Path(__file__).parent / name).read_text(encoding="utf-8")


def test_the_socket_check_catches_a_module_without_the_fixture() -> None:
    assert _NO_SOCKETS_OR_PROCESSES not in _NO_SOCKETS_OR_PROCESSES.replace("    block_processes(monkeypatch)\n", "")


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


# H4a brief section 8: the final H4a tuple of NEEDS-OWNER numbers, each with a marked site. H4b adds 108 and 140 with
# their sites, and H5 adds 133.
OWNER_MARKERS = (*range(1, 5), *range(26, 31), 66, 69, 106, 116, 129, 130, 134, 135, 136, 137, 139)


def test_owner_markers_are_in_the_sources() -> None:
    sources = "\n".join(_source(module) for module in MODULES)
    for number in OWNER_MARKERS:
        assert f"NEEDS-OWNER(AMB-{number})" in sources
    assert "NEEDS-OWNER(AMB-52)" not in sources


# H4a brief section 8: the module that holds each H4a marker, with its count there. AMB-66 keeps its H3 site in records
# beside its new gates site, and AMB-106 has two gates sites.
OWNER_MARKER_SITES = MappingProxyType(
    {
        66: (("records", 1), ("gates", 1)),
        106: (("gates", 2),),
        116: (("gates", 1),),
        135: (("gates", 1),),
        139: (("gates", 1),),
        129: (("decision", 1),),
        130: (("decision", 1),),
        134: (("decision", 1),),
        136: (("report", 1),),
        137: (("report", 1),),
    }
)


@pytest.mark.parametrize("number", sorted(OWNER_MARKER_SITES))
def test_each_h4a_owner_marker_sits_in_its_listed_module(number: int) -> None:
    for module, count in OWNER_MARKER_SITES[number]:
        assert _source(module).count(f"NEEDS-OWNER(AMB-{number})") == count, (number, module)
    assert number in OWNER_MARKERS


def test_a_moved_owner_marker_is_caught() -> None:
    moved = _source("report").replace("NEEDS-OWNER(AMB-136)", "NEEDS-OWNER(AMB-999)")
    assert moved.count("NEEDS-OWNER(AMB-136)") != dict(OWNER_MARKER_SITES[136])["report"]


_OWNER_MARKER = re.compile(r"NEEDS-OWNER\(AMB-(\d+)\)")
_OWNER_MARKER_LINE = re.compile(r"# NEEDS-OWNER\(AMB-\d+\): \S")


def _owner_marker_problems(sources: str) -> list[str]:
    """Every marker in a source is listed in OWNER_MARKERS, and each has the form `# NEEDS-OWNER(AMB-n): <text>`."""
    found = _OWNER_MARKER.findall(sources)
    problems = [f"AMB-{number} is not listed" for number in sorted({int(n) for n in found} - set(OWNER_MARKERS))]
    if len(_OWNER_MARKER_LINE.findall(sources)) != len(found):
        problems.append("a marker is not in the form '# NEEDS-OWNER(AMB-n): <text>'")
    return problems


def test_every_owner_marker_is_listed_and_well_formed() -> None:
    assert _owner_marker_problems("\n".join(_source(module) for module in MODULES)) == []


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("# NEEDS-OWNER(AMB-77): an unlisted reading", "AMB-77 is not listed"),
        ("# NEEDS-OWNER(AMB-66) no colon", "a marker is not in the form '# NEEDS-OWNER(AMB-n): <text>'"),
        ("x = 1  # see NEEDS-OWNER(AMB-66)", "a marker is not in the form '# NEEDS-OWNER(AMB-n): <text>'"),
    ],
)
def test_the_owner_marker_check_catches_each_problem(line: str, problem: str) -> None:
    assert problem in _owner_marker_problems(f"{line}\n")
    assert _owner_marker_problems("# NEEDS-OWNER(AMB-66): the probe calls of session 1\n") == []


def test_the_report_lists_every_open_ruling_that_has_a_site() -> None:
    """Spec section 3: `open_rulings` holds the NEEDS-OWNER numbers, so it must not drift from the marked sites."""
    assert OPEN_RULINGS == OWNER_MARKERS


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
        ("evidence", ("§3.13", "§6", "§9")),
        ("gates", ("§3.6", "§3.8", "§3.13", "§5", "§6", "§9")),
        ("decision", ("§1", "§2", "§3.6", "§3.13", "§8")),
        ("report", ("§3.13", "§9", "§10", "Annex A")),
        ("entry", ("§3.2", "§3.12", "§11", "§3.13", "§9")),
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


# H4a brief section 7, item 17: gates and decision join scoring and stats.
@pytest.mark.parametrize("module", ["scoring", "stats", "gates", "decision"])
def test_scoring_never_reads_unscored_fields(module: str) -> None:
    attributes = {node.attr for node in ast.walk(ast.parse(_source(module))) if isinstance(node, ast.Attribute)}
    assert not attributes & _UNREAD


# The text of a row, a claim, a model answer or an error never reaches the gates, the decision, the report or the
# evidence: they read ids, counts, hashes and outcomes only (S2 §9; H4 spec section 2, hygiene item 17).
_TEXT_FIELDS = frozenset({"text", "utterance", "claim_text", "reasoning_content", "error_text"})


def _text_reaches(source: str) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)} & _TEXT_FIELDS


@pytest.mark.parametrize("module", ["gates", "decision", "report", "evidence"])
def test_h4_modules_never_read_text_fields(module: str) -> None:
    assert _text_reaches(_source(module)) == set()


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("x = row.utterance\n", "utterance"),
        ("x = item.claim_text\n", "claim_text"),
        ("x = claim.text\n", "text"),
        ("x = attempt.reasoning_content\n", "reasoning_content"),
        ("x = call.attempts[0].error_text\n", "error_text"),
    ],
)
def test_the_text_field_check_catches_each_read(snippet: str, name: str) -> None:
    assert _text_reaches(snippet) == {name}
    assert _text_reaches("x = attempt.content\ny = call.item_id\n") == set()


@pytest.mark.parametrize(("snippet", "name"), [("x = match.start()\n", "start"), ("x = gold.segments\n", "segments")])
def test_the_unread_check_catches_each_read(snippet: str, name: str) -> None:
    assert {node.attr for node in ast.walk(ast.parse(snippet)) if isinstance(node, ast.Attribute)} & _UNREAD == {name}


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
