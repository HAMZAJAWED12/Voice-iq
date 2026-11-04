"""Hygiene of the harness package: H1 spec T1, T2, T4 and G8, and H2 spec R48.

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
MODULES = ("__init__", "gold", "extraction", "scoring", "stats", *H2_MODULES)
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
EXTRA_ALLOWED = {"client": frozenset({"httpx", "time"})}


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
    for number in (*range(1, 5), *range(26, 31), 52):
        assert f"NEEDS-OWNER(AMB-{number})" in sources


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
