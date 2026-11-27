"""The demo pipeline: the fixtures, the frozen prompts, extraction, the mapping rule and the probe calls.

The demo shows claims, then verdicts with cited fixture sources, on synthetic transcripts. It reuses the harness as it
is: requests are built from the frozen prompts in the freeze's own form, and every call goes through
`ModelServerClient`, so the settings, the one repair and the same-request retry are the harness's (S2 §3.7, §3.8). It
holds no prompt wording, reads no gold and no environment, and writes nothing.

Implements, from the demo specification:

- Inputs: each transcript is a v2-style request (S2 §3.11): the language and segments with `segmentId`, `speakerId`,
  `start`, `end`, `text` and `asrConfidence`, beside the ids of its out-of-scope segments. A bundle holds 1 to 5
  sources with stable ids (S2 §4), each text marked synthetic, fictional and not advice, and it anchors only on
  in-scope segments of its transcript. Both files are canonical JSON, read by the H5a loader's `json_value`, and no
  text may hold a lone surrogate, which no request body can carry.
- Mapping: a claim on an out-of-scope segment is leaked; otherwise it takes the first bundle of its transcript whose
  anchors overlap its segments, and with none it has no evidence fixture. Neither sends anything.
- Flow: the frozen prompts come through `frozen.load_draft_inputs`, which checks `INPUTS.sha256` before any parse.
  Extraction allows at most one frozen repair. Each mapped claim goes to `ModelServerClient.probe` with its bundle's
  source ids, where `parse_probe` refuses an unknown cited id (PHASE-0 §2.2 invariant 2); the demo checks a valid
  output again, and an unknown id there is a hard failure.
- Failures: a failed call keeps its fault kind, the `CallRecord` outcome or the class name of a `ClientFault`, and
  never becomes a verdict. A `ClientFault` leaves no `CallRecord`, so the attempt it already sent is kept beside it.
  A claim text from the model that holds a lone surrogate cannot be sent, and fails as `UnicodeEncodeError`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, cast

from app.evaluation.bakeoff import frozen
from app.evaluation.bakeoff.client import (
    AttemptRecord,
    CallRecord,
    ChatRequest,
    ClientFault,
    ModelServerClient,
    _encodable,
)
from app.evaluation.bakeoff.extraction import ExtractedClaim, Extraction
from app.evaluation.bakeoff.inputs import LANGUAGE, PromptSet, input_text
from app.evaluation.bakeoff.probe import MAX_SOURCES_PER_CLAIM, ProbeOutput

MODEL_ID = "Qwen/Qwen3-4B"
TRANSCRIPTS_FILE = "transcripts.json"
BUNDLES_FILE = "bundles.json"
FORMAT_TRANSCRIPTS = "demo-transcripts/1"
FORMAT_BUNDLES = "demo-bundles/1"
SOURCE_MARK = "[Synthetic, fictional source. Not advice.]"
CONTRACT_KEYS = ("segmentId", "speakerId", "start", "end", "text", "asrConfidence")
ClaimStatus = Literal["verdict", "no_verdict", "no_evidence_fixture", "leaked"]

_ID = re.compile("[A-Za-z0-9][A-Za-z0-9_]{0,31}")
_TRANSCRIPT_KEYS = ("outOfScopeSegmentIds", "request", "title", "transcriptId")
_REQUEST_KEYS = ("language", "segments")
_BUNDLE_KEYS = ("anchors", "bundleId", "sources", "transcriptId")
_SOURCE_KEYS = ("sourceId", "text", "title")


class DemoError(ValueError):
    """A fixture breaks its format, or a valid probe output cites outside its bundle. Messages name files, keys and
    ids, never a text."""


@dataclass(frozen=True, slots=True)
class Segment:
    segment_id: str
    speaker_id: str
    start: float
    end: float
    text: str
    asr_confidence: float

    def request_row(self) -> dict[str, Any]:
        """The segment as a v2 request holds it, in contract key order (S2 §3.11)."""
        values = (self.segment_id, self.speaker_id, self.start, self.end, self.text, self.asr_confidence)
        return dict(zip(CONTRACT_KEYS, values, strict=True))


@dataclass(frozen=True, slots=True)
class Transcript:
    transcript_id: str
    title: str
    segments: tuple[Segment, ...]
    out_of_scope: frozenset[str]

    @property
    def segment_ids(self) -> frozenset[str]:
        return frozenset(segment.segment_id for segment in self.segments)


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class Bundle:
    bundle_id: str
    transcript_id: str
    anchors: tuple[str, ...]
    sources: tuple[Source, ...]

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(source.source_id for source in self.sources)


@dataclass(frozen=True, slots=True)
class Fixtures:
    """The transcripts and bundles in file order, with each file's SHA-256 over its LF bytes."""

    transcripts: tuple[Transcript, ...]
    bundles: tuple[Bundle, ...]
    sha256: Mapping[str, str]

    def bundles_for(self, transcript_id: str) -> tuple[Bundle, ...]:
        return tuple(bundle for bundle in self.bundles if bundle.transcript_id == transcript_id)


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """One extracted claim and what became of it. `call` is set when a probe call left a record; a run fault leaves
    none, and `fault_attempts` holds the attempts it had already made."""

    claim: ExtractedClaim
    item_id: str
    status: ClaimStatus
    bundle: Bundle | None = None
    call: CallRecord | None = None
    fault: str | None = None
    fault_attempts: tuple[AttemptRecord, ...] = ()

    @property
    def output(self) -> ProbeOutput | None:
        output = None if self.call is None else self.call.output
        return output if isinstance(output, ProbeOutput) else None

    @property
    def cited(self) -> tuple[Source, ...]:
        """The cited sources, in citation order."""
        output, bundle = self.output, self.bundle
        if output is None or bundle is None:
            return ()
        by_id = {source.source_id: source for source in bundle.sources}
        return tuple(by_id[source_id] for source_id in output.cited_source_ids)

    @property
    def uncited(self) -> tuple[Source, ...]:
        """The bundle's other sources, in bundle order: all of them when there is no verdict."""
        cited = {source.source_id for source in self.cited}
        return () if self.bundle is None else tuple(row for row in self.bundle.sources if row.source_id not in cited)


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    """One transcript's extraction call, or its fault and the attempts a run fault had made, and its claims in
    claims[] order."""

    transcript: Transcript
    call: CallRecord | None
    fault: str | None
    claims: tuple[ClaimResult, ...]
    fault_attempts: tuple[AttemptRecord, ...] = ()

    @property
    def extraction(self) -> Extraction | None:
        output = None if self.call is None else self.call.output
        return output if isinstance(output, Extraction) else None


def _join(directory: str, name: str) -> str:
    return f"{directory.rstrip('/' + chr(92))}/{name}"


def _object(value: object, keys: tuple[str, ...], where: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise DemoError(f"{where} must be an object with exactly the keys {sorted(keys)}")
    return value


def _ids(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and _ID.fullmatch(item) for item in value):
        raise DemoError(f"{where} must be a list of ids matching {_ID.pattern}")
    if len(set(value)) != len(value):
        raise DemoError(f"{where} holds an id twice")
    return tuple(value)


def _one_id(value: object, where: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DemoError(f"{where} must be an id matching {_ID.pattern}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DemoError(f"{where} must be a non-blank string")
    if not _encodable(value):  # the client's own check: a JSON escape can decode to a lone surrogate
        raise DemoError(f"{where} holds a lone surrogate")
    return value


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DemoError(f"{where} must be a number")
    return value


def _unique(values: list[str], what: str) -> None:
    if len(set(values)) != len(values):
        raise DemoError(f"{what} is used twice")


def _list(value: object, where: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise DemoError(f"{where} must be a non-empty list")
    return value


def _top(value: object, name: str, key: str, tag: str) -> list[Any]:
    top = _object(value, ("format", key), name)
    if top["format"] != tag:
        raise DemoError(f"{name}: format is not {tag!r}")
    return _list(top[key], f"{name}: {key}")


def _segment(row: object, where: str) -> Segment:
    data = _object(row, CONTRACT_KEYS, where)
    start, end = _number(data["start"], f"{where} start"), _number(data["end"], f"{where} end")
    confidence = _number(data["asrConfidence"], f"{where} asrConfidence")
    if not 0 <= start < end:
        raise DemoError(f"{where}: start and end must satisfy 0 <= start < end")
    if not 0 <= confidence <= 1:
        raise DemoError(f"{where}: asrConfidence must lie between 0 and 1")
    segment_id = _one_id(data["segmentId"], f"{where} segmentId")
    speaker_id = _one_id(data["speakerId"], f"{where} speakerId")
    return Segment(segment_id, speaker_id, start, end, _text(data["text"], f"{where} text"), confidence)


def _transcript(row: object, position: int) -> Transcript:
    where = f"{TRANSCRIPTS_FILE}: transcripts[{position}]"
    data = _object(row, _TRANSCRIPT_KEYS, where)
    name = _one_id(data["transcriptId"], f"{where} transcriptId")
    request = _object(data["request"], _REQUEST_KEYS, f"{name} request")
    if request["language"] != LANGUAGE:
        raise DemoError(f"{name}: the request language is not {LANGUAGE!r}")
    rows = _list(request["segments"], f"{name} request segments")
    segments = tuple(_segment(segment, f"{name} segments[{number}]") for number, segment in enumerate(rows))
    _unique([segment.segment_id for segment in segments], f"{name}: a segment id")
    out_of_scope = frozenset(_ids(data["outOfScopeSegmentIds"], f"{name} outOfScopeSegmentIds"))
    transcript = Transcript(name, _text(data["title"], f"{name} title"), segments, out_of_scope)
    if not out_of_scope <= transcript.segment_ids:
        raise DemoError(f"{name}: an out-of-scope id is not a segment id of the transcript")
    return transcript


def _source(row: object, where: str) -> Source:
    data = _object(row, _SOURCE_KEYS, where)
    text = _text(data["text"], f"{where} text")
    if not text.startswith(SOURCE_MARK + " "):
        raise DemoError(f"{where}: the text must start with the synthetic-source mark")
    return Source(_one_id(data["sourceId"], f"{where} sourceId"), _text(data["title"], f"{where} title"), text)


def _bundle(row: object, position: int, transcripts: Mapping[str, Transcript]) -> Bundle:
    data = _object(row, _BUNDLE_KEYS, f"{BUNDLES_FILE}: bundles[{position}]")
    name = _one_id(data["bundleId"], f"{BUNDLES_FILE}: bundles[{position}] bundleId")
    transcript = transcripts.get(_one_id(data["transcriptId"], f"{name} transcriptId"))
    if transcript is None:
        raise DemoError(f"{name}: unknown transcript id")
    anchors = _ids(data["anchors"], f"{name} anchors")
    if not anchors:
        raise DemoError(f"{name}: anchors is empty")
    if not set(anchors) <= transcript.segment_ids:
        raise DemoError(f"{name}: an anchor is not a segment id of its transcript")
    if set(anchors) & transcript.out_of_scope:
        raise DemoError(f"{name}: an anchor is out of scope")
    rows = data["sources"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SOURCES_PER_CLAIM:
        raise DemoError(f"{name}: sources must hold 1 to {MAX_SOURCES_PER_CLAIM} entries")
    sources = tuple(_source(source, f"{name} sources[{number}]") for number, source in enumerate(rows))
    _unique([source.source_id for source in sources], f"{name}: a source id")
    return Bundle(name, transcript.transcript_id, anchors, sources)


def load_fixtures(fixtures_dir: str, read_bytes: Callable[[str], bytes]) -> Fixtures:
    """The transcripts and bundles of `fixtures_dir`, read only through `read_bytes` and checked. A file not in its
    canonical form is refused by `frozen.json_value`, as `FrozenInputError`; every other refusal is `DemoError`."""
    files = {name: read_bytes(_join(fixtures_dir, name)) for name in (BUNDLES_FILE, TRANSCRIPTS_FILE)}
    rows = _top(
        frozen.json_value(files[TRANSCRIPTS_FILE], TRANSCRIPTS_FILE),
        TRANSCRIPTS_FILE,
        "transcripts",
        FORMAT_TRANSCRIPTS,
    )
    transcripts = tuple(_transcript(row, position) for position, row in enumerate(rows))
    _unique([transcript.transcript_id for transcript in transcripts], f"{TRANSCRIPTS_FILE}: a transcript id")
    by_id = {transcript.transcript_id: transcript for transcript in transcripts}
    rows = _top(frozen.json_value(files[BUNDLES_FILE], BUNDLES_FILE), BUNDLES_FILE, "bundles", FORMAT_BUNDLES)
    bundles = tuple(_bundle(row, position, by_id) for position, row in enumerate(rows))
    _unique([bundle.bundle_id for bundle in bundles], f"{BUNDLES_FILE}: a bundle id")
    sha256 = {name: hashlib.sha256(frozen.lf_bytes(raw)).hexdigest() for name, raw in files.items()}
    return Fixtures(transcripts, bundles, MappingProxyType(sha256))


def load_prompts(inputs_dir: str, read_bytes: Callable[[str], bytes]) -> PromptSet:
    """The frozen prompts and repair wording, through the H5a loader: `INPUTS.sha256` is checked before any file is
    parsed, and a mismatch raises `FrozenInputError` (S2 §3.10, §3.12)."""
    return frozen.load_draft_inputs(inputs_dir, read_bytes).prompts


def extraction_request(prompts: PromptSet, transcript: Transcript) -> ChatRequest:
    """A single-conversation extraction in the freeze's form: the language and the segments, nothing else."""
    value = {"language": LANGUAGE, "segments": [segment.request_row() for segment in transcript.segments]}
    return ChatRequest("single_conversation_extraction", prompts.extraction_messages(input_text(value)))


def probe_request(prompts: PromptSet, claim_text: str, bundle: Bundle) -> ChatRequest:
    """A reasoning probe in the freeze's form: the claim and its bundle's sources by id and text."""
    sources = [{"sourceId": source.source_id, "text": source.text} for source in bundle.sources]
    return ChatRequest("reasoning_probe", prompts.probe_messages(input_text({"claim": claim_text, "sources": sources})))


def bundle_for(segment_ids: tuple[str, ...], bundles: tuple[Bundle, ...]) -> Bundle | None:
    """The first bundle whose anchors overlap `segment_ids`, or None."""
    return next((bundle for bundle in bundles if set(bundle.anchors) & set(segment_ids)), None)


def claim_item_id(transcript_id: str, claim: ExtractedClaim) -> str:
    return f"{transcript_id}.c{claim.index + 1:02d}"


_Guarded = tuple[CallRecord | None, str | None, tuple[AttemptRecord, ...]]


def _guarded(call: Callable[[], CallRecord]) -> _Guarded:
    """A call's record and fault kind: the outcome when it is not valid, or the class name of a `ClientFault`, a run
    condition that leaves no record but keeps the attempts already made (S2 §3.13), or of the `UnicodeEncodeError` of a
    request body that cannot be sent."""
    try:
        record = call()
    except (ClientFault, UnicodeEncodeError) as exc:
        return None, type(exc).__name__, tuple(getattr(exc, "attempts", ()))
    return record, None if record.outcome == "valid" else record.outcome, ()


def _claim(
    client: ModelServerClient,
    prompts: PromptSet,
    transcript: Transcript,
    bundles: tuple[Bundle, ...],
    claim: ExtractedClaim,
) -> ClaimResult:
    item_id = claim_item_id(transcript.transcript_id, claim)
    if set(claim.segment_ids) & transcript.out_of_scope:
        return ClaimResult(claim, item_id, "leaked")
    bundle = bundle_for(claim.segment_ids, bundles)
    if bundle is None:
        return ClaimResult(claim, item_id, "no_evidence_fixture")
    request = probe_request(prompts, claim.text, bundle)
    source_ids = frozenset(bundle.source_ids)
    record, fault, attempts = _guarded(
        lambda: client.probe(request, item_id=item_id, bundle_source_ids=source_ids, repair_template=prompts.repair)
    )
    if fault is not None:
        return ClaimResult(claim, item_id, "no_verdict", bundle, record, fault, attempts)
    output = cast(ProbeOutput, cast(CallRecord, record).output)
    if not set(output.cited_source_ids) <= source_ids:
        raise DemoError(f"{item_id}: a valid probe output cites an id outside its bundle")
    return ClaimResult(claim, item_id, "verdict", bundle, record)


def _transcript_run(
    client: ModelServerClient, prompts: PromptSet, transcript: Transcript, bundles: tuple[Bundle, ...]
) -> TranscriptResult:
    request = extraction_request(prompts, transcript)
    record, fault, attempts = _guarded(
        lambda: client.extract(
            request,
            item_id=transcript.transcript_id,
            request_segment_ids=transcript.segment_ids,
            repair_template=prompts.repair,
        )
    )
    if fault is not None:
        return TranscriptResult(transcript, record, fault, (), attempts)
    extraction = cast(Extraction, cast(CallRecord, record).output)
    claims = tuple(_claim(client, prompts, transcript, bundles, claim) for claim in extraction.claims)
    return TranscriptResult(transcript, record, None, claims)


def run_demo(client: ModelServerClient, prompts: PromptSet, fixtures: Fixtures) -> tuple[TranscriptResult, ...]:
    """Every transcript in fixture order: its extraction, then each claim in claims[] order, one call at a time."""
    return tuple(
        _transcript_run(client, prompts, transcript, fixtures.bundles_for(transcript.transcript_id))
        for transcript in fixtures.transcripts
    )
