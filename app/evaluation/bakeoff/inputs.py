"""The frozen inputs of the bake-off (bake-off harness H3).

Every request a run sends is built and hashed here, before the first run, from the gold set and H5's wording. H3 ships
no wording: the prompt set, the cap-session list, the probe and the warm-up are H5's input, and only tests hold
synthetic stand-ins. The result is immutable, so no request can change after the freeze.

Implements, from the signed D6 criteria note S2:

- S2 §3.10, prompts: "There is one prompt set, identical for every candidate and in the deployed form", "frozen and
  hashed before the first run". One extraction pair serves single conversations, cap sessions and the warm-up. A user
  template takes the input at one placeholder, by plain one-pass replacement, so transcript text is never rescanned;
  the delimiters and the untrusted-data label are H5's wording.
- S2 §3.11, input: one v2-style request per conversation, holding `segmentId`, `speakerId`, `start`, `end`, `text` and
  `asrConfidence`, with language `en`, and no call date or setting. In a cap session `segmentId` is re-keyed as
  `<source_id>:<segment_id>` and `speakerId` as `<source_id>:<speaker>`, in one form for both (AMB-56). `sessionId` is
  not sent (AMB-54).
- S2 §3.12, data: gold-set v1.0 only. The set is read only through `load_gold_set`, so the manifest is checked first,
  and no parameter or variable overrides the version check.
- S2 §3.13, runs: "Each run starts with one discarded warm-up request". It is a synthetic, non-gold single-conversation
  extraction (S2 §11 item 5; AMB-58).
- S2 §4, the reasoning probe: 20 claims, each with a bundle of 5 sources with stable ids. H3 does not count the 300
  tokens of a source, which differ by tokenizer, and cannot judge whether one bears on the claim: both are H5's.
- S2 §6, cap sessions: "4 frozen sessions", each joining whole conversations "until it holds at least 20 in-scope
  `CHECKABLE` rows". H5 chooses the list and the freeze verifies it (AMB-76).
- S2 §11 item 6, frozen and hashed: the prompts and the retry wording, the probe, and the cap-session list and its
  re-keyed requests, each through `canonical_sha256`, plus every request's own hash.
- PHASE-0 §2.2, the request shape: contract key order, in the compact form `input_text` writes (AMB-55).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .client import REPAIR_PLACEHOLDER, ChatMessage, ChatRequest, RepairTemplate, RequestKind
from .gold import GoldSegment, GoldSet, load_gold_set
from .probe import MAX_SOURCES_PER_CLAIM
from .schemas import canonical_sha256

GOLD_VERSION = "v1.0"
LANGUAGE = "en"
INPUT_PLACEHOLDER = "<<INPUT_JSON>>"
REKEY_SEPARATOR = ":"
CAP_SESSION_COUNT = 4
CAP_SESSION_ROWS = 20  # FACTCHECK_AGENT_MAX_CLAIMS (S2 §6), provisional
PROBE_ITEM_COUNT = 20  # sources per item: probe.MAX_SOURCES_PER_CLAIM


class InputsError(ValueError):
    """The inputs cannot be frozen."""


@dataclass(frozen=True, slots=True)
class PromptSet:
    """What H5 freezes (S2 §3.10, §3.8). The placeholders are checked here, so a request can only be filled one way."""

    extraction_system: str
    extraction_user: str
    probe_system: str
    probe_user: str
    repair: RepairTemplate

    def __post_init__(self) -> None:
        systems = (self.extraction_system, self.probe_system)
        users = (self.extraction_user, self.probe_user)
        if not all(isinstance(text, str) and text.strip() for text in (*systems, *users)):
            raise InputsError("PromptSet texts must each contain a non-whitespace character")
        if not isinstance(self.repair, RepairTemplate):
            raise InputsError("PromptSet.repair must be a RepairTemplate")
        if any(text.count(INPUT_PLACEHOLDER) != 1 for text in users):
            raise InputsError(f"each PromptSet user template must contain {INPUT_PLACEHOLDER} exactly once")
        if any(INPUT_PLACEHOLDER in text or REPAIR_PLACEHOLDER in text for text in systems):
            raise InputsError("a PromptSet system text must contain no placeholder")
        if any(REPAIR_PLACEHOLDER in text for text in users):
            raise InputsError(f"a PromptSet user template must not contain {REPAIR_PLACEHOLDER}")
        if INPUT_PLACEHOLDER in self.repair.text:
            raise InputsError(f"the repair wording must not contain {INPUT_PLACEHOLDER}")

    def extraction_messages(self, input_text: str) -> tuple[ChatMessage, ChatMessage]:
        return _messages(self.extraction_system, self.extraction_user, input_text)

    def probe_messages(self, input_text: str) -> tuple[ChatMessage, ChatMessage]:
        return _messages(self.probe_system, self.probe_user, input_text)

    def sha256(self) -> str:
        """The prompts and the retry wording together, as Annex A's "prompt and retry hash"."""
        return canonical_sha256(
            {
                "extractionSystem": self.extraction_system,
                "extractionUser": self.extraction_user,
                "probeSystem": self.probe_system,
                "probeUser": self.probe_user,
                "repair": self.repair.text,
            }
        )


def _messages(system: str, user: str, text: str) -> tuple[ChatMessage, ChatMessage]:
    """Plain one-pass substitution, as `RepairTemplate.render`: `str.format` is never used, and text holding the
    placeholder, a brace or a dollar sign stays literal."""
    return ChatMessage("system", system), ChatMessage("user", user.replace(INPUT_PLACEHOLDER, text, 1))


@dataclass(frozen=True, slots=True)
class CapSessionSpec:
    """One cap session: its id and the whole conversations it joins, in order (S2 §6)."""

    session_id: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProbeSource:
    """One source of a bundle, with its stable id (S2 §4)."""

    source_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ProbeItem:
    """One probe claim and its bundle (S2 §4)."""

    item_id: str
    claim: str
    sources: tuple[ProbeSource, ...]


@dataclass(frozen=True, slots=True)
class WarmupSpec:
    """The synthetic, non-gold warm-up request (S2 §3.13, §11 item 5), in the shape of a single conversation."""

    item_id: str
    segments: tuple[GoldSegment, ...]


@dataclass(frozen=True, slots=True)
class FrozenRequest:
    """One frozen request. `request` is the constrained form; `ids` is what its output may cite: the segment ids of an
    extraction, re-keyed for a cap session, or the source ids of a probe. `item_id` is the source id of a single
    conversation, the session id of a cap session (AMB-79) and the item id of a probe."""

    item_id: str
    request: ChatRequest
    ids: frozenset[str]

    @property
    def unconstrained(self) -> ChatRequest:
        """The diagnostic pass's form (S2 §3.8): the same kind and messages, with no `response_format`."""
        return ChatRequest(self.request.kind, self.request.messages, constrained=False)


@dataclass(frozen=True, slots=True)
class FreezeHashes:
    """S2 §11 item 6 and Annex A. `requests` and `requests_unconstrained` map every item id to the hash of that form;
    `requests[item_id]` equals the first attempt's `request_sha256`. `cap_requests_sha256` covers the constrained forms
    the gates read (AMB-83)."""

    prompts_sha256: str
    repair_sha256: str
    probe_sha256: str
    cap_sessions_sha256: str
    cap_requests_sha256: str
    warmup_sha256: str
    requests: Mapping[str, str]
    requests_unconstrained: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    """Everything a schedule sends, frozen. It holds tuples and read-only mappings."""

    gold_version: str
    gold_manifest: Mapping[str, str]
    prompts: PromptSet
    warmup: FrozenRequest
    singles: tuple[FrozenRequest, ...]
    cap_sessions: tuple[FrozenRequest, ...]
    probes: tuple[FrozenRequest, ...]
    hashes: FreezeHashes

    def all_requests(self) -> tuple[FrozenRequest, ...]:
        """The warm-up first, then the singles, the cap sessions and the probes."""
        return (self.warmup, *self.singles, *self.cap_sessions, *self.probes)


def input_text(value: object) -> str:
    """The text form of a request's input (AMB-55): contract key order, compact separators, real characters. The
    request hash pins the bytes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _check_ids(ids: list[str], what: str) -> None:
    """Non-empty strings with no repeat."""
    if not all(isinstance(value, str) and value for value in ids):
        raise InputsError(f"{what} ids must be non-empty strings")
    repeated = sorted({value for value in ids if ids.count(value) > 1})
    if repeated:
        raise InputsError(f"{what} id {repeated[0]!r} is used twice")


def _input_segment(segment: GoldSegment, segment_id: str, speaker_id: str) -> dict[str, Any]:
    """One segment of a v2-style request (S2 §3.11), in contract order. Times and texts go in unchanged, so a cap
    session's `start` restarts at each conversation (AMB-57)."""
    return {
        "segmentId": segment_id,
        "speakerId": speaker_id,
        "start": segment.start,
        "end": segment.end,
        "text": segment.text,
        "asrConfidence": segment.confidence,
    }


def _extraction(prompts: PromptSet, kind: RequestKind, item_id: str, segments: list[dict[str, Any]]) -> FrozenRequest:
    """The input is exactly the language and the segments, so no call date, setting or `sessionId` can reach it."""
    messages = prompts.extraction_messages(input_text({"language": LANGUAGE, "segments": segments}))
    return FrozenRequest(item_id, ChatRequest(kind, messages), frozenset(row["segmentId"] for row in segments))


def _single(prompts: PromptSet, item_id: str, segments: tuple[GoldSegment, ...]) -> FrozenRequest:
    rows = [_input_segment(segment, segment.segment_id, segment.speaker) for segment in segments]
    return _extraction(prompts, "single_conversation_extraction", item_id, rows)


def _cap_session(gold: GoldSet, prompts: PromptSet, spec: CapSessionSpec) -> FrozenRequest:
    """S2 §3.11's re-keying. The session id is the item id (AMB-79), so H2's `output.source_id == item_id` holds and no
    session output can pass as a conversation's."""
    rows = [
        _input_segment(
            segment,
            f"{source_id}{REKEY_SEPARATOR}{segment.segment_id}",
            f"{source_id}{REKEY_SEPARATOR}{segment.speaker}",
        )
        for source_id in spec.source_ids
        for segment in gold.conversation(source_id).segments
    ]
    return _extraction(prompts, "cap_session_extraction", spec.session_id, rows)


def _probe(prompts: PromptSet, item: ProbeItem) -> FrozenRequest:
    sources = [{"sourceId": source.source_id, "text": source.text} for source in item.sources]
    messages = prompts.probe_messages(input_text({"claim": item.claim, "sources": sources}))
    ids = frozenset(source.source_id for source in item.sources)
    return FrozenRequest(item.item_id, ChatRequest("reasoning_probe", messages), ids)


def _check_rekeyable(gold: GoldSet, spec: CapSessionSpec, source_id: str) -> None:
    """One split on the first separator must always give the gold key back, so a separator is refused in any id of a
    session, collision or not."""
    for segment in gold.conversation(source_id).segments:
        for part in (source_id, segment.segment_id, segment.speaker):
            if REKEY_SEPARATOR in part:
                raise InputsError(f"{spec.session_id}: id {part!r} holds the re-key separator {REKEY_SEPARATOR!r}")


def _check_cap_session(gold: GoldSet, spec: CapSessionSpec) -> None:
    """S2 §6's definition, "until" read literally (AMB-76): at least the cap, and short of it without the last
    conversation. A conversation may recur in another session, never within one."""
    name = spec.session_id
    if not isinstance(spec.source_ids, tuple) or not spec.source_ids:
        raise InputsError(f"{name}: source_ids must be a non-empty tuple")
    known = gold.conversation_ids()
    for position, source_id in enumerate(spec.source_ids):
        if source_id in spec.source_ids[:position]:
            raise InputsError(f"{name}: source id {source_id!r} is listed twice")
        if source_id not in known:
            raise InputsError(f"{name}: source id {source_id!r} is not a gold conversation")
        _check_rekeyable(gold, spec, source_id)
    rows = [sum(item.is_in_scope_checkable for item in gold.items_for(source_id)) for source_id in spec.source_ids]
    if sum(rows) < CAP_SESSION_ROWS:
        raise InputsError(f"{name}: {sum(rows)} in-scope CHECKABLE rows, fewer than {CAP_SESSION_ROWS}")
    if sum(rows[:-1]) >= CAP_SESSION_ROWS:
        raise InputsError(f"{name}: {sum(rows[:-1])} rows before its last conversation, so it passes the cap")


def _check_cap_sessions(gold: GoldSet, specs: tuple[CapSessionSpec, ...]) -> None:
    if not isinstance(specs, tuple) or len(specs) != CAP_SESSION_COUNT:
        raise InputsError(f"the cap-session list must be a tuple of exactly {CAP_SESSION_COUNT} sessions")
    _check_ids([spec.session_id for spec in specs], "cap-session")
    for spec in specs:
        _check_cap_session(gold, spec)


def _check_probe_items(items: tuple[ProbeItem, ...]) -> None:
    """S2 §4, stricter than H2's client, which alone accepts 1 to 5 sources."""
    if not isinstance(items, tuple) or len(items) != PROBE_ITEM_COUNT:
        raise InputsError(f"the probe must be a tuple of exactly {PROBE_ITEM_COUNT} items")
    _check_ids([item.item_id for item in items], "probe item")
    for item in items:
        if not isinstance(item.claim, str) or not item.claim.strip():
            raise InputsError(f"{item.item_id}: the claim must contain a non-whitespace character")
        if not isinstance(item.sources, tuple) or len(item.sources) != MAX_SOURCES_PER_CLAIM:
            raise InputsError(f"{item.item_id}: the bundle must be a tuple of exactly {MAX_SOURCES_PER_CLAIM} sources")
        _check_ids([source.source_id for source in item.sources], f"{item.item_id}: source")
        if not all(isinstance(source.text, str) and source.text.strip() for source in item.sources):
            raise InputsError(f"{item.item_id}: every source text must contain a non-whitespace character")


def _check_warmup(gold: GoldSet, warmup: WarmupSpec) -> None:
    """S2 §11 item 5: "one non-gold request"."""
    segments = warmup.segments
    if not isinstance(segments, tuple) or not segments or not all(isinstance(row, GoldSegment) for row in segments):
        raise InputsError("the warm-up must hold a non-empty tuple of GoldSegment values")
    _check_ids([segment.segment_id for segment in segments], "warm-up segment")
    if warmup.item_id in gold.conversation_ids():
        raise InputsError(f"the warm-up item id {warmup.item_id!r} is a gold source id")
    gold_texts = {segment.text for conversation in gold.conversations for segment in conversation.segments}
    if any(segment.text in gold_texts for segment in segments):
        raise InputsError("a warm-up segment repeats a gold segment text")


def _hashes(
    prompts: PromptSet,
    cap_sessions: tuple[CapSessionSpec, ...],
    probe_items: tuple[ProbeItem, ...],
    requests: tuple[FrozenRequest, ...],
    caps: tuple[FrozenRequest, ...],
) -> FreezeHashes:
    """S2 §11 item 6. A lone surrogate in a text raises `UnicodeEncodeError` here, as no request body can carry one."""
    probe = [
        {
            "itemId": item.item_id,
            "claim": item.claim,
            "sources": [{"sourceId": source.source_id, "text": source.text} for source in item.sources],
        }
        for item in probe_items
    ]
    sessions = [{"sessionId": spec.session_id, "sourceIds": list(spec.source_ids)} for spec in cap_sessions]
    return FreezeHashes(
        prompts_sha256=prompts.sha256(),
        repair_sha256=prompts.repair.sha256(),
        probe_sha256=canonical_sha256(probe),
        cap_sessions_sha256=canonical_sha256(sessions),
        cap_requests_sha256=canonical_sha256({frozen.item_id: frozen.request.sha256() for frozen in caps}),
        warmup_sha256=requests[0].request.sha256(),
        requests=MappingProxyType({frozen.item_id: frozen.request.sha256() for frozen in requests}),
        requests_unconstrained=MappingProxyType({frozen.item_id: frozen.unconstrained.sha256() for frozen in requests}),
    )


def freeze_inputs(
    gold_dir: str,
    *,
    prompts: PromptSet,
    cap_sessions: tuple[CapSessionSpec, ...],
    probe_items: tuple[ProbeItem, ...],
    warmup: WarmupSpec,
) -> FrozenInputs:
    """Build and hash every request (S2 §3.10-§3.13, §4, §11 item 6).

    The directory is resolved first, so a symlink cannot lend another version the name, and the manifest check comes
    before anything else: a bad manifest raises `ManifestError`. Only v1.0 passes, by exact equality with the resolved
    directory's name, and no parameter or variable overrides that. A renamed copy of another version passes this check
    (AMB-82), so the manifest values go into the result for comparison with Annex A. Any other refusal is `InputsError`.
    """
    gold = load_gold_set(Path(gold_dir).resolve())
    if gold.version != GOLD_VERSION:
        raise InputsError(f"gold set {gold.version!r} is not {GOLD_VERSION}, the only version a run may use (S2 §3.12)")
    _check_cap_sessions(gold, cap_sessions)
    _check_probe_items(probe_items)
    _check_warmup(gold, warmup)
    first = _single(prompts, warmup.item_id, warmup.segments)
    singles = tuple(_single(prompts, row.source_id, row.segments) for row in gold.conversations)
    caps = tuple(_cap_session(gold, prompts, spec) for spec in cap_sessions)
    probes = tuple(_probe(prompts, item) for item in probe_items)
    requests = (first, *singles, *caps, *probes)
    _check_ids([frozen.item_id for frozen in requests], "request item")
    return FrozenInputs(
        gold_version=gold.version,
        gold_manifest=MappingProxyType(dict(gold.manifest)),
        prompts=prompts,
        warmup=first,
        singles=singles,
        cap_sessions=caps,
        probes=probes,
        hashes=_hashes(prompts, cap_sessions, probe_items, requests, caps),
    )
