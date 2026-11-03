"""Extraction parsing: H1 spec rules E1-E6 (S2 §3.8, §3.10, §5.3; PHASE-0 §2.2)."""

from __future__ import annotations

import copy
import json
import pickle
from typing import Any, get_args

import pytest

from app.evaluation.bakeoff import extraction as extraction_module
from app.evaluation.bakeoff.extraction import (
    CHECKABILITY_KEYS,
    CLAIM_KEYS,
    CUT_OFF_TEXT,
    FAILURE_KINDS,
    NON_CHECKABLE_KEYS,
    REASON_KINDS,
    TOP_LEVEL_KEYS,
    ExtractedClaim,
    Extraction,
    ExtractionFailure,
    ExtractionInvalid,
    FailureKind,
    NonCheckableEntry,
    ReasonKind,
    parse_extraction,
)
from app.evaluation.tests._fixtures import NEEDS_DIGIT_LIMIT

REQUEST = frozenset(f"seg_{n:03d}" for n in range(1, 7))  # syn_a: seg_001 to seg_006
# Far deeper than any interpreter's recursion limit, and longer than CPython's integer-string limit (4,300 digits).
TOO_DEEP = "[" * 100_000
TOO_LONG = "1" * 5_000


def parse(raw: str | bytes, **kwargs: Any) -> Extraction:
    kwargs.setdefault("request_segment_ids", REQUEST)
    return parse_extraction(raw, source_id="syn_a", **kwargs)


def invalid(raw: str | bytes, **kwargs: Any) -> ExtractionInvalid:
    with pytest.raises(ExtractionInvalid) as caught:
        parse(raw, **kwargs)
    return caught.value


def doc(claims: list[Any] | None = None, nc: list[Any] | None = None, **extra: Any) -> str:
    return json.dumps({"claims": claims or [], "nonCheckable": nc or [], **extra})


def claim(*ids: Any, text: Any = "a claim", **extra: Any) -> dict[str, Any]:
    return {"text": text, "segmentIds": list(ids), **extra}


def entry(kind: Any, *ids: Any, **extra: Any) -> dict[str, Any]:
    return {"segmentIds": list(ids), "reasonKind": kind, **extra}


def test_constants() -> None:
    assert get_args(ReasonKind) == REASON_KINDS == ("opinion", "prediction", "request", "vague", "outOfScope")
    assert get_args(FailureKind) == FAILURE_KINDS == ("invalid_output", "cut_off", "timeout", "server_error")
    assert CUT_OFF_TEXT == "output was cut off at the token cap (finish_reason=length)"


# ---------------------------------------------------------------- E1 decoding order


def test_a_cut_off_is_invalid_whatever_the_content() -> None:
    for raw in (doc(), "not json", b"\xff"):
        error = invalid(raw, finish_reason="length")
        assert (error.kind, error.error_text, str(error)) == ("cut_off", CUT_OFF_TEXT, CUT_OFF_TEXT)


def test_other_finish_reasons_parse_normally() -> None:
    assert parse(doc(), finish_reason="stop").claims == ()
    assert parse(doc(), finish_reason=None).non_checkable == ()


@pytest.mark.parametrize(
    "raw",
    [
        "```json\n" + doc() + "\n```",
        "Let me think about this first.\n" + doc(),
        '{"claims": [], "claims": [], "nonCheckable": []}',
        "[]",
        "not json",
        doc() + " trailing words",
        "",
        "null",
        '"a string"',
        '{"claims": [], "nonCheckable": [], "x": NaN}',
        pytest.param(TOO_DEEP, id="nesting_too_deep"),
        pytest.param('{"claims": ' + TOO_DEEP, id="nesting_too_deep_in_claims"),
        pytest.param(TOO_LONG, id="number_too_long"),
    ],
)
def test_non_json_or_non_object_output_is_invalid(raw: str) -> None:
    assert invalid(raw).kind == "invalid_output"


def test_surrounding_whitespace_is_fine() -> None:
    result = parse('\n{"claims": [], "nonCheckable": []}\n')
    assert result == Extraction(source_id="syn_a", claims=(), non_checkable=())


def test_bytes_are_decoded_as_strict_utf8() -> None:
    assert parse(doc([claim("seg_002")]).encode("utf-8")).claims[0].segment_ids == ("seg_002",)
    assert invalid(b'{"claims": [], "nonCheckable": []}\xff').error_text.startswith("invalid UTF-8: ")


def test_a_bom_is_not_stripped() -> None:
    assert invalid(bytes((0xEF, 0xBB, 0xBF)) + doc().encode("utf-8")).kind == "invalid_output"


def test_raw_must_be_str_or_bytes() -> None:
    with pytest.raises(TypeError):
        parse({"claims": [], "nonCheckable": []})  # type: ignore[arg-type]


def test_request_segment_ids_must_be_a_set() -> None:
    with pytest.raises(TypeError):
        parse(doc(), request_segment_ids="seg_001")  # type: ignore[arg-type]


# ---------------------------------------------------------------- E2 wire schema


@pytest.mark.parametrize(
    "raw",
    [
        doc(nc=[entry("Opinion", "seg_003")]),
        doc(nc=[entry("out_of_scope", "seg_003")]),
        doc(nc=[entry("", "seg_003")]),
        doc(notes="extra top-level key"),
        json.dumps({"claims": []}),
        json.dumps({"nonCheckable": []}),
        json.dumps({"claims": {}, "nonCheckable": []}),
        doc(nc=[entry("opinion", "seg_003", verdict="SUPPORTED")]),
        doc(nc=[entry("opinion", "seg_003", confidence=0.9)]),
        doc(nc=[entry("opinion", "seg_003", citations=[])]),
        doc(nc=[entry("opinion", "seg_003", checkability={"isCheckable": True})]),
        doc(nc=[{"segmentIds": ["seg_003"]}]),
        doc(nc=[entry("opinion", "seg_003", text=None)]),
        doc(nc=[entry("opinion", "seg_003", reason=5)]),
        doc(nc=[entry("opinion", "seg_003", claimId=None)]),
        doc([claim("seg_002", verdict="SUPPORTED")]),
        doc([claim("seg_002", confidence=0.9)]),
        doc([claim("seg_002", citations=[])]),
        doc([claim("seg_002", sourceId="syn_a")]),
        doc([claim("seg_002", span="at 1.27")]),
        doc([claim("seg_002", reasonKind="opinion")]),
        doc([claim("seg_002", checkability={"isCheckable": False})]),
        doc([claim("seg_002", checkability={"isCheckable": 1})]),
        doc([claim("seg_002", checkability={"isCheckable": "true"})]),
        doc([claim("seg_002", checkability={})]),
        doc([claim("seg_002", checkability={"isCheckable": True, "verdict": "x"})]),
        doc([claim("seg_002", checkability={"isCheckable": True, "reason": None})]),
        doc([claim("seg_002", checkability=None)]),
        doc([{"text": "a claim", "segmentIds": "seg_002"}]),
        doc([{"text": "a claim", "segmentIds": [2]}]),
        doc([{"text": "a claim", "segment_ids": ["seg_002"]}]),
        doc([claim("seg_002", text="  ")]),
        doc([claim("seg_002", text="")]),
        doc([claim("seg_002", text=5)]),
        doc([{"segmentIds": ["seg_002"]}]),
        doc([claim("seg_002", claimId=None)]),
        doc([claim("seg_002", claimId=7)]),
        doc([claim("seg_002", speakerId=None)]),
        doc(["a bare string"]),
    ],
)
def test_wire_schema_violations_are_invalid(raw: str) -> None:
    assert invalid(raw).kind == "invalid_output"


def test_a_minimal_claim() -> None:
    raw = json.dumps({"claims": [{"text": "GBP/USD above 1.29", "segmentIds": ["seg_002"]}], "nonCheckable": []})
    result = parse(raw)
    assert result.claims == (ExtractedClaim(index=0, text="GBP/USD above 1.29", segment_ids=("seg_002",)),)
    assert result.non_checkable == ()
    assert result.source_id == "syn_a"


def test_every_optional_key_is_accepted_and_kept() -> None:
    raw = doc(
        [
            claim(
                "seg_002",
                text="a claim",
                claimId="claim_1",
                speakerId="SPEAKER_01",
                checkability={"isCheckable": True, "reason": "externally verifiable"},
            ),
            claim("seg_005", checkability={"isCheckable": True}),
        ],
        [entry("opinion", "seg_003", text="an opinion", claimId="claim_7", speakerId="SPEAKER_02", reason="fixed")],
    )
    result = parse(raw)
    first = result.claims[0]
    assert (first.claim_id, first.speaker_id) == ("claim_1", "SPEAKER_01")
    assert result.claims[1].index == 1
    assert result.non_checkable == (
        NonCheckableEntry(
            index=0,
            segment_ids=("seg_003",),
            reason_kind="opinion",
            text="an opinion",
            claim_id="claim_7",
            speaker_id="SPEAKER_02",
            reason="fixed",
        ),
    )


def test_ids_and_speaker_values_are_not_checked() -> None:
    result = parse(doc([claim("seg_002", claimId="", speakerId="anyone")]))
    assert (result.claims[0].claim_id, result.claims[0].speaker_id) == ("", "anyone")


def test_claim_text_is_kept_as_given() -> None:
    assert parse(doc([claim("seg_002", text="  padded  ")])).claims[0].text == "  padded  "


@pytest.mark.parametrize("kind", REASON_KINDS)
def test_every_reason_kind_parses(kind: str) -> None:
    assert parse(doc(nc=[entry(kind, "seg_004")])).non_checkable[0].reason_kind == kind


def _aliases(model: Any) -> frozenset[str]:
    return frozenset(field.alias or name for name, field in model.model_fields.items())


def test_the_validator_has_exactly_the_reference_key_sets() -> None:
    assert _aliases(extraction_module._WireExtraction) == TOP_LEVEL_KEYS
    assert _aliases(extraction_module._WireClaim) == CLAIM_KEYS
    assert _aliases(extraction_module._WireCheckability) == CHECKABILITY_KEYS
    assert _aliases(extraction_module._WireNonCheckable) == NON_CHECKABLE_KEYS
    assert not ({"verdict", "confidence", "citations"} & NON_CHECKABLE_KEYS)


def test_the_reference_key_sets_are_the_e2_sets() -> None:
    # The sets H2 freezes and hashes with the prompts and the constrained schema (AMB-19), written out in full.
    assert TOP_LEVEL_KEYS == {"claims", "nonCheckable"}
    assert CLAIM_KEYS == {"text", "segmentIds", "claimId", "speakerId", "checkability"}
    assert CHECKABILITY_KEYS == {"isCheckable", "reason"}
    assert NON_CHECKABLE_KEYS == {"segmentIds", "reasonKind", "text", "claimId", "speakerId", "reason"}
    assert invalid(doc([claim("seg_002", notes="x")])).error_text == "claims.0.notes: Extra inputs are not permitted"
    checkability = {"isCheckable": True, "score": 1}
    error = invalid(doc([claim("seg_002", checkability=checkability)]))
    assert error.error_text == "claims.0.checkability.score: Extra inputs are not permitted"
    error = invalid(doc(nc=[entry("opinion", "seg_003", notes="x")]))
    assert error.error_text == "nonCheckable.0.notes: Extra inputs are not permitted"


# ---------------------------------------------------------------- E3 segment ids


@pytest.mark.parametrize("ids", [[], ["seg_009"], ["SEG_002"], [" seg_002"], ["syn_a:seg_002"], ["seg_002 "]])
def test_claim_ids_outside_the_request_are_invalid(ids: list[str]) -> None:
    assert invalid(doc([claim(*ids)])).kind == "invalid_output"


@pytest.mark.parametrize("ids", [[], ["seg_007"], ["Seg_003"]])
def test_non_checkable_ids_outside_the_request_are_invalid(ids: list[str]) -> None:
    assert invalid(doc(nc=[entry("opinion", *ids)])).kind == "invalid_output"


@pytest.mark.parametrize(
    ("ids", "kept"),
    [
        (["seg_002", "seg_002", "seg_001"], ("seg_002", "seg_001")),
        (["seg_004", "seg_002", "seg_004"], ("seg_004", "seg_002")),
    ],
)
def test_a_repeated_id_is_kept_once_at_its_first_position(ids: list[str], kept: tuple[str, ...]) -> None:
    result = parse(doc([claim(*ids)], [entry("vague", *ids)]))
    assert result.claims[0].segment_ids == kept
    assert result.non_checkable[0].segment_ids == kept


def test_non_item_segments_are_valid_ids() -> None:
    assert parse(doc([claim("seg_001")])).claims[0].segment_ids == ("seg_001",)


def test_parsing_uses_whatever_request_set_it_is_given() -> None:
    rekeyed = frozenset({"syn_a:seg_002"})
    result = parse(doc([claim("syn_a:seg_002")]), request_segment_ids=rekeyed)
    assert result.claims[0].segment_ids == ("syn_a:seg_002",)
    assert invalid(doc([claim("seg_002")]), request_segment_ids=rekeyed).kind == "invalid_output"


# ---------------------------------------------------------------- E4 nothing salvaged


def test_one_bad_id_makes_the_whole_output_invalid() -> None:
    raw = doc([claim("seg_002"), claim("seg_009"), claim("seg_005")], [entry("opinion", "seg_003")])
    error = invalid(raw)
    assert error.error_text == "claims.1.segmentIds.0: 'seg_009' is not a segment id of this request"


# ---------------------------------------------------------------- E5 error text


def test_error_text_is_deterministic() -> None:
    raw = doc([claim("seg_002", text=" ", verdict="x")], [entry("Opinion", "seg_003")], notes=1)
    assert invalid(raw).error_text == invalid(raw).error_text
    assert "https://" not in invalid(raw).error_text


def test_membership_error_text() -> None:
    error = invalid(doc([claim("seg_002", "seg_009")]))
    assert error.error_text == "claims.0.segmentIds.1: 'seg_009' is not a segment id of this request"


def test_membership_errors_come_in_output_order() -> None:
    raw = doc([claim("seg_002", "seg_010"), claim("seg_011")], [entry("vague", "seg_012", "seg_001")])
    assert invalid(raw).error_text.split("\n") == [
        "claims.0.segmentIds.1: 'seg_010' is not a segment id of this request",
        "claims.1.segmentIds.0: 'seg_011' is not a segment id of this request",
        "nonCheckable.0.segmentIds.0: 'seg_012' is not a segment id of this request",
    ]


def test_membership_errors_within_one_entry_follow_id_order() -> None:
    assert invalid(doc([claim("seg_009", "seg_002", "seg_008")])).error_text.split("\n") == [
        "claims.0.segmentIds.0: 'seg_009' is not a segment id of this request",
        "claims.0.segmentIds.2: 'seg_008' is not a segment id of this request",
    ]


def test_model_text_in_an_error_stays_on_one_line() -> None:
    # Keys and ids come from the model; a line break in one must not split a violation over two lines (E5).
    line_separator = chr(0x2028)
    raw = doc([claim("seg_002", text=" ")], **{"a\nb": 1, "c\rd": 2, f"e{line_separator}f": 3})
    assert invalid(raw).error_text.split("\n") == [
        "claims.0.text: Value error, must contain a non-whitespace character",
        "a\\nb: Extra inputs are not permitted",
        "c\\rd: Extra inputs are not permitted",
        "e" + chr(92) + "u2028f: Extra inputs are not permitted",
    ]
    error = invalid(doc([claim("seg_002", **{"x\ny": 1})]))
    assert error.error_text == "claims.0.x\\ny: Extra inputs are not permitted"
    error = invalid(doc(nc=[entry("vague", "seg\n9", "seg_0'1")]))
    assert error.error_text.split("\n") == [
        "nonCheckable.0.segmentIds.0: 'seg\\n9' is not a segment id of this request",
        "nonCheckable.0.segmentIds.1: 'seg_0\\'1' is not a segment id of this request",
    ]


def test_membership_is_checked_only_after_a_structurally_valid_parse() -> None:
    error = invalid(doc([claim("seg_009", text=" ")]))
    assert error.error_text == "claims.0.text: Value error, must contain a non-whitespace character"


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("not json", "invalid JSON: Expecting value (line 1, column 1)"),
        ('{"claims": []\n,,}', "invalid JSON: Expecting property name enclosed in double quotes (line 2, column 2)"),
        ('{"claims": [], "claims": [], "nonCheckable": []}', "invalid JSON: duplicate key 'claims'"),
        ('{"claims": [{"text": "a", "text": "b"}], "nonCheckable": []}', "invalid JSON: duplicate key 'text'"),
        ('{"claims": [], "nonCheckable": [], "x": NaN}', "invalid JSON: non-finite number NaN"),
        ('{"claims": [], "nonCheckable": [], "x": -Infinity}', "invalid JSON: non-finite number -Infinity"),
        ("[]", "(root): output must be a JSON object"),
        (json.dumps({"claims": []}), "nonCheckable: Field required"),
        (doc(notes=1), "notes: Extra inputs are not permitted"),
        (
            doc(nc=[entry("Opinion", "seg_003")]),
            "nonCheckable.0.reasonKind: Input should be 'opinion', 'prediction', 'request', 'vague' or 'outOfScope'",
        ),
        (doc([claim()]), "claims.0.segmentIds: List should have at least 1 item after validation, not 0"),
        (doc([claim("seg_002", claimId=None)]), "claims.0.claimId: Value error, must be a string, not null"),
        (
            doc([claim("seg_002", checkability={"isCheckable": False})]),
            "claims.0.checkability.isCheckable: Value error, must be true: claims[] holds checkable claims only",
        ),
        (
            doc([claim("seg_002", checkability={"isCheckable": 1})]),
            "claims.0.checkability.isCheckable: Input should be a valid boolean",
        ),
        ('{"claims": [], "nonCheckable": [], "it\'s": 1, "it\'s": 2}', "invalid JSON: duplicate key 'it\\'s'"),
        ('{"claims": [], "nonCheckable": [], "a\\nb": 1, "a\\nb": 2}', "invalid JSON: duplicate key 'a\\nb'"),
        (doc([claim("seg_0'1")]), "claims.0.segmentIds.0: 'seg_0\\'1' is not a segment id of this request"),
        (doc(**{"a\nb": 1}), "a\\nb: Extra inputs are not permitted"),
        pytest.param(TOO_DEEP, "invalid JSON: nesting too deep", id="nesting_too_deep"),
        pytest.param(
            '{"claims": [], "nonCheckable": [], "n": ' + TOO_LONG + "}",
            "invalid JSON: number too long",
            id="number_too_long",
            marks=NEEDS_DIGIT_LIMIT,
        ),
    ],
)
def test_fixed_error_texts(raw: str, text: str) -> None:
    error = invalid(raw)
    assert (error.kind, error.error_text, str(error)) == ("invalid_output", text, text)


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (bytes([0xFF]), "invalid start byte"),
        (b'{"claims": [], "nonCheckable": []}' + bytes([0xFF]), "invalid start byte"),
        (bytes([0xC3]), "unexpected end of data"),
    ],
)
def test_bad_utf8_error_text(raw: bytes, reason: str) -> None:
    error = invalid(raw)
    assert (error.kind, error.error_text) == ("invalid_output", f"invalid UTF-8: {reason}")


def test_several_schema_violations_are_one_line_each() -> None:
    raw = doc([claim("seg_002", text=" ", verdict="x")], [entry("Opinion", "seg_003")], notes=1)
    lines = invalid(raw).error_text.split("\n")
    assert lines == [
        "claims.0.text: Value error, must contain a non-whitespace character",
        "claims.0.verdict: Extra inputs are not permitted",
        "nonCheckable.0.reasonKind: Input should be 'opinion', 'prediction', 'request', 'vague' or 'outOfScope'",
        "notes: Extra inputs are not permitted",
    ]


# ---------------------------------------------------------------- E6 record invariants


@pytest.mark.parametrize(
    "build",
    [
        lambda: ExtractedClaim(index=0, text="x", segment_ids=()),
        lambda: ExtractedClaim(index=0, text="x", segment_ids=["seg_001"]),  # type: ignore[arg-type]
        lambda: ExtractedClaim(index=0, text="x", segment_ids=("seg_001", "seg_001")),
        lambda: ExtractedClaim(index=0, text="x", segment_ids=(1,)),  # type: ignore[arg-type]
        lambda: ExtractedClaim(index=0, text="  ", segment_ids=("seg_001",)),
        lambda: ExtractedClaim(index=-1, text="x", segment_ids=("seg_001",)),
        lambda: ExtractedClaim(index=True, text="x", segment_ids=("seg_001",)),
        lambda: ExtractedClaim(index=0, text="x", segment_ids=("seg_001",), claim_id=3),  # type: ignore[arg-type]
        lambda: NonCheckableEntry(index=0, segment_ids=("seg_001",), reason_kind="maybe"),  # type: ignore[arg-type]
        lambda: NonCheckableEntry(index=0, segment_ids=(), reason_kind="opinion"),
        lambda: NonCheckableEntry(index=0, segment_ids=("seg_001",), reason_kind="opinion", reason=1),  # type: ignore[arg-type]
        lambda: ExtractionFailure("syn_a", "crash"),  # type: ignore[arg-type]
        lambda: ExtractionFailure("", "timeout"),
        lambda: Extraction("syn_a", (ExtractedClaim(index=1, text="x", segment_ids=("seg_001",)),), ()),
        lambda: Extraction("syn_a", (), (NonCheckableEntry(index=2, segment_ids=("s",), reason_kind="vague"),)),
        lambda: Extraction("syn_a", [], ()),  # type: ignore[arg-type]
        lambda: Extraction("", (), ()),
    ],
)
def test_records_check_their_invariants(build: Any) -> None:
    with pytest.raises(ValueError):
        build()


@pytest.mark.parametrize("kind", FAILURE_KINDS)
def test_every_failure_kind_is_accepted(kind: str) -> None:
    assert ExtractionFailure("syn_a", kind).kind == kind  # type: ignore[arg-type]


def test_extraction_invalid_carries_its_kind_and_text() -> None:
    error = ExtractionInvalid("invalid_output", "line one\nline two")
    assert (error.kind, error.error_text, str(error)) == ("invalid_output", "line one\nline two", "line one\nline two")
    assert isinstance(error, ValueError)
    with pytest.raises(ValueError):
        ExtractionInvalid("timeout", "x")  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", ["not json", doc([claim("seg_009")])])
def test_extraction_invalid_survives_pickle_and_copy(raw: str) -> None:
    error = invalid(raw)
    for clone in (pickle.loads(pickle.dumps(error)), copy.copy(error), copy.deepcopy(error)):
        assert type(clone) is ExtractionInvalid
        assert (clone.kind, clone.error_text, str(clone), clone.args) == (
            error.kind,
            error.error_text,
            str(error),
            error.args,
        )
    cut_off = invalid(doc(), finish_reason="length")
    assert pickle.loads(pickle.dumps(cut_off)).kind == "cut_off"
