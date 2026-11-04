"""Test helpers and synthetic constants for the H2 tests (H2 spec R46; S2 §3.12). Data is synthetic, never gold rows.

No test sends through a real transport or starts a server: responses come from `httpx.MockTransport`, driven by
`ScriptedServer`, in the shape of the pinned llama-server. Each H2 test module wraps `block_sockets` in its own autouse
fixture.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import httpx
import pytest

from app.evaluation.bakeoff.client import (
    CHAT_PATH,
    COUNT_PATH,
    PROPS_PATH,
    CallRecord,
    ChatMessage,
    ChatRequest,
    ModelServerClient,
    RepairTemplate,
)
from app.evaluation.bakeoff.schemas import canonical_json

BASE_URL = "http://127.0.0.1:8080"
PATHS = frozenset({CHAT_PATH, COUNT_PATH, PROPS_PATH})

MESSAGES = (ChatMessage("system", "S"), ChatMessage("user", "U"))
SEGMENT_IDS = frozenset(f"seg_00{number}" for number in range(1, 7))
BUNDLE = frozenset(f"src_{number}" for number in range(1, 6))
TEMPLATE = RepairTemplate("Fix:\n<<VALIDATION_ERRORS>>\nAnswer again.")
EMPTY = '{"claims": [], "nonCheckable": []}'
PROBE_OK = '{"citations": [{"sourceId": "src_1"}], "reason": "r", "verdict": "SUPPORTED"}'
SINGLE = ChatRequest("single_conversation_extraction", MESSAGES)
CAP = ChatRequest("cap_session_extraction", MESSAGES)
PROBE = ChatRequest("reasoning_probe", MESSAGES)


def chat_response(
    content: str,
    *,
    finish_reason: str | None = "stop",
    reasoning_content: str | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 10,
    cached_tokens: int = 0,
    timings: bool = True,
    extra: dict[str, Any] | None = None,
) -> httpx.Response:
    """A non-streaming chat completion in the pinned server's shape. `extra` adds or replaces top-level keys."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    body: dict[str, Any] = {
        "choices": [{"finish_reason": finish_reason, "index": 0, "message": message}],
        "created": 1,
        "model": "qwen3-4b",
        "system_fingerprint": "b11936-27b7a8a0",
        "object": "chat.completion",
        "usage": {
            "completion_tokens": completion_tokens,
            "prompt_tokens": prompt_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": cached_tokens},
        },
        "id": "chatcmpl-1",
    }
    if timings:
        body["timings"] = {
            "cache_n": cached_tokens,
            "prompt_n": prompt_tokens,
            "prompt_ms": 1.5,
            "prompt_per_token_ms": 0.015,
            "prompt_per_second": 66666.7,
            "predicted_n": completion_tokens,
            "predicted_ms": 20.0,
            "predicted_per_token_ms": 2.0,
            "predicted_per_second": 500.0,
        }
    body.update(extra or {})
    return httpx.Response(200, content=json.dumps(body).encode("utf-8"))


def error_response(status: int, type_: str, message: str) -> httpx.Response:
    """The pinned server's error body, but for `exceed_context_size_error`, which also holds `n_prompt_tokens` and
    `n_ctx` inside `error` (`server-task.cpp`; the `e5-the-pinned-body` row builds that one by hand)."""
    body = {"error": {"code": status, "message": message, "type": type_}}
    return httpx.Response(status, content=json.dumps(body).encode("utf-8"))


def input_tokens_response(n: int) -> httpx.Response:
    """A `/v1/chat/completions/input_tokens` answer."""
    body = {"input_tokens": n, "object": "response.input_tokens"}
    return httpx.Response(200, content=json.dumps(body).encode("utf-8"))


class ScriptedServer:
    """One ordered FIFO of steps, the handler of an `httpx.MockTransport`.

    A step is an `httpx.Response`, a `callable(request) -> httpx.Response`, an `httpx.RequestError` subclass (raised as
    `cls("scripted", request=request)`) or an exception instance. Every request is recorded in `.requests`. A call past
    the end of the script, or for a URL other than the three paths on `BASE_URL`, is an `AssertionError`.
    """

    def __init__(self, *steps: Any) -> None:
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url not in {BASE_URL + path for path in PATHS}:
            raise AssertionError(f"unexpected URL {url}")
        if not self.steps:
            raise AssertionError(f"called more often than scripted: {request.method} {url}")
        step = self.steps.pop(0)
        if isinstance(step, type) and issubclass(step, httpx.RequestError):
            raise step("scripted", request=request)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, httpx.Response):
            return step
        return step(request)


class FakeClock:
    """A clock that gives the scripted ticks in order. Reading it once too often is an `AssertionError`."""

    def __init__(self, *ticks: int) -> None:
        self.ticks = list(ticks)

    def __call__(self) -> int:
        if not self.ticks:
            raise AssertionError("the clock was read more often than scripted")
        return self.ticks.pop(0)


def block_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any real connection attempt or name lookup fail the test."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an H2 test must not touch a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def valid_content(kind: str) -> str:
    """A valid output for a request of `kind`."""
    return PROBE_OK if kind == "reasoning_probe" else EMPTY


def make_client(*steps: Any, **kwargs: Any) -> tuple[ModelServerClient, ScriptedServer]:
    """A client whose only transport is a `ScriptedServer` playing `steps`."""
    server = ScriptedServer(*steps)
    return ModelServerClient(BASE_URL, transport=httpx.MockTransport(server), **kwargs), server


def run_extract(*steps: Any, request: ChatRequest = SINGLE, **kwargs: Any) -> tuple[CallRecord, ScriptedServer]:
    """One extraction call for the item `syn_a` with the ids `SEGMENT_IDS`."""
    client, server = make_client(*steps, **kwargs)
    call = client.extract(request, item_id="syn_a", request_segment_ids=SEGMENT_IDS, repair_template=TEMPLATE)
    return call, server


def run_probe(*steps: Any, request: ChatRequest = PROBE, **kwargs: Any) -> tuple[CallRecord, ScriptedServer]:
    """One probe call for the item `probe_01` against `BUNDLE`."""
    client, server = make_client(*steps, **kwargs)
    call = client.probe(request, item_id="probe_01", bundle_source_ids=BUNDLE, repair_template=TEMPLATE)
    return call, server


def run_call(request: ChatRequest, *steps: Any, **kwargs: Any) -> tuple[CallRecord, ScriptedServer]:
    """`request` through `extract` or `probe`, whichever its kind takes."""
    run = run_probe if request.kind == "reasoning_probe" else run_extract
    return run(*steps, request=request, **kwargs)


def paths(server: ScriptedServer) -> list[str]:
    """The request paths, in order."""
    return [request.url.path for request in server.requests]


def completions(server: ScriptedServer) -> list[httpx.Request]:
    """The completion requests, in order."""
    return [request for request in server.requests if request.url.path == CHAT_PATH]


def repair_bytes(request: ChatRequest, invalid_output: str, error_text: str) -> bytes:
    """The exact body of `request`'s repair under `TEMPLATE`, spelled out here and not through `repair_request`."""
    body = request.body()
    turns = [
        {"content": invalid_output, "role": "assistant"},
        {"content": f"Fix:\n{error_text}\nAnswer again.", "role": "user"},
    ]
    return canonical_json({**body, "messages": body["messages"] + turns})
