"""The context check, `/props` and the client's faults: H2 spec R10, R24-R29, E10 and E30 (S2 §3.3, §3.5, §3.13).

Every response comes from `httpx.MockTransport`. Data is synthetic. Every test that sends a repair checks the exact
bytes both of the count and of the second completion.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import pickle
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff.client import (
    CHAT_PATH,
    COUNT_PATH,
    PROPS_PATH,
    AttemptRecord,
    ChatMessage,
    ChatRequest,
    ClientFault,
    ContextBudgetExceeded,
    ContextCheck,
    ModelServerClient,
    ServerCheckError,
    ServerProps,
    context_fits,
)
from app.evaluation.bakeoff.extraction import CUT_OFF_TEXT, FAILURE_KINDS
from app.evaluation.bakeoff.schemas import canonical_json, canonical_sha256
from app.evaluation.tests._client_fixtures import (
    BASE_URL,
    BUNDLE,
    CAP,
    EMPTY,
    MESSAGES,
    PROBE,
    SEGMENT_IDS,
    SINGLE,
    TEMPLATE,
    FakeClock,
    ScriptedServer,
    block_sockets,
    chat_response,
    error_response,
    input_tokens_response,
    make_client,
    paths,
    repair_bytes,
    valid_content,
)

V2_SHA256 = "dd3c79d14de77b4327fb3438812cdcef745ce64111511025a23f83a9e6b8aae6"
FIRST_CHAR = "invalid JSON: Expecting value (line 1, column 1)"
EXTRACT: dict[str, Any] = {"item_id": "syn_a", "request_segment_ids": SEGMENT_IDS, "repair_template": TEMPLATE}
PROBE_CALL: dict[str, Any] = {"item_id": "probe_01", "bundle_source_ids": BUNDLE, "repair_template": TEMPLATE}
PROPS: dict[str, Any] = {
    "default_generation_settings": {"n_ctx": 12288, "params": {}},
    "total_slots": 1,
    "model_alias": "qwen3-4b",
    "model_path": "/m/q.gguf",
    "build_info": "b11936-27b7a8a0",
    "chat_template": "...<think>...",
}


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def call_request(client: ModelServerClient, request: ChatRequest) -> Any:
    if request.kind == "reasoning_probe":
        return client.probe(request, **PROBE_CALL)
    return client.extract(request, **EXTRACT)


def props_response(**changes: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps({**PROPS, **changes}).encode("utf-8"))


# ---------------------------------------------------------------- R24 definition


@pytest.mark.parametrize(
    ("uses", "n_ctx", "fits"),
    [
        (0, 12_288, True),
        (11_059, 12_288, True),
        (11_060, 12_288, False),
        (12_288, 12_288, False),
        (14_745, 16_384, True),
        (14_746, 16_384, False),
        (9, 10, True),
        (10, 10, False),
        (9, 11, True),
        (10, 11, False),
    ],
)
def test_r24_context_fits_is_integer_arithmetic(uses: int, n_ctx: int, fits: bool) -> None:
    assert context_fits(uses, n_ctx) is fits


def test_r24_the_default_context_is_12288() -> None:
    assert context_fits(11_059) is True
    assert context_fits(11_060) is False


def test_r24_fits_and_the_rounded_down_limit_always_agree() -> None:
    for n_ctx in range(1, 300):
        limit = n_ctx * 9 // 10
        for uses in range(limit - 2, limit + 3):
            assert context_fits(uses, n_ctx) is (uses <= limit), (uses, n_ctx)


@pytest.mark.parametrize(
    ("request_", "fitting", "uses"),
    [
        pytest.param(SINGLE, 10_035, 11_059, id="single"),
        pytest.param(CAP, 6_963, 11_059, id="cap"),
        pytest.param(PROBE, 10_675, 11_059, id="probe"),
    ],
)
def test_r24_r25_the_boundary_for_each_kind(request_: ChatRequest, fitting: int, uses: int) -> None:
    client, _server = make_client(input_tokens_response(fitting), input_tokens_response(fitting + 1))
    inside = client.check_context(request_)
    outside = client.check_context(request_)
    assert (inside.input_tokens, inside.uses, inside.limit, inside.fits) == (fitting, uses, 11_059, True)
    assert (outside.input_tokens, outside.uses, outside.limit, outside.fits) == (fitting + 1, uses + 1, 11_059, False)


def test_r24_n_ctx_is_the_constructor_s() -> None:
    client, _server = make_client(n_ctx=16_384)
    assert (client.n_ctx, client.limit) == (16_384, 14_745)
    default, _server = make_client()
    assert (default.n_ctx, default.limit) == (12_288, 11_059)
    assert ModelServerClient(BASE_URL, transport=httpx.MockTransport(ScriptedServer()), n_ctx=11).limit == 9


@pytest.mark.parametrize(
    ("n_ctx", "error"),
    [
        (0, ValueError),
        (-1, ValueError),
        (True, TypeError),
        (False, TypeError),
        ("12288", TypeError),
        (12288.0, TypeError),
        (None, TypeError),
    ],
)
def test_r24_n_ctx_must_be_an_int_above_0(n_ctx: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        ModelServerClient(BASE_URL, transport=httpx.MockTransport(ScriptedServer()), n_ctx=n_ctx)


def test_r24_1_is_above_0() -> None:
    client, _server = make_client(n_ctx=1)
    assert (client.n_ctx, client.limit) == (1, 0)


def test_r24_the_limit_is_never_taken_from_the_server() -> None:
    client, server = make_client(
        props_response(default_generation_settings={"n_ctx": 4096}), input_tokens_response(9_000)
    )
    assert client.fetch_props().n_ctx == 4096
    assert (client.n_ctx, client.limit) == (12_288, 11_059)
    check = client.check_context(SINGLE)
    assert (check.n_ctx, check.limit, check.fits) == (12_288, 11_059, True)
    assert paths(server) == [PROPS_PATH, COUNT_PATH]


# ---------------------------------------------------------------- R25 the token-count route


def test_r25_the_count_of_v2() -> None:
    client, server = make_client(input_tokens_response(812), clock=FakeClock(100, 350))
    check = client.check_context(SINGLE)
    assert check == ContextCheck(
        label="request",
        request_sha256=V2_SHA256,
        input_tokens=812,
        reserved_tokens=0,
        max_tokens=1024,
        uses=1836,
        n_ctx=12288,
        limit=11059,
        fits=True,
        wall_ns=250,
    )
    assert paths(server) == [COUNT_PATH]
    assert server.requests[0].content == SINGLE.body_bytes()
    assert sha256(server.requests[0].content) == V2_SHA256


@pytest.mark.parametrize("constrained", [True, False], ids=["constrained", "unconstrained"])
@pytest.mark.parametrize("kind", ["single_conversation_extraction", "cap_session_extraction", "reasoning_probe"])
def test_r25_the_count_is_byte_identical_to_the_completion_it_measures(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    client, server = make_client(input_tokens_response(812), chat_response(valid_content(kind)))
    check = client.check_context(request)
    call = call_request(client, request)
    assert paths(server) == [COUNT_PATH, CHAT_PATH]
    assert server.requests[0].content == server.requests[1].content == request.body_bytes()
    assert check.request_sha256 == call.attempts[0].request_sha256 == sha256(request.body_bytes())
    assert check.max_tokens == request.max_tokens


def test_r25_a_request_that_does_not_fit_is_an_answer_not_an_error() -> None:
    client, server = make_client(input_tokens_response(20_000))
    check = client.check_context(SINGLE)
    assert (check.fits, check.uses) == (False, 21_024)
    assert paths(server) == [COUNT_PATH]


def test_r25_keys_the_harness_does_not_read_are_ignored() -> None:
    body = b'{"input_tokens": 5, "object": "response.input_tokens", "extra": [1]}'
    client, _server = make_client(httpx.Response(200, content=body))
    assert client.check_context(SINGLE).input_tokens == 5


def test_r25_zero_tokens_is_a_count() -> None:
    client, _server = make_client(input_tokens_response(0))
    check = client.check_context(SINGLE)
    assert (check.input_tokens, check.uses) == (0, 1024)


@pytest.mark.parametrize("request_", ["x", None, 3])
def test_r25_check_context_takes_a_chat_request(request_: Any) -> None:
    client, server = make_client()
    with pytest.raises(TypeError):
        client.check_context(request_)
    assert server.requests == []


# ---------------------------------------------------------------- R26 the pre-run check


def test_r26_the_spec_s_cap_session_at_7000_stops_the_run() -> None:
    client, _server = make_client(input_tokens_response(7_000))
    check = client.check_context(CAP)
    assert (check.uses, check.limit, check.fits) == (11_096, 11_059, False)


@pytest.mark.parametrize("request_", [SINGLE, CAP, PROBE], ids=["single", "cap", "probe"])
def test_r26_both_forms_of_a_request_can_be_checked(request_: ChatRequest) -> None:
    free = ChatRequest(request_.kind, request_.messages, constrained=False)
    client, server = make_client(input_tokens_response(900), input_tokens_response(800))
    constrained_check = client.check_context(request_)
    free_check = client.check_context(free)
    assert server.requests[0].content == request_.body_bytes()
    assert server.requests[1].content == free.body_bytes()
    assert b"response_format" in server.requests[0].content
    assert b"response_format" not in server.requests[1].content
    assert constrained_check.request_sha256 != free_check.request_sha256


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param(lambda: (chat_response(EMPTY),), id="valid"),
        pytest.param(lambda: (httpx.ReadTimeout, chat_response(EMPTY)), id="same-request"),
        pytest.param(
            lambda: (error_response(500, "server_error", "boom"), chat_response(EMPTY)), id="same-request-after-a-500"
        ),
    ],
)
def test_r26_the_original_request_is_not_recounted_at_run_time(steps: Any) -> None:
    client, server = make_client(*steps())
    call = client.extract(SINGLE, **EXTRACT)
    assert COUNT_PATH not in paths(server)
    assert call.context_checks == ()
    assert [sent.content for sent in server.requests] == [SINGLE.body_bytes()] * len(call.attempts)


# ---------------------------------------------------------------- R27 the repair bound


def test_r27_a_cap_session_at_5700_does_not_fit_12288() -> None:
    client, server = make_client(input_tokens_response(5_700), clock=FakeClock(0, 7))
    check = client.repair_bound(CAP, TEMPLATE)
    expected = repair_bytes(CAP, "", CUT_OFF_TEXT)
    assert check == ContextCheck(
        label="repair_bound",
        request_sha256=sha256(expected),
        input_tokens=5_700,
        reserved_tokens=4_096,
        max_tokens=4_096,
        uses=13_892,
        n_ctx=12_288,
        limit=11_059,
        fits=False,
        wall_ns=7,
    )
    assert paths(server) == [COUNT_PATH]
    assert server.requests[0].content == expected


def test_r27_the_bound_counts_an_empty_output_and_the_cut_off_text() -> None:
    client, server = make_client(input_tokens_response(5))
    client.repair_bound(SINGLE, TEMPLATE)
    assert json.loads(server.requests[0].content)["messages"] == [
        {"content": "S", "role": "system"},
        {"content": "U", "role": "user"},
        {"content": "", "role": "assistant"},
        {"content": f"Fix:\n{CUT_OFF_TEXT}\nAnswer again.", "role": "user"},
    ]


def test_r27_6000_at_16384_fits() -> None:
    client, _server = make_client(input_tokens_response(6_000), n_ctx=16_384)
    check = client.repair_bound(CAP, TEMPLATE)
    assert (check.uses, check.limit, check.fits, check.n_ctx) == (14_192, 14_745, True, 16_384)


def test_r27_6000_at_12288_does_not() -> None:
    client, _server = make_client(input_tokens_response(6_000))
    check = client.repair_bound(CAP, TEMPLATE)
    assert (check.uses, check.fits) == (14_192, False)


def test_r27_a_single_conversation_at_1800_fits() -> None:
    client, _server = make_client(input_tokens_response(1_800))
    check = client.repair_bound(SINGLE, TEMPLATE)
    assert (check.uses, check.reserved_tokens, check.max_tokens, check.fits) == (3_848, 1_024, 1_024, True)


def test_r27_the_probe_bound_reserves_the_probe_cap() -> None:
    client, server = make_client(input_tokens_response(100))
    check = client.repair_bound(PROBE, TEMPLATE)
    assert (check.uses, check.reserved_tokens, check.max_tokens) == (868, 384, 384)
    assert server.requests[0].content == repair_bytes(PROBE, "", CUT_OFF_TEXT)


def test_r27_the_bound_keeps_the_form() -> None:
    free = ChatRequest("cap_session_extraction", MESSAGES, constrained=False)
    client, server = make_client(input_tokens_response(100))
    client.repair_bound(free, TEMPLATE)
    assert b"response_format" not in server.requests[0].content
    assert server.requests[0].content == repair_bytes(free, "", CUT_OFF_TEXT)


@pytest.mark.parametrize(
    ("request_", "fitting"),
    [
        pytest.param(SINGLE, 9_011, id="single"),
        pytest.param(CAP, 2_867, id="cap"),
        pytest.param(PROBE, 10_291, id="probe"),
    ],
)
def test_r27_r47_the_bound_at_11059_and_11060(request_: ChatRequest, fitting: int) -> None:
    client, server = make_client(input_tokens_response(fitting), input_tokens_response(fitting + 1))
    inside = client.repair_bound(request_, TEMPLATE)
    outside = client.repair_bound(request_, TEMPLATE)
    assert (inside.uses, inside.fits) == (11_059, True)
    assert (outside.uses, outside.fits) == (11_060, False)
    assert [sent.content for sent in server.requests] == [repair_bytes(request_, "", CUT_OFF_TEXT)] * 2


@pytest.mark.parametrize(
    ("request_", "template"),
    [("x", TEMPLATE), (None, TEMPLATE), (SINGLE, "x"), (SINGLE, None)],
    ids=["request-str", "no-request", "template-str", "no-template"],
)
def test_r27_the_bound_checks_its_arguments_first(request_: Any, template: Any) -> None:
    client, server = make_client()
    with pytest.raises(TypeError):
        client.repair_bound(request_, template)
    assert server.requests == []


def test_r27_a_failed_bound_is_a_check_error() -> None:
    client, _server = make_client(httpx.ReadTimeout)
    with pytest.raises(ServerCheckError) as caught:
        client.repair_bound(CAP, TEMPLATE)
    assert caught.value.error_text == "token count: timeout: ReadTimeout after the 30 s limit"
    assert caught.value.attempts == ()


# ---------------------------------------------------------------- R28 the run-time repair check, E10


def test_r28_e10_a_repair_at_the_limit_is_sent() -> None:
    client, server = make_client(chat_response("not json"), input_tokens_response(10_035), chat_response(EMPTY))
    call = client.extract(SINGLE, **EXTRACT)
    expected = repair_bytes(SINGLE, "not json", FIRST_CHAR)
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH]
    assert server.requests[1].content == server.requests[2].content == expected
    assert len(call.context_checks) == 1
    check = call.context_checks[0]
    assert (check.label, check.uses, check.limit, check.fits) == ("repair", 11_059, 11_059, True)
    assert (check.input_tokens, check.reserved_tokens, check.max_tokens) == (10_035, 0, 1_024)
    assert check.request_sha256 == call.attempts[1].request_sha256 == sha256(expected)
    assert call.outcome == "valid"


def test_r28_e10_a_repair_past_the_limit_is_not_sent() -> None:
    client, server = make_client(chat_response("not json"), input_tokens_response(10_036), clock=FakeClock(0, 5, 6, 9))
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.extract(SINGLE, **EXTRACT)
    fault = caught.value
    expected = repair_bytes(SINGLE, "not json", FIRST_CHAR)
    assert fault.check == ContextCheck(
        label="repair",
        request_sha256=sha256(expected),
        input_tokens=10_036,
        reserved_tokens=0,
        max_tokens=1_024,
        uses=11_060,
        n_ctx=12_288,
        limit=11_059,
        fits=False,
        wall_ns=3,
    )
    assert str(fault) == "repair request uses 11060 tokens, limit 11059"
    assert paths(server) == [CHAT_PATH, COUNT_PATH]
    assert server.requests[1].content == expected
    assert len(fault.attempts) == 1
    first = fault.attempts[0]
    assert isinstance(first, AttemptRecord)
    assert (first.failure_kind, first.error_text, first.wall_ns) == ("invalid_output", FIRST_CHAR, 5)


def test_r28_a_cap_session_counted_at_7000_stops_after_one_attempt() -> None:
    client, server = make_client(chat_response("not json", completion_tokens=100), input_tokens_response(7_000))
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.extract(CAP, **EXTRACT)
    assert (caught.value.check.uses, caught.value.check.limit, caught.value.check.fits) == (11_096, 11_059, False)
    assert len(caught.value.attempts) == 1
    assert paths(server) == [CHAT_PATH, COUNT_PATH]


def test_r28_a_probe_repair_is_checked_too() -> None:
    client, server = make_client(chat_response("not json"), input_tokens_response(10_676))
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.probe(PROBE, **PROBE_CALL)
    assert (caught.value.check.uses, caught.value.check.max_tokens) == (11_060, 384)
    assert server.requests[1].content == repair_bytes(PROBE, "not json", FIRST_CHAR)


def test_r28_a_cut_off_repair_is_checked_too() -> None:
    client, server = make_client(chat_response(EMPTY, finish_reason="length"), input_tokens_response(10_036))
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.extract(SINGLE, **EXTRACT)
    assert caught.value.attempts[0].failure_kind == "cut_off"
    assert server.requests[1].content == repair_bytes(SINGLE, EMPTY, CUT_OFF_TEXT)


@pytest.mark.parametrize(
    ("request_", "fitting"), [pytest.param(CAP, 6_963, id="cap"), pytest.param(PROBE, 10_675, id="probe")]
)
def test_r28_r47_the_run_time_check_at_11059_and_11060(request_: ChatRequest, fitting: int) -> None:
    expected = repair_bytes(request_, "not json", FIRST_CHAR)
    client, server = make_client(
        chat_response("not json"), input_tokens_response(fitting), chat_response(valid_content(request_.kind))
    )
    assert call_request(client, request_).context_checks[0].uses == 11_059
    assert server.requests[1].content == server.requests[2].content == expected
    client, server = make_client(chat_response("not json"), input_tokens_response(fitting + 1))
    with pytest.raises(ContextBudgetExceeded) as caught:
        call_request(client, request_)
    assert caught.value.check.uses == 11_060
    assert paths(server) == [CHAT_PATH, COUNT_PATH]
    assert server.requests[1].content == expected


def test_r28_the_limit_follows_the_constructor() -> None:
    client, server = make_client(
        chat_response("not json"), input_tokens_response(7_000), chat_response(EMPTY), n_ctx=16_384
    )
    call = client.extract(CAP, **EXTRACT)
    check = call.context_checks[0]
    assert (check.uses, check.n_ctx, check.limit, check.fits) == (11_096, 16_384, 14_745, True)
    assert server.requests[1].content == server.requests[2].content == repair_bytes(CAP, "not json", FIRST_CHAR)


def test_r28_the_budget_text_gives_the_client_s_own_limit() -> None:
    client, server = make_client(chat_response("not json"), input_tokens_response(13_722), n_ctx=16_384)
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.extract(SINGLE, **EXTRACT)
    assert str(caught.value) == "repair request uses 14746 tokens, limit 14745"
    assert server.requests[1].content == repair_bytes(SINGLE, "not json", FIRST_CHAR)


def test_r28_one_count_per_repair_and_none_for_the_answer() -> None:
    client, server = make_client(chat_response("x"), input_tokens_response(900), chat_response("y"))
    call = client.extract(SINGLE, **EXTRACT)
    assert paths(server).count(COUNT_PATH) == 1
    assert server.requests[1].content == server.requests[2].content == repair_bytes(SINGLE, "x", FIRST_CHAR)
    assert call.outcome == "invalid_output"


def test_r28_the_budget_fault_is_a_run_fault_and_never_a_failure_kind() -> None:
    assert issubclass(ContextBudgetExceeded, ClientFault)
    assert issubclass(ServerCheckError, ClientFault)
    assert issubclass(ClientFault, RuntimeError)
    assert not issubclass(ClientFault, ValueError)
    assert not issubclass(ClientFault, httpx.RequestError)
    assert "context" not in " ".join(FAILURE_KINDS)


def budget_fault() -> ContextBudgetExceeded:
    client, _server = make_client(chat_response("not json"), input_tokens_response(10_036))
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.extract(SINGLE, **EXTRACT)
    return caught.value


@pytest.mark.parametrize("clone", [copy.copy, lambda fault: pickle.loads(pickle.dumps(fault))], ids=["copy", "pickle"])
def test_r28_a_budget_fault_keeps_its_fields_when_copied(clone: Any) -> None:
    fault = budget_fault()
    again = clone(fault)
    assert isinstance(again, ContextBudgetExceeded)
    assert (again.check, again.attempts, str(again)) == (fault.check, fault.attempts, str(fault))


# ---------------------------------------------------------------- R10 failed check calls, E30


def count_faults() -> list[Any]:
    return [
        pytest.param(
            lambda: error_response(503, "unavailable_error", "Loading model"),
            "token count: HTTP 503: unavailable_error: Loading model",
            503,
            id="http-503",
        ),
        pytest.param(
            lambda: httpx.Response(500, content=b"Internal Server Error"),
            "token count: HTTP 500: Internal Server Error",
            500,
            id="text-500",
        ),
        pytest.param(lambda: httpx.Response(500), "token count: HTTP 500: (empty body)", 500, id="empty-500"),
        pytest.param(
            lambda: httpx.Response(307, headers={"Location": "http://evil.example/"}),
            "token count: HTTP 307: (empty body)",
            307,
            id="redirect",
        ),
        pytest.param(
            lambda: httpx.ReadTimeout, "token count: timeout: ReadTimeout after the 30 s limit", None, id="timeout"
        ),
        pytest.param(
            lambda: httpx.ConnectError, "token count: transport error: ConnectError: scripted", None, id="connect-error"
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b'{"input_tokens": "812", "object": "response.input_tokens"}'),
            "token count: malformed response: input_tokens: Input should be a valid integer",
            200,
            id="count-a-str",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b'{"input_tokens": 812.0}'),
            "token count: malformed response: input_tokens: Input should be a valid integer",
            200,
            id="count-a-float",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b'{"input_tokens": true}'),
            "token count: malformed response: input_tokens: Input should be a valid integer",
            200,
            id="count-a-bool",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b'{"input_tokens": -1}'),
            "token count: malformed response: input_tokens: Input should be greater than or equal to 0",
            200,
            id="count-negative",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b"{}"),
            "token count: malformed response: input_tokens: Field required",
            200,
            id="no-count",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b"oops"),
            f"token count: malformed response: {FIRST_CHAR}",
            200,
            id="not-json",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b"[]"),
            "token count: malformed response: (root): response must be a JSON object",
            200,
            id="not-an-object",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b'{"input_tokens": 1, "input_tokens": 2}'),
            "token count: malformed response: invalid JSON: duplicate key 'input_tokens'",
            200,
            id="repeated-key",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b'{"input_tokens": 1, "object": NaN}'),
            "token count: malformed response: invalid JSON: non-finite number NaN",
            200,
            id="nan",
        ),
    ]


@pytest.mark.parametrize(("step", "text", "status"), count_faults())
def test_r10_a_failed_count_alone(step: Any, text: str, status: int | None) -> None:
    client, server = make_client(step())
    with pytest.raises(ServerCheckError) as caught:
        client.check_context(SINGLE)
    fault = caught.value
    assert (fault.path, fault.error_text, fault.http_status, fault.attempts) == (COUNT_PATH, text, status, ())
    assert str(fault) == text
    assert paths(server) == [COUNT_PATH]


@pytest.mark.parametrize(("step", "text", "status"), count_faults())
def test_r10_e30_a_failed_count_inside_a_call_stops_it(step: Any, text: str, status: int | None) -> None:
    client, server = make_client(chat_response("not json"), step())
    with pytest.raises(ServerCheckError) as caught:
        client.extract(SINGLE, **EXTRACT)
    fault = caught.value
    assert (fault.path, fault.error_text, fault.http_status) == (COUNT_PATH, text, status)
    assert len(fault.attempts) == 1
    assert (fault.attempts[0].failure_kind, fault.attempts[0].content) == ("invalid_output", "not json")
    assert paths(server) == [CHAT_PATH, COUNT_PATH]
    assert server.requests[1].content == repair_bytes(SINGLE, "not json", FIRST_CHAR)


def test_r10_e30_a_failed_count_inside_a_probe_stops_it() -> None:
    client, server = make_client(chat_response("not json"), error_response(503, "unavailable_error", "Loading model"))
    with pytest.raises(ServerCheckError) as caught:
        client.probe(PROBE, **PROBE_CALL)
    assert len(caught.value.attempts) == 1
    assert caught.value.attempts[0].request_kind == "reasoning_probe"
    assert paths(server) == [CHAT_PATH, COUNT_PATH]


def check_fault() -> ServerCheckError:
    client, _server = make_client(chat_response("not json"), error_response(503, "unavailable_error", "Loading model"))
    with pytest.raises(ServerCheckError) as caught:
        client.extract(SINGLE, **EXTRACT)
    return caught.value


@pytest.mark.parametrize("clone", [copy.copy, lambda fault: pickle.loads(pickle.dumps(fault))], ids=["copy", "pickle"])
def test_r10_a_check_fault_keeps_its_fields_when_copied(clone: Any) -> None:
    fault = check_fault()
    again = clone(fault)
    assert isinstance(again, ServerCheckError)
    assert (again.path, again.error_text, again.http_status, again.attempts, str(again)) == (
        fault.path,
        fault.error_text,
        fault.http_status,
        fault.attempts,
        str(fault),
    )


def test_r10_the_fault_s_defaults() -> None:
    fault = ServerCheckError(COUNT_PATH, "x")
    assert (fault.http_status, fault.attempts) == (None, ())


# ---------------------------------------------------------------- R29 /props


def test_r29_the_spec_s_example() -> None:
    client, server = make_client(props_response(), clock=FakeClock(0, 1))
    props = client.fetch_props()
    assert props == ServerProps(12288, 1, "qwen3-4b", "/m/q.gguf", "b11936-27b7a8a0", "...<think>...")
    assert json.loads(canonical_json(dataclasses.asdict(props)))["n_ctx"] == 12288
    assert len(canonical_sha256(props.chat_template)) == 64
    assert server.requests[0].method == "GET"
    assert paths(server) == [PROPS_PATH]


def test_r29_extra_keys_are_ignored_at_both_levels() -> None:
    client, _server = make_client(
        props_response(default_generation_settings={"n_ctx": 12288, "params": {"seed": 1}, "x": 1}, webui=True)
    )
    assert client.fetch_props().n_ctx == 12288


def test_r29_field_order() -> None:
    assert [field.name for field in dataclasses.fields(ServerProps)] == [
        "n_ctx",
        "total_slots",
        "model_alias",
        "model_path",
        "build_info",
        "chat_template",
    ]


def surrogate_props() -> list[Any]:
    return [
        pytest.param(
            lambda name=name: props_response(**{name: chr(0xD800)}),
            f"props: malformed response: {name} is not valid Unicode",
            200,
            id=f"surrogate-in-{name}",
        )
        for name in ("model_alias", "model_path", "build_info", "chat_template")
    ]


@pytest.mark.parametrize(
    ("step", "text", "status"),
    [
        pytest.param(
            lambda: props_response(default_generation_settings={"n_ctx": "12288"}),
            "props: malformed response: default_generation_settings.n_ctx: Input should be a valid integer",
            200,
            id="n-ctx-a-str",
        ),
        pytest.param(
            lambda: props_response(total_slots=True),
            "props: malformed response: total_slots: Input should be a valid integer",
            200,
            id="slots-a-bool",
        ),
        pytest.param(
            lambda: props_response(chat_template=None),
            "props: malformed response: chat_template: Input should be a valid string",
            200,
            id="no-template",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b"{}"),
            "props: malformed response: default_generation_settings: Field required; total_slots: Field required; "
            "model_alias: Field required; model_path: Field required; build_info: Field required; "
            "chat_template: Field required",
            200,
            id="empty-object",
        ),
        pytest.param(
            lambda: httpx.Response(200, content=b"oops"), f"props: malformed response: {FIRST_CHAR}", 200, id="not-json"
        ),
        pytest.param(
            lambda: httpx.Response(200, content=(json.dumps(PROPS)[:-1] + ', "total_slots": 2}').encode("utf-8")),
            "props: malformed response: invalid JSON: duplicate key 'total_slots'",
            200,
            id="repeated-key",
        ),
        *surrogate_props(),
        pytest.param(
            lambda: error_response(500, "server_error", "boom"),
            "props: HTTP 500: server_error: boom",
            500,
            id="http-500",
        ),
        pytest.param(lambda: httpx.Response(503), "props: HTTP 503: (empty body)", 503, id="empty-503"),
        pytest.param(lambda: httpx.ReadTimeout, "props: timeout: ReadTimeout after the 30 s limit", None, id="timeout"),
        pytest.param(
            lambda: httpx.ConnectError, "props: transport error: ConnectError: scripted", None, id="connect-error"
        ),
    ],
)
def test_r29_any_failure_is_a_check_error(step: Any, text: str, status: int | None) -> None:
    client, _server = make_client(step())
    with pytest.raises(ServerCheckError) as caught:
        client.fetch_props()
    fault = caught.value
    assert (fault.path, fault.error_text, fault.http_status, fault.attempts) == (PROPS_PATH, text, status, ())
    assert str(fault) == text


@pytest.mark.parametrize(
    "fields",
    [
        ("12288", 1, "a", "p", "b", "t"),
        (True, 1, "a", "p", "b", "t"),
        (12288, 1.0, "a", "p", "b", "t"),
        (12288, 1, None, "p", "b", "t"),
        (12288, 1, "a", 3, "b", "t"),
        (12288, 1, "a", "p", b"b", "t"),
        (12288, 1, "a", "p", "b", None),
    ],
)
def test_r29_server_props_refuse_wrong_types(fields: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        ServerProps(*fields)


def test_r29_server_props_are_frozen() -> None:
    props = ServerProps(12288, 1, "a", "p", "b", "t")
    with pytest.raises(dataclasses.FrozenInstanceError):
        props.n_ctx = 1  # type: ignore[misc]
    assert not hasattr(props, "__dict__")


# ---------------------------------------------------------------- ContextCheck's own rules

VALID_CHECK = ContextCheck("request", V2_SHA256, 812, 0, 1024, 1836, 12288, 11059, True, 250)


def test_section_3_context_check_field_order() -> None:
    assert [field.name for field in dataclasses.fields(ContextCheck)] == [
        "label",
        "request_sha256",
        "input_tokens",
        "reserved_tokens",
        "max_tokens",
        "uses",
        "n_ctx",
        "limit",
        "fits",
        "wall_ns",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"label": "other"}, id="unknown-label"),
        pytest.param({"request_sha256": "A" * 64}, id="upper-case-hash"),
        pytest.param({"request_sha256": "a"}, id="short-hash"),
        pytest.param({"input_tokens": -1, "uses": 1023}, id="negative-count"),
        pytest.param({"input_tokens": True, "uses": 1025}, id="bool-count"),
        pytest.param({"reserved_tokens": 1.0, "uses": 1837}, id="float-reserved"),
        pytest.param({"wall_ns": -1}, id="negative-time"),
        pytest.param({"uses": 1837}, id="uses-not-the-sum"),
        pytest.param({"input_tokens": 813}, id="input-without-uses"),
        pytest.param({"reserved_tokens": 1}, id="reserved-without-uses"),
        pytest.param({"max_tokens": 4096}, id="cap-without-uses"),
        pytest.param({"limit": 11060}, id="limit-rounded-up"),
        pytest.param({"n_ctx": 16384}, id="n-ctx-without-limit"),
        pytest.param({"fits": False}, id="fits-not-computed"),
        pytest.param({"input_tokens": 10036, "uses": 11060}, id="fits-true-past-the-limit"),
    ],
)
def test_section_3_a_context_check_refuses_inconsistent_fields(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(VALID_CHECK, **changes)


def test_section_3_a_context_check_is_consistent_past_the_limit() -> None:
    check = dataclasses.replace(VALID_CHECK, input_tokens=10036, uses=11060, fits=False)
    assert check.fits is False


def test_section_3_a_context_check_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        VALID_CHECK.fits = False  # type: ignore[misc]
    assert not hasattr(VALID_CHECK, "__dict__")


def test_r25_a_non_ascii_conversation_is_counted_as_sent() -> None:
    messages = (ChatMessage("system", "s" * 1000), ChatMessage("user", "é" * 1000))
    request = ChatRequest("single_conversation_extraction", messages)
    client, server = make_client(input_tokens_response(10))
    check = client.check_context(request)
    assert server.requests[0].content == request.body_bytes()
    assert check.request_sha256 == sha256(request.body_bytes())
    assert "é".encode() * 1000 in server.requests[0].content
