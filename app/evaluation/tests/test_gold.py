"""Gold-set loading: H1 spec rules G1-G9 (S2 §3.12, §5.1, §5.2, §5.3). Synthetic fixtures only."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import inspect
import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, get_args

import pytest

from app.evaluation.bakeoff.gold import (
    CHECKABILITY_VALUES,
    CONFIDENCE_VALUES,
    CONVERSATION_KEYS,
    DOMAIN_VALUES,
    HIGH_STAKES_DOMAINS,
    IN_SCOPE_DOMAINS,
    ITEM_KEYS,
    MANIFEST_FILES,
    OUT_OF_SCOPE_BUCKETS,
    REASON_KIND_CLASSES,
    SEGMENT_KEYS,
    Checkability,
    ConfidenceInLabel,
    Domain,
    GoldDataError,
    GoldItem,
    GoldReasonKind,
    GoldSetError,
    ManifestError,
    load_gold_set,
)
from app.evaluation.tests._fixtures import (
    NEEDS_DIGIT_LIMIT,
    fixture_f,
    fixture_x,
    jsonl_bytes,
    load_f,
    load_x,
    make_conversation,
    make_item,
    manifest_bytes,
    rewrite,
    write_version,
)

BOM = bytes((0xEF, 0xBB, 0xBF))


def _ids(items: tuple[GoldItem, ...]) -> list[str]:
    return [item.claim_id for item in items]


def _manifest(directory: Path) -> str:
    return (directory / "MANIFEST.sha256").read_text(encoding="ascii")


def _set_manifest(directory: Path, text: str) -> None:
    (directory / "MANIFEST.sha256").write_bytes(text.encode("ascii"))


# ---------------------------------------------------------------- constants


def test_literal_types_match_the_value_tuples() -> None:
    assert get_args(Checkability) == CHECKABILITY_VALUES
    assert get_args(Domain) == DOMAIN_VALUES
    assert get_args(GoldReasonKind) == REASON_KIND_CLASSES
    assert get_args(ConfidenceInLabel) == CONFIDENCE_VALUES
    assert REASON_KIND_CLASSES == ("", "opinion", "prediction", "request", "vague", "outOfScope")
    assert IN_SCOPE_DOMAINS == {"GENERAL", "MARKET_FINANCE", "WEATHER"}
    assert HIGH_STAKES_DOMAINS == {"HEALTH", "LEGAL", "PERSONAL_FINANCE"}
    assert OUT_OF_SCOPE_BUCKETS == (
        "OWN_COMPANY",
        "NEWS",
        "PERSONAL_FINANCE",
        "HEALTH",
        "LEGAL",
        "OTHER",
        "PRIVATE_INDIVIDUAL",
    )
    assert MANIFEST_FILES == ("conversations.jsonl", "items.csv", "items.jsonl")
    assert (len(ITEM_KEYS), len(CONVERSATION_KEYS), len(SEGMENT_KEYS)) == (20, 8, 6)


# ---------------------------------------------------------------- G1 manifest


def test_crlf_files_with_an_lf_manifest_load(tmp_path: Path) -> None:
    crlf_dir = write_version(tmp_path / "crlf", *fixture_f(), crlf=True)
    assert b"\r\n" in (crlf_dir / "items.jsonl").read_bytes()
    assert b"\r\n" in (crlf_dir / "items.csv").read_bytes()
    crlf = load_gold_set(crlf_dir)
    lf = load_f(tmp_path / "lf")
    assert (crlf.conversations, crlf.items, dict(crlf.manifest)) == (lf.conversations, lf.items, dict(lf.manifest))


def test_a_manifest_over_crlf_bytes_is_refused(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f(), crlf=True)
    crlf = {name: (directory / name).read_bytes() for name in MANIFEST_FILES}
    (directory / "MANIFEST.sha256").write_bytes(manifest_bytes(crlf))
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert caught.value.bad_files == MANIFEST_FILES


def test_each_gold_file_is_read_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = write_version(tmp_path, *fixture_f())
    opened: list[str] = []
    original = Path.open

    def counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        opened.append(self.name)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)  # Path.read_bytes and read_text go through Path.open
    load_gold_set(directory)
    assert sorted(opened) == ["MANIFEST.sha256", "conversations.jsonl", "items.csv", "items.jsonl"]


def test_the_parsers_read_the_lf_normalised_bytes(tmp_path: Path) -> None:
    # A line break inside a quoted CSV field is written as CRLF here; only the normalised bytes equal items.jsonl.
    conversations, items = fixture_f()
    text = "minus three in Oslo\nand snowing"
    conversations[0]["segments"][3]["text"] = text
    items[2]["utterance"] = items[2]["span_text"] = text
    crlf_dir = write_version(tmp_path / "crlf", conversations, items, crlf=True)
    assert b"Oslo\r\nand snowing" in (crlf_dir / "items.csv").read_bytes()
    gold = load_gold_set(crlf_dir)
    assert gold.item_by_claim_id("fx_03").utterance == gold.conversation("syn_900").segments[3].text == text
    assert gold.items == load_gold_set(write_version(tmp_path / "lf", conversations, items)).items


def test_one_changed_byte_names_the_file(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    path = directory / "conversations.jsonl"
    path.write_bytes(path.read_bytes().replace(b"Oslo", b"Oslp", 1))
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert caught.value.bad_files == ("conversations.jsonl",)
    assert "conversations.jsonl" in str(caught.value)


def _drop_csv_line(directory: Path, text: str) -> str:
    return "".join(line for line in text.splitlines(keepends=True) if not line.endswith("  items.csv\n"))


def _add_readme(directory: Path, text: str) -> str:
    (directory / "README.md").write_text("synthetic\n", encoding="ascii")
    digest = hashlib.sha256(b"synthetic\n").hexdigest()
    return text + f"{digest}  README.md\n"


def _parent_path(directory: Path, text: str) -> str:
    return text.replace("  items.jsonl", "  ../items.jsonl")


def _listed_twice(directory: Path, text: str) -> str:
    line = next(line for line in text.splitlines(keepends=True) if line.endswith("  items.jsonl\n"))
    return text + line


def _file_missing(directory: Path, text: str) -> str:
    (directory / "items.csv").unlink()
    return text


def _malformed_line(directory: Path, text: str) -> str:
    return text + "this is not a manifest line\n"


def _one_space(directory: Path, text: str) -> str:
    return text.replace("  items.csv", " items.csv")


def _trailing_text(directory: Path, text: str) -> str:
    return text.replace("  items.csv\n", "  items.csv extra\n")


def _trailing_space(directory: Path, text: str) -> str:
    return text.replace("  items.csv\n", "  items.csv \n")


def _short_digest(directory: Path, text: str) -> str:
    first, *rest = text.splitlines(keepends=True)
    digest, name = first.split("  ", 1)
    return "".join([f"{digest[:40]}  {name}", *rest])


@pytest.mark.parametrize(
    ("mutate", "bad_files"),
    [
        (_drop_csv_line, ("items.csv",)),
        (_add_readme, ("README.md",)),
        (_parent_path, ("../items.jsonl", "items.jsonl")),
        (_listed_twice, ("items.jsonl",)),
        (_file_missing, ("items.csv",)),
        (_malformed_line, ("MANIFEST.sha256",)),
        (_one_space, ("MANIFEST.sha256", "items.csv")),
        (_trailing_text, ("items.csv", "items.csv extra")),
        (_trailing_space, ("items.csv", "items.csv ")),
        (_short_digest, ("MANIFEST.sha256", "conversations.jsonl")),
    ],
)
def test_manifest_problems_raise(
    tmp_path: Path, mutate: Callable[[Path, str], str], bad_files: tuple[str, ...]
) -> None:
    directory = write_version(tmp_path, *fixture_f())
    _set_manifest(directory, mutate(directory, _manifest(directory)))
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert caught.value.bad_files == bad_files
    assert isinstance(caught.value, GoldSetError) and isinstance(caught.value, ValueError)


def test_a_manifest_that_is_not_utf8_names_the_manifest(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    path = directory / "MANIFEST.sha256"
    path.write_bytes(path.read_bytes() + bytes([0xFF, 0x0A]))
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert caught.value.bad_files == ("MANIFEST.sha256",)
    assert "MANIFEST.sha256 is not valid UTF-8" in str(caught.value)


def test_a_lone_cr_is_not_a_line_break(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    _set_manifest(directory, _manifest(directory).replace("\n", "\r"))
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert set(MANIFEST_FILES) <= set(caught.value.bad_files)


def test_uppercase_digests_and_the_star_separator_load(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    lines = _manifest(directory).splitlines()
    digest, name = lines[0].split("  ")
    lines[0] = f"{digest.upper()} *{name}"
    _set_manifest(directory, "\n".join(lines) + "\n")
    gold = load_gold_set(directory)
    assert gold.manifest[name] == digest.lower()


def test_crlf_manifest_lines_and_blank_lines_load(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    text = _manifest(directory)
    _set_manifest(directory, "\n  \n" + text.replace("\n", "\r\n") + "\r\n\n")
    assert load_gold_set(directory).version == "v9.9"


def test_every_bad_file_is_named_once_sorted(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    for name in ("items.jsonl", "conversations.jsonl"):
        path = directory / name
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert caught.value.bad_files == ("conversations.jsonl", "items.jsonl")


def test_the_manifest_is_checked_before_any_parsing(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    (directory / "items.jsonl").write_bytes(b"{not json\n")
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert not isinstance(caught.value, GoldDataError)
    assert caught.value.bad_files == ("items.jsonl",)


def test_a_missing_manifest_raises(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    (directory / "MANIFEST.sha256").unlink()
    with pytest.raises(ManifestError) as caught:
        load_gold_set(directory)
    assert caught.value.bad_files == ("MANIFEST.sha256",)


def test_other_files_in_the_directory_are_ignored(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f())
    (directory / "notes.txt").write_text("not part of the set\n", encoding="ascii")
    (directory / "extra").mkdir()
    assert len(load_gold_set(directory).items) == 7


def test_the_manifest_holds_lowercase_digests_of_the_lf_bytes(tmp_path: Path) -> None:
    directory = write_version(tmp_path, *fixture_f(), crlf=True)
    gold = load_gold_set(directory)
    expected = {
        name: hashlib.sha256((directory / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in MANIFEST_FILES
    }
    assert dict(gold.manifest) == expected
    with pytest.raises(TypeError):
        gold.manifest["items.csv"] = "0" * 64  # type: ignore[index]


def test_there_is_no_way_to_skip_the_check() -> None:
    assert list(inspect.signature(load_gold_set).parameters) == ["directory"]


# ---------------------------------------------------------------- G2 file parsing


def _write_x(tmp_path: Path, conversations: list[dict] | None = None, items: list[dict] | None = None) -> Path:
    base_conversations, base_items = fixture_x()
    return write_version(
        tmp_path,
        conversations if conversations is not None else base_conversations,
        items if items is not None else base_items,
    )


def _segment_without_confidence(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    del conversations[0]["segments"][1]["confidence"]
    return _write_x(tmp_path, conversations, items)


def _item_with_extra_key(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    items[0]["notes"] = "a private note"
    return _write_x(tmp_path, conversations, items)


def _json_boolean(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    items[0]["is_compound"] = True
    return _write_x(tmp_path, conversations, items)


def _blank_line_between_items(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    lines = (directory / "items.jsonl").read_bytes().split(b"\n")
    rewrite(directory, "items.jsonl", b"\n".join([lines[0], b"", *lines[1:]]))
    return directory


def _whitespace_line(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    lines = (directory / "conversations.jsonl").read_bytes().split(b"\n")
    rewrite(directory, "conversations.jsonl", b"\n".join([lines[0], b"   ", *lines[1:]]))
    return directory


def _two_trailing_newlines(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    rewrite(directory, "items.jsonl", (directory / "items.jsonl").read_bytes() + b"\n")
    return directory


def _nan_start(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["segments"][0]["start"] = float("nan")
    return _write_x(tmp_path, conversations, items)


def _infinite_end(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["segments"][0]["end"] = float("inf")
    return _write_x(tmp_path, conversations, items)


def _float_overflowing_to_infinity(tmp_path: Path) -> Path:
    # json parses 1e999 as float inf, so this takes the non-finite path, not the overflow one.
    directory = _write_x(tmp_path)
    data = (directory / "conversations.jsonl").read_bytes().replace(b'"start": 0.0', b'"start": 1e999', 1)
    rewrite(directory, "conversations.jsonl", data)
    return directory


def _huge_integer_start(tmp_path: Path) -> Path:
    # A 401-digit JSON integer parses as an int; float() of it raises OverflowError.
    directory = _write_x(tmp_path)
    data = (directory / "conversations.jsonl").read_bytes().replace(b'"start": 0.0', b'"start": 1' + b"0" * 400, 1)
    rewrite(directory, "conversations.jsonl", data)
    return directory


def _repeated_key(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    data = (
        (directory / "items.jsonl")
        .read_bytes()
        .replace(b'{"claim_id": "x_01",', b'{"claim_id": "x_01", "claim_id": "x_01",')
    )
    rewrite(directory, "items.jsonl", data)
    return directory


def _bom_items(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    rewrite(directory, "items.jsonl", BOM + (directory / "items.jsonl").read_bytes())
    return directory


def _bom_csv(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    rewrite(directory, "items.csv", BOM + (directory / "items.csv").read_bytes())
    return directory


def _invalid_utf8(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    rewrite(directory, "conversations.jsonl", (directory / "conversations.jsonl").read_bytes() + b"\xff\n")
    return directory


def _csv_differs(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    csv_rows = [dict(row) for row in items]
    csv_rows[1]["checkability"] = "VAGUE"  # x2 is OPINION in items.jsonl
    return write_version(tmp_path, conversations, items, csv_rows=csv_rows)


def _csv_header_order(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    return write_version(tmp_path, conversations, items, csv_fieldnames=tuple(reversed(ITEM_KEYS)))


def _csv_row_missing(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    return write_version(tmp_path, conversations, items, csv_rows=items[:-1])


def _csv_rows_reordered(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    return write_version(tmp_path, conversations, items, csv_rows=[items[1], items[0], *items[2:]])


def _csv_extra_value(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    lines = (directory / "items.csv").read_bytes().split(b"\n")
    lines[1] += b",surplus"
    rewrite(directory, "items.csv", b"\n".join(lines))
    return directory


def _array_line(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    rewrite(directory, "conversations.jsonl", (directory / "conversations.jsonl").read_bytes() + b"[1, 2]\n")
    return directory


def _invalid_json_line(tmp_path: Path) -> Path:
    directory = _write_x(tmp_path)
    rewrite(directory, "items.jsonl", (directory / "items.jsonl").read_bytes() + b"{oops\n")
    return directory


def _empty_segments(tmp_path: Path) -> Path:
    conversations, _ = fixture_x()
    conversations.append(make_conversation("syn_c", []))
    return _write_x(tmp_path, conversations)


def _bool_start(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["segments"][0]["start"] = True
    return _write_x(tmp_path, conversations, items)


def _string_confidence(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["segments"][0]["confidence"] = "0.9"
    return _write_x(tmp_path, conversations, items)


def _numeric_segment_id(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["segments"][0]["segment_id"] = 1
    return _write_x(tmp_path, conversations, items)


def _short_call_date(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["call_date"] = "2025-3-12"
    return _write_x(tmp_path, conversations, items)


def _impossible_call_date(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["call_date"] = "2025-02-30"
    return _write_x(tmp_path, conversations, items)


def _wide_digit_call_date(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[0]["call_date"] = "".join(chr(0xFF10 + int(c)) for c in "2025") + "-03-12"
    return _write_x(tmp_path, conversations, items)


def _conversation_missing_key(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    del conversations[1]["setting"]
    return _write_x(tmp_path, conversations, items)


def _setting_not_string(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[1]["setting"] = None
    return _write_x(tmp_path, conversations, items)


def _segment_not_object(tmp_path: Path) -> Path:
    conversations, items = fixture_x()
    conversations[1]["segments"][2] = "mm, right"
    return _write_x(tmp_path, conversations, items)


def _empty_conversations_file(tmp_path: Path) -> Path:
    return write_version(tmp_path, [], [])


@pytest.mark.parametrize(
    "build",
    [
        _segment_without_confidence,
        _item_with_extra_key,
        _json_boolean,
        _blank_line_between_items,
        _whitespace_line,
        _two_trailing_newlines,
        _nan_start,
        _infinite_end,
        _float_overflowing_to_infinity,
        _huge_integer_start,
        _repeated_key,
        _bom_items,
        _bom_csv,
        _invalid_utf8,
        _csv_differs,
        _csv_header_order,
        _csv_row_missing,
        _csv_rows_reordered,
        _csv_extra_value,
        _array_line,
        _invalid_json_line,
        _empty_segments,
        _bool_start,
        _string_confidence,
        _numeric_segment_id,
        _short_call_date,
        _impossible_call_date,
        _wide_digit_call_date,
        _conversation_missing_key,
        _setting_not_string,
        _segment_not_object,
        _empty_conversations_file,
    ],
)
def test_file_problems_raise_gold_data_error(tmp_path: Path, build: Callable[[Path], Path]) -> None:
    directory = build(tmp_path)
    with pytest.raises(GoldDataError):
        load_gold_set(directory)


def test_errors_name_the_file_and_line(tmp_path: Path) -> None:
    with pytest.raises(GoldDataError, match=r"items\.jsonl line 2: empty or whitespace-only line"):
        load_gold_set(_blank_line_between_items(tmp_path))


@pytest.mark.parametrize(
    ("build", "message"),
    [(_huge_integer_start, "start is out of range"), (_float_overflowing_to_infinity, "start must be finite")],
)
def test_numbers_a_float_cannot_hold_say_why(tmp_path: Path, build: Callable[[Path], Path], message: str) -> None:
    with pytest.raises(GoldDataError, match=rf"conversations\.jsonl line 1, segment 1: {message}"):
        load_gold_set(build(tmp_path))


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        pytest.param(b"[" * 100_000, "nesting too deep", id="nesting_too_deep"),
        pytest.param(b'{"n": ' + b"1" * 5_000 + b"}", "number too long", id="number_too_long", marks=NEEDS_DIGIT_LIMIT),
    ],
)
def test_json_the_decoder_cannot_build_is_a_data_error(tmp_path: Path, line: bytes, reason: str) -> None:
    directory = _write_x(tmp_path)
    rewrite(directory, "items.jsonl", (directory / "items.jsonl").read_bytes() + line + b"\n")
    with pytest.raises(GoldDataError, match=rf"items\.jsonl line 9: invalid JSON \({reason}\)"):
        load_gold_set(directory)


@pytest.mark.parametrize(
    ("call_date", "message"),
    [
        ("20250312", "is not YYYY-MM-DD"),  # date.fromisoformat accepts the next four on 3.11 and later
        ("2025-W11-3", "is not YYYY-MM-DD"),
        ("2025W113", "is not YYYY-MM-DD"),
        ("2025031200", "is not YYYY-MM-DD"),
        ("2025-03-12T00:00", "is not YYYY-MM-DD"),
        ("2025-3-12", "is not YYYY-MM-DD"),
        ("2025-02-30", "is not a calendar date"),
    ],
)
def test_call_date_must_be_the_extended_yyyy_mm_dd_form(tmp_path: Path, call_date: str, message: str) -> None:
    conversations, items = fixture_x()
    conversations[0]["call_date"] = call_date
    with pytest.raises(GoldDataError, match=f"call_date '{call_date}' {message}"):
        load_gold_set(write_version(tmp_path, conversations, items))


@pytest.mark.parametrize("key", ITEM_KEYS)
def test_items_csv_must_equal_items_jsonl_in_every_field(tmp_path: Path, key: str) -> None:
    conversations, items = fixture_x()
    csv_rows = [dict(row) for row in items]
    csv_rows[0][key] += "-csv"
    expected = rf"items\.csv row 1 differs from items\.jsonl line 1 \(claim_id 'x_01'; fields \['{key}'\]\)"
    with pytest.raises(GoldDataError, match=expected):
        load_gold_set(write_version(tmp_path, conversations, items, csv_rows=csv_rows))


def test_a_differing_csv_row_is_named(tmp_path: Path) -> None:
    with pytest.raises(GoldDataError, match=r"items\.csv row 2 differs from items\.jsonl line 2 .*\['checkability'\]"):
        load_gold_set(_csv_differs(tmp_path))


def test_a_single_trailing_newline_loads_and_so_does_none(tmp_path: Path) -> None:
    directory = _write_x(tmp_path)
    assert (directory / "items.jsonl").read_bytes().endswith(b"}\n")
    assert len(load_gold_set(directory).items) == 8
    for name in ("items.jsonl", "conversations.jsonl"):
        rewrite(directory, name, (directory / name).read_bytes().rstrip(b"\n"))
    assert len(load_gold_set(directory).items) == 8


def test_values_are_typed_and_file_order_is_kept(tmp_path: Path) -> None:
    gold = load_x(tmp_path)
    conversation = gold.conversation("syn_a")
    assert conversation.call_date == date(2025, 3, 12)
    segment = conversation.segments[0]
    assert (segment.start, segment.end, segment.confidence) == (0.0, 2.5, 0.9)
    assert all(isinstance(value, float) for value in (segment.start, segment.end, segment.confidence))
    assert _ids(gold.items) == [f"x_0{n}" for n in range(1, 9)]
    assert [c.source_id for c in gold.conversations] == ["syn_a", "syn_b"]


def test_integer_segment_numbers_become_floats(tmp_path: Path) -> None:
    conversations, items = fixture_x()
    conversations[0]["segments"][0]["start"] = 0
    gold = load_gold_set(write_version(tmp_path, conversations, items))
    start = gold.conversation("syn_a").segments[0].start
    assert start == 0.0 and isinstance(start, float)


def test_jsonl_key_order_does_not_matter(tmp_path: Path) -> None:
    conversations, items = fixture_x()
    directory = write_version(tmp_path, conversations, items)
    reordered = [dict(reversed(list(item.items()))) for item in items]
    rewrite(directory, "items.jsonl", jsonl_bytes(reordered))
    assert len(load_gold_set(directory).items) == 8


# ---------------------------------------------------------------- G3 enumerations


@pytest.mark.parametrize(
    ("index", "key", "value"),
    [
        (0, "private_individual", "Yes"),
        (2, "reason_kind", "OutOfScope"),
        (0, "deliberately_false", "maybe"),
        (0, "checkability", "checkable"),
        (0, "domain", "FINANCE"),
        (0, "confidence_in_label", "medium"),
        (0, "is_claim", "maybe"),
        (0, "in_scope", "true"),
        (0, "time_sensitive", "Y"),
        (0, "is_compound", ""),
        (2, "is_claim", "No"),
    ],
)
def test_enumeration_values_are_exact(tmp_path: Path, index: int, key: str, value: str) -> None:
    conversations, items = fixture_x()
    items[index][key] = value
    with pytest.raises(GoldDataError, match=items[index]["claim_id"]):
        load_gold_set(write_version(tmp_path, conversations, items))


def test_flags_become_bools_and_blank_is_claim_becomes_none(tmp_path: Path) -> None:
    gold = load_x(tmp_path)
    x1, x3 = gold.item_by_claim_id("x_01"), gold.item_by_claim_id("x_03")
    assert (x1.in_scope, x1.is_claim, x1.private_individual, x1.is_compound, x1.time_sensitive) == (
        True,
        True,
        False,
        False,
        False,
    )
    assert (x3.in_scope, x3.is_claim) == (False, None)
    assert gold.item_by_claim_id("x_02").is_claim is False


# ---------------------------------------------------------------- G4 derived fields


def test_an_out_of_scope_opinion_keeps_its_checkability_kind(tmp_path: Path) -> None:
    gold = load_f(tmp_path / "ok")
    pf = gold.item_by_claim_id("fx_07")
    assert (pf.in_scope, pf.is_claim, pf.reason_kind) == (False, None, "opinion")
    conversations, items = fixture_f()
    items[6]["reason_kind"] = "outOfScope"
    with pytest.raises(GoldDataError, match="fx_07"):
        load_gold_set(write_version(tmp_path / "bad", conversations, items))


def _private_row(**overrides: str) -> tuple[list[dict], list[dict]]:
    conversation = make_conversation("syn_p", ["hello", "my neighbour ran a marathon in Leeds last year"])
    row = make_item("p_01", conversation, 2, "CHECKABLE", "GENERAL", private_individual="yes", **overrides)
    return [conversation], [row]


def test_a_private_individual_row_is_out_of_scope(tmp_path: Path) -> None:
    gold = load_gold_set(write_version(tmp_path / "ok", *_private_row()))
    row = gold.item_by_claim_id("p_01")
    assert (row.in_scope, row.reason_kind, row.is_claim) == (False, "outOfScope", None)
    assert row.out_of_scope_bucket == "PRIVATE_INDIVIDUAL"


@pytest.mark.parametrize(
    "overrides",
    [{"in_scope": "yes"}, {"reason_kind": ""}, {"is_claim": "yes"}, {"is_claim": "no"}],
)
def test_private_individual_derivations_are_checked(tmp_path: Path, overrides: dict[str, str]) -> None:
    with pytest.raises(GoldDataError, match="p_01"):
        load_gold_set(write_version(tmp_path, *_private_row(**overrides)))


@pytest.mark.parametrize(
    ("index", "overrides"),
    [
        (4, {"in_scope": "yes"}),  # HE: a HEALTH row marked in scope
        (0, {"is_claim": "no"}),  # MK: in-scope CHECKABLE is a claim
        (0, {"is_claim": ""}),
        (3, {"is_claim": "yes"}),  # OP: in-scope OPINION is not a claim
        (3, {"is_claim": ""}),
        (0, {"reason_kind": "opinion"}),
        (1, {"reason_kind": ""}),  # RQ: a request keeps its kind
        (5, {"reason_kind": ""}),  # OC: out-of-scope CHECKABLE is outOfScope
        (5, {"in_scope": "yes", "reason_kind": "", "is_claim": "yes"}),  # all three consistent, still out of scope
    ],
)
def test_derived_field_mismatches_raise(tmp_path: Path, index: int, overrides: dict[str, str]) -> None:
    conversations, items = fixture_f()
    items[index].update(overrides)
    with pytest.raises(GoldDataError, match=items[index]["claim_id"]):
        load_gold_set(write_version(tmp_path, conversations, items))


# ---------------------------------------------------------------- G5 keys and cross-references


def _two_rows_on_one_segment(conversations: list[dict], items: list[dict]) -> None:
    items.append(make_item("fx_10", conversations[0], 2, "CHECKABLE", "MARKET_FINANCE"))


def _row_on_missing_segment(conversations: list[dict], items: list[dict]) -> None:
    items[0]["segment_id"] = "seg_010"


def _utterance_one_character_off(conversations: list[dict], items: list[dict]) -> None:
    items[0]["utterance"] = items[0]["utterance"] + "."


def _unknown_source(conversations: list[dict], items: list[dict]) -> None:
    items[0]["source_id"] = "syn_999"


def _duplicate_claim_id(conversations: list[dict], items: list[dict]) -> None:
    items[1]["claim_id"] = "fx_01"


def _duplicate_source_id(conversations: list[dict], items: list[dict]) -> None:
    conversations.append(make_conversation("syn_900", ["another call"]))


def _duplicate_segment_id(conversations: list[dict], items: list[dict]) -> None:
    conversations[0]["segments"][8]["segment_id"] = "seg_001"


def _speaker_mismatch(conversations: list[dict], items: list[dict]) -> None:
    items[0]["speaker_label"] = "SPEAKER_00" if items[0]["speaker_label"] == "SPEAKER_01" else "SPEAKER_01"


@pytest.mark.parametrize(
    "mutate",
    [
        _two_rows_on_one_segment,
        _row_on_missing_segment,
        _utterance_one_character_off,
        _unknown_source,
        _duplicate_claim_id,
        _duplicate_source_id,
        _duplicate_segment_id,
        _speaker_mismatch,
    ],
)
def test_key_and_cross_reference_problems_raise(
    tmp_path: Path, mutate: Callable[[list[dict], list[dict]], None]
) -> None:
    conversations, items = fixture_f()
    mutate(conversations, items)
    with pytest.raises(GoldDataError):
        load_gold_set(write_version(tmp_path, conversations, items))


def test_a_repeated_source_id_raises_even_for_an_identical_copy(tmp_path: Path) -> None:
    conversations, items = fixture_x()
    conversations.append(copy.deepcopy(conversations[0]))
    with pytest.raises(GoldDataError, match="source_id 'syn_a' appears on two conversations"):
        load_gold_set(write_version(tmp_path, conversations, items))


def test_item_at_and_the_other_accessors(tmp_path: Path) -> None:
    gold = load_f(tmp_path, plus=True)
    assert gold.item_at("syn_900", "seg_001") is None
    assert gold.item_at("syn_900", "seg_002") == gold.item_by_claim_id("fx_01")
    assert gold.conversation_ids() == ("syn_900", "syn_901")
    assert _ids(gold.items_for("syn_901")) == ["fx_08", "fx_09"]
    assert gold.segment_ids("syn_901") == {"seg_001", "seg_002", "seg_003"}
    assert gold.item_by_claim_id("fx_02").key == ("syn_900", "seg_003")
    for call in (
        lambda: gold.conversation("syn_999"),
        lambda: gold.item_by_claim_id("gs_0001"),
        lambda: gold.items_for("syn_999"),
        lambda: gold.segment_ids("syn_999"),
        lambda: gold.non_item_segment_ids("syn_999"),
        # An id outside the conversation is never a non-item segment (S2 §3.8 against §5.2).
        lambda: gold.item_at("syn_999", "seg_002"),
        lambda: gold.item_at("syn_900", "seg_099"),
        lambda: gold.item_at("syn_900", "syn_900:seg_002"),
    ):
        with pytest.raises(KeyError):
            call()


def test_conversation_ids_are_sorted_whatever_the_file_order(tmp_path: Path) -> None:
    conversations, items = fixture_x()
    gold = load_gold_set(write_version(tmp_path, list(reversed(conversations)), items))
    assert [c.source_id for c in gold.conversations] == ["syn_b", "syn_a"]
    assert gold.conversation_ids() == ("syn_a", "syn_b")


# ---------------------------------------------------------------- G6 corpus identity


@pytest.mark.parametrize(
    ("target", "key", "value"),
    [("conversation", "language", "ur"), ("conversation", "provenance", "real"), ("item", "provenance", "real")],
)
def test_corpus_identity_is_checked(tmp_path: Path, target: str, key: str, value: str) -> None:
    conversations, items = fixture_f()
    (conversations[0] if target == "conversation" else items[0])[key] = value
    with pytest.raises(GoldDataError):
        load_gold_set(write_version(tmp_path, conversations, items))


def test_version_is_the_directory_name(tmp_path: Path) -> None:
    assert load_f(tmp_path, name="v0.9-test").version == "v0.9-test"


# ---------------------------------------------------------------- G7 deliberately_false


def test_deliberately_false_is_validated_then_dropped(tmp_path: Path) -> None:
    gold = load_f(tmp_path)
    item = gold.items[0]
    assert not hasattr(item, "deliberately_false")
    assert "deliberately_false" not in {f.name for f in dataclasses.fields(GoldItem)}
    conversations, items = fixture_f()
    for row in items:
        row["deliberately_false"] = "yes"
    flipped = load_gold_set(write_version(tmp_path / "flipped", conversations, items))
    assert flipped.items == gold.items


# ---------------------------------------------------------------- G9 row groups


def test_fixture_f_row_groups(tmp_path: Path) -> None:
    gold = load_f(tmp_path)
    assert _ids(gold.in_scope_checkable()) == ["fx_01", "fx_03"]
    assert _ids(gold.in_scope_non_checkable()) == ["fx_02", "fx_04"]
    assert _ids(gold.out_of_scope()) == ["fx_05", "fx_06", "fx_07"]
    assert _ids(gold.high_stakes_out_of_scope()) == ["fx_05", "fx_07"]  # PF counts although its kind is opinion
    assert gold.non_item_segment_ids("syn_900") == {"seg_001", "seg_009"}
    assert gold.segment_ids("syn_900") == {f"seg_00{n}" for n in range(1, 10)}
    by_domain = gold.high_stakes_by_domain()
    assert list(by_domain) == ["HEALTH", "LEGAL", "PERSONAL_FINANCE"]
    assert {key: _ids(value) for key, value in by_domain.items()} == {
        "HEALTH": ["fx_05"],
        "LEGAL": [],
        "PERSONAL_FINANCE": ["fx_07"],
    }


def test_fixture_x_row_groups_and_classes(tmp_path: Path) -> None:
    gold = load_x(tmp_path)
    groups = (gold.in_scope_checkable(), gold.in_scope_non_checkable(), gold.out_of_scope())
    assert tuple(len(group) for group in groups) + (len(gold.high_stakes_out_of_scope()),) == (3, 2, 3, 2)
    classes = {kind: sum(1 for item in gold.items if item.reason_kind == kind) for kind in REASON_KIND_CLASSES}
    assert classes == {"": 3, "outOfScope": 2, "opinion": 1, "request": 1, "vague": 1, "prediction": 0}
    assert sum(classes.values()) == len(gold.items)
    assert gold.non_item_segment_ids("syn_a") == {"seg_001"}
    assert gold.non_item_segment_ids("syn_b") == {"seg_003"}


def test_groups_come_from_scope_and_domain_not_from_reason_kind(tmp_path: Path) -> None:
    gold = load_x(tmp_path)
    x5 = gold.item_by_claim_id("x_05")  # REQUEST about the speakers' own company
    assert x5.reason_kind == "request"
    assert x5.is_out_of_scope and not x5.is_in_scope_non_checkable
    assert x5.out_of_scope_bucket == "OWN_COMPANY"
    assert gold.item_by_claim_id("x_01").out_of_scope_bucket is None


def test_records_are_immutable(tmp_path: Path) -> None:
    gold = load_f(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        gold.items[0].domain = "GENERAL"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        gold.version = "v1.0"  # type: ignore[misc]
    assert isinstance(gold.items, tuple) and isinstance(gold.conversations[0].segments, tuple)


def test_fixture_rows_are_synthetic() -> None:
    _, items = fixture_f(plus=True)
    _, x_items = fixture_x()
    assert all(row["claim_id"].startswith(("fx_", "x_")) for row in items + x_items)
    assert not any(json.dumps(row).count("gs_0") for row in items + x_items)
