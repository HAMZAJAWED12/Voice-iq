"""Parse one model extraction output (bake-off harness H1).

Implements:

- S2 §3.8, structured output: "Pydantic validation of the §2.2 extraction shape follows" the constrained schema, and
  an output is invalid when it fails validation, including an empty `segmentIds` list, `segmentIds` that are not in
  the request, and output cut off at the token cap (`finish_reason = length`). Any violation makes the whole output
  invalid; nothing is salvaged. The error text is deterministic, because the repair request carries it.
- S2 §3.10 and PHASE-0 §2.2, the output shape: `claims[]` with `segmentIds` (checkable, in-scope claims, so any
  `checkability.isCheckable` is `true`) and `nonCheckable[]` with `reasonKind` (`opinion | prediction | request |
  vague | outOfScope`). A `nonCheckable[]` entry has no `verdict`, `confidence` or `citations` field at all.
- S2 §5.3, failures: a request that fails after its retry is an `ExtractionFailure` (invalid output, cut off, a
  timeout or a server error) and scores as empty output.

The key sets below are H1's reference: H2's constrained schema must have exactly these keys, and
`schemas.EXTRACTION_SCHEMA_SHA256` pins its bytes (spec AMB-19). H3's `inputs` hashes H5's prompts and each frozen
request, schema included. This module imports nothing from the rest of the package.
"""

from __future__ import annotations

import json
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError, field_validator

ReasonKind = Literal["opinion", "prediction", "request", "vague", "outOfScope"]
FailureKind = Literal["invalid_output", "cut_off", "timeout", "server_error"]
REASON_KINDS = ("opinion", "prediction", "request", "vague", "outOfScope")
FAILURE_KINDS = ("invalid_output", "cut_off", "timeout", "server_error")
INVALID_KINDS = ("invalid_output", "cut_off")
CUT_OFF_TEXT = "output was cut off at the token cap (finish_reason=length)"

# Wire key sets (camelCase only). The validator below is built to exactly these keys.
TOP_LEVEL_KEYS = frozenset({"claims", "nonCheckable"})
CLAIM_KEYS = frozenset({"text", "segmentIds", "claimId", "speakerId", "checkability"})
CHECKABILITY_KEYS = frozenset({"isCheckable", "reason"})
NON_CHECKABLE_KEYS = frozenset({"segmentIds", "reasonKind", "text", "claimId", "speakerId", "reason"})


class ExtractionInvalid(ValueError):
    """The output failed validation (S2 §3.8). `str(exc) == exc.error_text`."""

    def __init__(self, kind: Literal["invalid_output", "cut_off"], error_text: str) -> None:
        if kind not in INVALID_KINDS:
            raise ValueError(f"ExtractionInvalid kind must be one of {INVALID_KINDS}, not {kind!r}")
        super().__init__(error_text)
        self.kind = kind
        self.error_text = error_text

    def __reduce__(self) -> tuple[Any, ...]:
        # pickle and copy rebuild an exception from `args`, which holds the error text only; rebuild from both fields.
        return (type(self), (self.kind, self.error_text), self.__dict__)


def _check_index(owner: str, index: object) -> None:
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError(f"{owner}.index must be an int >= 0")


def _check_segment_ids(owner: str, segment_ids: object) -> None:
    if not isinstance(segment_ids, tuple) or not segment_ids:
        raise ValueError(f"{owner}.segment_ids must be a non-empty tuple")
    if not all(isinstance(segment_id, str) for segment_id in segment_ids):
        raise ValueError(f"{owner}.segment_ids must hold strings")
    if len(set(segment_ids)) != len(segment_ids):
        raise ValueError(f"{owner}.segment_ids must be unique")


def _check_optional_str(owner: str, name: str, value: object) -> None:
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{owner}.{name} must be a string or None")


@dataclass(frozen=True, slots=True)
class ExtractedClaim:
    """One `claims[]` entry. `index` is its position in the array; claims[] order decides duplicates (S2 §5.1).
    Scoring never reads `text`, `claim_id` or `speaker_id`: spans and claim text are not compared (S2 §5.1)."""

    index: int
    text: str
    segment_ids: tuple[str, ...]
    claim_id: str | None = None
    speaker_id: str | None = None

    def __post_init__(self) -> None:
        _check_index("ExtractedClaim", self.index)
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("ExtractedClaim.text must contain a non-whitespace character")
        _check_segment_ids("ExtractedClaim", self.segment_ids)
        _check_optional_str("ExtractedClaim", "claim_id", self.claim_id)
        _check_optional_str("ExtractedClaim", "speaker_id", self.speaker_id)


@dataclass(frozen=True, slots=True)
class NonCheckableEntry:
    """One `nonCheckable[]` entry (PHASE-0 §2.2)."""

    index: int
    segment_ids: tuple[str, ...]
    reason_kind: ReasonKind
    text: str | None = None
    claim_id: str | None = None
    speaker_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        _check_index("NonCheckableEntry", self.index)
        _check_segment_ids("NonCheckableEntry", self.segment_ids)
        if self.reason_kind not in REASON_KINDS:
            raise ValueError(f"NonCheckableEntry.reason_kind must be one of {REASON_KINDS}, not {self.reason_kind!r}")
        for name in ("text", "claim_id", "speaker_id", "reason"):
            _check_optional_str("NonCheckableEntry", name, getattr(self, name))


@dataclass(frozen=True, slots=True)
class Extraction:
    """A valid extraction output for one conversation."""

    source_id: str
    claims: tuple[ExtractedClaim, ...]
    non_checkable: tuple[NonCheckableEntry, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("Extraction.source_id must be a non-empty string")
        if not isinstance(self.claims, tuple) or not all(isinstance(c, ExtractedClaim) for c in self.claims):
            raise ValueError("Extraction.claims must be a tuple of ExtractedClaim")
        if not isinstance(self.non_checkable, tuple) or not all(
            isinstance(e, NonCheckableEntry) for e in self.non_checkable
        ):
            raise ValueError("Extraction.non_checkable must be a tuple of NonCheckableEntry")
        for position, claim in enumerate(self.claims):
            if claim.index != position:
                raise ValueError(f"Extraction.claims[{position}] has index {claim.index}")
        for position, entry in enumerate(self.non_checkable):
            if entry.index != position:
                raise ValueError(f"Extraction.non_checkable[{position}] has index {entry.index}")


@dataclass(frozen=True, slots=True)
class ExtractionFailure:
    """A request that failed after its retry (S2 §5.3); it scores as empty output. H2's client passes the retry's
    failure kind."""

    source_id: str
    kind: FailureKind
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, str) or not self.source_id:
            raise ValueError("ExtractionFailure.source_id must be a non-empty string")
        if self.kind not in FAILURE_KINDS:
            raise ValueError(f"ExtractionFailure.kind must be one of {FAILURE_KINDS}, not {self.kind!r}")
        if not isinstance(self.detail, str):
            raise ValueError("ExtractionFailure.detail must be a string")


ExtractionResult: TypeAlias = Extraction | ExtractionFailure


def _not_null(value: object, expected: str) -> object:
    """An optional key may be left out, but when present it carries a value: no null, no coercion."""
    if value is None:
        raise ValueError(f"must be {expected}, not null")
    return value


class _Wire(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class _WireCheckability(_Wire):
    is_checkable: StrictBool = Field(alias="isCheckable")
    reason: str | None = None

    @field_validator("is_checkable")
    @classmethod
    def _must_be_true(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("must be true: claims[] holds checkable claims only")
        return value

    @field_validator("reason", mode="before")
    @classmethod
    def _reason_not_null(cls, value: object) -> object:
        return _not_null(value, "a string")


class _WireClaim(_Wire):
    text: str
    segment_ids: list[str] = Field(alias="segmentIds", min_length=1)
    claim_id: str | None = Field(default=None, alias="claimId")
    speaker_id: str | None = Field(default=None, alias="speakerId")
    checkability: _WireCheckability | None = None

    @field_validator("text")
    @classmethod
    def _text_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain a non-whitespace character")
        return value

    @field_validator("claim_id", "speaker_id", mode="before")
    @classmethod
    def _strings_not_null(cls, value: object) -> object:
        return _not_null(value, "a string")

    @field_validator("checkability", mode="before")
    @classmethod
    def _checkability_not_null(cls, value: object) -> object:
        return _not_null(value, "an object")


class _WireNonCheckable(_Wire):
    segment_ids: list[str] = Field(alias="segmentIds", min_length=1)
    reason_kind: ReasonKind = Field(alias="reasonKind")
    text: str | None = None
    claim_id: str | None = Field(default=None, alias="claimId")
    speaker_id: str | None = Field(default=None, alias="speakerId")
    reason: str | None = None

    @field_validator("text", "claim_id", "speaker_id", "reason", mode="before")
    @classmethod
    def _strings_not_null(cls, value: object) -> object:
        return _not_null(value, "a string")


class _WireExtraction(_Wire):
    claims: list[_WireClaim]
    non_checkable: list[_WireNonCheckable] = Field(alias="nonCheckable")


class _JsonRejected(Exception):
    """Raised by the JSON hooks; not a ValueError, so it never mixes with json.JSONDecodeError."""


def _escaped(text: str) -> str:
    """Backslashes and non-printable characters (LF, CR, U+0085, U+2028, ...) escaped as `repr` escapes them, so text
    taken from the model can never split one violation over two lines (E5)."""
    return "".join(repr(char)[1:-1] if char == "\\" or not char.isprintable() else char for char in text)


def _quoted(text: str) -> str:
    """E5's `'<k>'` and `'<id>'` forms: always single quotes, with a quote inside escaped too."""
    return "'" + _escaped(text).replace("'", "\\'") + "'"


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _JsonRejected(f"invalid JSON: duplicate key {_quoted(key)}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise _JsonRejected(f"invalid JSON: non-finite number {token}")


def _error_path(location: tuple[int | str, ...]) -> str:
    """The JSON path with camelCase keys and indices joined by '.', or '(root)' at the top level (E5). Keys come from
    the model's output, so they are escaped."""
    if not location:
        return "(root)"
    return ".".join(_escaped(part) if isinstance(part, str) else str(part) for part in location)


def _decode(raw: str | bytes) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExtractionInvalid("invalid_output", f"invalid UTF-8: {exc.reason}") from None
    raise TypeError(f"parse_extraction takes the raw response as str or bytes, not {type(raw).__name__}")


def _load_json(text: str) -> Any:
    """`json.loads` on the text exactly as given: no code-fence stripping, no substring extraction, no repair.

    Output the decoder cannot build is invalid output too (E1), with fixed wording: nesting deeper than the
    interpreter's recursion limit, and an integer longer than CPython's integer-string conversion limit (4,300 digits
    by default), the only other ValueError `json.loads` raises on str input.
    """
    try:
        return json.loads(text, object_pairs_hook=_object_without_duplicates, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:  # a ValueError subclass, so it must come before the ValueError clause
        raise ExtractionInvalid(
            "invalid_output", f"invalid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})"
        ) from None
    except _JsonRejected as exc:
        raise ExtractionInvalid("invalid_output", str(exc)) from None
    except RecursionError:
        raise ExtractionInvalid("invalid_output", "invalid JSON: nesting too deep") from None
    except ValueError:
        raise ExtractionInvalid("invalid_output", "invalid JSON: number too long") from None


def _membership_errors(wire: _WireExtraction, request_segment_ids: AbstractSet[str]) -> list[str]:
    """S2 §3.8: `segmentIds` that are not in the request. Exact string equality; output order (E3, E5)."""
    errors: list[str] = []
    groups: tuple[tuple[str, list[list[str]]], ...] = (
        ("claims", [claim.segment_ids for claim in wire.claims]),
        ("nonCheckable", [entry.segment_ids for entry in wire.non_checkable]),
    )
    for array, entries in groups:
        for position, segment_ids in enumerate(entries):
            for id_position, segment_id in enumerate(segment_ids):
                if segment_id not in request_segment_ids:
                    errors.append(
                        f"{array}.{position}.segmentIds.{id_position}: "
                        f"{_quoted(segment_id)} is not a segment id of this request"
                    )
    return errors


def _unique(segment_ids: list[str]) -> tuple[str, ...]:
    """An id repeated within one entry is kept once, at its first position (E3)."""
    return tuple(dict.fromkeys(segment_ids))


def parse_extraction(
    raw: str | bytes,
    *,
    source_id: str,
    request_segment_ids: AbstractSet[str],
    finish_reason: str | None = None,
) -> Extraction:
    """Validate one raw model response for one conversation (S2 §3.8, §3.10; PHASE-0 §2.2).

    Order: a cut-off (`finish_reason == "length"`) is invalid whatever the content; then strict UTF-8, `json.loads`
    with repeated keys and NaN/Infinity rejected, a JSON object at the top level, the Pydantic wire schema, and every
    `segmentIds` value a member of `request_segment_ids` (every segment of the request, item and non-item). Raises
    `ExtractionInvalid` with a deterministic, one-line-per-violation error text.
    """
    if not isinstance(source_id, str) or not source_id:
        raise TypeError("source_id must be a non-empty string")
    if not isinstance(request_segment_ids, AbstractSet):
        raise TypeError("request_segment_ids must be a set of segment ids")
    if finish_reason == "length":
        raise ExtractionInvalid("cut_off", CUT_OFF_TEXT)
    data = _load_json(_decode(raw))
    if not isinstance(data, dict):
        raise ExtractionInvalid("invalid_output", "(root): output must be a JSON object")
    try:
        wire = _WireExtraction.model_validate(data)
    except ValidationError as exc:
        lines = [f"{_error_path(error['loc'])}: {error['msg']}" for error in exc.errors(include_url=False)]
        raise ExtractionInvalid("invalid_output", "\n".join(lines)) from None
    membership = _membership_errors(wire, request_segment_ids)
    if membership:
        raise ExtractionInvalid("invalid_output", "\n".join(membership))
    claims = tuple(
        ExtractedClaim(
            index=position,
            text=claim.text,
            segment_ids=_unique(claim.segment_ids),
            claim_id=claim.claim_id,
            speaker_id=claim.speaker_id,
        )
        for position, claim in enumerate(wire.claims)
    )
    non_checkable = tuple(
        NonCheckableEntry(
            index=position,
            segment_ids=_unique(entry.segment_ids),
            reason_kind=entry.reason_kind,
            text=entry.text,
            claim_id=entry.claim_id,
            speaker_id=entry.speaker_id,
            reason=entry.reason,
        )
        for position, entry in enumerate(wire.non_checkable)
    )
    return Extraction(source_id=source_id, claims=claims, non_checkable=non_checkable)
