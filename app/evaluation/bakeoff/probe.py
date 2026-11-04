"""Parse one reasoning-probe output (bake-off harness H2).

Implements:

- S2 §4, the reasoning probe: it "measures only reasoning-call latency and citation ids", against a bundle of at most
  `FACTCHECK_MAX_SOURCES_PER_CLAIM` delimited sources with stable ids.
- S2 §3.8, what "invalid" means: the output fails validation, and output cut off at the token cap is invalid whatever
  its content. Failures raise H1's `ExtractionInvalid`, so one `except` serves both validators. The error text is
  deterministic, because the repair request carries it.
- S2 §6, gate C: "a first attempt fails if it cites an id outside its bundle or cites none", counted on every first
  attempt, also on the unconstrained pass, where schema-invalid output occurs. `readable_citation_ids` gives gate C
  that evidence; H2 computes no gate.
- PHASE-0 §2.2, the per-claim result: `verdict` is one of exactly four members, `reason` is a short explanation, and an
  unknown `citations[].sourceId` is a hard validation failure. A model-reported confidence is never used (invariant 1),
  so the model does not write one. The product's fallback of an unparseable response to `INSUFFICIENT_EVIDENCE`
  (invariant 4) is product behaviour: here a failed probe stays failed, for gates J and T.

Neither S2 nor PHASE-0 defines what the model itself writes; `_WireProbe` is the best reading, flagged for the owner.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, ValidationError, field_validator

from .extraction import CUT_OFF_TEXT, ExtractionInvalid, _error_path, _load_json, _quoted, _Wire

Verdict = Literal["SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE"]
VERDICTS = ("SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE")

# Wire key sets. The validator below and `schemas.probe_schema()` are built to exactly these keys.
PROBE_KEYS = frozenset({"citations", "reason", "verdict"})
CITATION_KEYS = frozenset({"sourceId"})

MAX_SOURCES_PER_CLAIM = 5  # FACTCHECK_MAX_SOURCES_PER_CLAIM (PHASE-0 §7; S2 §4)


@dataclass(frozen=True, slots=True)
class ProbeOutput:
    """A valid probe output. `cited_source_ids` is unique, in first-occurrence order, and may be empty."""

    verdict: Verdict
    reason: str
    cited_source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.verdict not in VERDICTS:
            raise ValueError(f"ProbeOutput.verdict must be one of {VERDICTS}, not {self.verdict!r}")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("ProbeOutput.reason must contain a non-whitespace character")
        ids = self.cited_source_ids
        if not isinstance(ids, tuple) or not all(isinstance(source_id, str) for source_id in ids):
            raise ValueError("ProbeOutput.cited_source_ids must be a tuple of strings")
        if len(set(ids)) != len(ids):
            raise ValueError("ProbeOutput.cited_source_ids must be unique")

    @property
    def cites_none(self) -> bool:
        return not self.cited_source_ids


class _WireCitation(_Wire):
    source_id: str = Field(alias="sourceId")


class _WireProbe(_Wire):
    # NEEDS-OWNER(AMB-26): the model writes only verdict, reason and citations[{sourceId}]
    verdict: Verdict
    reason: str
    # NEEDS-OWNER(AMB-27): an empty citations list is valid output and fails gate C only
    citations: list[_WireCitation]

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain a non-whitespace character")
        return value


def _membership_errors(wire: _WireProbe, bundle_source_ids: AbstractSet[str]) -> list[str]:
    """PHASE-0 §2.2 invariant 2. Exact string equality; one line per offending occurrence, in output order."""
    return [
        f"citations.{position}.sourceId: {_quoted(citation.source_id)} is not a source id of this bundle"
        for position, citation in enumerate(wire.citations)
        if citation.source_id not in bundle_source_ids
    ]


def parse_probe(raw: str, *, bundle_source_ids: AbstractSet[str], finish_reason: str | None = None) -> ProbeOutput:
    """Validate one raw probe response (S2 §3.8, §4; PHASE-0 §2.2).

    Order: a cut-off (`finish_reason == "length"`) is invalid whatever the content; then `json.loads` with repeated
    keys and NaN/Infinity rejected and no fence stripping, a JSON object at the top level, the Pydantic wire schema, and
    every `sourceId` a member of the bundle. Raises `ExtractionInvalid` with one line per violation. An empty
    `citations` list is valid here, and a repeated id is kept once, at its first position.
    """
    if not isinstance(raw, str):
        raise TypeError(f"parse_probe takes the raw response as str, not {type(raw).__name__}")
    if not isinstance(bundle_source_ids, AbstractSet):
        raise TypeError("bundle_source_ids must be a set of source ids")
    if finish_reason == "length":
        raise ExtractionInvalid("cut_off", CUT_OFF_TEXT)
    data = _load_json(raw)
    if not isinstance(data, dict):
        raise ExtractionInvalid("invalid_output", "(root): output must be a JSON object")
    try:
        wire = _WireProbe.model_validate(data)
    except ValidationError as exc:
        lines = [f"{_error_path(error['loc'])}: {error['msg']}" for error in exc.errors(include_url=False)]
        raise ExtractionInvalid("invalid_output", "\n".join(lines)) from None
    membership = _membership_errors(wire, bundle_source_ids)
    if membership:
        raise ExtractionInvalid("invalid_output", "\n".join(membership))
    cited = tuple(dict.fromkeys(citation.source_id for citation in wire.citations))
    return ProbeOutput(verdict=wire.verdict, reason=wire.reason, cited_source_ids=cited)


def readable_citation_ids(raw: str) -> tuple[str, ...] | None:
    """The `sourceId` strings of `raw`, unique in first-occurrence order, whatever else is wrong with the output.

    Gate C judges a first attempt on the ids it cites, and has no schema condition, so ids are read even from output
    that fails validation. `None` means the citations cannot be read at all: the text does not parse under H1's JSON
    rules, or it is not an object whose `citations` is a list of objects each holding a string `sourceId`.
    """
    try:
        data = _load_json(raw)
    except ExtractionInvalid:
        return None
    citations = data.get("citations") if isinstance(data, dict) else None
    if not isinstance(citations, list):
        return None
    source_ids: list[str] = []
    for citation in citations:
        source_id = citation.get("sourceId") if isinstance(citation, dict) else None
        if not isinstance(source_id, str):
            return None
        source_ids.append(source_id)
    return tuple(dict.fromkeys(source_ids))
