"""The client's wire side: H2 spec R1-R10, R20, R46, R47 and section 4 (S2 §3.2, §3.3, §3.6-§3.9; PHASE-0 §6.6, §7).

Every response comes from `httpx.MockTransport`. `ScriptedServer` refuses any URL but the three paths on
http://127.0.0.1:8080, so each request in these tests has its scheme, host, port and path checked. Data is synthetic.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import socket
import ssl
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest

from app.evaluation import bakeoff
from app.evaluation.bakeoff import client as client_module
from app.evaluation.bakeoff import probe as probe_module
from app.evaluation.bakeoff import schemas as schemas_module
from app.evaluation.bakeoff.client import (
    BASE_URL_PATTERN,
    CAP_SESSION_TIMEOUT_SEC,
    CHAT_PATH,
    CHECK_TIMEOUT_SEC,
    CONTEXT_LIMIT,
    CONTEXT_TOKENS,
    COUNT_PATH,
    EXTRACTION_KINDS,
    LLM_TIMEOUT_SEC,
    MAX_TOKENS,
    OUTCOMES,
    PROPS_PATH,
    REASONING_BLANK,
    REPAIR_PLACEHOLDER,
    REPEAT_PENALTY,
    REQUEST_KINDS,
    ROLES,
    SEED,
    TEMPERATURE,
    THINK_TAGS,
    TIMEOUT_SEC,
    TOP_K,
    ChatMessage,
    ChatRequest,
    ContextLabel,
    ModelServerClient,
    Outcome,
    RepairTemplate,
    RequestKind,
    RetryType,
    Role,
    repair_request,
    validate_base_url,
)
from app.evaluation.bakeoff.extraction import CUT_OFF_TEXT, Extraction
from app.evaluation.bakeoff.probe import ProbeOutput
from app.evaluation.bakeoff.schemas import (
    EXTRACTION_SCHEMA_SHA256,
    PROBE_SCHEMA_SHA256,
    canonical_json,
    canonical_sha256,
    extraction_schema,
    probe_schema,
)
from app.evaluation.tests._client_fixtures import (
    BASE_URL,
    BUNDLE,
    CAP,
    EMPTY,
    MESSAGES,
    PATHS,
    PROBE,
    PROBE_OK,
    SEGMENT_IDS,
    SINGLE,
    TEMPLATE,
    FakeClock,
    ScriptedServer,
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

BODY_KEYS = frozenset(
    {
        "cache_prompt",
        "chat_template_kwargs",
        "max_tokens",
        "messages",
        "repeat_penalty",
        "seed",
        "stream",
        "temperature",
        "top_k",
    }
)
CAPS = {"single_conversation_extraction": 1024, "cap_session_extraction": 4096, "reasoning_probe": 384}
LIMITS = {"single_conversation_extraction": 30.0, "cap_session_extraction": 300.0, "reasoning_probe": 30.0}
KIND_FORMS = [
    pytest.param(kind, constrained, id=f"{kind}-{'constrained' if constrained else 'unconstrained'}")
    for kind in REQUEST_KINDS
    for constrained in (True, False)
]
V1_BYTES = (
    b'{"cache_prompt":false,"chat_template_kwargs":{"enable_thinking":false},"max_tokens":1024,"messages":'
    b'[{"content":"S","role":"system"},{"content":"U","role":"user"}],"repeat_penalty":1.0,"seed":42,'
    b'"stream":false,"temperature":0.0,"top_k":1}'
)
V2_SHA256 = "dd3c79d14de77b4327fb3438812cdcef745ce64111511025a23f83a9e6b8aae6"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)


def wire_schema(request: ChatRequest) -> dict[str, Any]:
    return json.loads(request.body_bytes())["response_format"]["json_schema"]["schema"]


def assert_wire_fields(raw: bytes, kind: str, constrained: bool) -> None:
    """Every field's value and JSON type. `==` alone would pass 0 for false and 0 for 0.0 (R4)."""
    body = json.loads(raw)
    assert set(body) == BODY_KEYS | ({"response_format"} if constrained else set())
    assert body["cache_prompt"] is False
    assert body["stream"] is False
    assert list(body["chat_template_kwargs"]) == ["enable_thinking"]
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    for key, value in (("max_tokens", CAPS[kind]), ("seed", 42), ("top_k", 1)):
        assert type(body[key]) is int and body[key] == value, key
    for key, number in (("temperature", 0.0), ("repeat_penalty", 1.0)):
        assert type(body[key]) is float and body[key] == number, key
    assert type(body["messages"]) is list and body["messages"]
    for message in body["messages"]:
        assert set(message) == {"content", "role"}
        assert type(message["content"]) is str and message["role"] in ROLES


def nulls(value: Any) -> Iterator[None]:
    if isinstance(value, dict):
        for item in value.values():
            yield from nulls(item)
    elif isinstance(value, list):
        for item in value:
            yield from nulls(item)
    elif value is None:
        yield None


# ---------------------------------------------------------------- constants (section 3)


def test_the_kinds_and_literals() -> None:
    assert REQUEST_KINDS == ("single_conversation_extraction", "cap_session_extraction", "reasoning_probe")
    assert get_args(RequestKind) == REQUEST_KINDS
    assert EXTRACTION_KINDS == ("single_conversation_extraction", "cap_session_extraction")
    assert ROLES == ("system", "user", "assistant")
    assert get_args(Role) == ROLES
    assert get_args(RetryType) == ("none", "repair", "same_request")
    assert get_args(ContextLabel) == ("request", "repair", "repair_bound")
    assert OUTCOMES == ("valid", "invalid_output", "cut_off", "timeout", "server_error")
    assert get_args(Outcome) == OUTCOMES


def test_the_numbers() -> None:
    assert dict(MAX_TOKENS) == CAPS
    assert dict(TIMEOUT_SEC) == LIMITS
    assert (LLM_TIMEOUT_SEC, CAP_SESSION_TIMEOUT_SEC, CHECK_TIMEOUT_SEC) == (30.0, 300.0, 30.0)
    assert (CONTEXT_TOKENS, CONTEXT_LIMIT) == (12_288, 11_059)
    assert CONTEXT_LIMIT == CONTEXT_TOKENS * 9 // 10
    for name, value, kind in (
        ("TEMPERATURE", TEMPERATURE, float),
        ("REPEAT_PENALTY", REPEAT_PENALTY, float),
        ("SEED", SEED, int),
        ("TOP_K", TOP_K, int),
    ):
        assert type(value) is kind, name
    assert (TEMPERATURE, SEED, TOP_K, REPEAT_PENALTY) == (0.0, 42, 1, 1.0)


def test_the_paths_and_text_constants() -> None:
    assert (CHAT_PATH, COUNT_PATH, PROPS_PATH) == (
        "/v1/chat/completions",
        "/v1/chat/completions/input_tokens",
        "/props",
    )
    assert BASE_URL_PATTERN.pattern == r"http://127[.]0[.]0[.]1:([1-9][0-9]{0,4})"
    assert THINK_TAGS == ("<think>", "</think>")
    assert REASONING_BLANK == " \n\r\t"
    assert REPAIR_PLACEHOLDER == "<<VALIDATION_ERRORS>>"


@pytest.mark.parametrize("mapping", [MAX_TOKENS, TIMEOUT_SEC], ids=["MAX_TOKENS", "TIMEOUT_SEC"])
def test_the_mappings_cannot_be_changed(mapping: Any) -> None:
    with pytest.raises(TypeError):
        mapping["reasoning_probe"] = 1


@pytest.mark.parametrize("module", [client_module, probe_module], ids=["client", "probe"])
def test_section_2_no_module_level_mutable_state(module: Any) -> None:
    mutable = {
        name
        for name, value in vars(module).items()
        if not name.startswith("__") and isinstance(value, dict | list | set | bytearray)
    }
    assert mutable == set()


TEXT_CONSTANTS = frozenset(
    {
        "CHAT_PATH",
        "COUNT_PATH",
        "CUT_OFF_TEXT",
        "EXTRACTION_KINDS",
        "EXTRACTION_SCHEMA_SHA256",
        "FAILURE_KINDS",
        "INVALID_KINDS",
        "OUTCOMES",
        "PROBE_SCHEMA_SHA256",
        "PROPS_PATH",
        "REASONING_BLANK",
        "REPAIR_PLACEHOLDER",
        "REQUEST_KINDS",
        "ROLES",
        "THINK_TAGS",
        "VERDICTS",
        "_CONTEXT_LABELS",
        "_FINISH_REASONS",
        "_RETRY_TYPES",
    }
)


@pytest.mark.parametrize("module", [client_module, probe_module, schemas_module], ids=["client", "probe", "schemas"])
def test_section_2_no_prompt_or_repair_wording_ships_in_h2(module: Any) -> None:
    values = {name: value for name, value in vars(module).items() if not name.startswith("__")}
    assert {name for name, value in values.items() if isinstance(value, str | tuple)} <= TEXT_CONSTANTS
    assert not [name for name, value in values.items() if isinstance(value, RepairTemplate | ChatMessage | ChatRequest)]


def test_r7_r20_no_default_stands_in_for_h5_s_wording() -> None:
    for function, name in (
        (ModelServerClient.extract, "repair_template"),
        (ModelServerClient.probe, "repair_template"),
        (ModelServerClient.repair_bound, "template"),
        (repair_request, "template"),
    ):
        assert inspect.signature(function).parameters[name].default is inspect.Parameter.empty, function
    messages = next(field for field in dataclasses.fields(ChatRequest) if field.name == "messages")
    assert messages.default is dataclasses.MISSING


def test_section_2_the_package_docstring_names_the_only_network_module() -> None:
    docstring = " ".join((bakeoff.__doc__ or "").split())
    assert "Only `client` talks to the network, and only to http://127.0.0.1:<port> (S2 §3.2, §3.12)" in docstring
    assert "Nothing here calls a model or the network" not in docstring
    for module in ("`schemas`", "`probe`", "`client`"):
        assert module in docstring


def test_section_3_a_call_takes_keywords_only() -> None:
    client, server = make_client()
    with pytest.raises(TypeError):
        client.extract(SINGLE, "syn_a", SEGMENT_IDS, TEMPLATE)  # type: ignore[misc]
    with pytest.raises(TypeError):
        client.probe(PROBE, "probe_01", BUNDLE, TEMPLATE)  # type: ignore[misc]
    with pytest.raises(TypeError):
        repair_request(SINGLE, "{}", "e", TEMPLATE)  # type: ignore[misc]
    assert server.requests == []


def test_section_3_the_other_keyword_only_parameters() -> None:
    init = inspect.signature(ModelServerClient.__init__).parameters
    for name in ("transport", "clock", "n_ctx"):
        assert init[name].kind is inspect.Parameter.KEYWORD_ONLY, name
    parse = inspect.signature(probe_module.parse_probe).parameters
    for name in ("bundle_source_ids", "finish_reason"):
        assert parse[name].kind is inspect.Parameter.KEYWORD_ONLY, name


def test_section_3_the_clock_defaults_to_the_monotonic_nanosecond_clock() -> None:
    parameters = inspect.signature(ModelServerClient.__init__).parameters
    assert parameters["clock"].default is time.perf_counter_ns
    assert parameters["n_ctx"].default == 12_288
    assert parameters["transport"].default is None


# ---------------------------------------------------------------- R1 loopback base URL

ACCEPTED_URLS = ["http://127.0.0.1:8080", "http://127.0.0.1:1", "http://127.0.0.1:65535"]
REFUSED_URLS = [
    "http://localhost:8080",
    "http://[::1]:8080",
    "http://127.0.0.2:8080",
    "http://192.168.1.20:8080",
    "http://10.0.0.5:8080",
    "http://0.0.0.0:8080",
    "http://127.1:8080",
    "http://2130706433:8080",
    "http://0x7f.0.0.1:8080",
    "https://127.0.0.1:8080",
    "HTTP://127.0.0.1:8080",
    "http://user@127.0.0.1:8080",
    "http://u:p@127.0.0.1:8080",
    "http://127.0.0.1:8080@evil.example",
    "http://127.0.0.1",
    "http://127.0.0.1:",
    "http://127.0.0.1:0",
    "http://127.0.0.1:65536",
    "http://127.0.0.1:99999",
    "http://127.0.0.1:100000",
    "http://127.0.0.1:08080",
    "http://127.0.0.1:" + "".join(chr(0xFF10 + int(digit)) for digit in "8080"),  # fullwidth digits
    "http://127.0.0.1:" + "".join(chr(0x0660 + int(digit)) for digit in "8080"),  # Arabic-Indic digits
    "http://127.0.0.1:8080/",
    "http://127.0.0.1:8080/v1",
    "http://127.0.0.1:8080?x=1",
    "http://127.0.0.1:8080#f",
    "http://127.0.0.1:8080\n",
    " http://127.0.0.1:8080",
    "http://127.0.0.1:8080 ",
    "",
]


@pytest.mark.parametrize("url", ACCEPTED_URLS)
def test_r1_the_loopback_url_comes_back_unchanged(url: str) -> None:
    assert validate_base_url(url) is url
    client = ModelServerClient(url, transport=httpx.MockTransport(ScriptedServer()))
    assert client.base_url is url


def test_r2_r3_requests_go_to_the_configured_port() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return input_tokens_response(5)

    client = ModelServerClient("http://127.0.0.1:65535", transport=httpx.MockTransport(answer))
    client.check_context(SINGLE)
    assert str(seen[0].url) == "http://127.0.0.1:65535/v1/chat/completions/input_tokens"


@pytest.mark.parametrize("url", REFUSED_URLS)
def test_r1_every_other_url_is_refused_and_named(url: str) -> None:
    with pytest.raises(ValueError) as caught:
        validate_base_url(url)
    assert repr(url) in str(caught.value)


@pytest.mark.parametrize("url", REFUSED_URLS)
def test_r1_e3_a_refused_url_builds_no_http_object(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("no httpx object may exist for a refused URL")

    monkeypatch.setattr(httpx, "Client", refuse)
    monkeypatch.setattr(httpx, "HTTPTransport", refuse)
    with pytest.raises(ValueError) as caught:
        ModelServerClient(url)
    assert repr(url) in str(caught.value)


def test_r1_the_url_is_checked_first_and_whatever_the_transport() -> None:
    with pytest.raises(ValueError, match="base_url"):
        ModelServerClient("http://localhost:8080", n_ctx=True)
    with pytest.raises(ValueError, match="base_url"):
        ModelServerClient("http://localhost:8080", transport=httpx.MockTransport(ScriptedServer()))


@pytest.mark.parametrize("url", [b"http://127.0.0.1:8080", httpx.URL("http://127.0.0.1:8080"), None, 8080])
def test_r1_a_url_that_is_not_a_str_is_a_type_error(url: Any) -> None:
    with pytest.raises(TypeError, match="base_url must be a string"):  # `re` would raise a TypeError of its own
        validate_base_url(url)
    with pytest.raises(TypeError, match="base_url must be a string"):
        ModelServerClient(url)


def test_r1_the_pattern_is_for_fullmatch_only() -> None:
    assert BASE_URL_PATTERN.match("http://127.0.0.1:8080/v1") is not None  # why `match` alone would be wrong
    assert BASE_URL_PATTERN.fullmatch("http://127.0.0.1:8080/v1") is None
    assert BASE_URL_PATTERN.fullmatch("http://127.0.0.1:8080\n") is None


# ---------------------------------------------------------------- R2 httpx configuration, E2, L1, L7, L8

PROXY = "http://10.0.0.1:3128"
LOOPBACK = httpx.URL("http://127.0.0.1:8080/v1/chat/completions")


@pytest.fixture
def proxy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, PROXY)
    monkeypatch.delenv("NO_PROXY", raising=False)


def test_r2_the_default_transport_gets_exactly_five_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[dict[str, Any]] = []

    class Stub(httpx.BaseTransport):
        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    monkeypatch.setattr(httpx, "HTTPTransport", Stub)
    client = ModelServerClient(BASE_URL)
    assert isinstance(client._http._transport, Stub)
    assert len(built) == 1
    settings = built[0]
    assert set(settings) == {"retries", "trust_env", "http1", "http2", "limits"}
    assert type(settings["retries"]) is int and settings["retries"] == 0
    assert settings["trust_env"] is False
    assert settings["http1"] is True
    assert settings["http2"] is False
    assert settings["limits"] == httpx.Limits(max_connections=1, max_keepalive_connections=0)


def test_r2_the_client_gets_exactly_five_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    real = httpx.Client

    def spy(**kwargs: Any) -> httpx.Client:
        seen.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(httpx, "Client", spy)
    transport = httpx.MockTransport(ScriptedServer())
    ModelServerClient(BASE_URL, transport=transport)
    assert len(seen) == 1
    settings = seen[0]
    assert set(settings) == {"base_url", "transport", "trust_env", "follow_redirects", "timeout"}
    assert settings["base_url"] == BASE_URL
    assert settings["transport"] is transport
    assert settings["trust_env"] is False
    assert settings["follow_redirects"] is False
    assert settings["timeout"] == httpx.Timeout(30.0)


def test_r2_e2_proxy_variables_reach_nothing(proxy_environment: None) -> None:
    client = ModelServerClient(BASE_URL)  # the default transport; nothing is sent through it
    assert client._http.trust_env is False
    assert client._http.follow_redirects is False
    assert client._http._mounts == {}
    assert client._http._transport_for_url(LOOPBACK) is client._http._transport
    client.close()


def test_r2_e2_with_proxy_variables_set_requests_still_reach_the_mock_only(proxy_environment: None) -> None:
    call, server = run_extract(chat_response(EMPTY))
    assert call.outcome == "valid"
    assert paths(server) == [CHAT_PATH]


def test_r2_the_default_transport_is_one_connection_without_keep_alive_or_retries() -> None:
    client = ModelServerClient(BASE_URL)
    pool = client._http._transport._pool  # type: ignore[attr-defined]
    assert (pool._max_connections, pool._max_keepalive_connections, pool._retries) == (1, 0, 0)
    assert (pool._http1, pool._http2) == (True, False)
    client.close()


def test_r2_an_injected_transport_replaces_the_transport_and_nothing_else() -> None:
    transport = httpx.MockTransport(ScriptedServer())
    client = ModelServerClient(BASE_URL, transport=transport)
    assert client._http._transport is transport
    assert client._http.trust_env is False
    assert client._http.follow_redirects is False
    assert client._http.timeout == httpx.Timeout(30.0)
    assert client._http._mounts == {}


def test_l1_a_client_that_trusts_the_environment_would_proxy_loopback_traffic(proxy_environment: None) -> None:
    trusting = httpx.Client(trust_env=True)
    assert len(trusting._mounts) == 3
    assert trusting._transport_for_url(LOOPBACK) is not trusting._transport
    assert httpx.Client(trust_env=False)._mounts == {}


def test_l7_httpx_reads_no_netrc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    netrc = tmp_path / ".netrc"
    netrc.write_text("machine 127.0.0.1 login synthetic password synthetic\n", encoding="utf-8")
    for name in ("NETRC", "HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(netrc if name == "NETRC" else tmp_path))
    server = ScriptedServer(httpx.Response(200, content=b"{}"))
    with httpx.Client(trust_env=True, transport=httpx.MockTransport(server)) as trusting:
        trusting.get(BASE_URL + PROPS_PATH)  # even a trusting client, so no setting of ours is what keeps it out
    assert "authorization" not in server.requests[0].headers
    _call, ours = run_extract(chat_response(EMPTY))
    assert "authorization" not in ours.requests[0].headers


def test_l8_the_transport_s_own_trust_env_defaults_to_true() -> None:
    assert inspect.signature(httpx.HTTPTransport.__init__).parameters["trust_env"].default is True
    httpx.HTTPTransport().close()  # constructing one opens no socket: the autouse fixture would fail this test


@pytest.mark.parametrize("name", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_r2_l8_ssl_environment_settings_are_never_read(
    name: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[dict[str, Any]] = []
    real = ssl.create_default_context

    def spy(*args: Any, **kwargs: Any) -> ssl.SSLContext:
        seen.append(kwargs)
        return real(*args, **kwargs)

    missing = str(tmp_path / "missing")
    monkeypatch.setattr(ssl, "create_default_context", spy)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.setenv(name, missing)
    ModelServerClient(BASE_URL).close()  # the transport's own default would read the setting
    assert seen and all(missing not in kwargs.values() for kwargs in seen)  # a missing directory alone raises nothing
    if name == "SSL_CERT_FILE":
        with pytest.raises(OSError):
            httpx.HTTPTransport()


def test_close_and_the_context_manager_close_the_http_client() -> None:
    client, _server = make_client()
    assert client._http.is_closed is False
    client.close()
    assert client._http.is_closed is True
    with make_client()[0] as managed:
        assert isinstance(managed, ModelServerClient)
        assert managed._http.is_closed is False
    assert managed._http.is_closed is True


@pytest.mark.parametrize(
    ("count", "fault"),
    [
        pytest.param(lambda: input_tokens_response(10_036), client_module.ContextBudgetExceeded, id="budget"),
        pytest.param(
            lambda: error_response(503, "unavailable_error", "Loading model"),
            client_module.ServerCheckError,
            id="failed-count",
        ),
    ],
)
def test_section_3_the_context_manager_closes_and_re_raises_a_fault(count: Any, fault: type[Exception]) -> None:
    client, _server = make_client(chat_response("not json"), count())
    with pytest.raises(fault):
        with client:
            client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert client._http.is_closed is True


# ---------------------------------------------------------------- R3 three exchanges

HTTPX_DEFAULTS = ("accept", "accept-encoding", "connection", "user-agent")


def assert_only_httpx_s_defaults(headers: httpx.Headers, *own: str) -> None:
    """R3, by value: `own` names the headers the exchange adds. `trust_env=False`, or a bad SSL_CERT_FILE stops this."""
    assert set(headers) == {*HTTPX_DEFAULTS, "host", *own}
    with httpx.Client(trust_env=False) as plain:
        for name in HTTPX_DEFAULTS:
            assert headers[name] == plain.headers[name], name


def test_r3_a_completion_is_a_post_of_the_exact_bytes() -> None:
    _call, server = run_extract(chat_response(EMPTY))
    request = server.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8080/v1/chat/completions"
    assert request.content == SINGLE.body_bytes()
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in request.headers


def test_r3_a_token_count_is_a_post_of_the_exact_bytes() -> None:
    client, server = make_client(input_tokens_response(812))
    client.check_context(SINGLE)
    request = server.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://127.0.0.1:8080/v1/chat/completions/input_tokens"
    assert request.content == SINGLE.body_bytes()
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in request.headers
    assert_only_httpx_s_defaults(request.headers, "content-length", "content-type")


def test_r3_props_is_a_get_with_no_body() -> None:
    client, server = make_client(error_response(500, "server_error", "boom"))
    with pytest.raises(client_module.ServerCheckError):
        client.fetch_props()
    request = server.requests[0]
    assert request.method == "GET"
    assert str(request.url) == "http://127.0.0.1:8080/props"
    assert request.content == b""
    assert "content-type" not in request.headers
    assert "authorization" not in request.headers
    assert_only_httpx_s_defaults(request.headers)


def test_r3_httpx_s_own_default_headers_stay() -> None:
    _call, server = run_extract(chat_response(EMPTY))
    headers = server.requests[0].headers
    assert set(headers) == {
        "accept",
        "accept-encoding",
        "connection",
        "content-length",
        "content-type",
        "host",
        "user-agent",
    }
    assert headers["host"] == "127.0.0.1:8080"
    assert headers["content-length"] == "1289"
    assert headers["user-agent"].startswith("python-httpx/")
    assert_only_httpx_s_defaults(headers, "content-length", "content-type")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/tokenize"),
        ("POST", "/apply-template"),
        ("GET", CHAT_PATH),
        ("GET", COUNT_PATH),
        ("POST", PROPS_PATH),
        ("POST", "/v1/chat/completions/"),
        ("DELETE", CHAT_PATH),
    ],
)
def test_r3_the_private_send_takes_only_the_three_exchanges(method: str, path: str) -> None:
    client, server = make_client()
    with pytest.raises(ValueError):
        client._exchange(method, path, b"{}", 30.0)
    assert server.requests == []


# ---------------------------------------------------------------- R4 exact body and the V vectors

FIX = RepairTemplate("Fix: <<VALIDATION_ERRORS>>")
V5 = repair_request(SINGLE, invalid_output='{"claims": [', error_text=CUT_OFF_TEXT, template=FIX)
V1 = ChatRequest("single_conversation_extraction", MESSAGES, constrained=False)
V6 = ChatRequest("reasoning_probe", MESSAGES, constrained=False)
V7 = ChatRequest("cap_session_extraction", MESSAGES, constrained=False)
VECTORS = [
    pytest.param(V1, 238, "0769cfe5fd97b7e454ab5953c363718353983920cc70dc5a99c46063057051bc", id="V1"),
    pytest.param(SINGLE, 1289, V2_SHA256, id="V2"),
    pytest.param(CAP, 1289, "7755da2cba05f91c9236b528683a9f2fd9caf248d5f74ed63290aa097b9c57c0", id="V3"),
    pytest.param(PROBE, 712, "283184bd4ad18282f1c082dc48086cfdead5dbdbe0e80986d6a50b2845051e38", id="V4"),
    pytest.param(V5, 1429, "686cc2041d484cf2e3b072e5ca7a4c132440cb3af1caf527839334f0cef57cbb", id="V5"),
    pytest.param(V6, 237, "7104beddf52506552f7a7d8baa7cd299dbdc62a2a93c4c6fea232342510668a2", id="V6"),
    pytest.param(V7, 238, "44756c4dd7dee1c3c7e260fccb6c947a2e6472db256a353a8d6b78c3f845e1bd", id="V7"),
]
NEVER_SENT = frozenset(
    {
        "model",
        "tools",
        "tool_choice",
        "parallel_tool_calls",
        "functions",
        "function_call",
        "grammar",
        "json_schema",
        "n",
        "stop",
        "logprobs",
        "top_logprobs",
        "n_probs",
        "n_predict",
        "max_completion_tokens",
        "reasoning_format",
        "reasoning_effort",
        "reasoning_budget_tokens",
        "id_slot",
        "response_fields",
        "stream_options",
        "timings_per_token",
        "top_p",
        "min_p",
        "typical_p",
        "presence_penalty",
        "frequency_penalty",
        "samplers",
        "logit_bias",
    }
)
NEVER_SENT_PREFIXES = ("dry_", "xtc_", "mirostat")


@pytest.mark.parametrize(("request_", "size", "sha256"), VECTORS)
def test_r4_the_byte_vectors(request_: ChatRequest, size: int, sha256: str) -> None:
    raw = request_.body_bytes()
    assert len(raw) == size
    assert hashlib.sha256(raw).hexdigest() == sha256
    assert request_.sha256() == sha256
    assert raw.isascii()


def test_r4_v1_exactly() -> None:
    assert V1.body_bytes() == V1_BYTES


def test_r5_v2_is_v1_with_the_response_format_after_repeat_penalty() -> None:
    wrapper = (
        b'"response_format":{"json_schema":{"schema":'
        + canonical_json(extraction_schema())
        + b'},"type":"json_schema"},'
    )
    assert SINGLE.body_bytes() == V1_BYTES.replace(b'"repeat_penalty":1.0,', b'"repeat_penalty":1.0,' + wrapper)


def test_r4_v5_holds_the_repair_turns_in_order() -> None:
    assert json.loads(V5.body_bytes())["messages"] == [
        {"content": "S", "role": "system"},
        {"content": "U", "role": "user"},
        {"content": '{"claims": [', "role": "assistant"},
        {"content": "Fix: output was cut off at the token cap (finish_reason=length)", "role": "user"},
    ]


@pytest.mark.parametrize(("request_", "size", "sha256"), [vector for vector in VECTORS if vector.id != "V5"])
def test_r8_the_vectors_are_what_reaches_the_wire(request_: ChatRequest, size: int, sha256: str) -> None:
    call, server = run_call(request_, chat_response(valid_content(request_.kind)))
    raw = server.requests[0].content
    assert len(raw) == size
    assert raw == request_.body_bytes()
    assert hashlib.sha256(raw).hexdigest() == call.attempts[0].request_sha256 == sha256


@pytest.mark.parametrize(("kind", "constrained"), KIND_FORMS)
def test_r4_every_field_has_its_value_and_json_type(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    assert_wire_fields(request.body_bytes(), kind, constrained)
    body = request.body()
    assert body["cache_prompt"] is False and body["stream"] is False
    assert body["chat_template_kwargs"]["enable_thinking"] is False
    assert type(body["temperature"]) is float and type(body["repeat_penalty"]) is float
    assert type(body["seed"]) is int and type(body["top_k"]) is int and type(body["max_tokens"]) is int


@pytest.mark.parametrize(("kind", "constrained"), KIND_FORMS)
def test_r47_what_the_server_receives_has_every_field(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    _call, server = run_call(request, chat_response(valid_content(kind)))
    sent = server.requests[0]
    assert sent.method == "POST"
    assert str(sent.url) == BASE_URL + CHAT_PATH
    assert sent.headers["content-type"] == "application/json"
    assert_wire_fields(sent.content, kind, constrained)
    limit = LIMITS[kind]
    assert sent.extensions["timeout"] == {"connect": limit, "read": limit, "write": limit, "pool": limit}


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b'"enable_thinking":false', b'"enable_thinking":0'),
        (b'"enable_thinking":false', b'"enable_thinking":null'),
        (b'"enable_thinking":false', b'"enable_thinking":"false"'),
        (b'"enable_thinking":false', b'"enable_thinking":true'),
        (b'"cache_prompt":false', b'"cache_prompt":0'),
        (b'"cache_prompt":false', b'"cache_prompt":null'),
        (b'"stream":false', b'"stream":0'),
        (b'"temperature":0.0', b'"temperature":0'),
        (b'"temperature":0.0', b'"temperature":false'),
        (b'"repeat_penalty":1.0', b'"repeat_penalty":1'),
        (b'"repeat_penalty":1.0', b'"repeat_penalty":true'),
        (b'"seed":42', b'"seed":42.0'),
        (b'"top_k":1', b'"top_k":1.0'),
        (b'"top_k":1', b'"top_k":true'),
        (b'"max_tokens":1024', b'"max_tokens":1024.0'),
        (b'"max_tokens":1024', b'"max_tokens":4096'),
    ],
)
def test_r4_the_type_check_catches_a_wrong_json_type(old: bytes, new: bytes) -> None:
    raw = SINGLE.body_bytes().replace(old, new)
    assert raw != SINGLE.body_bytes()
    with pytest.raises(AssertionError):
        assert_wire_fields(raw, "single_conversation_extraction", True)


@pytest.mark.parametrize(("request_", "size", "sha256"), VECTORS)
def test_r4_the_literal_booleans_and_the_tail(request_: ChatRequest, size: int, sha256: str) -> None:
    raw = request_.body_bytes()
    assert b'"chat_template_kwargs":{"enable_thinking":false}' in raw
    assert b'"cache_prompt":false' in raw
    assert raw.endswith(b'"stream":false,"temperature":0.0,"top_k":1}')


@pytest.mark.parametrize(("request_", "size", "sha256"), VECTORS)
def test_r4_nothing_else_is_ever_sent(request_: ChatRequest, size: int, sha256: str) -> None:
    body = json.loads(request_.body_bytes())
    assert set(body) & NEVER_SENT == set()
    assert not [key for key in body if key.startswith(NEVER_SENT_PREFIXES)]
    assert list(nulls(body)) == []


def test_r4_a_request_takes_only_its_kind_messages_and_form() -> None:
    assert [field.name for field in dataclasses.fields(ChatRequest)] == ["kind", "messages", "constrained"]
    with pytest.raises(TypeError):
        ChatRequest("reasoning_probe", MESSAGES, max_tokens=5)  # type: ignore[call-arg]
    with pytest.raises(dataclasses.FrozenInstanceError):
        SINGLE.kind = "reasoning_probe"  # type: ignore[misc]


@pytest.mark.parametrize(("kind", "constrained"), KIND_FORMS)
def test_r4_the_cap_and_the_limit_come_from_the_kind(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    assert request.max_tokens == CAPS[kind]
    assert request.timeout_sec == LIMITS[kind]


def test_r4_a_body_is_a_fresh_plain_dict() -> None:
    body = SINGLE.body()
    assert type(body) is dict
    body["messages"].append({"content": "x", "role": "user"})
    body["response_format"]["json_schema"]["schema"]["type"] = "array"
    body["chat_template_kwargs"]["enable_thinking"] = True
    assert SINGLE.sha256() == V2_SHA256
    assert hashlib.sha256(SINGLE.body_bytes()).hexdigest() == V2_SHA256


# ---------------------------------------------------------------- R5 response_format nesting, E1


@pytest.mark.parametrize(
    ("request_", "schema", "schema_sha256"),
    [
        pytest.param(SINGLE, extraction_schema(), EXTRACTION_SCHEMA_SHA256, id="single"),
        pytest.param(CAP, extraction_schema(), EXTRACTION_SCHEMA_SHA256, id="cap_session"),
        pytest.param(PROBE, probe_schema(), PROBE_SCHEMA_SHA256, id="probe"),
    ],
)
def test_r5_the_nesting_is_exact(request_: ChatRequest, schema: dict[str, Any], schema_sha256: str) -> None:
    body = json.loads(request_.body_bytes())
    wrapper = body["response_format"]
    assert set(wrapper) == {"type", "json_schema"}
    assert wrapper["type"] == "json_schema"
    assert set(wrapper["json_schema"]) == {"schema"}
    assert wrapper["json_schema"]["schema"] == schema
    assert canonical_sha256(wire_schema(request_)) == schema_sha256
    assert "schema" not in body and "json_schema" not in body and "grammar" not in body


@pytest.mark.parametrize(
    "wrong",
    [
        pytest.param(
            lambda schema: {"response_format": {"type": "json_schema", "schema": schema}}, id="e1-readme-shape"
        ),
        pytest.param(
            lambda schema: {"response_format": {"type": "json_schema", "json_schema": schema}}, id="one-level"
        ),
        pytest.param(lambda schema: {"response_format": {"type": "json_object", "schema": schema}}, id="json_object"),
        pytest.param(
            lambda schema: {"response_format": {"type": "json_schema", "json_schema": {"name": "x", "schema": schema}}},
            id="name",
        ),
        pytest.param(
            lambda schema: {
                "response_format": {"type": "json_schema", "json_schema": {"schema": schema, "strict": True}}
            },
            id="strict",
        ),
        pytest.param(
            lambda schema: {"response_format": {"type": "json_schema"}, "json_schema": schema}, id="top-level"
        ),
        pytest.param(
            lambda schema: {"response_format": {"type": "json_schema"}, "grammar": "root ::= x"}, id="grammar"
        ),
    ],
)
def test_e1_every_silent_mistake_changes_the_bytes(wrong: Any) -> None:
    body = {**SINGLE.body(), **wrong(extraction_schema())}
    raw = canonical_json(body)
    assert raw != SINGLE.body_bytes()
    assert hashlib.sha256(raw).hexdigest() != V2_SHA256


def test_r5_a_probe_never_carries_the_extraction_schema_or_the_reverse() -> None:
    assert canonical_sha256(wire_schema(PROBE)) != EXTRACTION_SCHEMA_SHA256
    assert canonical_sha256(wire_schema(SINGLE)) != PROBE_SCHEMA_SHA256
    assert wire_schema(SINGLE) == wire_schema(CAP)


# ---------------------------------------------------------------- R6 unconstrained pass


@pytest.mark.parametrize(
    ("constrained", "unconstrained"),
    [pytest.param(SINGLE, V1, id="V2-V1"), pytest.param(CAP, V7, id="V3-V7"), pytest.param(PROBE, V6, id="V4-V6")],
)
def test_r6_the_unconstrained_body_only_leaves_out_response_format(
    constrained: ChatRequest, unconstrained: ChatRequest
) -> None:
    with_schema = json.loads(constrained.body_bytes())
    assert "response_format" not in json.loads(unconstrained.body_bytes())
    assert {k: v for k, v in with_schema.items() if k != "response_format"} == json.loads(unconstrained.body_bytes())
    assert constrained.max_tokens == unconstrained.max_tokens
    assert constrained.timeout_sec == unconstrained.timeout_sec


def test_r6_the_unconstrained_repair_keeps_the_form_on_every_request() -> None:
    call, server = run_extract(chat_response("not json"), input_tokens_response(900), chat_response(EMPTY), request=V1)
    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH]
    assert all("response_format" not in json.loads(request.content) for request in server.requests)
    expected = repair_bytes(V1, "not json", "invalid JSON: Expecting value (line 1, column 1)")
    assert server.requests[1].content == server.requests[2].content == expected
    assert call.constrained is False
    assert [attempt.constrained for attempt in call.attempts] == [False, False]
    assert call.outcome == "valid"


def test_r6_raw_validity_is_read_from_the_first_attempt() -> None:
    fenced = "```json\n" + EMPTY + "\n```"
    call, server = run_extract(chat_response(fenced), input_tokens_response(900), chat_response(EMPTY), request=V1)
    assert call.attempts[0].failure_kind == "invalid_output"
    assert call.attempts[0].content == fenced
    assert call.first_attempt_failed is True
    assert completions(server)[1].content == repair_bytes(
        V1, fenced, "invalid JSON: Expecting value (line 1, column 1)"
    )


def test_r6_the_unconstrained_probe_uses_the_same_validator_and_loop() -> None:
    bad = '{"citations": [{"sourceId": "src_9"}], "reason": "r", "verdict": "SUPPORTED"}'
    error = "citations.0.sourceId: 'src_9' is not a source id of this bundle"
    call, server = run_probe(chat_response(bad), input_tokens_response(900), chat_response(PROBE_OK), request=V6)
    assert call.attempts[0].error_text == error
    assert completions(server)[1].content == repair_bytes(V6, bad, error)
    assert json.loads(completions(server)[1].content)["max_tokens"] == 384
    assert call.output == ProbeOutput("SUPPORTED", "r", ("src_1",))


# ---------------------------------------------------------------- R7 messages, E22


def test_r7_each_message_is_exactly_content_and_role_in_order() -> None:
    messages = (
        ChatMessage("system", "s"),
        ChatMessage("user", "u1"),
        ChatMessage("assistant", "a"),
        ChatMessage("user", "u2"),
    )
    raw = ChatRequest("reasoning_probe", messages, constrained=False).body_bytes()
    assert b'"messages":[{"content":"s","role":"system"},{"content":"u1","role":"user"},' in raw
    assert b'{"content":"a","role":"assistant"},{"content":"u2","role":"user"}],' in raw


def test_r7_content_is_sent_as_given() -> None:
    text = 'é "quoted" \\ back\nline ' + chr(0x1F600)
    request = ChatRequest("reasoning_probe", (ChatMessage("user", text),))
    assert json.loads(request.body_bytes())["messages"] == [{"content": text, "role": "user"}]
    assert "é".encode() in request.body_bytes()  # raw UTF-8, not a JSON escape
    assert ChatMessage("user", "").content == ""


@pytest.mark.parametrize(
    "messages",
    [
        pytest.param((), id="empty"),
        pytest.param((ChatMessage("user", "U"), ChatMessage("assistant", "A")), id="e22-trailing-assistant"),
        pytest.param((ChatMessage("assistant", "A"),), id="assistant-only"),
        pytest.param((ChatMessage("system", "S"),), id="system-only"),
        pytest.param([ChatMessage("user", "U")], id="a-list"),
        pytest.param(None, id="none"),
        pytest.param(("user",), id="not-a-message"),
        pytest.param(({"content": "U", "role": "user"},), id="a-dict"),
        pytest.param(("x", ChatMessage("user", "U")), id="a-bad-first-message"),
        pytest.param((ChatMessage("user", "U"), "x"), id="a-bad-last-message"),
    ],
)
@pytest.mark.parametrize("kind", REQUEST_KINDS)
def test_r7_e22_a_bad_message_list_is_refused(kind: Any, messages: Any) -> None:
    with pytest.raises(ValueError):
        ChatRequest(kind, messages)


@pytest.mark.parametrize("constrained", [1, 0, None, "true"])
def test_r7_constrained_must_be_a_real_bool(constrained: Any) -> None:
    with pytest.raises(ValueError):
        ChatRequest("reasoning_probe", (ChatMessage("user", "U"),), constrained=constrained)


@pytest.mark.parametrize("kind", ["single", "cap_session", "probe", "", None])
def test_r7_the_kind_must_be_one_of_the_three(kind: Any) -> None:
    with pytest.raises(ValueError):
        ChatRequest(kind, MESSAGES)


@pytest.mark.parametrize("role", ["tool", "function", "developer", "USER", "", None])
def test_r7_the_role_must_be_one_of_the_three(role: Any) -> None:
    with pytest.raises(ValueError):
        ChatMessage(role, "x")


@pytest.mark.parametrize("content", [3, None, b"x", ["x"]])
def test_r7_content_must_be_a_str(content: Any) -> None:
    with pytest.raises(TypeError):
        ChatMessage("user", content)


def test_r7_messages_and_requests_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        MESSAGES[0].content = "x"  # type: ignore[misc]
    assert not hasattr(MESSAGES[0], "__dict__")
    assert not hasattr(SINGLE, "__dict__")


# ---------------------------------------------------------------- R8 the bytes on the wire are what is hashed


def test_r8_the_record_holds_the_hash_of_the_bytes_on_the_wire() -> None:
    call, server = run_extract(chat_response(EMPTY))
    assert hashlib.sha256(server.requests[0].content).hexdigest() == call.attempts[0].request_sha256 == V2_SHA256


def test_r8_the_body_is_canonical_not_httpx_s_own_json() -> None:
    text = "é"
    request = ChatRequest("reasoning_probe", (ChatMessage("user", text),))
    call, server = run_probe(chat_response(PROBE_OK), request=request)
    raw = server.requests[0].content
    assert raw == canonical_json(request.body())
    assert raw != json.dumps(request.body()).encode("utf-8")
    assert raw != httpx.Request("POST", BASE_URL, json=request.body()).content  # httpx would keep insertion order
    assert call.attempts[0].request_sha256 == hashlib.sha256(raw).hexdigest() == request.sha256()


def test_r8_a_repair_is_hashed_as_sent_too() -> None:
    call, server = run_extract(chat_response("not json"), input_tokens_response(900), chat_response(EMPTY))
    assert hashlib.sha256(server.requests[0].content).hexdigest() == call.attempts[0].request_sha256
    assert hashlib.sha256(server.requests[2].content).hexdigest() == call.attempts[1].request_sha256
    assert call.context_checks[0].request_sha256 == call.attempts[1].request_sha256
    expected = repair_bytes(SINGLE, "not json", "invalid JSON: Expecting value (line 1, column 1)")
    assert server.requests[2].content == expected


# ---------------------------------------------------------------- R9, R10 timeouts, L2


def limit_of(request: httpx.Request) -> dict[str, float]:
    return request.extensions["timeout"]


def all_four(limit: float) -> dict[str, float]:
    return {"connect": limit, "read": limit, "write": limit, "pool": limit}


@pytest.mark.parametrize(("kind", "constrained"), KIND_FORMS)
def test_r9_both_attempts_carry_the_kind_s_limit(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    call, server = run_call(request, httpx.ReadTimeout, httpx.ReadTimeout)
    assert [limit_of(sent) for sent in server.requests] == [all_four(LIMITS[kind])] * 2
    assert [attempt.timeout_sec for attempt in call.attempts] == [LIMITS[kind]] * 2
    assert server.requests[1].content == server.requests[0].content == request.body_bytes()


@pytest.mark.parametrize(("kind", "constrained"), KIND_FORMS)
def test_r9_r10_a_repair_keeps_the_limit_and_its_count_takes_30_s(kind: Any, constrained: bool) -> None:
    request = ChatRequest(kind, MESSAGES, constrained)
    content = valid_content(kind)
    _call, server = run_call(request, chat_response("not json"), input_tokens_response(900), chat_response(content))
    assert [limit_of(sent) for sent in server.requests] == [
        all_four(LIMITS[kind]),
        all_four(30.0),
        all_four(LIMITS[kind]),
    ]
    expected = repair_bytes(request, "not json", "invalid JSON: Expecting value (line 1, column 1)")
    assert server.requests[2].content == expected


@pytest.mark.parametrize("request_", [SINGLE, CAP, PROBE], ids=["single", "cap_session", "probe"])
def test_r10_a_lone_count_takes_30_s_for_every_kind(request_: ChatRequest) -> None:
    client, server = make_client(input_tokens_response(5), input_tokens_response(5))
    client.check_context(request_)
    client.repair_bound(request_, TEMPLATE)
    assert [limit_of(sent) for sent in server.requests] == [all_four(30.0)] * 2


def test_r10_props_takes_30_s() -> None:
    client, server = make_client(httpx.ReadTimeout)
    with pytest.raises(client_module.ServerCheckError):
        client.fetch_props()
    assert limit_of(server.requests[0]) == all_four(30.0)


def test_l2_a_per_request_timeout_overrides_the_client_default() -> None:
    server = ScriptedServer(httpx.Response(200, content=b"{}"), httpx.Response(200, content=b"{}"))
    with httpx.Client(timeout=httpx.Timeout(1.0), transport=httpx.MockTransport(server)) as client:
        client.get(BASE_URL + PROPS_PATH, timeout=httpx.Timeout(5.0))
        client.get(BASE_URL + PROPS_PATH)
    assert [limit_of(sent) for sent in server.requests] == [all_four(5.0), all_four(1.0)]


# ---------------------------------------------------------------- R20 repair template


def test_r20_the_template_needs_the_placeholder_exactly_once() -> None:
    assert TEMPLATE.text == "Fix:\n<<VALIDATION_ERRORS>>\nAnswer again."
    for text in ("no marker", "<<VALIDATION_ERRORS>> <<VALIDATION_ERRORS>>", "", "<<validation_errors>>"):
        with pytest.raises(ValueError):
            RepairTemplate(text)


@pytest.mark.parametrize("text", [None, 3, b"<<VALIDATION_ERRORS>>"])
def test_r20_the_template_must_be_a_str(text: Any) -> None:
    with pytest.raises(ValueError):
        RepairTemplate(text)


def test_r20_render_is_plain_one_pass_substitution() -> None:
    assert TEMPLATE.render("{x} $y <<VALIDATION_ERRORS>>") == "Fix:\n{x} $y <<VALIDATION_ERRORS>>\nAnswer again."
    assert FIX.render(CUT_OFF_TEXT) == "Fix: output was cut off at the token cap (finish_reason=length)"
    assert RepairTemplate('{"a": {0}, "b": <<VALIDATION_ERRORS>>}').render("%s") == '{"a": {0}, "b": %s}'
    assert RepairTemplate("<<VALIDATION_ERRORS>>").render("line 1\nline 2") == "line 1\nline 2"
    assert TEMPLATE.render("") == "Fix:\n\nAnswer again."


def test_r20_the_template_hash_is_pinned() -> None:
    assert TEMPLATE.sha256() == "6cc71516bcc36ffa7d19848e6d971a45bac32575023fb8fef9128012d9146e93"
    assert TEMPLATE.sha256() == canonical_sha256(TEMPLATE.text)


def test_r20_the_template_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        TEMPLATE.text = "x"  # type: ignore[misc]
    assert not hasattr(TEMPLATE, "__dict__")


# ---------------------------------------------------------------- R46 the fixtures themselves, and no socket


def test_r46_chat_response_is_in_the_pinned_shape() -> None:
    response = chat_response(EMPTY, reasoning_content="r", prompt_tokens=7, completion_tokens=3, cached_tokens=2)
    body = json.loads(response.content)
    assert response.status_code == 200
    assert set(body) == {"choices", "created", "model", "system_fingerprint", "object", "usage", "id", "timings"}
    assert body["object"] == "chat.completion"
    assert body["model"] == "qwen3-4b"
    assert body["system_fingerprint"] == "b1-27b7a8a"
    assert body["choices"] == [
        {
            "finish_reason": "stop",
            "index": 0,
            "message": {"role": "assistant", "content": EMPTY, "reasoning_content": "r"},
        }
    ]
    assert body["usage"] == {
        "completion_tokens": 3,
        "prompt_tokens": 7,
        "total_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 2},
    }
    assert set(body["timings"]) == {
        "cache_n",
        "prompt_n",
        "prompt_ms",
        "prompt_per_token_ms",
        "prompt_per_second",
        "predicted_n",
        "predicted_ms",
        "predicted_per_token_ms",
        "predicted_per_second",
    }
    assert (body["timings"]["cache_n"], body["timings"]["prompt_n"], body["timings"]["predicted_n"]) == (2, 7, 3)


def test_r46_the_response_options() -> None:
    assert "reasoning_content" not in json.loads(chat_response(EMPTY).content)["choices"][0]["message"]
    assert "timings" not in json.loads(chat_response(EMPTY, timings=False).content)
    assert json.loads(chat_response(EMPTY, extra={"model": "other"}).content)["model"] == "other"
    assert json.loads(chat_response(EMPTY, finish_reason="length").content)["choices"][0]["finish_reason"] == "length"


def test_r46_the_error_and_count_responses() -> None:
    response = error_response(503, "unavailable_error", "Loading model")
    assert response.status_code == 503
    assert json.loads(response.content) == {
        "error": {"code": 503, "message": "Loading model", "type": "unavailable_error"}
    }
    count = input_tokens_response(812)
    assert count.status_code == 200
    assert json.loads(count.content) == {"input_tokens": 812, "object": "response.input_tokens"}


def scripted(*steps: Any) -> tuple[httpx.Client, ScriptedServer]:
    server = ScriptedServer(*steps)
    return httpx.Client(transport=httpx.MockTransport(server)), server


def test_r46_the_server_plays_every_kind_of_step_in_order() -> None:
    error = httpx.ReadError("instance")
    http, server = scripted(
        httpx.Response(201),
        lambda request: httpx.Response(202, content=request.url.path.encode()),
        httpx.ConnectError,
        error,
    )
    assert http.get(BASE_URL + PROPS_PATH).status_code == 201
    second = http.post(BASE_URL + CHAT_PATH, content=b"x")
    assert (second.status_code, second.content) == (202, CHAT_PATH.encode())
    with pytest.raises(httpx.ConnectError) as refused:
        http.get(BASE_URL + PROPS_PATH)
    assert str(refused.value) == "scripted"
    with pytest.raises(httpx.ReadError) as raised:
        http.get(BASE_URL + PROPS_PATH)
    assert raised.value is error
    assert [sent.method for sent in server.requests] == ["GET", "POST", "GET", "GET"]
    assert server.steps == []


def test_r46_the_server_refuses_one_call_too_many() -> None:
    http, server = scripted()
    with pytest.raises(AssertionError, match="more often than scripted"):
        http.get(BASE_URL + PROPS_PATH)
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8081/props",
        "http://localhost:8080/props",
        "http://127.0.0.2:8080/props",
        "https://127.0.0.1:8080/props",
        "http://127.0.0.1:8080/tokenize",
        "http://127.0.0.1:8080/props?x=1",
        "http://evil.example/",
    ],
)
def test_r46_the_server_refuses_any_other_url(url: str) -> None:
    http, _server = scripted(httpx.Response(200))
    with pytest.raises(AssertionError, match="unexpected URL"):
        http.get(url)


def test_r46_the_paths_are_the_three() -> None:
    assert PATHS == {CHAT_PATH, COUNT_PATH, PROPS_PATH}
    assert BASE_URL == "http://127.0.0.1:8080"


def test_r46_the_clock_gives_its_ticks_then_refuses() -> None:
    clock = FakeClock(5, 7)
    assert (clock(), clock()) == (5, 7)
    with pytest.raises(AssertionError, match="more often than scripted"):
        clock()


def test_r46_the_synthetic_constants() -> None:
    assert [(message.role, message.content) for message in MESSAGES] == [("system", "S"), ("user", "U")]
    assert sorted(SEGMENT_IDS) == ["seg_001", "seg_002", "seg_003", "seg_004", "seg_005", "seg_006"]
    assert sorted(BUNDLE) == ["src_1", "src_2", "src_3", "src_4", "src_5"]
    assert json.loads(EMPTY) == {"claims": [], "nonCheckable": []}


def test_r46_every_socket_entry_point_is_refused() -> None:
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(AssertionError):
        socket.getaddrinfo("localhost", 9)
    with socket.socket() as raw:
        with pytest.raises(AssertionError):
            raw.connect(("127.0.0.1", 9))
        with pytest.raises(AssertionError):
            raw.connect_ex(("127.0.0.1", 9))


def test_r46_every_h2_test_module_blocks_sockets() -> None:
    tests = Path(__file__).parent
    modules = [tests / "test_schemas.py", tests / "test_probe.py", *sorted(tests.glob("test_client*.py"))]
    assert len(modules) >= 5
    for module in modules:
        source = module.read_text(encoding="utf-8")
        assert "@pytest.fixture(autouse=True)\ndef _no_sockets" in source, module.name
        assert "    block_sockets(monkeypatch)\n" in source, module.name


def test_full_flow_with_sockets_disabled() -> None:
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9))

    invalid = '{"claims": [{"text": "x", "segmentIds": ["seg_9"]}], "nonCheckable": []}'
    error = "claims.0.segmentIds.0: 'seg_9' is not a segment id of this request"
    client, server = make_client(
        chat_response(invalid),
        input_tokens_response(900),
        chat_response(EMPTY),
        httpx.ReadTimeout,
        chat_response(PROBE_OK),
    )
    extraction = client.extract(SINGLE, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    assert extraction.outcome == "valid"
    assert extraction.output == Extraction("syn_a", (), ())
    assert [attempt.retry_type for attempt in extraction.attempts] == ["none", "repair"]

    probe = client.probe(PROBE, item_id="probe_01", bundle_source_ids=BUNDLE, repair_template=TEMPLATE)
    assert probe.outcome == "valid"
    assert probe.output == ProbeOutput("SUPPORTED", "r", ("src_1",))
    assert [attempt.retry_type for attempt in probe.attempts] == ["none", "same_request"]

    assert paths(server) == [CHAT_PATH, COUNT_PATH, CHAT_PATH, CHAT_PATH, CHAT_PATH]
    assert [sent.content for sent in server.requests] == [
        SINGLE.body_bytes(),
        repair_bytes(SINGLE, invalid, error),
        repair_bytes(SINGLE, invalid, error),
        PROBE.body_bytes(),
        PROBE.body_bytes(),
    ]
    for sent in server.requests:
        assert (sent.url.scheme, sent.url.host, sent.url.port) == ("http", "127.0.0.1", 8080)
        assert sent.url.path in PATHS

    ModelServerClient("http://127.0.0.1:8080").close()  # the default transport opens no socket
