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
H4_MODULES: tuple[str, ...] = ("evidence", "gates", "decision", "report", "entry", "machine")
# H4a brief section 7, items 13 and 14: each step adds its new test module here.
H4_TEST_FILES: tuple[str, ...] = (
    "test_evidence.py",
    "test_gates.py",
    "test_decision.py",
    "test_report.py",
    "test_entry.py",
    "test_machine.py",
)
# H5a brief section 4.7, items 1 and 11: the H5 modules and their test modules; every check over MODULES covers them.
H5_MODULES: tuple[str, ...] = ("frozen", "cli", "wrapper")
H5_PLANNED = frozenset(H5_MODULES)
H5_TEST_FILES: tuple[str, ...] = ("test_frozen.py", "test_cli.py", "test_wrapper.py", "test_launch_helper.py")
EARLIER_MODULES = ("__init__", "gold", "extraction", "scoring", "stats", *H2_MODULES, *H3_MODULES)
MODULES = (*EARLIER_MODULES, *H4_MODULES, *H5_MODULES)
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
# H4 spec section 2, hygiene item 2: the entry point parses its command line and writes to stdout and stderr, and
# machine runs the /proc thread. H5a brief section 4.7, item 4: the run CLI does as the entry point does, and the
# wrapper writes to the saved console and puts a traceback in its log.
EXTRA_ALLOWED = {
    "client": frozenset({"httpx", "time"}),
    "launch": frozenset({"subprocess", "time"}),
    "entry": frozenset({"argparse", "sys"}),
    "machine": frozenset({"threading"}),
    "cli": frozenset({"argparse", "sys"}),
    "wrapper": frozenset({"sys", "traceback"}),
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


# Hygiene item 2, widened by the H5a brief section 4.7, item 4: argparse only in `entry` and `cli`; sys only in those
# and `wrapper`; traceback only in `wrapper`.
@pytest.mark.parametrize("module", [module for module in MODULES if module not in ("entry", "cli")])
def test_only_the_entry_and_the_cli_may_import_argparse(module: str) -> None:
    assert _disallowed_for(module, _source(module) + "\nimport argparse\n") == {"argparse"}


@pytest.mark.parametrize("module", [module for module in MODULES if module not in ("entry", "cli", "wrapper")])
def test_only_the_entry_the_cli_and_the_wrapper_may_import_sys(module: str) -> None:
    assert _disallowed_for(module, _source(module) + "\nimport sys\n") == {"sys"}


@pytest.mark.parametrize("module", [module for module in MODULES if module != "wrapper"])
def test_only_the_wrapper_may_import_traceback(module: str) -> None:
    assert _disallowed_for(module, _source(module) + "\nimport traceback\n") == {"traceback"}


def test_the_cli_source_passes_with_argparse_and_sys() -> None:
    source = _source("cli")
    assert {"argparse", "sys"} <= _imports(source)
    assert _disallowed_for("cli", source) == set()
    assert _disallowed_for("cli", source + "\nimport subprocess\nimport threading\n") == {"subprocess", "threading"}


def test_the_wrapper_source_passes_with_sys_and_traceback() -> None:
    source = _source("wrapper")
    assert {"sys", "traceback"} <= _imports(source)
    assert _disallowed_for("wrapper", source) == set()
    assert _disallowed_for("wrapper", source + "\nimport argparse\nimport subprocess\n") == {"argparse", "subprocess"}


def _os_attributes(source: str) -> set[str]:
    """The names a source reaches as `os.<name>`, or through an alias of an `import os` or `import os.x`, or imports
    with `from os import`. Two markers stand for the forms that hide a name: `<os alias>` for such an alias, and
    `<bare os>` for any use of the module name other than `os.<name>`, such as `getattr(os, ...)`, `f(os)` or
    `x = os`. A pinned set holds neither."""
    tree = ast.parse(source)
    names = {"os"}
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (alias.name == "os" or alias.name.startswith("os.")) and alias.asname:
                    names.add(alias.asname)
                    reached.add("<os alias>")
    attribute_values = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in names:
            reached.add(node.attr)
        elif isinstance(node, ast.Name) and node.id in names and id(node) not in attribute_values:
            reached.add("<bare os>")
    return reached | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and not node.level and node.module == "os"
        for alias in node.names
    }


def test_the_os_attributes_of_the_h5_modules_are_pinned() -> None:
    """H5a brief section 4.7, item 4: `cli` reaches `os` for `fsync` alone, `wrapper` for the descriptor calls of its
    redirect alone, and `frozen` does not import it."""
    assert _os_attributes(_source("cli")) == {"fsync"}
    assert _os_attributes(_source("wrapper")) == {"dup", "dup2", "close", "write"}
    assert "os" not in {name.split(".")[0] for name in _imports(_source("frozen"))}
    assert _os_attributes(_source("frozen")) == set()


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("os.path.realpath(p)\n", "path"),
        ("os.getcwd()\n", "getcwd"),
        ("from os import fsync, sep\n", "sep"),
        ("import os as _os\n_os.unlink(p)\n", "unlink"),
        ("import os as _os\n_os.unlink(p)\n", "<os alias>"),
        ("import os.path as osp\nosp.join(a, b)\n", "<os alias>"),
        ("getattr(os, 'unlink')(p)\n", "<bare os>"),
        ("tool = os\n", "<bare os>"),
        ("call(os)\n", "<bare os>"),
    ],
)
def test_the_os_attribute_check_catches_each_form(snippet: str, name: str) -> None:
    assert name in _os_attributes(_source("cli") + "\n" + snippet)


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


@pytest.mark.parametrize("module", [module for module in MODULES if module != "machine"])
def test_only_machine_may_import_threading(module: str) -> None:
    assert _disallowed_for(module, _source(module) + "\nimport threading\n") == {"threading"}


def test_the_machine_source_passes_with_threading() -> None:
    source = _source("machine")
    assert {"threading", "os"} <= _imports(source)
    assert _disallowed_for("machine", source) == set()
    assert _disallowed_for("machine", source + "\nimport subprocess\n") == {"subprocess"}


# The machine module reaches files, processes and clocks only through the launch tools, so it imports none of the other
# ways out; hashlib is among them because the contention argv is hashed through `schemas`.
_MACHINE_NEVER_IMPORTS = frozenset(
    {
        "pathlib",
        "io",
        "shutil",
        "tempfile",
        "logging",
        "subprocess",
        "httpx",
        "socket",
        "urllib",
        "hashlib",
        "sys",
        "time",
    }
)


def _machine_way_out(source: str) -> set[str]:
    return {name.split(".")[0] for name in _imports(source)} & _MACHINE_NEVER_IMPORTS


def test_machine_imports_no_other_way_out() -> None:
    assert _machine_way_out(_source("machine")) == set()


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("from pathlib import Path", "pathlib"),
        ("import io", "io"),
        ("import shutil", "shutil"),
        ("from tempfile import mkdtemp", "tempfile"),
        ("import logging", "logging"),
        ("import subprocess", "subprocess"),
        ("import httpx", "httpx"),
        ("import socket", "socket"),
        ("from urllib import request", "urllib"),
        ("import hashlib", "hashlib"),
        ("import sys", "sys"),
        ("from time import monotonic", "time"),
    ],
)
def test_the_machine_import_ban_catches_each_name(line: str, name: str) -> None:
    assert _machine_way_out(_source("machine") + f"\n{line}\n") == {name}


_MACHINE_FILE_NAMES = frozenset({"read_bytes", "listdir", "fsync"})
_RECORD_WRITES = frozenset({"log_fault", "void_run", "mark_scored", "write_text", "write_bytes", "write_report"})


def _proc_listing(tree: ast.Module) -> ast.AST | None:
    """The func of the one `os.listdir("/proc")` inside `_list_pids`, the only file reach not through the tools."""
    for function in tree.body:
        if isinstance(function, ast.FunctionDef) and function.name == "_list_pids":
            for node in ast.walk(function):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "listdir"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and len(node.args) == 1
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "/proc"
                    and not node.keywords
                ):
                    return node.func
    return None


def _unrouted_file_attributes(source: str) -> set[str]:
    """File names reached on anything but `<x>.launch` or a name `tools`, the `/proc` listing excepted."""
    tree = ast.parse(source)
    allowed = _proc_listing(tree)
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr in _FILE_ATTRIBUTES
        and node is not allowed
        and not (isinstance(node.value, ast.Attribute) and node.value.attr == "launch")
        and not (isinstance(node.value, ast.Name) and node.value.id == "tools")
    }


def _machine_file_problems(source: str) -> list[str]:
    """The machine module touches files only through the injected tools and writes no record."""
    problems = []
    if _open_modes(source) != set():
        problems.append("an open call")
    if not _attributes(source) & _FILE_ATTRIBUTES <= _MACHINE_FILE_NAMES:
        problems.append("a file name beyond read_bytes, listdir and fsync")
    if _unrouted_file_attributes(source):
        problems.append("a file reach outside the injected tools")
    names = _names(ast.parse(source))
    if names & _RECORD_WRITES or any(name.startswith("append_") for name in names):
        problems.append("a record write")
    return problems


def test_machine_touches_files_only_through_tools() -> None:
    source = _source("machine")
    assert _machine_file_problems(source) == []
    assert source.count("os.listdir(") == 1
    assert _proc_listing(ast.parse(source)) is not None


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        ("Path('/etc/x').read_bytes()\n", "a file reach outside the injected tools"),
        ("from pathlib import Path\nPath('/etc/x').read_bytes()\n", "a file reach outside the injected tools"),
        ("import os\nos.listdir('/etc')\n", "a file reach outside the injected tools"),
        ("x.fsync(1)\n", "a file reach outside the injected tools"),
        ("store.append_call(key)\n", "a record write"),
        ("Path('x').open('xb')\n", "an open call"),
    ],
)
def test_the_machine_file_check_catches_each_reach(snippet: str, problem: str) -> None:
    assert problem in _machine_file_problems(_source("machine") + "\n" + snippet)


def test_the_machine_import_ban_also_catches_the_pathlib_reach() -> None:
    snippet = "\nfrom pathlib import Path\nPath('/etc/x').read_bytes()\n"
    assert _machine_way_out(_source("machine") + snippet) == {"pathlib"}


def test_a_builtin_open_in_machine_is_caught_by_the_name_ban() -> None:
    tree = ast.parse(_source("machine") + "\nopen('/etc/x').read()\n")
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert names & {"__import__", "eval", "exec", "compile", "open"} == {"open"}


@pytest.mark.parametrize("snippet", ["deps.launch.read_bytes(path)\n", "tools.read_bytes(p)\n"])
def test_the_machine_file_check_passes_the_tools(snippet: str) -> None:
    assert _machine_file_problems(snippet) == []
    assert _machine_file_problems(_source("machine") + "\n" + snippet) == []


# `popen` is refused on any receiver: machine starts its processes only through `launch.ServerProcess`, so even the
# injected `tools.launch.popen` is out of bounds for it.
_MACHINE_PROCESS_NAMES = frozenset(
    {"Popen", "check_output", "check_call", "getoutput", "getstatusoutput", "startfile", "popen"}
)
_MACHINE_ARGVS = frozenset({"gpu_argv", "apps_argv"})


def _is_server_process(func: ast.expr) -> bool:
    return (isinstance(func, ast.Name) and func.id == "ServerProcess") or (
        isinstance(func, ast.Attribute) and func.attr == "ServerProcess"
    )


def _machine_process_problems(source: str) -> list[str]:
    """machine starts processes only as a `ServerProcess` of the sampler, the compute-apps query or the contention
    command."""
    tree = ast.parse(source)
    problems = []
    if _names(tree) & _MACHINE_PROCESS_NAMES:
        problems.append("a process call by name")
    if any(
        isinstance(node, ast.Attribute)
        and node.attr in {"run", "call", "system", "popen"}
        and isinstance(node.value, ast.Name)
        and node.value.id in {"subprocess", "os", "shutil"}
        for node in ast.walk(tree)
    ):
        problems.append("a process call on subprocess, os or shutil")
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_server_process(node.func)):
            continue
        argv = node.args[0] if node.args else None
        named = isinstance(argv, ast.Call) and isinstance(argv.func, ast.Name) and argv.func.id in _MACHINE_ARGVS
        configured = isinstance(argv, ast.Attribute) and argv.attr == "contention_argv"
        if not (named or configured):
            problems.append("a ServerProcess argv outside gpu_argv, apps_argv and contention_argv")
    return problems


def test_machine_starts_processes_only_through_server_process() -> None:
    source = _source("machine")
    assert _machine_process_problems(source) == []
    calls = [
        node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and _is_server_process(node.func)
    ]
    assert len(calls) >= 3


def test_the_machine_process_check_passes_the_allowed_forms() -> None:
    snippet = (
        "key.run\nstored.call\nplatform.system()\nServerProcess(gpu_argv(c), e, p, t)\n"
        "ServerProcess(self._config.contention_argv, e, None, t)\n"
    )
    assert _machine_process_problems(snippet) == []


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        ("subprocess.run(['x'])\n", "a process call on subprocess, os or shutil"),
        ("os.system('x')\n", "a process call on subprocess, os or shutil"),
        ("tools.launch.popen(['x'])\n", "a process call by name"),
        ("x.Popen(a)\n", "a process call by name"),
        (
            "ServerProcess(argv, env, None, tools)\n",
            "a ServerProcess argv outside gpu_argv, apps_argv and contention_argv",
        ),
        ("launch.ServerProcess()\n", "a ServerProcess argv outside gpu_argv, apps_argv and contention_argv"),
    ],
)
def test_the_machine_process_check_catches_each_form(snippet: str, problem: str) -> None:
    assert problem in _machine_process_problems(_source("machine") + "\n" + snippet)


def _shell_problems(source: str) -> list[int]:
    """The line of every `shell=` keyword whose value is not the constant False."""
    return [
        node.value.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.keyword)
        and node.arg == "shell"
        and not (isinstance(node.value, ast.Constant) and node.value.value is False)
    ]


@pytest.mark.parametrize("module", MODULES)
def test_no_module_passes_a_shell(module: str) -> None:
    assert _shell_problems(_source(module)) == []


@pytest.mark.parametrize("snippet", ["f(shell=True)\n", "f(shell=flag)\n", "f(shell=0)\n"])
def test_the_shell_check_catches_each_form(snippet: str) -> None:
    assert _shell_problems(snippet) == [1]
    assert _shell_problems("f(shell=False)\n") == []


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


@pytest.mark.parametrize("module", (*H3_MODULES, *H4_MODULES, *H5_MODULES))
def test_h3_modules_read_no_environment_and_print_nothing(module: str) -> None:
    """H4 spec section 2, hygiene item 10: H4 and H5 modules too, but the scoring and stats ban is for H3 modules
    only."""
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
@pytest.mark.parametrize("module", (*H3_MODULES, *H4_MODULES, *H5_MODULES))
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
        {
            "client",
            "decision",
            "evidence",
            "gates",
            "gold",
            "inputs",
            "launch",
            "machine",
            "records",
            "report",
            "runner",
            "stats",
        }
    ),
    "machine": frozenset({"evidence", "launch", "records", "schemas"}),
}
# H4a brief section 5: these rows are exact, the others are subsets.
H4_EXACT_IMPORTS = frozenset({"evidence", "machine"})
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
        # H5a brief section 4.7, item 3: "nothing imports entry" became "only cli imports entry".
        assert ("entry" in _own_modules(_source(module))) is (module == "cli"), module
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
        "from .scoring import score_run",
        "from .extraction import parse_extraction",
        "from .schemas import canonical_sha256",
        "from .probe import ProbeOutput",
    ],
)
def test_the_entry_row_catches_an_import_outside_its_set(line: str) -> None:
    """entry imports from client, decision, evidence, gates, gold, inputs, launch, machine, records, report, runner and
    stats only."""
    assert not _own_modules(_source("entry") + f"\n{line}\n") <= H4_ALLOWED_IMPORTS["entry"]
    assert _own_modules(_source("entry")) <= H4_ALLOWED_IMPORTS["entry"]


@pytest.mark.parametrize(
    "line",
    [
        "from . import gates",
        "from .client import ModelServerClient",
        "from .runner import RunHooks",
        "from .inputs import FrozenInputs",
        "from .gold import load_gold_set",
        "from . import entry",
    ],
)
def test_the_machine_row_catches_an_import_outside_its_set(line: str) -> None:
    """machine imports only from evidence, launch, records and schemas."""
    assert not _own_modules(_source("machine") + f"\n{line}\n") <= H4_ALLOWED_IMPORTS["machine"]
    assert _own_modules(_source("machine")) <= H4_ALLOWED_IMPORTS["machine"]


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


_ENTRY_IMPORT_FORMS = (
    "from . import entry",
    "from . import gates, entry as e",
    "from .entry import main",
    "from .entry import load_entry_config as load",
    f"from {PACKAGE}.entry import run_bakeoff",
    f"from {PACKAGE} import entry",
    f"import {PACKAGE}.entry",
    f"import {PACKAGE}.entry as entry_module",
    "def f():\n    from . import entry\n",
)


@pytest.mark.parametrize("line", _ENTRY_IMPORT_FORMS)
@pytest.mark.parametrize("module", [module for module in MODULES if module != "cli"])
def test_only_the_cli_imports_the_entry_in_any_form(module: str, line: str) -> None:
    """H5a brief section 4.7, item 3: each import form `_own_modules` reads is caught outside `cli`."""
    assert "entry" not in _own_modules(_source(module))
    assert "entry" in _own_modules(_source(module) + f"\n{line}\n")


def test_the_cli_imports_the_entry() -> None:
    assert "entry" in _own_modules(_source("cli"))


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
    # H5a brief section 4.7, item 3: "imported by" is not read as an import statement, as "imports" would be.
    assert "No H1 to H3 module imports an H4 module, and `entry` is imported by `cli`\nalone." in docstring


def test_the_package_docstring_check_catches_a_sentence_saying_only_cli_imports_entry() -> None:
    """H5a brief section 4.7, item 3: "`cli` imports `entry`." alone would state cli's imports as {entry}."""
    docstring = ast.get_docstring(ast.parse(_source("__init__"))) or ""
    actual = {module: _own_modules(_source(module)) for module in MODULES[1:]}
    assert _stated_imports(docstring) == actual
    assert _stated_imports(docstring + "\n`cli` imports `entry`.\n") != actual
    assert _stated_imports(docstring + "\n`entry` is imported by `cli` alone.\n") == actual


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            "`decision` imports `gates`, `launch`, `runner` and `stats`",
            "`decision` imports `gates`, `runner` and `stats`",
        ),
        ("`evidence` imports\n`records`", "`evidence` imports\n`records` and `launch`"),
        ("`gold`, `extraction`, `schemas` and `stats` import", "`gold`, `extraction` and `stats` import"),
        (
            "`machine` imports `evidence`, `launch`, `records` and `schemas`",
            "`machine` imports `launch`, `records` and `schemas`",
        ),
        (
            "`frozen` imports `client`, `gates`, `gold`, `inputs`, `records` and `schemas`",
            "`frozen` imports `client`, `gold`, `inputs`, `records` and `schemas`",
        ),
        ("`entry`, `frozen`, `gates`, `gold`", "`frozen`, `gates`, `gold`"),
        (
            "`wrapper` imports `cli`,\n`launch` and `records`",
            "`wrapper` imports `cli`,\n`launch`, `records` and `entry`",
        ),
    ],
)
def test_the_package_docstring_check_catches_a_drifted_sentence(old: str, new: str) -> None:
    docstring = ast.get_docstring(ast.parse(_source("__init__"))) or ""
    assert old in docstring
    actual = {module: _own_modules(_source(module)) for module in MODULES[1:]}
    assert _stated_imports(docstring.replace(old, new)) != actual


def test_the_package_docstring_says_where_the_prompt_text_lives() -> None:
    """H5a brief section 4.7, item 9: the prompt text is in the frozen folder and read by `frozen`, the wrapper writes
    only its log, and the milestone list names H5a."""
    docstring = ast.get_docstring(ast.parse(_source("__init__"))) or ""
    flat = " ".join(docstring.split())
    assert "Prompt text lives in app/evaluation/frozen/ and is read by `frozen`." in flat
    assert "H5 supplies it" not in flat
    assert "`wrapper` writes only its host-only log, through the launch tools." in flat
    assert "(milestone H5a)" in " ".join(docstring.split("\n\n")[0].split())


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
        *(BAKEOFF / f"{module}.py" for module in (*H3_MODULES, *H4_MODULES, *H5_MODULES)),
        *(Path(__file__).parent / name for name in (*H4_TEST_FILES, "_h4_fixtures.py")),
        *(Path(__file__).parent / name for name in (*H5_TEST_FILES, "_h5_fixtures.py")),
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


@pytest.mark.parametrize("name", H5_TEST_FILES)
def test_every_h5_test_module_refuses_sockets_and_processes(name: str) -> None:
    assert _NO_SOCKETS_OR_PROCESSES in (Path(__file__).parent / name).read_text(encoding="utf-8")


# H5a brief section 4.7, item 12: no test names a gold version path or the gold drafts folder; guard tests build
# fake paths instead. The needles are built so that this file never matches itself.
_GOLD_VERSION_PATH = re.compile("go" + "ld" + r"[/\\]+v(?:0\.4|1\.0)", flags=re.IGNORECASE)
_GOLD_DRAFTS = "gold-set" + "-drafts"


def _gold_path_names(text: str) -> list[str]:
    found = _GOLD_VERSION_PATH.findall(text)
    return found + ([_GOLD_DRAFTS] if _GOLD_DRAFTS in text.lower() else [])


@pytest.mark.parametrize("path", sorted(Path(__file__).parent.glob("*.py")), ids=lambda path: path.name)
def test_no_test_file_names_a_gold_path(path: Path) -> None:
    assert _gold_path_names(path.read_text(encoding="utf-8")) == []


def test_the_gold_path_check_does_not_match_itself() -> None:
    assert Path(__file__) in set(Path(__file__).parent.glob("*.py"))
    assert _gold_path_names(Path(__file__).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "text",
    [
        "go" + "ld/v0.4",
        "go" + "ld/v1.0/items.csv",
        "app/evaluation/go" + "ld//v1.0",
        "go" + "ld" + chr(92) + "v0.4",
        "GO" + "LD" + chr(92) * 2 + "V1.0",
        "GOLD-SET" + "-DRAFTS",
        "../gold-set" + "-drafts/x",
    ],
)
def test_the_gold_path_check_catches_each_form(text: str) -> None:
    assert _gold_path_names(text)


@pytest.mark.parametrize("text", ["gold v1.0", "gold_v1.0", "golden/v1.0", "go" + "ld/v0.5", "synthetic_gold(root)"])
def test_the_gold_path_check_passes_other_text(text: str) -> None:
    assert _gold_path_names(text) == []


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


# The final H4 tuple of NEEDS-OWNER numbers, each with a marked site; H5a adds none, and the AMB-133 marker comes after
# 2026-11-30 (AMB-153).
OWNER_MARKERS = (*range(1, 5), *range(26, 31), 66, 69, 106, 108, 116, 129, 130, 134, 135, 136, 137, 139, 140)


def test_owner_markers_are_in_the_sources() -> None:
    sources = "\n".join(_source(module) for module in MODULES)
    for number in OWNER_MARKERS:
        assert f"NEEDS-OWNER(AMB-{number})" in sources
    assert "NEEDS-OWNER(AMB-52)" not in sources


# The module that holds each H4 marker, with its count there. AMB-66 keeps its H3 site in records beside its gates
# site, AMB-69 keeps its H3 site in runner beside its entry site, and AMB-106 has two gates sites.
OWNER_MARKER_SITES = MappingProxyType(
    {
        66: (("records", 1), ("gates", 1)),
        69: (("runner", 1), ("entry", 1)),
        106: (("gates", 2),),
        116: (("gates", 1),),
        135: (("gates", 1),),
        139: (("gates", 1),),
        108: (("machine", 1),),
        140: (("entry", 1),),
        129: (("decision", 1),),
        130: (("decision", 1),),
        134: (("decision", 1),),
        136: (("report", 1),),
        137: (("report", 1),),
    }
)


@pytest.mark.parametrize("number", sorted(OWNER_MARKER_SITES))
def test_each_h4_owner_marker_sits_in_its_listed_module(number: int) -> None:
    for module, count in OWNER_MARKER_SITES[number]:
        assert _source(module).count(f"NEEDS-OWNER(AMB-{number})") == count, (number, module)
    assert number in OWNER_MARKERS


def test_a_moved_owner_marker_is_caught() -> None:
    moved = _source("report").replace("NEEDS-OWNER(AMB-136)", "NEEDS-OWNER(AMB-999)")
    assert moved.count("NEEDS-OWNER(AMB-136)") != dict(OWNER_MARKER_SITES[136])["report"]


# H5a brief section 4.7, item 14 (AMB-155): the AMB-140 marker keeps its form and its site, the first line of
# `entry.run_bakeoff`'s body after the docstring, and only its text changes.
_AMB_140_MARKER = (
    "# NEEDS-OWNER(AMB-140): the entry point is this function, called by `cli run` with inputs from "
    "`frozen.load_frozen`"
)


def test_the_amb_140_marker_names_the_cli_and_the_loader() -> None:
    source = _source("entry")
    lines = [line.strip() for line in source.splitlines()]
    assert lines.count(_AMB_140_MARKER) == 1
    function = next(
        node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "run_bakeoff"
    )
    docstring, first = function.body[0], function.body[1]
    assert isinstance(docstring, ast.Expr) and isinstance(docstring.value, ast.Constant)
    assert (docstring.end_lineno or 0) < lines.index(_AMB_140_MARKER) + 1 < first.lineno


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
        ("machine", ("§3.1", "§3.13", "§6", "§9")),
        ("frozen", ("§3.10", "§3.11", "§3.12", "§3.13", "§4", "§11")),
        ("cli", ("§3.2", "§3.12", "§3.13", "§11")),
        ("wrapper", ("§3.12",)),
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


@pytest.mark.parametrize("module", ["gates", "decision", "report", "evidence", "machine"])
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


# Operator messages and the report body cite the signed note (S2) or an open point (AMB-n), never an H4 spec rule id
# (Gn-m); `3059a59` made the same kind of change, to AMB-4. Comments and docstrings are outside this check.
_SPEC_RULE_ID = re.compile(r"\bG[0-9]-[0-9]+\b")


def _runtime_strings(source: str) -> list[tuple[int, str]]:
    """Every string constant that is not a docstring (module, class, function or attribute), with its line."""
    tree = ast.parse(source)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        first = body[0] if body else None
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
        ):
            docstrings.add(id(first.value))
        for previous, current in zip(body, body[1:], strict=False):
            if (
                isinstance(previous, ast.Assign | ast.AnnAssign)
                and isinstance(current, ast.Expr)
                and isinstance(current.value, ast.Constant)
            ):
                docstrings.add(id(current.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
    ]


@pytest.mark.parametrize("module", MODULES)
def test_runtime_strings_cite_no_private_spec_rule_id(module: str) -> None:
    assert [(line, text) for line, text in _runtime_strings(_source(module)) if _SPEC_RULE_ID.search(text)] == []


@pytest.mark.parametrize(
    ("snippet", "found"),
    [
        ('raise ValueError("bad value (G4-6)")\n', True),
        ('x = f"{y} needs a reason (G4-14)"\n', True),
        ('X = "a note (G4-14)"\n', True),
        ('def f():\n    """A docstring may cite G4-6."""\n', False),
        ('X = 1\n"""An attribute docstring may cite G4-6."""\n', False),
        ("# a comment may cite G4-6\nx = 1\n", False),
        ('raise ValueError("bad value (S2 §10)")\n', False),
    ],
)
def test_the_runtime_string_check_catches_a_spec_rule_id(snippet: str, found: bool) -> None:
    assert any(_SPEC_RULE_ID.search(text) for _line, text in _runtime_strings(snippet)) is found


# H5 hygiene. The import rows: no earlier or H4 module imports an H5 module, and each H5 module imports exactly its row.
H5_ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "frozen": frozenset({"client", "gates", "gold", "inputs", "records", "schemas"}),
    "cli": frozenset(
        {"client", "decision", "entry", "frozen", "gates", "gold", "inputs", "launch", "machine", "records", "schemas"}
    ),
    "wrapper": frozenset({"cli", "launch", "records"}),
}


def test_h5_import_direction() -> None:
    assert set(H5_MODULES) <= H5_PLANNED
    assert set(H5_ALLOWED_IMPORTS) == set(H5_MODULES)
    for module in (*EARLIER_MODULES, *H4_MODULES):
        assert not _own_modules(_source(module)) & H5_PLANNED, module
    for module, allowed in H5_ALLOWED_IMPORTS.items():
        assert _own_modules(_source(module)) == allowed, module


@pytest.mark.parametrize(
    ("module", "line"),
    [
        ("runner", "from . import frozen"),
        ("inputs", "from .frozen import load_draft_inputs"),
        ("entry", f"from {PACKAGE}.frozen import load_frozen"),
        ("gates", "from . import cli"),
        ("machine", "from .wrapper import run_wrapped"),
    ],
)
def test_the_h5_direction_check_catches_an_earlier_module_importing_h5(module: str, line: str) -> None:
    assert _own_modules(_source(module) + f"\n{line}\n") & H5_PLANNED
    assert not _own_modules(_source(module)) & H5_PLANNED


@pytest.mark.parametrize(
    "line",
    [
        "from . import runner",
        "from .launch import base_url",
        "from .probe import MAX_SOURCES_PER_CLAIM",
        "from .extraction import parse_extraction",
        "from . import entry",
        "from . import cli",
    ],
)
def test_the_frozen_row_catches_an_import_outside_its_set(line: str) -> None:
    """frozen imports only from client, gates, gold, inputs, records and schemas."""
    assert not _own_modules(_source("frozen") + f"\n{line}\n") <= H5_ALLOWED_IMPORTS["frozen"]
    assert _own_modules(_source("frozen")) <= H5_ALLOWED_IMPORTS["frozen"]


@pytest.mark.parametrize(
    "line",
    [
        "from . import runner",
        "from .report import build_report",
        "from . import wrapper",
        "from .extraction import parse_extraction",
        "from .probe import MAX_SOURCES_PER_CLAIM",
        "from . import scoring",
    ],
)
def test_the_cli_row_catches_an_import_outside_its_set(line: str) -> None:
    assert not _own_modules(_source("cli") + f"\n{line}\n") <= H5_ALLOWED_IMPORTS["cli"]
    assert _own_modules(_source("cli")) == H5_ALLOWED_IMPORTS["cli"]


@pytest.mark.parametrize(
    "line",
    [
        "from . import entry",
        "from .frozen import load_frozen",
        f"from {PACKAGE}.gold import load_gold_set",
        "from . import inputs",
        "from .client import ModelServerClient",
        "from . import runner",
        f"from {PACKAGE} import machine",
    ],
)
def test_the_wrapper_row_catches_an_import_outside_its_set(line: str) -> None:
    """wrapper imports only cli, launch and records, so it cannot read gold or a frozen input itself."""
    assert not _own_modules(_source("wrapper") + f"\n{line}\n") <= H5_ALLOWED_IMPORTS["wrapper"]
    assert _own_modules(_source("wrapper")) == H5_ALLOWED_IMPORTS["wrapper"]
    assert not _own_modules(_source("wrapper")) & {"entry", "frozen", "gold", "inputs", "client"}


# H5a brief section 4.7, item 5: an `if __name__ == "__main__":` block appears only in `cli` and `wrapper`, and each
# ends with `raise SystemExit(main())`.
_MAIN_BLOCK_MODULES = frozenset({"cli", "wrapper"})


def _main_blocks(source: str) -> list[ast.If]:
    """Every `if __name__ == "__main__":` block, at any depth."""
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
        and any(isinstance(item, ast.Constant) and item.value == "__main__" for item in node.test.comparators)
    ]


def _ends_with_system_exit_of_main(block: ast.If) -> bool:
    last = block.body[-1]
    return (
        isinstance(last, ast.Raise)
        and isinstance(last.exc, ast.Call)
        and isinstance(last.exc.func, ast.Name)
        and last.exc.func.id == "SystemExit"
        and len(last.exc.args) == 1
        and isinstance(last.exc.args[0], ast.Call)
        and isinstance(last.exc.args[0].func, ast.Name)
        and last.exc.args[0].func.id == "main"
        and not last.exc.args[0].args
    )


@pytest.mark.parametrize("module", MODULES)
def test_only_the_cli_and_the_wrapper_have_a_main_block(module: str) -> None:
    blocks = _main_blocks(_source(module))
    if module in _MAIN_BLOCK_MODULES:
        assert len(blocks) == 1, module
        assert _ends_with_system_exit_of_main(blocks[0]), module
    else:
        assert blocks == [], module


def test_the_cli_main_block_is_at_module_level() -> None:
    tree = ast.parse(_source("cli"))
    top = [node for node in tree.body if isinstance(node, ast.If)]
    assert len(top) == 1 and _main_blocks(ast.unparse(top[0])) and tree.body[-1] is top[0]


def test_the_wrapper_main_block_is_at_module_level() -> None:
    tree = ast.parse(_source("wrapper"))
    top = [node for node in tree.body if isinstance(node, ast.If)]
    assert len(top) == 1 and _main_blocks(ast.unparse(top[0])) and tree.body[-1] is top[0]


@pytest.mark.parametrize(
    ("snippet", "blocks", "ends"),
    [
        ('if __name__ == "__main__":\n    raise SystemExit(main())\n', 1, True),
        ('if __name__ == "__main__":\n    main()\n', 1, False),
        ('if __name__ == "__main__":\n    raise SystemExit(main(sys.argv))\n', 1, False),
        ('def f():\n    if __name__ == "__main__":\n        raise SystemExit(main())\n', 1, True),
        ('if __name__ == "x":\n    raise SystemExit(main())\n', 0, False),
    ],
)
def test_the_main_block_check_reads_each_form(snippet: str, blocks: int, ends: bool) -> None:
    found = _main_blocks(snippet)
    assert len(found) == blocks
    if found:
        assert _ends_with_system_exit_of_main(found[0]) is ends


# H5a brief section 4.7, item 6: `cli` names no file attribute but `read_bytes` and the injected `fsync`, never calls
# `fsync`, and opens nothing; every write goes through `records`.
def _cli_file_problems(source: str) -> list[str]:
    tree = ast.parse(source)
    problems = []
    if _names(tree) & (_FILE_ATTRIBUTES - {"read_bytes", "fsync"}):
        problems.append("a file name beyond read_bytes and fsync")
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    if "fsync" in _called_attributes(source) or "fsync" in called:
        problems.append("fsync called")
    if _open_modes(source) != set():
        problems.append("an open call")
    if "open" in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}:
        problems.append("a builtin open")
    return problems


def test_the_cli_reaches_files_only_through_the_tools_and_records() -> None:
    source = _source("cli")
    assert _cli_file_problems(source) == []
    assert {"read_bytes", "fsync"} <= _names(ast.parse(source))


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        ("Path(x).write_bytes(b'x')\n", "a file name beyond read_bytes and fsync"),
        ("os.mkdir(d)\n", "a file name beyond read_bytes and fsync"),
        ("Path(d).glob('*')\n", "a file name beyond read_bytes and fsync"),
        ("env.fsync(1)\n", "fsync called"),
        ("os.fsync(handle)\n", "fsync called"),
        ("fsync(handle)\n", "fsync called"),
        ("Path(x).open('xb')\n", "an open call"),
        ("open(x, 'wb')\n", "a builtin open"),
    ],
)
def test_the_cli_file_check_catches_each_reach(snippet: str, problem: str) -> None:
    assert problem in _cli_file_problems(_source("cli") + "\n" + snippet)
    assert _cli_file_problems("data = env.tools.read_bytes(p)\nwrite_new_file(p, d, fsync=env.fsync)\n") == []


# H5a brief section 4.7, item 8: the CLI reads no text attribute; the per-source requests are built in `frozen`.
_CLI_TEXT_NAMES = frozenset({"text", "claim", "claim_text", "utterance", "reasoning_content"})


def _cli_text_reaches(source: str) -> set[str]:
    return {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)} & _CLI_TEXT_NAMES


def test_the_cli_reads_no_text_attribute() -> None:
    assert _cli_text_reaches(_source("cli")) == set()


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("x = item.claim\n", "claim"),
        ("x = source.text\n", "text"),
        ("x = row.claim_text\n", "claim_text"),
        ("x = row.utterance\n", "utterance"),
        ("x = attempt.reasoning_content\n", "reasoning_content"),
    ],
)
def test_the_cli_text_check_catches_each_read(snippet: str, name: str) -> None:
    assert _cli_text_reaches(_source("cli") + "\n" + snippet) == {name}


def test_the_wrapper_reads_no_text_attribute() -> None:
    """H5a brief section 4.7, item 8: the wrapper sees typed fields and captured lines, never a text attribute."""
    assert _cli_text_reaches(_source("wrapper")) == set()


@pytest.mark.parametrize(
    ("snippet", "name"),
    [("x = item.claim\n", "claim"), ("x = segment.text\n", "text"), ("x = row.utterance\n", "utterance")],
)
def test_the_wrapper_text_check_catches_each_read(snippet: str, name: str) -> None:
    assert _cli_text_reaches(_source("wrapper") + "\n" + snippet) == {name}


# H5a brief section 4.7, item 6: `wrapper` names no file attribute and no tool but `open_log`, and opens nothing
# itself; its log is created through the launch tools, and its console is a descriptor it already holds.
_TOOL_NAMES = frozenset({"open_log", "read_chunks", "popen"})


def _wrapper_file_problems(source: str) -> list[str]:
    tree = ast.parse(source)
    problems = []
    if _names(tree) & ((_FILE_ATTRIBUTES | _TOOL_NAMES) - {"open_log"}):
        problems.append("a file or tool name beyond open_log")
    if _open_modes(source) != set():
        problems.append("an open call")
    if "open" in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}:
        problems.append("a builtin open")
    return problems


def test_the_wrapper_reaches_files_only_through_open_log() -> None:
    source = _source("wrapper")
    assert _wrapper_file_problems(source) == []
    assert "open_log" in _names(ast.parse(source))


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        ("data = env.tools.read_bytes(p)\n", "a file or tool name beyond open_log"),
        ("Path(x).write_text('x')\n", "a file or tool name beyond open_log"),
        ("os.mkdir(d)\n", "a file or tool name beyond open_log"),
        ("os.fsync(fd)\n", "a file or tool name beyond open_log"),
        ("tools.popen(argv)\n", "a file or tool name beyond open_log"),
        ("Path(x).open('xb')\n", "an open call"),
        ("io.open(x, 'wb')\n", "an open call"),
        ("handle = open(x, 'ab')\n", "a builtin open"),
    ],
)
def test_the_wrapper_file_check_catches_each_reach(snippet: str, problem: str) -> None:
    assert problem in _wrapper_file_problems(_source("wrapper") + "\n" + snippet)
    assert _wrapper_file_problems("log = env.tools.open_log(path)\nos.write(fd, b'x')\n") == []


# frozen reads files only through the injected `read_bytes`, so it imports none of the ways out.
_FROZEN_NEVER_IMPORTS = frozenset({"os", "io", "pathlib", "subprocess", "time", "logging", "sys", "httpx"})


def _frozen_way_out(source: str) -> set[str]:
    return {name.split(".")[0] for name in _imports(source)} & _FROZEN_NEVER_IMPORTS


def test_frozen_imports_no_way_out() -> None:
    assert _frozen_way_out(_source("frozen")) == set()


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("import os", "os"),
        ("import os.path", "os"),
        ("from io import BytesIO", "io"),
        ("from pathlib import Path", "pathlib"),
        ("import subprocess", "subprocess"),
        ("from time import monotonic", "time"),
        ("import logging", "logging"),
        ("import sys", "sys"),
        ("import httpx", "httpx"),
    ],
)
def test_the_frozen_import_ban_catches_each_name(line: str, name: str) -> None:
    assert _frozen_way_out(_source("frozen") + f"\n{line}\n") == {name}


def _frozen_file_problems(source: str) -> list[str]:
    """frozen names no file attribute but the injected `read_bytes`, and opens nothing."""
    tree = ast.parse(source)
    problems = []
    if _names(tree) & (_FILE_ATTRIBUTES - {"read_bytes"}):
        problems.append("a file name beyond read_bytes")
    if _open_modes(source) != set():
        problems.append("an open call")
    if "open" in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}:
        problems.append("a builtin open")
    return problems


def test_frozen_reaches_files_only_through_the_injected_reader() -> None:
    source = _source("frozen")
    assert _frozen_file_problems(source) == []
    assert "read_bytes" in _names(ast.parse(source))


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        ("Path(x).read_text()\n", "a file name beyond read_bytes"),
        ("os.listdir(d)\n", "a file name beyond read_bytes"),
        ("tools.fsync(1)\n", "a file name beyond read_bytes"),
        ("Path(x).write_bytes(b'x')\n", "a file name beyond read_bytes"),
        ("Path(x).open('rb')\n", "an open call"),
        ("open(x)\n", "a builtin open"),
    ],
)
def test_the_frozen_file_check_catches_each_reach(snippet: str, problem: str) -> None:
    assert problem in _frozen_file_problems(_source("frozen") + "\n" + snippet)
    assert _frozen_file_problems("data = read_bytes(path)\n") == []


# In frozen only these functions read an attribute named `text` or `claim`, besides the private probe and warm-up
# parse and check helpers; `cap_session_rows` reads `is_in_scope_checkable` and no text.
_FROZEN_TEXT_NAMES = frozenset({"text", "claim"})
_FROZEN_TEXT_READERS = frozenset({"gold_free_hashes", "overlap_counts", "source_delta_requests", "delimiters"})
_FROZEN_TEXT_HELPER = re.compile(r"_(?:parse|check)_(?:probe|warmup)\w*")
_CAP_ROWS_NEVER = frozenset({"text", "claim", "claim_text", "utterance", "segments", "speaker"})


def _top_level_scopes(tree: ast.Module) -> list[tuple[str, ast.AST]]:
    """Each top-level function and method, by name, and every other top-level statement as `<module>`."""
    scopes: list[tuple[str, ast.AST]] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            scopes.append((node.name, node))
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                name = member.name if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef) else "<module>"
                scopes.append((name, member))
        else:
            scopes.append(("<module>", node))
    return scopes


def _frozen_text_problems(source: str) -> list[str]:
    problems = []
    for name, scope in _top_level_scopes(ast.parse(source)):
        reads = {node.attr for node in ast.walk(scope) if isinstance(node, ast.Attribute)}
        allowed = name in _FROZEN_TEXT_READERS or _FROZEN_TEXT_HELPER.fullmatch(name)
        if reads & _FROZEN_TEXT_NAMES and not allowed:
            problems.append(f"{name} reads text")
        if name == "cap_session_rows":
            if reads & _CAP_ROWS_NEVER:
                problems.append("cap_session_rows reads text")
            if "is_in_scope_checkable" not in reads:
                problems.append("cap_session_rows does not read is_in_scope_checkable")
    return problems


def test_frozen_reads_text_only_where_it_must() -> None:
    assert _frozen_text_problems(_source("frozen")) == []


def test_every_frozen_text_rule_names_a_defined_function() -> None:
    """The allowlist and the `cap_session_rows` rule are in force: each function they name exists in frozen."""
    defined = {name for name, _ in _top_level_scopes(ast.parse(_source("frozen"))) if name != "<module>"}
    assert _FROZEN_TEXT_READERS | {"cap_session_rows"} <= defined
    assert {"build_cap_sessions", "parse_cap_sessions", "load_frozen", "check_expected"} <= defined


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        (
            "def build_cap_sessions(g):\n    return [s.text for c in g.conversations for s in c.segments]\n",
            "build_cap_sessions reads text",
        ),
        ("def load_frozen(d):\n    return d.probe_items[0].claim\n", "load_frozen reads text"),
        ("def check_expected(i, e):\n    return i.warmup.text\n", "check_expected reads text"),
        (
            "def cap_session_rows(g, ids):\n    return sum(len(g.conversation(s).segments) for s in ids if g.x)\n",
            "cap_session_rows reads text",
        ),
    ],
)
def test_the_frozen_text_check_catches_a_read_in_the_a4_functions(snippet: str, problem: str) -> None:
    assert problem in _frozen_text_problems(snippet)


@pytest.mark.parametrize(
    ("snippet", "problem"),
    [
        ("def load_draft_inputs(d):\n    return d.prompts.repair.text\n", "load_draft_inputs reads text"),
        ("def build_cap_sessions(g):\n    return [i.claim for i in g.items]\n", "build_cap_sessions reads text"),
        ("X = SOMETHING.text\n", "<module> reads text"),
        (
            "def cap_session_rows(g, ids):\n    return sum(len(s.text) for s in g.rows if s.is_in_scope_checkable)\n",
            "cap_session_rows reads text",
        ),
        (
            "def cap_session_rows(g, ids):\n    return sum(1 for i in g.items_for(ids[0]))\n",
            "cap_session_rows does not read is_in_scope_checkable",
        ),
    ],
)
def test_the_frozen_text_check_catches_each_read(snippet: str, problem: str) -> None:
    assert problem in _frozen_text_problems(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        "def gold_free_hashes(d):\n    return [i.claim for i in d.probe_items]\n",
        "def _parse_probe(v):\n    return v.text\n",
        "def _check_warmup_segment(s):\n    return s.text\n",
        "def cap_session_rows(g, ids):\n    return sum(i.is_in_scope_checkable for s in ids for i in g.items_for(s))\n",
    ],
)
def test_the_frozen_text_check_passes_the_allowed_reads(snippet: str) -> None:
    assert _frozen_text_problems(snippet) == []


# The committed gold-free inputs verify against their hash file once the freeze has written it; until then the test is
# skipped. It reads no gold.
FROZEN_DIR = BAKEOFF.parent / "frozen"


@pytest.mark.skipif(not (FROZEN_DIR / "INPUTS.sha256").is_file(), reason="not frozen yet")
def test_the_committed_frozen_inputs_verify() -> None:
    # Imported here: the other hygiene checks read sources and must not depend on a module loading.
    from app.evaluation.bakeoff import frozen

    files = {name: (FROZEN_DIR / name).read_bytes() for name in frozen.INPUT_FILES}
    checked = frozen.check_inputs_manifest((FROZEN_DIR / frozen.INPUTS_MANIFEST).read_bytes(), files)
    draft = frozen.parse_draft_inputs(files)
    assert dict(draft.file_sha256) == dict(checked)


# The H5 modules cite no plan decision id (D-n) or standing-go id (SUn) in a runtime string either, beside the spec
# rule ids: the plan's citation allowance covers comments and docstrings only.
_PLAN_ID = re.compile(r"\bD-[0-9]+\b|\bSU[0-9]+\b")


def _plan_id_strings(source: str) -> list[tuple[int, str]]:
    return [
        (line, text) for line, text in _runtime_strings(source) if _PLAN_ID.search(text) or _SPEC_RULE_ID.search(text)
    ]


@pytest.mark.parametrize("module", H5_MODULES)
def test_h5_runtime_strings_cite_no_plan_id(module: str) -> None:
    assert _plan_id_strings(_source(module)) == []


@pytest.mark.parametrize(
    ("snippet", "found"),
    [
        ('raise ValueError("refused under D-5")\n', True),
        ('x = f"{y} needs the SU1 go"\n', True),
        ('X = "a standing go (SU12)"\n', True),
        ('raise ValueError("bad value (G4-6)")\n', True),
        ('def f():\n    """A docstring may cite D-5 and SU1."""\n', False),
        ("# a comment may cite D-5\nx = 1\n", False),
        ('raise ValueError("synthetic inputs equal gold text (AMB-157)")\n', False),
        ('X = "ID-5 SUM1 D-x"\n', False),
    ],
)
def test_the_plan_id_check_catches_each_form(snippet: str, found: bool) -> None:
    assert bool(_plan_id_strings(snippet)) is found


def test_the_load_evidence_docstring_keys_on_the_host_problem() -> None:
    """H5a brief section 4.6: the docstring states the branch the code takes, on host.json's `problem` field."""
    function = next(
        node
        for node in ast.parse(_source("machine")).body
        if isinstance(node, ast.FunctionDef) and node.name == "load_evidence"
    )
    docstring = " ".join((ast.get_docstring(function) or "").split())
    for sentence in (
        "When host.json records no problem, a malformed log or a gpu.csv with no sample is a `StoreError`.",
        "When host.json records a problem (AMB-123), the evidence is given when both logs parse and hold a GPU sample,"
        " and None otherwise.",
        "The branch keys on host.json's `problem` field, not on whether the attempt was later counted or voided.",
    ):
        assert sentence in docstring
    assert "(a counted one)" not in docstring
    assert "For a voided attempt" not in docstring
