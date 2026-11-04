"""The model-server client (bake-off harness H2).

A synchronous httpx client for the pinned llama-server's OpenAI-compatible API, used only by the harness. It returns
records and writes nothing: the H3 runner stores them outside OneDrive (S2 §3.12). It holds no prompt text: messages
and the repair wording arrive from H5's frozen prompts.

Implements:

- S2 §3.2, environment: the server binds 127.0.0.1 in a loopback-only namespace, so `http://127.0.0.1:<port>` is the
  only base URL accepted, and proxy, netrc and SSL certificate settings are never read from the environment. The
  harness sends no tools and uses no function calling.
- S2 §3.3, launch line: every timed request sends `cache_prompt: false`. `fetch_props` reads the context per slot for
  the pre-run record.
- S2 §3.5, context: "every request uses no more than 90% of the context". `uses` is the server's count of the exact
  request body plus the output cap, and it fits when `10 * uses <= 9 * n_ctx`, so 11,059 at 12,288. The count comes
  from `/v1/chat/completions/input_tokens`, which applies the chat template, `chat_template_kwargs` and
  `response_format` exactly as the completion does. H3 calls `check_context` and `repair_bound` before any run; the
  client itself checks every repair before sending it.
- S2 §3.6, thinking mode: off through `chat_template_kwargs`, with a JSON boolean. Each attempt records whether
  thinking text appeared, in the content or in the reasoning field. That alone is never a reason to retry.
- S2 §3.7, sampling: temperature 0, seed 42, top-k 1, no repetition penalty, and output caps of 1,024, 4,096 and 384.
- S2 §3.8, structured output and retry: the schema travels as `response_format.json_schema.schema`. A call makes at
  most two attempts. Invalid output, including output cut off at the cap, gets the repair request: the original
  messages, the invalid output and the validation error text, at the same settings. A timed-out or failed attempt is
  retried with the same request. The scored output is the retry's, and both attempts' times count. The unconstrained
  diagnostic pass only leaves out `response_format`.
- S2 §3.9, timeouts: 30 s per attempt for single conversations and probe calls, 300 s for cap sessions.
- PHASE-0 §6.6, prompt-injection containment: no tools reach the server, output is schema-constrained and validated,
  and citation ids are checked against the supplied list.
- PHASE-0 §7: `FACTCHECK_LLM_TIMEOUT_SEC` is 30.0.

How a timeout maps to an attempt: `httpx.Timeout(T)` limits connect, write, read and pool each on its own, not their
sum, but the pinned server sends nothing until generation ends, so for a non-streaming request the read limit is the
per-attempt limit in effect. An attempt is a timeout only when httpx raises one. A response that arrives is never
reclassified by its wall time, and wall times are never clipped.

A failed token count or `/props` call and a repair that does not fit raise `ClientFault`, which is a run condition for
H3 to log, never a model failure. Only `ExtractionInvalid` from a validator fails an attempt; any other exception is a
harness bug and propagates.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from collections.abc import Callable
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, TypeVar, cast

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .extraction import (
    CUT_OFF_TEXT,
    FAILURE_KINDS,
    INVALID_KINDS,
    Extraction,
    ExtractionFailure,
    ExtractionInvalid,
    ExtractionResult,
    FailureKind,
    _check_optional_str,
    _decode,
    _error_path,
    _escaped,
    _load_json,
    parse_extraction,
)
from .probe import MAX_SOURCES_PER_CLAIM, ProbeOutput, parse_probe, readable_citation_ids
from .schemas import canonical_json, canonical_sha256, extraction_schema, probe_schema

RequestKind = Literal["single_conversation_extraction", "cap_session_extraction", "reasoning_probe"]
REQUEST_KINDS = ("single_conversation_extraction", "cap_session_extraction", "reasoning_probe")
EXTRACTION_KINDS = REQUEST_KINDS[:2]
Role = Literal["system", "user", "assistant"]
ROLES = ("system", "user", "assistant")
RetryType = Literal["none", "repair", "same_request"]
ContextLabel = Literal["request", "repair", "repair_bound"]
Outcome = Literal["valid", "invalid_output", "cut_off", "timeout", "server_error"]
OUTCOMES = ("valid",) + FAILURE_KINDS

MAX_TOKENS = MappingProxyType(
    {"single_conversation_extraction": 1024, "cap_session_extraction": 4096, "reasoning_probe": 384}
)
LLM_TIMEOUT_SEC = 30.0  # FACTCHECK_LLM_TIMEOUT_SEC (PHASE-0 §7)
CAP_SESSION_TIMEOUT_SEC = 300.0
TIMEOUT_SEC = MappingProxyType(
    {
        "single_conversation_extraction": LLM_TIMEOUT_SEC,
        "cap_session_extraction": CAP_SESSION_TIMEOUT_SEC,
        "reasoning_probe": LLM_TIMEOUT_SEC,
    }
)
CHECK_TIMEOUT_SEC = 30.0  # token count and /props
TEMPERATURE = 0.0
SEED = 42
TOP_K = 1
REPEAT_PENALTY = 1.0
CONTEXT_TOKENS = 12_288
CONTEXT_LIMIT = 11_059  # == CONTEXT_TOKENS * 9 // 10
CHAT_PATH = "/v1/chat/completions"
COUNT_PATH = "/v1/chat/completions/input_tokens"
PROPS_PATH = "/props"
BASE_URL_PATTERN = re.compile(r"http://127[.]0[.]0[.]1:([1-9][0-9]{0,4})")  # fullmatch only
THINK_TAGS = ("<think>", "</think>")
REASONING_BLANK = " \n\r\t"  # the whitespace the pinned server drops from a reasoning field
REPAIR_PLACEHOLDER = "<<VALIDATION_ERRORS>>"

_RETRY_TYPES = ("none", "repair", "same_request")
_CONTEXT_LABELS = ("request", "repair", "repair_bound")
_FINISH_REASONS = ("stop", "length", None)
_ROUTES = frozenset({("POST", CHAT_PATH), ("POST", COUNT_PATH), ("GET", PROPS_PATH)})
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One chat message. On the wire it is exactly `{"content": ..., "role": ...}`."""

    role: Role
    content: str

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"ChatMessage.role must be one of {ROLES}, not {self.role!r}")
        if not isinstance(self.content, str):
            raise TypeError(f"ChatMessage.content must be a string, not {type(self.content).__name__}")


@dataclass(frozen=True, slots=True)
class ChatRequest:
    """One chat-completion request. The kind alone fixes the output cap, the timeout and the schema, so none of them
    can differ between attempts, passes or candidates. `constrained=False` is the diagnostic pass (S2 §3.8)."""

    kind: RequestKind
    messages: tuple[ChatMessage, ...]
    constrained: bool = True

    def __post_init__(self) -> None:
        if self.kind not in REQUEST_KINDS:
            raise ValueError(f"ChatRequest.kind must be one of {REQUEST_KINDS}, not {self.kind!r}")
        if not isinstance(self.messages, tuple) or not self.messages:
            raise ValueError("ChatRequest.messages must be a non-empty tuple")
        if not all(isinstance(message, ChatMessage) for message in self.messages):
            raise ValueError("ChatRequest.messages must hold ChatMessage values")
        if self.messages[-1].role != "user":
            raise ValueError("ChatRequest.messages must end with a user message: the server continues an assistant one")
        if not isinstance(self.constrained, bool):
            raise ValueError("ChatRequest.constrained must be a bool")

    @property
    def max_tokens(self) -> int:
        return MAX_TOKENS[self.kind]

    @property
    def timeout_sec(self) -> float:
        return TIMEOUT_SEC[self.kind]

    def body(self) -> dict[str, Any]:
        """A fresh plain dict of the request body. The pinned server drops a misplaced schema without an error (any
        object then passes), and takes `0` for false as nothing at all, so the nesting and JSON types are exact."""
        body: dict[str, Any] = {
            "cache_prompt": False,
            "chat_template_kwargs": {"enable_thinking": False},
            "max_tokens": self.max_tokens,
            "messages": [{"content": message.content, "role": message.role} for message in self.messages],
            "repeat_penalty": REPEAT_PENALTY,
            "seed": SEED,
            "stream": False,
            "temperature": TEMPERATURE,
            "top_k": TOP_K,
        }
        if self.constrained:
            schema = extraction_schema() if self.kind in EXTRACTION_KINDS else probe_schema()
            body["response_format"] = {"type": "json_schema", "json_schema": {"schema": schema}}
        return body

    def body_bytes(self) -> bytes:
        """The exact bytes sent: canonical JSON, which is what the records hash."""
        return canonical_json(self.body())

    def sha256(self) -> str:
        return canonical_sha256(self.body())


@dataclass(frozen=True, slots=True)
class RepairTemplate:
    """The repair wording, frozen and hashed with the prompts (S2 §3.8). H5 supplies the text; `REPAIR_PLACEHOLDER`
    marks where the validation error text goes, exactly once."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or self.text.count(REPAIR_PLACEHOLDER) != 1:
            raise ValueError(f"RepairTemplate.text must contain {REPAIR_PLACEHOLDER} exactly once")

    def render(self, error_text: str) -> str:
        """Plain one-pass substitution: H5's wording may hold JSON braces, so `str.format` is never used."""
        return self.text.replace(REPAIR_PLACEHOLDER, error_text, 1)

    def sha256(self) -> str:
        return canonical_sha256(self.text)


def repair_request(
    original: ChatRequest, *, invalid_output: str, error_text: str, template: RepairTemplate
) -> ChatRequest:
    """S2 §3.8: the original messages, the invalid output and the validation error text, at the same settings. The
    invalid output goes back exactly as received, and reasoning text never does (PHASE-0 §2.2 invariant 3)."""
    turns = (ChatMessage("assistant", invalid_output), ChatMessage("user", template.render(error_text)))
    return ChatRequest(original.kind, original.messages + turns, original.constrained)


def validate_base_url(base_url: str) -> str:
    """Return `base_url` unchanged if it is exactly `http://127.0.0.1:<port>` (S2 §3.2). `fullmatch` and `[0-9]` matter:
    `re.match` with `$` accepts a trailing line feed, and `\\d` accepts non-ASCII digits."""
    if not isinstance(base_url, str):
        raise TypeError(f"base_url must be a string, not {type(base_url).__name__}")
    match = BASE_URL_PATTERN.fullmatch(base_url)
    if match is None or not 1 <= int(match.group(1)) <= 65535:
        raise ValueError(f"base_url must be http://127.0.0.1:<port>, not {base_url!r}")
    return base_url


def context_fits(uses: int, n_ctx: int = CONTEXT_TOKENS) -> bool:
    """S2 §3.5: no more than 90% of the context, in integer arithmetic."""
    return 10 * uses <= 9 * n_ctx


def _is_count(value: object) -> bool:
    """An int >= 0. A `bool` is an `int` in Python, so it is refused by name."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: object) -> bool:
    """A finite int or float, not a `bool`: a record must pass `canonical_json`, which refuses NaN and infinities."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return isinstance(value, int) or math.isfinite(value)


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _is_str_tuple(value: object) -> bool:
    return isinstance(value, tuple) and all(isinstance(item, str) for item in value)


def _require_counts(owner: object, names: tuple[str, ...]) -> None:
    for name in names:
        if not _is_count(getattr(owner, name)):
            raise ValueError(f"{type(owner).__name__}.{name} must be an int >= 0")


@dataclass(frozen=True, slots=True)
class ThinkingCheck:
    """Whether thinking text appeared in one response (S2 §3.6)."""

    in_reasoning: bool
    in_content: bool

    def __post_init__(self) -> None:
        if not isinstance(self.in_reasoning, bool) or not isinstance(self.in_content, bool):
            raise ValueError("ThinkingCheck fields must be bools")

    @property
    def seen(self) -> bool:
        return self.in_reasoning or self.in_content


def _tag_outside_strings(content: str) -> bool:
    """Whether a think tag starts outside every JSON string literal. Inside a string a backslash skips one character."""
    in_string = False
    position = 0
    while position < len(content):
        char = content[position]
        if in_string and char == "\\":
            position += 2
            continue
        if char == '"':
            in_string = not in_string
        elif not in_string and content.startswith(THINK_TAGS, position):
            return True
        position += 1
    return False


def thinking_check(content: str, reasoning_content: str | None) -> ThinkingCheck:
    """S2 §3.6, on one well-formed response. Whitespace alone in the reasoning field is no thinking text: the pinned
    server drops exactly that whitespace. A tag quoted inside a JSON string is claim text, not thinking, and must not
    cost a candidate its eligibility. Tags are exact and case-sensitive."""
    in_reasoning = reasoning_content is not None and reasoning_content.strip(REASONING_BLANK) != ""
    if content.lstrip(REASONING_BLANK).startswith(("{", "[")):
        return ThinkingCheck(in_reasoning, _tag_outside_strings(content))
    return ThinkingCheck(in_reasoning, any(tag in content for tag in THINK_TAGS))


@dataclass(frozen=True, slots=True)
class Usage:
    """`cached_tokens` is `None` when the server sent no `prompt_tokens_details`. With `cache_prompt` false it should
    be 0; a nonzero value is recorded as received, and H3 treats it as an infrastructure fault."""

    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int | None

    def __post_init__(self) -> None:
        _require_counts(self, ("prompt_tokens", "completion_tokens"))
        if self.cached_tokens is not None and not _is_count(self.cached_tokens):
            raise ValueError("Usage.cached_tokens must be an int >= 0 or None")


@dataclass(frozen=True, slots=True)
class Timings:
    """The server's own `timings` block. The two draft counts appear only with speculative decoding."""

    cache_n: int
    prompt_n: int
    prompt_ms: float
    prompt_per_token_ms: float
    prompt_per_second: float
    predicted_n: int
    predicted_ms: float
    predicted_per_token_ms: float
    predicted_per_second: float
    draft_n: int | None = None
    draft_n_accepted: int | None = None

    def __post_init__(self) -> None:
        _require_counts(self, ("cache_n", "prompt_n", "predicted_n"))
        for name in (
            "prompt_ms",
            "prompt_per_token_ms",
            "prompt_per_second",
            "predicted_ms",
            "predicted_per_token_ms",
            "predicted_per_second",
        ):
            if not _is_number(getattr(self, name)):
                raise ValueError(f"Timings.{name} must be a finite number")
        for name in ("draft_n", "draft_n_accepted"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"Timings.{name} must be an int or None")


@dataclass(frozen=True, slots=True)
class ContextCheck:
    """One S2 §3.5 check. `uses = input_tokens + reserved_tokens + max_tokens`: the server's count of the exact body,
    room reserved for output the count cannot see yet, and the output cap. `limit` is 90% of `n_ctx`, rounded down.
    `wall_ns` is the count's own time; it is not model time and is kept out of a call's total."""

    label: ContextLabel
    request_sha256: str
    input_tokens: int
    reserved_tokens: int
    max_tokens: int
    uses: int
    n_ctx: int
    limit: int
    fits: bool
    wall_ns: int

    def __post_init__(self) -> None:
        if self.label not in _CONTEXT_LABELS:
            raise ValueError(f"ContextCheck.label must be one of {_CONTEXT_LABELS}, not {self.label!r}")
        if not _is_sha256(self.request_sha256):
            raise ValueError("ContextCheck.request_sha256 must be 64 lowercase hex characters")
        _require_counts(self, ("input_tokens", "reserved_tokens", "max_tokens", "uses", "n_ctx", "limit", "wall_ns"))
        if self.uses != self.input_tokens + self.reserved_tokens + self.max_tokens:
            raise ValueError("ContextCheck.uses must be input_tokens + reserved_tokens + max_tokens")
        if self.limit != self.n_ctx * 9 // 10:
            raise ValueError("ContextCheck.limit must be 90% of n_ctx, rounded down")
        if self.fits is not context_fits(self.uses, self.n_ctx):
            raise ValueError("ContextCheck.fits must equal context_fits(uses, n_ctx)")


@dataclass(frozen=True, slots=True)
class ServerProps:
    """What `GET /props` reports, for H3's pre-run record. H2 never takes a limit from it."""

    n_ctx: int
    total_slots: int
    model_alias: str
    model_path: str
    build_info: str
    chat_template: str

    def __post_init__(self) -> None:
        for name in ("n_ctx", "total_slots"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"ServerProps.{name} must be an int")
        for name in ("model_alias", "model_path", "build_info", "chat_template"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"ServerProps.{name} must be a string")


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One attempt, whatever became of it. Every field is required.

    `request_sha256` is the hash of the exact bytes sent. `thinking` is `None` when there was no response to check,
    which must not read as checked and clean. `cited_source_ids` is gate C's evidence on a finished probe response:
    `None` when the citations cannot be read, or the attempt is not a finished probe response.
    `server_error_type` is the OpenAI error `type` of a non-200.
    """

    request_kind: RequestKind
    constrained: bool
    attempt: Literal[1, 2]
    retry_type: RetryType
    request_sha256: str
    max_tokens: int
    timeout_sec: float
    wall_ns: int
    http_status: int | None
    finish_reason: Literal["stop", "length"] | None
    usage: Usage | None
    timings: Timings | None
    server_model: str | None
    system_fingerprint: str | None
    content: str | None
    reasoning_content: str | None
    thinking: ThinkingCheck | None
    cited_source_ids: tuple[str, ...] | None
    unknown_source_ids: tuple[str, ...]
    failure_kind: FailureKind | None
    error_text: str
    server_error_type: str | None

    def __post_init__(self) -> None:
        self._check_request()
        self._check_optional_fields()
        self._check_response()
        self._check_citations()

    def _check_request(self) -> None:
        if self.request_kind not in REQUEST_KINDS:
            raise ValueError(f"AttemptRecord.request_kind must be one of {REQUEST_KINDS}, not {self.request_kind!r}")
        if not isinstance(self.constrained, bool):
            raise ValueError("AttemptRecord.constrained must be a bool")
        if not _is_count(self.attempt) or self.attempt not in (1, 2):
            raise ValueError("AttemptRecord.attempt must be 1 or 2")
        if self.retry_type not in _RETRY_TYPES or (self.attempt == 1) != (self.retry_type == "none"):
            raise ValueError("AttemptRecord.retry_type must be 'none' on attempt 1 and only there")
        if not _is_sha256(self.request_sha256):
            raise ValueError("AttemptRecord.request_sha256 must be 64 lowercase hex characters")
        if not _is_count(self.max_tokens) or self.max_tokens != MAX_TOKENS[self.request_kind]:
            raise ValueError(f"AttemptRecord.max_tokens must be {MAX_TOKENS[self.request_kind]}")
        if not isinstance(self.timeout_sec, float) or self.timeout_sec != TIMEOUT_SEC[self.request_kind]:
            raise ValueError(f"AttemptRecord.timeout_sec must be {TIMEOUT_SEC[self.request_kind]}")
        _require_counts(self, ("wall_ns",))

    def _check_optional_fields(self) -> None:
        """Each optional field is its declared type or `None`, so a rebuilt record cannot hide a wrong one."""
        if self.http_status is not None and not _is_count(self.http_status):
            raise ValueError("AttemptRecord.http_status must be an int or None")
        for name, record in (("usage", Usage), ("timings", Timings), ("thinking", ThinkingCheck)):
            value = getattr(self, name)
            if value is not None and not isinstance(value, record):
                raise ValueError(f"AttemptRecord.{name} must be a {record.__name__} or None")
        for name in ("server_model", "system_fingerprint", "content", "reasoning_content", "server_error_type"):
            _check_optional_str("AttemptRecord", name, getattr(self, name))

    def _check_response(self) -> None:
        kind = self.failure_kind
        if kind is not None and kind not in FAILURE_KINDS:
            raise ValueError(f"AttemptRecord.failure_kind must be None or one of {FAILURE_KINDS}, not {kind!r}")
        if not isinstance(self.error_text, str) or (kind is None) != (self.error_text == ""):
            raise ValueError("AttemptRecord.error_text must be empty exactly when the attempt is valid")
        if self.finish_reason not in _FINISH_REASONS:
            raise ValueError(f"AttemptRecord.finish_reason must be one of {_FINISH_REASONS}")
        if (kind == "cut_off") != (self.finish_reason == "length"):
            raise ValueError("AttemptRecord.finish_reason must be 'length' exactly when the attempt is cut off")
        response = (
            self.content,
            self.finish_reason,
            self.usage,
            self.server_model,
            self.system_fingerprint,
            self.thinking,
        )
        if kind is None or kind in INVALID_KINDS:
            if self.http_status != 200 or any(field is None for field in response):
                raise ValueError("AttemptRecord: a validated attempt must hold its HTTP 200 response")
        elif any(field is not None for field in (*response, self.reasoning_content, self.timings)):
            raise ValueError("AttemptRecord: a timeout or server error must hold no response")
        if kind == "timeout" and self.http_status is not None:
            raise ValueError("AttemptRecord: a timeout has no HTTP status")
        if self.server_error_type is not None and (kind != "server_error" or self.http_status in (None, 200)):
            raise ValueError("AttemptRecord.server_error_type belongs to a server error with a non-200 status")

    def _check_citations(self) -> None:
        cited = self.cited_source_ids
        if cited is not None and not _is_str_tuple(cited):
            raise ValueError("AttemptRecord.cited_source_ids must be None or a tuple of strings")
        if cited is not None and (self.request_kind != "reasoning_probe" or self.finish_reason != "stop"):
            raise ValueError("AttemptRecord.cited_source_ids belongs to a finished probe response")
        if not _is_str_tuple(self.unknown_source_ids) or not set(self.unknown_source_ids) <= set(cited or ()):
            raise ValueError("AttemptRecord.unknown_source_ids must be a tuple of cited ids")


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One call: its one or two attempts and the result that counts. The scored result is the last attempt's, and
    `total_wall_ns` adds both attempts (S2 §3.8, §6 gate T). `context_checks` holds the check before a repair."""

    item_id: str
    request_kind: RequestKind
    constrained: bool
    attempts: tuple[AttemptRecord, ...]
    context_checks: tuple[ContextCheck, ...]
    outcome: Outcome
    output: Extraction | ProbeOutput | None
    total_wall_ns: int

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id:
            raise ValueError("CallRecord.item_id must be a non-empty string")
        self._check_attempts()
        self._check_context_checks()
        self._check_result()

    def _check_attempts(self) -> None:
        attempts = self.attempts
        if not isinstance(attempts, tuple) or len(attempts) not in (1, 2):
            raise ValueError("CallRecord.attempts must be a tuple of 1 or 2 attempts")
        if not all(isinstance(attempt, AttemptRecord) for attempt in attempts):
            raise ValueError("CallRecord.attempts must hold AttemptRecord values")
        for number, attempt in enumerate(attempts, start=1):
            if attempt.attempt != number:
                raise ValueError(f"CallRecord.attempts[{number - 1}] is attempt {attempt.attempt}")
            if attempt.request_kind != self.request_kind or attempt.constrained is not self.constrained:
                raise ValueError("CallRecord.attempts must share the call's kind and constrained flag")
        first = attempts[0]
        if (len(attempts) == 2) != (first.failure_kind is not None):
            raise ValueError("CallRecord: a second attempt exists exactly when the first failed")
        if len(attempts) == 2:
            expected = "repair" if first.failure_kind in INVALID_KINDS else "same_request"
            if attempts[1].retry_type != expected:
                raise ValueError(f"CallRecord: after {first.failure_kind} the retry must be {expected}")
            if (attempts[1].request_sha256 == first.request_sha256) != (expected == "same_request"):
                raise ValueError("CallRecord: a same-request retry repeats the bytes and a repair changes them")

    def _check_context_checks(self) -> None:
        checks = self.context_checks
        if not isinstance(checks, tuple):
            raise ValueError("CallRecord.context_checks must be a tuple")
        last = self.attempts[-1]
        if last.retry_type != "repair":
            if checks != ():
                raise ValueError("CallRecord.context_checks must be empty unless attempt 2 is a repair")
            return
        if len(checks) != 1 or not isinstance(checks[0], ContextCheck):
            raise ValueError("CallRecord: a repair must hold exactly one context check")
        check = checks[0]
        if check.label != "repair" or not check.fits or check.request_sha256 != last.request_sha256:
            raise ValueError("CallRecord: the context check must be the repair's own, and it must fit")

    def _check_result(self) -> None:
        last = self.attempts[-1]
        if self.outcome != (last.failure_kind or "valid"):
            raise ValueError("CallRecord.outcome must be 'valid' or the last attempt's failure kind")
        if (self.output is not None) != (self.outcome == "valid"):
            raise ValueError("CallRecord.output must be set exactly when the outcome is valid")
        if self.request_kind == "reasoning_probe":
            if self.output is not None and not isinstance(self.output, ProbeOutput):
                raise ValueError("CallRecord.output of a probe must be a ProbeOutput")
        elif self.output is not None and (
            not isinstance(self.output, Extraction) or self.output.source_id != self.item_id
        ):
            raise ValueError("CallRecord.output of an extraction must be an Extraction of this item")
        if not _is_count(self.total_wall_ns) or self.total_wall_ns != sum(attempt.wall_ns for attempt in self.attempts):
            raise ValueError("CallRecord.total_wall_ns must be the sum of the attempts' wall_ns")

    @property
    def first_attempt_failed(self) -> bool:
        return self.attempts[0].failure_kind is not None

    @property
    def cut_off_attempts(self) -> int:
        return sum(attempt.failure_kind == "cut_off" for attempt in self.attempts)

    @property
    def thinking_seen(self) -> bool:
        return any(attempt.thinking is not None and attempt.thinking.seen for attempt in self.attempts)

    def extraction_result(self) -> ExtractionResult:
        """What scoring takes (S2 §5.3): the output, or a failure that scores as empty output."""
        if self.request_kind not in EXTRACTION_KINDS:
            raise ValueError("extraction_result needs an extraction call, not a reasoning probe")
        if isinstance(self.output, Extraction):
            return self.output
        return ExtractionFailure(self.item_id, cast(FailureKind, self.outcome), detail=self.attempts[-1].error_text)


class ClientFault(RuntimeError):
    """A run condition, never a model failure: H3 logs it before scoring (S2 §3.13). The client never catches one."""


class ServerCheckError(ClientFault):
    """A token-count or `/props` call failed. `attempts` holds the attempt already made in this call, if any.
    `str(exc) == exc.error_text`."""

    def __init__(
        self, path: str, error_text: str, http_status: int | None = None, attempts: tuple[AttemptRecord, ...] = ()
    ) -> None:
        super().__init__(error_text)
        self.path = path
        self.error_text = error_text
        self.http_status = http_status
        self.attempts = attempts

    def __reduce__(self) -> tuple[Any, ...]:
        # pickle and copy rebuild an exception from `args`, which holds the error text only; rebuild from every field.
        return (type(self), (self.path, self.error_text, self.http_status, self.attempts), self.__dict__)


class ContextBudgetExceeded(ClientFault):
    """A repair request does not fit S2 §3.5's limit, so it was not sent. `attempts` holds attempt 1."""

    def __init__(self, check: ContextCheck, attempts: tuple[AttemptRecord, ...]) -> None:
        super().__init__(f"repair request uses {check.uses} tokens, limit {check.limit}")
        self.check = check
        self.attempts = attempts

    def __reduce__(self) -> tuple[Any, ...]:
        # As ServerCheckError: `args` holds only the message.
        return (type(self), (self.check, self.attempts), self.__dict__)


class _Resp(BaseModel):
    """Strict types for every field the harness reads; keys it does not read are ignored. `json.loads` turns `1e999`
    into an infinity that no literal check sees, and a record holding one could not be stored, so finite only."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True, allow_inf_nan=False)


class _MessageOut(BaseModel):
    """With no tools an assistant message has only these keys, so anything else is a malformed response."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    role: Literal["assistant"]
    content: str
    reasoning_content: str | None = None


class _ChoiceOut(_Resp):
    finish_reason: Literal["stop", "length"]
    message: _MessageOut


class _PromptDetailsOut(_Resp):
    cached_tokens: int = Field(ge=0)


class _UsageOut(_Resp):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    prompt_tokens_details: _PromptDetailsOut | None = None


class _TimingsOut(_Resp):
    cache_n: int = Field(ge=0)
    prompt_n: int = Field(ge=0)
    prompt_ms: float
    prompt_per_token_ms: float
    prompt_per_second: float
    predicted_n: int = Field(ge=0)
    predicted_ms: float
    predicted_per_token_ms: float
    predicted_per_second: float
    draft_n: int | None = None
    draft_n_accepted: int | None = None


class _CompletionOut(_Resp):
    model: str
    system_fingerprint: str
    choices: list[_ChoiceOut] = Field(min_length=1, max_length=1)
    usage: _UsageOut
    timings: _TimingsOut | None = None


class _InputTokensOut(_Resp):
    input_tokens: int = Field(ge=0)


class _ErrorBody(_Resp):
    message: str
    type: str
    code: int | None = None


class _ErrorOut(_Resp):
    error: _ErrorBody


class _SettingsOut(_Resp):
    n_ctx: int


class _PropsOut(_Resp):
    default_generation_settings: _SettingsOut
    total_slots: int
    model_alias: str
    model_path: str
    build_info: str
    chat_template: str


_Model = TypeVar("_Model", bound=BaseModel)


class _Malformed(Exception):
    """A 200 whose body cannot be used. Not a ValueError, so it never mixes with the validators' exceptions."""


@dataclass(frozen=True, slots=True)
class _Fault:
    """Why an exchange gave no usable response."""

    kind: FailureKind
    text: str
    http_status: int | None = None
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class _Exchange:
    """One timed HTTP exchange: the body of a 200, or the fault."""

    wall_ns: int
    body: bytes = b""
    fault: _Fault | None = None


def _load_body(raw: bytes, model: type[_Model]) -> _Model:
    """H1's strict JSON rules, then the model. Pydantic's own JSON parser is not used: it keeps the last of two repeated
    keys and accepts NaN, which H1's rules reject."""
    try:
        data = _load_json(_decode(raw))
    except ExtractionInvalid as exc:
        raise _Malformed(exc.error_text) from None
    if not isinstance(data, dict):
        raise _Malformed("(root): response must be a JSON object")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        raise _Malformed("; ".join(f"{_error_path(error['loc'])}: {error['msg']}" for error in errors)) from None


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:  # a JSON escape can decode to a lone surrogate, which no request body can carry
        return False
    return True


def _parse_completion(raw: bytes, max_tokens: int) -> _CompletionOut:
    """A well-formed 200 (S2 §3.7 for the cap: the pinned server never generates past it)."""
    completion = _load_body(raw, _CompletionOut)
    if completion.usage.completion_tokens > max_tokens:
        raise _Malformed(
            f"usage.completion_tokens {completion.usage.completion_tokens} exceeds max_tokens {max_tokens}"
        )
    message = completion.choices[0].message
    for path, text in (
        ("choices.0.message.content", message.content),
        ("choices.0.message.reasoning_content", message.reasoning_content),
        ("model", completion.model),
        ("system_fingerprint", completion.system_fingerprint),
    ):
        if text is not None and not _encodable(text):
            raise _Malformed(f"{path} is not valid Unicode")
    return completion


def _read_completion(exchange: _Exchange, max_tokens: int) -> _CompletionOut | _Fault:
    if exchange.fault is not None:
        return exchange.fault
    try:
        return _parse_completion(exchange.body, max_tokens)
    except _Malformed as exc:
        return _Fault("server_error", f"malformed response: {exc}", 200)


def _transport_fault(exc: httpx.RequestError, timeout_sec: float) -> _Fault:
    name = type(exc).__name__
    if isinstance(exc, httpx.TimeoutException):
        return _Fault("timeout", f"timeout: {name} after the {timeout_sec:g} s limit")
    return _Fault("server_error", f"transport error: {name}: {_escaped(str(exc))}")


def _http_fault(status: int, raw: bytes) -> _Fault:
    """A non-200. The server's error shape when the body has it; otherwise an excerpt, because its last-resort error
    body is plain text."""
    try:
        error = _load_body(raw, _ErrorOut).error
    except _Malformed:
        if not raw:
            return _Fault("server_error", f"HTTP {status}: (empty body)", status)
        text = raw.decode("utf-8", errors="replace")
        excerpt = _escaped(text[:500]) + ("..." if len(text) > 500 else "")
        return _Fault("server_error", f"HTTP {status}: {excerpt}", status)
    error_type = _escaped(error.type)  # a record must pass canonical_json; the pinned server's types are plain ASCII
    return _Fault("server_error", f"HTTP {status}: {error_type}: {_escaped(error.message)}", status, error_type)


def _failed_attempt(sent: dict[str, Any], fault: _Fault) -> AttemptRecord:
    """An attempt with no usable response: nothing but the fault is recorded, and no validator ran."""
    return AttemptRecord(
        **sent,
        http_status=fault.http_status,
        finish_reason=None,
        usage=None,
        timings=None,
        server_model=None,
        system_fingerprint=None,
        content=None,
        reasoning_content=None,
        thinking=None,
        cited_source_ids=None,
        unknown_source_ids=(),
        failure_kind=fault.kind,
        error_text=fault.text,
        server_error_type=fault.error_type,
    )


def _check_call(request: ChatRequest, kinds: tuple[str, ...], item_id: str, repair_template: RepairTemplate) -> None:
    """The arguments every call shares, checked before anything is sent."""
    if not isinstance(request, ChatRequest):
        raise TypeError(f"request must be a ChatRequest, not {type(request).__name__}")
    if request.kind not in kinds:
        raise ValueError(f"request.kind must be one of {kinds}, not {request.kind!r}")
    if not isinstance(item_id, str):
        raise TypeError(f"item_id must be a string, not {type(item_id).__name__}")
    if not item_id:
        raise ValueError("item_id must not be empty")
    if not isinstance(repair_template, RepairTemplate):
        raise TypeError(f"repair_template must be a RepairTemplate, not {type(repair_template).__name__}")


_Validator = Callable[[str, str], Extraction | ProbeOutput]


class ModelServerClient:
    """The client for one llama-server. `transport` exists for tests and replaces nothing but the transport.

    `n_ctx` is the `-c` value of the server under test and is never read from the server. Keep-alive is off and one
    connection is used at a time, so an idle connection the server closed can never turn into a spurious server error.
    """

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], int] = time.perf_counter_ns,
        n_ctx: int = CONTEXT_TOKENS,
    ) -> None:
        self._base_url = validate_base_url(base_url)  # first, so a rejected URL reaches neither DNS nor a socket
        if isinstance(n_ctx, bool) or not isinstance(n_ctx, int):
            raise TypeError(f"n_ctx must be an int, not {type(n_ctx).__name__}")
        if n_ctx <= 0:
            raise ValueError(f"n_ctx must be above 0, not {n_ctx}")
        self._n_ctx = n_ctx
        self._clock = clock
        if transport is None:
            # The transport's own trust_env defaults to True, so the client's setting alone is not enough.
            transport = httpx.HTTPTransport(
                retries=0,
                trust_env=False,
                http1=True,
                http2=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            )
        self._http = httpx.Client(
            base_url=self._base_url,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(CHECK_TIMEOUT_SEC),
        )

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def n_ctx(self) -> int:
        return self._n_ctx

    @property
    def limit(self) -> int:
        return self._n_ctx * 9 // 10

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ModelServerClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def extract(
        self,
        request: ChatRequest,
        *,
        item_id: str,
        request_segment_ids: AbstractSet[str],
        repair_template: RepairTemplate,
    ) -> CallRecord:
        """One extraction call, validated by H1's `parse_extraction` (S2 §3.8). `request_segment_ids` is every segment
        id of the request; for a cap session those are the re-keyed ids (S2 §3.11)."""
        _check_call(request, EXTRACTION_KINDS, item_id, repair_template)
        if not isinstance(request_segment_ids, AbstractSet):
            raise TypeError("request_segment_ids must be a set of segment ids")
        if not request_segment_ids or not all(isinstance(segment_id, str) for segment_id in request_segment_ids):
            raise ValueError("request_segment_ids must be a non-empty set of strings")
        segment_ids = frozenset(request_segment_ids)

        def validate(content: str, finish_reason: str) -> Extraction:
            return parse_extraction(
                content, source_id=item_id, request_segment_ids=segment_ids, finish_reason=finish_reason
            )

        return self._call(request, item_id, validate, repair_template, None)

    def probe(
        self,
        request: ChatRequest,
        *,
        item_id: str,
        bundle_source_ids: AbstractSet[str],
        repair_template: RepairTemplate,
    ) -> CallRecord:
        """One reasoning-probe call, validated by `parse_probe` against its bundle (S2 §4)."""
        _check_call(request, ("reasoning_probe",), item_id, repair_template)
        if not isinstance(bundle_source_ids, AbstractSet):
            raise TypeError("bundle_source_ids must be a set of source ids")
        if not bundle_source_ids or len(bundle_source_ids) > MAX_SOURCES_PER_CLAIM:
            raise ValueError(f"bundle_source_ids must hold 1 to {MAX_SOURCES_PER_CLAIM} ids")
        if not all(isinstance(source_id, str) and source_id for source_id in bundle_source_ids):
            raise ValueError("bundle_source_ids must hold non-empty strings")
        bundle = frozenset(bundle_source_ids)

        def validate(content: str, finish_reason: str) -> ProbeOutput:
            return parse_probe(content, bundle_source_ids=bundle, finish_reason=finish_reason)

        return self._call(request, item_id, validate, repair_template, bundle)

    def check_context(self, request: ChatRequest) -> ContextCheck:
        """S2 §3.5, for one frozen first-attempt request. `fits` False is an answer, not an error: H3 stops before any
        run. It raises `ServerCheckError` only when the count itself fails."""
        if not isinstance(request, ChatRequest):
            raise TypeError(f"request must be a ChatRequest, not {type(request).__name__}")
        return self._count(request, label="request")

    def repair_bound(self, request: ChatRequest, template: RepairTemplate) -> ContextCheck:
        """The size of `request`'s repair after a cut-off: its messages, an output of `max_tokens` tokens echoed back
        (counted as reserved, since it cannot be built yet) and `max_tokens` of new output. An estimate, not a ceiling:
        the error text of a complete invalid output has no limit of its own, so a real repair can be larger, and the
        check before each real repair stays authoritative."""
        if not isinstance(request, ChatRequest):
            raise TypeError(f"request must be a ChatRequest, not {type(request).__name__}")
        if not isinstance(template, RepairTemplate):
            raise TypeError(f"template must be a RepairTemplate, not {type(template).__name__}")
        # NEEDS-OWNER(AMB-29): the repair size after a cut-off; H3 stops before any run when it does not fit
        repair = repair_request(request, invalid_output="", error_text=CUT_OFF_TEXT, template=template)
        return self._count(repair, label="repair_bound", reserved_tokens=request.max_tokens)

    def fetch_props(self) -> ServerProps:
        """`GET /props` for H3's pre-run record (S2 §3.3). Any failure raises `ServerCheckError`."""
        exchange = self._exchange("GET", PROPS_PATH, None, CHECK_TIMEOUT_SEC)
        if exchange.fault is not None:
            raise ServerCheckError(PROPS_PATH, f"props: {exchange.fault.text}", exchange.fault.http_status)
        try:
            props = _load_body(exchange.body, _PropsOut)
        except _Malformed as exc:
            raise ServerCheckError(PROPS_PATH, f"props: malformed response: {exc}", 200) from None
        for name in ("model_alias", "model_path", "build_info", "chat_template"):
            if not _encodable(getattr(props, name)):
                raise ServerCheckError(PROPS_PATH, f"props: malformed response: {name} is not valid Unicode", 200)
        return ServerProps(
            n_ctx=props.default_generation_settings.n_ctx,
            total_slots=props.total_slots,
            model_alias=props.model_alias,
            model_path=props.model_path,
            build_info=props.build_info,
            chat_template=props.chat_template,
        )

    def _exchange(self, method: str, path: str, content: bytes | None, timeout_sec: float) -> _Exchange:
        """One HTTP exchange. The body goes out as exactly `content`, never through httpx's `json=`, so the bytes sent
        are the bytes hashed. Encoding comes before the timed span and parsing after it (S2 §6 gate T). A non-200 is a
        fault, a 3xx included: nothing follows a redirect. Other exceptions are harness bugs and propagate."""
        if (method, path) not in _ROUTES:
            raise ValueError(f"no such exchange: {method} {path}")
        headers = None if content is None else {"Content-Type": "application/json"}
        timeout = httpx.Timeout(timeout_sec)
        started = self._clock()
        try:
            response = self._http.request(method, path, content=content, headers=headers, timeout=timeout)
        except httpx.RequestError as exc:
            wall_ns = self._clock() - started
            return _Exchange(wall_ns, fault=_transport_fault(exc, timeout_sec))
        wall_ns = self._clock() - started
        if response.status_code != 200:
            return _Exchange(wall_ns, fault=_http_fault(response.status_code, response.content))
        return _Exchange(wall_ns, body=response.content)

    def _count(
        self,
        request: ChatRequest,
        *,
        label: ContextLabel,
        reserved_tokens: int = 0,
        attempts: tuple[AttemptRecord, ...] = (),
    ) -> ContextCheck:
        """The server's count of `request`'s exact bytes. It equals the completion's `usage.prompt_tokens`."""
        raw = request.body_bytes()
        exchange = self._exchange("POST", COUNT_PATH, raw, CHECK_TIMEOUT_SEC)
        if exchange.fault is not None:
            text = f"token count: {exchange.fault.text}"
            raise ServerCheckError(COUNT_PATH, text, exchange.fault.http_status, attempts)
        try:
            input_tokens = _load_body(exchange.body, _InputTokensOut).input_tokens
        except _Malformed as exc:
            raise ServerCheckError(COUNT_PATH, f"token count: malformed response: {exc}", 200, attempts) from None
        uses = input_tokens + reserved_tokens + request.max_tokens
        return ContextCheck(
            label=label,
            request_sha256=hashlib.sha256(raw).hexdigest(),
            input_tokens=input_tokens,
            reserved_tokens=reserved_tokens,
            max_tokens=request.max_tokens,
            uses=uses,
            n_ctx=self._n_ctx,
            limit=self.limit,
            fits=context_fits(uses, self._n_ctx),
            wall_ns=exchange.wall_ns,
        )

    def _attempt(
        self,
        request: ChatRequest,
        number: Literal[1, 2],
        retry_type: RetryType,
        validate: _Validator,
        bundle: frozenset[str] | None,
    ) -> tuple[AttemptRecord, Extraction | ProbeOutput | None]:
        """Send one attempt and record it. `bundle` is set only for a probe."""
        raw = request.body_bytes()
        exchange = self._exchange("POST", CHAT_PATH, raw, request.timeout_sec)
        sent: dict[str, Any] = {
            "request_kind": request.kind,
            "constrained": request.constrained,
            "attempt": number,
            "retry_type": retry_type,
            "request_sha256": hashlib.sha256(raw).hexdigest(),
            "max_tokens": request.max_tokens,
            "timeout_sec": request.timeout_sec,
            "wall_ns": exchange.wall_ns,
        }
        completion = _read_completion(exchange, request.max_tokens)
        if isinstance(completion, _Fault):
            return _failed_attempt(sent, completion), None
        choice = completion.choices[0]
        content = choice.message.content
        reasoning_content = choice.message.reasoning_content
        details = completion.usage.prompt_tokens_details
        thinking = thinking_check(content, reasoning_content)
        cited: tuple[str, ...] | None = None
        unknown: tuple[str, ...] = ()
        if bundle is not None and choice.finish_reason == "stop":
            cited = readable_citation_ids(content)
            unknown = tuple(source_id for source_id in cited or () if source_id not in bundle)
        failure_kind: FailureKind | None = None
        error_text = ""
        output: Extraction | ProbeOutput | None = None
        try:
            # NEEDS-OWNER(AMB-52): an escaped lone surrogate passes R13 and H1's parse, but canonical_json refuses it
            output = validate(content, choice.finish_reason)
        except ExtractionInvalid as exc:  # only this: both it and ValidationError subclass ValueError
            failure_kind, error_text = exc.kind, exc.error_text
        record = AttemptRecord(
            **sent,
            http_status=200,
            finish_reason=choice.finish_reason,
            usage=Usage(
                completion.usage.prompt_tokens,
                completion.usage.completion_tokens,
                None if details is None else details.cached_tokens,
            ),
            timings=None if completion.timings is None else Timings(**completion.timings.model_dump()),
            server_model=completion.model,
            system_fingerprint=completion.system_fingerprint,
            content=content,
            reasoning_content=reasoning_content,
            thinking=thinking,
            cited_source_ids=cited,
            unknown_source_ids=unknown,
            failure_kind=failure_kind,
            error_text=error_text,
            server_error_type=None,
        )
        return record, output

    def _call(
        self,
        request: ChatRequest,
        item_id: str,
        validate: _Validator,
        template: RepairTemplate,
        bundle: frozenset[str] | None,
    ) -> CallRecord:
        """The attempt loop (S2 §3.8): at most two attempts, with no delay between them. The second attempt's result is
        final, whatever it is. A same-request retry needs no context check, because its bytes were checked before the
        run; a repair's bytes are new, so each one is counted first."""
        first, output = self._attempt(request, 1, "none", validate, bundle)
        attempts: tuple[AttemptRecord, ...] = (first,)
        checks: tuple[ContextCheck, ...] = ()
        if first.failure_kind in INVALID_KINDS:
            repair = repair_request(
                request, invalid_output=cast(str, first.content), error_text=first.error_text, template=template
            )
            check = self._count(repair, label="repair", attempts=attempts)
            if not check.fits:
                # NEEDS-OWNER(AMB-30): a repair that does not fit stops the call as a run fault, never a model failure
                raise ContextBudgetExceeded(check, attempts)
            second, output = self._attempt(repair, 2, "repair", validate, bundle)
            attempts, checks = (first, second), (check,)
        elif first.failure_kind is not None:
            second, output = self._attempt(request, 2, "same_request", validate, bundle)
            attempts = (first, second)
        return CallRecord(
            item_id=item_id,
            request_kind=request.kind,
            constrained=request.constrained,
            attempts=attempts,
            context_checks=checks,
            outcome=attempts[-1].failure_kind or "valid",
            output=output,
            total_wall_ns=sum(attempt.wall_ns for attempt in attempts),
        )
