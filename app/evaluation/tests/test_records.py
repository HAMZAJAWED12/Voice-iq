"""The stored records: H3 spec D1-D15, section 4's record schemas and rows X7, X8, X9, X13, X26, X33, X34, X53
(S2 §3.12, §3.13, §9, §10; AMB-52).

Every `CallRecord` comes from the real client over `httpx.MockTransport`, so these are the records H2 really returns.
Paths under `tmp_path` are real; the OneDrive rows use an injected `realpath` over made-up paths.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, get_args

import httpx
import pytest

from app.evaluation.bakeoff import records as records_module
from app.evaluation.bakeoff.client import (
    CAP_SESSION_TIMEOUT_SEC,
    MAX_TOKENS,
    REQUEST_KINDS,
    AttemptRecord,
    CallRecord,
    ChatRequest,
    ContextCheck,
    Modalities,
    ThinkingCheck,
    Timings,
    Usage,
)
from app.evaluation.bakeoff.extraction import ExtractedClaim, Extraction, NonCheckableEntry
from app.evaluation.bakeoff.inputs import FreezeHashes
from app.evaluation.bakeoff.probe import ProbeOutput
from app.evaluation.bakeoff.records import (
    CALL_ROLES,
    CANDIDATE_IDS,
    CANDIDATE_SLUGS,
    CARD_SETTINGS_FIELDS,
    FAULT_KINDS,
    MAX_FAULT_DETAIL,
    OPERATOR_FIELDS,
    PHASES,
    RUNS,
    SCHEMA_VERSION,
    SECTION2_FIELDS,
    CallRole,
    FaultKind,
    FaultRecord,
    LaunchRecord,
    Phase,
    PreRunRecord,
    RunKey,
    RunManifest,
    RunRecord,
    RunStore,
    RunView,
    StartupLog,
    StoredCall,
    StoreError,
    TruncatedLine,
    VoidRecord,
    call_from_dict,
    call_to_dict,
    load_run,
    log_name,
    machine_log_name,
    parse_unit_name,
    read_log,
    record_json,
    record_loads,
    refuse_onedrive,
    report_numbers,
    write_report,
)
from app.evaluation.bakeoff.schemas import EXTRACTION_SCHEMA_SHA256, PROBE_SCHEMA_SHA256, canonical_json
from app.evaluation.tests._client_fixtures import (
    CAP,
    EMPTY,
    MESSAGES,
    PROBE_OK,
    block_sockets,
    chat_response,
    error_response,
    input_tokens_response,
    run_call,
    run_extract,
    run_probe,
)
from app.evaluation.tests._runner_fixtures import NOW, block_processes, fixed_now, frozen_inputs, no_fsync

SHA = "a" * 64
Q4B, Q35, Q8B = CANDIDATE_IDS
KEY = RunKey(Q4B, "timed_run", 1, 1)
PRE_RUN_KEY = RunKey(Q4B, "pre_run", None, 1)
SURROGATE = chr(0xD800)
ESCAPE = chr(92) + "ud800"
FULL_CLAIM = {
    "claimId": "c_1",
    "speakerId": "SPEAKER_00",
    "text": "x",
    "segmentIds": ["seg_001", "seg_002"],
}
FULL_ENTRY = {
    "claimId": "c_2",
    "speakerId": "SPEAKER_01",
    "text": "y",
    "reason": "r",
    "reasonKind": "opinion",
    "segmentIds": ["seg_003"],
}
RICH = json.dumps({"claims": [FULL_CLAIM], "nonCheckable": [FULL_ENTRY]})
BAD_PROBE = '{"citations": [{"sourceId": "src_9"}], "reason": "r", "verdict": "SUPPORTED"}'
STARTUP = StartupLog(4, 1, 12288, 18080, 37, 37, 2, 2, 2375.91, 1728.0, None, 89.01)
CHECK = ContextCheck("request", SHA, 812, 0, 1024, 1836, 12288, 11059, True, 1_000)


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


def valid_single() -> CallRecord:
    return run_extract(chat_response(EMPTY))[0]


def call_of(kind: str, *, constrained: bool = True) -> CallRecord:
    request = ChatRequest(kind, MESSAGES, constrained)  # type: ignore[arg-type]
    return run_call(request, chat_response(PROBE_OK if kind == "reasoning_probe" else EMPTY))[0]


def manifest_fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "gold_version": "v1.0",
        "gold_manifest": {"conversations.jsonl": SHA, "items.csv": SHA, "items.jsonl": SHA},
        "hashes": {
            "prompts_sha256": SHA,
            "repair_sha256": SHA,
            "probe_sha256": SHA,
            "cap_sessions_sha256": SHA,
            "cap_requests_sha256": SHA,
            "warmup_sha256": SHA,
            "requests": {"syn_a": SHA},
            "requests_unconstrained": {"syn_a": SHA},
        },
        "extraction_schema_sha256": EXTRACTION_SCHEMA_SHA256,
        "probe_schema_sha256": PROBE_SCHEMA_SHA256,
        "sampling": {"temperature": 0.0, "seed": 42, "top_k": 1, "repeat_penalty": 1.0},
        "max_tokens": dict(MAX_TOKENS),
        "timeout_sec": {kind: 30.0 for kind in REQUEST_KINDS} | {"cap_session_extraction": CAP_SESSION_TIMEOUT_SEC},
        "thinking_mode": "off: chat_template_kwargs.enable_thinking=false",
        "conditions": {
            "binary_path": "/opt/llama/llama-server",
            "context": 12288,
            "kv_type": "f16",
            "port": 18080,
            "ready_deadline_sec": 600.0,
        },
        "binary_sha256": SHA,
        "pinned_build_info": "b1-27b7a8a",
        "interfaces": ("lo",),
        "candidates": (
            {
                "candidate_id": Q4B,
                "gguf_path": "/models/candidate.gguf",
                "gguf_sha256": SHA,
                "source_revision": "rev",
                "quantization_commands": "llama-quantize Q4_K_M",
                "argv": ["/opt/llama/llama-server", "-m", "/models/candidate.gguf"],
            },
        ),
        "operator": dict.fromkeys(OPERATOR_FIELDS, "recorded"),
    }
    return {**fields, **overrides}


def manifest(**overrides: Any) -> RunManifest:
    return RunManifest(**manifest_fields(**overrides))


def new_store(tmp_path: Path, **kwargs: Any) -> RunStore:
    arguments: dict[str, Any] = {"now": fixed_now, "fsync": no_fsync, **kwargs}
    return RunStore.create(str(tmp_path), "run_1", manifest(), **arguments)


def attached(store: RunStore, **kwargs: Any) -> RunStore:
    arguments: dict[str, Any] = {"now": fixed_now, "fsync": no_fsync, **kwargs}
    return RunStore.attach(store.path, **arguments)


def append_warmup(store: RunStore, key: RunKey = KEY, call: CallRecord | None = None) -> StoredCall:
    return store.append_call(
        key, "warmup", session_id=None, session_index=None, position=None, call=call or valid_single()
    )


def append_single(store: RunStore, key: RunKey = KEY, position: int = 1) -> StoredCall:
    return store.append_call(key, "single", session_id=None, session_index=None, position=position, call=valid_single())


def finish(store: RunStore, key: RunKey = KEY, status: Any = "complete", calls: int = 0) -> None:
    store.append_run(RunRecord(store.stamp(), key, status, calls))


def launch_record(key: RunKey = KEY, **overrides: Any) -> LaunchRecord:
    fields: dict[str, Any] = {
        "stamp": NOW.isoformat(),
        "key": key,
        "mode": "timed",
        "argv": ("/opt/llama/llama-server", "-m", "/models/candidate.gguf"),
        "env_names": ("HOME", "PATH"),
        "log_name": log_name(key),
        "pid": 4242,
        "ready_sec": 16.25,
        "n_ctx": 12288,
        "total_slots": 1,
        "build_info": "b1-27b7a8a",
        "model_path": "/models/candidate.gguf",
        "chat_template_sha256": SHA,
        "template_parser": "auto",
        "modalities": Modalities(False, False, False),
        "startup": STARTUP,
        "exit_code": 0,
        "stopped_how": "terminated",
    }
    return LaunchRecord(**{**fields, **overrides})


def pre_run_record(**overrides: Any) -> PreRunRecord:
    fields: dict[str, Any] = {
        "stamp": NOW.isoformat(),
        "candidate": Q4B,
        "status": "ready",
        "gguf_sha256": SHA,
        "exit_code": None,
        "thinking": "off",
        "context_checks": (CHECK,),
        "blocked": (),
    }
    return PreRunRecord(**{**fields, **overrides})


def lines_of(store: RunStore, file_name: str) -> list[dict[str, Any]]:
    raw = Path(store.path, file_name).read_bytes()
    assert raw.isascii()
    return [json.loads(line) for line in raw.splitlines()]


def size_of(store: RunStore, file_name: str) -> int:
    return Path(store.path, file_name).stat().st_size


# ---------------------------------------------------------------- constants


def test_the_constants() -> None:
    assert (SCHEMA_VERSION, RUNS, MAX_FAULT_DETAIL) == (1, (1, 2, 3), 200)
    assert PHASES == get_args(Phase) == ("pre_run", "timed_run", "unconstrained", "contended")
    assert CALL_ROLES == get_args(CallRole) == ("warmup", "single", "cap_session", "probe")
    assert FAULT_KINDS == get_args(FaultKind)
    assert len(OPERATOR_FIELDS) == len(set(OPERATOR_FIELDS)) == 30
    assert CANDIDATE_IDS == ("Qwen/Qwen3-4B", "Qwen/Qwen3.5-4B", "Qwen/Qwen3-8B")
    assert dict(CANDIDATE_SLUGS) == {Q4B: "qwen3-4b", Q35: "qwen3.5-4b", Q8B: "qwen3-8b"}
    assert issubclass(StoreError, RuntimeError)


# ---------------------------------------------------------------- D1 record_json


def test_d1_the_spec_s_example() -> None:
    raw = record_json({"b": 1, "a": [True, None, 0.0, chr(0xE9), SURROGATE]})
    assert raw == ('{"a":[true,null,0.0,"' + chr(92) + 'u00e9","' + ESCAPE + '"],"b":1}').encode("ascii")


@pytest.mark.parametrize(
    ("value", "stored"),
    [
        pytest.param(chr(0x1F600), chr(92) + "ud83d" + chr(92) + "ude00", id="a-pair-from-an-emoji"),
        pytest.param("\n", chr(92) + "n", id="a-line-feed"),
        pytest.param(chr(0x2028), chr(92) + "u2028", id="u-2028"),
        pytest.param(SURROGATE, ESCAPE, id="a-lone-surrogate"),
    ],
)
def test_d1_a_character_is_stored_as_its_escape_and_reads_back(value: str, stored: str) -> None:
    raw = record_json(value)
    assert raw == f'"{stored}"'.encode("ascii")
    assert record_loads(raw) == value


@pytest.mark.parametrize(
    ("value", "error"),
    [
        pytest.param(float("nan"), ValueError, id="nan"),
        pytest.param(float("inf"), ValueError, id="infinity"),
        pytest.param({"a": [float("-inf")]}, ValueError, id="a-nested-infinity"),
        pytest.param({1: "x"}, TypeError, id="a-non-str-key"),
        pytest.param({"a": {1: "x"}}, TypeError, id="a-nested-non-str-key"),
        pytest.param({"a"}, TypeError, id="a-set"),
        pytest.param(b"x", TypeError, id="bytes"),
    ],
)
def test_d1_what_is_refused(value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        record_json(value)


def test_d1_one_record_is_one_ascii_line_with_no_spaces() -> None:
    raw = record_json({"key": "two\nlines", "other": [1, 2]})
    assert raw.isascii()
    assert b"\n" not in raw
    assert b" " not in raw


def test_d1_canonical_json_is_unchanged() -> None:
    with pytest.raises(UnicodeEncodeError):
        canonical_json(SURROGATE)
    assert canonical_json(chr(0xE9)) == '"é"'.encode()


# ---------------------------------------------------------------- D2 record_loads


@pytest.mark.parametrize(
    "value",
    [SURROGATE, chr(0xDE00) + chr(0xD83D), 1e-300, 12345678901234567890, 117.046, -0.0, [None, {"a": 1.5}]],
)
def test_d2_values_read_back_exactly(value: Any) -> None:
    assert record_loads(record_json(value)) == value


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(b'{"a":1,"a":2}', id="a-repeated-key"),
        pytest.param(b'{"a":NaN}', id="nan"),
        pytest.param(b'{"a":Infinity}', id="infinity"),
        pytest.param(b'{"a":', id="cut-short"),
        pytest.param(b"", id="empty"),
        pytest.param('"é"'.encode(), id="a-non-ascii-byte"),
    ],
)
def test_d2_what_is_refused(raw: bytes) -> None:
    with pytest.raises(StoreError):
        record_loads(raw)


# ---------------------------------------------------------------- D3 the call round trip


def surrogate_output() -> CallRecord:
    content = json.dumps({"claims": [{"text": SURROGATE, "segmentIds": ["seg_001"]}], "nonCheckable": []})
    return run_extract(chat_response(content))[0]


def repaired_rich() -> CallRecord:
    return run_extract(chat_response("x"), input_tokens_response(9), chat_response(RICH))[0]


def cut_off_twice() -> CallRecord:
    cut = {"finish_reason": "length"}
    return run_extract(chat_response(EMPTY, **cut), input_tokens_response(9), chat_response(EMPTY, **cut))[0]


OUTCOMES: dict[str, Callable[[], CallRecord]] = {
    "valid": valid_single,
    "invalid-then-valid": lambda: run_extract(chat_response("x"), input_tokens_response(9), chat_response(EMPTY))[0],
    "cut-off-twice": cut_off_twice,
    "timeout-twice": lambda: run_extract(httpx.ReadTimeout, httpx.ReadTimeout)[0],
    "server-error-then-invalid": lambda: run_extract(error_response(500, "server_error", "b"), chat_response("x"))[0],
    "probe-with-unknown-ids": lambda: run_probe(
        chat_response(BAD_PROBE), input_tokens_response(9), chat_response(BAD_PROBE)
    )[0],
    "probe-valid": lambda: run_probe(chat_response(PROBE_OK))[0],
    "escaped-surrogate": surrogate_output,
    "no-timings": lambda: run_extract(chat_response(EMPTY, timings=False))[0],
    "every-optional-field": repaired_rich,
    "reasoning-text": lambda: run_extract(chat_response(EMPTY, reasoning_content="Let me check"))[0],
    "cap-session": lambda: run_call(CAP, chat_response(EMPTY))[0],
    "unconstrained": lambda: call_of("single_conversation_extraction", constrained=False),
}


@pytest.mark.parametrize("make", OUTCOMES.values(), ids=OUTCOMES.keys())
def test_d3_a_call_reloads_into_the_same_typed_record(make: Callable[[], CallRecord]) -> None:
    call = make()
    assert call_to_dict(call) == dataclasses.asdict(call)
    assert call_from_dict(record_loads(record_json(call_to_dict(call)))) == call


def test_d3_every_type_comes_back() -> None:
    call = call_from_dict(record_loads(record_json(call_to_dict(repaired_rich()))))
    attempt = call.attempts[1]
    assert isinstance(call.attempts, tuple)
    assert isinstance(call.context_checks, tuple)
    assert type(call.context_checks[0]) is ContextCheck
    assert type(attempt) is AttemptRecord
    assert type(attempt.usage) is Usage
    assert type(attempt.timings) is Timings
    assert type(attempt.thinking) is ThinkingCheck
    assert isinstance(attempt.unknown_source_ids, tuple)
    output = call.output
    assert type(output) is Extraction
    assert type(output.claims[0]) is ExtractedClaim
    assert type(output.non_checkable[0]) is NonCheckableEntry
    assert output.claims[0].segment_ids == ("seg_001", "seg_002")
    assert output.claims[0].speaker_id == "SPEAKER_00"
    assert output.non_checkable[0].reason_kind == "opinion"
    assert output.non_checkable[0].reason == "r"


def test_d3_a_probe_output_comes_back_as_a_probe_output() -> None:
    call = call_from_dict(record_loads(record_json(call_to_dict(run_probe(chat_response(PROBE_OK))[0]))))
    assert call.output == ProbeOutput("SUPPORTED", "r", ("src_1",))
    assert call.attempts[0].cited_source_ids == ("src_1",)


def stored_form(call: CallRecord) -> dict[str, Any]:
    return json.loads(record_json(call_to_dict(call)))


def at(data: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        data = data[step]
    return data


LEVELS = [
    pytest.param((), id="call"),
    pytest.param(("attempts", 0), id="attempt"),
    pytest.param(("attempts", 1, "usage"), id="usage"),
    pytest.param(("attempts", 1, "timings"), id="timings"),
    pytest.param(("attempts", 1, "thinking"), id="thinking"),
    pytest.param(("context_checks", 0), id="context-check"),
    pytest.param(("output",), id="extraction"),
    pytest.param(("output", "claims", 0), id="claim"),
    pytest.param(("output", "non_checkable", 0), id="non-checkable"),
]


@pytest.mark.parametrize("path", LEVELS)
def test_d3_a_missing_key_at_any_level_is_refused(path: tuple[Any, ...]) -> None:
    data = stored_form(repaired_rich())
    target = at(data, path)
    del target[sorted(target)[0]]
    with pytest.raises(StoreError):
        call_from_dict(data)


@pytest.mark.parametrize("path", LEVELS)
def test_d3_an_extra_key_at_any_level_is_refused(path: tuple[Any, ...]) -> None:
    data = stored_form(repaired_rich())
    at(data, path)["extra"] = 1
    with pytest.raises(StoreError):
        call_from_dict(data)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_d3_a_probe_output_with_other_keys_is_refused(change: str) -> None:
    data = stored_form(run_probe(chat_response(PROBE_OK))[0])
    if change == "missing":
        del data["output"]["reason"]
    else:
        data["output"]["confidence"] = 0.9
    with pytest.raises(StoreError):
        call_from_dict(data)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        pytest.param(("attempts", 0, "attempt"), 3, id="attempt-number"),
        pytest.param(("attempts", 0, "attempt"), True, id="attempt-a-bool"),
        pytest.param(("attempts", 0, "wall_ns"), -1, id="negative-wall-time"),
        pytest.param(("attempts", 0, "timeout_sec"), 31.0, id="another-timeout"),
        pytest.param(("attempts", 0, "request_sha256"), "x", id="a-bad-hash"),
        pytest.param(("attempts", 0, "failure_kind"), None, id="a-failure-erased"),
        pytest.param(("attempts", 1, "usage", "prompt_tokens"), "100", id="a-count-as-text"),
        pytest.param(("attempts", 1, "timings", "prompt_ms"), "1.5", id="a-time-as-text"),
        pytest.param(("attempts", 1, "thinking", "in_content"), 0, id="a-flag-as-a-number"),
        pytest.param(("attempts",), {}, id="attempts-not-an-array"),
        pytest.param(("attempts", 1, "unknown_source_ids"), 1, id="ids-not-an-array"),
        pytest.param(("total_wall_ns",), 1, id="a-total-that-does-not-add-up"),
        pytest.param(("outcome",), "cut_off", id="another-outcome"),
        pytest.param(("context_checks", 0, "uses"), 1, id="a-count-that-does-not-add-up"),
        pytest.param(("context_checks", 0, "fits"), False, id="a-check-that-no-longer-fits"),
        pytest.param(("output",), None, id="the-output-erased"),
        pytest.param(("output", "source_id"), "syn_b", id="another-item-s-output"),
        pytest.param(("output", "claims"), {}, id="claims-not-an-array"),
        pytest.param(("output", "claims", 0, "index"), 5, id="a-claim-out-of-place"),
        pytest.param(("output", "claims", 0, "segment_ids"), "seg_001", id="segment-ids-not-an-array"),
        pytest.param(("output", "non_checkable", 0, "reason_kind"), "other", id="an-unknown-reason-kind"),
        pytest.param(("item_id",), "", id="an-empty-item-id"),
    ],
)
def test_d3_a_tampered_value_is_refused(path: tuple[Any, ...], value: Any) -> None:
    data = stored_form(repaired_rich())
    at(data, path[:-1])[path[-1]] = value
    with pytest.raises(StoreError):
        call_from_dict(data)


@pytest.mark.parametrize("data", [[], None, "x", 1])
def test_d3_a_call_must_be_an_object(data: Any) -> None:
    with pytest.raises(StoreError):
        call_from_dict(data)


def test_d3_an_output_of_the_wrong_shape_for_the_kind_is_refused() -> None:
    data = stored_form(valid_single())
    data["output"] = {"verdict": "SUPPORTED", "reason": "r", "cited_source_ids": []}
    with pytest.raises(StoreError):
        call_from_dict(data)


def test_d3_the_stored_line_reloads_into_the_same_stored_call(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    stored = append_warmup(store, call=repaired_rich())
    assert load_run(store.path).calls == (stored,)


# ---------------------------------------------------------------- X9 and X34 surrogates


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"text": SURROGATE}, id="claim-text"),
        pytest.param({"claimId": SURROGATE}, id="claim-id"),
        pytest.param({"speakerId": SURROGATE}, id="speaker-id"),
    ],
)
def test_x9_a_lone_surrogate_in_a_stored_output_keeps_its_value(tmp_path: Path, fields: dict[str, str]) -> None:
    content = json.dumps({"claims": [{"text": "x", "segmentIds": ["seg_001"], **fields}], "nonCheckable": []})
    call = run_extract(chat_response(content))[0]
    store = new_store(tmp_path)
    append_warmup(store, call=call)
    raw = Path(store.path, "calls.jsonl").read_bytes()
    assert raw.isascii()
    assert ESCAPE.encode("ascii") in raw
    reloaded = load_run(store.path).calls[0].call
    assert reloaded == call
    assert reloaded.outcome == "valid"


def test_x9_a_surrogate_in_non_checkable_fields_and_a_probe_reason_keeps_its_value(tmp_path: Path) -> None:
    entry = {"reasonKind": "opinion", "segmentIds": ["seg_001"], "text": SURROGATE, "reason": SURROGATE}
    extraction = run_extract(chat_response(json.dumps({"claims": [], "nonCheckable": [entry]})))[0]
    probe_text = json.dumps({"citations": [{"sourceId": "src_1"}], "reason": SURROGATE, "verdict": "SUPPORTED"})
    probe = run_probe(chat_response(probe_text))[0]
    store = new_store(tmp_path)
    append_warmup(store, call=extraction)
    store.append_call(KEY, "probe", session_id="cap_1", session_index=1, position=1, call=probe)
    assert [stored.call for stored in load_run(store.path).calls] == [extraction, probe]


def test_x34_an_escaped_pair_in_model_output_becomes_one_character_and_stores(tmp_path: Path) -> None:
    pair = chr(92) + "ud83d" + chr(92) + "ude00"
    content = '{"claims": [{"text": "' + pair + '", "segmentIds": ["seg_001"]}], "nonCheckable": []}'
    call = run_extract(chat_response(content))[0]
    assert isinstance(call.output, Extraction)
    assert call.output.claims[0].text == chr(0x1F600)
    store = new_store(tmp_path)
    append_warmup(store, call=call)
    assert load_run(store.path).calls[0].call == call


def test_x34_two_adjacent_surrogates_built_by_hand_are_refused_and_nothing_is_written(tmp_path: Path) -> None:
    claim = ExtractedClaim(0, chr(0xD83D) + chr(0xDE00), ("seg_001",))
    call = dataclasses.replace(valid_single(), output=Extraction("syn_a", (claim,), ()))
    store = new_store(tmp_path)
    with pytest.raises(StoreError):
        append_warmup(store, call=call)
    assert size_of(store, "calls.jsonl") == 0
    assert append_warmup(store).sequence == 1


# ---------------------------------------------------------------- D4 exclusive creation


def test_d4_a_new_store_holds_the_layout(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    root = Path(store.path)
    assert root.name == "run_1"
    records = ["pre_run.jsonl", "launches.jsonl", "calls.jsonl", "runs.jsonl", "faults.jsonl", "voids.jsonl"]
    assert sorted(entry.name for entry in root.iterdir()) == sorted(["logs", "scored", "manifest.json", *records])
    assert (root / "logs").is_dir()
    assert (root / "scored").is_dir()
    assert [size_of(store, name) for name in records] == [0] * 6
    assert (root / "manifest.json").read_bytes().count(b"\n") == 1


@pytest.mark.parametrize("name", ["../x", "a/b", "a" + chr(92) + "b", "", ".hidden", "-x", "a b", "a\n", "é"])
def test_d4_a_bad_run_name_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(StoreError):
        RunStore.create(str(tmp_path), name, manifest(), now=fixed_now, fsync=no_fsync)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("name", ["a", "run_1", "2026-11-07.first", "A1"])
def test_d4_a_good_run_name_is_accepted(tmp_path: Path, name: str) -> None:
    assert Path(RunStore.create(str(tmp_path), name, manifest(), now=fixed_now, fsync=no_fsync).path).is_dir()


@pytest.mark.parametrize("keep", [False, True], ids=["empty", "not-empty"])
def test_x7_an_existing_run_directory_is_refused_and_left_alone(tmp_path: Path, keep: bool) -> None:
    directory = tmp_path / "run_1"
    directory.mkdir()
    if keep:
        (directory / "keep.txt").write_bytes(b"keep")
    with pytest.raises(StoreError):
        new_store(tmp_path)
    expected = ["keep.txt"] if keep else []
    assert [entry.name for entry in directory.iterdir()] == expected
    if keep:
        assert (directory / "keep.txt").read_bytes() == b"keep"


def test_d4_a_store_is_never_created_twice(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store)
    with pytest.raises(StoreError):
        new_store(tmp_path)
    assert size_of(store, "calls.jsonl") > 0


def test_d4_the_parent_must_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        RunStore.create(str(tmp_path / "missing"), "run_1", manifest(), now=fixed_now, fsync=no_fsync)


def test_d4_there_is_no_default_parent() -> None:
    parameters = inspect.signature(RunStore.create).parameters
    assert parameters["parent"].default is inspect.Parameter.empty
    assert parameters["name"].default is inspect.Parameter.empty
    assert parameters["now"].default is inspect.Parameter.empty


def test_d4_a_value_that_cannot_be_stored_leaves_no_directory(tmp_path: Path) -> None:
    bad = manifest(interfaces=({"lo"},))
    with pytest.raises(TypeError):
        RunStore.create(str(tmp_path), "run_1", bad, now=fixed_now, fsync=no_fsync)
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------- D5 outside OneDrive, X13


def as_given(path: str) -> str:
    return path


@pytest.mark.parametrize(
    "path",
    [
        "/mnt/c/data/OneDrive/r1",
        "/mnt/c/data/ONEDRIVE/r1",
        "/mnt/c/data/onedrive/r1",
        "/mnt/c/data/OneDrive - Contoso/r1",
        "/mnt/c/data/ONEDRI~1/r1",
        "/mnt/c/data/onedri~2/r1",
        chr(92).join(("C:", "data", "OneDrive", "r1")),
        chr(92).join(("C:", "data", "ONEDRI~1", "r1")),
    ],
)
def test_x13_a_path_under_onedrive_is_refused(path: str) -> None:
    with pytest.raises(StoreError, match="S2 §3.12"):
        refuse_onedrive(path, realpath=as_given)


@pytest.mark.parametrize("path", ["/data/link/r1", "results/r1"])
def test_x13_the_check_is_on_the_resolved_path(path: str) -> None:
    with pytest.raises(StoreError):
        refuse_onedrive(path, realpath=lambda _path: "/mnt/c/data/OneDrive/r1")


@pytest.mark.parametrize("path", ["/data/results/r1", "/data/myonedrive/r1", "/data/one drive/r1"])
def test_x13_other_paths_are_allowed_and_returned_resolved(path: str) -> None:
    assert refuse_onedrive(path, realpath=as_given) == path
    assert refuse_onedrive("anything", realpath=lambda _path: path) == path


def test_x13_a_refusal_leaves_no_directory(tmp_path: Path) -> None:
    parent = tmp_path / "OneDrive"
    parent.mkdir()
    with pytest.raises(StoreError):
        RunStore.create(str(parent), "run_1", manifest(), now=fixed_now, fsync=no_fsync)
    assert list(parent.iterdir()) == []


def test_x13_create_resolves_through_the_injected_realpath(tmp_path: Path) -> None:
    def to_onedrive(_path: str) -> str:
        return "/mnt/c/data/OneDrive/r1"

    with pytest.raises(StoreError):
        RunStore.create(str(tmp_path), "run_1", manifest(), now=fixed_now, fsync=no_fsync, realpath=to_onedrive)
    assert list(tmp_path.iterdir()) == []


def test_x13_attach_runs_the_same_check(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    with pytest.raises(StoreError, match="S2 §3.12"):
        attached(store, realpath=lambda _path: "/mnt/c/data/OneDrive/r1")


def test_d5_nothing_allows_a_refused_path() -> None:
    assert set(inspect.signature(refuse_onedrive).parameters) == {"path", "realpath"}
    assert not {"sync", "override", "force"} & set(inspect.signature(RunStore.create).parameters)


# ---------------------------------------------------------------- D6 appends


def test_d6_every_append_is_synced_once(tmp_path: Path) -> None:
    synced: list[int] = []
    store = new_store(tmp_path, fsync=synced.append)
    assert len(synced) == 1  # the manifest
    appends: list[Callable[[], object]] = [
        lambda: append_warmup(store),
        lambda: store.append_launch(launch_record()),
        lambda: store.append_pre_run(pre_run_record()),
        lambda: finish(store),
        lambda: store.log_fault(KEY, "operator", item_id=None, detail="x"),
        lambda: store.void_run(KEY, 1),
    ]
    for count, append in enumerate(appends, start=2):
        append()
        assert len(synced) == count


def test_d6_a_record_is_on_disk_when_the_append_returns(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    stored = append_warmup(store)
    assert load_run(store.path).calls == (stored,)


def test_d6_a_line_ends_in_one_line_feed_with_no_carriage_return(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store)
    raw = Path(store.path, "calls.jsonl").read_bytes()
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    assert b"\r" not in raw


def test_d6_sequence_numbers_rise_by_one_from_one(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert [append_single(store, position=number).sequence for number in (1, 2, 3)] == [1, 2, 3]


def test_d6_a_refused_call_takes_no_sequence_number(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    with pytest.raises(ValueError):
        store.append_call(KEY, "single", session_id=None, session_index=None, position=0, call=valid_single())
    assert append_single(store).sequence == 1


def test_d6_the_stamp_is_the_clock_s_time_in_iso_form(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert store.stamp() == "2026-11-07T20:15:00+00:00"
    assert append_warmup(store).stamp == "2026-11-07T20:15:00+00:00"


def test_d6_an_aware_clock_in_another_zone_keeps_its_offset(tmp_path: Path) -> None:
    east = datetime(2026, 11, 7, 20, 15, tzinfo=timezone(timedelta(hours=5)))
    assert new_store(tmp_path, now=lambda: east).stamp() == "2026-11-07T20:15:00+05:00"


def test_d6_a_naive_clock_is_refused_and_leaves_no_directory(tmp_path: Path) -> None:
    def naive() -> datetime:
        return datetime(2026, 11, 7, 20, 15)

    with pytest.raises(StoreError):
        new_store(tmp_path, now=naive)
    assert list(tmp_path.iterdir()) == []


def test_d6_a_store_has_no_close_and_keeps_no_handle(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert not hasattr(store, "close")
    append_warmup(store)
    assert not [value for value in vars(store).values() if hasattr(value, "fileno")]


def test_x33_a_clock_going_backwards_is_stored_as_given_and_is_no_fault(tmp_path: Path) -> None:
    moments = iter((NOW, NOW, NOW - timedelta(hours=1)))
    store = new_store(tmp_path, now=lambda: next(moments))
    first = append_warmup(store)
    second = append_single(store)
    assert second.stamp < first.stamp
    view = load_run(store.path)
    assert view.calls == (first, second)  # in sequence order, `total_wall_ns` untouched
    assert view.faults == ()


# ---------------------------------------------------------------- D7 the reader


def as_read(value: Any) -> Any:
    return value


def write_file(path: Path, *values: Any, tail: bytes = b"") -> str:
    path.write_bytes(b"".join(record_json(value) + b"\n" for value in values) + tail)
    return str(path)


def test_d7_a_truncated_last_line_is_reported(tmp_path: Path) -> None:
    path = write_file(tmp_path / "calls.jsonl", {"sequence": 1}, {"sequence": 2}, tail=b'{"sch')
    records, tail = read_log(path, as_read)
    assert records == ({"sequence": 1}, {"sequence": 2})
    assert tail == TruncatedLine("calls.jsonl", 3, 5)


def test_d7_a_complete_record_without_its_line_feed_is_reported_not_parsed(tmp_path: Path) -> None:
    path = write_file(tmp_path / "runs.jsonl", {"a": 1}, tail=record_json({"a": 2}))
    records, tail = read_log(path, as_read)
    assert records == ({"a": 1},)
    assert tail == TruncatedLine("runs.jsonl", 2, len(record_json({"a": 2})))


def test_d7_an_empty_file_gives_no_records(tmp_path: Path) -> None:
    assert read_log(write_file(tmp_path / "runs.jsonl"), as_read) == ((), None)


def test_d7_a_clean_file_reports_no_truncation(tmp_path: Path) -> None:
    assert read_log(write_file(tmp_path / "runs.jsonl", {"a": 1}), as_read) == (({"a": 1},), None)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"", id="empty"),
        pytest.param('"é"'.encode(), id="non-ascii"),
    ],
)
def test_d7_a_bad_middle_line_raises_naming_the_file_and_line(tmp_path: Path, bad: bytes) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_bytes(record_json({"a": 1}) + b"\n" + bad + b"\n" + record_json({"a": 3}) + b"\n")
    with pytest.raises(StoreError, match="runs.jsonl line 2"):
        read_log(str(path), as_read)


@pytest.mark.parametrize("error", [ValueError("x"), TypeError("x"), KeyError("x"), StoreError("x")])
def test_d7_a_line_that_does_not_decode_raises_naming_the_file_and_line(tmp_path: Path, error: Exception) -> None:
    def decode(_value: Any) -> None:
        raise error

    with pytest.raises(StoreError, match="runs.jsonl line 1"):
        read_log(write_file(tmp_path / "runs.jsonl", {"a": 1}), decode)


@pytest.mark.parametrize("sequence", [(1, 2, 4), (2,), (1, 1), (0, 1), (2, 1)])
def test_d7_a_broken_sequence_raises(tmp_path: Path, sequence: tuple[int, ...]) -> None:
    path = write_file(tmp_path / "calls.jsonl", *({"sequence": number} for number in sequence))
    with pytest.raises(StoreError, match="calls.jsonl line"):
        read_log(path, as_read)


def test_d7_a_calls_line_with_no_sequence_raises(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="calls.jsonl line 1"):
        read_log(write_file(tmp_path / "calls.jsonl", [1]), as_read)


def test_d7_other_logs_have_no_sequence_rule(tmp_path: Path) -> None:
    records, _tail = read_log(write_file(tmp_path / "faults.jsonl", {"sequence": 9}), as_read)
    assert records == ({"sequence": 9},)


def test_d7_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="runs.jsonl"):
        read_log(str(tmp_path / "runs.jsonl"), as_read)


def test_d7_a_cut_at_every_byte_offset_loses_only_the_line_in_progress(tmp_path: Path) -> None:
    lines = [record_json({"a": number, "text": "x" * number}) + b"\n" for number in (1, 2, 3)]
    raw = b"".join(lines)
    path = tmp_path / "runs.jsonl"
    for offset in range(len(raw) + 1):
        path.write_bytes(raw[:offset])
        complete = raw[:offset].count(b"\n")
        records, tail = read_log(str(path), as_read)
        assert records == tuple(record_loads(line) for line in lines[:complete])
        rest = offset - sum(len(line) for line in lines[:complete])
        assert tail == (TruncatedLine("runs.jsonl", complete + 1, rest) if rest else None)


def test_x8_a_crash_leaves_every_earlier_call_and_reports_the_tail(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    earlier = (append_warmup(store), append_single(store))
    with Path(store.path, "calls.jsonl").open("ab") as file:
        file.write(b"x" * 40)
    view = load_run(store.path)
    assert view.calls == earlier
    assert view.truncated == (TruncatedLine("calls.jsonl", 3, 40),)
    with pytest.raises(StoreError):
        view.counted_attempt(Q4B, 1)
    operator = attached(store)
    fault = operator.log_fault(KEY, "operator", item_id=None, detail="host rebooted")
    assert operator.void_run(KEY, fault.fault_id).fault_id == 1


def test_d7_load_run_collects_every_truncated_tail(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store)
    finish(store, calls=1)
    for name in ("calls.jsonl", "runs.jsonl"):
        with Path(store.path, name).open("ab") as file:
            file.write(b"{x")
    view = load_run(store.path)
    assert view.truncated == (TruncatedLine("calls.jsonl", 2, 2), TruncatedLine("runs.jsonl", 2, 2))
    assert len(view.calls) == len(view.runs) == 1


# ---------------------------------------------------------------- D8 stored calls, section 4's tag table

SINGLE_KIND, CAP_KIND, PROBE_KIND = REQUEST_KINDS
TAG_ROWS = [
    ("pre_run", None, "warmup", None, None, None, True, SINGLE_KIND),
    ("timed_run", 1, "warmup", None, None, None, True, SINGLE_KIND),
    ("timed_run", 2, "single", None, None, 63, True, SINGLE_KIND),
    ("timed_run", 3, "cap_session", "syn_a", 1, None, True, CAP_KIND),
    ("timed_run", 3, "cap_session", "syn_a", 4, None, True, CAP_KIND),
    ("timed_run", 1, "probe", "cap_1", 1, 1, True, PROBE_KIND),
    ("timed_run", 1, "probe", "cap_4", 4, 20, True, PROBE_KIND),
    ("unconstrained", None, "warmup", None, None, None, False, SINGLE_KIND),
    ("unconstrained", None, "single", None, None, 1, False, SINGLE_KIND),
    ("unconstrained", None, "cap_session", "syn_a", 4, None, False, CAP_KIND),
    ("unconstrained", None, "probe", None, None, 20, False, PROBE_KIND),
    ("contended", None, "warmup", None, None, None, True, SINGLE_KIND),
    ("contended", None, "cap_session", "syn_a", 1, None, True, CAP_KIND),
    ("contended", None, "probe", "cap_1", 1, 20, True, PROBE_KIND),
]


def stored_call(row: tuple[Any, ...], *, sequence: int = 1) -> StoredCall:
    phase, run, role, session_id, session_index, position, constrained, kind = row
    call = call_of(kind, constrained=constrained)
    return StoredCall(
        sequence, NOW.isoformat(), RunKey(Q4B, phase, run, 1), role, session_id, session_index, position, call
    )


@pytest.mark.parametrize("row", TAG_ROWS, ids=lambda row: f"{row[0]}-{row[2]}")
def test_d8_every_row_of_the_table_is_accepted_and_stored(tmp_path: Path, row: tuple[Any, ...]) -> None:
    expected = stored_call(row)
    store = new_store(tmp_path)
    stored = store.append_call(
        expected.key,
        expected.role,
        session_id=expected.session_id,
        session_index=expected.session_index,
        position=expected.position,
        call=expected.call,
    )
    assert stored == expected
    assert load_run(store.path).calls == (expected,)


def with_tags(row_index: int, **changes: Any) -> tuple[Any, ...]:
    names = ("phase", "run", "role", "session_id", "session_index", "position", "constrained", "kind")
    return tuple(changes.get(name, value) for name, value in zip(names, TAG_ROWS[row_index], strict=True))


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(with_tags(0, role="single", position=1), id="pre-run-single"),
        pytest.param(with_tags(0, position=1), id="a-warm-up-with-a-position"),
        pytest.param(with_tags(1, session_id="cap_1", session_index=1), id="a-warm-up-in-a-session"),
        pytest.param(with_tags(2, position=None), id="a-single-without-a-position"),
        pytest.param(with_tags(2, position=0), id="a-single-at-position-0"),
        pytest.param(with_tags(2, position=True), id="a-single-at-position-true"),
        pytest.param(with_tags(2, session_id="cap_1", session_index=1), id="a-single-in-a-session"),
        pytest.param(with_tags(3, session_index=None), id="a-cap-call-without-an-index"),
        pytest.param(with_tags(3, session_id=None), id="a-cap-call-without-a-session"),
        pytest.param(with_tags(3, position=1), id="a-cap-call-with-a-position"),
        pytest.param(with_tags(3, session_index=5), id="a-cap-call-in-session-5"),
        pytest.param(with_tags(3, session_id="cap_1"), id="a-cap-call-under-another-id"),
        pytest.param(with_tags(3, kind=SINGLE_KIND), id="a-cap-role-holding-a-single-call"),
        pytest.param(with_tags(5, session_index=5), id="a-probe-in-session-5"),
        pytest.param(with_tags(5, session_index=0), id="a-probe-in-session-0"),
        pytest.param(with_tags(5, position=21), id="a-probe-at-position-21"),
        pytest.param(with_tags(5, position=None), id="a-probe-without-a-position"),
        pytest.param(with_tags(5, session_id=""), id="a-probe-with-an-empty-session-id"),
        pytest.param(with_tags(5, session_id=None), id="a-probe-without-a-session"),
        pytest.param(with_tags(5, kind=SINGLE_KIND), id="a-probe-role-holding-a-single-call"),
        pytest.param(with_tags(5, constrained=False), id="a-timed-unconstrained-probe"),
        pytest.param(with_tags(8, constrained=True), id="a-constrained-call-on-the-unconstrained-pass"),
        pytest.param(with_tags(10, session_id="cap_1", session_index=1), id="an-unconstrained-probe-in-a-session"),
        pytest.param(with_tags(11, role="single", position=1), id="a-contended-single"),
        pytest.param(with_tags(12, session_index=2), id="a-contended-second-session"),
        pytest.param(with_tags(13, session_index=2), id="a-contended-probe-in-a-second-session"),
        pytest.param(with_tags(1, role="other"), id="an-unknown-role"),
    ],
)
def test_d8_anything_else_is_refused(row: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        stored_call(row)


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(with_tags(0, session_id="cap_1"), id="a-pre-run-warm-up"),
        pytest.param(with_tags(1, session_id="cap_1"), id="a-timed-warm-up"),
        pytest.param(with_tags(2, session_id="cap_1"), id="a-single"),
        pytest.param(with_tags(10, session_id="cap_1"), id="an-unconstrained-probe"),
        pytest.param(with_tags(11, session_id="cap_1"), id="a-contended-warm-up"),
    ],
)
def test_d8_a_session_id_alone_is_refused_where_the_table_has_no_session(row: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError, match="StoredCall.session_id"):
        stored_call(row)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        pytest.param(
            with_tags(3, kind=SINGLE_KIND),
            f"StoredCall: role cap_session on phase timed_run needs {(CAP_KIND, True)}, not {(SINGLE_KIND, True)}",
            id="a-kind-swap",
        ),
        pytest.param(
            with_tags(5, constrained=False),
            f"StoredCall: role probe on phase timed_run needs {(PROBE_KIND, True)}, not {(PROBE_KIND, False)}",
            id="a-constrained-swap",
        ),
    ],
)
def test_d8_a_wrong_call_names_the_kind_and_constraint_needed_and_found(row: tuple[Any, ...], message: str) -> None:
    with pytest.raises(ValueError) as caught:
        stored_call(row)
    assert str(caught.value) == message


@pytest.mark.parametrize("sequence", [0, -1, True, "1", 1.0])
def test_d8_a_sequence_is_an_int_from_one(sequence: Any) -> None:
    with pytest.raises(ValueError):
        stored_call(TAG_ROWS[1], sequence=sequence)


def test_d8_the_call_and_key_are_typed() -> None:
    good = stored_call(TAG_ROWS[1])
    with pytest.raises(ValueError):
        dataclasses.replace(good, call="x")
    with pytest.raises(ValueError):
        dataclasses.replace(good, key="x")


def test_d8_two_sessions_records_of_one_item_differ_only_by_their_session_tags(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    call = call_of(PROBE_KIND)
    first = store.append_call(KEY, "probe", session_id="cap_1", session_index=1, position=7, call=call)
    second = store.append_call(KEY, "probe", session_id="cap_2", session_index=2, position=7, call=call)
    assert first.call == second.call
    same = {"sequence": 2, "session_id": "cap_2", "session_index": 2}
    assert dataclasses.replace(first, **same) == second


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("Qwen/Qwen3-4B-Base", "timed_run", 1, 1), id="a-candidate-outside-the-field"),
        pytest.param((Q4B, "other", None, 1), id="an-unknown-phase"),
        pytest.param((Q4B, "timed_run", None, 1), id="a-timed-run-without-a-run"),
        pytest.param((Q4B, "timed_run", 4, 1), id="run-4"),
        pytest.param((Q4B, "timed_run", 0, 1), id="run-0"),
        pytest.param((Q4B, "timed_run", True, 1), id="run-true"),
        pytest.param((Q4B, "timed_run", 1.0, 1), id="a-float-run"),
        pytest.param((Q4B, "unconstrained", 1, 1), id="an-unconstrained-pass-with-a-run"),
        pytest.param((Q4B, "contended", 1, 1), id="a-contended-run-with-a-run"),
        pytest.param((Q4B, "pre_run", 1, 1), id="a-pre-run-with-a-run"),
        pytest.param((Q4B, "timed_run", 1, 0), id="attempt-0"),
        pytest.param((Q4B, "timed_run", 1, True), id="attempt-true"),
        pytest.param((Q4B, "pre_run", None, 2), id="a-second-pre-run"),
    ],
)
def test_section_4_a_bad_run_key_is_refused(fields: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        RunKey(*fields)


@pytest.mark.parametrize(
    ("key", "name"),
    [
        (RunKey(Q35, "timed_run", 2, 1), "logs/qwen3.5-4b.run2.a1.log"),
        (RunKey(Q8B, "timed_run", 3, 2), "logs/qwen3-8b.run3.a2.log"),
        (RunKey(Q4B, "pre_run", None, 1), "logs/qwen3-4b.pre_run.a1.log"),
        (RunKey(Q4B, "unconstrained", None, 3), "logs/qwen3-4b.unconstrained.a3.log"),
        (RunKey(Q8B, "contended", None, 1), "logs/qwen3-8b.contended.a1.log"),
    ],
)
def test_section_4_log_names(tmp_path: Path, key: RunKey, name: str) -> None:
    assert log_name(key) == name
    store = new_store(tmp_path)
    assert Path(store.server_log_path(key)) == Path(store.path) / name


# ---------------------------------------------------------------- D9 the manifest


def test_d9_the_manifest_line_holds_section_4_s_keys(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    (line,) = lines_of(store, "manifest.json")
    assert set(line) == {
        "schema",
        "record",
        "stamp",
        "gold_version",
        "gold_manifest",
        "hashes",
        "extraction_schema_sha256",
        "probe_schema_sha256",
        "sampling",
        "max_tokens",
        "timeout_sec",
        "thinking_mode",
        "conditions",
        "binary_sha256",
        "pinned_build_info",
        "interfaces",
        "candidates",
        "operator",
    }
    assert (line["schema"], line["record"], line["stamp"]) == (1, "manifest", "2026-11-07T20:15:00+00:00")
    assert line["extraction_schema_sha256"] == EXTRACTION_SCHEMA_SHA256
    assert load_run(store.path).manifest == line


def test_d9_the_operator_fields_are_the_spec_s() -> None:
    assert OPERATOR_FIELDS == (
        "machine",
        "wsl_kernel",
        "cuda_version",
        "wsl_networking_mode",
        "wsl_localhost_forwarding",
        "wslconfig_memory",
        "wslconfig_swap",
        "gpu_driver",
        "windows_power_mode",
        "gpu_profile",
        "sysmem_fallback",
        "namespace_outbound_check",
        "namespace_eth0_check",
        "namespace_lan_check",
        "llama_cpp_commit",
        "idle_free_vram_mib",
        "audio_pipeline_vram_mib",
        "run_deadline_sec",
        "rerun_allowance_sec",
        "bootstrap_seed",
        "section2_qwen3_4b",
        "section2_qwen3_5_4b",
        "section2_qwen3_8b",
        "cuda_process_check",
        "card_settings_qwen3_4b",
        "card_settings_qwen3_5_4b",
        "card_settings_qwen3_8b",
        "prompt_development_models",
        "audio_vram_samples",
        "audio_vram_min_per_sec",
    )


def operator_without(name: str) -> dict[str, str]:
    return {key: "recorded" for key in OPERATOR_FIELDS if key != name}


@pytest.mark.parametrize(
    "operator",
    [
        pytest.param(operator_without("gpu_driver"), id="a-missing-field"),
        pytest.param({**operator_without(""), "gpu_driver": "  "}, id="a-blank-value"),
        pytest.param({**operator_without(""), "gpu_driver": 1}, id="a-number"),
        pytest.param({**operator_without(""), "extra": "x"}, id="an-extra-key"),
        pytest.param(None, id="no-operator-values"),
    ],
)
def test_d9_bad_operator_values_are_refused(operator: Any) -> None:
    with pytest.raises(StoreError):
        manifest(operator=operator)


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("hashes", "prompts_sha256"),
        ("sampling", "seed"),
        ("max_tokens", "reasoning_probe"),
        ("timeout_sec", "cap_session_extraction"),
        ("conditions", "port"),
    ],
)
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_d9_section_4_s_nested_keys_are_exact(name: str, key: str, change: str) -> None:
    value = dict(manifest_fields()[name])
    if change == "missing":
        del value[key]
    else:
        value["extra"] = 1
    with pytest.raises(StoreError):
        manifest(**{name: value})


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_d9_a_candidate_entry_s_keys_are_exact(change: str) -> None:
    entry = dict(manifest_fields()["candidates"][0])
    if change == "missing":
        del entry["argv"]
    else:
        entry["home"] = "/data"
    with pytest.raises(StoreError):
        manifest(candidates=(entry,))


def test_d9_a_bad_operator_value_makes_no_directory(tmp_path: Path) -> None:
    with pytest.raises(StoreError):
        RunStore.create(str(tmp_path), "r", manifest(operator=operator_without("machine")), now=fixed_now)
    assert list(tmp_path.iterdir()) == []


def test_d9_the_frozen_hashes_fill_the_manifest(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path / "gold")
    hashes = {field.name: getattr(frozen.hashes, field.name) for field in dataclasses.fields(FreezeHashes)}
    hashes["requests"] = dict(frozen.hashes.requests)
    hashes["requests_unconstrained"] = dict(frozen.hashes.requests_unconstrained)
    store = RunStore.create(str(tmp_path), "r", manifest(hashes=hashes), now=fixed_now, fsync=no_fsync)
    stored = load_run(store.path).manifest["hashes"]
    assert stored["prompts_sha256"] == frozen.hashes.prompts_sha256
    assert stored["requests"] == dict(frozen.hashes.requests)


def test_d9_a_mapping_proxy_is_refused_since_the_runner_copies_them(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path / "gold")
    hashes = dict(manifest_fields()["hashes"], requests=frozen.hashes.requests)
    with pytest.raises(TypeError):
        RunStore.create(str(tmp_path), "r", manifest(hashes=hashes), now=fixed_now, fsync=no_fsync)
    assert not (tmp_path / "r").exists()


def test_d9_a_tampered_manifest_fails_to_load(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    (line,) = lines_of(store, "manifest.json")
    del line["operator"]["gpu_driver"]
    Path(store.path, "manifest.json").write_bytes(record_json(line) + b"\n")
    with pytest.raises(StoreError, match="manifest.json line 1"):
        load_run(store.path)


@pytest.mark.parametrize("raw", [b"", b'{"schema"'], ids=["empty", "truncated"])
def test_d9_a_manifest_without_one_complete_line_fails_to_load(tmp_path: Path, raw: bytes) -> None:
    store = new_store(tmp_path)
    Path(store.path, "manifest.json").write_bytes(raw)
    with pytest.raises(StoreError, match="manifest.json"):
        load_run(store.path)


def test_d9_a_manifest_of_two_lines_fails_to_load(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    path = Path(store.path, "manifest.json")
    path.write_bytes(path.read_bytes() * 2)
    with pytest.raises(StoreError, match="manifest.json must hold exactly one complete record"):
        load_run(store.path)


# ---------------------------------------------------------------- D10 pre-run and launch records


def test_d10_launch_and_pre_run_records_round_trip(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    pre_run = pre_run_record()
    launch = launch_record(PRE_RUN_KEY, mode="pre_run", env_names=("HOME", "LLAMA_ARG_LOG_VERBOSITY", "PATH"))
    store.append_pre_run(pre_run)
    store.append_launch(launch)
    view = load_run(store.path)
    assert view.pre_run == (pre_run,)
    assert view.launches == (launch,)
    assert type(view.launches[0].startup) is StartupLog
    assert type(view.launches[0].modalities) is Modalities
    assert isinstance(view.launches[0].argv, tuple)
    assert type(view.pre_run[0].context_checks[0]) is ContextCheck


def test_d10_a_timed_launch_s_startup_keeps_its_nones(tmp_path: Path) -> None:
    startup = StartupLog(3, 1, 12288, 18080, *([None] * 8))
    store = new_store(tmp_path)
    store.append_launch(launch_record(startup=startup))
    loaded = load_run(store.path).launches[0].startup
    assert loaded == startup
    assert loaded.graph_splits is None


def test_d10_graph_splits_is_the_larger_of_the_two() -> None:
    assert dataclasses.replace(STARTUP, graph_splits_pp=3, graph_splits_tg=2).graph_splits == 3
    assert dataclasses.replace(STARTUP, graph_splits_pp=2, graph_splits_tg=5).graph_splits == 5


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"mode": "fast"}, id="an-unknown-mode"),
        pytest.param({"log_name": "logs/other.log"}, id="a-log-name-of-another-launch"),
        pytest.param({"env_names": ("PATH", "HOME")}, id="unsorted-names"),
    ],
)
def test_d10_a_bad_launch_record_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        launch_record(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"candidate": "Qwen/Qwen3-4B-Base"}, id="a-candidate-outside-the-field"),
        pytest.param({"status": "skipped"}, id="an-unknown-status"),
        pytest.param({"thinking": "unknown"}, id="an-unknown-verdict"),
        pytest.param({"status": "blocked"}, id="blocked-naming-no-check"),
        pytest.param({"blocked": ("context",)}, id="ready-naming-a-check"),
    ],
)
def test_d10_a_bad_pre_run_record_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        pre_run_record(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"status": "ready", "thinking": "off"}, id="ready"),
        pytest.param({"status": "ready", "thinking": "seen"}, id="ready-information-only"),
        pytest.param({"status": "not_loaded", "thinking": None, "exit_code": 1, "gguf_sha256": SHA}, id="not-loaded"),
        pytest.param({"status": "blocked", "blocked": ("context", "thinking")}, id="blocked"),
        pytest.param({"status": "blocked", "blocked": ("model_location",), "gguf_sha256": None}, id="no-hash"),
    ],
)
def test_d10_every_outcome_round_trips(tmp_path: Path, overrides: dict[str, Any]) -> None:
    record = pre_run_record(**overrides)
    store = new_store(tmp_path)
    store.append_pre_run(record)
    assert load_run(store.path).pre_run == (record,)


def test_d10_modalities_take_real_bools_and_name_what_is_on() -> None:
    assert Modalities(False, False, False).on == ()
    assert Modalities(True, False, True).on == ("vision", "audio")
    with pytest.raises(ValueError):
        Modalities(0, False, False)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Modalities(False, "no", False)  # type: ignore[arg-type]


# ---------------------------------------------------------------- D11 units and counted runs


def test_d11_a_repeated_run_counts_only_its_complete_attempt(tmp_path: Path) -> None:
    first, second = RunKey(Q8B, "timed_run", 2, 1), RunKey(Q8B, "timed_run", 2, 2)
    store = new_store(tmp_path)
    append_warmup(store, first)
    voided = append_single(store, first)
    fault = store.log_fault(first, "server_exit", item_id="syn_a", detail="server exited with code -11")
    store.void_run(first, fault.fault_id)
    finish(store, first, "void", 2)
    append_warmup(store, second)
    counted = append_single(store, second)
    finish(store, second, "complete", 2)
    view = load_run(store.path)
    assert view.counted_attempt(Q8B, 2) == 2
    assert view.counted_calls(Q8B, 2) == (counted,)
    assert voided in view.calls


def test_d11_counted_calls_leave_out_warm_ups(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store)
    singles = (append_single(store, position=1), append_single(store, position=2))
    finish(store, calls=3)
    assert load_run(store.path).counted_calls(Q4B, 1) == singles


def test_d11_no_complete_attempt_raises(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store)
    with pytest.raises(StoreError, match="run 1"):
        load_run(store.path).counted_attempt(Q4B, 1)
    finish(store, status="void")
    with pytest.raises(StoreError):
        load_run(store.path).counted_calls(Q4B, 1)


def test_d11_a_voided_complete_attempt_does_not_count(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="found later")
    store.void_run(KEY, fault.fault_id)
    with pytest.raises(StoreError):
        load_run(store.path).counted_attempt(Q4B, 1)


def test_d11_two_complete_unvoided_attempts_raise(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    finish(store, RunKey(Q4B, "timed_run", 1, 2))
    with pytest.raises(StoreError, match="2 complete"):
        load_run(store.path).counted_attempt(Q4B, 1)


def test_d11_ungated_units_never_count_as_a_run(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store, RunKey(Q4B, "unconstrained", None, 1))
    finish(store, RunKey(Q4B, "contended", None, 1))
    with pytest.raises(StoreError):
        load_run(store.path).counted_attempt(Q4B, 1)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("x", KEY, "skipped", 0), id="an-unknown-status"),
        pytest.param(("x", KEY, "complete", -1), id="a-negative-count"),
        pytest.param(("x", KEY, "complete", True), id="a-bool-count"),
    ],
)
def test_d11_a_bad_run_record_is_refused(fields: tuple[Any, ...]) -> None:
    with pytest.raises(ValueError):
        RunRecord(*fields)


# ---------------------------------------------------------------- D12 faults


def test_d12_faults_number_from_one_and_alone_void_nothing(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    first = store.log_fault(KEY, "client_fault", item_id="probe_07", detail="ContextBudgetExceeded uses=1 limit=0")
    second = store.log_fault(KEY, "operator", item_id=None, detail="x")
    assert [first.fault_id, second.fault_id] == [1, 2]
    assert first == FaultRecord(
        1, NOW.isoformat(), KEY, "client_fault", "probe_07", "ContextBudgetExceeded uses=1 limit=0"
    )
    view = load_run(store.path)
    assert view.faults == (first, second)
    assert view.voids == ()


@pytest.mark.parametrize("kind", FAULT_KINDS)
def test_d12_every_kind_is_accepted(tmp_path: Path, kind: FaultKind) -> None:
    store = new_store(tmp_path)
    assert load_run(store.path).faults == ()
    fault = store.log_fault(KEY, kind, item_id=None, detail="x")
    assert load_run(store.path).faults == (fault,)


@pytest.mark.parametrize(
    ("kind", "detail"),
    [
        pytest.param("operator", "x" * 201, id="too-long"),
        pytest.param("operator", "é", id="not-ascii"),
        pytest.param("operator", 1, id="not-text"),
        pytest.param("other", "x", id="an-unknown-kind"),
    ],
)
def test_d12_a_bad_fault_is_refused_and_nothing_is_written(tmp_path: Path, kind: Any, detail: Any) -> None:
    store = new_store(tmp_path)
    with pytest.raises(StoreError):
        store.log_fault(KEY, kind, item_id=None, detail=detail)
    assert size_of(store, "faults.jsonl") == 0


def test_d12_the_limit_itself_is_accepted(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    assert len(store.log_fault(KEY, "operator", item_id=None, detail="x" * 200).detail) == 200


def test_d12_a_stored_id_needs_a_real_fault_id() -> None:
    with pytest.raises(StoreError):
        FaultRecord(0, "x", KEY, "operator", None, "x")
    with pytest.raises(StoreError):
        FaultRecord(1, "x", KEY, "operator", 7, "x")  # type: ignore[arg-type]


def test_d12_a_long_detail_found_in_a_log_fails_to_load(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.log_fault(KEY, "operator", item_id=None, detail="x")
    rewrite_line(store, "faults.jsonl", lambda line: line.update(detail="x" * 201))
    with pytest.raises(StoreError, match="faults.jsonl line 1"):
        load_run(store.path)


def test_d12_model_and_request_text_stay_out_of_the_fault_and_void_logs(tmp_path: Path) -> None:
    sentinel = "SENTINEL"
    content = json.dumps({"claims": [{"text": sentinel, "segmentIds": ["seg_001"]}], "nonCheckable": []})
    store = new_store(tmp_path)
    append_warmup(store, call=run_extract(chat_response(content))[0])
    fault = store.log_fault(KEY, "server_exit", item_id="syn_a", detail="server exited with code -11")
    store.void_run(KEY, fault.fault_id)
    assert sentinel.encode("ascii") in Path(store.path, "calls.jsonl").read_bytes()
    assert sentinel.encode("ascii") not in Path(store.path, "faults.jsonl").read_bytes()
    assert sentinel.encode("ascii") not in Path(store.path, "voids.jsonl").read_bytes()


# ---------------------------------------------------------------- D13 void, D14 the scored marker, X26, X53


def test_d13_a_fault_of_that_attempt_voids_it(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.log_fault(KEY, "operator", item_id=None, detail="x")
    key = RunKey(Q4B, "timed_run", 2, 1)
    fault = store.log_fault(key, "server_exit", item_id=None, detail="server exited with code 1")
    void = store.void_run(key, fault.fault_id)
    assert void == VoidRecord(NOW.isoformat(), key, 2)
    assert load_run(store.path).voids == (void,)


def test_d13_what_void_run_refuses_leaves_the_log_unchanged(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    run_2 = RunKey(Q4B, "timed_run", 2, 1)
    run_3 = RunKey(Q4B, "timed_run", 3, 1)
    for _ in (1, 2):
        store.log_fault(KEY, "operator", item_id=None, detail="x")
    store.log_fault(run_2, "operator", item_id=None, detail="x")
    with pytest.raises(StoreError, match="not logged"):
        store.void_run(run_3, 3)
    with pytest.raises(StoreError, match="not logged"):
        store.void_run(run_2, 99)
    assert size_of(store, "voids.jsonl") == 0
    store.void_run(run_2, 3)
    size = size_of(store, "voids.jsonl")
    with pytest.raises(StoreError, match="already void"):
        store.void_run(run_2, 3)
    assert size_of(store, "voids.jsonl") == size


@pytest.mark.parametrize("fault_id", [0, True, "1"])
def test_d13_a_void_names_a_real_fault_id(fault_id: Any) -> None:
    with pytest.raises(ValueError):
        VoidRecord("x", KEY, fault_id)


def test_x53_a_pre_run_key_cannot_be_voided(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    fault = store.log_fault(PRE_RUN_KEY, "launch_failed", item_id=None, detail="spawn failed: PermissionError")
    with pytest.raises(StoreError, match="pre-run"):
        store.void_run(PRE_RUN_KEY, fault.fault_id)
    assert size_of(store, "voids.jsonl") == 0


def test_d14_a_complete_run_is_marked_once(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    store.mark_scored(KEY)
    marker = Path(store.path, "scored", "qwen3-4b.run1.a1.marker")
    assert marker.read_bytes().count(b"\n") == 1
    assert json.loads(marker.read_bytes()) == {
        "schema": 1,
        "record": "scored",
        "stamp": "2026-11-07T20:15:00+00:00",
        "key": {"candidate": Q4B, "phase": "timed_run", "run": 1, "run_attempt": 1},
    }
    with pytest.raises(StoreError, match="already marked"):
        store.mark_scored(KEY)
    assert load_run(store.path).scored == frozenset({KEY})


def test_d14_an_ungated_unit_can_be_marked_too(tmp_path: Path) -> None:
    key = RunKey(Q35, "unconstrained", None, 2)
    store = new_store(tmp_path)
    finish(store, key)
    store.mark_scored(key)
    assert Path(store.path, "scored", "qwen3.5-4b.unconstrained.a2.marker").is_file()
    assert load_run(store.path).scored == frozenset({key})


def test_d14_an_unfinished_or_void_attempt_cannot_be_marked(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    with pytest.raises(StoreError, match="only a complete"):
        store.mark_scored(KEY)
    finish(store, status="void")
    with pytest.raises(StoreError, match="only a complete"):
        store.mark_scored(KEY)
    assert list(Path(store.path, "scored").iterdir()) == []


def test_x26_void_after_scoring_is_refused(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="x")
    store.mark_scored(KEY)
    with pytest.raises(StoreError, match="S2 §3.13"):
        store.void_run(KEY, fault.fault_id)
    assert size_of(store, "voids.jsonl") == 0


def test_x26_scoring_after_a_void_is_refused(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="x")
    store.void_run(KEY, fault.fault_id)
    with pytest.raises(StoreError, match="void attempt"):
        store.mark_scored(KEY)
    assert load_run(store.path).scored == frozenset()


def test_d14_a_marker_cut_short_is_reported_and_still_blocks_a_void(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="x")
    marker = Path(store.path, "scored", "qwen3-4b.run1.a1.marker")
    marker.write_bytes(b'{"sch')
    view = load_run(store.path)
    assert view.scored == frozenset()
    assert view.truncated == (TruncatedLine(marker.name, 1, 5),)
    with pytest.raises(StoreError, match="S2 §3.13"):
        store.void_run(KEY, fault.fault_id)


def test_d14_an_empty_marker_is_reported_too(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    marker = Path(store.path, "scored", "qwen3-4b.run1.a1.marker")
    marker.write_bytes(b"")
    assert load_run(store.path).truncated == (TruncatedLine(marker.name, 1, 0),)


def test_d14_a_marker_under_another_unit_s_name_fails_to_load(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    store.mark_scored(KEY)
    scored = Path(store.path, "scored")
    (scored / "qwen3-4b.run1.a1.marker").rename(scored / "qwen3-4b.run2.a1.marker")
    with pytest.raises(StoreError, match="not a scored marker"):
        load_run(store.path)


# ---------------------------------------------------------------- D15 attach


def test_d15_attach_logs_voids_and_marks(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    finish(store, RunKey(Q4B, "timed_run", 2, 1))
    operator = attached(store)
    fault = operator.log_fault(KEY, "operator", item_id=None, detail="host rebooted")
    assert fault.fault_id == 1
    assert operator.void_run(KEY, fault.fault_id).key == KEY
    operator.mark_scored(RunKey(Q4B, "timed_run", 2, 1))
    assert attached(store).log_fault(KEY, "operator", item_id=None, detail="y").fault_id == 2
    view = load_run(store.path)
    assert len(view.faults) == 2 and len(view.voids) == 1 and len(view.scored) == 1


def test_d15_attach_appends_nothing_else(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    operator = attached(store)
    with pytest.raises(StoreError):
        append_warmup(operator)
    with pytest.raises(StoreError):
        operator.append_launch(launch_record())
    with pytest.raises(StoreError):
        operator.append_pre_run(pre_run_record())
    with pytest.raises(StoreError):
        operator.append_run(RunRecord("x", KEY, "void", 0))
    for name in ("calls.jsonl", "launches.jsonl", "pre_run.jsonl", "runs.jsonl"):
        assert size_of(store, name) == 0


@pytest.mark.parametrize("name", ["faults.jsonl", "voids.jsonl"])
def test_d15_a_truncated_fault_or_void_log_refuses_attach_and_is_left_alone(tmp_path: Path, name: str) -> None:
    store = new_store(tmp_path)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="x")
    store.void_run(KEY, fault.fault_id)
    path = Path(store.path, name)
    raw = path.read_bytes() + b'{"sch'
    path.write_bytes(raw)
    with pytest.raises(StoreError, match=f"{name} line 2"):
        attached(store)
    assert path.read_bytes() == raw


def test_d15_a_truncated_calls_log_does_not_refuse_attach(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    with Path(store.path, "calls.jsonl").open("ab") as file:
        file.write(b'{"sch')
    assert attached(store).path == store.path


def test_d15_a_directory_that_is_not_a_run_is_refused(tmp_path: Path) -> None:
    with pytest.raises(StoreError, match="manifest.json"):
        RunStore.attach(str(tmp_path), now=fixed_now, fsync=no_fsync)


def test_d6_a_truncated_fault_log_is_never_appended_to(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    path = Path(store.path, "faults.jsonl")
    path.write_bytes(b'{"sch')
    with pytest.raises(StoreError, match="faults.jsonl line 1"):
        store.log_fault(KEY, "operator", item_id=None, detail="x")
    assert path.read_bytes() == b'{"sch'


# ---------------------------------------------------------------- section 4: the record schemas

KEY_KEYS = {"candidate", "phase", "run", "run_attempt"}
CALL_KEYS = {
    "item_id",
    "request_kind",
    "constrained",
    "attempts",
    "context_checks",
    "outcome",
    "output",
    "total_wall_ns",
}
ATTEMPT_KEYS = {
    "attempt",
    "cited_source_ids",
    "constrained",
    "content",
    "error_text",
    "failure_kind",
    "finish_reason",
    "http_status",
    "max_tokens",
    "reasoning_content",
    "request_kind",
    "request_sha256",
    "retry_type",
    "server_error_type",
    "server_model",
    "system_fingerprint",
    "thinking",
    "timeout_sec",
    "timings",
    "unknown_source_ids",
    "usage",
    "wall_ns",
}
HEADER = {"schema", "record"}
LAUNCH_KEYS = {field.name for field in dataclasses.fields(LaunchRecord)}


def full_store(tmp_path: Path) -> RunStore:
    """One record of every kind."""
    store = new_store(tmp_path)
    store.append_pre_run(pre_run_record())
    store.append_launch(launch_record())
    append_warmup(store)
    finish(store, calls=1)
    fault = store.log_fault(RunKey(Q4B, "timed_run", 2, 1), "operator", item_id=None, detail="x")
    store.void_run(fault.key, fault.fault_id)
    store.mark_scored(KEY)
    return store


@pytest.mark.parametrize(
    ("file_name", "record", "keys"),
    [
        (
            "calls.jsonl",
            "call",
            {"sequence", "stamp", "key", "role", "session_id", "session_index", "position", "call"},
        ),
        ("launches.jsonl", "launch", LAUNCH_KEYS),
        (
            "pre_run.jsonl",
            "pre_run",
            {"stamp", "candidate", "status", "gguf_sha256", "exit_code", "thinking", "context_checks", "blocked"},
        ),
        ("runs.jsonl", "run", {"stamp", "key", "status", "calls"}),
        ("faults.jsonl", "fault", {"fault_id", "stamp", "key", "kind", "item_id", "detail"}),
        ("voids.jsonl", "void", {"stamp", "key", "fault_id"}),
    ],
)
def test_section_4_each_line_holds_exactly_its_keys(
    tmp_path: Path, file_name: str, record: str, keys: set[str]
) -> None:
    (line,) = lines_of(full_store(tmp_path), file_name)
    assert set(line) == HEADER | keys
    assert (line["schema"], line["record"]) == (1, record)
    if "key" in keys:
        assert set(line["key"]) == KEY_KEYS


def test_section_4_a_launch_line_nests_its_startup_and_modalities(tmp_path: Path) -> None:
    (line,) = lines_of(full_store(tmp_path), "launches.jsonl")
    assert line["modalities"] == {"vision": False, "video": False, "audio": False}
    assert set(line["startup"]) == {field.name for field in dataclasses.fields(StartupLog)}
    assert "graph_splits" not in line["startup"]


def test_section_4_a_call_line_nests_the_call_record(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store, call=repaired_rich())
    (line,) = lines_of(store, "calls.jsonl")
    call = line["call"]
    assert set(call) == CALL_KEYS
    assert [set(attempt) for attempt in call["attempts"]] == [ATTEMPT_KEYS, ATTEMPT_KEYS]
    assert set(call["output"]) == {"source_id", "claims", "non_checkable"}


def test_section_4_a_probe_output_holds_its_three_keys(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.append_call(KEY, "probe", session_id="cap_1", session_index=1, position=1, call=call_of(PROBE_KIND))
    (line,) = lines_of(store, "calls.jsonl")
    assert set(line["call"]["output"]) == {"verdict", "reason", "cited_source_ids"}


def test_section_4_a_failed_call_has_a_null_output(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    append_warmup(store, call=run_extract(httpx.ReadTimeout, httpx.ReadTimeout)[0])
    (line,) = lines_of(store, "calls.jsonl")
    assert line["call"]["output"] is None


def test_section_4_every_stored_line_is_ascii(tmp_path: Path) -> None:
    store = full_store(tmp_path)
    content = json.dumps({"claims": [{"text": "café" + chr(0x1F600), "segmentIds": ["seg_001"]}], "nonCheckable": []})
    append_warmup(store, RunKey(Q35, "timed_run", 1, 1), run_extract(chat_response(content))[0])
    for path in Path(store.path).iterdir():
        if path.is_file():
            assert path.read_bytes().isascii(), path.name


LINE_FILES = ["calls.jsonl", "launches.jsonl", "pre_run.jsonl", "runs.jsonl", "faults.jsonl", "voids.jsonl"]


def rewrite_line(store: RunStore, file_name: str, change: Callable[[dict[str, Any]], object]) -> None:
    (line,) = lines_of(store, file_name)
    change(line)
    Path(store.path, file_name).write_bytes(record_json(line) + b"\n")


@pytest.mark.parametrize("file_name", LINE_FILES)
@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda line: line.pop("stamp"), id="a-missing-key"),
        pytest.param(lambda line: line.update(extra=1), id="an-extra-key"),
        pytest.param(lambda line: line.update(schema=2), id="another-schema"),
        pytest.param(lambda line: line.update(schema=True), id="a-bool-schema"),
        pytest.param(lambda line: line.update(schema=1.0), id="a-float-schema"),
        pytest.param(lambda line: line.update(record="other"), id="another-record"),
        pytest.param(lambda line: line.update(stamp=1), id="a-stamp-that-is-not-text"),
    ],
)
def test_section_4_a_decoder_refuses_a_changed_line(
    tmp_path: Path, file_name: str, change: Callable[[dict[str, Any]], object]
) -> None:
    store = full_store(tmp_path)
    rewrite_line(store, file_name, change)
    with pytest.raises(StoreError, match=f"{file_name} line 1"):
        load_run(store.path)


@pytest.mark.parametrize("file_name", ["calls.jsonl", "launches.jsonl", "runs.jsonl", "faults.jsonl", "voids.jsonl"])
@pytest.mark.parametrize(
    "change",
    [
        pytest.param(lambda key: key.pop("run"), id="a-missing-key"),
        pytest.param(lambda key: key.update(extra=1), id="an-extra-key"),
        pytest.param(lambda key: key.update(candidate="Qwen/Qwen3-4B-Base"), id="a-candidate-outside-the-field"),
    ],
)
def test_section_4_a_decoder_refuses_a_changed_key(
    tmp_path: Path, file_name: str, change: Callable[[dict[str, Any]], object]
) -> None:
    store = full_store(tmp_path)
    rewrite_line(store, file_name, lambda line: change(line["key"]))
    with pytest.raises(StoreError, match=f"{file_name} line 1"):
        load_run(store.path)


@pytest.mark.parametrize("name", ["startup", "modalities"])
@pytest.mark.parametrize("change", ["missing", "extra"])
def test_section_4_a_decoder_refuses_changed_launch_details(tmp_path: Path, name: str, change: str) -> None:
    store = full_store(tmp_path)

    def tamper(line: dict[str, Any]) -> None:
        if change == "missing":
            del line[name][sorted(line[name])[0]]
        else:
            line[name]["extra"] = 1

    rewrite_line(store, "launches.jsonl", tamper)
    with pytest.raises(StoreError, match="launches.jsonl line 1"):
        load_run(store.path)


def test_section_4_a_missing_file_fails_to_load(tmp_path: Path) -> None:
    store = full_store(tmp_path)
    Path(store.path, "voids.jsonl").unlink()
    with pytest.raises(StoreError, match="voids.jsonl"):
        load_run(store.path)


def test_section_4_a_missing_scored_directory_fails_to_load(tmp_path: Path) -> None:
    store = full_store(tmp_path)
    scored = Path(store.path, "scored")
    for marker in scored.iterdir():
        marker.unlink()
    scored.rmdir()
    with pytest.raises(StoreError, match="scored is missing from the run directory"):
        load_run(store.path)


def test_load_run_gives_back_every_record(tmp_path: Path) -> None:
    store = full_store(tmp_path)
    view = load_run(store.path)
    assert isinstance(view, RunView)
    assert view.pre_run == (pre_run_record(),)
    assert view.launches == (launch_record(),)
    assert [stored.role for stored in view.calls] == ["warmup"]
    assert view.runs == (RunRecord(NOW.isoformat(), KEY, "complete", 1),)
    assert [fault.fault_id for fault in view.faults] == [1]
    assert [void.fault_id for void in view.voids] == [1]
    assert view.scored == frozenset({KEY})
    assert view.truncated == ()


# ---------------------------------------------------------------- H4 hooks (H4 spec section 2 item 1)


def test_h4_the_section_2_and_card_settings_fields_follow_the_candidates() -> None:
    assert tuple(SECTION2_FIELDS) == tuple(CARD_SETTINGS_FIELDS) == CANDIDATE_IDS
    values = (*SECTION2_FIELDS.values(), *CARD_SETTINGS_FIELDS.values())
    assert len(set(values)) == len(values) == 6
    assert set(values) <= set(OPERATOR_FIELDS)
    assert len(OPERATOR_FIELDS) == 30
    assert all(name.isidentifier() and "." not in name and "-" not in name for name in values)
    for fields in (SECTION2_FIELDS, CARD_SETTINGS_FIELDS):
        assert isinstance(fields, MappingProxyType)
        with pytest.raises(TypeError):
            fields["Qwen/Qwen3-4B"] = "x"  # type: ignore[index]


def test_h4_a_manifest_needs_the_ten_new_operator_fields(tmp_path: Path) -> None:
    for name in ("section2_qwen3_5_4b", "cuda_process_check", "card_settings_qwen3_8b", "audio_vram_min_per_sec"):
        with pytest.raises(StoreError, match="missing keys"):
            manifest(operator=operator_without(name))
    with pytest.raises(StoreError, match="unexpected keys"):
        manifest(operator={**dict.fromkeys(OPERATOR_FIELDS, "recorded"), "section2_qwen3.5-4b": "passed"})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    ("key", "names"),
    [
        (
            KEY,
            (
                "logs/qwen3-4b.run1.a1.gpu.csv",
                "logs/qwen3-4b.run1.a1.apps.txt",
                "logs/qwen3-4b.run1.a1.proc.csv",
                "logs/qwen3-4b.run1.a1.host.json",
            ),
        ),
        (
            RunKey(Q35, "contended", None, 2),
            (
                "logs/qwen3.5-4b.contended.a2.gpu.csv",
                "logs/qwen3.5-4b.contended.a2.apps.txt",
                "logs/qwen3.5-4b.contended.a2.proc.csv",
                "logs/qwen3.5-4b.contended.a2.host.json",
            ),
        ),
    ],
)
def test_h4_machine_log_names_sit_beside_the_server_log(key: RunKey, names: tuple[str, ...]) -> None:
    kinds = get_args(records_module.MachineLogKind)
    assert kinds == ("gpu", "apps", "proc", "host")
    assert tuple(machine_log_name(key, kind) for kind in kinds) == names
    stem = log_name(key).removesuffix(".log")
    assert all(name.startswith(f"{stem}.") for name in names)


def test_h4_an_unknown_machine_log_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="kind must be one of"):
        machine_log_name(KEY, "log")  # type: ignore[arg-type]


def every_key() -> list[RunKey]:
    keys: list[RunKey] = []
    for candidate in CANDIDATE_IDS:
        keys.append(RunKey(candidate, "pre_run", None, 1))
        for attempt in (1, 2, 3, 12):
            keys += [RunKey(candidate, "timed_run", run, attempt) for run in RUNS]
            keys += [RunKey(candidate, phase, None, attempt) for phase in ("unconstrained", "contended")]
    return keys


def test_h4_parse_unit_name_inverts_the_unit_name_exactly() -> None:
    keys = every_key()
    assert len(keys) == 3 * (1 + 4 * 5)
    names = [records_module._unit_name(key) for key in keys]
    assert len(set(names)) == len(names)
    for key, name in zip(keys, names, strict=True):
        assert parse_unit_name(name) == key
    assert parse_unit_name("qwen3.5-4b.run2.a3") == RunKey(Q35, "timed_run", 2, 3)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "qwen3-4b",
        "qwen3-4b.run1",
        "qwen3-4b.run1.a",
        "qwen3-4b.run1.a0",
        "qwen3-4b.run1.a01",
        "qwen3-4b.run01.a1",
        "qwen3-4b.run0.a1",
        "qwen3-4b.run4.a1",
        "qwen3-4b.timed_run.a1",
        "qwen3-4b.pre_run.a2",
        "qwen3-4b.run1.a1.marker",
        "qwen3-4b.run1.a1.log",
        "qwen3-4b.run1.a1 ",
        " qwen3-4b.run1.a1",
        "Qwen3-4B.run1.a1",
        "qwen3-9b.run1.a1",
        "qwen3.5-4b..run1.a1",
        "qwen3-4b.run1.a" + chr(0x0661),
        "logs/qwen3-4b.run1.a1",
        "qwen3-4b.run1.a1/../x",
    ],
)
def test_h4_parse_unit_name_refuses_anything_else(text: str) -> None:
    with pytest.raises(StoreError, match="is not a unit name"):
        parse_unit_name(text)


def test_h4_parse_unit_name_refuses_a_non_string() -> None:
    with pytest.raises(StoreError):
        parse_unit_name(None)  # type: ignore[arg-type]


def test_h4_marked_is_true_for_a_scored_key_only(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    other = RunKey(Q4B, "timed_run", 2, 1)
    finish(store)
    finish(store, other)
    assert not load_run(store.path).marked(KEY)
    store.mark_scored(KEY)
    view = load_run(store.path)
    assert view.marked(KEY)
    assert not view.marked(other)
    assert not view.marked(RunKey(Q4B, "timed_run", 1, 2))


@pytest.mark.parametrize("raw", [b'{"sch', b""], ids=["cut", "empty"])
def test_h4_a_marker_cut_short_still_counts_as_marked(tmp_path: Path, raw: bytes) -> None:
    store = new_store(tmp_path)
    finish(store)
    Path(store.path, "scored", "qwen3-4b.run1.a1.marker").write_bytes(raw)
    view = load_run(store.path)
    assert view.scored == frozenset()
    assert view.marked(KEY)
    assert not view.marked(RunKey(Q4B, "timed_run", 2, 1))


def test_h4_another_file_s_truncated_tail_marks_nothing(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    with Path(store.path, "runs.jsonl").open("ab") as file:
        file.write(b'{"cut')
    view = load_run(store.path)
    assert [line.file_name for line in view.truncated] == ["runs.jsonl"]
    assert not view.marked(KEY)


def test_h4_the_h3_store_rules_hold_beside_marked(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    finish(store)
    fault = store.log_fault(KEY, "operator", item_id=None, detail="x")
    store.mark_scored(KEY)
    operator = attached(store)
    with pytest.raises(StoreError, match="S2 §3.13"):
        operator.void_run(KEY, fault.fault_id)
    with pytest.raises(StoreError, match="an attached store"):
        append_warmup(operator)
    assert (size_of(store, "voids.jsonl"), size_of(store, "calls.jsonl")) == (0, 0)
    assert load_run(store.path).marked(KEY)


# ---------------------------------------------------------------- H4 write_report and report_numbers

MD = b"# Report\n\nsynthetic\n"


def report_body(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {name: None for name in records_module._REPORT_KEYS if name != "markdown_sha256"}
    body.update({"schema": 1, "record": "report", "run_name": "run_1", "caveats": ["c"]})
    body.update(changes)
    return body


def report_dir(tmp_path: Path) -> Path:
    run = tmp_path / "run_1"
    run.mkdir(exist_ok=True)
    return run


def read_disk(path: str) -> bytes:
    return Path(path).read_bytes()


def numbers(run: Path) -> tuple[tuple[int, bool], ...]:
    return report_numbers(str(run), read_disk)


def snapshot(run: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted((run / "reports").iterdir())}


def test_h4_report_keys_are_the_spec_s_top_level_keys() -> None:
    keys = records_module._REPORT_KEYS
    assert len(keys) == len(set(keys)) == 18
    assert keys[:5] == ("schema", "record", "run_name", "manifest_sha256", "markdown_sha256")
    assert {"recorded", "budgets", "candidates", "decision", "reopening", "caveats", "open_rulings"} <= set(keys)


def test_h4_reports_number_from_one_and_each_file_is_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = report_dir(tmp_path)
    opened: list[tuple[str, str]] = []
    original = Path.open

    def spy(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        opened.append((self.name, mode))
        return original(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    synced: list[int] = []
    for number in (1, 2, 3):
        paths = write_report(str(run), MD, report_body(), fsync=synced.append, realpath=as_given)
        assert paths == (str(run / "reports" / f"report-{number}.md"), str(run / "reports" / f"report-{number}.json"))
    monkeypatch.undo()
    assert opened == [(f"report-{n}.{ext}", "xb") for n in (1, 2, 3) for ext in ("md", "json")]
    assert len(synced) == 6
    assert numbers(run) == ((1, True), (2, True), (3, True))
    digest = hashlib.sha256(MD).hexdigest()
    assert read_disk(str(run / "reports" / "report-2.md")) == MD
    raw = read_disk(str(run / "reports" / "report-2.json"))
    assert raw == record_json({**report_body(), "markdown_sha256": digest}) + b"\n"
    assert raw.isascii() and raw.count(b"\n") == 1


def test_h4_the_markdown_is_written_and_synced_before_the_json(tmp_path: Path) -> None:
    run = report_dir(tmp_path)
    seen: list[tuple[str, ...]] = []

    def fsync(_descriptor: int) -> None:
        seen.append(tuple(path.name for path in sorted((run / "reports").iterdir())))

    write_report(str(run), MD, report_body(), fsync=fsync, realpath=as_given)
    assert seen == [("report-1.md",), ("report-1.json", "report-1.md")]


class HalfWrite:
    """A file whose write stores half the bytes, then fails, as a full disk does."""

    def __init__(self, file: Any) -> None:
        self.file = file

    def __enter__(self) -> HalfWrite:
        return self

    def __exit__(self, *exc: object) -> None:
        self.file.close()

    def write(self, data: bytes) -> int:
        self.file.write(data[: len(data) // 2])
        raise OSError("no space left")


@pytest.mark.parametrize("failing", ["report-1.md", "report-1.json"])
@pytest.mark.parametrize("how", ["write", "fsync"])
def test_h4_a_failure_part_way_leaves_its_files_and_the_next_report_is_n_plus_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str, how: str
) -> None:
    run = report_dir(tmp_path)
    original = Path.open

    def open_failing(self: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        file = original(self, mode, *args, **kwargs)
        return HalfWrite(file) if how == "write" and self.name == failing else file

    synced: list[int] = []

    def fsync(descriptor: int) -> None:
        synced.append(descriptor)
        if how == "fsync" and len(synced) == (1 if failing.endswith(".md") else 2):
            raise OSError("sync failed")

    monkeypatch.setattr(Path, "open", open_failing)
    with pytest.raises(OSError):
        write_report(str(run), MD, report_body(), fsync=fsync, realpath=as_given)
    monkeypatch.undo()
    left = snapshot(run)
    assert set(left) == ({"report-1.md"} if failing.endswith(".md") else {"report-1.md", "report-1.json"})
    # A failed sync of the whole JSON leaves bytes that read as complete: only the raise tells the caller.
    complete = how == "fsync" and failing.endswith(".json")
    assert numbers(run) == ((1, complete),)
    assert write_report(str(run), MD, report_body(), fsync=no_fsync, realpath=as_given)[0].endswith("report-2.md")
    assert {name: raw for name, raw in snapshot(run).items() if name.startswith("report-1.")} == left
    assert numbers(run) == ((1, complete), (2, True))


@pytest.mark.parametrize("lone", ["report-1.md", "report-1.json"])
def test_h4_a_lone_file_uses_up_its_number(tmp_path: Path, lone: str) -> None:
    run = report_dir(tmp_path)
    (run / "reports").mkdir()
    (run / "reports" / lone).write_bytes(b"left over")
    md_path, json_path = write_report(str(run), MD, report_body(), fsync=no_fsync, realpath=as_given)
    assert (Path(md_path).name, Path(json_path).name) == ("report-2.md", "report-2.json")
    assert (run / "reports" / lone).read_bytes() == b"left over"
    assert numbers(run) == ((1, False), (2, True))


def hand_written(run: Path, raw: bytes, md: bytes = MD) -> tuple[tuple[int, bool], ...]:
    (run / "reports").mkdir()
    (run / "reports" / "report-1.md").write_bytes(md)
    (run / "reports" / "report-1.json").write_bytes(raw)
    return numbers(run)


def line_of(body: dict[str, Any]) -> bytes:
    return record_json(body) + b"\n"


DIGEST = hashlib.sha256(MD).hexdigest()


@pytest.mark.parametrize(
    ("raw", "complete"),
    [
        pytest.param(line_of({**report_body(), "markdown_sha256": DIGEST}), True, id="complete"),
        pytest.param(line_of({**report_body(), "markdown_sha256": "0" * 64}), False, id="sha-mismatch"),
        pytest.param(
            line_of({k: v for k, v in {**report_body(), "markdown_sha256": DIGEST}.items() if k != "pending"}),
            False,
            id="missing-key",
        ),
        pytest.param(line_of({**report_body(record="run"), "markdown_sha256": DIGEST}), False, id="not-a-report"),
        pytest.param(line_of({**report_body(schema=2), "markdown_sha256": DIGEST}), False, id="schema-2"),
        pytest.param(line_of({**report_body(schema=True), "markdown_sha256": DIGEST}), False, id="schema-true"),
        pytest.param(line_of({**report_body(), "markdown_sha256": DIGEST})[:-1], False, id="no-line-feed"),
        pytest.param(line_of({**report_body(), "markdown_sha256": DIGEST})[:40], False, id="cut"),
        pytest.param(line_of({**report_body(), "markdown_sha256": DIGEST}) * 2, False, id="two-lines"),
        pytest.param(b"[]\n", False, id="not-an-object"),
        pytest.param(b"", False, id="empty"),
    ],
)
def test_h4_report_numbers_says_which_reports_are_complete(tmp_path: Path, raw: bytes, complete: bool) -> None:
    assert hand_written(report_dir(tmp_path), raw) == ((1, complete),)


def test_h4_a_changed_markdown_makes_its_report_incomplete(tmp_path: Path) -> None:
    raw = line_of({**report_body(), "markdown_sha256": DIGEST})
    assert hand_written(report_dir(tmp_path), raw, md=MD + b" ") == ((1, False),)


def test_h4_a_body_missing_a_key_is_written_and_reads_as_incomplete(tmp_path: Path) -> None:
    run = report_dir(tmp_path)
    body = {k: v for k, v in report_body().items() if k != "budgets"}
    write_report(str(run), MD, body, fsync=no_fsync, realpath=as_given)
    assert numbers(run) == ((1, False),)


def test_h4_no_reports_means_no_numbers(tmp_path: Path) -> None:
    assert numbers(report_dir(tmp_path)) == ()


def test_h4_a_body_already_holding_the_markdown_hash_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    run = report_dir(tmp_path)
    with pytest.raises(StoreError, match="markdown_sha256"):
        write_report(str(run), MD, {**report_body(), "markdown_sha256": DIGEST}, fsync=no_fsync, realpath=as_given)
    assert list(run.iterdir()) == []


def test_h4_a_body_that_cannot_be_stored_writes_nothing(tmp_path: Path) -> None:
    run = report_dir(tmp_path)
    with pytest.raises(ValueError):
        write_report(str(run), MD, report_body(pending=float("nan")), fsync=no_fsync, realpath=as_given)
    assert list(run.iterdir()) == []


def test_h4_a_report_under_onedrive_is_refused(tmp_path: Path) -> None:
    run = report_dir(tmp_path)
    with pytest.raises(StoreError, match="S2 §3.12"):
        write_report(str(run), MD, report_body(), fsync=no_fsync, realpath=lambda _path: "/mnt/c/data/OneDrive/run_1")
    assert list(run.iterdir()) == []


def test_h4_write_report_resolves_the_run_directory(tmp_path: Path) -> None:
    run = report_dir(tmp_path)
    md_path, _json_path = write_report(
        "relative/run_1", MD, report_body(), fsync=no_fsync, realpath=lambda _p: str(run)
    )
    assert Path(md_path) == run / "reports" / "report-1.md"


def test_h4_write_report_s_defaults_are_the_real_calls() -> None:
    defaults = inspect.signature(write_report).parameters
    assert defaults["fsync"].default is os.fsync
    assert defaults["realpath"].default is os.path.realpath
