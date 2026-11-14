"""The frozen H5 inputs: file formats, hash checks, the cap-session builder and the expected-freeze bytes.

H3's `freeze_inputs` builds and hashes every request from values. This module reads those values from the committed
files of `app/evaluation/frozen/`, checks their bytes and their hashes, and hands them on. Files are read only through
an injected `read_bytes`, and every refusal is a `FrozenInputError` that names files, keys, ids, line numbers and
counts, never what a file holds.

Implements, from the signed D6 criteria note S2:

- S2 §3.10, prompts: "frozen and hashed before the first run". The five prompt files map one-to-one onto `PromptSet`
  and its `RepairTemplate`, whose constructors check the placeholders. Each file is printable ASCII with LF line ends,
  so no typographic quote or tool escape can move a hash. In each user template the input placeholder stands alone on
  its line, between an opening and a closing delimiter line that mark the untrusted data; `delimiters` returns the two
  closing ones, so gold segment texts holding one can be counted (AMB-157).
- S2 §3.11, input: the per-source count requests keep the freeze's probe request, unchanged, beside a copy with one
  source's text emptied, so a source's length is the difference of two server counts of whole request bodies
  (AMB-148).
- S2 §3.12, data: the gold-free files are checked against `INPUTS.sha256`, in the gold manifest's line format, over LF
  bytes and before any parse, so a CRLF checkout still verifies. That check reads no gold. Gold is loaded from disk
  only in `load_frozen`, through `gold.load_gold_set` and `inputs.freeze_inputs`. The functions that take a `GoldSet`
  read only the set they are given.
- S2 §3.13, runs: the warm-up is one synthetic conversation. `GoldSegment` checks no value, so its times and
  confidences are checked here, and the freeze then checks it against gold.
- S2 §4, the reasoning probe: exactly 20 claims, `probe_01` to `probe_20`, each with exactly 5 sources, `src_1` to
  `src_5`, and non-blank texts. The loader refuses every probe the freeze refuses, so a gold-free load never accepts
  one that the freeze would reject.
- S2 §11 item 6, frozen and hashed: `gold_free_hashes` recomputes the prompt, repair and probe hashes without gold, by
  the freeze's formulas. The warm-up and request hashes need gold and come from `freeze_inputs`, which `load_frozen`
  calls last, on the one resolved gold directory it also loads. The cap-session list is rebuilt from the gold ids and
  row kinds by one order rule (S2 §6; AMB-147), so the committed list is reproducible. A synthetic text equal to a gold
  text refuses the load; containment and delimiter hits are counted for the project owner (AMB-157).
- S2 §11 item 1, the recorded freeze: `expected_freeze_bytes` writes the gold version, the gold manifest and every
  hash in the run manifest's shape, and `check_expected` compares them before a run starts, so a renamed copy of
  another version is caught then as well as at report time (AMB-82).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any

from .client import ChatRequest, RepairTemplate
from .gates import ExpectedFreeze
from .gold import GoldSegment, GoldSet, load_gold_set
from .inputs import (
    CAP_SESSION_COUNT,
    CAP_SESSION_ROWS,
    GOLD_VERSION,
    INPUT_PLACEHOLDER,
    PROBE_ITEM_COUNT,
    CapSessionSpec,
    FrozenInputs,
    InputsError,
    ProbeItem,
    ProbeSource,
    PromptSet,
    WarmupSpec,
    freeze_inputs,
    input_text,
)
from .records import StoreError, record_json, record_loads
from .schemas import canonical_sha256

FORMAT_PROBE = "bakeoff-probe/1"
FORMAT_WARMUP = "bakeoff-warmup/1"
FORMAT_CAP_SESSIONS = "bakeoff-cap-sessions/1"
CAP_ORDER_RULE = "ascending-source-id-greedy-wrap/1"
INPUTS_MANIFEST = "INPUTS.sha256"
CAP_SESSIONS_FILE = "cap-sessions.json"
EXPECTED_FREEZE_FILE = "EXPECTED-FREEZE.json"
PROBE_FILE = "probe.json"
WARMUP_FILE = "warmup.json"
PROMPT_FILES: Mapping[str, str] = MappingProxyType(
    {
        "extraction_system": "prompts/extraction-system.txt",
        "extraction_user": "prompts/extraction-user.txt",
        "probe_system": "prompts/probe-system.txt",
        "probe_user": "prompts/probe-user.txt",
        "repair": "prompts/repair.txt",
    }
)
INPUT_FILES: tuple[str, ...] = tuple(sorted((*PROMPT_FILES.values(), PROBE_FILE, WARMUP_FILE)))
PROBE_SOURCE_COUNT = 5  # probe.MAX_SOURCES_PER_CLAIM (S2 §4); a test pins the two equal

_BOM = b"\xef\xbb\xbf"
# A probe id: it also excludes the re-key separator ":" of `inputs`.
_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
_MANIFEST_LINE = re.compile(r"([0-9A-Fa-f]{64})  (\S+)")
_PROBE_KEYS = ("format", "items")
_PROBE_ITEM_KEYS = ("claim", "itemId", "sources")
_PROBE_SOURCE_KEYS = ("sourceId", "text")
_WARMUP_KEYS = ("format", "itemId", "segments")
_WARMUP_SEGMENT_KEYS = ("confidence", "end", "segmentId", "speaker", "start", "text")
_CAP_KEYS = ("format", "goldVersion", "orderRule", "sessions")
_CAP_SESSION_KEYS = ("sessionId", "sourceIds")
# AMB-157: a gold segment text this long or longer, found inside a probe claim or source, is counted.
CONTAINED_MIN_CHARS = 40


class FrozenInputError(ValueError):
    """A frozen input file breaks its format or its hash. The message names files, keys, ids, line numbers and counts
    only."""


@dataclass(frozen=True, slots=True)
class DraftInputs:
    """The gold-free inputs: the prompt set, the probe and the warm-up, with each file's SHA-256 over its LF bytes."""

    prompts: PromptSet
    probe_items: tuple[ProbeItem, ...]
    warmup: WarmupSpec
    file_sha256: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class OverlapCounts:
    """How the synthetic inputs meet the gold texts (AMB-157), as counts only. `equal` counts probe claims, probe
    sources and warm-up texts equal to a gold segment text, and refuses a load; `contained` counts gold segment texts of
    at least `CONTAINED_MIN_CHARS` characters found inside a probe claim or source; `delimiter_hits` counts gold segment
    texts holding a closing delimiter of a user template. The last two are shown, for the project owner to rule on."""

    equal: int
    contained: int
    delimiter_hits: int


@dataclass(frozen=True, slots=True)
class SourceDelta:
    """One probe source's pair of count requests (AMB-148): `full` is the frozen probe request of its item, unchanged,
    and `emptied` is the same request with this source's text set to ""."""

    item_id: str
    source_id: str
    full: ChatRequest
    emptied: ChatRequest


def _line_of(data: bytes, offset: int) -> int:
    return data[:offset].count(b"\n") + 1


def lf_bytes(raw: bytes) -> bytes:
    """The gold manifest's normalisation: CRLF becomes LF, so a CRLF checkout verifies. A lone CR is kept. Every
    reader of a frozen file compares or hashes these bytes."""
    return raw.replace(b"\r\n", b"\n")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(lf_bytes(raw)).hexdigest()


def text_value(raw: bytes, name: str) -> str:
    """A prompt file's text: its LF bytes as printable ASCII, without the one final LF. Interior newlines are kept; a
    BOM, a CR left after normalisation, trailing spaces or tabs, any other character, a missing final LF and a blank
    last line are refused (S2 §3.10)."""
    if raw.startswith(_BOM):
        raise FrozenInputError(f"{name}: line 1 starts with a byte order mark")
    data = lf_bytes(raw)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FrozenInputError(f"{name}: line {_line_of(data, exc.start)} is not valid UTF-8") from None
    body = text[:-1] if text.endswith("\n") else text
    for number, line in enumerate(body.split("\n"), start=1):
        if "\r" in line:
            raise FrozenInputError(f"{name}: line {number} holds a CR")
        if line != line.rstrip(" \t"):
            raise FrozenInputError(f"{name}: line {number} ends in a space or tab")
        if not all(" " <= char <= "~" for char in line):
            raise FrozenInputError(f"{name}: line {number} holds a character outside printable ASCII")
    if not text.endswith("\n"):
        raise FrozenInputError(f"{name}: line {body.count(chr(10)) + 1} has no final LF")
    if body.endswith("\n"):
        raise FrozenInputError(f"{name}: line {body.count(chr(10)) + 1} is a blank last line")
    return body


def pretty_json(value: object) -> bytes:
    """The canonical bytes of a JSON input file: sorted keys, a two-space indent, ASCII escapes, one final LF."""
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")


def json_value(raw: bytes, name: str) -> object:
    """The value of a JSON input file, parsed by `records.record_loads`, which refuses repeated keys, NaN and any
    non-ASCII byte. The LF bytes must equal `pretty_json` of the value, so one meaning has one file form."""
    data = lf_bytes(raw)
    try:
        value = record_loads(data)
    except StoreError as exc:
        if not data.isascii():
            offset = next(position for position, byte in enumerate(data) if byte > 0x7F)
            raise FrozenInputError(f"{name}: line {_line_of(data, offset)} holds a non-ASCII byte") from None
        raise FrozenInputError(f"{name}: {exc}") from None
    try:
        canonical = pretty_json(value)
    except ValueError:
        raise FrozenInputError(f"{name}: holds a number out of range") from None
    if canonical != data:
        found, wanted = data.split(b"\n"), canonical.split(b"\n")
        line = next((n for n, pair in enumerate(zip(found, wanted, strict=False), start=1) if pair[0] != pair[1]), None)
        where = min(len(found), len(wanted)) + 1 if line is None else line
        raise FrozenInputError(
            f"{name}: not in canonical form (sorted keys, two-space indent, ASCII escapes, one final LF); "
            f"first difference on line {where}"
        )
    return value


def _check_file_set(files: Mapping[str, bytes]) -> None:
    missing = [name for name in INPUT_FILES if name not in files]
    extra = sorted(set(files) - set(INPUT_FILES))
    if missing or extra:
        raise FrozenInputError(f"the input files must be exactly {list(INPUT_FILES)}: missing {missing}, extra {extra}")


def inputs_manifest(files: Mapping[str, bytes]) -> bytes:
    """The bytes of `INPUTS.sha256` over the seven input files: `<sha256>  <path>` per line, sorted by path, each hash
    over the file's LF bytes, with LF line ends and a final LF, as `sha256sum -c` reads it."""
    _check_file_set(files)
    return "".join(f"{_sha256(files[name])}  {name}\n" for name in INPUT_FILES).encode("ascii")


def check_inputs_manifest(raw: bytes, files: Mapping[str, bytes]) -> Mapping[str, str]:
    """Each input file's SHA-256, once `INPUTS.sha256` lists exactly the seven files, once each, in lowercase hex, and
    every listed file is present and matches (S2 §3.12). Every fault is collected and named by file and kind."""
    data = lf_bytes(raw)
    if not data.isascii():
        offset = next(position for position, byte in enumerate(data) if byte > 0x7F)
        raise FrozenInputError(f"{INPUTS_MANIFEST}: line {_line_of(data, offset)} holds a non-ASCII byte")
    lines = data.decode("ascii").split("\n")
    if lines[-1] == "":
        lines.pop()
    problems: dict[str, list[str]] = {}
    if not data.endswith(b"\n"):
        last = data.count(b"\n") + 1
        problems.setdefault(INPUTS_MANIFEST, []).append(f"line {last} has no final LF")
    listed: dict[str, list[str]] = {}
    for number, line in enumerate(lines, start=1):
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            problems.setdefault(INPUTS_MANIFEST, []).append(f"line {number} is not '<sha256>  <path>'")
            continue
        listed.setdefault(match.group(2), []).append(match.group(1))
    for name in sorted(set(listed) | set(files)):
        if name not in INPUT_FILES:
            problems.setdefault(name, []).append("not one of the input files")
    checked: dict[str, str] = {}
    for name in INPUT_FILES:
        digests = listed.get(name, [])
        if not digests:
            problems.setdefault(name, []).append(f"not listed in {INPUTS_MANIFEST}")
        elif len(digests) > 1:
            problems.setdefault(name, []).append(f"listed {len(digests)} times")
        elif digests[0] != digests[0].lower():
            problems.setdefault(name, []).append("listed in uppercase hex")
        elif name not in files:
            problems.setdefault(name, []).append("listed but missing from the input files")
        elif _sha256(files[name]) != digests[0]:
            problems.setdefault(name, []).append(f"SHA-256 of the LF bytes does not match {INPUTS_MANIFEST}")
        else:
            checked[name] = digests[0]
    if problems:
        detail = "; ".join(f"{name}: {', '.join(problems[name])}" for name in sorted(problems))
        raise FrozenInputError(f"{INPUTS_MANIFEST} check failed: {detail}")
    return MappingProxyType(checked)


def _join(directory: str, name: str) -> str:
    return f"{directory.rstrip('/' + chr(92))}/{name}"


def read_input_files(inputs_dir: str, read_bytes: Callable[[str], bytes]) -> Mapping[str, bytes]:
    """The bytes of the seven input files, by relative path, read only through `read_bytes`. Its errors, a missing
    file's included, propagate unchanged."""
    return MappingProxyType({name: read_bytes(_join(inputs_dir, name)) for name in INPUT_FILES})


def _object(value: object, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    """A JSON object with exactly `keys`."""
    if not isinstance(value, dict):
        raise FrozenInputError(f"{where} is not a JSON object")
    missing = sorted(set(keys) - set(value))
    extra = sorted(set(value) - set(keys))
    if missing or extra:
        raise FrozenInputError(f"{where} must hold exactly the keys {list(keys)}: missing {missing}, extra {extra}")
    return value


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list):
        raise FrozenInputError(f"{where} is not a JSON list")
    return value


def _string(value: object, where: str) -> str:
    """A text that holds a non-whitespace character and can go into a request body (no lone surrogate)."""
    if not isinstance(value, str):
        raise FrozenInputError(f"{where} is not a string")
    if not value.strip():
        raise FrozenInputError(f"{where} must contain a non-whitespace character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise FrozenInputError(f"{where} holds a lone surrogate") from None
    return value


def _probe_id(value: object, expected: str, where: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise FrozenInputError(f"{where} does not match {_ID.pattern}")
    if value != expected:
        raise FrozenInputError(f"{where} is not {expected!r}")
    return value


def _parse_probe_source(row: object, item_id: str, number: int) -> ProbeSource:
    where = f"{PROBE_FILE}: {item_id} sources[{number - 1}]"
    source = _object(row, _PROBE_SOURCE_KEYS, where)
    source_id = _probe_id(source["sourceId"], f"src_{number}", f"{where} sourceId")
    return ProbeSource(source_id, _string(source["text"], f"{PROBE_FILE}: {item_id} {source_id} text"))


def _parse_probe_item(row: object, number: int) -> ProbeItem:
    where = f"{PROBE_FILE}: items[{number - 1}]"
    item = _object(row, _PROBE_ITEM_KEYS, where)
    item_id = _probe_id(item["itemId"], f"probe_{number:02d}", f"{where} itemId")
    claim = _string(item["claim"], f"{PROBE_FILE}: {item_id} claim")
    sources = _list(item["sources"], f"{PROBE_FILE}: {item_id} sources")
    if len(sources) != PROBE_SOURCE_COUNT:
        raise FrozenInputError(
            f"{PROBE_FILE}: {item_id} sources holds {len(sources)} entries, not {PROBE_SOURCE_COUNT}"
        )
    return ProbeItem(
        item_id, claim, tuple(_parse_probe_source(source, item_id, n) for n, source in enumerate(sources, 1))
    )


def _parse_probe(value: object) -> tuple[ProbeItem, ...]:
    """S2 §4, as the freeze checks it and stricter: the ids are fixed by position, so none repeats or is empty."""
    probe = _object(value, _PROBE_KEYS, PROBE_FILE)
    if probe["format"] != FORMAT_PROBE:
        raise FrozenInputError(f"{PROBE_FILE}: format is not {FORMAT_PROBE!r}")
    rows = _list(probe["items"], f"{PROBE_FILE}: items")
    if len(rows) != PROBE_ITEM_COUNT:
        raise FrozenInputError(f"{PROBE_FILE}: items holds {len(rows)} entries, not {PROBE_ITEM_COUNT}")
    return tuple(_parse_probe_item(row, number) for number, row in enumerate(rows, start=1))


def _number(value: object, where: str) -> float:
    """A finite JSON number, int or float but never bool, as a float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FrozenInputError(f"{where} is not a JSON number")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        raise FrozenInputError(f"{where} is not finite")
    return number


def _id_string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise FrozenInputError(f"{where} must be a non-empty string")
    return value


def _parse_warmup_segment(row: object, position: int) -> GoldSegment:
    segment = _object(row, _WARMUP_SEGMENT_KEYS, f"{WARMUP_FILE}: segments[{position}]")
    segment_id = _id_string(segment["segmentId"], f"{WARMUP_FILE}: segments[{position}] segmentId")
    where = f"{WARMUP_FILE}: segment {segment_id!r}"
    speaker = _id_string(segment["speaker"], f"{where} speaker")
    text = _string(segment["text"], f"{where} text")
    start = _number(segment["start"], f"{where} start")
    end = _number(segment["end"], f"{where} end")
    confidence = _number(segment["confidence"], f"{where} confidence")
    if not 0 <= start < end:
        raise FrozenInputError(f"{where}: start and end must satisfy 0 <= start < end")
    if not 0 <= confidence <= 1:
        raise FrozenInputError(f"{where}: confidence must lie between 0 and 1")
    return GoldSegment(segment_id, speaker, start, end, text, confidence)


def _parse_warmup(value: object) -> WarmupSpec:
    """S2 §3.13 and §11 item 5. The checks against gold stay in the freeze."""
    warmup = _object(value, _WARMUP_KEYS, WARMUP_FILE)
    if warmup["format"] != FORMAT_WARMUP:
        raise FrozenInputError(f"{WARMUP_FILE}: format is not {FORMAT_WARMUP!r}")
    item_id = _id_string(warmup["itemId"], f"{WARMUP_FILE}: itemId")
    rows = _list(warmup["segments"], f"{WARMUP_FILE}: segments")
    if not rows:
        raise FrozenInputError(f"{WARMUP_FILE}: segments is empty")
    segments = tuple(_parse_warmup_segment(row, position) for position, row in enumerate(rows))
    ids = [segment.segment_id for segment in segments]
    repeated = sorted({segment_id for segment_id in ids if ids.count(segment_id) > 1})
    if repeated:
        raise FrozenInputError(f"{WARMUP_FILE}: segment id {repeated[0]!r} is used twice")
    return WarmupSpec(item_id, segments)


def _closing_delimiter(template: str, name: str) -> str:
    """The H5 rule for a user template (AMB-157): the placeholder alone on its line, between two non-blank delimiter
    lines that differ."""
    lines = template.split("\n")
    at = [number for number, line in enumerate(lines) if line == INPUT_PLACEHOLDER]
    if len(at) != 1:
        raise FrozenInputError(f"{name}: {INPUT_PLACEHOLDER} must stand alone on exactly one line")
    index = at[0]
    if index == 0 or index == len(lines) - 1:
        raise FrozenInputError(f"{name}: {INPUT_PLACEHOLDER} on line {index + 1} needs a delimiter line on each side")
    opening, closing = lines[index - 1], lines[index + 1]
    if not opening.strip():
        raise FrozenInputError(f"{name}: the opening delimiter on line {index} is blank")
    if not closing.strip():
        raise FrozenInputError(f"{name}: the closing delimiter on line {index + 2} is blank")
    if opening == closing:
        raise FrozenInputError(f"{name}: the delimiters on lines {index} and {index + 2} are equal")
    return closing


def delimiters(prompts: PromptSet) -> tuple[str, str]:
    """The closing delimiters of the extraction and the probe user templates, in that order (AMB-157)."""
    return (
        _closing_delimiter(prompts.extraction_user, PROMPT_FILES["extraction_user"]),
        _closing_delimiter(prompts.probe_user, PROMPT_FILES["probe_user"]),
    )


def parse_draft_inputs(files: Mapping[str, bytes]) -> DraftInputs:
    """The gold-free inputs from the bytes of the seven files, with no manifest check. The placeholder refusals of
    `PromptSet` and `RepairTemplate` come back as `FrozenInputError`, with their own message."""
    _check_file_set(files)
    texts = {field: text_value(files[name], name) for field, name in PROMPT_FILES.items()}
    repair_file = PROMPT_FILES["repair"]
    try:
        repair = RepairTemplate(texts["repair"])
    except ValueError as exc:
        raise FrozenInputError(f"{repair_file}: {exc}") from None
    try:
        prompts = PromptSet(
            texts["extraction_system"], texts["extraction_user"], texts["probe_system"], texts["probe_user"], repair
        )
    except InputsError as exc:
        raise FrozenInputError(f"prompts: {exc}") from None
    delimiters(prompts)
    probe_items = _parse_probe(json_value(files[PROBE_FILE], PROBE_FILE))
    warmup = _parse_warmup(json_value(files[WARMUP_FILE], WARMUP_FILE))
    return DraftInputs(
        prompts, probe_items, warmup, MappingProxyType({name: _sha256(files[name]) for name in INPUT_FILES})
    )


def load_draft_inputs(inputs_dir: str, read_bytes: Callable[[str], bytes]) -> DraftInputs:
    """The gold-free inputs of a frozen directory: `INPUTS.sha256` is read first and checked before any file is
    parsed. Reader errors propagate unchanged; nothing is written."""
    manifest = read_bytes(_join(inputs_dir, INPUTS_MANIFEST))
    files = read_input_files(inputs_dir, read_bytes)
    check_inputs_manifest(manifest, files)
    return parse_draft_inputs(files)


def gold_free_hashes(draft: DraftInputs) -> Mapping[str, str]:
    """`prompts_sha256`, `repair_sha256` and `probe_sha256`, by the freeze's formulas (S2 §11 item 6), without gold."""
    probe = [
        {
            "itemId": item.item_id,
            "claim": item.claim,
            "sources": [{"sourceId": source.source_id, "text": source.text} for source in item.sources],
        }
        for item in draft.probe_items
    ]
    return MappingProxyType(
        {
            "prompts_sha256": draft.prompts.sha256(),
            "repair_sha256": draft.prompts.repair.sha256(),
            "probe_sha256": canonical_sha256(probe),
        }
    )


def _probe_request(prompts: PromptSet, claim: str, sources: list[tuple[str, str]]) -> ChatRequest:
    """A probe request in the freeze's form (S2 §3.11): the claim and its sources, as `input_text` writes them."""
    value = {"claim": claim, "sources": [{"sourceId": source_id, "text": text} for source_id, text in sources]}
    return ChatRequest("reasoning_probe", prompts.probe_messages(input_text(value)))


def source_delta_requests(draft: DraftInputs) -> tuple[SourceDelta, ...]:
    """One `SourceDelta` per probe source, item by item and source by source (AMB-148). A source's tokens are the
    count of `full` minus the count of `emptied`; only counts are asked of a server, never a generation."""
    deltas: list[SourceDelta] = []
    for item in draft.probe_items:
        sources = [(source.source_id, source.text) for source in item.sources]
        full = _probe_request(draft.prompts, item.claim, sources)
        for position, (source_id, _) in enumerate(sources):
            emptied = [(other, "" if index == position else text) for index, (other, text) in enumerate(sources)]
            deltas.append(
                SourceDelta(item.item_id, source_id, full, _probe_request(draft.prompts, item.claim, emptied))
            )
    return tuple(deltas)


def cap_session_rows(gold: GoldSet, source_ids: tuple[str, ...]) -> int:
    """The in-scope CHECKABLE rows of the conversations `source_ids` (S2 §6). It reads row kinds only, never a text."""
    return sum(item.is_in_scope_checkable for source_id in source_ids for item in gold.items_for(source_id))


def build_cap_sessions(gold: GoldSet) -> tuple[CapSessionSpec, ...]:
    """The cap-session list under `CAP_ORDER_RULE` (S2 §6; AMB-147), from the gold ids and row kinds alone.

    The conversations are taken in code-point order of their ids, and those with no in-scope CHECKABLE row are dropped.
    One cursor runs over what is left: each session `cap_1` to `cap_4` takes conversations from it until it holds at
    least `CAP_SESSION_ROWS` rows, so it is short of the cap without its last conversation. Past the end the cursor
    wraps to the start, and a conversation already in the session is skipped, so sessions are disjoint while the list
    lasts and none repeats within a session (AMB-76). A list with fewer rows in all than the cap is refused.
    """
    known = gold.conversation_ids()
    names = [f"cap_{number}" for number in range(1, CAP_SESSION_COUNT + 1)]
    for session_id in names:
        if session_id in known:
            raise FrozenInputError(f"cap sessions: {session_id} is a gold source id (AMB-79)")
    listed = [(source_id, cap_session_rows(gold, (source_id,))) for source_id in sorted(known)]
    usable = [(source_id, rows) for source_id, rows in listed if rows > 0]
    cursor = 0
    specs: list[CapSessionSpec] = []
    for session_id in names:
        taken: list[str] = []
        total = 0
        while total < CAP_SESSION_ROWS:
            if len(taken) == len(usable):
                raise FrozenInputError(f"cap sessions: {session_id} reaches only {total} rows")
            source_id, rows = usable[cursor]
            cursor = (cursor + 1) % len(usable)
            if source_id in taken:
                continue
            taken.append(source_id)
            total += rows
        specs.append(CapSessionSpec(session_id, tuple(taken)))
    return tuple(specs)


def cap_sessions_bytes(gold_version: str, specs: tuple[CapSessionSpec, ...]) -> bytes:
    """The canonical bytes of `cap-sessions.json`: the format tag, the gold version, the order rule and the sessions,
    each with its conversations in join order."""
    sessions = [{"sessionId": spec.session_id, "sourceIds": list(spec.source_ids)} for spec in specs]
    return pretty_json(
        {"format": FORMAT_CAP_SESSIONS, "goldVersion": gold_version, "orderRule": CAP_ORDER_RULE, "sessions": sessions}
    )


def _parse_cap_session(row: object, position: int) -> CapSessionSpec:
    where = f"{CAP_SESSIONS_FILE}: sessions[{position}]"
    session = _object(row, _CAP_SESSION_KEYS, where)
    if not isinstance(session["sessionId"], str):
        raise FrozenInputError(f"{where} sessionId is not a string")
    source_ids = _list(session["sourceIds"], f"{where} sourceIds")
    if not all(isinstance(source_id, str) for source_id in source_ids):
        raise FrozenInputError(f"{where} sourceIds holds a value that is not a string")
    return CapSessionSpec(session["sessionId"], tuple(source_ids))


def parse_cap_sessions(raw: bytes, gold: GoldSet) -> tuple[CapSessionSpec, ...]:
    """The committed cap-session list, once its bytes are canonical, its gold version is the loaded set's, its order
    rule is `CAP_ORDER_RULE` and it equals `build_cap_sessions(gold)` exactly, so it is reproducible from the gold set
    (S2 §6, §11 item 6; AMB-147). The freeze then checks it again. Refusals name no value from the file."""
    name = CAP_SESSIONS_FILE
    value = _object(json_value(raw, name), _CAP_KEYS, name)
    if value["format"] != FORMAT_CAP_SESSIONS:
        raise FrozenInputError(f"{name}: format is not {FORMAT_CAP_SESSIONS!r}")
    if value["goldVersion"] != gold.version:
        raise FrozenInputError(f"{name}: goldVersion is not the loaded gold set's version {gold.version!r}")
    if value["orderRule"] != CAP_ORDER_RULE:
        raise FrozenInputError(f"{name}: orderRule is not {CAP_ORDER_RULE!r}")
    rows = _list(value["sessions"], f"{name}: sessions")
    if len(rows) != CAP_SESSION_COUNT:
        raise FrozenInputError(f"{name}: sessions holds {len(rows)} entries, not {CAP_SESSION_COUNT}")
    specs = tuple(_parse_cap_session(row, position) for position, row in enumerate(rows))
    rebuilt = build_cap_sessions(gold)
    for position, (spec, expected) in enumerate(zip(specs, rebuilt, strict=True)):
        if spec != expected:
            raise FrozenInputError(
                f"{name}: sessions[{position}] differs from {expected.session_id} as {CAP_ORDER_RULE} builds it"
            )
    return rebuilt


def overlap_counts(gold: GoldSet, draft: DraftInputs) -> OverlapCounts:
    """How the synthetic inputs meet the gold segment texts (AMB-157), as counts only. Each synthetic text equal to a
    gold text counts once in `equal`; each gold text of at least `CONTAINED_MIN_CHARS` characters found inside a probe
    claim or source counts once in `contained`; each gold text holding either closing delimiter counts once in
    `delimiter_hits`."""
    gold_texts = [segment.text for conversation in gold.conversations for segment in conversation.segments]
    known = set(gold_texts)
    probe_texts = [text for item in draft.probe_items for text in (item.claim, *(row.text for row in item.sources))]
    synthetic = [*probe_texts, *(segment.text for segment in draft.warmup.segments)]
    closing = delimiters(draft.prompts)
    return OverlapCounts(
        equal=sum(text in known for text in synthetic),
        contained=sum(
            len(text) >= CONTAINED_MIN_CHARS and any(text in probe for probe in probe_texts) for text in gold_texts
        ),
        delimiter_hits=sum(any(delimiter in text for delimiter in closing) for text in gold_texts),
    )


def load_frozen(
    inputs_dir: str,
    gold_dir: str,
    cap_sessions_path: str | None,
    read_bytes: Callable[[str], bytes],
    realpath: Callable[[str], str],
) -> tuple[FrozenInputs, OverlapCounts]:
    """The frozen inputs of a run, with their overlap counts (S2 §3.12, §11 item 6). Nothing is written.

    The gold-free files are checked against `INPUTS.sha256` before any parse, and before gold is touched. The gold
    directory is resolved once, and that one path goes to both gold loads, so they cannot see different directories
    through a link. The cap-session list is read and checked from `cap_sessions_path`, or, with None, built in memory.
    A synthetic text equal to a gold text refuses the load with counts only (AMB-157); containment and delimiter hits
    are returned for display. The freeze itself comes last, and keeps its own checks and refusals.
    """
    draft = load_draft_inputs(inputs_dir, read_bytes)
    resolved = realpath(gold_dir)
    gold = load_gold_set(resolved)
    if cap_sessions_path is None:
        cap_sessions = build_cap_sessions(gold)
    else:
        cap_sessions = parse_cap_sessions(read_bytes(cap_sessions_path), gold)
    counts = overlap_counts(gold, draft)
    if counts.equal > 0:
        raise FrozenInputError(f"synthetic inputs equal gold text: equal={counts.equal}")
    frozen = freeze_inputs(
        resolved,
        prompts=draft.prompts,
        cap_sessions=cap_sessions,
        probe_items=draft.probe_items,
        warmup=draft.warmup,
    )
    return frozen, counts


def _freeze_hashes(inputs: FrozenInputs) -> dict[str, object]:
    """Every freeze hash by field name, request tables as plain dicts, as the run manifest stores them."""
    hashes = {item.name: getattr(inputs.hashes, item.name) for item in fields(inputs.hashes)}
    return {name: dict(value) if isinstance(value, Mapping) else value for name, value in hashes.items()}


def expected_freeze_value(inputs: FrozenInputs) -> dict[str, object]:
    """The value of `EXPECTED-FREEZE.json`: the gold version, the gold manifest and every freeze hash, in the shape the
    run manifest stores them, with plain dicts in place of read-only mappings (S2 §3.12, §11 item 1)."""
    return {
        "gold_manifest": dict(inputs.gold_manifest),
        "gold_version": inputs.gold_version,
        "hashes": _freeze_hashes(inputs),
    }


def expected_freeze_bytes(inputs: FrozenInputs) -> bytes:
    """The bytes of `EXPECTED-FREEZE.json`: the stored-record form of `expected_freeze_value` and one LF."""
    return record_json(expected_freeze_value(inputs)) + b"\n"


def _plain(value: Mapping[str, Any]) -> dict[str, Any]:
    """A recorded mapping as a dict, its mapping values as dicts too, so it compares with a loaded one."""
    return {key: dict(item) if isinstance(item, Mapping) else item for key, item in value.items()}


def _first_difference(recorded: Mapping[str, Any], actual: Mapping[str, Any]) -> str:
    """The first key, in sorted order, that one side lacks or that the two sides hold differently."""
    return next(
        key
        for key in sorted(set(recorded) | set(actual))
        if key not in recorded or key not in actual or recorded[key] != actual[key]
    )


def check_expected(inputs: FrozenInputs, expected: ExpectedFreeze) -> None:
    """The comparisons the report makes with the recorded freeze, made before a run starts (S2 §3.12, §11 item 1): the
    recorded gold version is v1.0, and the gold manifest and every hash are equal. A renamed copy of another version
    passes the freeze (AMB-82) and fails here, on the gold manifest. A refusal names the first differing key in sorted
    order and, for a request table, the first differing item id."""
    if expected.gold_version != GOLD_VERSION:
        raise FrozenInputError(f"the recorded freeze's gold_version is not {GOLD_VERSION}")
    recorded_manifest, actual_manifest = _plain(expected.gold_manifest), dict(inputs.gold_manifest)
    if recorded_manifest != actual_manifest:
        where = _first_difference(recorded_manifest, actual_manifest)
        raise FrozenInputError(f"gold manifest differs from the recorded freeze at {where}")
    recorded, actual = _plain(expected.hashes), _freeze_hashes(inputs)
    if recorded != actual:
        key = _first_difference(recorded, actual)
        message = f"the recorded freeze differs in hashes.{key}"
        left, right = recorded.get(key), actual.get(key)
        if key.startswith("requests") and isinstance(left, Mapping) and isinstance(right, Mapping):
            message += f" at item {_first_difference(left, right)!r}"
        raise FrozenInputError(message)
