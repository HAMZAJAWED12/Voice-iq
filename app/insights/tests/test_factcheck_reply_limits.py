"""Provider replies are read up to a size limit, and a failed subject is asked once per run.

Real provider replies are a few kilobytes. A reply whose decoded body grows
past `MAX_REPLY_BYTES` is not read further and gives SOURCE_UNAVAILABLE for
that claim. Within one run, a subject whose fetch failed is not fetched again.
All traffic goes through `httpx.MockTransport`.
"""

from __future__ import annotations

import gzip
import json

import httpx

from app.insights.core.factcheck.factcheck_engine import FactCheckEngine, SourceRouter
from app.insights.core.factcheck.source_clients.base_client import MAX_REPLY_BYTES
from app.insights.core.factcheck.source_clients.coingecko_client import CoinGeckoClient

CHUNK = 64 * 1024


def _engine(handler) -> FactCheckEngine:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return FactCheckEngine(router=SourceRouter({"CRYPTO_PRICE": CoinGeckoClient(client=http)}))


def _verdicts(engine: FactCheckEngine, transcript: str) -> list[tuple[str, str | None]]:
    response = engine.run(conversation_id="c", speaker_id="s", transcript_text=transcript)
    return [(r.verdict, r.reason) for r in response.fact_check_results]


def test_an_oversized_reply_is_not_read_to_the_end():
    sent = {"bytes": 0}
    head = b'{"bitcoin": {"usd": 95000}, "pad": "'

    def body():
        yield head
        sent["bytes"] += len(head)
        for _ in range((3 * MAX_REPLY_BYTES) // CHUNK):
            sent["bytes"] += CHUNK
            yield b"x" * CHUNK
        yield b'"}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers={"content-type": "application/json"})

    verdicts = _verdicts(_engine(handler), "BTC is at 95000.")

    assert [v for v, _ in verdicts] == ["SOURCE_UNAVAILABLE"]
    assert sent["bytes"] <= MAX_REPLY_BYTES + 2 * CHUNK


def test_the_limit_counts_decompressed_bytes():
    reply = json.dumps({"bitcoin": {"usd": 95000}, "pad": "x" * (2 * MAX_REPLY_BYTES)}).encode()
    compressed = gzip.compress(reply)
    assert len(compressed) < MAX_REPLY_BYTES // 10

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=compressed, headers={"content-encoding": "gzip"})

    assert [v for v, _ in _verdicts(_engine(handler), "BTC is at 95000.")] == ["SOURCE_UNAVAILABLE"]


def test_a_normal_reply_is_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"bitcoin": {"usd": 95000}})

    assert [v for v, _ in _verdicts(_engine(handler), "BTC is at 95000.")] == ["TRUE"]


def test_a_failed_subject_is_fetched_once_per_run():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    verdicts = _verdicts(_engine(handler), "BTC is at 95000. BTC is at 96000. BTC is at 97000.")

    assert [v for v, _ in verdicts] == ["SOURCE_UNAVAILABLE"] * 3
    assert len({reason for _, reason in verdicts}) == 1
    assert calls["n"] == 2  # one fetch: the first attempt and its single retry


def test_a_failed_subject_is_fetched_again_in_the_next_run():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503)

    engine = _engine(handler)
    _verdicts(engine, "BTC is at 95000.")
    _verdicts(engine, "BTC is at 95000.")

    assert calls["n"] == 4
