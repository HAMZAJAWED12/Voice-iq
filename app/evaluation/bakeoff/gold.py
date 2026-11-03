"""Load and verify one gold-set version directory (bake-off harness H1).

Implements, from the signed D6 criteria note S2:

- §3.12, data: the set is checked against its `MANIFEST.sha256` using LF bytes before anything is parsed, and it is
  synthetic and English only. `load_gold_set` has no way to skip the check. Refusing anything but v1.0 for a real
  run is H2's job; the loader accepts any version directory.
- §5.1, keys: items are keyed by (`source_id`, `segment_id`), one item per segment (guide §2).
- §5.2, row groups: in-scope `CHECKABLE` rows; in-scope non-checkable rows; out-of-scope rows; high-stakes rows,
  meaning out-of-scope rows in `HEALTH`, `LEGAL` or `PERSONAL_FINANCE` whatever their checkability. Groups come from
  `in_scope` and `domain` (checkability first, then scope: guide Q1), never from `reason_kind == "outOfScope"`.
- §5.3, `deliberately_false`: validated as yes/no, then dropped. It is construction metadata, not truth, so
  `GoldItem` has no such attribute.

File rules follow the gold README and the labelling guide (§2, §3, §4.1-§4.3, §13): `items.csv` is the canonical
table and `items.jsonl`, generated from it for the harness, must equal it row for row; `in_scope`, `is_claim` and
`reason_kind` are derived fields and must agree with their derivation. Fields that scoring never reads (spans, claim
text, call date, setting, speakers, timings, ASR confidence) are still loaded and validated.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

Checkability = Literal["CHECKABLE", "OPINION", "PREDICTION", "REQUEST", "VAGUE"]
Domain = Literal[
    "GENERAL", "MARKET_FINANCE", "WEATHER", "PERSONAL_FINANCE", "HEALTH", "LEGAL", "NEWS", "OWN_COMPANY", "OTHER"
]
GoldReasonKind = Literal["", "opinion", "prediction", "request", "vague", "outOfScope"]
ConfidenceInLabel = Literal["high", "low"]

CHECKABILITY_VALUES = ("CHECKABLE", "OPINION", "PREDICTION", "REQUEST", "VAGUE")
DOMAIN_VALUES = (
    "GENERAL",
    "MARKET_FINANCE",
    "WEATHER",
    "PERSONAL_FINANCE",
    "HEALTH",
    "LEGAL",
    "NEWS",
    "OWN_COMPANY",
    "OTHER",
)
CONFIDENCE_VALUES = ("high", "low")
IN_SCOPE_DOMAINS = frozenset({"GENERAL", "MARKET_FINANCE", "WEATHER"})
HIGH_STAKES_DOMAINS = frozenset({"HEALTH", "LEGAL", "PERSONAL_FINANCE"})
HIGH_STAKES_ORDER = tuple(sorted(HIGH_STAKES_DOMAINS))  # report order: HEALTH, LEGAL, PERSONAL_FINANCE
OUT_OF_SCOPE_BUCKETS = ("OWN_COMPANY", "NEWS", "PERSONAL_FINANCE", "HEALTH", "LEGAL", "OTHER", "PRIVATE_INDIVIDUAL")
REASON_KIND_CLASSES = ("", "opinion", "prediction", "request", "vague", "outOfScope")  # "" = blank
MANIFEST_NAME = "MANIFEST.sha256"
MANIFEST_FILES = ("conversations.jsonl", "items.csv", "items.jsonl")
ITEM_KEYS = (
    "claim_id",
    "source_id",
    "segment_id",
    "speaker_label",
    "utterance",
    "checkability",
    "domain",
    "private_individual",
    "in_scope",
    "is_claim",
    "span_text",
    "claim_text",
    "is_compound",
    "time_sensitive",
    "deliberately_false",
    "reason_kind",
    "confidence_in_label",
    "provenance",
    "drafted_by",
    "label_status",
)
CONVERSATION_KEYS = (
    "source_id",
    "call_date",
    "setting",
    "language",
    "provenance",
    "drafted_by",
    "guide_version",
    "segments",
)
SEGMENT_KEYS = ("segment_id", "speaker", "start", "end", "text", "confidence")

_UTF8_BOM = bytes((0xEF, 0xBB, 0xBF))
_MANIFEST_LINE = re.compile(r"([0-9A-Fa-f]{64}) [ *](.+)")
_CALL_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_YES_NO = {"yes": True, "no": False}
_IS_CLAIM = {"yes": True, "no": False, "": None}
_STR_ITEM_FIELDS = (
    "claim_id",
    "source_id",
    "segment_id",
    "speaker_label",
    "utterance",
    "span_text",
    "claim_text",
    "provenance",
    "drafted_by",
    "label_status",
)
_BOOL_ITEM_FIELDS = ("private_individual", "in_scope", "is_compound", "time_sensitive")


class GoldSetError(ValueError):
    """The gold-set directory cannot be used."""


class ManifestError(GoldSetError):
    """`MANIFEST.sha256` does not verify the directory (S2 §3.12). `bad_files` names every bad file, sorted."""

    def __init__(self, message: str, bad_files: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.bad_files = bad_files


class GoldDataError(GoldSetError):
    """A gold file is malformed or breaks a rule of the labelling guide."""


@dataclass(frozen=True, slots=True)
class GoldSegment:
    """One speaker turn. `confidence` is the synthetic ASR confidence (guide §4.1)."""

    segment_id: str
    speaker: str
    start: float
    end: float
    text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class GoldConversation:
    source_id: str
    call_date: date
    setting: str
    language: str
    provenance: str
    drafted_by: str
    guide_version: str
    segments: tuple[GoldSegment, ...]


def _yes_no_text(value: bool | None) -> str:
    return "blank" if value is None else ("yes" if value else "no")


def _derived_reason_kind(checkability: str, in_scope: bool) -> str:
    """Guide §4.3: checkability first, then scope (S2 §5.2, guide Q1)."""
    if checkability != "CHECKABLE":
        return checkability.lower()
    return "" if in_scope else "outOfScope"


@dataclass(frozen=True, slots=True)
class GoldItem:
    """One gold row. There is no `deliberately_false` attribute: S2 §5.3 says it is never used."""

    claim_id: str
    source_id: str
    segment_id: str
    speaker_label: str
    utterance: str
    checkability: Checkability
    domain: Domain
    private_individual: bool
    in_scope: bool
    is_claim: bool | None
    span_text: str
    claim_text: str
    is_compound: bool
    time_sensitive: bool
    reason_kind: GoldReasonKind
    confidence_in_label: ConfidenceInLabel
    provenance: str
    drafted_by: str
    label_status: str

    def __post_init__(self) -> None:
        name = f"item {self.claim_id!r}"
        for key in _STR_ITEM_FIELDS:
            if not isinstance(getattr(self, key), str):
                raise GoldDataError(f"{name}: {key} must be a string")
        for key, allowed in (
            ("checkability", CHECKABILITY_VALUES),
            ("domain", DOMAIN_VALUES),
            ("reason_kind", REASON_KIND_CLASSES),
            ("confidence_in_label", CONFIDENCE_VALUES),
        ):
            if getattr(self, key) not in allowed:
                raise GoldDataError(f"{name}: {key} is {getattr(self, key)!r}, not one of {allowed}")
        for key in _BOOL_ITEM_FIELDS:
            if type(getattr(self, key)) is not bool:
                raise GoldDataError(f"{name}: {key} must be a bool")
        if self.is_claim is not None and type(self.is_claim) is not bool:
            raise GoldDataError(f"{name}: is_claim must be a bool or None")
        # Guide §3 step 3: in scope exactly when the domain is in scope and the subject is not a private individual.
        in_scope = self.domain in IN_SCOPE_DOMAINS and not self.private_individual
        if self.in_scope != in_scope:
            raise GoldDataError(
                f"{name}: in_scope is {_yes_no_text(self.in_scope)}, but domain {self.domain} with private_individual "
                f"{_yes_no_text(self.private_individual)} makes it {_yes_no_text(in_scope)} (guide §3 step 3)"
            )
        expected_kind = _derived_reason_kind(self.checkability, in_scope)
        if self.reason_kind != expected_kind:
            raise GoldDataError(
                f"{name}: reason_kind is {self.reason_kind!r}, expected {expected_kind!r} "
                "(guide §4.3: checkability first, then scope)"
            )
        expected_is_claim = (self.checkability == "CHECKABLE") if in_scope else None
        if self.is_claim != expected_is_claim:
            raise GoldDataError(
                f"{name}: is_claim is {_yes_no_text(self.is_claim)}, expected {_yes_no_text(expected_is_claim)} "
                "(guide §4.2: blank on out-of-scope rows)"
            )

    @property
    def key(self) -> tuple[str, str]:
        """S2 §5.1: items are keyed by (`source_id`, `segment_id`)."""
        return (self.source_id, self.segment_id)

    @property
    def is_in_scope_checkable(self) -> bool:
        """Blank reason kind: in scope and `CHECKABLE`. These rows feed recall (S2 §5.2)."""
        return self.reason_kind == ""

    @property
    def is_in_scope_non_checkable(self) -> bool:
        return self.in_scope and self.checkability != "CHECKABLE"

    @property
    def is_out_of_scope(self) -> bool:
        """Out of scope whatever the checkability (S2 §5.2: 45 rows in v0.4, the non-checkable ones included)."""
        return not self.in_scope

    @property
    def is_high_stakes(self) -> bool:
        """Out of scope in `HEALTH`, `LEGAL` or `PERSONAL_FINANCE`, whatever the checkability (S2 §5.2)."""
        return not self.in_scope and self.domain in HIGH_STAKES_DOMAINS

    @property
    def out_of_scope_bucket(self) -> str | None:
        """The leak report's domain bucket. A row out of scope only through `private_individual` (an in-scope domain)
        goes in `PRIVATE_INDIVIDUAL` (AMB-18; guide §12: "not counted in the domain figures")."""
        if self.in_scope:
            return None
        if self.domain in IN_SCOPE_DOMAINS:
            return "PRIVATE_INDIVIDUAL"
        return self.domain


@dataclass(frozen=True, slots=True)
class GoldSet:
    """One verified gold-set version. Immutable; conversations and items keep file order."""

    version: str
    manifest: Mapping[str, str]
    conversations: tuple[GoldConversation, ...]
    items: tuple[GoldItem, ...]
    _conversation_by_id: Mapping[str, GoldConversation] = field(init=False, repr=False, compare=False)
    _segment_ids: Mapping[str, frozenset[str]] = field(init=False, repr=False, compare=False)
    _non_item_segment_ids: Mapping[str, frozenset[str]] = field(init=False, repr=False, compare=False)
    _items_by_source: Mapping[str, tuple[GoldItem, ...]] = field(init=False, repr=False, compare=False)
    _item_by_key: Mapping[tuple[str, str], GoldItem] = field(init=False, repr=False, compare=False)
    _item_by_claim_id: Mapping[str, GoldItem] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest", MappingProxyType(dict(self.manifest)))
        object.__setattr__(self, "conversations", tuple(self.conversations))
        object.__setattr__(self, "items", tuple(self.items))
        if not self.conversations:
            raise GoldDataError("the gold set holds no conversations")
        conversations: dict[str, GoldConversation] = {}
        segments: dict[str, dict[str, GoldSegment]] = {}
        for conversation in self.conversations:
            sid = conversation.source_id
            if sid in conversations:
                raise GoldDataError(f"source_id {sid!r} appears on two conversations")
            # S2 §3.12: synthetic (U10) and English (U2) only.
            if conversation.language != "en":
                raise GoldDataError(f"conversation {sid}: language is {conversation.language!r}, not 'en' (S2 §3.12)")
            if conversation.provenance != "synthetic":
                raise GoldDataError(
                    f"conversation {sid}: provenance is {conversation.provenance!r}, not 'synthetic' (S2 §3.12)"
                )
            by_segment: dict[str, GoldSegment] = {}
            for segment in conversation.segments:
                if segment.segment_id in by_segment:
                    raise GoldDataError(f"conversation {sid}: segment_id {segment.segment_id!r} appears twice")
                by_segment[segment.segment_id] = segment
            conversations[sid] = conversation
            segments[sid] = by_segment
        by_claim: dict[str, GoldItem] = {}
        by_key: dict[tuple[str, str], GoldItem] = {}
        by_source: dict[str, list[GoldItem]] = {sid: [] for sid in conversations}
        for item in self.items:
            name = f"item {item.claim_id!r}"
            if item.claim_id in by_claim:
                raise GoldDataError(f"claim_id {item.claim_id!r} appears twice")
            if item.provenance != "synthetic":
                raise GoldDataError(f"{name}: provenance is {item.provenance!r}, not 'synthetic' (S2 §3.12)")
            if item.source_id not in conversations:
                raise GoldDataError(f"{name}: unknown source_id {item.source_id!r}")
            item_segment = segments[item.source_id].get(item.segment_id)
            if item_segment is None:
                raise GoldDataError(f"{name}: conversation {item.source_id} has no segment {item.segment_id!r}")
            if item.key in by_key:
                raise GoldDataError(
                    f"{name}: {item.source_id} {item.segment_id} already holds item {by_key[item.key].claim_id} "
                    "(one item per segment, guide §2)"
                )
            if item.utterance != item_segment.text:
                raise GoldDataError(f"{name}: utterance differs from the text of {item.source_id} {item.segment_id}")
            if item.speaker_label != item_segment.speaker:
                raise GoldDataError(f"{name}: speaker_label differs from the speaker of {item.segment_id}")
            by_claim[item.claim_id] = item
            by_key[item.key] = item
            by_source[item.source_id].append(item)
        segment_ids = {sid: frozenset(by_segment) for sid, by_segment in segments.items()}
        item_segments = {sid: frozenset(item.segment_id for item in rows) for sid, rows in by_source.items()}
        object.__setattr__(self, "_conversation_by_id", MappingProxyType(conversations))
        object.__setattr__(self, "_segment_ids", MappingProxyType(segment_ids))
        object.__setattr__(
            self,
            "_non_item_segment_ids",
            MappingProxyType({sid: segment_ids[sid] - item_segments[sid] for sid in conversations}),
        )
        object.__setattr__(
            self, "_items_by_source", MappingProxyType({sid: tuple(rows) for sid, rows in by_source.items()})
        )
        object.__setattr__(self, "_item_by_key", MappingProxyType(by_key))
        object.__setattr__(self, "_item_by_claim_id", MappingProxyType(by_claim))

    def conversation(self, source_id: str) -> GoldConversation:
        """KeyError if the conversation is unknown."""
        return self._conversation_by_id[source_id]

    def conversation_ids(self) -> tuple[str, ...]:
        """Every source_id, sorted."""
        return tuple(sorted(self._conversation_by_id))

    def items_for(self, source_id: str) -> tuple[GoldItem, ...]:
        """The conversation's items in file order; KeyError if the conversation is unknown."""
        return self._items_by_source[source_id]

    def item_at(self, source_id: str, segment_id: str) -> GoldItem | None:
        """The item on that segment, or None for a non-item segment. KeyError if the conversation is unknown or has
        no such segment: an id outside the conversation is never a non-item segment (S2 §3.8, §5.2)."""
        if segment_id not in self._segment_ids[source_id]:
            raise KeyError((source_id, segment_id))
        return self._item_by_key.get((source_id, segment_id))

    def item_by_claim_id(self, claim_id: str) -> GoldItem:
        """KeyError if the claim_id is unknown."""
        return self._item_by_claim_id[claim_id]

    def segment_ids(self, source_id: str) -> frozenset[str]:
        """Every segment of the conversation, item and non-item: the request's segment ids (S2 §3.8, §3.11)."""
        return self._segment_ids[source_id]

    def non_item_segment_ids(self, source_id: str) -> frozenset[str]:
        """The conversation's segments that hold no item."""
        return self._non_item_segment_ids[source_id]

    def in_scope_checkable(self) -> tuple[GoldItem, ...]:
        return tuple(item for item in self.items if item.is_in_scope_checkable)

    def in_scope_non_checkable(self) -> tuple[GoldItem, ...]:
        return tuple(item for item in self.items if item.is_in_scope_non_checkable)

    def out_of_scope(self) -> tuple[GoldItem, ...]:
        return tuple(item for item in self.items if item.is_out_of_scope)

    def high_stakes_out_of_scope(self) -> tuple[GoldItem, ...]:
        return tuple(item for item in self.items if item.is_high_stakes)

    def high_stakes_by_domain(self) -> Mapping[str, tuple[GoldItem, ...]]:
        """Always the three keys `HEALTH`, `LEGAL`, `PERSONAL_FINANCE` (S2 §5.2: each domain shown separately)."""
        return MappingProxyType(
            {
                domain: tuple(item for item in self.items if item.is_high_stakes and item.domain == domain)
                for domain in HIGH_STAKES_ORDER
            }
        )


class _JsonRejected(Exception):
    """Raised by the JSON hooks; not a ValueError, so it never mixes with json.JSONDecodeError."""


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonRejected(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise _JsonRejected(f"non-finite number {token}")


def _read_verified(root: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    """S2 §3.12: check every file against the manifest over LF bytes, before any parsing.

    Returns the verified, LF-normalised bytes of each file (each read once) and the lowercase digests.
    """
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ManifestError(f"{MANIFEST_NAME} is missing from {root}", (MANIFEST_NAME,))
    raw = manifest_path.read_bytes().replace(b"\r\n", b"\n")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{MANIFEST_NAME} is not valid UTF-8 ({exc.reason})", (MANIFEST_NAME,)) from None
    problems: dict[str, list[str]] = {}
    listed: dict[str, list[str]] = {}
    for number, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            problems.setdefault(MANIFEST_NAME, []).append(f"line {number} is not '<sha256>  <file name>'")
            continue
        listed.setdefault(match.group(2), []).append(match.group(1).lower())
    for name in listed:
        if name not in MANIFEST_FILES:
            problems.setdefault(name, []).append(f"not one of {MANIFEST_FILES}")
    verified: dict[str, bytes] = {}
    for name in MANIFEST_FILES:
        digests = listed.get(name, [])
        if not digests:
            problems.setdefault(name, []).append("not listed in the manifest")
            continue
        if len(digests) > 1:
            problems.setdefault(name, []).append(f"listed {len(digests)} times")
            continue
        path = root / name
        if not path.is_file():
            problems.setdefault(name, []).append("listed but missing from the directory")
            continue
        data = path.read_bytes().replace(b"\r\n", b"\n")
        if hashlib.sha256(data).hexdigest() != digests[0]:
            problems.setdefault(name, []).append("SHA-256 of the LF bytes does not match the manifest")
            continue
        verified[name] = data
    if problems:
        bad = tuple(sorted(problems))
        detail = "; ".join(f"{name}: {', '.join(problems[name])}" for name in bad)
        raise ManifestError(f"manifest check failed (S2 §3.12): {detail}", bad)
    return verified, {name: listed[name][0] for name in MANIFEST_FILES}


def _decode(data: bytes, name: str) -> str:
    if data.startswith(_UTF8_BOM):
        raise GoldDataError(f"{name}: starts with a byte-order mark")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GoldDataError(f"{name}: not valid UTF-8 ({exc.reason} at byte {exc.start})") from None


def _jsonl_objects(data: bytes, name: str) -> list[tuple[int, dict[str, Any]]]:
    """One JSON object per LF-terminated line. Only the empty element after the final LF may be dropped."""
    lines = _decode(data, name).split("\n")
    if lines[-1] == "":
        lines.pop()
    objects: list[tuple[int, dict[str, Any]]] = []
    for number, line in enumerate(lines, start=1):
        where = f"{name} line {number}"
        if not line.strip():
            raise GoldDataError(f"{where}: empty or whitespace-only line")
        try:
            value = json.loads(line, object_pairs_hook=_object_without_duplicates, parse_constant=_reject_constant)
        except json.JSONDecodeError as exc:  # a ValueError subclass, so it must come before the ValueError clause
            raise GoldDataError(f"{where}: invalid JSON ({exc.msg}, column {exc.colno})") from None
        except _JsonRejected as exc:
            raise GoldDataError(f"{where}: {exc}") from None
        except RecursionError:
            raise GoldDataError(f"{where}: invalid JSON (nesting too deep)") from None
        except ValueError:  # an integer past CPython's integer-string conversion limit (4,300 digits by default)
            raise GoldDataError(f"{where}: invalid JSON (number too long)") from None
        if not isinstance(value, dict):
            raise GoldDataError(f"{where}: not a JSON object")
        objects.append((number, value))
    return objects


def _check_keys(obj: Mapping[str, Any], expected: tuple[str, ...], where: str) -> None:
    missing = [key for key in expected if key not in obj]
    extra = sorted(set(obj) - set(expected))
    if missing or extra:
        raise GoldDataError(f"{where}: missing keys {missing}, unexpected keys {extra}")


def _segment(obj: object, where: str) -> GoldSegment:
    if not isinstance(obj, dict):
        raise GoldDataError(f"{where}: a segment must be a JSON object")
    _check_keys(obj, SEGMENT_KEYS, where)
    for key in ("segment_id", "speaker", "text"):
        if not isinstance(obj[key], str):
            raise GoldDataError(f"{where}: {key} must be a JSON string")
    numbers: dict[str, float] = {}
    for key in ("start", "end", "confidence"):
        value = obj[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise GoldDataError(f"{where}: {key} must be a JSON number")
        try:
            number = float(value)
        except OverflowError:
            raise GoldDataError(f"{where}: {key} is out of range") from None
        if not math.isfinite(number):
            raise GoldDataError(f"{where}: {key} must be finite")
        numbers[key] = number
    return GoldSegment(
        segment_id=obj["segment_id"],
        speaker=obj["speaker"],
        start=numbers["start"],
        end=numbers["end"],
        text=obj["text"],
        confidence=numbers["confidence"],
    )


def _conversation(obj: dict[str, Any], number: int) -> GoldConversation:
    where = f"conversations.jsonl line {number}"
    _check_keys(obj, CONVERSATION_KEYS, where)
    for key in CONVERSATION_KEYS:
        if key != "segments" and not isinstance(obj[key], str):
            raise GoldDataError(f"{where}: {key} must be a JSON string")
    raw_segments = obj["segments"]
    if not isinstance(raw_segments, list) or not raw_segments:
        raise GoldDataError(f"{where}: segments must be a non-empty array")
    segments = tuple(_segment(segment, f"{where}, segment {index}") for index, segment in enumerate(raw_segments, 1))
    call_date = obj["call_date"]
    if _CALL_DATE.fullmatch(call_date) is None:
        raise GoldDataError(f"{where}: call_date {call_date!r} is not YYYY-MM-DD")
    try:
        parsed_date = date.fromisoformat(call_date)
    except ValueError:
        raise GoldDataError(f"{where}: call_date {call_date!r} is not a calendar date") from None
    return GoldConversation(
        source_id=obj["source_id"],
        call_date=parsed_date,
        setting=obj["setting"],
        language=obj["language"],
        provenance=obj["provenance"],
        drafted_by=obj["drafted_by"],
        guide_version=obj["guide_version"],
        segments=segments,
    )


def _item_row(obj: dict[str, Any], number: int) -> dict[str, str]:
    where = f"items.jsonl line {number}"
    _check_keys(obj, ITEM_KEYS, where)
    for key in ITEM_KEYS:
        if not isinstance(obj[key], str):
            raise GoldDataError(f"{where}: {key} must be a JSON string")
    return cast(dict[str, str], obj)


def _check_items_csv(data: bytes, rows: list[tuple[int, dict[str, str]]]) -> None:
    """Guide §4.2: `items.csv` is the canonical table and `items.jsonl` is generated from it, so they must be equal:
    the header is `ITEM_KEYS` in order, and the rows match one for one, in order."""
    reader = csv.DictReader(io.StringIO(_decode(data, "items.csv"), newline=""))
    try:
        header = reader.fieldnames
        csv_rows = list(reader)
    except csv.Error as exc:
        raise GoldDataError(f"items.csv: {exc}") from None
    if header is None or tuple(header) != ITEM_KEYS:
        raise GoldDataError("items.csv: the header must be ITEM_KEYS in order")
    if len(csv_rows) != len(rows):
        raise GoldDataError(f"items.csv has {len(csv_rows)} rows but items.jsonl has {len(rows)}")
    for index, (csv_row, (number, row)) in enumerate(zip(csv_rows, rows, strict=True), start=1):
        if csv_row != row:
            differing = sorted(key for key in ITEM_KEYS if csv_row.get(key) != row.get(key))
            raise GoldDataError(
                f"items.csv row {index} differs from items.jsonl line {number} "
                f"(claim_id {row['claim_id']!r}; fields {differing})"
            )


def _choice(row: Mapping[str, str], key: str, allowed: tuple[str, ...]) -> str:
    value = row[key]
    if value not in allowed:
        raise GoldDataError(f"item {row['claim_id']!r}: {key} is {value!r}, not one of {allowed}")
    return value


def _flag(row: Mapping[str, str], key: str, allowed: Mapping[str, bool | None]) -> bool | None:
    value = row[key]
    if value not in allowed:
        raise GoldDataError(f"item {row['claim_id']!r}: {key} is {value!r}, not one of {tuple(allowed)}")
    return allowed[value]


def _yes_no(row: Mapping[str, str], key: str) -> bool:
    return bool(_flag(row, key, _YES_NO))


def _item(row: Mapping[str, str]) -> GoldItem:
    # S2 §5.3: `deliberately_false` is validated here and then dropped. It is never used.
    _yes_no(row, "deliberately_false")
    return GoldItem(
        claim_id=row["claim_id"],
        source_id=row["source_id"],
        segment_id=row["segment_id"],
        speaker_label=row["speaker_label"],
        utterance=row["utterance"],
        checkability=cast(Checkability, _choice(row, "checkability", CHECKABILITY_VALUES)),
        domain=cast(Domain, _choice(row, "domain", DOMAIN_VALUES)),
        private_individual=_yes_no(row, "private_individual"),
        in_scope=_yes_no(row, "in_scope"),
        is_claim=_flag(row, "is_claim", _IS_CLAIM),
        span_text=row["span_text"],
        claim_text=row["claim_text"],
        is_compound=_yes_no(row, "is_compound"),
        time_sensitive=_yes_no(row, "time_sensitive"),
        reason_kind=cast(GoldReasonKind, _choice(row, "reason_kind", REASON_KIND_CLASSES)),
        confidence_in_label=cast(ConfidenceInLabel, _choice(row, "confidence_in_label", CONFIDENCE_VALUES)),
        provenance=row["provenance"],
        drafted_by=row["drafted_by"],
        label_status=row["label_status"],
    )


def load_gold_set(directory: str | os.PathLike[str]) -> GoldSet:
    """Load one gold-set version directory (S2 §3.12, §5.1-§5.3).

    The manifest is checked first, over LF bytes, and a `ManifestError` names every bad file before anything is
    parsed. The parsers then read exactly the verified bytes. Data problems raise `GoldDataError`. Other files in the
    directory are ignored. `version` is the directory name.
    """
    root = Path(directory)
    data, digests = _read_verified(root)
    conversations = tuple(
        _conversation(obj, number) for number, obj in _jsonl_objects(data["conversations.jsonl"], "conversations.jsonl")
    )
    rows = [(number, _item_row(obj, number)) for number, obj in _jsonl_objects(data["items.jsonl"], "items.jsonl")]
    _check_items_csv(data["items.csv"], rows)
    items = tuple(_item(row) for _, row in rows)
    return GoldSet(version=root.absolute().name, manifest=digests, conversations=conversations, items=items)
