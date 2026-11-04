"""The constrained schemas and the canonical-JSON helper: H2 spec R37-R42 (S2 §3.8, §3.10, §4; PHASE-0 §2.2)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from types import MappingProxyType
from typing import Any

import pytest
from pydantic import BaseModel

from app.evaluation.bakeoff import extraction, probe, schemas
from app.evaluation.bakeoff.extraction import parse_extraction
from app.evaluation.bakeoff.probe import parse_probe
from app.evaluation.bakeoff.schemas import (
    EXTRACTION_SCHEMA_SHA256,
    PROBE_SCHEMA_SHA256,
    SCHEMA_KEYWORDS,
    canonical_json,
    canonical_sha256,
    extraction_schema,
    probe_schema,
)
from app.evaluation.tests._client_fixtures import block_sockets

EXTRACTION_BYTES = (
    b'{"additionalProperties":false,"properties":{"claims":{"items":{"additionalProperties":false,"properties":'
    b'{"checkability":{"additionalProperties":false,"properties":{"isCheckable":{"const":true,"type":"boolean"},'
    b'"reason":{"type":"string"}},"required":["isCheckable"],"type":"object"},"claimId":{"type":"string"},'
    b'"segmentIds":{"items":{"type":"string"},"minItems":1,"type":"array"},"speakerId":{"type":"string"},'
    b'"text":{"minLength":1,"type":"string"}},"required":["segmentIds","text"],"type":"object"},"type":"array"},'
    b'"nonCheckable":{"items":{"additionalProperties":false,"properties":{"claimId":{"type":"string"},'
    b'"reason":{"type":"string"},"reasonKind":{"enum":["opinion","prediction","request","vague","outOfScope"],'
    b'"type":"string"},"segmentIds":{"items":{"type":"string"},"minItems":1,"type":"array"},'
    b'"speakerId":{"type":"string"},"text":{"type":"string"}},"required":["reasonKind","segmentIds"],'
    b'"type":"object"},"type":"array"}},"required":["claims","nonCheckable"],"type":"object"}'
)
PROBE_BYTES = (
    b'{"additionalProperties":false,"properties":{"citations":{"items":{"additionalProperties":false,"properties":'
    b'{"sourceId":{"type":"string"}},"required":["sourceId"],"type":"object"},"type":"array"},'
    b'"reason":{"minLength":1,"type":"string"},"verdict":{"enum":["SUPPORTED","CONTRADICTED","PARTIALLY_SUPPORTED",'
    b'"INSUFFICIENT_EVIDENCE"],"type":"string"}},"required":["citations","reason","verdict"],"type":"object"}'
)


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)


def aliases(model: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items()}


def required(model: type[BaseModel]) -> set[str]:
    return {field.alias or name for name, field in model.model_fields.items() if field.is_required()}


def nodes(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield schema
    for child in schema.get("properties", {}).values():
        yield from nodes(child)
    if "items" in schema:
        yield from nodes(schema["items"])


def claim_schema() -> dict[str, Any]:
    return extraction_schema()["properties"]["claims"]["items"]


def non_checkable_schema() -> dict[str, Any]:
    return extraction_schema()["properties"]["nonCheckable"]["items"]


def checkability_schema() -> dict[str, Any]:
    return claim_schema()["properties"]["checkability"]


def citation_schema() -> dict[str, Any]:
    return probe_schema()["properties"]["citations"]["items"]


# ---------------------------------------------------------------- R37 canonical_json


def test_canonical_json_sorts_keys_and_keeps_non_ascii_as_utf8() -> None:
    assert canonical_json({"b": 1, "a": [True, None, 0.0, "é"]}) == b'{"a":[true,null,0.0,"\xc3\xa9"],"b":1}'


def test_canonical_json_sorts_nested_keys() -> None:
    value = {"b": [3, {"z": 1, "a": "café"}], "a": True}
    assert canonical_json(value) == '{"a":true,"b":[3,{"a":"café","z":1}]}'.encode()
    assert canonical_sha256(value) == "e89f2c2acb7f660c1ae12f04cf4d6829692bd373673add1fd6b12ecd27786036"


def test_canonical_sha256_of_an_empty_object() -> None:
    assert canonical_sha256({}) == "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"


def test_canonical_sha256_is_the_hash_of_the_canonical_bytes() -> None:
    value = {"k": ["v", 1]}
    digest = canonical_sha256(value)
    assert digest == hashlib.sha256(canonical_json(value)).hexdigest()
    assert len(digest) == 64 and digest == digest.lower()


def test_tuples_serialize_as_arrays() -> None:
    assert canonical_json({"a": (1, (2, 3))}) == b'{"a":[1,[2,3]]}'


@pytest.mark.parametrize(
    "value",
    [{1: "x"}, {"a": {2: "x"}}, {"a": [{3: "x"}]}, ({4: "x"},), {None: "x"}, {True: "x"}, [[{(1, 2): "x"}]]],
)
def test_a_non_str_key_is_refused_at_any_depth(value: object) -> None:
    with pytest.raises(TypeError, match="keys must be strings"):
        canonical_json(value)


def test_a_non_str_key_cannot_share_a_hash_with_a_str_key() -> None:
    assert json.dumps({1: "x"}, sort_keys=True) == json.dumps({"1": "x"}, sort_keys=True)  # L6: why the check exists
    with pytest.raises(TypeError):
        canonical_sha256({1: "x"})


@pytest.mark.parametrize("value", [{1, 2}, b"bytes", {"a": frozenset()}, MappingProxyType({"a": 1}), object()])
def test_non_json_types_are_refused(value: object) -> None:
    with pytest.raises(TypeError):
        canonical_json(value)


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_refused(number: float) -> None:
    with pytest.raises(ValueError):
        canonical_json({"x": number})


def test_a_lone_surrogate_is_refused() -> None:
    with pytest.raises(UnicodeEncodeError):
        canonical_json({"x": chr(0xD800)})


# ---------------------------------------------------------------- R38 accessors and pinned bytes


@pytest.mark.parametrize(
    ("accessor", "expected", "size", "digest"),
    [
        (extraction_schema, EXTRACTION_BYTES, 984, EXTRACTION_SCHEMA_SHA256),
        (probe_schema, PROBE_BYTES, 408, PROBE_SCHEMA_SHA256),
    ],
)
def test_the_schema_bytes_and_hashes_are_pinned(accessor: Any, expected: bytes, size: int, digest: str) -> None:
    raw = canonical_json(accessor())
    assert raw == expected
    assert len(raw) == size
    assert raw.isascii()
    assert hashlib.sha256(raw).hexdigest() == digest == canonical_sha256(accessor())


def test_the_pinned_constants() -> None:
    assert EXTRACTION_SCHEMA_SHA256 == "47a47e9853f514936e9f19bfa0af94004c6637e06f382647b8226cdb5456c2b6"
    assert PROBE_SCHEMA_SHA256 == "f015f3085ac4aca81fb13f078a0441a13f3978af5061084e0b427712f891b527"


@pytest.mark.parametrize(
    ("accessor", "digest"), [(extraction_schema, EXTRACTION_SCHEMA_SHA256), (probe_schema, PROBE_SCHEMA_SHA256)]
)
def test_an_accessor_gives_a_fresh_plain_copy(accessor: Any, digest: str) -> None:
    first = accessor()
    assert type(first) is dict
    assert first is not accessor()
    first["type"] = "string"
    first["properties"].clear()
    assert canonical_sha256(accessor()) == digest


def test_shared_sub_schemas_do_not_alias_in_a_copy() -> None:
    schema = extraction_schema()
    claim = schema["properties"]["claims"]["items"]["properties"]
    non_checkable = schema["properties"]["nonCheckable"]["items"]["properties"]
    assert claim["segmentIds"] == non_checkable["segmentIds"]
    assert claim["segmentIds"] is not non_checkable["segmentIds"]
    claim["segmentIds"]["minItems"] = 2
    assert non_checkable["segmentIds"]["minItems"] == 1


def test_a_changed_schema_no_longer_matches_its_hash() -> None:
    schema = extraction_schema()
    schema["properties"]["claims"]["items"]["properties"]["text"].pop("minLength")
    assert canonical_sha256(schema) != EXTRACTION_SCHEMA_SHA256


# ---------------------------------------------------------------- R39 extraction schema


@pytest.mark.parametrize(
    ("node", "key_set", "wire"),
    [
        (extraction_schema, extraction.TOP_LEVEL_KEYS, extraction._WireExtraction),
        (claim_schema, extraction.CLAIM_KEYS, extraction._WireClaim),
        (checkability_schema, extraction.CHECKABILITY_KEYS, extraction._WireCheckability),
        (non_checkable_schema, extraction.NON_CHECKABLE_KEYS, extraction._WireNonCheckable),
    ],
)
def test_extraction_keys_equal_the_validator_key_sets(node: Any, key_set: frozenset[str], wire: Any) -> None:
    assert set(node()["properties"]) == key_set == aliases(wire)


@pytest.mark.parametrize(
    ("node", "expected", "wire"),
    [
        (extraction_schema, {"claims", "nonCheckable"}, extraction._WireExtraction),
        (claim_schema, {"segmentIds", "text"}, extraction._WireClaim),
        (checkability_schema, {"isCheckable"}, extraction._WireCheckability),
        (non_checkable_schema, {"reasonKind", "segmentIds"}, extraction._WireNonCheckable),
    ],
)
def test_extraction_required_keys_equal_the_wire_model(node: Any, expected: set[str], wire: Any) -> None:
    assert set(node()["required"]) == expected == required(wire)


def test_extraction_value_constraints() -> None:
    assert non_checkable_schema()["properties"]["reasonKind"]["enum"] == list(extraction.REASON_KINDS)
    assert checkability_schema()["properties"]["isCheckable"] == {"const": True, "type": "boolean"}
    assert claim_schema()["properties"]["text"] == {"minLength": 1, "type": "string"}
    assert non_checkable_schema()["properties"]["text"] == {"type": "string"}


@pytest.mark.parametrize("node", [claim_schema, non_checkable_schema])
def test_segment_ids_are_free_strings(node: Any) -> None:
    segment_ids = node()["properties"]["segmentIds"]
    assert segment_ids == {"items": {"type": "string"}, "minItems": 1, "type": "array"}
    assert segment_ids["items"] == {"type": "string"}  # no enum, const or pattern: S2 §3.8


def test_the_minimal_and_maximal_grammar_outputs_pass_the_validator() -> None:
    request = {"seg_001", "seg_002"}
    minimal = parse_extraction('{"claims": [], "nonCheckable": []}', source_id="syn_a", request_segment_ids=request)
    assert minimal.claims == () and minimal.non_checkable == ()
    maximal = parse_extraction(
        '{"claims": [{"segmentIds": ["seg_001"], "text": "EUR/JPY is above 150", "checkability": '
        '{"isCheckable": true, "reason": "r"}, "claimId": "c1", "speakerId": "S1"}], "nonCheckable": '
        '[{"reasonKind": "opinion", "segmentIds": ["seg_002"], "claimId": "c2", "reason": "r", "speakerId": "S2", '
        '"text": "t"}]}',
        source_id="syn_a",
        request_segment_ids=request,
    )
    assert maximal.claims[0].segment_ids == ("seg_001",)
    assert maximal.non_checkable[0].reason_kind == "opinion"


# ---------------------------------------------------------------- R40 probe schema


def test_probe_keys_equal_the_validator_key_sets() -> None:
    assert set(probe_schema()["properties"]) == probe.PROBE_KEYS == aliases(probe._WireProbe)
    assert set(citation_schema()["properties"]) == probe.CITATION_KEYS == aliases(probe._WireCitation)


def test_every_probe_key_is_required() -> None:
    assert set(probe_schema()["required"]) == probe.PROBE_KEYS == required(probe._WireProbe)
    assert set(citation_schema()["required"]) == probe.CITATION_KEYS == required(probe._WireCitation)


def test_probe_value_constraints() -> None:
    properties = probe_schema()["properties"]
    assert properties["verdict"]["enum"] == list(probe.VERDICTS)
    assert properties["reason"] == {"minLength": 1, "type": "string"}
    assert properties["citations"]["items"]["properties"] == {"sourceId": {"type": "string"}}


def test_citations_have_no_minimum_length() -> None:
    assert "minItems" not in probe_schema()["properties"]["citations"]  # AMB-27


def test_a_schema_shaped_probe_output_passes_the_validator() -> None:
    raw = '{"citations": [{"sourceId": "src_1"}], "reason": "r", "verdict": "SUPPORTED"}'
    assert parse_probe(raw, bundle_source_ids={"src_1"}).cited_source_ids == ("src_1",)


# ---------------------------------------------------------------- R41 supported keywords only

_TYPES = ("object", "array", "string", "boolean")


def violations(schema: dict[str, Any]) -> list[str]:
    """R41's walk: every problem found in `schema`, or an empty list."""
    found: list[str] = []
    for node in nodes(schema):
        found.extend(f"keyword {key}" for key in set(node) - SCHEMA_KEYWORDS)
        if node.get("type") not in _TYPES:
            found.append(f"type {node.get('type')!r}")
        if node.get("type") == "object":
            if node.get("additionalProperties") is not False or "properties" not in node:
                found.append("an object must have properties and additionalProperties false")
            elif node.get("required") != sorted(node.get("required", [None])) or not set(node["required"]) <= set(
                node["properties"]
            ):
                found.append("required must be a sorted subset of properties")
        if node.get("type") == "array" and "items" not in node:
            found.append("an array must have items")
    return found


@pytest.mark.parametrize("accessor", [extraction_schema, probe_schema])
def test_the_schemas_pass_the_keyword_walk(accessor: Any) -> None:
    assert violations(accessor()) == []


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        {"type": "array"},
        {"type": "string", "pattern": "^seg_"},
        {"type": "string", "format": "uuid"},
        {"type": "string", "maxLength": 3},
        {"type": ["string", "null"]},
        {"type": "integer"},
        {"$ref": "#/$defs/x"},
        {"type": "object", "properties": {}},
        {"type": "object", "additionalProperties": False},
        {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"}}},
        {"type": "object", "additionalProperties": False, "properties": {"a": {"type": "string"}}, "required": ["b"]},
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
            "required": ["b", "a"],
        },
    ],
)
def test_the_keyword_walk_catches_unsupported_nodes(bad: dict[str, Any]) -> None:
    assert violations(bad)


def test_the_schemas_use_exactly_the_nine_keywords() -> None:
    seen = {key for accessor in (extraction_schema, probe_schema) for node in nodes(accessor()) for key in node}
    assert seen == SCHEMA_KEYWORDS
    assert len(SCHEMA_KEYWORDS) == 9


@pytest.mark.parametrize(
    "keyword",
    [
        "pattern",
        "format",
        "$ref",
        "$defs",
        "anyOf",
        "oneOf",
        "allOf",
        "uniqueItems",
        "maxItems",
        "maxLength",
        "description",
        "title",
    ],
)
def test_keywords_that_are_never_used(keyword: str) -> None:
    assert keyword not in SCHEMA_KEYWORDS
    assert f'"{keyword}"'.encode() not in EXTRACTION_BYTES + PROBE_BYTES


# ---------------------------------------------------------------- R42 enforced key order


def grammar_order(node: dict[str, Any]) -> list[str]:
    """F15: required properties first, then optional ones, each in `properties` order."""
    keys = list(node["properties"])
    return [key for key in keys if key in node["required"]] + [key for key in keys if key not in node["required"]]


@pytest.mark.parametrize(
    ("node", "order"),
    [
        (extraction_schema, ["claims", "nonCheckable"]),
        (claim_schema, ["segmentIds", "text", "checkability", "claimId", "speakerId"]),
        (checkability_schema, ["isCheckable", "reason"]),
        (non_checkable_schema, ["reasonKind", "segmentIds", "claimId", "reason", "speakerId", "text"]),
        (probe_schema, ["citations", "reason", "verdict"]),
        (citation_schema, ["sourceId"]),
    ],
)
def test_the_key_order_the_grammar_forces(node: Any, order: list[str]) -> None:
    assert grammar_order(node()) == order


@pytest.mark.parametrize(
    "line",
    [
        "- top level: `claims`, `nonCheckable`;",
        "- claims item: `segmentIds`, `text`, then optional `checkability`, `claimId`, `speakerId`;",
        "- checkability: `isCheckable`, then optional `reason`;",
        "- nonCheckable item: `reasonKind`, `segmentIds`, then optional `claimId`, `reason`, `speakerId`, `text`;",
        "- probe: `citations`, `reason`, `verdict`;",
        "- citation: `sourceId`.",
    ],
)
def test_the_module_docstring_states_the_key_order(line: str) -> None:
    assert line in (schemas.__doc__ or "")
