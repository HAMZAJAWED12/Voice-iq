"""The frozen inputs in `frozen`: H5 rows F-1 to F-21 (S2 §3.10, §3.12, §4, §6, §11; AMB-147, AMB-157).

Every input is synthetic and built here or in `_h5_fixtures`, which writes the file bytes with the standard library
alone. Refusals must name files, keys, ids, line numbers and counts, never content, so the bad files carry a canary
string that no message may hold. No test reads a gold directory except the synthetic ones under `tmp_path`.
"""

from __future__ import annotations

import codecs
import copy
import dataclasses
import hashlib
import json
import os
import random
import shutil
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from app.evaluation.bakeoff import frozen
from app.evaluation.bakeoff import inputs as inputs_module
from app.evaluation.bakeoff.client import ChatRequest, RepairTemplate
from app.evaluation.bakeoff.gates import ExpectedFreeze, parse_expected_freeze
from app.evaluation.bakeoff.gold import GoldSegment, GoldSet, load_gold_set
from app.evaluation.bakeoff.inputs import (
    CAP_SESSION_ROWS,
    CapSessionSpec,
    FreezeHashes,
    FrozenInputs,
    InputsError,
    ProbeItem,
    ProbeSource,
    PromptSet,
    WarmupSpec,
    freeze_inputs,
    input_text,
)
from app.evaluation.bakeoff.probe import MAX_SOURCES_PER_CLAIM
from app.evaluation.bakeoff.records import RunView, record_json
from app.evaluation.bakeoff.schemas import canonical_sha256
from app.evaluation.tests._client_fixtures import block_sockets
from app.evaluation.tests._fixtures import make_conversation, make_item, write_version
from app.evaluation.tests._h5_fixtures import (
    H5_INPUT_FILES,
    H5_PROMPTS,
    INPUTS_MANIFEST_NAME,
    PROBE_FILE,
    PROMPT_FILE_NAMES,
    WARMUP_FILE,
    h5_frozen_inputs,
    h5_input_bytes,
    inputs_manifest_bytes,
    pretty_json_bytes,
    probe_value,
    warmup_value,
    write_h5_inputs,
)
from app.evaluation.tests._runner_fixtures import (
    PROBE_ITEMS,
    PROMPTS,
    WARMUP,
    block_processes,
    gold_rows,
    schedule_rig,
    stop_before,
    synthetic_gold,
)

CANARY = "ZQXCANARY-4471"
EXTRACTION_USER = PROMPT_FILE_NAMES["extraction_user"]
PROBE_USER = PROMPT_FILE_NAMES["probe_user"]


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


@pytest.fixture(scope="module")
def frozen_h5(tmp_path_factory: pytest.TempPathFactory) -> FrozenInputs:
    return h5_frozen_inputs(tmp_path_factory.mktemp("gold"))


def refusal(call: object, *args: object) -> str:
    """The message of the `FrozenInputError` that `call(*args)` raises."""
    assert callable(call)
    with pytest.raises(frozen.FrozenInputError) as caught:
        call(*args)
    return str(caught.value)


def assert_no_canary(message: str) -> None:
    """No substring of the canary of 6 or more characters reaches a message."""
    for start in range(len(CANARY) - 5):
        assert CANARY[start : start + 6] not in message, message


def canary_probe() -> tuple[ProbeItem, ...]:
    """The standard probe shape, with the canary in every claim and source text."""
    return tuple(
        ProbeItem(
            f"probe_{number:02d}",
            f"{CANARY} claim {number}.",
            tuple(ProbeSource(f"src_{index}", f"{CANARY} source {number}.{index}.") for index in range(1, 6)),
        )
        for number in range(1, 21)
    )


CANARY_WARMUP = WarmupSpec(
    "warmup",
    (
        GoldSegment("w_001", "SPEAKER_00", 0.0, 2.0, f"{CANARY} first warm-up line.", 0.9),
        GoldSegment("w_002", "SPEAKER_01", 2.0, 4.5, f"{CANARY} second warm-up line.", 0.8),
    ),
)


def with_file(name: str, data: bytes) -> dict[str, bytes]:
    files = h5_input_bytes()
    files[name] = data
    return files


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Constants and types (S2 §3.10, §4).


def test_the_constants_name_the_seven_files_and_the_formats() -> None:
    assert frozen.INPUT_FILES == H5_INPUT_FILES
    assert frozen.INPUT_FILES == tuple(sorted(frozen.INPUT_FILES))
    assert len(frozen.INPUT_FILES) == 7
    assert isinstance(frozen.PROMPT_FILES, MappingProxyType)
    assert dict(frozen.PROMPT_FILES) == dict(PROMPT_FILE_NAMES)
    assert tuple(frozen.PROMPT_FILES) == (
        "extraction_system",
        "extraction_user",
        "probe_system",
        "probe_user",
        "repair",
    )
    assert (frozen.FORMAT_PROBE, frozen.FORMAT_WARMUP) == ("bakeoff-probe/1", "bakeoff-warmup/1")
    assert frozen.FORMAT_CAP_SESSIONS == "bakeoff-cap-sessions/1"
    assert frozen.CAP_ORDER_RULE == "ascending-source-id-greedy-wrap/1"
    assert frozen.INPUTS_MANIFEST == INPUTS_MANIFEST_NAME == "INPUTS.sha256"
    assert (frozen.CAP_SESSIONS_FILE, frozen.EXPECTED_FREEZE_FILE) == ("cap-sessions.json", "EXPECTED-FREEZE.json")
    assert (frozen.PROBE_FILE, frozen.WARMUP_FILE) == (PROBE_FILE, WARMUP_FILE)
    assert frozen.PROBE_SOURCE_COUNT == MAX_SOURCES_PER_CLAIM == 5
    assert issubclass(frozen.FrozenInputError, ValueError)


def test_draft_inputs_and_source_delta_are_frozen_slotted_values() -> None:
    for kind in (frozen.DraftInputs, frozen.SourceDelta):
        assert dataclasses.is_dataclass(kind)
        assert hasattr(kind, "__slots__")
    assert [field.name for field in dataclasses.fields(frozen.DraftInputs)] == [
        "prompts",
        "probe_items",
        "warmup",
        "file_sha256",
    ]
    assert [field.name for field in dataclasses.fields(frozen.SourceDelta)] == [
        "item_id",
        "source_id",
        "full",
        "emptied",
    ]
    draft = frozen.parse_draft_inputs(h5_input_bytes())
    with pytest.raises(dataclasses.FrozenInstanceError):
        draft.warmup = WARMUP  # type: ignore[misc]


# F-1: the text rules of every file (S2 §3.10).


def test_text_value_takes_lf_text_and_drops_the_one_final_lf() -> None:
    assert frozen.text_value(b"line one\nline two\n", "prompts/x.txt") == "line one\nline two"
    assert frozen.text_value(b"one line\n", "prompts/x.txt") == "one line"
    assert frozen.text_value(b"a\n\nb\n", "prompts/x.txt") == "a\n\nb"


def test_text_value_accepts_crlf_after_normalisation() -> None:
    assert frozen.text_value(b"line one\r\nline two\r\n", "prompts/x.txt") == "line one\nline two"


_CANARY_BYTES = CANARY.encode("ascii")


@pytest.mark.parametrize(
    ("raw", "line"),
    [
        (b"good\n" + _CANARY_BYTES + b"\rx\n", 2),  # a lone CR
        (codecs.BOM_UTF8 + _CANARY_BYTES + b"\n", 1),  # a BOM
        (b"good\n" + _CANARY_BYTES, 2),  # no final LF
        (b"good\n" + _CANARY_BYTES + b"\n\n", 3),  # a doubled final LF
        (b"good\n" + _CANARY_BYTES + b" \n", 2),  # a trailing space
        (b"good\n" + _CANARY_BYTES + b"\t\n", 2),  # a trailing tab
        (b"good\n" + _CANARY_BYTES + bytes([0xFF]) + b"\n", 2),  # not UTF-8
        (b"good\n" + (CANARY + chr(0xE9)).encode("utf-8") + b"\n", 2),  # not ASCII
        (b"good\n" + _CANARY_BYTES + b"\x07\n", 2),  # a control character
        (b"good\n" + _CANARY_BYTES + b"\tinside\n", 2),  # a tab inside a line
    ],
    ids=[
        "lone-cr",
        "bom",
        "no-final-lf",
        "doubled-final-lf",
        "trailing-space",
        "trailing-tab",
        "not-utf8",
        "not-ascii",
        "control-char",
        "interior-tab",
    ],
)
def test_text_value_refuses_each_fault_naming_file_and_line_only(raw: bytes, line: int) -> None:
    message = refusal(frozen.text_value, raw, "prompts/x.txt")
    assert "prompts/x.txt" in message
    assert f"line {line}" in message
    assert_no_canary(message)


def test_text_value_refuses_an_empty_file() -> None:
    assert "prompts/x.txt" in refusal(frozen.text_value, b"", "prompts/x.txt")


# The JSON file helpers (S2 §4).


def test_pretty_json_is_the_canonical_file_form() -> None:
    value = probe_value(PROBE_ITEMS)
    assert frozen.pretty_json(value) == pretty_json_bytes(value)
    assert frozen.pretty_json({"b": 1, "a": [1, 2]}) == b'{\n  "a": [\n    1,\n    2\n  ],\n  "b": 1\n}\n'
    assert frozen.pretty_json({"text": chr(0xE9)}).isascii()


def test_json_value_round_trips_canonical_bytes_and_accepts_crlf() -> None:
    raw = pretty_json_bytes(warmup_value(WARMUP))
    assert frozen.json_value(raw, WARMUP_FILE) == warmup_value(WARMUP)
    assert frozen.json_value(raw.replace(b"\n", b"\r\n"), WARMUP_FILE) == warmup_value(WARMUP)


def test_json_value_refuses_a_number_out_of_float_range() -> None:
    raw = pretty_json_bytes({"a": 1.5}).replace(b"1.5", b"1e400")
    message = refusal(frozen.json_value, raw, WARMUP_FILE)
    assert WARMUP_FILE in message


# F-2: INPUTS.sha256 (S2 §3.12, §11 item 6).


def test_inputs_manifest_is_the_sha256sum_form() -> None:
    files = h5_input_bytes()
    assert frozen.inputs_manifest(files) == inputs_manifest_bytes(files)
    lines = frozen.inputs_manifest(files).decode("ascii").split("\n")
    assert lines[-1] == "" and len(lines) == 8
    assert [line.split("  ")[1] for line in lines[:-1]] == list(H5_INPUT_FILES)


def test_inputs_manifest_hashes_the_lf_bytes() -> None:
    files = h5_input_bytes()
    crlf = {name: data.replace(b"\n", b"\r\n") for name, data in files.items()}
    assert frozen.inputs_manifest(crlf) == frozen.inputs_manifest(files)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_inputs_manifest_refuses_another_file_set(change: str) -> None:
    files = h5_input_bytes()
    if change == "missing":
        del files[WARMUP_FILE]
    else:
        files["notes.txt"] = b"x\n"
    message = refusal(frozen.inputs_manifest, files)
    assert (WARMUP_FILE if change == "missing" else "notes.txt") in message


def test_check_inputs_manifest_returns_each_files_hash() -> None:
    files = h5_input_bytes()
    checked = frozen.check_inputs_manifest(inputs_manifest_bytes(files), files)
    assert isinstance(checked, MappingProxyType)
    assert tuple(checked) == H5_INPUT_FILES
    assert dict(checked) == {name: sha(data) for name, data in files.items()}


def test_check_inputs_manifest_accepts_a_crlf_checkout() -> None:
    files = h5_input_bytes()
    manifest = inputs_manifest_bytes(files).replace(b"\n", b"\r\n")
    crlf = {name: data.replace(b"\n", b"\r\n") for name, data in files.items()}
    assert dict(frozen.check_inputs_manifest(manifest, crlf)) == {name: sha(data) for name, data in files.items()}


def _manifest_lines(files: dict[str, bytes]) -> list[str]:
    return inputs_manifest_bytes(files).decode("ascii").splitlines()


def _joined(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode("ascii")


def test_check_inputs_manifest_refuses_a_missing_line() -> None:
    files = h5_input_bytes()
    lines = [line for line in _manifest_lines(files) if not line.endswith(PROBE_FILE)]
    message = refusal(frozen.check_inputs_manifest, _joined(lines), files)
    assert PROBE_FILE in message and "not listed" in message


def test_check_inputs_manifest_refuses_an_extra_line() -> None:
    files = h5_input_bytes()
    lines = [*_manifest_lines(files), f"{'0' * 64}  notes.txt"]
    message = refusal(frozen.check_inputs_manifest, _joined(lines), files)
    assert "notes.txt" in message


def test_check_inputs_manifest_refuses_a_duplicate_line() -> None:
    files = h5_input_bytes()
    lines = _manifest_lines(files)
    message = refusal(frozen.check_inputs_manifest, _joined([*lines, lines[0]]), files)
    assert H5_INPUT_FILES[0] in message and "2 times" in message


@pytest.mark.parametrize("ending", ["lf", "crlf"])
def test_check_inputs_manifest_refuses_a_missing_final_lf(ending: str) -> None:
    files = h5_input_bytes()
    manifest = inputs_manifest_bytes(files)
    if ending == "crlf":
        manifest = manifest.replace(b"\n", b"\r\n")
    message = refusal(frozen.check_inputs_manifest, manifest.rstrip(b"\r\n"), files)
    assert f"{INPUTS_MANIFEST_NAME}: line {len(H5_INPUT_FILES)} has no final LF" in message


def test_check_inputs_manifest_refuses_an_empty_manifest_as_having_no_final_lf() -> None:
    message = refusal(frozen.check_inputs_manifest, b"", h5_input_bytes())
    assert f"{INPUTS_MANIFEST_NAME}: line 1 has no final LF" in message and "not listed" in message


@pytest.mark.parametrize(
    "bad",
    [
        f"{'0' * 63}  {PROBE_FILE}",
        f"{'0' * 64} {PROBE_FILE}",
        f"{'0' * 64}  ",
        f"{'g' * 64}  {PROBE_FILE}",
        "",
        CANARY,
    ],
    ids=["short-hash", "one-space", "no-path", "not-hex", "blank-line", "canary"],
)
def test_check_inputs_manifest_refuses_a_malformed_line(bad: str) -> None:
    files = h5_input_bytes()
    lines = _manifest_lines(files)
    message = refusal(frozen.check_inputs_manifest, _joined([*lines[:2], bad, *lines[2:]]), files)
    assert "line 3" in message
    assert_no_canary(message)


def test_check_inputs_manifest_refuses_uppercase_hex() -> None:
    files = h5_input_bytes()
    lines = _manifest_lines(files)
    digest, name = lines[0].split("  ")
    message = refusal(frozen.check_inputs_manifest, _joined([f"{digest.upper()}  {name}", *lines[1:]]), files)
    assert name in message and "uppercase" in message


def test_check_inputs_manifest_refuses_a_missing_file() -> None:
    files = h5_input_bytes()
    manifest = inputs_manifest_bytes(files)
    del files[WARMUP_FILE]
    message = refusal(frozen.check_inputs_manifest, manifest, files)
    assert WARMUP_FILE in message and "missing" in message


def test_check_inputs_manifest_refuses_a_one_byte_tamper_naming_the_file_only() -> None:
    files = h5_input_bytes(probe_items=canary_probe())
    manifest = inputs_manifest_bytes(files)
    files[PROBE_FILE] = files[PROBE_FILE].replace(b"claim 7.", b"claim 8.", 1)
    message = refusal(frozen.check_inputs_manifest, manifest, files)
    assert PROBE_FILE in message
    assert [name for name in H5_INPUT_FILES if name != PROBE_FILE and name in message] == []
    assert sha(files[PROBE_FILE]) not in message
    assert sha(h5_input_bytes(probe_items=canary_probe())[PROBE_FILE]) not in message
    assert_no_canary(message)


def test_check_inputs_manifest_refuses_a_non_ascii_manifest() -> None:
    files = h5_input_bytes()
    manifest = inputs_manifest_bytes(files) + (CANARY + chr(0xE9) + "\n").encode("utf-8")
    message = refusal(frozen.check_inputs_manifest, manifest, files)
    assert INPUTS_MANIFEST_NAME in message
    assert_no_canary(message)


# F-3: the placeholder rules come from the existing constructors (S2 §3.10).


@pytest.mark.parametrize(
    ("field", "text", "placeholder"),
    [
        ("extraction_user", "BEGIN\nno placeholder here\nEND", "<<INPUT_JSON>>"),
        ("probe_user", "BEGIN\n<<INPUT_JSON>>\nEND\n<<INPUT_JSON>>", "<<INPUT_JSON>>"),
        ("extraction_system", "System text.\n<<INPUT_JSON>>", "placeholder"),
        ("probe_system", "System text <<VALIDATION_ERRORS>>.", "placeholder"),
        ("extraction_user", "BEGIN\n<<INPUT_JSON>>\nEND\n<<VALIDATION_ERRORS>>", "<<VALIDATION_ERRORS>>"),
        ("repair", "Answer again with JSON only.", "<<VALIDATION_ERRORS>>"),
        ("repair", "<<VALIDATION_ERRORS>>\n<<VALIDATION_ERRORS>>", "<<VALIDATION_ERRORS>>"),
        ("repair", "<<VALIDATION_ERRORS>>\n<<INPUT_JSON>>", "<<INPUT_JSON>>"),
    ],
)
def test_each_placeholder_refusal_surfaces_as_frozen_input_error(field: str, text: str, placeholder: str) -> None:
    files = with_file(PROMPT_FILE_NAMES[field], (text + "\n").encode("ascii"))
    message = refusal(frozen.parse_draft_inputs, files)
    assert placeholder in message


def test_the_placeholder_refusal_keeps_the_constructor_message() -> None:
    with pytest.raises(InputsError) as original:
        PromptSet("System.", "No placeholder.", "System.", "BEGIN\n<<INPUT_JSON>>\nEND", H5_PROMPTS.repair)
    files = with_file(EXTRACTION_USER, b"No placeholder.\n")
    assert str(original.value) in refusal(frozen.parse_draft_inputs, files)
    with pytest.raises(ValueError) as repair:
        RepairTemplate("No placeholder.")
    assert str(repair.value) in refusal(frozen.parse_draft_inputs, with_file(PROMPT_FILE_NAMES["repair"], b"No.\n"))


def test_a_blank_prompt_file_is_refused() -> None:
    message = refusal(frozen.parse_draft_inputs, with_file(PROMPT_FILE_NAMES["probe_system"], b"\n"))
    assert "non-whitespace" in message


# F-4: the H5 delimiter rule (AMB-157).


@pytest.mark.parametrize(
    ("name", "text"),
    [
        (EXTRACTION_USER, "BEGIN TRANSCRIPT\nData: <<INPUT_JSON>>\nEND TRANSCRIPT"),
        (EXTRACTION_USER, "BEGIN TRANSCRIPT\n <<INPUT_JSON>>\nEND TRANSCRIPT"),
        (EXTRACTION_USER, "<<INPUT_JSON>>\nEND TRANSCRIPT"),
        (EXTRACTION_USER, "BEGIN TRANSCRIPT\n<<INPUT_JSON>>"),
        (EXTRACTION_USER, "Untrusted data follows.\n\n<<INPUT_JSON>>\nEND TRANSCRIPT"),
        (EXTRACTION_USER, "BEGIN TRANSCRIPT\n<<INPUT_JSON>>\n\nAnswer with JSON only."),
        (EXTRACTION_USER, "---\n<<INPUT_JSON>>\n---"),
        (PROBE_USER, "BEGIN CLAIM AND SOURCES\n<<INPUT_JSON>> END CLAIM AND SOURCES"),
        (PROBE_USER, "SOURCES\n<<INPUT_JSON>>\nSOURCES"),
    ],
    ids=[
        "not-alone",
        "leading-space",
        "no-opening-line",
        "no-closing-line",
        "blank-opening",
        "blank-closing",
        "equal",
        "probe-not-alone",
        "probe-equal",
    ],
)
def test_the_delimiter_rule_refuses_each_case(name: str, text: str) -> None:
    message = refusal(frozen.parse_draft_inputs, with_file(name, (text + "\n").encode("ascii")))
    assert name in message


def test_the_h3_prompts_fixture_breaks_the_delimiter_rule() -> None:
    """Control for the rule: each H3 user template ends with the placeholder line."""
    message = refusal(frozen.parse_draft_inputs, h5_input_bytes(prompts=PROMPTS))
    assert EXTRACTION_USER in message


def test_delimiters_returns_the_two_closing_delimiters() -> None:
    assert frozen.delimiters(H5_PROMPTS) == ("END TRANSCRIPT", "END CLAIM AND SOURCES")
    assert EXTRACTION_USER in refusal(frozen.delimiters, PROMPTS)
    only_probe_broken = dataclasses.replace(H5_PROMPTS, probe_user="Claim and sources:\n<<INPUT_JSON>>")
    assert PROBE_USER in refusal(frozen.delimiters, only_probe_broken)


# F-5: probe.json (S2 §4).


def _probe_raw(value: object) -> bytes:
    return pretty_json_bytes(value)


def _canary_probe_value() -> dict[str, object]:
    return probe_value(canary_probe())


def _items(value: dict[str, object]) -> list[dict[str, object]]:
    items = value["items"]
    assert isinstance(items, list)
    return items


def _sources(value: dict[str, object], position: int) -> list[dict[str, object]]:
    sources = _items(value)[position]["sources"]
    assert isinstance(sources, list)
    return sources


def _probe_case(case: str) -> dict[str, object]:
    value = _canary_probe_value()
    items = _items(value)
    if case == "format":
        value["format"] = "bakeoff-probe/2"
    elif case == "extra-top-key":
        value["notes"] = CANARY
    elif case == "missing-top-key":
        del value["format"]
    elif case == "extra-item-key":
        items[3]["verdict"] = "SUPPORTED"
    elif case == "missing-item-key":
        del items[3]["claim"]
    elif case == "extra-source-key":
        _sources(value, 3)[1]["url"] = CANARY
    elif case == "missing-source-key":
        del _sources(value, 3)[1]["text"]
    elif case == "19-items":
        del items[-1]
    elif case == "21-items":
        items.append(dict(items[0], itemId="probe_21"))
    elif case == "4-sources":
        del _sources(value, 3)[-1]
    elif case == "6-sources":
        _sources(value, 3).append({"sourceId": "src_6", "text": f"{CANARY} extra."})
    elif case == "duplicate-item-id":
        items[4]["itemId"] = "probe_04"
    elif case == "duplicate-source-id":
        _sources(value, 3)[2]["sourceId"] = "src_2"
    elif case == "colon-in-item-id":
        items[0]["itemId"] = "probe:01"
    elif case == "colon-in-source-id":
        _sources(value, 0)[0]["sourceId"] = "src:1"
    elif case == "blank-claim":
        items[5]["claim"] = "   "
    elif case == "empty-claim":
        items[5]["claim"] = ""
    elif case == "blank-text":
        _sources(value, 5)[4]["text"] = " "
    elif case == "items-not-list":
        value["items"] = {"probe_01": CANARY}
    elif case == "item-not-object":
        items[2] = CANARY
    elif case == "sources-not-list":
        items[2]["sources"] = CANARY
    elif case == "claim-not-string":
        items[2]["claim"] = 7
    elif case == "item-id-out-of-order":
        items[0]["itemId"], items[1]["itemId"] = "probe_02", "probe_01"
    elif case == "lone-surrogate":
        items[6]["claim"] = CANARY + chr(0xD800)
    else:
        raise AssertionError(case)
    return value


_PROBE_CASES = (
    "format",
    "extra-top-key",
    "missing-top-key",
    "extra-item-key",
    "missing-item-key",
    "extra-source-key",
    "missing-source-key",
    "19-items",
    "21-items",
    "4-sources",
    "6-sources",
    "duplicate-item-id",
    "duplicate-source-id",
    "colon-in-item-id",
    "colon-in-source-id",
    "blank-claim",
    "empty-claim",
    "blank-text",
    "items-not-list",
    "item-not-object",
    "sources-not-list",
    "claim-not-string",
    "item-id-out-of-order",
    "lone-surrogate",
)


@pytest.mark.parametrize("case", _PROBE_CASES)
def test_the_probe_file_refuses_each_case_without_content(case: str) -> None:
    files = h5_input_bytes()
    files[PROBE_FILE] = _probe_raw(_probe_case(case))
    message = refusal(frozen.parse_draft_inputs, files)
    assert PROBE_FILE in message
    assert_no_canary(message)


def test_the_probe_count_refusals_name_the_counts() -> None:
    for case, count in (("19-items", "19"), ("21-items", "21"), ("4-sources", "4"), ("6-sources", "6")):
        files = with_file(PROBE_FILE, _probe_raw(_probe_case(case)))
        assert count in refusal(frozen.parse_draft_inputs, files), case


def test_the_probe_key_refusals_name_the_key() -> None:
    files = with_file(PROBE_FILE, _probe_raw(_probe_case("missing-item-key")))
    assert "claim" in refusal(frozen.parse_draft_inputs, files)
    files = with_file(PROBE_FILE, _probe_raw(_probe_case("extra-item-key")))
    assert "verdict" in refusal(frozen.parse_draft_inputs, files)


def test_the_probe_file_refuses_a_duplicate_json_key() -> None:
    raw = _probe_raw(_canary_probe_value())
    line = b'  "format": "bakeoff-probe/1",\n'
    assert line in raw
    message = refusal(frozen.parse_draft_inputs, with_file(PROBE_FILE, raw.replace(line, line + line, 1)))
    assert PROBE_FILE in message and "duplicate key" in message
    assert_no_canary(message)


def test_the_probe_file_refuses_nan() -> None:
    raw = _probe_raw(_canary_probe_value()).replace(b'"bakeoff-probe/1"', b"NaN", 1)
    message = refusal(frozen.parse_draft_inputs, with_file(PROBE_FILE, raw))
    assert PROBE_FILE in message and "NaN" in message


def test_the_probe_file_refuses_a_non_ascii_byte_naming_the_line() -> None:
    raw = _probe_raw(_canary_probe_value())
    target = f"{CANARY} claim 3.".encode("ascii")
    line = raw[: raw.index(target)].count(b"\n") + 1
    bad = raw.replace(target, (CANARY + chr(0xE9) + " claim 3.").encode("utf-8"), 1)
    message = refusal(frozen.parse_draft_inputs, with_file(PROBE_FILE, bad))
    assert PROBE_FILE in message and f"line {line}" in message
    assert_no_canary(message)


def test_the_probe_file_refuses_non_canonical_bytes() -> None:
    value = _canary_probe_value()
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=4) + "\n").encode("ascii")
    message = refusal(frozen.parse_draft_inputs, with_file(PROBE_FILE, raw))
    assert PROBE_FILE in message and "canonical" in message
    assert_no_canary(message)
    reordered = {"items": [{"itemId": row["itemId"], **row} for row in _items(value)], "format": value["format"]}
    unsorted = (json.dumps(reordered, ensure_ascii=True, indent=2) + "\n").encode("ascii")
    assert unsorted != raw and json.loads(unsorted) == value
    assert "canonical" in refusal(frozen.parse_draft_inputs, with_file(PROBE_FILE, unsorted))


def test_the_probe_file_keeps_non_ascii_text_as_escapes() -> None:
    items = (ProbeItem("probe_01", "Caf" + chr(0xE9) + " claim.", PROBE_ITEMS[0].sources), *PROBE_ITEMS[1:])
    draft = frozen.parse_draft_inputs(h5_input_bytes(probe_items=items))
    assert draft.probe_items == items


# F-6: warmup.json (S2 §3.13, §11 item 5).


def _warmup_case(case: str) -> dict[str, object]:
    value = warmup_value(CANARY_WARMUP)
    segments = value["segments"]
    assert isinstance(segments, list)
    first = segments[0]
    if case == "format":
        value["format"] = "bakeoff-warmup/2"
    elif case == "extra-key":
        value["notes"] = CANARY
    elif case == "missing-key":
        del value["itemId"]
    elif case == "segment-extra-key":
        first["language"] = "en"
    elif case == "segment-missing-key":
        del first["speaker"]
    elif case == "no-segments":
        value["segments"] = []
    elif case == "segments-not-list":
        value["segments"] = CANARY
    elif case == "segment-not-object":
        segments[1] = CANARY
    elif case == "blank-item-id":
        value["itemId"] = ""
    elif case == "duplicate-segment-id":
        segments[1]["segmentId"] = "w_001"
    elif case == "blank-speaker":
        first["speaker"] = ""
    elif case == "blank-text":
        first["text"] = "  "
    elif case == "bool-start":
        first["start"] = False
    elif case == "bool-confidence":
        first["confidence"] = True
    elif case == "string-end":
        first["end"] = "2.0"
    elif case == "start-equals-end":
        first["start"], first["end"] = 2.0, 2.0
    elif case == "start-after-end":
        first["start"], first["end"] = 3.0, 2.0
    elif case == "negative-start":
        first["start"] = -0.5
    elif case == "confidence-below-0":
        first["confidence"] = -0.1
    elif case == "confidence-above-1":
        first["confidence"] = 1.1
    else:
        raise AssertionError(case)
    return value


_WARMUP_CASES = (
    "format",
    "extra-key",
    "missing-key",
    "segment-extra-key",
    "segment-missing-key",
    "no-segments",
    "segments-not-list",
    "segment-not-object",
    "blank-item-id",
    "duplicate-segment-id",
    "blank-speaker",
    "blank-text",
    "bool-start",
    "bool-confidence",
    "string-end",
    "start-equals-end",
    "start-after-end",
    "negative-start",
    "confidence-below-0",
    "confidence-above-1",
)
_NAMES_SEGMENT = frozenset(
    {
        "bool-start",
        "bool-confidence",
        "string-end",
        "start-equals-end",
        "start-after-end",
        "negative-start",
        "confidence-below-0",
        "confidence-above-1",
    }
)


@pytest.mark.parametrize("case", _WARMUP_CASES)
def test_the_warmup_file_refuses_each_case_without_content(case: str) -> None:
    message = refusal(frozen.parse_draft_inputs, with_file(WARMUP_FILE, pretty_json_bytes(_warmup_case(case))))
    assert WARMUP_FILE in message
    if case in _NAMES_SEGMENT:
        assert "w_001" in message
    assert_no_canary(message)


@pytest.mark.parametrize("number", [b"1e400", b"-1e400"])
def test_the_warmup_file_refuses_a_non_finite_number(number: bytes) -> None:
    raw = pretty_json_bytes(warmup_value(CANARY_WARMUP)).replace(b'"end": 4.5', b'"end": ' + number, 1)
    message = refusal(frozen.parse_draft_inputs, with_file(WARMUP_FILE, raw))
    assert WARMUP_FILE in message
    assert_no_canary(message)


def test_the_warmup_file_refuses_non_canonical_bytes() -> None:
    raw = (json.dumps(warmup_value(CANARY_WARMUP), ensure_ascii=True, sort_keys=True, indent=1) + "\n").encode("ascii")
    message = refusal(frozen.parse_draft_inputs, with_file(WARMUP_FILE, raw))
    assert WARMUP_FILE in message and "canonical" in message
    assert_no_canary(message)


def test_the_warmup_file_takes_ints_as_floats() -> None:
    warmup = WarmupSpec("warmup", (GoldSegment("w_001", "SPEAKER_00", 0, 2, "An integer-timed line.", 1),))
    draft = frozen.parse_draft_inputs(h5_input_bytes(warmup=warmup))
    segment = draft.warmup.segments[0]
    assert (segment.start, segment.end, segment.confidence) == (0.0, 2.0, 1.0)
    assert all(isinstance(value, float) for value in (segment.start, segment.end, segment.confidence))


def test_the_warmup_boundaries_are_accepted() -> None:
    warmup = WarmupSpec(
        "warmup",
        (
            GoldSegment("w_001", "SPEAKER_00", 0.0, 0.5, "A line at the lower bounds.", 0.0),
            GoldSegment("w_002", "SPEAKER_00", 0.5, 1.0, "A line at the upper bound.", 1.0),
        ),
    )
    assert frozen.parse_draft_inputs(h5_input_bytes(warmup=warmup)).warmup == warmup


# parse_draft_inputs as a whole.


def test_parse_draft_inputs_gives_the_frozen_values() -> None:
    files = h5_input_bytes(warmup=CANARY_WARMUP)
    draft = frozen.parse_draft_inputs(files)
    assert draft.prompts == H5_PROMPTS
    assert draft.probe_items == PROBE_ITEMS
    assert draft.warmup == CANARY_WARMUP
    assert isinstance(draft.file_sha256, MappingProxyType)
    assert dict(draft.file_sha256) == {name: sha(data) for name, data in files.items()}
    assert tuple(draft.file_sha256) == H5_INPUT_FILES


def test_parse_draft_inputs_accepts_a_crlf_checkout() -> None:
    files = h5_input_bytes()
    crlf = {name: data.replace(b"\n", b"\r\n") for name, data in files.items()}
    assert frozen.parse_draft_inputs(crlf) == frozen.parse_draft_inputs(files)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_parse_draft_inputs_refuses_another_file_set(change: str) -> None:
    files = h5_input_bytes()
    if change == "missing":
        del files[PROBE_FILE]
    else:
        files["prompts/notes.txt"] = b"x\n"
    message = refusal(frozen.parse_draft_inputs, files)
    assert (PROBE_FILE if change == "missing" else "prompts/notes.txt") in message


def test_a_prompt_file_refusal_names_its_file() -> None:
    files = with_file(PROMPT_FILE_NAMES["probe_system"], (CANARY + " \n").encode("ascii"))
    message = refusal(frozen.parse_draft_inputs, files)
    assert PROMPT_FILE_NAMES["probe_system"] in message and "line 1" in message
    assert_no_canary(message)


# F-9: the gold-free hashes equal the freeze's (S2 §11 item 6).


def test_the_probe_hash_formula_is_the_freezes(frozen_h5: FrozenInputs) -> None:
    """Control: the 3.3 formula over the file's items, keyed as the freeze keys them, is `FreezeHashes.probe_sha256`."""
    items = probe_value(PROBE_ITEMS)["items"]
    assert isinstance(items, list)
    formula = [{"itemId": row["itemId"], "claim": row["claim"], "sources": row["sources"]} for row in items]
    assert canonical_sha256(formula) == frozen_h5.hashes.probe_sha256
    assert H5_PROMPTS.sha256() == frozen_h5.hashes.prompts_sha256
    assert H5_PROMPTS.repair.sha256() == frozen_h5.hashes.repair_sha256


def test_gold_free_hashes_equal_the_freeze_hashes(frozen_h5: FrozenInputs) -> None:
    hashes = frozen.gold_free_hashes(frozen.parse_draft_inputs(h5_input_bytes()))
    assert isinstance(hashes, MappingProxyType)
    assert dict(hashes) == {
        "prompts_sha256": frozen_h5.hashes.prompts_sha256,
        "repair_sha256": frozen_h5.hashes.repair_sha256,
        "probe_sha256": frozen_h5.hashes.probe_sha256,
    }


def test_one_repair_character_moves_both_prompt_hashes_and_not_the_probe_hash() -> None:
    files = h5_input_bytes()
    base = frozen.gold_free_hashes(frozen.parse_draft_inputs(files))
    name = PROMPT_FILE_NAMES["repair"]
    files[name] = files[name].replace(b"again", b"agaiN", 1)
    moved = frozen.gold_free_hashes(frozen.parse_draft_inputs(files))
    assert moved["prompts_sha256"] != base["prompts_sha256"]
    assert moved["repair_sha256"] != base["repair_sha256"]
    assert moved["probe_sha256"] == base["probe_sha256"]


# F-11 (the load_draft_inputs part): reading through the injected reader.


class Reader:
    """`read_bytes` over a real directory, recording each path."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def __call__(self, path: str) -> bytes:
        self.paths.append(path)
        return Path(path).read_bytes()


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_read_input_files_reads_the_seven_files_through_the_reader(tmp_path: Path) -> None:
    written = write_h5_inputs(tmp_path)
    reader = Reader()
    files = frozen.read_input_files(str(tmp_path), reader)
    assert tuple(files) == H5_INPUT_FILES
    assert dict(files) == {name: written[name] for name in H5_INPUT_FILES}
    assert reader.paths == [f"{tmp_path}/{name}" for name in H5_INPUT_FILES]


def test_load_draft_inputs_loads_a_written_directory(tmp_path: Path) -> None:
    written = write_h5_inputs(tmp_path)
    reader = Reader()
    draft = frozen.load_draft_inputs(str(tmp_path), reader)
    assert draft == frozen.parse_draft_inputs({name: written[name] for name in H5_INPUT_FILES})
    assert draft.prompts == H5_PROMPTS and draft.probe_items == PROBE_ITEMS and draft.warmup == WARMUP
    assert reader.paths[0] == f"{tmp_path}/{INPUTS_MANIFEST_NAME}"
    assert sorted(reader.paths[1:]) == [f"{tmp_path}/{name}" for name in H5_INPUT_FILES]
    assert frozen.load_draft_inputs(f"{tmp_path}/", Reader()) == draft


def test_load_draft_inputs_checks_the_manifest_before_any_parse(tmp_path: Path) -> None:
    """The probe file is not JSON at all, and the manifest lists the good bytes: the manifest refusal comes first."""
    files = h5_input_bytes()
    manifest = inputs_manifest_bytes(files)
    files[PROBE_FILE] = (CANARY + "\n").encode("ascii")
    files[INPUTS_MANIFEST_NAME] = manifest
    write_h5_inputs(tmp_path, files, manifest=False)
    message = refusal(frozen.load_draft_inputs, str(tmp_path), Reader())
    assert PROBE_FILE in message and "does not match" in message
    assert "JSON" not in message
    assert_no_canary(message)


def test_load_draft_inputs_refuses_a_directory_with_no_matching_manifest_line(tmp_path: Path) -> None:
    files = h5_input_bytes()
    lines = [line for line in inputs_manifest_bytes(files).decode("ascii").splitlines() if WARMUP_FILE not in line]
    write_h5_inputs(tmp_path, {**files, INPUTS_MANIFEST_NAME: _joined(lines)}, manifest=False)
    assert WARMUP_FILE in refusal(frozen.load_draft_inputs, str(tmp_path), Reader())


def test_a_reader_error_propagates_unchanged_and_nothing_is_written(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path)
    before = _snapshot(tmp_path)
    error = PermissionError(13, "refused by the fake reader")

    def refuse(path: str) -> bytes:
        raise error

    with pytest.raises(PermissionError) as caught:
        frozen.load_draft_inputs(str(tmp_path), refuse)
    assert caught.value is error

    def refuse_probe(path: str) -> bytes:
        if path.endswith(PROBE_FILE):
            raise error
        return Path(path).read_bytes()

    with pytest.raises(PermissionError) as caught:
        frozen.load_draft_inputs(str(tmp_path), refuse_probe)
    assert caught.value is error
    assert _snapshot(tmp_path) == before


def test_a_missing_file_reaches_the_caller_as_the_readers_error(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path)
    (tmp_path / WARMUP_FILE).unlink()
    with pytest.raises(FileNotFoundError):
        frozen.load_draft_inputs(str(tmp_path), Reader())


# F-12: every probe case the freeze refuses, the loader refuses too (S2 §4).


def _source_set(count: int) -> tuple[ProbeSource, ...]:
    return tuple(ProbeSource(f"src_{index}", f"Source text {index}.") for index in range(1, count + 1))


def _freeze_refused_probes() -> dict[str, tuple[ProbeItem, ...]]:
    base = list(PROBE_ITEMS)

    def replaced(position: int, item: ProbeItem) -> tuple[ProbeItem, ...]:
        return (*base[:position], item, *base[position + 1 :])

    sources = list(_source_set(5))
    return {
        "19-items": tuple(base[:19]),
        "21-items": (*base, ProbeItem("probe_21", "One more claim.", _source_set(5))),
        "empty-item-id": replaced(2, ProbeItem("", "A claim.", _source_set(5))),
        "repeated-item-id": replaced(2, ProbeItem("probe_02", "A claim.", _source_set(5))),
        "blank-claim": replaced(2, ProbeItem("probe_03", "  ", _source_set(5))),
        "4-sources": replaced(2, ProbeItem("probe_03", "A claim.", _source_set(4))),
        "6-sources": replaced(2, ProbeItem("probe_03", "A claim.", _source_set(6))),
        "empty-source-id": replaced(2, ProbeItem("probe_03", "A claim.", (ProbeSource("", "x."), *sources[1:]))),
        "repeated-source-id": replaced(
            2, ProbeItem("probe_03", "A claim.", (*sources[:4], ProbeSource("src_1", "Text.")))
        ),
        "blank-source-text": replaced(2, ProbeItem("probe_03", "A claim.", (*sources[:4], ProbeSource("src_5", " ")))),
    }


@pytest.mark.parametrize("case", sorted(_freeze_refused_probes()))
def test_every_freeze_probe_refusal_is_a_loader_refusal(case: str) -> None:
    items = _freeze_refused_probes()[case]
    with pytest.raises(InputsError):
        inputs_module._check_probe_items(items)
    message = refusal(frozen.parse_draft_inputs, h5_input_bytes(probe_items=items))
    assert PROBE_FILE in message


def test_the_freeze_probe_cases_cover_each_check_of_the_freeze() -> None:
    """The cases above reach each refusal message of `_check_probe_items`, so none of its checks is left untested."""
    texts = []
    for items in _freeze_refused_probes().values():
        with pytest.raises(InputsError) as caught:
            inputs_module._check_probe_items(items)
        texts.append(str(caught.value))
    for fragment in (
        "exactly 20 items",
        "ids must be non-empty strings",
        "is used twice",
        "the claim must contain",
        "exactly 5 sources",
        "every source text",
    ):
        assert any(fragment in text for text in texts), fragment


# F-21 and AMB-148: the per-source count requests.


def test_source_delta_requests_pair_each_source_with_the_unchanged_request(frozen_h5: FrozenInputs) -> None:
    draft = frozen.parse_draft_inputs(h5_input_bytes())
    deltas = frozen.source_delta_requests(draft)
    assert isinstance(deltas, tuple) and len(deltas) == 100
    expected_ids = [(item.item_id, source.source_id) for item in PROBE_ITEMS for source in item.sources]
    assert [(delta.item_id, delta.source_id) for delta in deltas] == expected_ids
    full_by_item = {probe.item_id: probe.request for probe in frozen_h5.probes}
    for delta in deltas:
        assert delta.full == full_by_item[delta.item_id]


def test_each_emptied_request_differs_only_in_its_sources_text() -> None:
    items = canary_probe()
    draft = frozen.parse_draft_inputs(h5_input_bytes(probe_items=items))
    deltas = frozen.source_delta_requests(draft)
    by_item = {item.item_id: item for item in items}
    for delta in deltas:
        item = by_item[delta.item_id]
        sources = [
            {"sourceId": source.source_id, "text": "" if source.source_id == delta.source_id else source.text}
            for source in item.sources
        ]
        expected = ChatRequest(
            "reasoning_probe", H5_PROMPTS.probe_messages(input_text({"claim": item.claim, "sources": sources}))
        )
        assert delta.emptied == expected
        assert delta.emptied.constrained and delta.full.constrained
        full_body, emptied_body = delta.full.body(), delta.emptied.body()
        assert full_body["messages"][0] == emptied_body["messages"][0]
        assert {key: value for key, value in full_body.items() if key != "messages"} == {
            key: value for key, value in emptied_body.items() if key != "messages"
        }
        assert delta.full.sha256() != delta.emptied.sha256()


# A4: the cap-session builder (AMB-147), the cap-session file, the overlap counts (AMB-157), `load_frozen`, the
# expected-freeze bytes and `check_expected` (S2 §3.12, §6, §11).


def cap_gold(root: Path, rows: dict[str, int], *, other: dict[str, int] | None = None, name: str = "v1.0") -> Path:
    """A synthetic gold version: conversation `source_id` holds `rows[source_id]` in-scope CHECKABLE rows, then
    `other[source_id]` OPINION rows. A conversation with no row of either kind has one segment and no item."""
    conversations: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for source_id, count in rows.items():
        extra = (other or {}).get(source_id, 0)
        texts = [f"{source_id} line {number} states a fact." for number in range(1, max(count + extra, 1) + 1)]
        conversation = make_conversation(source_id, texts)
        conversations.append(conversation)
        items += [
            make_item(f"{source_id}_{n:02d}", conversation, n, "CHECKABLE", "GENERAL") for n in range(1, count + 1)
        ]
        items += [
            make_item(f"{source_id}_{n:02d}", conversation, n, "OPINION", "GENERAL")
            for n in range(count + 1, count + extra + 1)
        ]
    return write_version(root, conversations, items, name=name)


def built(tmp_path: Path, rows: dict[str, int], other: dict[str, int] | None = None) -> tuple[tuple[str, ...], ...]:
    specs = frozen.build_cap_sessions(load_gold_set(cap_gold(tmp_path, rows, other=other)))
    assert [spec.session_id for spec in specs] == ["cap_1", "cap_2", "cap_3", "cap_4"]
    return tuple(spec.source_ids for spec in specs)


def assert_i5(gold_dir: Path, specs: tuple[CapSessionSpec, ...]) -> None:
    """Each session reaches the cap only with its last conversation, and none repeats within a session."""
    gold = load_gold_set(gold_dir)
    for spec in specs:
        rows = [frozen.cap_session_rows(gold, (source_id,)) for source_id in spec.source_ids]
        assert sum(rows) >= CAP_SESSION_ROWS > sum(rows[:-1])
        assert len(set(spec.source_ids)) == len(spec.source_ids)


def test_overlap_counts_is_a_frozen_slotted_value() -> None:
    assert dataclasses.is_dataclass(frozen.OverlapCounts) and hasattr(frozen.OverlapCounts, "__slots__")
    assert [field.name for field in dataclasses.fields(frozen.OverlapCounts)] == [
        "equal",
        "contained",
        "delimiter_hits",
    ]
    counts = frozen.OverlapCounts(0, 0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        counts.equal = 1  # type: ignore[misc]


def test_cap_session_rows_counts_in_scope_checkable_rows_only(tmp_path: Path) -> None:
    gold = load_gold_set(cap_gold(tmp_path, {"syn_1": 3, "syn_2": 0, "syn_3": 5}, other={"syn_1": 4, "syn_2": 2}))
    assert frozen.cap_session_rows(gold, ("syn_1",)) == 3
    assert frozen.cap_session_rows(gold, ("syn_2",)) == 0
    assert frozen.cap_session_rows(gold, ("syn_1", "syn_3")) == 8
    assert frozen.cap_session_rows(gold, ()) == 0


# F-17: the builder.


def test_the_builder_sorts_by_code_point_not_file_or_number_order(tmp_path: Path) -> None:
    rows = {"syn_2": 20, "syn_10": 20, "syn_3": 20, "syn_1": 20}
    assert built(tmp_path, rows) == (("syn_1",), ("syn_10",), ("syn_2",), ("syn_3",))


def test_the_builder_skips_conversations_with_no_checkable_row(tmp_path: Path) -> None:
    rows = {"syn_1": 0, "syn_2": 20, "syn_3": 0, "syn_4": 20, "syn_5": 20, "syn_6": 20, "syn_7": 0}
    result = built(tmp_path, rows, other={"syn_1": 3, "syn_3": 5})
    assert result == (("syn_2",), ("syn_4",), ("syn_5",), ("syn_6",))


def test_the_builder_stops_at_the_first_total_of_twenty(tmp_path: Path) -> None:
    gold_dir = cap_gold(tmp_path, {f"syn_{letter}": 7 for letter in "abcdefghijkl"})
    specs = frozen.build_cap_sessions(load_gold_set(gold_dir))
    assert [spec.source_ids for spec in specs] == [
        ("syn_a", "syn_b", "syn_c"),
        ("syn_d", "syn_e", "syn_f"),
        ("syn_g", "syn_h", "syn_i"),
        ("syn_j", "syn_k", "syn_l"),
    ]
    assert_i5(gold_dir, specs)


def test_the_sessions_are_disjoint_while_the_list_lasts(tmp_path: Path) -> None:
    result = built(tmp_path, {f"syn_{letter}": 10 + index for index, letter in enumerate("abcdefghij")})
    joined = [source_id for session in result for source_id in session]
    assert len(joined) == len(set(joined))
    assert result == (("syn_a", "syn_b"), ("syn_c", "syn_d"), ("syn_e", "syn_f"), ("syn_g", "syn_h"))


def test_the_builder_wraps_to_the_start_of_the_list(tmp_path: Path) -> None:
    """Too few rows for four disjoint sessions: later sessions take conversations from the start of the list again,
    none repeats within a session, and each still reaches the cap only with its last conversation (AMB-76)."""
    gold_dir = cap_gold(tmp_path, {"syn_a": 15, "syn_b": 15, "syn_c": 15})
    specs = frozen.build_cap_sessions(load_gold_set(gold_dir))
    assert [spec.source_ids for spec in specs] == [
        ("syn_a", "syn_b"),
        ("syn_c", "syn_a"),
        ("syn_b", "syn_c"),
        ("syn_a", "syn_b"),
    ]
    assert_i5(gold_dir, specs)
    freeze_inputs(str(gold_dir), prompts=H5_PROMPTS, cap_sessions=specs, probe_items=PROBE_ITEMS, warmup=WARMUP)


def test_a_session_may_take_the_whole_list(tmp_path: Path) -> None:
    gold_dir = cap_gold(tmp_path, {"syn_a": 5, "syn_b": 6, "syn_c": 9, "syn_d": 0})
    specs = frozen.build_cap_sessions(load_gold_set(gold_dir))
    assert [spec.source_ids for spec in specs] == [("syn_a", "syn_b", "syn_c")] * 4
    assert_i5(gold_dir, specs)


@pytest.mark.parametrize(
    ("rows", "count"),
    [({"syn_a": 5, "syn_b": 6}, 11), ({"syn_a": 19}, 19), ({"syn_a": 0, "syn_b": 0}, 0)],
)
def test_a_list_below_twenty_rows_is_refused_with_the_session_and_count(
    tmp_path: Path, rows: dict[str, int], count: int
) -> None:
    gold = load_gold_set(cap_gold(tmp_path, rows))
    assert refusal(frozen.build_cap_sessions, gold) == f"cap sessions: cap_1 reaches only {count} rows"


def test_the_builder_refuses_a_session_id_that_is_a_gold_source_id(tmp_path: Path) -> None:
    gold_dir = cap_gold(tmp_path, {"cap_1": 20, "syn_b": 20, "syn_c": 20, "syn_d": 20})
    message = refusal(frozen.build_cap_sessions, load_gold_set(gold_dir))
    assert "cap_1" in message and "gold source id" in message


# F-18: the builder over 200 seeded random synthetic gold sets.


def test_the_builder_holds_on_random_gold_sets(tmp_path: Path) -> None:
    generator = random.Random(20261115)
    built_count = refused_count = 0
    for number in range(200):
        ids = [f"syn_{value}" for value in generator.sample(range(1, 60), generator.randint(1, 9))]
        rows = {source_id: generator.choice((0, 0, 1, 3, 5, 8, 12, 20, 25)) for source_id in ids}
        other = {source_id: generator.randint(0, 2) for source_id in ids}
        gold_dir = cap_gold(tmp_path / f"set_{number}", rows, other=other)
        gold = load_gold_set(gold_dir)
        if sum(rows.values()) < CAP_SESSION_ROWS:
            assert refusal(frozen.build_cap_sessions, gold).startswith("cap sessions: cap_1 reaches only ")
            refused_count += 1
            continue
        specs = frozen.build_cap_sessions(gold)
        freeze_inputs(str(gold_dir), prompts=H5_PROMPTS, cap_sessions=specs, probe_items=PROBE_ITEMS, warmup=WARMUP)
        assert_i5(gold_dir, specs)
        built_count += 1
    assert built_count > 50 and refused_count > 10


# F-7: the cap-session file.


CAP_ROWS = {"syn_1": 12, "syn_2": 12, "syn_3": 12, "syn_4": 12, "syn_5": 12}
CAP_LIST = [["syn_1", "syn_2"], ["syn_3", "syn_4"], ["syn_5", "syn_1"], ["syn_2", "syn_3"]]


def cap_value(version: str = "v1.0", lists: list[list[str]] | None = None) -> dict[str, Any]:
    return {
        "format": "bakeoff-cap-sessions/1",
        "goldVersion": version,
        "orderRule": "ascending-source-id-greedy-wrap/1",
        "sessions": [
            {"sessionId": f"cap_{number}", "sourceIds": list(source_ids)}
            for number, source_ids in enumerate(CAP_LIST if lists is None else lists, start=1)
        ],
    }


@pytest.fixture(scope="module")
def cap_set(tmp_path_factory: pytest.TempPathFactory) -> GoldSet:
    return load_gold_set(cap_gold(tmp_path_factory.mktemp("capgold"), CAP_ROWS))


def test_cap_sessions_bytes_is_the_canonical_file_form(cap_set: GoldSet) -> None:
    specs = frozen.build_cap_sessions(cap_set)
    assert [list(spec.source_ids) for spec in specs] == CAP_LIST
    assert frozen.cap_sessions_bytes("v1.0", specs) == pretty_json_bytes(cap_value())


def test_parse_cap_sessions_gives_the_specs_back(cap_set: GoldSet) -> None:
    specs = frozen.parse_cap_sessions(pretty_json_bytes(cap_value()), cap_set)
    assert specs == frozen.build_cap_sessions(cap_set)
    assert specs == tuple(CapSessionSpec(f"cap_{n}", tuple(ids)) for n, ids in enumerate(CAP_LIST, start=1))
    crlf = pretty_json_bytes(cap_value()).replace(b"\n", b"\r\n")
    assert frozen.parse_cap_sessions(crlf, cap_set) == specs


def _cap_case(case: str) -> dict[str, Any]:
    value = cap_value()
    sessions = value["sessions"]
    if case == "format":
        value["format"] = f"{CANARY}/1"
    elif case == "extra-key":
        value["note"] = CANARY
    elif case == "missing-key":
        del value["orderRule"]
    elif case == "gold-version":
        value["goldVersion"] = CANARY
    elif case == "order-rule":
        value["orderRule"] = CANARY
    elif case == "one-id":
        sessions[1]["sourceIds"] = ["syn_3", "syn_5"]
    elif case == "order":
        sessions[0]["sourceIds"] = ["syn_2", "syn_1"]
    elif case == "canary-id":
        sessions[2]["sourceIds"] = ["syn_5", CANARY]
    elif case == "session-id":
        sessions[3]["sessionId"] = CANARY
    elif case == "three-sessions":
        del sessions[3]
    elif case == "sessions-not-a-list":
        value["sessions"] = {"cap_1": CANARY}
    elif case == "session-extra-key":
        sessions[0]["rows"] = 24
    elif case == "source-ids-not-a-list":
        sessions[0]["sourceIds"] = CANARY
    elif case == "source-id-not-a-string":
        sessions[0]["sourceIds"] = [1, "syn_2"]
    elif case == "session-not-an-object":
        sessions[0] = CANARY
    return value


CAP_CASES = (
    "format",
    "extra-key",
    "missing-key",
    "gold-version",
    "order-rule",
    "one-id",
    "order",
    "canary-id",
    "session-id",
    "three-sessions",
    "sessions-not-a-list",
    "session-extra-key",
    "source-ids-not-a-list",
    "source-id-not-a-string",
    "session-not-an-object",
)


@pytest.mark.parametrize("case", CAP_CASES)
def test_the_cap_session_file_refuses_each_case_without_content(cap_set: GoldSet, case: str) -> None:
    message = refusal(frozen.parse_cap_sessions, pretty_json_bytes(_cap_case(case)), cap_set)
    assert message.startswith(frozen.CAP_SESSIONS_FILE)
    assert_no_canary(message)


def test_the_cap_session_file_must_match_the_loaded_version(tmp_path: Path) -> None:
    gold = load_gold_set(cap_gold(tmp_path, CAP_ROWS, name="v0.9"))
    message = refusal(frozen.parse_cap_sessions, pretty_json_bytes(cap_value()), gold)
    assert "goldVersion" in message and "'v0.9'" in message
    assert frozen.parse_cap_sessions(pretty_json_bytes(cap_value("v0.9")), gold) == frozen.build_cap_sessions(gold)


def test_a_differing_list_names_the_session(cap_set: GoldSet) -> None:
    message = refusal(frozen.parse_cap_sessions, pretty_json_bytes(_cap_case("order")), cap_set)
    assert "cap_1" in message and frozen.CAP_ORDER_RULE in message


def test_the_cap_session_file_refuses_non_canonical_bytes_and_json_faults(cap_set: GoldSet) -> None:
    indented = json.dumps(cap_value(), sort_keys=True, indent=4).encode("ascii") + b"\n"
    assert "canonical" in refusal(frozen.parse_cap_sessions, indented, cap_set)
    repeated = b'{"format": "a", "format": "b"}\n'
    assert frozen.CAP_SESSIONS_FILE in refusal(frozen.parse_cap_sessions, repeated, cap_set)


# F-19 and AMB-157: the overlap counts.


def overlap_gold(root: Path, texts: list[str]) -> Path:
    """`synthetic_gold`'s four conversations of 20 rows, plus `syn_x` whose segments hold `texts` and no item."""
    conversations, items = gold_rows()
    conversations.append(make_conversation("syn_x", texts))
    return write_version(root, conversations, items, name="v1.0")


def probe_with(*, claim: str | None = None, source: str | None = None) -> tuple[ProbeItem, ...]:
    """The standard probe, with the first item's claim or third source replaced."""
    first = PROBE_ITEMS[0]
    sources = list(first.sources)
    if source is not None:
        sources[2] = ProbeSource("src_3", source)
    return (ProbeItem(first.item_id, first.claim if claim is None else claim, tuple(sources)), *PROBE_ITEMS[1:])


def draft_with(*, claim: str | None = None, source: str | None = None, warmup: str | None = None) -> Any:
    segments = WARMUP.segments
    if warmup is not None:
        segments = (dataclasses.replace(segments[0], text=warmup),)
    files = h5_input_bytes(
        probe_items=probe_with(claim=claim, source=source), warmup=WarmupSpec(WARMUP.item_id, segments)
    )
    return frozen.parse_draft_inputs(files)


def text_of(length: int) -> str:
    """A text of exactly `length` characters, unlike any synthetic input text."""
    return ("Synthetic gold sentence about the quarterly rate, number " * 3)[:length]


def test_overlap_counts_are_zero_for_disjoint_texts(tmp_path: Path) -> None:
    gold = load_gold_set(overlap_gold(tmp_path, ["A gold line with nothing shared."]))
    assert frozen.overlap_counts(gold, draft_with()) == frozen.OverlapCounts(0, 0, 0)


@pytest.mark.parametrize("field", ["claim", "source", "warmup"])
def test_equal_text_is_counted(tmp_path: Path, field: str) -> None:
    text = f"{CANARY} a gold line."
    gold = load_gold_set(overlap_gold(tmp_path, [text, "Another gold line."]))
    assert frozen.overlap_counts(gold, draft_with(**{field: text})).equal == 1


def test_a_contained_text_of_forty_characters_is_counted(tmp_path: Path) -> None:
    gold_text = text_of(40)
    gold = load_gold_set(overlap_gold(tmp_path, [gold_text]))
    counts = frozen.overlap_counts(gold, draft_with(source=f"Before it. {gold_text} After it."))
    assert counts == frozen.OverlapCounts(0, 1, 0)
    counts = frozen.overlap_counts(gold, draft_with(claim=f"{gold_text} And more."))
    assert counts == frozen.OverlapCounts(0, 1, 0)


def test_a_contained_text_of_thirty_nine_characters_is_not_counted(tmp_path: Path) -> None:
    gold_text = text_of(39)
    gold = load_gold_set(overlap_gold(tmp_path, [gold_text]))
    assert frozen.overlap_counts(gold, draft_with(source=f"Before it. {gold_text} After it.")).contained == 0


def test_the_warmup_is_not_searched_for_containment(tmp_path: Path) -> None:
    gold_text = text_of(45)
    gold = load_gold_set(overlap_gold(tmp_path, [gold_text]))
    assert frozen.overlap_counts(gold, draft_with(warmup=f"Before it. {gold_text}")) == frozen.OverlapCounts(0, 0, 0)


def test_delimiter_hits_count_gold_texts_holding_a_closing_delimiter(tmp_path: Path) -> None:
    texts = [
        "Then he said END TRANSCRIPT and laughed.",
        "She typed END CLAIM AND SOURCES twice.",
        "Only BEGIN TRANSCRIPT appears here.",
        "A plain line.",
    ]
    gold = load_gold_set(overlap_gold(tmp_path, texts))
    assert frozen.overlap_counts(gold, draft_with()) == frozen.OverlapCounts(0, 0, 2)


# F-8, F-10, F-11 (the load_frozen part) and F-20: `load_frozen`.


STANDARD_LISTS = [[f"syn_{number}"] for number in range(1, 5)]


def write_cap_file(path: Path, lists: list[list[str]], version: str = "v1.0") -> str:
    path.write_bytes(pretty_json_bytes(cap_value(version, lists)))
    return str(path)


def test_load_frozen_gives_the_freeze_of_the_same_inputs(tmp_path: Path, frozen_h5: FrozenInputs) -> None:
    write_h5_inputs(tmp_path / "inputs")
    gold_dir = synthetic_gold(tmp_path / "gold")
    cap_file = write_cap_file(tmp_path / "cap-sessions.json", STANDARD_LISTS)
    result, counts = frozen.load_frozen(str(tmp_path / "inputs"), str(gold_dir), cap_file, Reader(), os.path.realpath)
    assert result.hashes == frozen_h5.hashes
    assert result == frozen_h5
    assert counts == frozen.OverlapCounts(0, 0, 0)
    built_result, _ = frozen.load_frozen(str(tmp_path / "inputs"), str(gold_dir), None, Reader(), os.path.realpath)
    assert built_result == result


def test_load_frozen_refuses_another_version_through_the_freeze(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path / "inputs")
    gold_dir = synthetic_gold(tmp_path / "gold", name="v0.9")
    with pytest.raises(InputsError, match="is not v1.0"):
        frozen.load_frozen(str(tmp_path / "inputs"), str(gold_dir), None, Reader(), os.path.realpath)


def test_a_renamed_copy_passes_the_freeze_and_fails_check_expected(tmp_path: Path, frozen_h5: FrozenInputs) -> None:
    """AMB-82: a copy of another version under the name v1.0 passes `freeze_inputs`; the gold-manifest comparison with
    the recorded freeze catches it before a run starts."""
    write_h5_inputs(tmp_path / "inputs")
    renamed = tmp_path / "renamed" / "v1.0"
    shutil.copytree(synthetic_gold(tmp_path / "other", name="v0.9", syn_a=12), renamed)
    result, _ = frozen.load_frozen(str(tmp_path / "inputs"), str(renamed), None, Reader(), os.path.realpath)
    expected = parse_expected_freeze(frozen.expected_freeze_bytes(frozen_h5))
    assert "gold manifest" in refusal(frozen.check_expected, result, expected)


def test_load_frozen_resolves_the_gold_dir_once_for_both_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_h5: FrozenInputs
) -> None:
    write_h5_inputs(tmp_path / "inputs")
    target = str(synthetic_gold(tmp_path / "real"))
    link = str(tmp_path / "link" / "v1.0")
    resolved: list[str] = []
    seen: dict[str, list[str]] = {"load_gold_set": [], "freeze_inputs": []}

    def fake_realpath(path: str) -> str:
        resolved.append(path)
        return target if path == link else path

    def recording(name: str) -> Any:
        original = getattr(frozen, name)

        def record(directory: str, *args: Any, **kwargs: Any) -> Any:
            seen[name].append(directory)
            return original(directory, *args, **kwargs)

        return record

    for name in seen:
        monkeypatch.setattr(frozen, name, recording(name))
    result, _ = frozen.load_frozen(str(tmp_path / "inputs"), link, None, Reader(), fake_realpath)
    assert resolved == [link]
    assert seen == {"load_gold_set": [target], "freeze_inputs": [target]}
    assert result == frozen_h5


def test_a_tampered_inputs_manifest_is_refused_before_gold_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = h5_input_bytes()
    manifest = inputs_manifest_bytes(files).decode("ascii")
    files[INPUTS_MANIFEST_NAME] = (("0" if manifest[0] != "0" else "1") + manifest[1:]).encode("ascii")
    write_h5_inputs(tmp_path / "inputs", files, manifest=False)

    def no_gold(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("gold was read")

    monkeypatch.setattr(frozen, "load_gold_set", no_gold)
    monkeypatch.setattr(frozen, "freeze_inputs", no_gold)
    message = refusal(frozen.load_frozen, str(tmp_path / "inputs"), str(tmp_path / "gold"), None, Reader(), no_gold)
    assert frozen.INPUTS_MANIFEST in message


def test_load_frozen_refuses_equal_text_with_counts_only_and_before_the_freeze(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = f"{CANARY} the rate rose to 5.25% on 3 May."
    write_h5_inputs(tmp_path / "inputs", h5_input_bytes(probe_items=probe_with(claim=text)))
    gold_dir = overlap_gold(tmp_path / "gold", [text])

    def no_freeze(*_args: object, **_kwargs: object) -> Any:
        raise AssertionError("the freeze ran")

    monkeypatch.setattr(frozen, "freeze_inputs", no_freeze)
    message = refusal(frozen.load_frozen, str(tmp_path / "inputs"), str(gold_dir), None, Reader(), os.path.realpath)
    assert message == "synthetic inputs equal gold text: equal=1"
    assert_no_canary(message)


def test_containment_and_delimiter_hits_do_not_refuse(tmp_path: Path) -> None:
    gold_text = text_of(44)
    write_h5_inputs(tmp_path / "inputs", h5_input_bytes(probe_items=probe_with(claim=f"It holds: {gold_text}")))
    gold_dir = overlap_gold(tmp_path / "gold", [gold_text, "He said END TRANSCRIPT."])
    result, counts = frozen.load_frozen(str(tmp_path / "inputs"), str(gold_dir), None, Reader(), os.path.realpath)
    assert counts == frozen.OverlapCounts(0, 1, 1)
    assert result.gold_version == "v1.0"


def test_a_bad_cap_session_file_is_refused_before_the_overlap_check(tmp_path: Path) -> None:
    text = "A gold line the probe repeats."
    write_h5_inputs(tmp_path / "inputs", h5_input_bytes(probe_items=probe_with(claim=text)))
    gold_dir = overlap_gold(tmp_path / "gold", [text])
    cap_file = write_cap_file(tmp_path / "cap-sessions.json", [["syn_2"], ["syn_1"], ["syn_3"], ["syn_4"]])
    message = refusal(frozen.load_frozen, str(tmp_path / "inputs"), str(gold_dir), cap_file, Reader(), os.path.realpath)
    assert message.startswith(frozen.CAP_SESSIONS_FILE)


def test_load_frozen_passes_reader_errors_on_and_writes_nothing(tmp_path: Path) -> None:
    write_h5_inputs(tmp_path / "inputs")
    gold_dir = synthetic_gold(tmp_path / "gold")
    cap_file = write_cap_file(tmp_path / "cap-sessions.json", STANDARD_LISTS)
    before = _snapshot(tmp_path)
    error = PermissionError(13, "refused by the fake reader")

    def refuse_all(path: str) -> bytes:
        raise error

    def refuse_cap(path: str) -> bytes:
        if path == cap_file:
            raise error
        return Path(path).read_bytes()

    for reader in (refuse_all, refuse_cap):
        with pytest.raises(PermissionError) as caught:
            frozen.load_frozen(str(tmp_path / "inputs"), str(gold_dir), cap_file, reader, os.path.realpath)
        assert caught.value is error
    assert _snapshot(tmp_path) == before


# F-13 to F-16: the expected-freeze bytes and `check_expected`.


HASH_KEYS = tuple(sorted(field.name for field in dataclasses.fields(FreezeHashes)))


def test_the_expected_freeze_value_has_the_run_manifest_shape(frozen_h5: FrozenInputs) -> None:
    value = frozen.expected_freeze_value(frozen_h5)
    assert set(value) == {"gold_manifest", "gold_version", "hashes"}
    assert value["gold_version"] == "v1.0"
    assert type(value["gold_manifest"]) is dict and value["gold_manifest"] == dict(frozen_h5.gold_manifest)
    hashes = value["hashes"]
    assert isinstance(hashes, dict) and tuple(sorted(hashes)) == HASH_KEYS
    for name in HASH_KEYS:
        stored = getattr(frozen_h5.hashes, name)
        if name.startswith("requests"):
            assert type(hashes[name]) is dict and hashes[name] == dict(stored)
        else:
            assert hashes[name] == stored


def test_the_expected_freeze_bytes_are_ascii_with_one_final_lf(frozen_h5: FrozenInputs) -> None:
    data = frozen.expected_freeze_bytes(frozen_h5)
    assert data.isascii() and data.endswith(b"\n") and data.count(b"\n") == 1
    assert data == record_json(frozen.expected_freeze_value(frozen_h5)) + b"\n"
    assert isinstance(parse_expected_freeze(data), ExpectedFreeze)


def test_the_expected_freeze_bytes_round_trip(frozen_h5: FrozenInputs) -> None:
    value = frozen.expected_freeze_value(frozen_h5)
    parsed = parse_expected_freeze(frozen.expected_freeze_bytes(frozen_h5))
    assert parsed.gold_version == value["gold_version"]
    assert dict(parsed.gold_manifest) == value["gold_manifest"]
    assert dict(parsed.hashes) == value["hashes"]
    frozen.check_expected(frozen_h5, parsed)


@pytest.fixture(scope="module")
def run_view(tmp_path_factory: pytest.TempPathFactory, frozen_h5: FrozenInputs) -> RunView:
    """A synthetic schedule over the H5 inputs, stopped as its first timed launch is about to start."""
    root = tmp_path_factory.mktemp("runs")
    with pytest.MonkeyPatch.context() as patch:
        block_sockets(patch)
        block_processes(patch)
        return schedule_rig(frozen_h5, root, fail=stop_before("qwen3-4b.run1.a1")).run_to_the_stop()


def test_the_run_manifest_stores_the_freeze_hashes_in_the_expected_freeze_shape(
    frozen_h5: FrozenInputs, run_view: RunView
) -> None:
    """Control: the runner already stores the 3.7 shape, built here from `FreezeHashes` without `frozen`."""
    hand_built = {
        name: dict(getattr(frozen_h5.hashes, name)) if name.startswith("requests") else getattr(frozen_h5.hashes, name)
        for name in HASH_KEYS
    }
    assert run_view.manifest["hashes"] == hand_built
    assert run_view.manifest["gold_manifest"] == dict(frozen_h5.gold_manifest)


def test_the_writer_hashes_equal_the_run_manifest_hashes(frozen_h5: FrozenInputs, run_view: RunView) -> None:
    value = frozen.expected_freeze_value(frozen_h5)
    assert value["hashes"] == run_view.manifest["hashes"]
    assert value["gold_manifest"] == run_view.manifest["gold_manifest"]
    assert value["gold_version"] == run_view.manifest["gold_version"]


def expected_with(inputs: FrozenInputs, change: Callable[[dict[str, Any]], object]) -> ExpectedFreeze:
    value = copy.deepcopy(frozen.expected_freeze_value(inputs))
    change(value)
    return parse_expected_freeze(record_json(value) + b"\n")


@pytest.mark.parametrize("key", [name for name in HASH_KEYS if not name.startswith("requests")])
def test_check_expected_refuses_each_changed_hash(frozen_h5: FrozenInputs, key: str) -> None:
    expected = expected_with(frozen_h5, lambda value: value["hashes"].__setitem__(key, "0" * 64))
    message = refusal(frozen.check_expected, frozen_h5, expected)
    assert f"hashes.{key}" in message
    assert all(f"hashes.{other}" not in message for other in HASH_KEYS if other != key)


@pytest.mark.parametrize("key", ["requests", "requests_unconstrained"])
@pytest.mark.parametrize("item_id", ["warmup", "syn_3", "cap_2", "probe_17"])
def test_check_expected_names_the_item_of_a_changed_request_hash(
    frozen_h5: FrozenInputs, key: str, item_id: str
) -> None:
    expected = expected_with(frozen_h5, lambda value: value["hashes"][key].__setitem__(item_id, "0" * 64))
    message = refusal(frozen.check_expected, frozen_h5, expected)
    assert f"hashes.{key} " in message and repr(item_id) in message


def test_check_expected_names_a_missing_or_extra_request_item(frozen_h5: FrozenInputs) -> None:
    expected = expected_with(frozen_h5, lambda value: value["hashes"]["requests"].pop("probe_20"))
    assert "'probe_20'" in refusal(frozen.check_expected, frozen_h5, expected)
    expected = expected_with(frozen_h5, lambda value: value["hashes"]["requests"].__setitem__("zz_extra", "0" * 64))
    assert "'zz_extra'" in refusal(frozen.check_expected, frozen_h5, expected)


def test_check_expected_names_the_first_differing_key_and_item_in_sorted_order(frozen_h5: FrozenInputs) -> None:
    def several(value: dict[str, Any]) -> None:
        value["hashes"]["probe_sha256"] = "0" * 64
        value["hashes"]["warmup_sha256"] = "0" * 64
        value["hashes"]["requests"]["syn_4"] = "0" * 64

    message = refusal(frozen.check_expected, frozen_h5, expected_with(frozen_h5, several))
    assert "hashes.probe_sha256" in message and "warmup" not in message and "syn_4" not in message

    def two_items(value: dict[str, Any]) -> None:
        value["hashes"]["requests"]["syn_4"] = "0" * 64
        value["hashes"]["requests"]["syn_2"] = "0" * 64

    message = refusal(frozen.check_expected, frozen_h5, expected_with(frozen_h5, two_items))
    assert "'syn_2'" in message and "syn_4" not in message


def test_check_expected_refuses_a_changed_gold_manifest(frozen_h5: FrozenInputs) -> None:
    name = sorted(frozen_h5.gold_manifest)[1]
    expected = expected_with(frozen_h5, lambda value: value["gold_manifest"].__setitem__(name, "0" * 64))
    message = refusal(frozen.check_expected, frozen_h5, expected)
    assert "gold manifest" in message and name in message
    expected = expected_with(frozen_h5, lambda value: value["gold_manifest"].pop(name))
    assert "gold manifest" in refusal(frozen.check_expected, frozen_h5, expected)


def test_check_expected_refuses_another_recorded_version(frozen_h5: FrozenInputs) -> None:
    expected = expected_with(frozen_h5, lambda value: value.__setitem__("gold_version", "v0.4"))
    message = refusal(frozen.check_expected, frozen_h5, expected)
    assert "gold_version" in message and "v1.0" in message


def test_check_expected_accepts_mapping_proxies(frozen_h5: FrozenInputs) -> None:
    hashes = frozen.expected_freeze_value(frozen_h5)["hashes"]
    assert isinstance(hashes, dict)
    proxied = {name: MappingProxyType(item) if isinstance(item, dict) else item for name, item in hashes.items()}
    frozen.check_expected(
        frozen_h5,
        ExpectedFreeze("v1.0", MappingProxyType(dict(frozen_h5.gold_manifest)), MappingProxyType(proxied)),
    )
