"""The reasoning-probe validator: H2 spec R31-R36 (S2 §3.8, §4, §6 gate C; PHASE-0 §2.2). Data here is synthetic."""

from __future__ import annotations

import dataclasses
import json
from typing import Any, get_args

import pytest

from app.evaluation.bakeoff.extraction import CUT_OFF_TEXT, ExtractionInvalid
from app.evaluation.bakeoff.probe import (
    CITATION_KEYS,
    MAX_SOURCES_PER_CLAIM,
    PROBE_KEYS,
    VERDICTS,
    ProbeOutput,
    Verdict,
    parse_probe,
    readable_citation_ids,
)
from app.evaluation.tests._client_fixtures import BUNDLE, block_sockets

VERDICT_ERROR = "verdict: Input should be 'SUPPORTED', 'CONTRADICTED', 'PARTIALLY_SUPPORTED' or 'INSUFFICIENT_EVIDENCE'"
BLANK_REASON_ERROR = "reason: Value error, must contain a non-whitespace character"
FIRST_CHAR_ERROR = "invalid JSON: Expecting value (line 1, column 1)"


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    block_sockets(monkeypatch)


def output(citations: tuple[str, ...] = ("src_1",), reason: str = "r", verdict: str = "SUPPORTED", **extra: Any) -> str:
    body = {"citations": [{"sourceId": source_id} for source_id in citations], "reason": reason, "verdict": verdict}
    return json.dumps({**body, **extra})


def invalid(raw: str, **kwargs: Any) -> ExtractionInvalid:
    with pytest.raises(ExtractionInvalid) as caught:
        parse_probe(raw, bundle_source_ids=BUNDLE, **kwargs)
    return caught.value


def error_text(raw: str) -> str:
    failure = invalid(raw)
    assert failure.kind == "invalid_output"
    return failure.error_text


# ---------------------------------------------------------------- constants and ProbeOutput


def test_the_constants() -> None:
    assert VERDICTS == ("SUPPORTED", "CONTRADICTED", "PARTIALLY_SUPPORTED", "INSUFFICIENT_EVIDENCE")
    assert get_args(Verdict) == VERDICTS
    assert PROBE_KEYS == {"citations", "reason", "verdict"}
    assert CITATION_KEYS == {"sourceId"}
    assert MAX_SOURCES_PER_CLAIM == 5


def test_a_valid_output_gives_a_probe_output() -> None:
    parsed = parse_probe(output(("src_1",), "Source 1 states it."), bundle_source_ids=BUNDLE)
    assert parsed == ProbeOutput("SUPPORTED", "Source 1 states it.", ("src_1",))
    assert parsed.cites_none is False


def test_the_text_is_kept_exactly_and_surrounding_whitespace_is_fine() -> None:
    raw = " \n" + output(("src_1",), " r\n") + "\n"
    assert parse_probe(raw, bundle_source_ids=BUNDLE).reason == " r\n"
    assert readable_citation_ids(raw) == ("src_1",)


def test_only_json_whitespace_is_fine() -> None:
    raw = chr(0xA0) + output()
    assert error_text(raw) == FIRST_CHAR_ERROR
    assert readable_citation_ids(raw) is None


@pytest.mark.parametrize(
    ("verdict", "reason", "ids"),
    [
        ("MAYBE", "r", ()),
        ("supported", "r", ()),
        ("SUPPORTED", "", ()),
        ("SUPPORTED", "  \n", ()),
        ("SUPPORTED", 3, ()),
        ("SUPPORTED", "r", ["src_1"]),
        ("SUPPORTED", "r", (1,)),
        ("SUPPORTED", "r", ("src_1", "src_1")),
    ],
)
def test_a_probe_output_refuses_bad_fields(verdict: Any, reason: Any, ids: Any) -> None:
    with pytest.raises(ValueError):
        ProbeOutput(verdict, reason, ids)


def test_a_probe_output_is_frozen() -> None:
    parsed = ProbeOutput("SUPPORTED", "r", ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.reason = "x"  # type: ignore[misc]
    assert not hasattr(parsed, "__dict__")


# ---------------------------------------------------------------- R31 order


@pytest.mark.parametrize("raw", [b"{}", None, 3, {"citations": []}])
def test_raw_must_be_a_str(raw: Any) -> None:
    with pytest.raises(TypeError):
        parse_probe(raw, bundle_source_ids=BUNDLE)


@pytest.mark.parametrize("bundle", [["src_1"], ("src_1",), "src_1", {"src_1": 1}, None])
def test_the_bundle_must_be_a_set(bundle: Any) -> None:
    with pytest.raises(TypeError):
        parse_probe(output(), bundle_source_ids=bundle)


@pytest.mark.parametrize("bundle", [{"src_1"}, frozenset({"src_1"}), {"src_1": 1}.keys()])
def test_any_set_type_is_a_bundle(bundle: Any) -> None:
    assert parse_probe(output(), bundle_source_ids=bundle).cited_source_ids == ("src_1",)


def test_the_type_checks_come_before_a_cut_off() -> None:
    with pytest.raises(TypeError):
        parse_probe(b"{}", bundle_source_ids=BUNDLE, finish_reason="length")  # type: ignore[arg-type]


def test_the_bundle_type_check_comes_before_a_cut_off() -> None:
    with pytest.raises(TypeError):
        parse_probe(output(), bundle_source_ids=["src_1"], finish_reason="length")  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", [output(), "not json", "", output(("src_9",)), "[]"])
def test_finish_reason_length_is_cut_off_whatever_the_content(raw: str) -> None:
    failure = invalid(raw, finish_reason="length")
    assert (failure.kind, failure.error_text) == ("cut_off", CUT_OFF_TEXT)
    assert str(failure) == CUT_OFF_TEXT


@pytest.mark.parametrize("finish_reason", [None, "stop"])
def test_any_other_finish_reason_goes_on_to_validation(finish_reason: str | None) -> None:
    assert parse_probe(output(), bundle_source_ids=BUNDLE, finish_reason=finish_reason).verdict == "SUPPORTED"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("```json\n" + output() + "\n```", FIRST_CHAR_ERROR),
        ("Sure: " + output(), FIRST_CHAR_ERROR),
        ("", FIRST_CHAR_ERROR),
        ("not json", FIRST_CHAR_ERROR),
        ('{"citations": [], "citations": []}', "invalid JSON: duplicate key 'citations'"),
        ('{"citations": [], "reason": NaN, "verdict": "SUPPORTED"}', "invalid JSON: non-finite number NaN"),
    ],
)
def test_the_json_rules_are_h1s(raw: str, expected: str) -> None:
    assert error_text(raw) == expected


def test_text_after_the_object_is_invalid() -> None:
    raw = output() + " trailing"
    assert error_text(raw) == f"invalid JSON: Extra data (line 1, column {len(output()) + 2})"


@pytest.mark.parametrize("raw", ["[]", '"text"', "1", "null", "true"])
def test_the_top_level_must_be_an_object(raw: str) -> None:
    assert error_text(raw) == "(root): output must be a JSON object"


def test_the_shape_is_checked_before_membership() -> None:
    assert error_text(output(("src_9",), verdict="MAYBE")) == VERDICT_ERROR


# ---------------------------------------------------------------- R32 wire shape

PRODUCT_KEYS = (
    "confidence",
    "url",
    "title",
    "excerpt",
    "publisher",
    "publishedAt",
    "retrievedAt",
    "tier",
    "model",
    "cacheHit",
    "claimId",
)


@pytest.mark.parametrize("key", PRODUCT_KEYS)
def test_a_product_key_at_the_top_level_is_invalid(key: str) -> None:
    assert error_text(output(**{key: 0.9})) == f"{key}: Extra inputs are not permitted"


@pytest.mark.parametrize("key", PRODUCT_KEYS)
def test_a_product_key_in_a_citation_is_invalid(key: str) -> None:
    raw = json.dumps({"citations": [{"sourceId": "src_1", key: "x"}], "reason": "r", "verdict": "SUPPORTED"})
    assert error_text(raw) == f"citations.0.{key}: Extra inputs are not permitted"


@pytest.mark.parametrize("verdict", VERDICTS)
def test_every_verdict_passes(verdict: str) -> None:
    assert parse_probe(output(verdict=verdict), bundle_source_ids=BUNDLE).verdict == verdict


@pytest.mark.parametrize("verdict", ["supported", "Supported", "NOT_CHECKABLE", "MAYBE", "", 1, None, True])
def test_any_other_verdict_is_invalid(verdict: Any) -> None:
    assert error_text(output(verdict=verdict)) == VERDICT_ERROR


@pytest.mark.parametrize("reason", ["", "  ", "\n\t"])
def test_a_blank_reason_is_invalid(reason: str) -> None:
    assert error_text(output(reason=reason)) == BLANK_REASON_ERROR


@pytest.mark.parametrize("reason", [1, None, ["r"], True])
def test_a_reason_must_be_a_string(reason: Any) -> None:
    assert error_text(output(reason=reason)) == "reason: Input should be a valid string"


@pytest.mark.parametrize(
    ("citations", "expected"),
    [
        (["src_1"], "citations.0: Input should be a valid dictionary or instance of _WireCitation"),
        ("src_1", "citations: Input should be a valid list"),
        (None, "citations: Input should be a valid list"),
        ({"sourceId": "src_1"}, "citations: Input should be a valid list"),
        ([{"sourceId": 1}], "citations.0.sourceId: Input should be a valid string"),
        ([{"sourceId": None}], "citations.0.sourceId: Input should be a valid string"),
        ([{}], "citations.0.sourceId: Field required"),
        ([{"url": "x"}], "citations.0.sourceId: Field required\ncitations.0.url: Extra inputs are not permitted"),
    ],
)
def test_the_citation_shape_is_exact(citations: Any, expected: str) -> None:
    raw = json.dumps({"citations": citations, "reason": "r", "verdict": "SUPPORTED"})
    assert error_text(raw) == expected


@pytest.mark.parametrize("key", sorted(PROBE_KEYS))
def test_every_key_is_required(key: str) -> None:
    body = json.loads(output())
    del body[key]
    assert error_text(json.dumps(body)) == f"{key}: Field required"


def test_a_snake_case_key_is_invalid() -> None:
    raw = json.dumps({"citations": [{"source_id": "src_1"}], "reason": "r", "verdict": "SUPPORTED"})
    expected = "citations.0.sourceId: Field required\ncitations.0.source_id: Extra inputs are not permitted"
    assert error_text(raw) == expected


def test_every_violation_gets_its_own_line() -> None:
    raw = json.dumps({"citations": "x", "reason": " ", "verdict": "MAYBE", "confidence": 0.9})
    assert error_text(raw).split("\n") == [
        VERDICT_ERROR,
        BLANK_REASON_ERROR,
        "citations: Input should be a valid list",
        "confidence: Extra inputs are not permitted",
    ]


# ---------------------------------------------------------------- R33 membership


def test_each_offending_occurrence_gets_a_line_in_output_order() -> None:
    assert error_text(output(("src_2", "src_9", "SRC_2", "src_9"))).split("\n") == [
        "citations.1.sourceId: 'src_9' is not a source id of this bundle",
        "citations.2.sourceId: 'SRC_2' is not a source id of this bundle",
        "citations.3.sourceId: 'src_9' is not a source id of this bundle",
    ]


@pytest.mark.parametrize("source_id", ["SRC_1", " src_1", "src_1 ", "src_", "", "src_6"])
def test_membership_is_exact_string_equality(source_id: str) -> None:
    assert "is not a source id of this bundle" in error_text(output((source_id,)))


def test_an_id_from_the_model_cannot_split_a_line() -> None:
    assert error_text(output(("src_1\nsrc_2",))) == (
        r"citations.0.sourceId: 'src_1\nsrc_2' is not a source id of this bundle"
    )


def test_a_quote_in_an_id_is_escaped() -> None:
    assert error_text(output(("it's",))) == r"citations.0.sourceId: 'it\'s' is not a source id of this bundle"


def test_every_bundle_id_passes() -> None:
    cited = tuple(sorted(BUNDLE))
    assert parse_probe(output(cited), bundle_source_ids=BUNDLE).cited_source_ids == cited


# ---------------------------------------------------------------- R34 empty and repeated citations


def test_citing_none_is_valid_output() -> None:
    raw = '{"citations": [], "reason": "No source gives the rate.", "verdict": "INSUFFICIENT_EVIDENCE"}'
    parsed = parse_probe(raw, bundle_source_ids=BUNDLE)
    assert parsed == ProbeOutput("INSUFFICIENT_EVIDENCE", "No source gives the rate.", ())
    assert parsed.cites_none is True


@pytest.mark.parametrize("verdict", VERDICTS)
def test_citing_none_is_valid_with_any_verdict(verdict: str) -> None:
    assert parse_probe(output((), verdict=verdict), bundle_source_ids=BUNDLE).cites_none is True


def test_a_repeated_id_is_kept_once_at_its_first_position() -> None:
    parsed = parse_probe(output(("src_2", "src_2", "src_1"), "Source 2 states 1.29."), bundle_source_ids=BUNDLE)
    assert parsed == ProbeOutput("SUPPORTED", "Source 2 states 1.29.", ("src_2", "src_1"))


# ---------------------------------------------------------------- R35 citation evidence for gate C


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (output(("src_1", "src_2")), ("src_1", "src_2")),
        (output(("src_2", "src_2", "src_1")), ("src_2", "src_1")),
        (output(()), ()),
        (output(("src_9",), verdict="MAYBE"), ("src_9",)),
        (output(("src_1",), confidence=0.9), ("src_1",)),
        (output(("src_1",), reason=" "), ("src_1",)),
        ('{"citations": [{"sourceId": "src_1", "url": "x"}]}', ("src_1",)),
        ('{"citations": []}', ()),
    ],
)
def test_ids_are_read_whatever_else_is_wrong(raw: str, expected: tuple[str, ...]) -> None:
    assert readable_citation_ids(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Sure: " + output(),
        "```json\n" + output() + "\n```",
        "",
        "[]",
        "null",
        '{"reason": "r"}',
        '{"citations": null}',
        '{"citations": "src_1", "reason": "r", "verdict": "SUPPORTED"}',
        '{"citations": ["src_1"], "reason": "r", "verdict": "SUPPORTED"}',
        '{"citations": [{"id": "src_1"}]}',
        '{"citations": [{"sourceId": 1}]}',
        '{"citations": [{"sourceId": "src_1"}, {"id": "src_2"}]}',
        '{"citations": [], "citations": []}',
        '{"citations": [], "reason": NaN}',
    ],
)
def test_unreadable_citations_are_none(raw: str) -> None:
    assert readable_citation_ids(raw) is None


# ---------------------------------------------------------------- R36 no fallback verdict


@pytest.mark.parametrize("raw", ["not json", output(verdict="MAYBE"), output(("src_9",))])
def test_a_failed_probe_is_never_mapped_to_a_verdict(raw: str) -> None:
    with pytest.raises(ExtractionInvalid):
        parse_probe(raw, bundle_source_ids=BUNDLE)
