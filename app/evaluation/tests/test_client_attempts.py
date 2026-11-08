"""The attempt loop and its records: H2 spec R11-R23, R30, R31, R33-R36, R43-R45, R47 and the section 6 rows that are
not about the wire or the context (S2 §3.3, §3.6, §3.8, §3.9, §5.3, §6 gates C, J, T; PHASE-0 §2.2).

Every response comes from `httpx.MockTransport`. Data is synthetic. Every test that reaches a second attempt checks the
exact bytes of the second completion.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import math
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from app.evaluation.bakeoff import client as client_module
from app.evaluation.bakeoff.client import (
    CHAT_PATH,
    COUNT_PATH,
    REQUEST_KINDS,
    AttemptRecord,
    CallRecord,
    ChatMessage,
    ChatRequest,
    ContextCheck,
    RepairTemplate,
    ThinkingCheck,
    Timings,
    Usage,
    repair_request,
    thinking_check,
)
from app.evaluation.bakeoff.extraction import (
    CUT_OFF_TEXT,
    ExtractedClaim,
    Extraction,
    ExtractionFailure,
    ExtractionInvalid,
    parse_extraction,
)
from app.evaluation.bakeoff.probe import ProbeOutput
from app.evaluation.bakeoff.records import call_from_dict, call_to_dict, record_json, record_loads
from app.evaluation.bakeoff.schemas import canonical_json
from app.evaluation.tests._client_fixtures import (
    BUNDLE,
    CAP,
    EMPTY,
    MESSAGES,
    PROBE,
    PROBE_OK,
    SEGMENT_IDS,
    SINGLE,
    TEMPLATE,
    FakeClock,
    block_sockets,
    chat_response,
    completions,
    error_response,
    input_tokens_response,
    make_client,
    paths,
    repair_bytes,
    run_call,
    run_extract,
    run_probe,
    valid_content,
)

FIRST_CHAR = "invalid JSON: Expecting value (line 1, column 1)"
BAD_SEGMENT = '{"claims": [{"text": "x", "segmentIds": ["seg_9"]}], "nonCheckable": []}'
BAD_SEGMENT_ERROR = "claims.0.segmentIds.0: 'seg_9' is not a segment id of this request"
TIMINGS = Timings(0, 100, 1.5, 0.015, 66666.7, 10, 20.0, 2.0, 500.0)
EMPTY_OUTPUT = Extraction("syn_a", (), ())
QUOTED_TAG = '{"claims": [{"text": "He typed <think> into the chat", "segmentIds": ["seg_001"]}], "nonCheckable": []}'


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)


@pytest.fixture
def no_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no validator may run on a response that is not a well-formed 200")

    monkeypatch.setattr(client_module, "parse_extraction", refuse)
    monkeypatch.setattr(client_module, "parse_probe", refuse)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def second_body(server: Any) -> bytes:
    return completions(server)[1].content


def body_of(content: str = EMPTY) -> dict[str, Any]:
    return json.loads(chat_response(content).content)


def response_of(body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(body).encode("utf-8"))


def with_value(path: tuple[Any, ...], value: Any, content: str = EMPTY) -> httpx.Response:
    """A 200 whose body has `value` at `path`."""
    body = body_of(content)
    target = body
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return response_of(body)


def without(*keys: tuple[Any, ...]) -> httpx.Response:
    """A 200 whose body lacks each of `keys`."""
    body = body_of()
    for path in keys:
        target = body
        for key in path[:-1]:
            target = target[key]
        del target[path[-1]]
    return response_of(body)


def vars_of(record: Any) -> Iterator[Any]:
    for field in dataclasses.fields(record):
        yield getattr(record, field.name)


def assert_no_response_held(attempt: AttemptRecord) -> None:
    """R12 rows 1-5."""
    assert attempt.content is None
    assert attempt.reasoning_content is None
    assert attempt.finish_reason is None
    assert attempt.usage is None
    assert attempt.timings is None
    assert attempt.server_model is None
    assert attempt.system_fingerprint is None
    assert attempt.thinking is None
    assert attempt.cited_source_ids is None
    assert attempt.unknown_source_ids == ()


def malformed_detail(step: httpx.Response) -> str:
    """The `<detail>` of a malformed 200: a `server_error` with status 200, then a same-request retry, exact bytes."""
    call, server = run_extract(step, chat_response(EMPTY))
    first, second = call.attempts
    assert paths(server) == [CHAT_PATH, CHAT_PATH]
    assert second_body(server) == SINGLE.body_bytes()
    assert (first.failure_kind, first.http_status, first.server_error_type) == ("server_error", 200, None)
    assert_no_response_held(first)
    assert (second.retry_type, second.request_sha256) == ("same_request", first.request_sha256)
    assert (call.outcome, call.output, call.context_checks) == ("valid", EMPTY_OUTPUT, ())
    prefix = "malformed response: "
    assert first.error_text.startswith(prefix)
    return first.error_text[len(prefix) :]


# ---------------------------------------------------------------- R11 well-formed 200, E24, L4


def test_r11_a_well_formed_200_fills_the_record() -> None:
    call, _server = run_extract(chat_response(EMPTY))
    attempt = call.attempts[0]
    assert attempt.http_status == 200
    assert attempt.finish_reason == "stop"
    assert attempt.usage == Usage(100, 10, 0)
    assert attempt.timings == TIMINGS
    assert attempt.server_model == "qwen3-4b"
    assert attempt.system_fingerprint == "b1-27b7a8a"
    assert attempt.content == EMPTY
    assert attempt.reasoning_content is None
    assert attempt.failure_kind is None
    assert attempt.error_text == ""
    assert attempt.server_error_type is None


def test_r11_records_hold_no_pydantic_objects() -> None:
    call, _server = run_extract(chat_response(EMPTY, reasoning_content="r"))
    attempt = call.attempts[0]
    assert type(attempt.usage) is Usage
    assert type(attempt.timings) is Timings
    assert type(attempt.thinking) is ThinkingCheck
    assert not [value for value in vars_of(attempt) if isinstance(value, BaseModel)]


def test_r11_a_body_without_timings_is_well_formed() -> None:
    call, _server = run_extract(chat_response(EMPTY, timings=False))
    assert call.outcome == "valid"
    assert call.attempts[0].timings is None


def test_r11_a_body_without_prompt_tokens_details_is_well_formed() -> None:
    call, _server = run_extract(without(("usage", "prompt_tokens_details")))
    assert call.outcome == "valid"
    assert call.attempts[0].usage == Usage(100, 10, None)


def test_r11_the_draft_counts_are_kept_when_present() -> None:
    body = body_of()
    body["timings"].update(draft_n=5, draft_n_accepted=3)
    call, _server = run_extract(response_of(body))
    assert call.attempts[0].timings == dataclasses.replace(TIMINGS, draft_n=5, draft_n_accepted=3)


def test_r11_a_null_reasoning_field_is_no_reasoning() -> None:
    call, _server = run_extract(with_value(("choices", 0, "message", "reasoning_content"), None))
    assert call.outcome == "valid"
    assert call.attempts[0].reasoning_content is None


@pytest.mark.parametrize("path", [("timings", "prompt_ms"), ("timings", "predicted_per_second")])
def test_r11_l4_a_json_integer_is_accepted_for_a_float_field(path: tuple[str, str]) -> None:
    call, _server = run_extract(with_value(path, 0))
    value = getattr(call.attempts[0].timings, path[1])
    assert type(value) is float and value == 0.0


@pytest.mark.parametrize(
    "extend",
    [
        pytest.param(lambda body: body.update(__verbose={"a": 1}), id="e24-verbose"),
        pytest.param(lambda body: body.update(id="x", created=1, object="y"), id="e24-id-created-object"),
        pytest.param(lambda body: body.update(unknown=[1]), id="top-level"),
        pytest.param(lambda body: body["choices"][0].update(index=7, logprobs={"content": []}), id="choice"),
        pytest.param(lambda body: body["usage"].update(total_tokens=-1, extra="x"), id="e24-usage"),
        pytest.param(lambda body: body["usage"]["prompt_tokens_details"].update(audio_tokens=1), id="details"),
        pytest.param(lambda body: body["timings"].update(extra=1), id="timings"),
    ],
)
def test_r11_e24_keys_the_harness_does_not_read_are_ignored(extend: Callable[[dict[str, Any]], None]) -> None:
    body = body_of()
    extend(body)
    call, _server = run_extract(response_of(body))
    assert call.outcome == "valid"
    assert call.attempts[0].usage == Usage(100, 10, 0)
    assert call.attempts[0].content == EMPTY


UNREAD_PATHS = [("id",), ("created",), ("object",), ("choices", 0, "index"), ("usage", "total_tokens")]


@pytest.mark.parametrize("path", UNREAD_PATHS)
def test_r11_c12_a_key_the_harness_does_not_read_may_be_absent(path: tuple[Any, ...]) -> None:
    call, _server = run_extract(without(path))
    assert call.outcome == "valid"
    assert call.attempts[0].usage == Usage(100, 10, 0)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("id",), None),
        (("created",), "x"),
        (("object",), 3),
        (("choices", 0, "index"), "0"),
        (("usage", "total_tokens"), "x"),
    ],
)
def test_r11_c12_a_key_the_harness_does_not_read_may_hold_any_json_value(path: tuple[Any, ...], value: Any) -> None:
    call, _server = run_extract(with_value(path, value))
    assert call.outcome == "valid"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tool_calls", [{"id": "x"}]),
        ("name", "x"),
        ("tool_call_id", "x"),
        ("refusal", None),
        ("function_call", {}),
        ("reasoning", "x"),
    ],
)
def test_r11_e18_a_message_holds_only_three_keys(key: str, value: Any) -> None:
    detail = malformed_detail(with_value(("choices", 0, "message", key), value))
    assert detail == f"choices.0.message.{key}: Extra inputs are not permitted"


INTEGER = "Input should be a valid integer"
NUMBER = "Input should be a valid number"
STRING = "Input should be a valid string"
NOT_NEGATIVE = "Input should be greater than or equal to 0"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("usage", "prompt_tokens"), "12", INTEGER),
        (("usage", "prompt_tokens"), 12.0, INTEGER),
        (("usage", "prompt_tokens"), True, INTEGER),
        (("usage", "prompt_tokens"), None, INTEGER),
        (("usage", "prompt_tokens"), -1, NOT_NEGATIVE),
        (("usage", "completion_tokens"), "10", INTEGER),
        (("usage", "completion_tokens"), 10.0, INTEGER),
        (("usage", "completion_tokens"), False, INTEGER),
        (("usage", "completion_tokens"), -1, NOT_NEGATIVE),
        (("usage", "prompt_tokens_details", "cached_tokens"), "0", INTEGER),
        (("usage", "prompt_tokens_details", "cached_tokens"), -1, NOT_NEGATIVE),
        (("timings", "cache_n"), 1.0, INTEGER),
        (("timings", "cache_n"), -1, NOT_NEGATIVE),
        (("timings", "prompt_n"), "1", INTEGER),
        (("timings", "prompt_n"), -1, NOT_NEGATIVE),
        (("timings", "predicted_n"), True, INTEGER),
        (("timings", "predicted_n"), -1, NOT_NEGATIVE),
        (("timings", "draft_n"), "1", INTEGER),
        (("timings", "draft_n_accepted"), 1.5, INTEGER),
        (("timings", "prompt_ms"), "1.5", NUMBER),
        (("timings", "prompt_ms"), True, NUMBER),
        (("timings", "prompt_ms"), None, NUMBER),
        (("timings", "prompt_per_token_ms"), "x", NUMBER),
        (("timings", "prompt_per_second"), False, NUMBER),
        (("timings", "predicted_ms"), [], NUMBER),
        (("timings", "predicted_per_token_ms"), {}, NUMBER),
        (("timings", "predicted_per_second"), "1", NUMBER),
        (("model",), 3, STRING),
        (("model",), None, STRING),
        (("system_fingerprint",), 3, STRING),
        (("system_fingerprint",), None, STRING),
        (("choices", 0, "message", "content"), None, STRING),
        (("choices", 0, "message", "content"), 3, STRING),
        (("choices", 0, "message", "content"), {"a": 1}, STRING),
        (("choices", 0, "message", "reasoning_content"), 3, STRING),
        (("choices", 0, "message", "role"), "user", "Input should be 'assistant'"),
        (("choices", 0, "message", "role"), None, "Input should be 'assistant'"),
        (("choices",), "x", "Input should be a valid list"),
        (("choices",), None, "Input should be a valid list"),
    ],
)
def test_r11_l4_r12_no_coercion(path: tuple[Any, ...], value: Any, message: str) -> None:
    assert malformed_detail(with_value(path, value)) == f"{'.'.join(str(part) for part in path)}: {message}"


@pytest.mark.parametrize(
    ("path", "value", "detail"),
    [
        (("usage",), "x", "usage: Input should be a valid dictionary or instance of _UsageOut"),
        (("usage",), None, "usage: Input should be a valid dictionary or instance of _UsageOut"),
        (("timings",), "x", "timings: Input should be a valid dictionary or instance of _TimingsOut"),
        (("choices", 0), "x", "choices.0: Input should be a valid dictionary or instance of _ChoiceOut"),
        (
            ("choices", 0, "message"),
            "x",
            "choices.0.message: Input should be a valid dictionary or instance of _MessageOut",
        ),
        (
            ("usage", "prompt_tokens_details"),
            "x",
            "usage.prompt_tokens_details: Input should be a valid dictionary or instance of _PromptDetailsOut",
        ),
    ],
)
def test_r11_a_nested_object_must_be_an_object(path: tuple[Any, ...], value: Any, detail: str) -> None:
    assert malformed_detail(with_value(path, value)) == detail


@pytest.mark.parametrize(
    "path",
    [
        ("model",),
        ("system_fingerprint",),
        ("choices",),
        ("usage",),
        ("usage", "prompt_tokens"),
        ("usage", "completion_tokens"),
        ("usage", "prompt_tokens_details", "cached_tokens"),
        ("choices", 0, "finish_reason"),
        ("choices", 0, "message"),
        ("choices", 0, "message", "role"),
        ("choices", 0, "message", "content"),
        ("timings", "cache_n"),
        ("timings", "prompt_n"),
        ("timings", "prompt_ms"),
        ("timings", "prompt_per_token_ms"),
        ("timings", "prompt_per_second"),
        ("timings", "predicted_n"),
        ("timings", "predicted_ms"),
        ("timings", "predicted_per_token_ms"),
        ("timings", "predicted_per_second"),
    ],
)
def test_r11_e13_every_field_read_is_required(path: tuple[Any, ...]) -> None:
    assert malformed_detail(without(path)) == f"{'.'.join(str(part) for part in path)}: Field required"


def test_r12_several_faults_in_one_body_are_joined_in_field_order() -> None:
    detail = malformed_detail(without(("model",), ("usage",)))
    assert detail == "model: Field required; usage: Field required"


@pytest.mark.parametrize(
    "name",
    [
        "prompt_ms",
        "prompt_per_token_ms",
        "prompt_per_second",
        "predicted_ms",
        "predicted_per_token_ms",
        "predicted_per_second",
    ],
)
def test_r23_an_infinite_timing_is_malformed(name: str) -> None:
    raw = chat_response(EMPTY).content
    body = json.loads(raw)
    text = json.dumps(body).replace(f'"{name}": {body["timings"][name]}', f'"{name}": 1e999')
    assert "1e999" in text
    detail = malformed_detail(httpx.Response(200, content=text.encode("utf-8")))
    assert detail == f"timings.{name}: Input should be a finite number"


def test_r23_a_float_too_big_for_a_double_is_malformed() -> None:
    assert malformed_detail(with_value(("timings", "prompt_ms"), 10**400)) == f"timings.prompt_ms: {NUMBER}"


class _Float(BaseModel):
    model_config = ConfigDict(strict=True)

    value: float


class _Int(BaseModel):
    model_config = ConfigDict(strict=True)

    value: int


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_l4_strict_int_rejects_bool_float_and_str(value: Any) -> None:
    with pytest.raises(ValidationError):
        _Int.model_validate({"value": value})


@pytest.mark.parametrize("value", [True, "1.0"])
def test_l4_strict_float_rejects_bool_and_str(value: Any) -> None:
    with pytest.raises(ValidationError):
        _Float.model_validate({"value": value})


def test_l4_strict_float_accepts_an_int() -> None:
    parsed = _Float.model_validate({"value": 1})
    assert type(parsed.value) is float and parsed.value == 1.0


def test_l5_pydantic_s_own_json_keeps_what_h1_s_rules_reject() -> None:
    assert _Float.model_validate_json('{"value": 1.0, "value": 2.0}').value == 2.0
    assert math.isnan(_Float.model_validate_json('{"value": NaN}').value)


def test_l5_the_client_reads_bodies_under_h1_s_rules() -> None:
    raw = b'{"choices": [], "choices": []}'
    assert malformed_detail(httpx.Response(200, content=raw)) == "invalid JSON: duplicate key 'choices'"
    text = chat_response(EMPTY).content.replace(b'"prompt_ms": 1.5', b'"prompt_ms": NaN')
    assert malformed_detail(httpx.Response(200, content=text)) == "invalid JSON: non-finite number NaN"


def test_l5_model_validate_passes_a_lone_surrogate() -> None:
    message = client_module._MessageOut.model_validate({"role": "assistant", "content": chr(0xD800)})
    assert message.content == chr(0xD800)


# ---------------------------------------------------------------- R12 classification, rows 1 and 2, L3

TIMEOUTS = [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout]
TRANSPORT_ERRORS = [
    httpx.ConnectError,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
    httpx.RemoteProtocolError,
    httpx.LocalProtocolError,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
    httpx.DecodingError,
    httpx.TooManyRedirects,
    httpx.RequestError,
]


def test_l3_the_exception_tree() -> None:
    for timeout in TIMEOUTS:
        assert issubclass(timeout, httpx.TimeoutException)
    assert issubclass(httpx.TimeoutException, httpx.TransportError)
    assert issubclass(httpx.TransportError, httpx.RequestError)
    assert issubclass(httpx.DecodingError, httpx.RequestError)
    assert issubclass(httpx.TooManyRedirects, httpx.RequestError)
    assert not issubclass(httpx.InvalidURL, httpx.RequestError)
    assert not issubclass(httpx.RequestError, ValueError)


@pytest.mark.parametrize("error", TIMEOUTS)
@pytest.mark.parametrize(
    ("request_", "limit"),
    [
        pytest.param(SINGLE, "30", id="single"),
        pytest.param(CAP, "300", id="cap"),
        pytest.param(PROBE, "30", id="probe"),
    ],
)
def test_r9_r12_row_1_any_httpx_timeout_is_a_timeout(
    error: type[httpx.TimeoutException], request_: ChatRequest, limit: str, no_validator: None
) -> None:
    call, server = run_call(request_, error, error)
    for attempt in call.attempts:
        assert attempt.failure_kind == "timeout"
        assert attempt.http_status is None
        assert attempt.error_text == f"timeout: {error.__name__} after the {limit} s limit"
        assert attempt.server_error_type is None
        assert_no_response_held(attempt)
    assert second_body(server) == request_.body_bytes()
    assert call.outcome == "timeout"


@pytest.mark.parametrize("error", TRANSPORT_ERRORS)
def test_r12_row_2_any_other_request_error_is_a_server_error(
    error: type[httpx.RequestError], no_validator: None
) -> None:
    call, server = run_extract(error, error)
    for attempt in call.attempts:
        assert attempt.failure_kind == "server_error"
        assert attempt.http_status is None
        assert attempt.error_text == f"transport error: {error.__name__}: scripted"
        assert attempt.server_error_type is None
        assert_no_response_held(attempt)
    assert second_body(server) == SINGLE.body_bytes()
    assert call.outcome == "server_error"


def test_e23_a_connection_refused_twice() -> None:
    errno = "[Errno 111] Connection refused"
    call, server = run_extract(httpx.ConnectError(errno), httpx.ConnectError(errno))
    assert [attempt.http_status for attempt in call.attempts] == [None, None]
    assert [attempt.failure_kind for attempt in call.attempts] == ["server_error", "server_error"]
    assert [attempt.error_text for attempt in call.attempts] == [f"transport error: ConnectError: {errno}"] * 2
    assert second_body(server) == SINGLE.body_bytes()
    assert (call.outcome, call.output) == ("server_error", None)


def test_r12_row_2_the_exception_text_is_kept_and_escaped() -> None:
    errno = "[Errno 111] Connection refused"
    call, server = run_extract(httpx.ConnectError(errno), httpx.ConnectError("line 1\nline 2\\"))
    assert call.attempts[0].error_text == "transport error: ConnectError: [Errno 111] Connection refused"
    assert call.attempts[1].error_text == "transport error: ConnectError: line 1\\nline 2\\\\"
    assert second_body(server) == SINGLE.body_bytes()


def test_r12_row_2_a_body_that_cannot_be_decoded_is_a_transport_error() -> None:
    broken = httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"not gzip"))
    call, server = run_extract(broken, chat_response(EMPTY))
    first = call.attempts[0]
    assert (first.failure_kind, first.http_status) == ("server_error", None)
    assert first.error_text.startswith("transport error: DecodingError: ")
    assert_no_response_held(first)
    assert second_body(server) == SINGLE.body_bytes()
    assert call.outcome == "valid"


@pytest.mark.parametrize(
    "error",
    [httpx.InvalidURL("x"), RuntimeError("x"), KeyError("x"), ValueError("x"), AssertionError("x")],
    ids=lambda error: type(error).__name__,
)
def test_r12_l3_an_exception_outside_request_error_propagates_with_no_record(error: Exception) -> None:
    client, server = make_client(error)
    with pytest.raises(type(error)) as caught:
        client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert caught.value is error
    assert len(server.requests) == 1


# ---------------------------------------------------------------- R12 rows 3 and 4, E5, E25, E26

CONTEXT_MESSAGE = "request (13000 tokens) exceeds the available context size (12288 tokens), try increasing it"
E5_BODY = json.dumps(  # the pinned body: the two counts sit inside `error`, which `error_response` cannot build
    {
        "error": {
            "code": 400,
            "message": CONTEXT_MESSAGE,
            "type": "exceed_context_size_error",
            "n_prompt_tokens": 13000,
            "n_ctx": 12288,
        }
    }
).encode("utf-8")
REPEATED_ERROR = b'{"error": {"message": "a", "type": "t"}, "error": {"message": "b", "type": "t"}}'


@pytest.mark.parametrize(
    ("step", "status", "text", "error_type"),
    [
        pytest.param(
            lambda: error_response(500, "server_error", "boom"),
            500,
            "HTTP 500: server_error: boom",
            "server_error",
            id="row-3",
        ),
        pytest.param(
            lambda: error_response(400, "exceed_context_size_error", CONTEXT_MESSAGE),
            400,
            f"HTTP 400: exceed_context_size_error: {CONTEXT_MESSAGE}",
            "exceed_context_size_error",
            id="e5",
        ),
        pytest.param(
            lambda: httpx.Response(400, content=E5_BODY),
            400,
            f"HTTP 400: exceed_context_size_error: {CONTEXT_MESSAGE}",
            "exceed_context_size_error",
            id="e5-the-pinned-body",
        ),
        pytest.param(
            lambda: error_response(503, "unavailable_error", "line 1\nline 2\\"),
            503,
            "HTTP 503: unavailable_error: line 1\\nline 2\\\\",
            "unavailable_error",
            id="row-3-escaped",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": {"message": "m", "type": "t"}}'),
            500,
            "HTTP 500: t: m",
            "t",
            id="row-3-no-code",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": {"code": null, "message": "m", "type": "t"}, "x": 1}'),
            500,
            "HTTP 500: t: m",
            "t",
            id="row-3-null-code-extra-key",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b"Internal Server Error"),
            500,
            "HTTP 500: Internal Server Error",
            None,
            id="e26-text",
        ),
        pytest.param(lambda: httpx.Response(503, content=b""), 503, "HTTP 503: (empty body)", None, id="e26-empty"),
        pytest.param(
            lambda: httpx.Response(502, content=b"x" * 600),
            502,
            "HTTP 502: " + "x" * 500 + "...",
            None,
            id="e26-long",
        ),
        pytest.param(
            lambda: httpx.Response(502, content=b"x" * 500), 502, "HTTP 502: " + "x" * 500, None, id="exactly-500"
        ),
        pytest.param(
            lambda: httpx.Response(502, content=b"x" * 501),
            502,
            "HTTP 502: " + "x" * 500 + "...",
            None,
            id="just-over-500",
        ),
        pytest.param(
            lambda: httpx.Response(502, content=b"\n" * 501),
            502,
            "HTTP 502: " + "\\n" * 500 + "...",
            None,
            id="the-500-counts-before-escaping",
        ),
        pytest.param(
            lambda: httpx.Response(502, content="é".encode() * 300),
            502,
            "HTTP 502: " + "é" * 300,
            None,
            id="300-characters-in-600-bytes",
        ),
        pytest.param(
            lambda: httpx.Response(502, content="é".encode() * 500),
            502,
            "HTTP 502: " + "é" * 500,
            None,
            id="500-characters-in-1000-bytes",
        ),
        pytest.param(
            lambda: httpx.Response(502, content="é".encode() * 600),
            502,
            "HTTP 502: " + "é" * 500 + "...",
            None,
            id="the-500-counts-characters-not-bytes",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b"bad \xff body"),
            500,
            "HTTP 500: bad " + chr(0xFFFD) + " body",
            None,
            id="bad-utf-8",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b"line 1\nline 2\\"),
            500,
            "HTTP 500: line 1\\nline 2\\\\",
            None,
            id="excerpt-escaped",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"detail": "x"}'),
            500,
            'HTTP 500: {"detail": "x"}',
            None,
            id="other-json",
        ),
        pytest.param(lambda: httpx.Response(500, content=b"[]"), 500, "HTTP 500: []", None, id="not-an-object"),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": "x"}'),
            500,
            'HTTP 500: {"error": "x"}',
            None,
            id="error-not-an-object",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": {"message": "m"}}'),
            500,
            'HTTP 500: {"error": {"message": "m"}}',
            None,
            id="error-without-type",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": {"code": "500", "message": "m", "type": "t"}}'),
            500,
            'HTTP 500: {"error": {"code": "500", "message": "m", "type": "t"}}',
            None,
            id="error-with-a-string-code",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": {"type": "t"}}'),
            500,
            'HTTP 500: {"error": {"type": "t"}}',
            None,
            id="error-without-message",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b'{"error": {"message": 3, "type": "t"}}'),
            500,
            'HTTP 500: {"error": {"message": 3, "type": "t"}}',
            None,
            id="error-with-a-number-message",
        ),
        pytest.param(
            lambda: httpx.Response(503, content=b'{"error": {"code": 500, "message": "m", "type": "t"}}'),
            503,
            "HTTP 503: t: m",
            "t",
            id="the-status-is-the-response-s",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=REPEATED_ERROR),
            500,
            "HTTP 500: " + REPEATED_ERROR.decode(),
            None,
            id="error-with-a-repeated-key",
        ),
        pytest.param(lambda: httpx.Response(204), 204, "HTTP 204: (empty body)", None, id="204"),
        pytest.param(lambda: httpx.Response(304), 304, "HTTP 304: (empty body)", None, id="304"),
    ],
)
def test_r12_rows_3_and_4_a_non_200(
    step: Callable[[], httpx.Response], status: int, text: str, error_type: str | None, no_validator: None
) -> None:
    call, server = run_extract(step(), step())
    assert len(call.attempts) == 2
    for attempt in call.attempts:
        assert attempt.failure_kind == "server_error"
        assert attempt.http_status == status
        assert attempt.error_text == text
        assert attempt.server_error_type == error_type
        assert_no_response_held(attempt)
    assert [attempt.retry_type for attempt in call.attempts] == ["none", "same_request"]
    assert paths(server) == [CHAT_PATH, CHAT_PATH]
    assert second_body(server) == SINGLE.body_bytes()
    assert (call.outcome, call.output, call.context_checks) == ("server_error", None, ())


def test_r11_only_a_200_is_parsed() -> None:
    created = httpx.Response(201, content=chat_response(EMPTY).content)
    call, server = run_extract(created, chat_response(EMPTY))
    first = call.attempts[0]
    assert (first.failure_kind, first.http_status) == ("server_error", 201)
    assert first.error_text.startswith('HTTP 201: {"choices": [{"finish_reason": "stop"')
    assert_no_response_held(first)
    assert second_body(server) == SINGLE.body_bytes()


def test_r3_e25_a_redirect_is_never_followed() -> None:
    def moved() -> httpx.Response:
        return httpx.Response(307, headers={"Location": "http://evil.example/"})

    call, server = run_extract(moved(), moved())
    assert [str(sent.url) for sent in server.requests] == ["http://127.0.0.1:8080/v1/chat/completions"] * 2
    for attempt in call.attempts:
        assert (attempt.http_status, attempt.failure_kind) == (307, "server_error")
        assert attempt.error_text == "HTTP 307: (empty body)"
        assert attempt.content is None
    assert call.attempts[1].retry_type == "same_request"
    assert second_body(server) == SINGLE.body_bytes()
    assert call.outcome == "server_error"


# ---------------------------------------------------------------- R12 row 5, E4, E11, E12


@pytest.mark.parametrize(
    ("raw", "detail"),
    [
        pytest.param(b"Internal Server Error", FIRST_CHAR, id="e4"),
        pytest.param(b"", FIRST_CHAR, id="empty"),
        pytest.param(b"\xff", "invalid UTF-8: invalid start byte", id="bad-utf-8"),
        pytest.param(b'{"a": NaN}', "invalid JSON: non-finite number NaN", id="nan"),
        pytest.param(b'{"a": Infinity}', "invalid JSON: non-finite number Infinity", id="infinity"),
        pytest.param(b'{"a": 1, "a": 2}', "invalid JSON: duplicate key 'a'", id="repeated-key"),
        pytest.param(b"[]", "(root): response must be a JSON object", id="array"),
        pytest.param(b"null", "(root): response must be a JSON object", id="null"),
        pytest.param(b'"text"', "(root): response must be a JSON object", id="string"),
        pytest.param(b"12", "(root): response must be a JSON object", id="number"),
    ],
)
def test_r12_row_5_e4_a_200_that_cannot_be_read(raw: bytes, detail: str) -> None:
    assert malformed_detail(httpx.Response(200, content=raw)) == detail


def test_e11_the_choice_list_holds_exactly_one() -> None:
    too_few = "choices: List should have at least 1 item after validation, not 0"
    too_many = "choices: List should have at most 1 item after validation, not 2"
    assert malformed_detail(with_value(("choices",), [])) == too_few
    two = body_of()["choices"] * 2
    assert malformed_detail(with_value(("choices",), two)) == too_many


@pytest.mark.parametrize("finish_reason", ["tool_calls", "content_filter", None, "", "STOP", 1])
def test_e12_any_other_finish_reason_is_malformed_never_a_repair(finish_reason: Any) -> None:
    detail = malformed_detail(chat_response(EMPTY, finish_reason=finish_reason))
    assert detail == "choices.0.finish_reason: Input should be 'stop' or 'length'"


def test_r12_rows_5_on_a_probe_run_no_validator_and_read_no_ids(no_validator: None) -> None:
    call, server = run_probe(httpx.Response(200, content=b"oops"), httpx.Response(200, content=b"oops"))
    for attempt in call.attempts:
        assert attempt.error_text == f"malformed response: {FIRST_CHAR}"
        assert_no_response_held(attempt)
    assert second_body(server) == PROBE.body_bytes()


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(lambda: chat_response(EMPTY, completion_tokens=1025), id="over-the-cap"),
        pytest.param(lambda: chat_response(chr(0xD800)), id="lone-surrogate"),
        pytest.param(lambda: chat_response(EMPTY, finish_reason="tool_calls"), id="finish-reason"),
        pytest.param(lambda: without(("usage",)), id="no-usage"),
    ],
)
def test_r12_row_5_no_validator_runs_on_a_malformed_200(step: Callable[[], httpx.Response], no_validator: None) -> None:
    call, server = run_extract(step(), step())
    assert [attempt.failure_kind for attempt in call.attempts] == ["server_error", "server_error"]
    assert second_body(server) == SINGLE.body_bytes()


def test_r12_row_6_the_validator_s_text_is_kept_verbatim() -> None:
    raw = '{"claims": [{"text": " ", "segmentIds": []}], "nonCheckable": [{"segmentIds": ["seg_9"]}]}'
    expected = (
        "claims.0.text: Value error, must contain a non-whitespace character\n"
        "claims.0.segmentIds: List should have at least 1 item after validation, not 0\n"
        "nonCheckable.0.reasonKind: Field required"
    )
    call, server = run_extract(chat_response(raw), input_tokens_response(900), chat_response(EMPTY))
    first = call.attempts[0]
    assert (first.failure_kind, first.http_status, first.error_text) == ("invalid_output", 200, expected)
    assert first.content == raw
    assert second_body(server) == repair_bytes(SINGLE, raw, expected)


@pytest.mark.parametrize(
    ("content", "error_text"),
    [
        pytest.param('{"claims": []}', "nonCheckable: Field required", id="missing-key"),
        pytest.param("not json", FIRST_CHAR, id="not-json"),
        pytest.param("```json\n" + EMPTY + "\n```", FIRST_CHAR, id="amb-49-a-fence-stays-invalid"),
        pytest.param("[]", "(root): output must be a JSON object", id="not-an-object"),
        pytest.param('{"claims": [], "claims": []}', "invalid JSON: duplicate key 'claims'", id="repeated-key"),
    ],
)
def test_r12_row_6_the_examples(content: str, error_text: str) -> None:
    call, server = run_extract(chat_response(content), input_tokens_response(900), chat_response(EMPTY))
    assert (call.attempts[0].failure_kind, call.attempts[0].error_text) == ("invalid_output", error_text)
    assert second_body(server) == repair_bytes(SINGLE, content, error_text)


@pytest.mark.parametrize(
    ("finish_reason", "kind", "text"),
    [("stop", "invalid_output", FIRST_CHAR), ("length", "cut_off", CUT_OFF_TEXT)],
)
def test_r11_r12_row_6_a_failed_validation_keeps_its_whole_response(finish_reason: str, kind: str, text: str) -> None:
    def failing() -> httpx.Response:
        return chat_response("not json", finish_reason=finish_reason, reasoning_content="r", cached_tokens=2)

    call, server = run_extract(failing(), input_tokens_response(900), failing())
    assert len(call.attempts) == 2
    for attempt in call.attempts:
        assert (attempt.failure_kind, attempt.error_text) == (kind, text)
        assert attempt.usage == Usage(100, 10, 2)
        assert attempt.timings == dataclasses.replace(TIMINGS, cache_n=2)
        assert (attempt.server_model, attempt.system_fingerprint) == ("qwen3-4b", "b1-27b7a8a")
        assert attempt.reasoning_content == "r"
    assert second_body(server) == repair_bytes(SINGLE, "not json", text)


def test_r12_row_7_valid() -> None:
    call, _server = run_extract(chat_response(EMPTY))
    first = call.attempts[0]
    assert (first.failure_kind, first.http_status, first.error_text) == (None, 200, "")


# ---------------------------------------------------------------- R13 impossible values, E19, E21


@pytest.mark.parametrize(
    ("request_", "cap"),
    [pytest.param(SINGLE, 1024, id="single"), pytest.param(CAP, 4096, id="cap"), pytest.param(PROBE, 384, id="probe")],
)
def test_r13_e19_tokens_above_the_cap_are_malformed(request_: ChatRequest, cap: int) -> None:
    content = valid_content(request_.kind)
    call, server = run_call(
        request_, chat_response(content, completion_tokens=cap + 1), chat_response(content, completion_tokens=cap)
    )
    first, second = call.attempts
    expected = f"malformed response: usage.completion_tokens {cap + 1} exceeds max_tokens {cap}"
    assert (first.failure_kind, first.http_status, first.error_text) == ("server_error", 200, expected)
    assert_no_response_held(first)
    assert (second.retry_type, second.failure_kind) == ("same_request", None)
    assert second.usage == Usage(100, cap, 0)
    assert second_body(server) == request_.body_bytes()


@pytest.mark.parametrize(
    ("content", "finish_reason"),
    [
        pytest.param(EMPTY, "length", id="at-the-cap"),
        pytest.param("not json", "stop", id="invalid-output"),
        pytest.param("not json", "length", id="invalid-output-at-the-cap"),
    ],
)
def test_r12_r13_a_cap_overrun_comes_before_the_validator(content: str, finish_reason: str) -> None:
    detail = malformed_detail(chat_response(content, finish_reason=finish_reason, completion_tokens=1025))
    assert detail == "usage.completion_tokens 1025 exceeds max_tokens 1024"


@pytest.mark.parametrize(
    ("first", "route", "expected"),
    [
        pytest.param(
            lambda: (chat_response("not json"), input_tokens_response(900)),
            [CHAT_PATH, COUNT_PATH, CHAT_PATH],
            repair_bytes(SINGLE, "not json", FIRST_CHAR),
            id="repair",
        ),
        pytest.param(lambda: (httpx.ReadTimeout,), [CHAT_PATH, CHAT_PATH], SINGLE.body_bytes(), id="same-request"),
    ],
)
def test_r13_r19_a_second_attempt_over_the_cap_is_the_final_server_error(
    first: Callable[[], tuple[Any, ...]], route: list[str], expected: bytes
) -> None:
    call, server = run_extract(*first(), chat_response(EMPTY, completion_tokens=1025))
    second = call.attempts[1]
    assert (second.failure_kind, second.http_status) == ("server_error", 200)
    assert second.error_text == "malformed response: usage.completion_tokens 1025 exceeds max_tokens 1024"
    assert_no_response_held(second)
    assert (call.outcome, call.output) == ("server_error", None)
    assert paths(server) == route
    assert second_body(server) == expected


def test_r13_e21_a_lone_surrogate_in_the_content_is_malformed() -> None:
    assert malformed_detail(chat_response(chr(0xD800))) == "choices.0.message.content is not valid Unicode"


def test_r13_a_lone_surrogate_in_the_reasoning_field_is_malformed() -> None:
    detail = malformed_detail(chat_response(EMPTY, reasoning_content=chr(0xDFFF)))
    assert detail == "choices.0.message.reasoning_content is not valid Unicode"


@pytest.mark.parametrize("key", ["model", "system_fingerprint"])
def test_r13_r23_a_lone_surrogate_in_a_recorded_name_is_malformed(key: str) -> None:
    assert malformed_detail(with_value((key,), chr(0xD800))) == f"{key} is not valid Unicode"


def test_r13_a_character_outside_the_bmp_is_fine() -> None:
    text = '{"claims": [], "nonCheckable": []}' + chr(0x1F600)
    call, server = run_extract(chat_response(text), input_tokens_response(900), chat_response(EMPTY))
    assert call.attempts[0].content == text
    assert call.attempts[0].failure_kind == "invalid_output"
    expected = f"invalid JSON: Extra data (line 1, column {len(EMPTY) + 1})"
    assert call.attempts[0].error_text == expected
    assert second_body(server) == repair_bytes(SINGLE, text, expected)


# ---------------------------------------------------------------- R14 cached tokens, E20


def test_r14_e20_cached_tokens_are_recorded_and_change_nothing() -> None:
    call, server = run_extract(chat_response(EMPTY, cached_tokens=512))
    attempt = call.attempts[0]
    assert attempt.failure_kind is None
    assert attempt.usage == Usage(100, 10, 512)
    assert attempt.timings is not None and attempt.timings.cache_n == 512
    assert len(call.attempts) == 1
    assert paths(server) == [CHAT_PATH]
    assert call.outcome == "valid"


# ---------------------------------------------------------------- R15 thinking, E8, E9

THINKING_TABLE = [
    pytest.param("{}", None, False, False, id="clean"),
    pytest.param("{}", "", False, False, id="empty-reasoning"),
    pytest.param("{}", "\n\n", False, False, id="blank-reasoning"),
    pytest.param("{}", " \n\r\t", False, False, id="all-four-blanks"),
    pytest.param("{}", "Let me think", True, False, id="reasoning"),
    pytest.param("{}", chr(0xA0), True, False, id="no-break-space-is-text"),
    pytest.param("{}", chr(0x0B), True, False, id="vertical-tab-is-text"),
    pytest.param("<think>\n\n</think>{}", None, False, True, id="empty-think-block"),
    pytest.param("</think>", None, False, True, id="closing-tag-alone"),
    pytest.param("<think>", None, False, True, id="opening-tag-alone"),
    pytest.param("<THINK>{}", None, False, False, id="upper-case"),
    pytest.param("<Think>x</Think>", None, False, False, id="mixed-case"),
    pytest.param("< think >", None, False, False, id="spaced"),
    pytest.param(QUOTED_TAG, None, False, False, id="quoted-in-a-claim"),
    pytest.param("<think>plan</think>" + QUOTED_TAG, None, False, True, id="block-then-json"),
    pytest.param(QUOTED_TAG + "</think>", None, False, True, id="json-then-tag"),
    pytest.param('{"claims": [{"text": "cut <think>', None, False, False, id="cut-off-inside-a-string"),
    pytest.param('{"a": "x' + chr(92), None, False, False, id="cut-off-right-after-a-backslash"),
    pytest.param("reasoning ... </think>" + QUOTED_TAG, None, False, True, id="text-then-tag-then-json"),
    pytest.param(" \n\r\t" + QUOTED_TAG, None, False, False, id="leading-blanks"),
    pytest.param(chr(0xA0) + QUOTED_TAG, None, False, True, id="other-leading-space-is-not-json"),
    pytest.param("[1, <think>]", None, False, True, id="array-outside-a-string"),
    pytest.param('["<think>"]', None, False, False, id="array-inside-a-string"),
    pytest.param('"<think>"', None, False, True, id="a-json-string-is-not-an-object"),
    pytest.param('{"a": "x\\"<think>"}', None, False, False, id="escaped-quote-stays-inside"),
    pytest.param('{"a": "x\\\\"}<think>', None, False, True, id="escaped-backslash-closes"),
    pytest.param('{"a": "x\\\\"<think>', None, False, True, id="tag-right-after-the-string"),
    pytest.param("{\\<think>", None, False, True, id="a-backslash-outside-a-string-skips-nothing"),
    pytest.param('{"a": 1}<think', None, False, False, id="half-a-tag"),
    pytest.param("plain text", None, False, False, id="plain-text"),
    pytest.param("", None, False, False, id="empty-content"),
    pytest.param("<think>x</think>{}", "Let me think", True, True, id="both"),
]


@pytest.mark.parametrize(("content", "reasoning", "in_reasoning", "in_content"), THINKING_TABLE)
def test_r15_the_table(content: str, reasoning: str | None, in_reasoning: bool, in_content: bool) -> None:
    check = thinking_check(content, reasoning)
    assert check == ThinkingCheck(in_reasoning, in_content)
    assert check.seen is (in_reasoning or in_content)


@pytest.mark.parametrize(("content", "reasoning", "in_reasoning", "in_content"), THINKING_TABLE)
def test_r15_every_row_reaches_the_record_and_changes_nothing_else(
    content: str, reasoning: str | None, in_reasoning: bool, in_content: bool
) -> None:
    call, server = run_extract(
        chat_response(content, reasoning_content=reasoning), input_tokens_response(900), chat_response(EMPTY)
    )
    first = call.attempts[0]
    assert first.thinking == ThinkingCheck(in_reasoning, in_content)
    assert first.reasoning_content == reasoning
    assert call.thinking_seen is (in_reasoning or in_content)
    try:  # H1's verdict alone decides the attempt
        parse_extraction(content, source_id="syn_a", request_segment_ids=SEGMENT_IDS, finish_reason="stop")
    except ExtractionInvalid as exc:
        assert (first.failure_kind, first.error_text) == (exc.kind, exc.error_text)
        assert call.attempts[1].retry_type == "repair"
        assert second_body(server) == repair_bytes(SINGLE, content, exc.error_text)
        assert b"Let me think" not in second_body(server)
    else:
        assert (first.failure_kind, len(call.attempts)) == (None, 1)


def test_r15_e8_reasoning_on_a_valid_output_is_no_reason_to_retry() -> None:
    call, server = run_extract(chat_response(EMPTY, reasoning_content="Let me check the segments."))
    assert len(call.attempts) == 1
    assert paths(server) == [CHAT_PATH]
    first = call.attempts[0]
    assert first.failure_kind is None
    assert first.thinking == ThinkingCheck(True, False)
    assert first.reasoning_content == "Let me check the segments."
    assert call.outcome == "valid"
    assert call.thinking_seen is True


def test_r15_e9_a_tag_quoted_in_a_claim_is_not_thinking() -> None:
    call, _server = run_extract(chat_response(QUOTED_TAG))
    assert call.attempts[0].thinking == ThinkingCheck(False, False)
    assert call.outcome == "valid"
    assert call.thinking_seen is False
    assert isinstance(call.output, Extraction)
    assert call.output.claims == (ExtractedClaim(0, "He typed <think> into the chat", ("seg_001",)),)


def test_r15_e9_a_tag_before_the_json_fails_h1_s_parse_in_its_own_right() -> None:
    content = "<think>plan</think>" + QUOTED_TAG
    call, server = run_extract(chat_response(content), input_tokens_response(900), chat_response(EMPTY))
    first, second = call.attempts
    assert first.thinking == ThinkingCheck(False, True)
    assert (first.failure_kind, first.error_text) == ("invalid_output", FIRST_CHAR)
    assert second.retry_type == "repair"
    assert second_body(server) == repair_bytes(SINGLE, content, FIRST_CHAR)
    assert call.thinking_seen is True


def test_r15_thinking_is_read_on_every_attempt_and_on_a_cut_off() -> None:
    call, server = run_extract(
        chat_response("<think>", finish_reason="length"),
        input_tokens_response(900),
        chat_response(EMPTY, reasoning_content="r"),
    )
    assert [attempt.thinking for attempt in call.attempts] == [ThinkingCheck(False, True), ThinkingCheck(True, False)]
    assert second_body(server) == repair_bytes(SINGLE, "<think>", CUT_OFF_TEXT)


@pytest.mark.parametrize("constrained", [True, False], ids=["constrained", "unconstrained"])
@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_r15_r47_thinking_is_read_on_every_kind_and_form(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    content = valid_content(kind)
    call, _server = run_call(request, chat_response(content, reasoning_content="Let me think"))
    assert call.attempts[0].thinking == ThinkingCheck(True, False)
    assert call.attempts[0].reasoning_content == "Let me think"
    assert call.thinking_seen is True
    tagged = "<think>plan</think>" + content
    call, server = run_call(request, chat_response(tagged), input_tokens_response(900), chat_response(content))
    assert call.attempts[0].thinking == ThinkingCheck(False, True)
    assert second_body(server) == repair_bytes(request, tagged, FIRST_CHAR)


@pytest.mark.parametrize("constrained", [True, False], ids=["constrained", "unconstrained"])
@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_r15_r47_thinking_is_read_on_a_cut_off_and_on_attempt_2_of_every_kind_and_form(
    kind: Any, constrained: bool
) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    call, server = run_call(
        request,
        chat_response("<think>", finish_reason="length", reasoning_content="c"),
        input_tokens_response(900),
        chat_response(valid_content(kind), reasoning_content="r"),
    )
    assert [attempt.thinking for attempt in call.attempts] == [ThinkingCheck(True, True), ThinkingCheck(True, False)]
    assert [attempt.reasoning_content for attempt in call.attempts] == ["c", "r"]
    assert second_body(server) == repair_bytes(request, "<think>", CUT_OFF_TEXT)


def test_r15_only_the_second_attempt_can_show_thinking_too() -> None:
    call, server = run_extract(httpx.ReadTimeout, chat_response(EMPTY, reasoning_content="r"))
    assert call.attempts[0].thinking is None
    assert call.thinking_seen is True
    assert second_body(server) == SINGLE.body_bytes()


def test_r15_no_response_is_none_and_not_clean() -> None:
    call, server = run_extract(httpx.ReadTimeout, error_response(500, "server_error", "boom"))
    assert [attempt.thinking for attempt in call.attempts] == [None, None]
    assert call.thinking_seen is False
    assert second_body(server) == SINGLE.body_bytes()


@pytest.mark.parametrize("fields", [("x", False), (False, 0), (None, None), (1, 1)])
def test_r15_a_thinking_check_holds_real_bools(fields: tuple[Any, Any]) -> None:
    with pytest.raises(ValueError):
        ThinkingCheck(*fields)


# ---------------------------------------------------------------- R16 first attempt


def test_r16_a_valid_first_attempt_ends_the_call_with_no_count() -> None:
    call, server = run_extract(chat_response(EMPTY))
    assert paths(server) == [CHAT_PATH]
    assert server.requests[0].content == SINGLE.body_bytes()
    assert len(call.attempts) == 1
    assert (call.attempts[0].attempt, call.attempts[0].retry_type) == (1, "none")
    assert call.outcome == "valid"
    assert call.output == EMPTY_OUTPUT
    assert call.context_checks == ()
    assert call.total_wall_ns == call.attempts[0].wall_ns


# ---------------------------------------------------------------- R17 repair, E6, E7, E27, E32

REPAIRED_MESSAGES = [
    {"content": "S", "role": "system"},
    {"content": "U", "role": "user"},
    {"content": BAD_SEGMENT, "role": "assistant"},
    {
        "content": "Fix:\nclaims.0.segmentIds.0: 'seg_9' is not a segment id of this request\nAnswer again.",
        "role": "user",
    },
]


def test_r17_the_repair_carries_the_original_messages_the_output_and_the_error() -> None:
    call, server = run_extract(chat_response(BAD_SEGMENT), input_tokens_response(900), chat_response(EMPTY))
    assert call.attempts[0].error_text == BAD_SEGMENT_ERROR
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH]
    assert server.requests[1].content == server.requests[2].content
    assert json.loads(server.requests[2].content)["messages"] == REPAIRED_MESSAGES
    assert server.requests[2].content == repair_bytes(SINGLE, BAD_SEGMENT, BAD_SEGMENT_ERROR)
    assert [attempt.retry_type for attempt in call.attempts] == ["none", "repair"]
    assert (call.outcome, call.output) == ("valid", EMPTY_OUTPUT)


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(SINGLE, id="single"),
        pytest.param(CAP, id="cap"),
        pytest.param(PROBE, id="probe"),
        pytest.param(ChatRequest("single_conversation_extraction", MESSAGES, constrained=False), id="single-free"),
        pytest.param(ChatRequest("cap_session_extraction", MESSAGES, constrained=False), id="cap-free"),
        pytest.param(ChatRequest("reasoning_probe", MESSAGES, constrained=False), id="probe-free"),
    ],
)
def test_r17_the_repair_is_at_the_same_settings(request_: ChatRequest) -> None:
    content = valid_content(request_.kind)
    call, server = run_call(request_, chat_response("not json"), input_tokens_response(900), chat_response(content))
    first = json.loads(completions(server)[0].content)
    second = json.loads(second_body(server))
    assert second["messages"][:2] == first["messages"]
    assert [message["role"] for message in second["messages"][2:]] == ["assistant", "user"]
    del first["messages"], second["messages"]
    assert first == second
    assert second_body(server) == repair_bytes(request_, "not json", FIRST_CHAR)
    assert call.attempts[1].max_tokens == call.attempts[0].max_tokens
    assert call.attempts[1].timeout_sec == call.attempts[0].timeout_sec


def test_r17_v5_reaches_the_wire() -> None:
    partial = '{"claims": ['
    client, server = make_client(
        chat_response(partial, finish_reason="length"), input_tokens_response(900), chat_response(EMPTY)
    )
    template = RepairTemplate("Fix: <<VALIDATION_ERRORS>>")
    client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=template)
    assert len(second_body(server)) == 1429
    assert sha256(second_body(server)) == "686cc2041d484cf2e3b072e5ca7a4c132440cb3af1caf527839334f0cef57cbb"


def test_r17_the_error_of_a_missing_key_reaches_the_user_turn() -> None:
    content = '{"claims": []}'
    call, server = run_extract(chat_response(content), input_tokens_response(900), chat_response(EMPTY))
    assert json.loads(second_body(server))["messages"][-2:] == [
        {"content": content, "role": "assistant"},
        {"content": "Fix:\nnonCheckable: Field required\nAnswer again.", "role": "user"},
    ]
    assert second_body(server) == repair_bytes(SINGLE, content, "nonCheckable: Field required")
    assert call.outcome == "valid"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("  not json \n", id="surrounding-whitespace"),
        pytest.param("é " + chr(0x1F600), id="non-ascii"),
        pytest.param('"\\', id="escapes"),
    ],
)
def test_r17_amb_40_the_output_goes_back_exactly_as_received(content: str) -> None:
    call, server = run_extract(chat_response(content), input_tokens_response(900), chat_response(EMPTY))
    assert call.attempts[0].content == content
    assert json.loads(second_body(server))["messages"][2] == {"content": content, "role": "assistant"}
    assert second_body(server) == repair_bytes(SINGLE, content, call.attempts[0].error_text)


def test_r17_amb_40_reasoning_text_never_goes_back() -> None:
    call, server = run_extract(
        chat_response("not json", reasoning_content="secret reasoning"),
        input_tokens_response(900),
        chat_response(EMPTY),
    )
    assert call.attempts[0].reasoning_content == "secret reasoning"
    assert b"secret reasoning" not in server.requests[1].content
    assert second_body(server) == repair_bytes(SINGLE, "not json", FIRST_CHAR)


def test_r17_e7_a_cut_off_goes_back_as_it_stopped() -> None:
    partial = '{"claims": [{"segmentIds": ["seg_001"], "text": "cut'
    call, server = run_extract(
        chat_response(partial, finish_reason="length"),
        input_tokens_response(900),
        chat_response(partial, finish_reason="length"),
    )
    first, second = call.attempts
    assert (first.failure_kind, first.error_text) == ("cut_off", CUT_OFF_TEXT)
    assert (second.failure_kind, second.error_text) == ("cut_off", CUT_OFF_TEXT)
    assert json.loads(second_body(server))["messages"] == [
        {"content": "S", "role": "system"},
        {"content": "U", "role": "user"},
        {"content": partial, "role": "assistant"},
        {"content": f"Fix:\n{CUT_OFF_TEXT}\nAnswer again.", "role": "user"},
    ]
    assert second_body(server) == repair_bytes(SINGLE, partial, CUT_OFF_TEXT)
    assert (call.outcome, call.output, call.cut_off_attempts) == ("cut_off", None, 2)


def test_r17_e27_complete_json_at_the_cap_is_still_cut_off() -> None:
    call, server = run_extract(
        chat_response(EMPTY, finish_reason="length"), input_tokens_response(900), chat_response(EMPTY)
    )
    first, second = call.attempts
    assert (first.failure_kind, first.error_text, first.finish_reason) == ("cut_off", CUT_OFF_TEXT, "length")
    assert first.content == EMPTY
    assert second.retry_type == "repair"
    assert second_body(server) == repair_bytes(SINGLE, EMPTY, CUT_OFF_TEXT)
    assert (call.outcome, call.cut_off_attempts, len(call.context_checks)) == ("valid", 1, 1)


def test_r17_e32_an_empty_output_is_repaired_with_an_empty_assistant_turn() -> None:
    call, server = run_extract(chat_response(""), input_tokens_response(900), chat_response(EMPTY))
    first = call.attempts[0]
    assert (first.failure_kind, first.error_text, first.content) == ("invalid_output", FIRST_CHAR, "")
    assert json.loads(second_body(server))["messages"][2] == {"content": "", "role": "assistant"}
    assert second_body(server) == repair_bytes(SINGLE, "", FIRST_CHAR)


def test_r17_e6_a_repair_that_times_out_is_the_final_result() -> None:
    clock = FakeClock(0, 100, 200, 210, 300, 30_000_000_300)
    call, server = run_extract(chat_response("not json"), input_tokens_response(900), httpx.ReadTimeout, clock=clock)
    first, second = call.attempts
    assert (first.failure_kind, first.error_text) == ("invalid_output", FIRST_CHAR)
    assert (second.retry_type, second.failure_kind, second.http_status) == ("repair", "timeout", None)
    assert second.content is None
    assert second_body(server) == repair_bytes(SINGLE, "not json", FIRST_CHAR)
    assert call.outcome == "timeout"
    assert call.total_wall_ns == 100 + 30_000_000_000
    detail = "timeout: ReadTimeout after the 30 s limit"
    assert call.extraction_result() == ExtractionFailure("syn_a", "timeout", detail)


def test_r17_repair_request_alone() -> None:
    repair = repair_request(SINGLE, invalid_output="out", error_text="err", template=TEMPLATE)
    assert repair.kind == SINGLE.kind
    assert repair.constrained is True
    assert repair.messages == MESSAGES + (
        ChatMessage("assistant", "out"),
        ChatMessage("user", "Fix:\nerr\nAnswer again."),
    )
    free = ChatRequest("reasoning_probe", MESSAGES, constrained=False)
    assert repair_request(free, invalid_output="", error_text="e", template=TEMPLATE).constrained is False


# ---------------------------------------------------------------- R18 same-request retry


def test_r18_after_a_timeout_the_same_bytes_go_again() -> None:
    call, server = run_extract(httpx.ReadTimeout, chat_response(EMPTY))
    assert paths(server) == [CHAT_PATH, CHAT_PATH]
    assert server.requests[0].content == server.requests[1].content == SINGLE.body_bytes()
    first, second = call.attempts
    assert (first.failure_kind, second.failure_kind) == ("timeout", None)
    assert second.retry_type == "same_request"
    assert first.request_sha256 == second.request_sha256
    assert (call.outcome, call.output, call.context_checks) == ("valid", EMPTY_OUTPUT, ())


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(lambda: httpx.ReadTimeout, id="timeout"),
        pytest.param(lambda: error_response(500, "server_error", "boom"), id="http-500"),
        pytest.param(lambda: httpx.ConnectError, id="connect-error"),
        pytest.param(lambda: httpx.Response(200, content=b"oops"), id="malformed-200"),
    ],
)
def test_r18_amb_47_the_retry_goes_at_once(step: Callable[[], Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_seconds: float) -> None:
        raise AssertionError("no delay between attempts")

    monkeypatch.setattr(time, "sleep", refuse)
    clock = FakeClock(0, 10, 11, 21)
    call, server = run_extract(step(), chat_response(EMPTY), clock=clock)
    assert clock.ticks == []
    assert second_body(server) == SINGLE.body_bytes()
    assert call.outcome == "valid"


def test_section_3_the_repair_goes_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_seconds: float) -> None:
        raise AssertionError("no delay between attempts")

    monkeypatch.setattr(time, "sleep", refuse)
    _call, server = run_extract(chat_response("not json"), input_tokens_response(900), chat_response(EMPTY))
    assert second_body(server) == repair_bytes(SINGLE, "not json", FIRST_CHAR)


# ---------------------------------------------------------------- R19 at most two attempts, E17


def test_r19_e17_no_repair_follows_a_same_request_retry() -> None:
    call, server = run_extract(error_response(500, "server_error", "boom"), chat_response("not json"))
    assert [attempt.retry_type for attempt in call.attempts] == ["none", "same_request"]
    assert paths(server) == [CHAT_PATH, CHAT_PATH]
    assert second_body(server) == SINGLE.body_bytes()
    assert (call.outcome, call.output) == ("invalid_output", None)


def test_r19_nothing_follows_a_repair() -> None:
    call, server = run_extract(
        chat_response("not json"), input_tokens_response(900), error_response(500, "server_error", "boom")
    )
    assert [attempt.retry_type for attempt in call.attempts] == ["none", "repair"]
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH]
    assert second_body(server) == repair_bytes(SINGLE, "not json", FIRST_CHAR)
    assert (call.outcome, call.output) == ("server_error", None)


def test_r19_the_scored_result_is_the_retry_s() -> None:
    claim = '{"claims": [{"text": "x", "segmentIds": ["seg_001"]}], "nonCheckable": []}'
    call, server = run_extract(chat_response(BAD_SEGMENT), input_tokens_response(900), chat_response(claim))
    assert call.output == Extraction("syn_a", (ExtractedClaim(0, "x", ("seg_001",)),), ())
    assert call.extraction_result() == call.output
    assert second_body(server) == repair_bytes(SINGLE, BAD_SEGMENT, BAD_SEGMENT_ERROR)


# ---------------------------------------------------------------- R21 foreign exceptions, E16


@pytest.mark.parametrize(
    "error", [ValueError("boom"), TypeError("boom"), KeyError("boom")], ids=lambda e: type(e).__name__
)
def test_r21_e16_an_extraction_validator_s_own_fault_propagates(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(client_module, "parse_extraction", fail)
    client, server = make_client(chat_response(EMPTY))
    with pytest.raises(type(error)) as caught:
        client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert caught.value is error
    assert len(server.requests) == 1


def test_r21_a_probe_validator_s_own_fault_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("boom")

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(client_module, "parse_probe", fail)
    client, server = make_client(chat_response(PROBE_OK))
    with pytest.raises(ValueError) as caught:
        client.probe(PROBE, item_id="probe_01", bundle_source_ids=BUNDLE, repair_template=TEMPLATE)
    assert caught.value is error
    assert len(server.requests) == 1


def test_r21_a_pydantic_error_from_a_validator_is_a_harness_bug_too(monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        _Int.model_validate({"value": "x"})
    except ValidationError as exc:
        error = exc

    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(client_module, "parse_extraction", fail)
    client, server = make_client(chat_response(EMPTY))
    with pytest.raises(ValidationError) as caught:
        client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert caught.value is error
    assert len(server.requests) == 1


def test_r21_a_fault_on_the_second_attempt_propagates_too(monkeypatch: pytest.MonkeyPatch) -> None:
    error = ValueError("boom")
    raised = iter([ExtractionInvalid("invalid_output", "first"), error])

    def fail(*_args: object, **_kwargs: object) -> None:
        raise next(raised)

    monkeypatch.setattr(client_module, "parse_extraction", fail)
    client, server = make_client(chat_response("x"), input_tokens_response(900), chat_response(EMPTY))
    with pytest.raises(ValueError) as caught:
        client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert caught.value is error
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH]
    assert second_body(server) == repair_bytes(SINGLE, "x", "first")


# ---------------------------------------------------------------- R22 wall time


def test_r22_the_clock_example() -> None:
    clock = FakeClock(0, 1_200_000_000, 1_300_000_000, 1_310_000_000, 1_400_000_000, 2_000_000_000)
    call, server = run_extract(
        chat_response(BAD_SEGMENT), input_tokens_response(900), chat_response(EMPTY), clock=clock
    )
    assert call.attempts[0].wall_ns == 1_200_000_000
    assert call.context_checks[0].wall_ns == 10_000_000
    assert call.attempts[1].wall_ns == 600_000_000
    assert call.total_wall_ns == 1_800_000_000
    assert clock.ticks == []
    assert second_body(server) == repair_bytes(SINGLE, BAD_SEGMENT, BAD_SEGMENT_ERROR)


def test_r22_a_timeout_counts_its_full_time() -> None:
    clock = FakeClock(5, 30_000_000_005, 40_000_000_000, 40_000_000_100)
    call, server = run_extract(httpx.ReadTimeout, chat_response(EMPTY), clock=clock)
    assert [attempt.wall_ns for attempt in call.attempts] == [30_000_000_000, 100]
    assert call.total_wall_ns == 30_000_000_100
    assert clock.ticks == []
    assert second_body(server) == SINGLE.body_bytes()


def test_r9_amb_33_a_response_past_the_limit_is_not_a_timeout() -> None:
    clock = FakeClock(0, 31_000_000_000)
    call, _server = run_extract(chat_response(EMPTY), clock=clock)
    assert call.attempts[0].wall_ns == 31_000_000_000
    assert call.attempts[0].failure_kind is None
    assert call.outcome == "valid"


def test_r9_wall_time_is_never_clipped() -> None:
    clock = FakeClock(0, 45_000_000_123, 45_000_000_124, 345_000_000_999)
    call, server = run_extract(httpx.ReadTimeout, httpx.ReadTimeout, request=CAP, clock=clock)
    assert [attempt.wall_ns for attempt in call.attempts] == [45_000_000_123, 300_000_000_875]
    assert second_body(server) == CAP.body_bytes()


def test_r22_only_the_exchange_is_timed(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    def traced(name: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            events.append(name)
            return real(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(client_module, "canonical_json", traced("encode", client_module.canonical_json))
    monkeypatch.setattr(client_module, "_parse_completion", traced("parse", client_module._parse_completion))
    monkeypatch.setattr(client_module, "parse_extraction", traced("validate", client_module.parse_extraction))

    def clock() -> int:
        events.append("clock")
        return 0

    def answer(_request: httpx.Request) -> httpx.Response:
        events.append("send")
        return chat_response(EMPTY)

    run_extract(answer, clock=clock)
    assert events == ["encode", "clock", "send", "clock", "parse", "validate"]


def refused() -> httpx.Response:
    raise httpx.ConnectError("scripted")


@pytest.mark.parametrize(
    ("reader", "step"),
    [
        pytest.param("_http_fault", lambda: error_response(500, "server_error", "boom"), id="http-error"),
        pytest.param("_transport_fault", refused, id="transport-error"),
    ],
)
def test_r22_a_failed_exchange_is_read_after_the_timed_span(
    reader: str, step: Callable[[], httpx.Response], monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real = getattr(client_module, reader)

    def traced(*args: Any) -> Any:
        events.append("fault")
        return real(*args)

    def clock() -> int:
        events.append("clock")
        return 0

    def answer(_request: httpx.Request) -> httpx.Response:
        events.append("send")
        return step()

    monkeypatch.setattr(client_module, reader, traced)
    _call, server = run_extract(answer, answer, clock=clock)
    assert events == ["clock", "send", "clock", "fault"] * 2
    assert second_body(server) == SINGLE.body_bytes()


def test_r22_a_failed_exchange_reads_the_clock_twice_too() -> None:
    clock = FakeClock(1, 2, 3, 4)
    call, server = run_extract(httpx.ConnectError, httpx.ConnectError, clock=clock)
    assert clock.ticks == []
    assert [attempt.wall_ns for attempt in call.attempts] == [1, 1]
    assert second_body(server) == SINGLE.body_bytes()


def test_r22_real_clock_values_are_non_negative_ints() -> None:
    call, _server = run_extract(chat_response(EMPTY))
    assert type(call.attempts[0].wall_ns) is int and call.attempts[0].wall_ns >= 0
    assert call.total_wall_ns == call.attempts[0].wall_ns


# ---------------------------------------------------------------- R23 no side effects


def test_r23_a_call_writes_prints_and_logs_nothing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.DEBUG, logger="app"):
        _call, repaired = run_extract(chat_response("not json"), input_tokens_response(900), chat_response(EMPTY))
        _call, retried = run_probe(httpx.ReadTimeout, chat_response(PROBE_OK))
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")
    assert list(tmp_path.iterdir()) == []
    assert [record for record in caplog.records if record.name.startswith("app")] == []
    assert second_body(repaired) == repair_bytes(SINGLE, "not json", FIRST_CHAR)
    assert second_body(retried) == PROBE.body_bytes()


SURROGATE = chr(0xD800)


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(lambda: error_response(500, SURROGATE, "boom"), id="error-type"),
        pytest.param(lambda: error_response(500, "server_error", SURROGATE), id="error-message"),
        pytest.param(lambda: httpx.ConnectError(SURROGATE), id="exception-text"),
        pytest.param(lambda: chat_response(EMPTY, extra={SURROGATE: 1}), id="ignored-top-level-key"),
        pytest.param(lambda: with_value(("choices", 0, "message", SURROGATE), 1), id="message-key"),
        pytest.param(lambda: with_value(("model",), SURROGATE), id="model"),
        pytest.param(lambda: with_value(("system_fingerprint",), SURROGATE), id="system-fingerprint"),
        pytest.param(lambda: chat_response(SURROGATE), id="content"),
        pytest.param(lambda: chat_response(EMPTY, reasoning_content=SURROGATE), id="reasoning-content"),
    ],
)
def test_r23_a_lone_surrogate_in_the_response_envelope_cannot_break_a_record(step: Callable[[], Any]) -> None:
    call, server = run_extract(step(), chat_response(EMPTY))
    assert isinstance(canonical_json(dataclasses.asdict(call)), bytes)
    assert [sent.content for sent in completions(server)] == [SINGLE.body_bytes()] * len(call.attempts)


def test_r23_an_unencodable_error_type_is_kept_in_escaped_form() -> None:
    escaped = chr(92) + "ud800"
    call, server = run_extract(error_response(500, SURROGATE, "boom"), chat_response(EMPTY))
    first = call.attempts[0]
    assert first.error_text == f"HTTP 500: {escaped}: boom"
    assert first.server_error_type == escaped
    assert second_body(server) == SINGLE.body_bytes()


def test_r23_a_record_passes_canonical_json() -> None:
    extraction, repaired = run_extract(chat_response(BAD_SEGMENT), input_tokens_response(900), chat_response(EMPTY))
    probe, retried = run_probe(httpx.ReadTimeout, chat_response(PROBE_OK))
    assert json.loads(canonical_json(dataclasses.asdict(extraction)))["outcome"] == "valid"
    stored = json.loads(canonical_json(dataclasses.asdict(probe)))
    assert stored["output"] == {"cited_source_ids": ["src_1"], "reason": "r", "verdict": "SUPPORTED"}
    assert stored["attempts"][0]["failure_kind"] == "timeout"
    assert second_body(repaired) == repair_bytes(SINGLE, BAD_SEGMENT, BAD_SEGMENT_ERROR)
    assert second_body(retried) == PROBE.body_bytes()


# ---------------------------------------------------------------- R47 the attempt matrix

FIRST_STEPS = ("valid", "invalid_output", "cut_off", "timeout", "http_500", "malformed_200", "connect_error")
SECOND_STEPS = ("valid", "invalid_output", "cut_off", "timeout", "server_error")


def matrix_step(name: str, kind: str) -> Any:
    content = valid_content(kind)
    steps: dict[str, Callable[[], Any]] = {
        "valid": lambda: chat_response(content),
        "invalid_output": lambda: chat_response("not json"),
        "cut_off": lambda: chat_response(content, finish_reason="length"),
        "timeout": lambda: httpx.ReadTimeout,
        "http_500": lambda: error_response(500, "server_error", "boom"),
        "server_error": lambda: error_response(500, "server_error", "boom"),
        "malformed_200": lambda: httpx.Response(200, content=b"oops"),
        "connect_error": lambda: httpx.ConnectError,
    }
    return steps[name]()


def matrix_expected(name: str, limit: float) -> tuple[str | None, int | None, str]:
    expected: dict[str, tuple[str | None, int | None, str]] = {
        "valid": (None, 200, ""),
        "invalid_output": ("invalid_output", 200, FIRST_CHAR),
        "cut_off": ("cut_off", 200, CUT_OFF_TEXT),
        "timeout": ("timeout", None, f"timeout: ReadTimeout after the {limit:g} s limit"),
        "http_500": ("server_error", 500, "HTTP 500: server_error: boom"),
        "server_error": ("server_error", 500, "HTTP 500: server_error: boom"),
        "malformed_200": ("server_error", 200, f"malformed response: {FIRST_CHAR}"),
        "connect_error": ("server_error", None, "transport error: ConnectError: scripted"),
    }
    return expected[name]


def outcome_of(attempt: AttemptRecord) -> tuple[str | None, int | None, str]:
    return attempt.failure_kind, attempt.http_status, attempt.error_text


def valid_output(kind: str) -> Extraction | ProbeOutput:
    return ProbeOutput("SUPPORTED", "r", ("src_1",)) if kind == "reasoning_probe" else EMPTY_OUTPUT


@pytest.mark.parametrize("constrained", [True, False], ids=["constrained", "unconstrained"])
@pytest.mark.parametrize("kind", REQUEST_KINDS)
@pytest.mark.parametrize("second", SECOND_STEPS)
@pytest.mark.parametrize("first", FIRST_STEPS)
def test_r47_the_attempt_matrix(first: str, second: str, kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    repairs = first in ("invalid_output", "cut_off")
    steps = [matrix_step(first, kind)]
    if repairs:
        steps.append(input_tokens_response(900))
    steps.append(matrix_step(second, kind))
    clock = FakeClock(0, 1_000, *((2_000, 2_010) if repairs else ()), 3_000, 3_500)
    call, server = run_call(request, *steps, clock=clock)
    limit = request.timeout_sec

    assert server.requests[0].content == request.body_bytes()
    assert outcome_of(call.attempts[0]) == matrix_expected(first, limit)
    assert (call.constrained, call.request_kind) == (constrained, kind)
    if first == "valid":
        assert paths(server) == [CHAT_PATH]
        assert len(server.steps) == 1  # attempt 2's step was never played
        assert len(call.attempts) == 1
        assert (call.outcome, call.output, call.context_checks) == ("valid", valid_output(kind), ())
        assert call.total_wall_ns == 1_000
        assert call.first_attempt_failed is False
        return

    assert server.steps == []
    assert clock.ticks == []
    assert paths(server) == ([CHAT_PATH, COUNT_PATH, CHAT_PATH] if repairs else [CHAT_PATH, CHAT_PATH])
    assert outcome_of(call.attempts[1]) == matrix_expected(second, limit)
    assert [attempt.attempt for attempt in call.attempts] == [1, 2]
    assert [attempt.retry_type for attempt in call.attempts] == ["none", "repair" if repairs else "same_request"]
    if repairs:
        content = "not json" if first == "invalid_output" else valid_content(kind)
        expected = repair_bytes(request, content, matrix_expected(first, limit)[2])
        assert server.requests[1].content == expected
        assert expected != request.body_bytes()
        assert call.attempts[1].request_sha256 != call.attempts[0].request_sha256
    else:
        expected = request.body_bytes()
        assert call.attempts[1].request_sha256 == call.attempts[0].request_sha256
    assert second_body(server) == expected
    assert call.attempts[1].request_sha256 == sha256(expected)

    assert call.outcome == second
    assert call.output == (valid_output(kind) if second == "valid" else None)
    assert call.total_wall_ns == 1_500
    assert [attempt.wall_ns for attempt in call.attempts] == [1_000, 500]
    assert len(call.context_checks) == (1 if repairs else 0)
    assert call.first_attempt_failed is True
    assert call.cut_off_attempts == [first, second].count("cut_off")
    assert isinstance(canonical_json(dataclasses.asdict(call)), bytes)
    if kind != "reasoning_probe":
        result = call.extraction_result()
        if second == "valid":
            assert result == EMPTY_OUTPUT
        else:
            assert result == ExtractionFailure("syn_a", second, detail=call.attempts[1].error_text)  # type: ignore[arg-type]


# ---------------------------------------------------------------- R30 the extraction validator, E31, E33


def test_r30_parse_extraction_gets_the_raw_content_the_ids_and_the_finish_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    real = client_module.parse_extraction

    def spy(*args: Any, **kwargs: Any) -> Extraction:
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(client_module, "parse_extraction", spy)
    ids = {"seg_001", "seg_002"}
    client, _server = make_client(chat_response(" " + EMPTY + " "))
    call = client.extract(CAP, item_id="cap_1", request_segment_ids=ids, repair_template=TEMPLATE)
    assert seen == [
        ((" " + EMPTY + " ",), {"source_id": "cap_1", "request_segment_ids": ids, "finish_reason": "stop"}),
    ]
    assert type(seen[0][1]["request_segment_ids"]) is frozenset
    assert call.output == Extraction("cap_1", (), ())


def test_r30_a_cut_off_reaches_the_validator_as_length(monkeypatch: pytest.MonkeyPatch) -> None:
    reasons: list[str] = []
    real = client_module.parse_extraction

    def spy(*args: Any, **kwargs: Any) -> Extraction:
        reasons.append(kwargs["finish_reason"])
        return real(*args, **kwargs)

    monkeypatch.setattr(client_module, "parse_extraction", spy)
    call, server = run_extract(
        chat_response(EMPTY, finish_reason="length"), input_tokens_response(900), chat_response(EMPTY)
    )
    assert reasons == ["length", "stop"]
    assert call.outcome == "valid"
    assert second_body(server) == repair_bytes(SINGLE, EMPTY, CUT_OFF_TEXT)


def test_r30_the_output_belongs_to_the_item() -> None:
    claim = '{"claims": [{"text": "x", "segmentIds": ["seg_001"]}], "nonCheckable": []}'
    client, _server = make_client(chat_response(claim))
    call = client.extract(SINGLE, item_id="syn_b", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert call.item_id == "syn_b"
    assert call.output == Extraction("syn_b", (ExtractedClaim(0, "x", ("seg_001",)),), ())


def test_r30_e31_a_cap_session_needs_the_re_keyed_ids() -> None:
    raw = '{"claims": [{"text": "x", "segmentIds": ["seg_001"]}], "nonCheckable": []}'
    error = "claims.0.segmentIds.0: 'seg_001' is not a segment id of this request"
    fixed = '{"claims": [{"text": "x", "segmentIds": ["syn_a:seg_001"]}], "nonCheckable": []}'
    client, server = make_client(chat_response(raw), input_tokens_response(900), chat_response(fixed))
    ids = {"syn_a:seg_001", "syn_b:seg_001"}
    call = client.extract(CAP, item_id="cap_1", request_segment_ids=ids, repair_template=TEMPLATE)
    assert (call.attempts[0].failure_kind, call.attempts[0].error_text) == ("invalid_output", error)
    assert second_body(server) == repair_bytes(CAP, raw, error)
    assert call.output == Extraction("cap_1", (ExtractedClaim(0, "x", ("syn_a:seg_001",)),), ())


@pytest.mark.parametrize(
    "ids", [{"seg_001"}, frozenset({"seg_001"}), {"seg_001": 1}.keys()], ids=["set", "frozen", "keys"]
)
def test_r30_any_set_type_holds_the_ids(ids: Any) -> None:
    client, _server = make_client(chat_response(EMPTY))
    call = client.extract(SINGLE, item_id="syn_a", request_segment_ids=ids, repair_template=TEMPLATE)
    assert call.outcome == "valid"


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        pytest.param({"request": PROBE}, ValueError, id="e33-probe-request"),
        pytest.param({"request": "x"}, TypeError, id="request-not-a-request"),
        pytest.param({"request": None}, TypeError, id="no-request"),
        pytest.param({"item_id": ""}, ValueError, id="empty-item"),
        pytest.param({"item_id": 3}, TypeError, id="int-item"),
        pytest.param({"item_id": None}, TypeError, id="no-item"),
        pytest.param({"request_segment_ids": frozenset()}, ValueError, id="e33-empty-set"),
        pytest.param({"request_segment_ids": {1}}, ValueError, id="int-id"),
        pytest.param({"request_segment_ids": {"seg_001", 1}}, ValueError, id="one-int-id"),
        pytest.param({"request_segment_ids": ["seg_001"]}, TypeError, id="a-list"),
        pytest.param({"request_segment_ids": "seg_001"}, TypeError, id="a-str"),
        pytest.param({"request_segment_ids": None}, TypeError, id="no-ids"),
        pytest.param({"repair_template": "Fix: <<VALIDATION_ERRORS>>"}, TypeError, id="template-as-text"),
        pytest.param({"repair_template": None}, TypeError, id="no-template"),
    ],
)
def test_r30_e33_extract_checks_everything_before_sending(changes: dict[str, Any], error: type[Exception]) -> None:
    client, server = make_client()
    arguments: dict[str, Any] = {
        "request": SINGLE,
        "item_id": "syn_a",
        "request_segment_ids": SEGMENT_IDS,
        "repair_template": TEMPLATE,
    }
    arguments.update(changes)
    request = arguments.pop("request")
    with pytest.raises(error):
        client.extract(request, **arguments)
    assert server.requests == []


# ---------------------------------------------------------------- R31, R33-R36 the probe through the client


def probe_text(*cited: str, reason: str = "r", verdict: str = "SUPPORTED", **extra: Any) -> str:
    body = {"citations": [{"sourceId": source_id} for source_id in cited], "reason": reason, "verdict": verdict}
    return json.dumps({**body, **extra})


VERDICT_ERROR = "verdict: Input should be 'SUPPORTED', 'CONTRADICTED', 'PARTIALLY_SUPPORTED' or 'INSUFFICIENT_EVIDENCE'"


def test_r31_parse_probe_gets_the_content_the_bundle_and_the_finish_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    real = client_module.parse_probe

    def spy(*args: Any, **kwargs: Any) -> ProbeOutput:
        seen.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(client_module, "parse_probe", spy)
    padded = " " + PROBE_OK + " "
    run_probe(chat_response(padded))
    assert seen == [((padded,), {"bundle_source_ids": BUNDLE, "finish_reason": "stop"})]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        pytest.param({"request": SINGLE}, ValueError, id="extraction-request"),
        pytest.param({"request": CAP}, ValueError, id="cap-request"),
        pytest.param({"request": "x"}, TypeError, id="request-not-a-request"),
        pytest.param({"item_id": ""}, ValueError, id="empty-item"),
        pytest.param({"item_id": 3}, TypeError, id="int-item"),
        pytest.param({"bundle_source_ids": frozenset()}, ValueError, id="empty-bundle"),
        pytest.param({"bundle_source_ids": BUNDLE | {"src_6"}}, ValueError, id="e33-six-sources"),
        pytest.param({"bundle_source_ids": {""}}, ValueError, id="an-empty-id"),
        pytest.param({"bundle_source_ids": {"src_1", ""}}, ValueError, id="one-empty-id"),
        pytest.param({"bundle_source_ids": {"src_1", 1}}, ValueError, id="one-int-id"),
        pytest.param({"bundle_source_ids": {1}}, ValueError, id="an-int-id"),
        pytest.param({"bundle_source_ids": ["src_1"]}, TypeError, id="a-list"),
        pytest.param({"bundle_source_ids": "src_1"}, TypeError, id="a-str"),
        pytest.param({"bundle_source_ids": None}, TypeError, id="no-bundle"),
        pytest.param({"repair_template": "x"}, TypeError, id="template-as-text"),
    ],
)
def test_r31_e33_probe_checks_everything_before_sending(changes: dict[str, Any], error: type[Exception]) -> None:
    client, server = make_client()
    arguments: dict[str, Any] = {
        "request": PROBE,
        "item_id": "probe_01",
        "bundle_source_ids": BUNDLE,
        "repair_template": TEMPLATE,
    }
    arguments.update(changes)
    request = arguments.pop("request")
    with pytest.raises(error):
        client.probe(request, **arguments)
    assert server.requests == []


def test_r31_five_sources_are_the_most_and_one_is_enough() -> None:
    assert len(BUNDLE) == 5
    call, _server = run_probe(chat_response(PROBE_OK))
    assert call.outcome == "valid"
    client, _server = make_client(chat_response(PROBE_OK))
    one = client.probe(PROBE, item_id="probe_01", bundle_source_ids={"src_1"}, repair_template=TEMPLATE)
    assert one.outcome == "valid"


def test_r31_a_valid_probe_has_its_output_and_its_ids() -> None:
    call, server = run_probe(chat_response(probe_text("src_2", "src_2", "src_1", reason="Source 2 states 1.29.")))
    assert call.output == ProbeOutput("SUPPORTED", "Source 2 states 1.29.", ("src_2", "src_1"))
    assert call.attempts[0].cited_source_ids == ("src_2", "src_1")
    assert call.attempts[0].unknown_source_ids == ()
    assert paths(server) == [CHAT_PATH]


def test_r33_e14_a_probe_citing_an_outside_id_is_repaired() -> None:
    raw = probe_text("src_9")
    error = "citations.0.sourceId: 'src_9' is not a source id of this bundle"
    call, server = run_probe(chat_response(raw), input_tokens_response(900), chat_response(PROBE_OK))
    first, second = call.attempts
    assert (first.failure_kind, first.error_text) == ("invalid_output", error)
    assert first.cited_source_ids == ("src_9",)
    assert first.unknown_source_ids == ("src_9",)
    assert second.retry_type == "repair"
    assert second_body(server) == repair_bytes(PROBE, raw, error)
    assert (second.cited_source_ids, second.unknown_source_ids) == (("src_1",), ())
    assert (call.outcome, call.first_attempt_failed) == ("valid", True)


def test_r33_each_occurrence_is_a_line_and_each_id_is_recorded_once() -> None:
    raw = probe_text("src_2", "src_9", "SRC_2", "src_9")
    error = (
        "citations.1.sourceId: 'src_9' is not a source id of this bundle\n"
        "citations.2.sourceId: 'SRC_2' is not a source id of this bundle\n"
        "citations.3.sourceId: 'src_9' is not a source id of this bundle"
    )
    call, server = run_probe(chat_response(raw), input_tokens_response(900), chat_response(PROBE_OK))
    first = call.attempts[0]
    assert first.error_text == error
    assert first.cited_source_ids == ("src_2", "src_9", "SRC_2")
    assert first.unknown_source_ids == ("src_9", "SRC_2")
    assert second_body(server) == repair_bytes(PROBE, raw, error)


def test_r34_e15_citing_none_is_valid_with_no_repair() -> None:
    raw = probe_text(reason="No source gives the rate.", verdict="INSUFFICIENT_EVIDENCE")
    call, server = run_probe(chat_response(raw))
    first = call.attempts[0]
    assert (len(call.attempts), first.failure_kind) == (1, None)
    assert (first.cited_source_ids, first.unknown_source_ids) == ((), ())
    assert call.output == ProbeOutput("INSUFFICIENT_EVIDENCE", "No source gives the rate.", ())
    assert isinstance(call.output, ProbeOutput) and call.output.cites_none is True
    assert call.outcome == "valid"
    assert paths(server) == [CHAT_PATH]


@pytest.mark.parametrize(
    ("raw", "error", "cited"),
    [
        pytest.param(
            probe_text("src_9", verdict="MAYBE"),
            VERDICT_ERROR,
            ("src_9",),
            id="e28-bad-verdict",
        ),
        pytest.param(
            probe_text("src_1", confidence=0.9),
            "confidence: Extra inputs are not permitted",
            ("src_1",),
            id="e28-extra-key",
        ),
        pytest.param(
            '{"citations": [], "reason": " ", "verdict": "SUPPORTED"}',
            "reason: Value error, must contain a non-whitespace character",
            (),
            id="cites-none-and-invalid",
        ),
        pytest.param("Sure: " + PROBE_OK, FIRST_CHAR, None, id="text-before-the-json"),
        pytest.param("not json", FIRST_CHAR, None, id="e29-not-json"),
        pytest.param(
            '{"citations": [{"id": "src_1"}], "reason": "r", "verdict": "SUPPORTED"}',
            "citations.0.sourceId: Field required\ncitations.0.id: Extra inputs are not permitted",
            None,
            id="wrong-citation-key",
        ),
        pytest.param(
            '{"citations": ["src_1"], "reason": "r", "verdict": "SUPPORTED"}',
            "citations.0: Input should be a valid dictionary or instance of _WireCitation",
            None,
            id="bare-id",
        ),
        pytest.param(
            '{"citations": "src_1", "reason": "r", "verdict": "SUPPORTED"}',
            "citations: Input should be a valid list",
            None,
            id="citations-a-string",
        ),
    ],
)
def test_r35_e28_e29_what_gate_c_can_read_from_an_invalid_output(
    raw: str, error: str, cited: tuple[str, ...] | None
) -> None:
    call, server = run_probe(chat_response(raw), input_tokens_response(900), chat_response(PROBE_OK))
    first = call.attempts[0]
    assert (first.failure_kind, first.error_text) == ("invalid_output", error)
    assert first.cited_source_ids == cited
    assert first.unknown_source_ids == tuple(source_id for source_id in cited or () if source_id not in BUNDLE)
    assert second_body(server) == repair_bytes(PROBE, raw, error)


def test_r35_gate_c_evidence_is_read_on_the_unconstrained_pass() -> None:
    free = ChatRequest("reasoning_probe", MESSAGES, constrained=False)
    raw = probe_text("src_9", verdict="MAYBE")
    call, server = run_probe(chat_response(raw), input_tokens_response(900), chat_response(PROBE_OK), request=free)
    first = call.attempts[0]
    assert (first.failure_kind, first.error_text) == ("invalid_output", VERDICT_ERROR)
    assert (first.cited_source_ids, first.unknown_source_ids) == (("src_9",), ("src_9",))
    assert second_body(server) == repair_bytes(free, raw, VERDICT_ERROR)


def test_r35_e29_a_cut_off_cites_nothing_whatever_it_holds() -> None:
    raw = probe_text("src_9")
    call, server = run_probe(
        chat_response(raw, finish_reason="length"), input_tokens_response(900), chat_response(PROBE_OK)
    )
    first = call.attempts[0]
    assert (first.failure_kind, first.cited_source_ids, first.unknown_source_ids) == ("cut_off", None, ())
    assert call.cut_off_attempts == 1
    assert second_body(server) == repair_bytes(PROBE, raw, CUT_OFF_TEXT)


@pytest.mark.parametrize(
    "step",
    [
        pytest.param(lambda: httpx.ReadTimeout, id="e29-timeout"),
        pytest.param(lambda: error_response(500, "server_error", "boom"), id="server-error"),
        pytest.param(lambda: httpx.Response(200, content=b"oops"), id="malformed-200"),
    ],
)
def test_r35_e29_no_response_cites_nothing(step: Callable[[], Any]) -> None:
    call, server = run_probe(step(), chat_response(PROBE_OK))
    first, second = call.attempts
    assert (first.cited_source_ids, first.unknown_source_ids) == (None, ())
    assert (second.retry_type, second.cited_source_ids) == ("same_request", ("src_1",))
    assert second_body(server) == PROBE.body_bytes()


def test_r35_an_extraction_attempt_never_records_ids() -> None:
    raw = '{"citations": [{"sourceId": "src_1"}], "claims": [], "nonCheckable": []}'
    call, server = run_extract(chat_response(raw), input_tokens_response(900), chat_response(EMPTY))
    assert [attempt.cited_source_ids for attempt in call.attempts] == [None, None]
    assert [attempt.unknown_source_ids for attempt in call.attempts] == [(), ()]
    assert second_body(server) == repair_bytes(SINGLE, raw, "citations: Extra inputs are not permitted")


@pytest.mark.parametrize(
    ("steps", "outcome"),
    [
        pytest.param(lambda: (httpx.ReadTimeout, httpx.ReadTimeout), "timeout", id="timeouts"),
        pytest.param(
            lambda: (chat_response("x"), input_tokens_response(9), chat_response("x")), "invalid_output", id="invalid"
        ),
        pytest.param(
            lambda: (
                error_response(500, "server_error", "boom"),
                error_response(500, "server_error", "boom"),
            ),
            "server_error",
            id="server-errors",
        ),
    ],
)
def test_r36_a_failed_probe_stays_failed(steps: Callable[[], tuple[Any, ...]], outcome: str) -> None:
    call, server = run_probe(*steps())
    assert (call.outcome, call.output) == (outcome, None)
    expected = repair_bytes(PROBE, "x", FIRST_CHAR) if outcome == "invalid_output" else PROBE.body_bytes()
    assert second_body(server) == expected


# ---------------------------------------------------------------- R23, AMB-52 option (a): an escape the model wrote

OUTSIDE_SURROGATE = f"citations.0.sourceId: '{chr(92)}ud800' is not a source id of this bundle"


def claim_output(**fields: Any) -> str:
    """`json.dumps` writes a lone surrogate as its JSON escape, as a model can: the pinned grammar's `char` rule takes
    any four hex digits. R13 then sees plain ASCII, and H1's parse decodes the escape into the output."""
    return json.dumps({"claims": [{"text": "x", "segmentIds": ["seg_001"], **fields}], "nonCheckable": []})


def non_checkable_output(**fields: Any) -> str:
    entry = {"reasonKind": "opinion", "segmentIds": ["seg_001"], **fields}
    return json.dumps({"claims": [], "nonCheckable": [entry]})


def assert_stored_as_its_escape(call: CallRecord) -> None:
    """H3 spec T2. `canonical_json` still refuses the value; `record_json` stores it and reads it back unchanged."""
    with pytest.raises(UnicodeEncodeError):
        canonical_json(dataclasses.asdict(call))
    raw = record_json(call_to_dict(call))
    assert raw.isascii()
    assert call_from_dict(record_loads(raw)) == call
    assert f"{chr(92)}ud800".encode("ascii") in raw


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(claim_output(text=SURROGATE), id="claim-text"),
        pytest.param(claim_output(claimId=SURROGATE), id="claim-id"),
        pytest.param(claim_output(speakerId=SURROGATE), id="claim-speaker-id"),
        pytest.param(non_checkable_output(text=SURROGATE), id="non-checkable-text"),
        pytest.param(non_checkable_output(reason=SURROGATE), id="non-checkable-reason"),
        pytest.param(non_checkable_output(claimId=SURROGATE), id="non-checkable-claim-id"),
        pytest.param(non_checkable_output(speakerId=SURROGATE), id="non-checkable-speaker-id"),
    ],
)
def test_r23_amb_52_a_valid_extraction_holding_an_escaped_surrogate_is_stored_as_its_escape(content: str) -> None:
    assert content.isascii()
    call, server = run_extract(chat_response(content))
    assert (call.outcome, paths(server)) == ("valid", [CHAT_PATH])
    assert_stored_as_its_escape(call)


def test_r23_amb_52_a_valid_probe_holding_an_escaped_surrogate_is_stored_as_its_escape() -> None:
    call, server = run_probe(chat_response(probe_text("src_1", reason=SURROGATE)))
    assert (call.outcome, paths(server)) == ("valid", [CHAT_PATH])
    assert_stored_as_its_escape(call)


@pytest.mark.parametrize(
    ("raw", "error", "last", "outcome"),
    [
        pytest.param(probe_text(SURROGATE), OUTSIDE_SURROGATE, PROBE_OK, "valid", id="an-outside-id-then-a-repair"),
        pytest.param(
            probe_text(SURROGATE), OUTSIDE_SURROGATE, probe_text(SURROGATE), "invalid_output", id="an-outside-id-twice"
        ),
        pytest.param(
            probe_text(SURROGATE, verdict="MAYBE"), VERDICT_ERROR, PROBE_OK, "valid", id="a-schema-invalid-id"
        ),
    ],
)
def test_r23_amb_52_a_cited_id_holding_an_escaped_surrogate_is_stored_as_its_escape(
    raw: str, error: str, last: str, outcome: str
) -> None:
    call, server = run_probe(chat_response(raw), input_tokens_response(900), chat_response(last))
    first = call.attempts[0]
    assert (first.failure_kind, first.error_text) == ("invalid_output", error)
    assert (first.cited_source_ids, first.unknown_source_ids) == ((SURROGATE,), (SURROGATE,))
    assert second_body(server) == repair_bytes(PROBE, raw, error)
    assert call.outcome == outcome
    assert_stored_as_its_escape(call)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(claim_output(checkability={"isCheckable": True, "reason": SURROGATE}), id="a-reason-h1-drops"),
        pytest.param(claim_output(text=chr(0x1F600)), id="a-surrogate-pair"),
    ],
)
def test_r23_amb_52_the_pins_stop_at_what_the_record_holds_unpaired(content: str) -> None:
    call, _server = run_extract(chat_response(content))
    assert call.outcome == "valid"
    assert isinstance(canonical_json(dataclasses.asdict(call)), bytes)


# ---------------------------------------------------------------- R43 AttemptRecord

SHA_A = sha256(b"a")
SHA_B = sha256(b"b")
VALID_FIELDS: dict[str, Any] = {
    "request_kind": "single_conversation_extraction",
    "constrained": True,
    "attempt": 1,
    "retry_type": "none",
    "request_sha256": SHA_A,
    "max_tokens": 1024,
    "timeout_sec": 30.0,
    "wall_ns": 1_000,
    "http_status": 200,
    "finish_reason": "stop",
    "usage": Usage(100, 10, 0),
    "timings": TIMINGS,
    "server_model": "qwen3-4b",
    "system_fingerprint": "b1-27b7a8a",
    "content": EMPTY,
    "reasoning_content": None,
    "thinking": ThinkingCheck(False, False),
    "cited_source_ids": None,
    "unknown_source_ids": (),
    "failure_kind": None,
    "error_text": "",
    "server_error_type": None,
}
NO_RESPONSE: dict[str, Any] = {
    "http_status": None,
    "finish_reason": None,
    "usage": None,
    "timings": None,
    "server_model": None,
    "system_fingerprint": None,
    "content": None,
    "reasoning_content": None,
    "thinking": None,
}
INVALID: dict[str, Any] = {"failure_kind": "invalid_output", "error_text": "e"}
CUT_OFF: dict[str, Any] = {"failure_kind": "cut_off", "error_text": CUT_OFF_TEXT, "finish_reason": "length"}
TIMED_OUT: dict[str, Any] = {**NO_RESPONSE, "failure_kind": "timeout", "error_text": "t"}
TRANSPORT_FAULT: dict[str, Any] = {**NO_RESPONSE, "failure_kind": "server_error", "error_text": "s"}
HTTP_FAULT: dict[str, Any] = {**TRANSPORT_FAULT, "http_status": 500, "server_error_type": "server_error"}
PROBE_FIELDS: dict[str, Any] = {
    "request_kind": "reasoning_probe",
    "max_tokens": 384,
    "cited_source_ids": ("src_1",),
    "content": PROBE_OK,
}


def attempt_of(*changes: dict[str, Any], **more: Any) -> AttemptRecord:
    fields = dict(VALID_FIELDS)
    for change in changes:
        fields.update(change)
    fields.update(more)
    return AttemptRecord(**fields)


def test_r43_every_field_is_required_and_in_the_spec_s_order() -> None:
    fields = dataclasses.fields(AttemptRecord)
    assert [field.name for field in fields] == list(VALID_FIELDS)
    assert all(field.default is dataclasses.MISSING for field in fields)
    assert all(field.default_factory is dataclasses.MISSING for field in fields)


def test_r43_the_spec_s_valid_first_attempt() -> None:
    record = attempt_of()
    assert record.failure_kind is None
    assert record.thinking == ThinkingCheck(False, False)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(INVALID, id="invalid-output"),
        pytest.param(CUT_OFF, id="cut-off"),
        pytest.param(TIMED_OUT, id="timeout"),
        pytest.param(TRANSPORT_FAULT, id="transport-error"),
        pytest.param(HTTP_FAULT, id="http-error"),
        pytest.param({**TRANSPORT_FAULT, "http_status": 200}, id="malformed-200"),
        pytest.param({**TRANSPORT_FAULT, "http_status": 500}, id="http-error-without-a-type"),
        pytest.param(PROBE_FIELDS, id="probe"),
        pytest.param({**PROBE_FIELDS, **INVALID, "unknown_source_ids": ("src_1",)}, id="probe-unknown-id"),
        pytest.param({**PROBE_FIELDS, **INVALID, "cited_source_ids": None}, id="probe-unreadable"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": ()}, id="probe-cites-none"),
        pytest.param({"attempt": 2, "retry_type": "repair"}, id="repair"),
        pytest.param({"attempt": 2, "retry_type": "same_request"}, id="same-request"),
        pytest.param({"reasoning_content": "r", "thinking": ThinkingCheck(True, False)}, id="thinking"),
        pytest.param({"timings": None}, id="no-timings"),
        pytest.param({"wall_ns": 0}, id="zero-time"),
        pytest.param({"request_kind": "cap_session_extraction", "max_tokens": 4096, "timeout_sec": 300.0}, id="cap"),
        pytest.param({"constrained": False}, id="unconstrained"),
    ],
)
def test_r43_every_legal_shape_is_accepted(fields: dict[str, Any]) -> None:
    assert isinstance(attempt_of(fields), AttemptRecord)


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"attempt": 2}, id="attempt-2-without-a-retry"),
        pytest.param({"retry_type": "repair"}, id="attempt-1-repair"),
        pytest.param({"retry_type": "same_request"}, id="attempt-1-same-request"),
        pytest.param({"attempt": 2, "retry_type": "none"}, id="attempt-2-none"),
        pytest.param({"attempt": 3, "retry_type": "repair"}, id="attempt-3"),
        pytest.param({"attempt": 0}, id="attempt-0"),
        pytest.param({"attempt": True}, id="attempt-bool"),
        pytest.param({"attempt": "1"}, id="attempt-str"),
        pytest.param({"attempt": 1.0}, id="attempt-float"),
        pytest.param({"retry_type": "same"}, id="unknown-retry-type"),
        pytest.param({"request_kind": "single"}, id="unknown-kind"),
        pytest.param({"constrained": 1}, id="constrained-int"),
        pytest.param({"request_sha256": "A" * 64}, id="upper-case-hash"),
        pytest.param({"request_sha256": "a" * 63}, id="short-hash"),
        pytest.param({"request_sha256": "a" * 65}, id="long-hash"),
        pytest.param({"request_sha256": "g" * 64}, id="not-hex"),
        pytest.param({"request_sha256": "a" * 64 + "\n"}, id="hash-with-a-line-feed"),
        pytest.param({"request_sha256": None}, id="no-hash"),
        pytest.param({"max_tokens": 4096}, id="another-kind-s-cap"),
        pytest.param({"max_tokens": 1024.0}, id="float-cap"),
        pytest.param({"timeout_sec": 300.0}, id="another-kind-s-limit"),
        pytest.param({"timeout_sec": 30}, id="int-limit"),
        pytest.param({"request_kind": "cap_session_extraction", "max_tokens": 4096}, id="cap-with-30-s"),
        pytest.param({"request_kind": "reasoning_probe"}, id="probe-with-1024"),
        pytest.param({"wall_ns": -1}, id="negative-time"),
        pytest.param({"wall_ns": True}, id="bool-time"),
        pytest.param({"wall_ns": 1.0}, id="float-time"),
        pytest.param({"error_text": "x"}, id="valid-with-text"),
        pytest.param({"failure_kind": "invalid_output"}, id="failed-without-text"),
        pytest.param({"error_text": None}, id="no-text"),
        pytest.param({"failure_kind": "other", "error_text": "x"}, id="unknown-failure"),
        pytest.param({**TIMED_OUT, "failure_kind": "other"}, id="unknown-failure-with-no-response"),
        pytest.param({"finish_reason": "length"}, id="valid-at-the-cap"),
        pytest.param({"failure_kind": "cut_off", "error_text": "x"}, id="cut-off-with-stop"),
        pytest.param({**INVALID, "finish_reason": "length"}, id="invalid-output-at-the-cap"),
        pytest.param({"finish_reason": "tool_calls"}, id="unknown-finish-reason"),
        pytest.param({"http_status": 500}, id="valid-with-500"),
        pytest.param({"http_status": None}, id="valid-without-status"),
        pytest.param({"http_status": 200.0}, id="float-status"),
        pytest.param({"usage": "x"}, id="usage-a-str"),
        pytest.param({"timings": "x"}, id="timings-a-str"),
        pytest.param({"thinking": True}, id="thinking-a-bool"),
        pytest.param({"server_model": 3}, id="int-model"),
        pytest.param({"system_fingerprint": 3}, id="int-fingerprint"),
        pytest.param({"content": 3}, id="int-content"),
        pytest.param({"reasoning_content": 3}, id="int-reasoning"),
        pytest.param({**HTTP_FAULT, "server_error_type": 3}, id="int-error-type"),
        pytest.param({"server_error_type": "server_error"}, id="valid-with-an-error-type"),
        pytest.param({"failure_kind": "timeout", "http_status": 500, "error_text": "x"}, id="spec-timeout-with-500"),
    ],
)
def test_r43_each_broken_rule_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        attempt_of(changes)


def test_r43_the_spec_s_replace_examples() -> None:
    valid = attempt_of()
    for changes in (
        {"attempt": 2},
        {"failure_kind": "timeout", "http_status": 500},
        {"error_text": "x"},
        {"finish_reason": "length"},
        {"request_sha256": SHA_A.upper()},
    ):
        with pytest.raises(ValueError):
            dataclasses.replace(valid, **changes)


def test_r43_a_record_is_frozen() -> None:
    record = attempt_of()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.content = "x"  # type: ignore[misc]
    assert not hasattr(record, "__dict__")


@pytest.mark.parametrize(
    "field", ["content", "finish_reason", "usage", "server_model", "system_fingerprint", "thinking"]
)
@pytest.mark.parametrize(
    "base",
    [pytest.param({}, id="valid"), pytest.param(INVALID, id="invalid-output"), pytest.param(CUT_OFF, id="cut-off")],
)
def test_r43_a_validated_attempt_must_hold_its_response(base: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError):
        attempt_of(base, {field: None})


@pytest.mark.parametrize("status", [None, 201, 500])
@pytest.mark.parametrize(
    "base",
    [pytest.param({}, id="valid"), pytest.param(INVALID, id="invalid-output"), pytest.param(CUT_OFF, id="cut-off")],
)
def test_r43_a_validated_attempt_is_a_200(base: dict[str, Any], status: int | None) -> None:
    with pytest.raises(ValueError):
        attempt_of(base, {"http_status": status})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("finish_reason", "stop"),
        ("usage", Usage(1, 1, 0)),
        ("timings", TIMINGS),
        ("server_model", "m"),
        ("system_fingerprint", "f"),
        ("content", "c"),
        ("reasoning_content", "r"),
        ("thinking", ThinkingCheck(False, False)),
    ],
)
@pytest.mark.parametrize(
    "base",
    [pytest.param(TIMED_OUT, id="timeout"), pytest.param(TRANSPORT_FAULT, id="server-error")],
)
def test_r43_a_timeout_or_server_error_holds_no_response(base: dict[str, Any], field: str, value: Any) -> None:
    with pytest.raises(ValueError):
        attempt_of(base, {field: value})


@pytest.mark.parametrize("status", [200, 408, 500])
def test_r43_a_timeout_has_no_status(status: int) -> None:
    with pytest.raises(ValueError):
        attempt_of(TIMED_OUT, {"http_status": status})


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({}, id="valid"),
        pytest.param(INVALID, id="invalid-output"),
        pytest.param(CUT_OFF, id="cut-off"),
        pytest.param(TIMED_OUT, id="timeout"),
        pytest.param(TRANSPORT_FAULT, id="server-error-with-no-status"),
        pytest.param({**TRANSPORT_FAULT, "http_status": 200}, id="server-error-on-a-200"),
    ],
)
def test_r43_an_error_type_belongs_to_a_non_200_server_error(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        attempt_of(fields, {"server_error_type": "server_error"})


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"cited_source_ids": ()}, id="extraction-kind"),
        pytest.param({"cited_source_ids": ("src_1",)}, id="extraction-kind-with-ids"),
        pytest.param({**PROBE_FIELDS, **CUT_OFF}, id="probe-cut-off"),
        pytest.param({**PROBE_FIELDS, **TIMED_OUT}, id="probe-timeout"),
        pytest.param({**PROBE_FIELDS, **TRANSPORT_FAULT}, id="probe-server-error"),
        pytest.param({"unknown_source_ids": ("src_1",)}, id="unknown-without-citations"),
        pytest.param({**PROBE_FIELDS, "unknown_source_ids": ("src_9",)}, id="unknown-not-cited"),
        pytest.param({**PROBE_FIELDS, "unknown_source_ids": ["src_1"]}, id="unknown-a-list"),
        pytest.param({**PROBE_FIELDS, "unknown_source_ids": (["src_1"],)}, id="unknown-an-unhashable-id"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": ["src_1"]}, id="cited-a-list"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": "src_1"}, id="cited-a-str"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": (1,)}, id="cited-an-int-id"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": 3}, id="cited-an-int"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": [["src_1"]]}, id="cited-a-nested-list"),
        pytest.param({**PROBE_FIELDS, "cited_source_ids": None, "unknown_source_ids": ("src_1",)}, id="unknown-unread"),
    ],
)
def test_r43_citation_evidence_belongs_to_a_finished_probe_response(fields: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        attempt_of(fields)


# ---------------------------------------------------------------- R44 CallRecord

REPAIR_CHECK = ContextCheck("repair", SHA_B, 900, 0, 1024, 1924, 12288, 11059, True, 5)
FIRST_VALID = attempt_of()
FIRST_INVALID = attempt_of(INVALID)
FIRST_CUT_OFF = attempt_of(CUT_OFF)
FIRST_TIMEOUT = attempt_of(TIMED_OUT)
REPAIR_FIELDS: dict[str, Any] = {"attempt": 2, "retry_type": "repair", "request_sha256": SHA_B}
SECOND_FIELDS: dict[str, Any] = {"attempt": 2, "retry_type": "same_request"}
CAP_FIELDS: dict[str, Any] = {"request_kind": "cap_session_extraction", "max_tokens": 4096, "timeout_sec": 300.0}
SECOND_REPAIR = attempt_of(REPAIR_FIELDS)
SECOND_SAME = attempt_of(SECOND_FIELDS)


def call_of(**changes: Any) -> CallRecord:
    fields: dict[str, Any] = {
        "item_id": "syn_a",
        "request_kind": "single_conversation_extraction",
        "constrained": True,
        "attempts": (FIRST_VALID,),
        "context_checks": (),
        "outcome": "valid",
        "output": EMPTY_OUTPUT,
        "total_wall_ns": 1_000,
    }
    return CallRecord(**{**fields, **changes})


def repaired_call(**changes: Any) -> CallRecord:
    fields = {
        "attempts": (FIRST_INVALID, SECOND_REPAIR),
        "context_checks": (REPAIR_CHECK,),
        "total_wall_ns": 2_000,
    }
    return call_of(**{**fields, **changes})


def retried_call(**changes: Any) -> CallRecord:
    return call_of(**{"attempts": (FIRST_TIMEOUT, SECOND_SAME), "total_wall_ns": 2_000, **changes})


def failed_call(**changes: Any) -> CallRecord:
    """A call that failed after its retry. With no output, the output's own rules cannot stand in for another rule."""
    attempts = (FIRST_TIMEOUT, attempt_of(TIMED_OUT, SECOND_FIELDS))
    return retried_call(**{"attempts": attempts, "outcome": "timeout", "output": None, **changes})


def test_r44_the_fields_in_the_spec_s_order() -> None:
    fields = [field.name for field in dataclasses.fields(CallRecord)]
    assert fields == [
        "item_id",
        "request_kind",
        "constrained",
        "attempts",
        "context_checks",
        "outcome",
        "output",
        "total_wall_ns",
    ]


def test_r44_the_legal_shapes_are_accepted() -> None:
    assert call_of().outcome == "valid"
    assert repaired_call().context_checks == (REPAIR_CHECK,)
    assert retried_call().context_checks == ()
    failed = attempt_of(TIMED_OUT, {"attempt": 2, "retry_type": "same_request"})
    assert retried_call(attempts=(FIRST_TIMEOUT, failed), outcome="timeout", output=None).output is None


@pytest.mark.parametrize(
    "broken",
    [
        pytest.param(lambda: call_of(attempts=()), id="no-attempt"),
        pytest.param(lambda: call_of(attempts=[FIRST_VALID]), id="a-list"),
        pytest.param(lambda: call_of(attempts=("x",)), id="not-a-record"),
        pytest.param(lambda: repaired_call(attempts=(FIRST_INVALID, "x")), id="second-not-a-record"),
        pytest.param(lambda: repaired_call(attempts=(FIRST_INVALID, SECOND_REPAIR, SECOND_REPAIR)), id="three"),
        pytest.param(lambda: repaired_call(attempts=(SECOND_REPAIR, FIRST_INVALID)), id="out-of-order"),
        pytest.param(lambda: call_of(attempts=(SECOND_SAME,)), id="only-attempt-2"),
        pytest.param(
            lambda: call_of(attempts=(FIRST_VALID, SECOND_SAME), total_wall_ns=2_000), id="valid-then-another"
        ),
        pytest.param(lambda: call_of(attempts=(FIRST_TIMEOUT,), outcome="timeout", output=None), id="failed-no-retry"),
        pytest.param(lambda: repaired_call(attempts=(FIRST_TIMEOUT, SECOND_REPAIR)), id="repair-after-timeout"),
        pytest.param(
            lambda: repaired_call(
                attempts=(FIRST_TIMEOUT, attempt_of(REPAIR_FIELDS, request_sha256=SHA_A)),
                context_checks=(dataclasses.replace(REPAIR_CHECK, request_sha256=SHA_A),),
            ),
            id="only-the-retry-type-is-wrong-after-a-timeout",
        ),
        pytest.param(
            lambda: retried_call(attempts=(FIRST_INVALID, attempt_of(SECOND_FIELDS, request_sha256=SHA_B))),
            id="only-the-retry-type-is-wrong-after-invalid-output",
        ),
        pytest.param(
            lambda: retried_call(attempts=(FIRST_INVALID, SECOND_SAME)), id="same-request-after-invalid-output"
        ),
        pytest.param(lambda: retried_call(attempts=(FIRST_CUT_OFF, SECOND_SAME)), id="same-request-after-cut-off"),
        pytest.param(
            lambda: retried_call(attempts=(FIRST_TIMEOUT, attempt_of(SECOND_FIELDS, request_sha256=SHA_B))),
            id="same-request-with-other-bytes",
        ),
        pytest.param(
            lambda: repaired_call(attempts=(FIRST_INVALID, attempt_of(REPAIR_FIELDS, request_sha256=SHA_A))),
            id="repair-with-the-same-bytes",
        ),
        pytest.param(lambda: call_of(request_kind="cap_session_extraction"), id="another-kind"),
        pytest.param(lambda: call_of(constrained=False), id="another-form"),
        pytest.param(
            lambda: repaired_call(attempts=(FIRST_INVALID, attempt_of(REPAIR_FIELDS, constrained=False))),
            id="second-attempt-in-another-form",
        ),
        pytest.param(
            lambda: repaired_call(attempts=(FIRST_INVALID, attempt_of(REPAIR_FIELDS, CAP_FIELDS))),
            id="second-attempt-of-another-kind",
        ),
        pytest.param(
            lambda: repaired_call(attempts=(attempt_of(INVALID, constrained=False), SECOND_REPAIR)),
            id="first-of-two-in-another-form",
        ),
        pytest.param(
            lambda: repaired_call(attempts=(attempt_of(INVALID, CAP_FIELDS), SECOND_REPAIR)),
            id="first-of-two-of-another-kind",
        ),
        pytest.param(lambda: call_of(constrained=1), id="constrained-int"),
        pytest.param(lambda: call_of(item_id=""), id="empty-item"),
        pytest.param(lambda: call_of(item_id=3), id="int-item"),
        pytest.param(lambda: failed_call(item_id=""), id="empty-item-with-no-output"),
        pytest.param(lambda: failed_call(item_id=3), id="int-item-with-no-output"),
        pytest.param(lambda: repaired_call(context_checks=()), id="repair-without-a-check"),
        pytest.param(lambda: repaired_call(context_checks=(REPAIR_CHECK, REPAIR_CHECK)), id="two-checks"),
        pytest.param(lambda: repaired_call(context_checks=[REPAIR_CHECK]), id="checks-a-list"),
        pytest.param(lambda: repaired_call(context_checks=("x",)), id="check-not-a-record"),
        pytest.param(
            lambda: repaired_call(context_checks=(dataclasses.replace(REPAIR_CHECK, label="request"),)),
            id="check-labelled-request",
        ),
        pytest.param(
            lambda: repaired_call(context_checks=(dataclasses.replace(REPAIR_CHECK, label="repair_bound"),)),
            id="check-labelled-bound",
        ),
        pytest.param(
            lambda: repaired_call(context_checks=(dataclasses.replace(REPAIR_CHECK, request_sha256=SHA_A),)),
            id="check-of-other-bytes",
        ),
        pytest.param(
            lambda: repaired_call(
                context_checks=(dataclasses.replace(REPAIR_CHECK, input_tokens=10_036, uses=11_060, fits=False),)
            ),
            id="check-that-does-not-fit",
        ),
        pytest.param(lambda: call_of(context_checks=(REPAIR_CHECK,)), id="check-without-a-repair"),
        pytest.param(lambda: retried_call(context_checks=(REPAIR_CHECK,)), id="check-on-a-same-request-retry"),
        pytest.param(lambda: call_of(outcome="timeout"), id="outcome-is-not-the-last-kind"),
        pytest.param(lambda: failed_call(outcome="server_error"), id="another-failure-as-the-outcome"),
        pytest.param(lambda: call_of(outcome="other"), id="unknown-outcome"),
        pytest.param(lambda: call_of(output=None), id="valid-without-output"),
        pytest.param(
            lambda: retried_call(attempts=(FIRST_TIMEOUT, attempt_of(TIMED_OUT, SECOND_FIELDS)), outcome="timeout"),
            id="failed-with-output",
        ),
        pytest.param(lambda: call_of(output=Extraction("syn_b", (), ())), id="another-item-s-output"),
        pytest.param(lambda: call_of(output=ProbeOutput("SUPPORTED", "r", ())), id="probe-output-on-an-extraction"),
        pytest.param(lambda: call_of(output="x"), id="output-of-no-type"),
        pytest.param(lambda: call_of(total_wall_ns=999), id="wrong-total"),
        pytest.param(lambda: call_of(total_wall_ns=1_000.0), id="float-total"),
        pytest.param(lambda: repaired_call(total_wall_ns=1_000), id="total-of-the-last-attempt-only"),
    ],
)
def test_r44_each_broken_rule_is_refused(broken: Callable[[], CallRecord]) -> None:
    with pytest.raises(ValueError):
        broken()


def probe_attempt(*changes: dict[str, Any]) -> AttemptRecord:
    return attempt_of(PROBE_FIELDS, *changes)


def test_r44_a_probe_output_must_be_a_probe_output() -> None:
    fields: dict[str, Any] = {
        "item_id": "probe_01",
        "request_kind": "reasoning_probe",
        "constrained": True,
        "attempts": (probe_attempt(),),
        "context_checks": (),
        "outcome": "valid",
        "total_wall_ns": 1_000,
    }
    assert CallRecord(**fields, output=ProbeOutput("SUPPORTED", "r", ("src_1",))).outcome == "valid"
    with pytest.raises(ValueError):
        CallRecord(**fields, output=Extraction("probe_01", (), ()))


def test_r44_a_probe_call_keeps_its_item_id() -> None:
    client, server = make_client(chat_response(PROBE_OK), httpx.ReadTimeout, httpx.ReadTimeout)
    arguments: dict[str, Any] = {"item_id": "probe_07", "bundle_source_ids": BUNDLE, "repair_template": TEMPLATE}
    assert client.probe(PROBE, **arguments).item_id == "probe_07"
    assert client.probe(PROBE, **arguments).item_id == "probe_07"  # a failed call too
    assert [sent.content for sent in server.requests] == [PROBE.body_bytes()] * 3


def test_r44_a_call_is_frozen() -> None:
    call = call_of()
    with pytest.raises(dataclasses.FrozenInstanceError):
        call.outcome = "timeout"  # type: ignore[misc]
    assert not hasattr(call, "__dict__")


def test_r44_cut_off_then_valid() -> None:
    call, server = run_extract(
        chat_response(EMPTY, finish_reason="length"), input_tokens_response(900), chat_response(EMPTY)
    )
    assert call.outcome == "valid"
    assert call.attempts[0].failure_kind == "cut_off"
    assert call.attempts[1].retry_type == "repair"
    assert len(call.context_checks) == 1
    assert call.cut_off_attempts == 1
    assert second_body(server) == repair_bytes(SINGLE, EMPTY, CUT_OFF_TEXT)


def test_r44_server_error_twice() -> None:
    call, server = run_extract(error_response(500, "server_error", "boom"), error_response(500, "server_error", "boom"))
    assert (call.outcome, call.output, call.context_checks) == ("server_error", None, ())
    assert second_body(server) == SINGLE.body_bytes()


def test_r44_the_properties() -> None:
    assert (call_of().first_attempt_failed, call_of().cut_off_attempts, call_of().thinking_seen) == (False, 0, False)
    assert (repaired_call().first_attempt_failed, repaired_call().cut_off_attempts) == (True, 0)
    assert repaired_call(attempts=(FIRST_CUT_OFF, SECOND_REPAIR)).cut_off_attempts == 1
    thoughtful = attempt_of(thinking=ThinkingCheck(False, True))
    assert call_of(attempts=(thoughtful,)).thinking_seen is True
    assert retried_call().thinking_seen is False


def test_r44_a_record_built_by_the_client_passes_its_own_checks() -> None:
    call, server = run_extract(chat_response("not json"), input_tokens_response(900), chat_response(EMPTY))
    assert dataclasses.replace(call) == call
    assert dataclasses.replace(call.attempts[0]) == call.attempts[0]
    assert second_body(server) == repair_bytes(SINGLE, "not json", FIRST_CHAR)


# ---------------------------------------------------------------- R45 extraction_result, S2 §5.3


def test_r45_a_valid_call_gives_its_output() -> None:
    call = repaired_call()
    assert call.extraction_result() is call.output
    assert call_of().extraction_result() == EMPTY_OUTPUT


def test_r45_timeouts_give_a_failure_with_the_last_text() -> None:
    call, server = run_extract(httpx.ReadTimeout, httpx.ReadTimeout)
    detail = "timeout: ReadTimeout after the 30 s limit"
    assert call.extraction_result() == ExtractionFailure("syn_a", "timeout", detail=detail)
    assert second_body(server) == SINGLE.body_bytes()


@pytest.mark.parametrize(
    ("second", "kind", "detail"),
    [
        pytest.param(lambda: chat_response("x"), "invalid_output", FIRST_CHAR, id="invalid-output"),
        pytest.param(lambda: chat_response("x", finish_reason="length"), "cut_off", CUT_OFF_TEXT, id="cut-off"),
        pytest.param(
            lambda: error_response(500, "server_error", "boom"),
            "server_error",
            "HTTP 500: server_error: boom",
            id="server-error",
        ),
    ],
)
def test_r45_the_failure_is_the_last_attempt_s(second: Callable[[], Any], kind: Any, detail: str) -> None:
    call, server = run_extract(httpx.ReadTimeout, second())
    assert call.extraction_result() == ExtractionFailure("syn_a", kind, detail=detail)
    assert second_body(server) == SINGLE.body_bytes()


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param(lambda: (chat_response(PROBE_OK),), id="valid"),
        pytest.param(lambda: (httpx.ReadTimeout, httpx.ReadTimeout), id="failed"),
    ],
)
def test_r45_a_probe_has_no_extraction_result(steps: Callable[[], tuple[Any, ...]]) -> None:
    call, server = run_probe(*steps())
    with pytest.raises(ValueError, match="not a reasoning probe"):
        call.extraction_result()
    assert [sent.content for sent in server.requests] == [PROBE.body_bytes()] * len(call.attempts)


# ---------------------------------------------------------------- the small records


def test_section_3_the_response_models_config() -> None:
    assert client_module._Resp.model_config == {
        "strict": True,
        "extra": "ignore",
        "frozen": True,
        "allow_inf_nan": False,
    }
    assert client_module._MessageOut.model_config == {"strict": True, "extra": "forbid", "frozen": True}
    assert client_module._ModalitiesOut.model_config == {"strict": True, "extra": "forbid", "frozen": True}
    for name in (
        "_ChoiceOut",
        "_PromptDetailsOut",
        "_UsageOut",
        "_TimingsOut",
        "_CompletionOut",
        "_InputTokensOut",
        "_ErrorBody",
        "_ErrorOut",
        "_SettingsOut",
        "_PropsOut",
    ):
        assert issubclass(getattr(client_module, name), client_module._Resp), name


def test_section_3_usage_and_timings_field_order() -> None:
    assert [field.name for field in dataclasses.fields(Usage)] == [
        "prompt_tokens",
        "completion_tokens",
        "cached_tokens",
    ]
    assert [field.name for field in dataclasses.fields(Timings)] == [
        "cache_n",
        "prompt_n",
        "prompt_ms",
        "prompt_per_token_ms",
        "prompt_per_second",
        "predicted_n",
        "predicted_ms",
        "predicted_per_token_ms",
        "predicted_per_second",
        "draft_n",
        "draft_n_accepted",
    ]
    assert [field.name for field in dataclasses.fields(ThinkingCheck)] == ["in_reasoning", "in_content"]
    assert (TIMINGS.draft_n, TIMINGS.draft_n_accepted) == (None, None)


@pytest.mark.parametrize(
    "fields",
    [
        (-1, 0, None),
        (0, -1, None),
        (0, 0, -1),
        (True, 0, None),
        (0, False, None),
        (0, 0, True),
        ("1", 0, None),
        (1.0, 0, None),
        (None, 0, None),
    ],
)
def test_section_3_usage_holds_counts(fields: tuple[Any, Any, Any]) -> None:
    with pytest.raises(ValueError):
        Usage(*fields)


@pytest.mark.parametrize(
    "changes",
    [
        {"cache_n": -1},
        {"prompt_n": True},
        {"predicted_n": 1.0},
        {"prompt_ms": math.inf},
        {"prompt_per_token_ms": math.nan},
        {"prompt_per_second": True},
        {"predicted_ms": "1"},
        {"predicted_per_token_ms": None},
        {"predicted_per_second": -math.inf},
        {"draft_n": True},
        {"draft_n_accepted": 1.0},
    ],
)
def test_section_3_timings_hold_counts_and_finite_numbers(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(TIMINGS, **changes)


def test_section_3_the_small_records_are_frozen() -> None:
    for record, name in (
        (Usage(1, 1, 0), "prompt_tokens"),
        (TIMINGS, "cache_n"),
        (ThinkingCheck(False, False), "in_reasoning"),
    ):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(record, name, 1)
        assert not hasattr(record, "__dict__")
