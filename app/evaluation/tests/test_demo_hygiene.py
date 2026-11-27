"""Hygiene of the demo package (demo specification; S2 §3.2, §3.12; PHASE-0 §6.6).

Only the harness client talks to the network, so no demo module imports httpx or another way out. The demo reads no
gold and reuses no fact-check code, so nothing imports `app.insights`, the gold loader or the harness modules the
specification leaves out. Nothing reads this process's environment, no file names a gold or machine path, every bundle
holds at most five sources, and outputs are only ever created new: the only `os` calls are `fsync` and the two
checks of the output directory and names. No module starts a process or imports a module by name. Prompt wording
lives in the frozen inputs (S2 §3.10), so no demo module builds a chat message or holds a placeholder. The needles
below are built so that this file never matches itself.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._runner_fixtures import block_processes

DEMO = Path(__file__).resolve().parents[1] / "demo"
TESTS = Path(__file__).resolve().parent
PACKAGE = "app.evaluation.demo"
BAKEOFF = "app.evaluation.bakeoff"
MODULES = ("__init__", "pipeline", "render", "__main__")
DEMO_TEST_FILES = ("test_demo_pipeline.py", "test_demo_render.py", "test_demo_main.py", "test_demo_hygiene.py")
LF = chr(10)
WAYS_OUT = frozenset(
    {"httpx", "socket", "ssl", "urllib", "http", "requests", "aiohttp", "subprocess", "ftplib", "multiprocessing"}
    | {"pty", "asyncio", "importlib", "ctypes", "shutil", "tempfile", "concurrent"}
)
OS_NAMES = frozenset({"os.fsync", "os.path.isdir", "os.path.lexists"})
NEVER_IMPORTED = (
    "app.insights",
    f"{BAKEOFF}.gold",
    f"{BAKEOFF}.gates",
    f"{BAKEOFF}.report",
    f"{BAKEOFF}.entry",
    f"{BAKEOFF}.cli",
    f"{BAKEOFF}.wrapper",
    f"{BAKEOFF}.runner",
    f"{BAKEOFF}.machine",
)
ENVIRONMENT_READS = frozenset({"environ", "environb", "getenv", "getenvb", "expanduser", "expandvars", "home"})
FILE_CHANGES = frozenset(
    {
        "open",
        "write_text",
        "write_bytes",
        "unlink",
        "rename",
        "rmdir",
        "mkdir",
        "makedirs",
        "touch",
        "truncate",
        "remove",
        "symlink_to",
        "hardlink_to",
        "chmod",
    }
)
PLACEHOLDERS = ("<<INPUT" + "_JSON>>", "<<VALIDATION" + "_ERRORS>>")
NO_SOCKETS_OR_PROCESSES = LF.join(
    (
        "@pytest.fixture(autouse=True)",
        "def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:",
        "    block_sockets(monkeypatch)",
        "    block_processes(monkeypatch)",
    )
)


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


def _source(module: str) -> str:
    return (DEMO / f"{module}.py").read_text(encoding="utf-8")


def _imports(source: str) -> set[str]:
    """Every module an import names, relative ones resolved from the demo package, and each `from` name as a
    submodule too, so `from app.evaluation.bakeoff import gold` shows as the gold module."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = PACKAGE.split(".")
            base = ".".join([*parts[: len(parts) - node.level + 1], node.module or ""]) if node.level else node.module
            base = (base or "").strip(".")
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def _ways_out(source: str) -> set[str]:
    """Way-out modules imported, and any import by name through `__import__`."""
    names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)} & {"__import__"}
    return ({name.split(".")[0] for name in _imports(source)} & WAYS_OUT) | names


def _os_names(source: str) -> set[str]:
    """Every dotted name under `os` that a module uses, and every name it imports from `os` or `os.path`."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "os":
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Attribute):
            parts, value = [node.attr], node.value
            while isinstance(value, ast.Attribute):
                parts, value = [value.attr, *parts], value.value
            if isinstance(value, ast.Name) and value.id == "os":
                found.add(".".join(["os", *parts]))
    return {name for name in found if not any(other.startswith(name + ".") for other in found)}


def _never_imported(source: str) -> set[str]:
    return {
        name for name in _imports(source) if any(name == bad or name.startswith(bad + ".") for bad in NEVER_IMPORTED)
    }


def _environment_reads(source: str) -> set[str]:
    tree = ast.parse(source)
    found = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    found |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
    return found & ENVIRONMENT_READS


def _file_changes(source: str) -> set[str]:
    """Calls that open or change a file other than through `records.write_new_file`: the builtin `open`, any `.open`
    and the path and `os` calls that write, move or delete."""
    tree = ast.parse(source)
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute | ast.Name)
    }
    return called & FILE_CHANGES


def _bad_paths(text: str) -> list[str]:
    """Machine paths (a user folder, a WSL share) and gold paths (a version directory, the drafts and tools folders)."""
    lowered = text.lower()
    needles = ("/ho" + "me/", "/us" + "ers/", "wsl" + ".localhost", "wsl" + "$", "gold-set" + "-drafts")
    needles += ("gold-tools" + "-private", "app/evaluation/" + "gold")
    found = [needle for needle in needles if needle in lowered]
    separators = "[/" + chr(92) * 2 + "]+"
    found += re.findall("[a-z]:" + separators + "users", lowered)
    found += re.findall("go" + "ld" + separators + "v(?:0[.]4|1[.]0)", lowered)
    return found


# ---------------------------------------------------------------- the module list


def test_every_demo_module_is_listed() -> None:
    found = sorted(path.relative_to(DEMO).as_posix() for path in DEMO.rglob("*.py"))
    assert found == sorted(f"{module}.py" for module in MODULES)


def test_every_demo_test_module_is_listed() -> None:
    assert sorted(path.name for path in TESTS.glob("test_demo_*.py")) == sorted(DEMO_TEST_FILES)


# ---------------------------------------------------------------- imports


@pytest.mark.parametrize("module", MODULES)
def test_no_demo_module_imports_a_way_out(module: str) -> None:
    assert _ways_out(_source(module)) == set()


@pytest.mark.parametrize("module", MODULES)
def test_no_demo_module_imports_insights_gold_or_an_unused_harness_module(module: str) -> None:
    assert _never_imported(_source(module)) == set()


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("import httpx", "httpx"),
        ("from urllib import request", "urllib"),
        ("import http.client", "http"),
        ("def f():" + LF + "    import socket", "socket"),
        ("import multiprocessing", "multiprocessing"),
        ("from importlib import import_module", "importlib"),
        ("import shutil", "shutil"),
        ("x = __import__('socket')", "__import__"),
    ],
)
def test_the_way_out_check_catches_each_form(line: str, name: str) -> None:
    assert _ways_out(line + LF) == {name}


@pytest.mark.parametrize(
    ("line", "name"),
    [
        ("from app.evaluation.bakeoff import gold", f"{BAKEOFF}.gold"),
        ("from app.evaluation.bakeoff.gold import load_gold_set", f"{BAKEOFF}.gold"),
        ("from ..bakeoff import gates", f"{BAKEOFF}.gates"),
        ("from ..bakeoff.entry import main", f"{BAKEOFF}.entry"),
        ("import app.insights.service", "app.insights.service"),
        ("from app.insights import factcheck", "app.insights"),
    ],
)
def test_the_import_check_catches_each_form(line: str, name: str) -> None:
    assert name in _never_imported(line + LF)
    assert _never_imported("from app.evaluation.bakeoff import client, frozen, launch" + LF) == set()


# ---------------------------------------------------------------- the environment


@pytest.mark.parametrize("module", MODULES)
def test_no_demo_module_reads_the_environment(module: str) -> None:
    assert _environment_reads(_source(module)) == set()


@pytest.mark.parametrize(
    "snippet",
    [
        "import os" + LF + "os.environ['A']",
        "import os" + LF + "os.getenv('A')",
        "from os import environ",
        "os.path.expanduser('~')",
        "Path.home()",
    ],
)
def test_the_environment_check_catches_each_read(snippet: str) -> None:
    assert _environment_reads(snippet + LF)


# ---------------------------------------------------------------- paths


@pytest.mark.parametrize(
    "path",
    [
        *(DEMO / f"{module}.py" for module in MODULES),
        *sorted((DEMO / "fixtures").glob("*")),
        *sorted((TESTS / "demo_golden").glob("*")),
        *(TESTS / name for name in (*DEMO_TEST_FILES, "_demo_fixtures.py")),
    ],
    ids=lambda path: path.name,
)
def test_no_demo_file_names_a_gold_or_machine_path(path: Path) -> None:
    assert _bad_paths(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "text",
    [
        "/ho" + "me/somebody",
        "C:" + chr(92) + "Us" + "ers" + chr(92) + "somebody",
        "c:/us" + "ers/somebody",
        "wsl" + ".localhost/Ubuntu/share",
        "wsl" + "$/Ubuntu/share",
        "go" + "ld/v1.0",
        "GO" + "LD" + chr(92) + "V0.4",
        "../GOLD-SET" + "-DRAFTS/x",
        "app/evaluation/go" + "ld",
    ],
)
def test_the_path_check_catches_each_form(text: str) -> None:
    assert _bad_paths(text)


# ---------------------------------------------------------------- bundles


def test_each_bundle_holds_at_most_five_unique_source_ids() -> None:
    value = json.loads((DEMO / "fixtures" / "bundles.json").read_text(encoding="ascii"))
    assert value["bundles"]
    for bundle in value["bundles"]:
        source_ids = [row["sourceId"] for row in bundle["sources"]]
        assert 1 <= len(set(source_ids)) == len(source_ids) <= 5


# ---------------------------------------------------------------- writes


@pytest.mark.parametrize("module", MODULES)
def test_no_demo_module_opens_or_changes_a_file_itself(module: str) -> None:
    assert _file_changes(_source(module)) == set()


def test_the_outputs_are_created_new_through_write_new_file() -> None:
    tree = ast.parse(_source("__main__"))
    called = [
        node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert called.count("write_new_file") == 2


@pytest.mark.parametrize("module", MODULES)
def test_no_demo_module_uses_another_os_call(module: str) -> None:
    assert _os_names(_source(module)) <= OS_NAMES


@pytest.mark.parametrize(
    ("snippet", "name"),
    [
        ("os.replace(a, b)", "os.replace"),
        ("os.system('x')", "os.system"),
        ("os.path.join(a, b)", "os.path.join"),
        ("from os import spawnv", "os.spawnv"),
        ("from os.path import expanduser", "os.path.expanduser"),
    ],
)
def test_the_os_check_catches_each_form(snippet: str, name: str) -> None:
    assert _os_names(snippet + LF) - OS_NAMES == {name}


@pytest.mark.parametrize(
    "snippet",
    ["open(p, 'w')", "Path(p).open('xb')", "Path(p).write_text('x')", "os.remove(p)", "Path(p).mkdir()"],
)
def test_the_write_check_catches_each_form(snippet: str) -> None:
    assert _file_changes(snippet + LF)


# ---------------------------------------------------------------- prompt wording


@pytest.mark.parametrize("module", MODULES)
def test_no_demo_module_builds_a_chat_message_or_holds_a_placeholder(module: str) -> None:
    source = _source(module)
    names = {node.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Attribute)}
    assert "ChatMessage" not in names and "ChatMessage" not in _imports(source)
    assert not any(placeholder in source for placeholder in PLACEHOLDERS)


def test_no_fixture_holds_a_placeholder() -> None:
    for path in sorted((DEMO / "fixtures").glob("*")):
        assert not any(placeholder in path.read_text(encoding="ascii") for placeholder in PLACEHOLDERS), path.name


# ---------------------------------------------------------------- test modules


def test_every_demo_test_module_refuses_sockets_and_processes() -> None:
    for name in DEMO_TEST_FILES:
        assert NO_SOCKETS_OR_PROCESSES in (TESTS / name).read_text(encoding="utf-8"), name
