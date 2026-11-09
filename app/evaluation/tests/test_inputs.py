"""The frozen inputs: H3 spec I1-I10 and rows X10, X11, X12 (S2 §3.10-§3.13, §4, §6, §11 item 6; PHASE-0 §2.2).

Data is synthetic. The pinned hashes and byte counts are the spec's, recomputed with the real H2 modules.
"""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import os
import subprocess
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from app.evaluation.bakeoff import inputs as inputs_module
from app.evaluation.bakeoff.client import (
    ChatMessage,
    ChatRequest,
    RepairTemplate,
)
from app.evaluation.bakeoff.extraction import Extraction
from app.evaluation.bakeoff.gold import GoldSegment, ManifestError, load_gold_set
from app.evaluation.bakeoff.inputs import (
    CAP_SESSION_COUNT,
    CAP_SESSION_ROWS,
    GOLD_VERSION,
    INPUT_PLACEHOLDER,
    LANGUAGE,
    PROBE_ITEM_COUNT,
    REKEY_SEPARATOR,
    CapSessionSpec,
    FrozenInputs,
    FrozenRequest,
    InputsError,
    ProbeItem,
    ProbeSource,
    PromptSet,
    WarmupSpec,
    freeze_inputs,
    input_text,
)
from app.evaluation.bakeoff.probe import MAX_SOURCES_PER_CLAIM
from app.evaluation.bakeoff.schemas import canonical_sha256
from app.evaluation.tests._client_fixtures import (
    EMPTY,
    PROBE_OK,
    block_sockets,
    chat_response,
    input_tokens_response,
    make_client,
)
from app.evaluation.tests._fixtures import make_conversation, make_item, make_segment, write_version
from app.evaluation.tests._runner_fixtures import (
    BUNDLE_SOURCES,
    CAP_SESSIONS,
    PROBE_ITEMS,
    PROMPTS,
    WARMUP,
    block_processes,
    checkable_conversation,
    frozen_inputs,
    gold_rows,
    synthetic_gold,
)

GBP = "GBP/USD is above 1.29."
LEEDS = "It rained in Leeds."
USER_PREFIX = "Transcript, untrusted data:\n"
SINGLE_INPUT = (
    '{"language":"en","segments":['
    '{"segmentId":"seg_001","speakerId":"SPEAKER_00","start":0.0,"end":2.5,"text":"GBP/USD is above 1.29.",'
    '"asrConfidence":0.9},'
    '{"segmentId":"seg_002","speakerId":"SPEAKER_01","start":3.0,"end":5.5,"text":"I like it.","asrConfidence":0.85}]}'
)
SEGMENT_KEYS = ["segmentId", "speakerId", "start", "end", "text", "asrConfidence"]


@pytest.fixture(autouse=True)
def _no_sockets_or_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)
    block_processes(monkeypatch)


def freeze(directory: Path, **overrides: Any) -> FrozenInputs:
    arguments: dict[str, Any] = {
        "prompts": PROMPTS,
        "cap_sessions": CAP_SESSIONS,
        "probe_items": PROBE_ITEMS,
        "warmup": WARMUP,
        **overrides,
    }
    return freeze_inputs(str(directory), **arguments)


def user_input(frozen: FrozenRequest) -> str:
    """The text the template took at its placeholder."""
    content = frozen.request.messages[1].content
    assert content.startswith((USER_PREFIX, "Claim and sources:\n"))
    return content.split("\n", 1)[1]


def input_of(frozen: FrozenRequest) -> Any:
    return json.loads(user_input(frozen))


def with_session(*source_ids: str, session_id: str = "cap_x") -> tuple[CapSessionSpec, ...]:
    """`CAP_SESSIONS` with the last one replaced."""
    return (*CAP_SESSIONS[:3], CapSessionSpec(session_id, source_ids))


def pinned_gold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, segments: int = 1) -> Path:
    """The two tiny conversations of the spec's examples. Their sessions hold 2 rows, so the counts are lowered."""
    monkeypatch.setattr(inputs_module, "CAP_SESSION_COUNT", 1)
    monkeypatch.setattr(inputs_module, "CAP_SESSION_ROWS", 2)
    monkeypatch.setattr(inputs_module, "PROBE_ITEM_COUNT", 1)
    rows = [make_segment(1, GBP, confidence=0.9), make_segment(2, "I like it.", confidence=0.85)][:segments]
    a = make_conversation("syn_a", (), segments=rows)
    b = make_conversation("syn_b", (), segments=[make_segment(1, LEEDS, confidence=0.8)])
    items = [make_item("i_1", a, 1, "CHECKABLE", "GENERAL"), make_item("i_2", b, 1, "CHECKABLE", "GENERAL")]
    return write_version(tmp_path, [a, b], items, name="v1.0")


def pinned_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, segments: int = 1) -> FrozenInputs:
    item = ProbeItem("probe_01", "Water boils at 100 C at sea level.", BUNDLE_SOURCES)
    return freeze(
        pinned_gold(tmp_path, monkeypatch, segments=segments),
        cap_sessions=(CapSessionSpec("cap_1", ("syn_a", "syn_b")),),
        probe_items=(item,),
    )


def prompt_set(**overrides: Any) -> PromptSet:
    return dataclasses.replace(PROMPTS, **overrides)


# ---------------------------------------------------------------- constants


def test_a_process_start_is_refused_here() -> None:
    with pytest.raises(AssertionError, match="must not start a process"):
        subprocess.Popen(["never-run"])


def test_the_constants() -> None:
    assert (GOLD_VERSION, LANGUAGE, INPUT_PLACEHOLDER, REKEY_SEPARATOR) == ("v1.0", "en", "<<INPUT_JSON>>", ":")
    assert (CAP_SESSION_COUNT, CAP_SESSION_ROWS, PROBE_ITEM_COUNT) == (4, 20, 20)
    assert issubclass(InputsError, ValueError)


@pytest.mark.parametrize(
    "value",
    [
        PROMPTS,
        CAP_SESSIONS[0],
        BUNDLE_SOURCES[0],
        PROBE_ITEMS[0],
        WARMUP,
    ],
)
def test_value_types_are_frozen(value: Any) -> None:
    field = dataclasses.fields(value)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(value, field, "x")


# ---------------------------------------------------------------- I1 the prompt set


def test_i1_the_spec_s_set_is_accepted() -> None:
    assert PROMPTS.repair.text == "Fix:\n<<VALIDATION_ERRORS>>\nAnswer again."


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"extraction_system": ""}, id="empty-extraction-system"),
        pytest.param({"extraction_system": " \n\t"}, id="blank-extraction-system"),
        pytest.param({"probe_system": "   "}, id="blank-probe-system"),
        pytest.param({"extraction_user": ""}, id="empty-extraction-user"),
        pytest.param({"probe_user": "\n"}, id="blank-probe-user"),
        pytest.param({"extraction_system": 1}, id="a-number-for-a-text"),
        pytest.param({"extraction_user": "no marker"}, id="extraction-user-no-marker"),
        pytest.param({"probe_user": "no marker"}, id="probe-user-no-marker"),
        pytest.param({"extraction_user": "<<INPUT_JSON>> <<INPUT_JSON>>"}, id="extraction-user-two-markers"),
        pytest.param({"probe_user": "<<INPUT_JSON>><<INPUT_JSON>>"}, id="probe-user-two-markers"),
        pytest.param({"extraction_system": "x <<INPUT_JSON>>"}, id="extraction-system-input-marker"),
        pytest.param({"probe_system": "x <<INPUT_JSON>>"}, id="probe-system-input-marker"),
        pytest.param({"extraction_system": "x <<VALIDATION_ERRORS>>"}, id="extraction-system-repair-marker"),
        pytest.param({"probe_system": "x <<VALIDATION_ERRORS>>"}, id="probe-system-repair-marker"),
        pytest.param({"extraction_user": "<<INPUT_JSON>> <<VALIDATION_ERRORS>>"}, id="extraction-user-repair-marker"),
        pytest.param({"probe_user": "<<INPUT_JSON>> <<VALIDATION_ERRORS>>"}, id="probe-user-repair-marker"),
        pytest.param({"repair": "Fix:\n<<VALIDATION_ERRORS>>"}, id="repair-a-plain-string"),
        pytest.param({"repair": RepairTemplate("<<VALIDATION_ERRORS>> <<INPUT_JSON>>")}, id="repair-input-marker"),
    ],
)
def test_i1_a_bad_prompt_set_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(InputsError):
        prompt_set(**overrides)


# ---------------------------------------------------------------- I2 one-pass fill


@pytest.mark.parametrize("text", ["<<INPUT_JSON>>", "{x}", "$y", "{0} {} %s <<VALIDATION_ERRORS>>"])
@pytest.mark.parametrize("method", ["extraction_messages", "probe_messages"])
def test_i2_inserted_text_stays_literal(method: str, text: str) -> None:
    system, user = getattr(PROMPTS, method)(text)
    prefix = USER_PREFIX if method == "extraction_messages" else "Claim and sources:\n"
    assert user == ChatMessage("user", prefix + text)
    assert system.role == "system"


def test_i2_the_messages_are_the_system_text_and_the_filled_template() -> None:
    assert PROMPTS.extraction_messages("T") == (
        ChatMessage("system", "Extract claims."),
        ChatMessage("user", USER_PREFIX + "T"),
    )
    assert PROMPTS.probe_messages("T") == (
        ChatMessage("system", "Judge the claim."),
        ChatMessage("user", "Claim and sources:\nT"),
    )


def test_i2_a_segment_text_holding_a_marker_reaches_the_request_literally(tmp_path: Path) -> None:
    conversations, items = gold_rows()
    trap = "see <<INPUT_JSON>> or {x} or $y"
    conversations.append(make_conversation("syn_t", [trap]))
    frozen = freeze(write_version(tmp_path, conversations, items, name="v1.0"))
    assert input_of(frozen.singles[-1])["segments"][0]["text"] == trap


# ---------------------------------------------------------------- I3 single conversations


def test_i3_input_text_is_compact_and_keeps_key_order_and_real_characters() -> None:
    assert input_text({"b": 1, "a": ["é", 0.5]}) == '{"b":1,"a":["é",0.5]}'


def test_i3_input_text_refuses_nan() -> None:
    with pytest.raises(ValueError):
        input_text(float("nan"))


def test_i3_the_spec_s_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = pinned_inputs(tmp_path, monkeypatch, segments=2).singles[0]
    assert frozen.item_id == "syn_a"
    assert frozen.ids == frozenset({"seg_001", "seg_002"})
    assert frozen.request.kind == "single_conversation_extraction"
    assert user_input(frozen) == SINGLE_INPUT
    assert len(SINGLE_INPUT) == 265
    assert frozen.request.messages[0] == ChatMessage("system", "Extract claims.")
    assert len(frozen.request.body_bytes()) == 1638
    assert frozen.request.sha256() == "4e9157f152462230a9d82adfe1b2ff63cb48d541005c67e8881f833e5f6284a3"
    assert len(frozen.unconstrained.body_bytes()) == 587
    assert frozen.unconstrained.sha256() == "7a56a75bf9598da61958701d74cb62a33c0dbe2844898ef5462dbd5a47427043"


def test_i3_one_request_per_conversation_in_file_order(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path, syn_z=2, syn_b=2)
    assert [row.item_id for row in frozen.singles] == ["syn_1", "syn_2", "syn_3", "syn_4", "syn_z", "syn_b"]
    assert {row.request.kind for row in frozen.singles} == {"single_conversation_extraction"}


def test_i3_the_input_is_exactly_the_language_and_the_six_keys(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    for row in frozen.singles:
        value = input_of(row)
        assert list(value) == ["language", "segments"]
        assert value["language"] == "en"
        assert all(list(segment) == SEGMENT_KEYS for segment in value["segments"])


def test_i3_amb_54_session_id_is_not_sent(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    assert not any("sessionId" in user_input(row) for row in (*frozen.singles, *frozen.cap_sessions, frozen.warmup))


def test_i3_no_other_gold_field_reaches_a_message(tmp_path: Path) -> None:
    sentinel = "SENTINEL"
    conversations, items = gold_rows()
    conversation = make_conversation(
        f"{sentinel}_source",
        ["plain words"],
        call_date="1999-12-31",
        setting=f"{sentinel} setting",
        drafted_by=f"{sentinel} drafter",
        guide_version=f"{sentinel} guide",
    )
    conversations.append(conversation)
    items.append(
        make_item(
            f"{sentinel}_item",
            conversation,
            1,
            "CHECKABLE",
            "GENERAL",
            claim_text=f"{sentinel} claim",
            drafted_by=f"{sentinel} drafter",
            label_status=f"{sentinel} status",
        )
    )
    frozen = freeze(write_version(tmp_path, conversations, items, name="v1.0"))
    assert frozen.singles[-1].item_id == f"{sentinel}_source"
    for row in frozen.all_requests():
        for message in row.request.messages:
            assert sentinel not in message.content
            assert "1999-12-31" not in message.content


def test_i3_a_lone_surrogate_in_gold_text_raises_when_the_request_is_hashed(tmp_path: Path) -> None:
    conversations, items = gold_rows()
    conversations.append(make_conversation("syn_s", [chr(0xD800)]))
    with pytest.raises(UnicodeEncodeError):
        freeze(write_version(tmp_path, conversations, items, name="v1.0"))


# ---------------------------------------------------------------- I4 cap sessions


def test_i4_the_spec_s_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = pinned_inputs(tmp_path, monkeypatch).cap_sessions[0]
    assert frozen.item_id == "cap_1"
    assert frozen.request.kind == "cap_session_extraction"
    assert frozen.ids == frozenset({"syn_a:seg_001", "syn_b:seg_001"})
    assert len(frozen.request.body_bytes()) == 1670
    assert frozen.request.sha256() == "230beb2b56617ebda1908edd69c1909bea3afca8788612532eac11ffdd7e0c7e"
    assert input_of(frozen) == {
        "language": "en",
        "segments": [
            {
                "segmentId": "syn_a:seg_001",
                "speakerId": "syn_a:SPEAKER_00",
                "start": 0.0,
                "end": 2.5,
                "text": GBP,
                "asrConfidence": 0.9,
            },
            {
                "segmentId": "syn_b:seg_001",
                "speakerId": "syn_b:SPEAKER_00",
                "start": 0.0,
                "end": 2.5,
                "text": LEEDS,
                "asrConfidence": 0.8,
            },
        ],
    }


def test_i4_segments_join_in_list_order_then_file_order(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path, syn_a=12, syn_b=9)
    frozen = freeze(directory, cap_sessions=with_session("syn_b", "syn_a")).cap_sessions[3]
    segments = input_of(frozen)["segments"]
    expected = [f"syn_b:seg_{n:03d}" for n in range(1, 10)] + [f"syn_a:seg_{n:03d}" for n in range(1, 13)]
    assert [row["segmentId"] for row in segments] == expected
    assert frozen.ids == frozenset(expected)
    assert all(list(row) == SEGMENT_KEYS for row in segments)


def test_i4_amb_57_times_are_unchanged_so_start_restarts_at_each_conversation(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path, syn_a=12, syn_b=9)
    frozen = freeze(directory, cap_sessions=with_session("syn_a", "syn_b")).cap_sessions[3]
    starts = [row["start"] for row in input_of(frozen)["segments"]]
    assert starts[0] == 0.0 and starts[12] == 0.0 and starts[11] == 33.0


def test_i4_amb_79_a_bare_id_is_invalid_and_the_output_carries_the_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    frozen = pinned_inputs(tmp_path, monkeypatch).cap_sessions[0]
    bare = json.dumps({"claims": [{"text": "x", "segmentIds": ["seg_001"]}], "nonCheckable": []})
    rekeyed = json.dumps({"claims": [{"text": "x", "segmentIds": ["syn_a:seg_001"]}], "nonCheckable": []})
    client, _server = make_client(chat_response(bare), input_tokens_response(9), chat_response(rekeyed))
    call = client.extract(
        frozen.request, item_id=frozen.item_id, request_segment_ids=frozen.ids, repair_template=PROMPTS.repair
    )
    assert call.attempts[0].failure_kind == "invalid_output"
    assert call.outcome == "valid"
    assert isinstance(call.output, Extraction)
    assert call.output.source_id == "cap_1"


def colon_conversation(source_id: str, rows: int, *, segment_id: str = "seg_001", speaker: str = "SPEAKER_00") -> Any:
    conversation, _items = checkable_conversation(source_id, rows)
    conversation["segments"][0].update(segment_id=segment_id, speaker=speaker)
    numbers = range(1, rows + 1)
    return conversation, [make_item(f"{source_id}_{n}", conversation, n, "CHECKABLE", "GENERAL") for n in numbers]


@pytest.mark.parametrize(
    ("source_id", "fields"),
    [
        pytest.param("syn:a", {}, id="a-lone-source-id-with-no-collision"),
        pytest.param("syn_c", {"segment_id": "seg:1"}, id="a-segment-id"),
        pytest.param("syn_c", {"speaker": "SPEAKER:1"}, id="a-speaker"),
    ],
)
def test_x10_a_separator_in_any_id_of_a_session_is_refused(
    tmp_path: Path, source_id: str, fields: dict[str, str]
) -> None:
    conversations, items = gold_rows()
    conversation, new_items = colon_conversation(source_id, 20, **fields)
    directory = write_version(tmp_path, [*conversations, conversation], items + new_items, name="v1.0")
    with pytest.raises(InputsError, match="cap_x"):
        freeze(directory, cap_sessions=with_session(source_id))


def test_x10_a_real_collision_is_refused(tmp_path: Path) -> None:
    conversations, items = gold_rows()
    first, first_items = colon_conversation("a:b", 10, segment_id="c")
    second, second_items = colon_conversation("a", 10, segment_id="b:c")
    directory = write_version(
        tmp_path, [*conversations, first, second], items + first_items + second_items, name="v1.0"
    )
    with pytest.raises(InputsError, match="cap_x"):
        freeze(directory, cap_sessions=with_session("a:b", "a"))


def test_x10_a_separator_outside_every_session_is_harmless(tmp_path: Path) -> None:
    conversations, items = gold_rows()
    conversation, new_items = colon_conversation("syn:a", 2)
    frozen = freeze(write_version(tmp_path, [*conversations, conversation], items + new_items, name="v1.0"))
    assert frozen.singles[-1].item_id == "syn:a"


# ---------------------------------------------------------------- I5 the cap-session list


def test_i5_rows_12_and_9_are_accepted(tmp_path: Path) -> None:
    frozen = freeze(synthetic_gold(tmp_path, syn_a=12, syn_b=9), cap_sessions=with_session("syn_a", "syn_b"))
    assert frozen.cap_sessions[3].item_id == "cap_x"


@pytest.mark.parametrize(
    ("source_ids", "message"),
    [
        pytest.param(("syn_a", "syn_b", "syn_c"), "passes the cap", id="rows-12-9-1-past-the-cap"),
        pytest.param(("syn_a", "syn_d"), "fewer than 20", id="rows-12-7-short-of-the-cap"),
        pytest.param(("syn_q",), "not a gold conversation", id="an-unknown-source-id"),
        pytest.param((), "non-empty tuple", id="no-source-ids"),
    ],
)
def test_i5_a_session_outside_the_definition_is_refused(
    tmp_path: Path, source_ids: tuple[str, ...], message: str
) -> None:
    directory = synthetic_gold(tmp_path, syn_a=12, syn_b=9, syn_c=1, syn_d=7)
    with pytest.raises(InputsError, match=f"cap_x: .*{message}"):
        freeze(directory, cap_sessions=with_session(*source_ids))


def test_x11_a_conversation_listed_twice_in_a_session_is_refused_never_de_duplicated(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path, syn_a=12)
    with pytest.raises(InputsError) as raised:
        freeze(directory, cap_sessions=(CapSessionSpec("cap_1", ("syn_a", "syn_a")), *CAP_SESSIONS[1:]))
    assert str(raised.value) == "cap_1: source id 'syn_a' is listed twice"


def test_i5_a_conversation_may_recur_in_another_session(tmp_path: Path) -> None:
    frozen = freeze(synthetic_gold(tmp_path), cap_sessions=with_session("syn_1"))
    assert frozen.cap_sessions[0].ids == frozen.cap_sessions[3].ids


@pytest.mark.parametrize(
    "specs",
    [
        pytest.param(CAP_SESSIONS[:3], id="three-sessions"),
        pytest.param((*CAP_SESSIONS, CapSessionSpec("cap_5", ("syn_1",))), id="five-sessions"),
        pytest.param(list(CAP_SESSIONS), id="a-list"),
        pytest.param((*CAP_SESSIONS[:3], CapSessionSpec("cap_3", ("syn_4",))), id="a-repeated-id"),
        pytest.param((*CAP_SESSIONS[:3], CapSessionSpec("", ("syn_4",))), id="an-empty-id"),
        pytest.param((*CAP_SESSIONS[:3], CapSessionSpec("cap_4", ["syn_4"])), id="source-ids-a-list"),  # type: ignore[arg-type]
    ],
)
def test_i5_a_malformed_list_is_refused(tmp_path: Path, specs: Any) -> None:
    with pytest.raises(InputsError):
        freeze(synthetic_gold(tmp_path), cap_sessions=specs)


def test_i5_only_in_scope_checkable_rows_count(tmp_path: Path) -> None:
    conversations, items = gold_rows()
    conversation, new_items = checkable_conversation("syn_o", 19)
    conversation["segments"] += [make_segment(20, "my knee hurts"), make_segment(21, "I think it is fine")]
    new_items += [
        make_item("o_20", conversation, 20, "CHECKABLE", "HEALTH"),
        make_item("o_21", conversation, 21, "OPINION", "GENERAL"),
    ]
    directory = write_version(tmp_path, [*conversations, conversation], items + new_items, name="v1.0")
    with pytest.raises(InputsError, match="19 in-scope CHECKABLE rows"):
        freeze(directory, cap_sessions=with_session("syn_o"))


# ---------------------------------------------------------------- I6 the probe


def test_i6_the_spec_s_example(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    frozen = pinned_inputs(tmp_path, monkeypatch).probes[0]
    assert (frozen.item_id, frozen.request.kind) == ("probe_01", "reasoning_probe")
    assert frozen.ids == frozenset(f"src_{number}" for number in range(1, 6))
    assert list(input_of(frozen)) == ["claim", "sources"]
    assert input_of(frozen)["sources"][4] == {"sourceId": "src_5", "text": "Source text 5."}
    assert frozen.request.messages[0] == ChatMessage("system", "Judge the claim.")
    assert len(frozen.request.body_bytes()) == 1075
    assert frozen.request.sha256() == "d3e2ef6879396f19a065c7ff067b115fdb1060aecc6b28036ed8d6651017fb08"


def test_i6_the_order_is_the_given_order(tmp_path: Path) -> None:
    items = tuple(reversed(PROBE_ITEMS))
    frozen = freeze(synthetic_gold(tmp_path), probe_items=items)
    assert [row.item_id for row in frozen.probes] == [item.item_id for item in items]


def probe_item(item_id: str = "probe_20", claim: str = "A claim.", sources: Any = BUNDLE_SOURCES) -> ProbeItem:
    return ProbeItem(item_id, claim, sources)


@pytest.mark.parametrize(
    "items",
    [
        pytest.param(PROBE_ITEMS[:19], id="nineteen-items"),
        pytest.param((*PROBE_ITEMS, probe_item("probe_21")), id="twenty-one-items"),
        pytest.param(list(PROBE_ITEMS), id="a-list"),
        pytest.param((*PROBE_ITEMS[:19], probe_item("probe_19")), id="a-repeated-item-id"),
        pytest.param((*PROBE_ITEMS[:19], probe_item("")), id="an-empty-item-id"),
        pytest.param((*PROBE_ITEMS[:19], probe_item(claim="  ")), id="a-blank-claim"),
        pytest.param((*PROBE_ITEMS[:19], probe_item(sources=BUNDLE_SOURCES[:4])), id="four-sources"),
        pytest.param((*PROBE_ITEMS[:19], probe_item(sources=(*BUNDLE_SOURCES, ProbeSource("src_6", "x")))), id="six"),
        pytest.param((*PROBE_ITEMS[:19], probe_item(sources=list(BUNDLE_SOURCES))), id="sources-a-list"),
        pytest.param(
            (*PROBE_ITEMS[:19], probe_item(sources=(*BUNDLE_SOURCES[:4], ProbeSource("src_1", "x")))),
            id="two-sources-with-one-id",
        ),
        pytest.param(
            (*PROBE_ITEMS[:19], probe_item(sources=(*BUNDLE_SOURCES[:4], ProbeSource("", "x")))),
            id="an-empty-source-id",
        ),
        pytest.param(
            (*PROBE_ITEMS[:19], probe_item(sources=(*BUNDLE_SOURCES[:4], ProbeSource("src_5", " \n")))),
            id="a-blank-source-text",
        ),
    ],
)
def test_i6_a_probe_outside_s2_s_shape_is_refused(tmp_path: Path, items: Any) -> None:
    with pytest.raises(InputsError):
        freeze(synthetic_gold(tmp_path), probe_items=items)


def test_i6_sources_are_the_deployed_cap() -> None:
    assert len(BUNDLE_SOURCES) == MAX_SOURCES_PER_CLAIM == 5


# ---------------------------------------------------------------- I7 the warm-up


def test_i7_the_warm_up_is_a_constrained_single_on_its_own_text(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path).warmup
    assert (frozen.item_id, frozen.ids) == ("warmup", frozenset({"w_001"}))
    assert frozen.request.kind == "single_conversation_extraction"
    assert frozen.request.constrained is True
    expected = {
        "language": "en",
        "segments": [
            {
                "segmentId": "w_001",
                "speakerId": "SPEAKER_00",
                "start": 0.0,
                "end": 2.0,
                "text": "The warm-up sentence.",
                "asrConfidence": 0.9,
            }
        ],
    }
    assert input_of(frozen) == expected
    assert frozen.request == ChatRequest(
        "single_conversation_extraction", PROMPTS.extraction_messages(input_text(expected))
    )


def segment(segment_id: str = "w_001", text: str = "The warm-up sentence.") -> GoldSegment:
    return GoldSegment(segment_id, "SPEAKER_00", 0.0, 2.0, text, 0.9)


@pytest.mark.parametrize(
    "warmup",
    [
        pytest.param(WarmupSpec("syn_1", WARMUP.segments), id="a-gold-source-id"),
        pytest.param(WarmupSpec("warmup", (segment(text="syn_2 line 3 states a fact."),)), id="a-gold-text"),
        pytest.param(WarmupSpec("warmup", ()), id="no-segments"),
        pytest.param(WarmupSpec("warmup", list(WARMUP.segments)), id="a-list"),  # type: ignore[arg-type]
        pytest.param(WarmupSpec("warmup", ({"segment_id": "w_001"},)), id="not-a-segment"),  # type: ignore[arg-type]
        pytest.param(WarmupSpec("warmup", (segment(), segment(text="Another."))), id="a-repeated-segment-id"),
        pytest.param(WarmupSpec("", WARMUP.segments), id="an-empty-id"),
    ],
)
def test_i7_a_bad_warm_up_is_refused(tmp_path: Path, warmup: WarmupSpec) -> None:
    with pytest.raises(InputsError):
        freeze(synthetic_gold(tmp_path), warmup=warmup)


# ---------------------------------------------------------------- I8 gold v1.0 only, X12


def test_i8_the_gold_version_and_manifest_are_kept(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    assert frozen.gold_version == "v1.0"
    assert set(frozen.gold_manifest) == {"conversations.jsonl", "items.csv", "items.jsonl"}
    assert all(len(digest) == 64 for digest in frozen.gold_manifest.values())


def test_i8_the_manifest_values_are_the_gold_set_s_own(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path)
    frozen = freeze(directory)
    assert dict(frozen.gold_manifest) == dict(load_gold_set(directory).manifest)
    assert dict(frozen.gold_manifest) == {
        name: hashlib.sha256((directory / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for name in ("conversations.jsonl", "items.csv", "items.jsonl")
    }


def test_i8_there_is_no_override() -> None:
    parameters = inspect.signature(freeze_inputs).parameters
    assert set(parameters) == {"gold_dir", "prompts", "cap_sessions", "probe_items", "warmup"}
    assert all(parameter.default is inspect.Parameter.empty for parameter in parameters.values())


def test_i8_one_flipped_byte_raises_manifest_error_before_anything_else(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path)
    table = directory / "items.csv"
    data = bytearray(table.read_bytes())
    data[-2] ^= 1
    table.write_bytes(bytes(data))
    with pytest.raises(ManifestError):
        freeze(directory, cap_sessions=(), probe_items=(), warmup=None)


@pytest.mark.parametrize("name", ["V1.0", "v1.00", "v1.0.0", "1.0", "v0.4"])
def test_x12_any_other_version_name_is_refused(tmp_path: Path, name: str) -> None:
    with pytest.raises(InputsError, match="S2 §3.12"):
        freeze(synthetic_gold(tmp_path, name=name))


def test_x12_a_trailing_space_is_refused_where_the_filesystem_keeps_the_name(tmp_path: Path) -> None:
    (tmp_path / "v1.0 ").mkdir()
    if "v1.0 " not in [entry.name for entry in tmp_path.iterdir()]:
        pytest.skip("this filesystem drops a trailing space")
    with pytest.raises(InputsError):
        freeze(synthetic_gold(tmp_path, name="v1.0 "))


def test_x12_the_parent_of_v1_0_is_refused(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path)
    with pytest.raises(ManifestError):
        freeze_inputs(
            str(directory / ".."), prompts=PROMPTS, cap_sessions=CAP_SESSIONS, probe_items=PROBE_ITEMS, warmup=WARMUP
        )


def make_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:  # Windows without the privilege
        pytest.skip(f"this OS refused the symlink: {exc}")


def test_x12_a_symlink_named_v1_0_to_another_version_is_refused(tmp_path: Path) -> None:
    target = synthetic_gold(tmp_path, name="v0.4")
    (tmp_path / "links").mkdir()
    link = tmp_path / "links" / "v1.0"
    make_link(link, target)
    with pytest.raises(InputsError, match="v0.4"):
        freeze(link)


def test_x12_a_symlink_to_v1_0_is_accepted(tmp_path: Path) -> None:
    link = tmp_path / "link"
    make_link(link, synthetic_gold(tmp_path))
    assert freeze(link).gold_version == "v1.0"


def test_x12_the_version_is_read_from_the_resolved_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The symlink rows' rule on any OS: the name that counts is the one after resolving."""
    target = synthetic_gold(tmp_path, name="v0.4")
    monkeypatch.setattr(Path, "resolve", lambda _self, strict=False: target)
    with pytest.raises(InputsError, match="v0.4"):
        freeze(tmp_path / "links" / "v1.0")


@pytest.mark.parametrize(
    "spelling",
    [
        pytest.param("x/v1.0/", id="a-trailing-slash"),
        pytest.param("./x/v1.0", id="dot-slash"),
        pytest.param("x/v0.4/../v1.0", id="through-a-sibling"),
    ],
)
def test_x12_other_spellings_of_v1_0_are_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    synthetic_gold(tmp_path / "x")
    (tmp_path / "x" / "v0.4").mkdir()
    monkeypatch.chdir(tmp_path)
    frozen = freeze_inputs(spelling, prompts=PROMPTS, cap_sessions=CAP_SESSIONS, probe_items=PROBE_ITEMS, warmup=WARMUP)
    assert frozen.gold_version == "v1.0"


# ---------------------------------------------------------------- I9 hashes


def test_i9_the_spec_s_examples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hashes = pinned_inputs(tmp_path, monkeypatch).hashes
    assert hashes.prompts_sha256 == "f7a21522d7431609b88beed86b8e415d8732b059d550f3f9dbd221a066cd4ce2"
    assert hashes.repair_sha256 == "6cc71516bcc36ffa7d19848e6d971a45bac32575023fb8fef9128012d9146e93"
    assert hashes.cap_sessions_sha256 == "c01129ec9c7fb6e61705d788ae1befc7e536e3f43d32403870f987c4551546ee"
    assert hashes.cap_requests_sha256 == "e339c19445c86c90a2ce56c0a41db94ac39cb2e62f4e0cfc1d106531645987da"
    assert hashes.probe_sha256 == "9695e7077af056415b3785d0bb1efb8c00e8ff47a16b09a444183058ac870ec9"


def test_i9_the_hash_shapes_are_the_spec_s() -> None:
    assert PROMPTS.sha256() == canonical_sha256(
        {
            "extractionSystem": "Extract claims.",
            "extractionUser": USER_PREFIX + "<<INPUT_JSON>>",
            "probeSystem": "Judge the claim.",
            "probeUser": "Claim and sources:\n<<INPUT_JSON>>",
            "repair": PROMPTS.repair.text,
        }
    )
    assert canonical_sha256([{"sessionId": "cap_1", "sourceIds": ["syn_a", "syn_b"]}]) == (
        "c01129ec9c7fb6e61705d788ae1befc7e536e3f43d32403870f987c4551546ee"
    )


def test_i9_every_item_has_a_hash_of_each_form(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    hashes = frozen.hashes
    assert list(hashes.requests) == [row.item_id for row in frozen.all_requests()]
    assert list(hashes.requests_unconstrained) == list(hashes.requests)
    for row in frozen.all_requests():
        assert hashes.requests[row.item_id] == row.request.sha256()
        assert hashes.requests_unconstrained[row.item_id] == row.unconstrained.sha256()
    assert hashes.warmup_sha256 == frozen.warmup.request.sha256()


def test_i9_a_request_hash_equals_the_first_attempt_s(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    client, _server = make_client(chat_response(EMPTY), chat_response(PROBE_OK))
    single, probe = frozen.singles[0], frozen.probes[0]
    extracted = client.extract(
        single.request, item_id=single.item_id, request_segment_ids=single.ids, repair_template=PROMPTS.repair
    )
    probed = client.probe(
        probe.request, item_id=probe.item_id, bundle_source_ids=probe.ids, repair_template=PROMPTS.repair
    )
    assert extracted.attempts[0].request_sha256 == frozen.hashes.requests[single.item_id]
    assert probed.attempts[0].request_sha256 == frozen.hashes.requests[probe.item_id]


def test_i9_one_changed_character_of_the_repair_wording_changes_no_request_hash(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path)
    before = freeze(directory).hashes
    changed = prompt_set(repair=RepairTemplate("Fix!\n<<VALIDATION_ERRORS>>\nAnswer again."))
    after = freeze(directory, prompts=changed).hashes
    assert after.prompts_sha256 != before.prompts_sha256
    assert after.repair_sha256 != before.repair_sha256
    assert dict(after.requests) == dict(before.requests)
    assert dict(after.requests_unconstrained) == dict(before.requests_unconstrained)
    assert after.cap_requests_sha256 == before.cap_requests_sha256


def test_i9_the_list_hashes_change_with_their_input(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path)
    before = freeze(directory).hashes
    items = (*PROBE_ITEMS[:19], ProbeItem("probe_20", "A changed claim.", BUNDLE_SOURCES))
    specs = tuple(reversed(CAP_SESSIONS))
    assert freeze(directory, probe_items=items).hashes.probe_sha256 != before.probe_sha256
    after = freeze(directory, cap_sessions=specs).hashes
    assert after.cap_sessions_sha256 != before.cap_sessions_sha256
    assert after.cap_requests_sha256 == before.cap_requests_sha256  # a map: the same requests in another order


# ---------------------------------------------------------------- I10 forms and ids


def test_i10_the_unconstrained_form_differs_only_by_the_schema(tmp_path: Path) -> None:
    for row in frozen_inputs(tmp_path).all_requests():
        constrained = json.loads(row.request.body_bytes())
        constrained.pop("response_format")
        assert json.loads(row.unconstrained.body_bytes()) == constrained
        assert row.request.constrained is True
        assert row.unconstrained == ChatRequest(row.request.kind, row.request.messages, constrained=False)


def test_i10_all_requests_holds_n_plus_25_with_the_warm_up_first(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path, syn_a=2)
    every = frozen.all_requests()
    assert len(frozen.singles) == 5
    assert len(every) == 5 + 25
    assert every[0] is frozen.warmup
    assert every[1:6] == frozen.singles
    assert every[6:10] == frozen.cap_sessions
    assert every[10:] == frozen.probes


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"cap_sessions": with_session("syn_4", session_id="syn_1")}, id="a-session-named-as-a-source"),
        pytest.param(
            {"probe_items": (*PROBE_ITEMS[:19], probe_item("syn_1"))},
            id="a-probe-item-named-as-a-source",
        ),
        pytest.param(
            {"probe_items": (*PROBE_ITEMS[:19], probe_item("cap_1"))},
            id="a-probe-item-named-as-a-session",
        ),
        pytest.param({"warmup": WarmupSpec("cap_1", WARMUP.segments)}, id="a-warm-up-named-as-a-session"),
        pytest.param({"warmup": WarmupSpec("probe_01", WARMUP.segments)}, id="a-warm-up-named-as-a-probe-item"),
    ],
)
def test_i10_item_ids_are_unique_across_every_request(tmp_path: Path, overrides: dict[str, Any]) -> None:
    with pytest.raises(InputsError, match="used twice"):
        freeze(synthetic_gold(tmp_path), **overrides)


def test_i10_the_result_holds_tuples_and_read_only_mappings(tmp_path: Path) -> None:
    frozen = frozen_inputs(tmp_path)
    assert isinstance(frozen.singles, tuple)
    assert isinstance(frozen.cap_sessions, tuple)
    assert isinstance(frozen.probes, tuple)
    assert isinstance(frozen.warmup.ids, frozenset)
    for mapping in (frozen.gold_manifest, frozen.hashes.requests, frozen.hashes.requests_unconstrained):
        assert isinstance(mapping, MappingProxyType)
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.gold_version = "v0.4"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.hashes.warmup_sha256 = "x"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        frozen.warmup.item_id = "x"  # type: ignore[misc]


def test_i10_freezing_twice_gives_the_same_hashes(tmp_path: Path) -> None:
    directory = synthetic_gold(tmp_path)
    assert freeze(directory).hashes == freeze(directory).hashes
