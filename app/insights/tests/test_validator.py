"""Unit + edge-case coverage for InsightValidator.

The validator inspects an *untrusted raw payload* and produces a
`ValidationResult` with two buckets:

  * ``errors``   -> hard problems that flip ``valid=False``
  * ``warnings`` -> soft problems that leave ``valid=True``

It never raises and never mutates the payload — it only reports. The
real error/warning signal is **which list an issue lands in**, NOT the
``severity`` field (which is hardcoded to "warning" everywhere — see the
KNOWN BUG tripwire below).

No heavy ML deps: pure dict inspection, runs in milliseconds.
"""

from __future__ import annotations

import pytest

from app.insights.core.validator import InsightValidator
from app.insights.models.analytics_models import ValidationResult

V = InsightValidator


def _valid_utt(**overrides):
    """A fully-valid raw utterance (no issues), override any field."""
    base = {
        "id": "u1",
        "speaker": "agent",
        "start": 0.0,
        "end": 2.0,
        "text": "hello there",
        "word_count": 2,
        "overlap": False,
        "confidence": 0.9,
        "diarization_confidence": 0.8,
        "sentiment": {"label": "positive", "score": 0.8},
        "emotion": {"values": {"happy": 0.5}},
    }
    base.update(overrides)
    return base


def _valid_payload(**overrides):
    """A fully-valid raw payload (valid=True, no errors, no warnings)."""
    base = {
        "session_id": "sess-1",
        "duration_sec": 4.0,
        "speakers": ["agent", "customer"],
        "utterances": [
            _valid_utt(id="u1", speaker="agent", start=0.0, end=2.0),
            _valid_utt(id="u2", speaker="customer", start=2.0, end=4.0),
        ],
    }
    base.update(overrides)
    return base


def _codes(issues):
    return {i.code for i in issues}


# --------------------------------------------------------------------------- #
# Happy path / single speaker                                                 #
# --------------------------------------------------------------------------- #


def test_happy_path_valid_multi_speaker() -> None:
    result = V.validate_raw_payload(_valid_payload())

    assert isinstance(result, ValidationResult)
    assert result.valid is True
    assert result.errors == []
    assert result.warnings == []


def test_single_speaker_valid() -> None:
    payload = _valid_payload(
        speakers=["agent"],
        utterances=[
            _valid_utt(id="u1", speaker="agent", start=0.0, end=1.0),
            _valid_utt(id="u2", speaker="agent", start=1.0, end=2.0),
        ],
    )

    result = V.validate_raw_payload(payload)

    assert result.valid is True
    assert result.errors == []
    assert result.warnings == []


# --------------------------------------------------------------------------- #
# Top-level structural errors                                                 #
# --------------------------------------------------------------------------- #


def test_non_dict_payload_single_error_early_return() -> None:
    result = V.validate_raw_payload("not-a-dict")

    assert result.valid is False
    assert len(result.errors) == 1
    assert result.errors[0].code == "invalid_payload_type"
    # Early return: utterance-level checks never run.
    assert result.warnings == []


def test_missing_session_id_is_error() -> None:
    payload = _valid_payload()
    del payload["session_id"]

    result = V.validate_raw_payload(payload)

    assert result.valid is False
    assert "missing_required_field" in _codes(result.errors)
    assert any(e.field == "session_id" for e in result.errors)


def test_missing_utterances_key_double_reports() -> None:
    # Missing 'utterances' is reported by BOTH _validate_top_level
    # (missing_required_field) AND _validate_utterances (missing_utterances).
    result = V.validate_raw_payload({"session_id": "s"})

    assert result.valid is False
    assert _codes(result.errors) == {"missing_required_field", "missing_utterances"}
    assert len(result.errors) == 2


def test_utterances_not_a_list_is_error() -> None:
    result = V.validate_raw_payload(_valid_payload(utterances="nope"))

    assert result.valid is False
    assert "invalid_utterances_type" in _codes(result.errors)


def test_empty_utterances_list_is_error() -> None:
    result = V.validate_raw_payload(_valid_payload(utterances=[]))

    assert result.valid is False
    assert "empty_utterances" in _codes(result.errors)


def test_session_id_bad_type_error_but_int_ok() -> None:
    bad = V.validate_raw_payload(_valid_payload(session_id=["list"]))
    assert bad.valid is False
    assert "invalid_session_id" in _codes(bad.errors)

    ok = V.validate_raw_payload(_valid_payload(session_id=12345))
    assert "invalid_session_id" not in _codes(ok.errors)


def test_duration_and_speakers_bad_type_are_warnings_not_errors() -> None:
    # Bad duration_sec / speakers degrade to warnings; valid stays True
    # because the utterances themselves are sound.
    payload = _valid_payload(duration_sec=-5.0, speakers="not-a-list")

    result = V.validate_raw_payload(payload)

    assert result.valid is True
    assert result.errors == []
    assert {"invalid_duration_sec", "invalid_speakers_type"} <= _codes(result.warnings)


# --------------------------------------------------------------------------- #
# Utterance-level                                                             #
# --------------------------------------------------------------------------- #


def test_non_dict_utterance_item_is_error_and_loop_continues() -> None:
    payload = _valid_payload(
        utterances=[
            _valid_utt(id="u1", start=0.0, end=1.0),
            "not-a-dict",
            _valid_utt(id="u3", start=1.0, end=2.0),
        ]
    )

    result = V.validate_raw_payload(payload)

    assert result.valid is False
    # Exactly one bad-item error; the two valid dicts added none.
    bad_item_errors = [e for e in result.errors if e.code == "invalid_utterance_type"]
    assert len(bad_item_errors) == 1
    assert bad_item_errors[0].field == "utterances.1"


def test_utterance_missing_start_and_end_two_errors() -> None:
    payload = _valid_payload(utterances=[{"id": "u1", "speaker": "a", "text": "hi"}])

    result = V.validate_raw_payload(payload)

    assert result.valid is False
    missing = [e for e in result.errors if e.code == "missing_utterance_field"]
    assert len(missing) == 2
    assert {e.field for e in missing} == {"utterances.0.start", "utterances.0.end"}


def test_invalid_times_are_errors() -> None:
    # Negative start.
    neg = V.validate_raw_payload(_valid_payload(utterances=[_valid_utt(start=-1.0, end=1.0)]))
    assert neg.valid is False
    assert "invalid_start_time" in _codes(neg.errors)

    # Negative end.
    neg_end = V.validate_raw_payload(_valid_payload(utterances=[_valid_utt(start=0.0, end=-3.0)]))
    assert neg_end.valid is False
    assert "invalid_end_time" in _codes(neg_end.errors)

    # end < start.
    inverted = V.validate_raw_payload(_valid_payload(utterances=[_valid_utt(start=5.0, end=2.0)]))
    assert inverted.valid is False
    assert "invalid_time_order" in _codes(inverted.errors)


def test_extreme_values_huge_times_ok_bad_probability_warns() -> None:
    payload = _valid_payload(
        utterances=[
            _valid_utt(
                start=1_000_000_000.0,
                end=1_000_000_005.0,
                word_count=10_000_000,
                confidence=5.0,  # out of [0,1] -> warning, not error
            )
        ]
    )

    result = V.validate_raw_payload(payload)

    assert result.valid is True  # huge but valid times + warning-only probability
    assert result.errors == []
    assert "invalid_probability" in _codes(result.warnings)


def test_missing_optional_fields_are_warnings() -> None:
    # Bare utterance: only start/end. id/speaker/text absent -> warnings.
    # duration_sec / speakers also absent at top level -> skipped silently.
    payload = {"session_id": "s", "utterances": [{"start": 0.0, "end": 1.0}]}

    result = V.validate_raw_payload(payload)

    assert result.valid is True
    assert result.errors == []
    assert {"missing_utterance_id", "missing_speaker", "missing_text"} <= _codes(result.warnings)


def test_bad_optional_field_types_are_warnings() -> None:
    payload = _valid_payload(
        utterances=[
            _valid_utt(
                start=0.0,
                end=1.0,
                text=123,  # non-str
                word_count=1.5,  # non-int
                overlap="yes",  # non-bool
                confidence=2.0,  # out of range
                sentiment="positive",  # non-dict
                emotion=[1, 2],  # non-dict
            )
        ]
    )

    result = V.validate_raw_payload(payload)

    assert result.valid is True
    assert result.errors == []
    expected = {
        "invalid_text_type",
        "invalid_word_count",
        "invalid_overlap_type",
        "invalid_probability",
        "invalid_sentiment_type",
        "invalid_emotion_type",
    }
    assert expected <= _codes(result.warnings)


def test_utterance_order_irregular_is_warning() -> None:
    # Second utterance starts before the first in SOURCE order.
    payload = _valid_payload(
        utterances=[
            _valid_utt(id="u1", start=5.0, end=6.0),
            _valid_utt(id="u2", start=1.0, end=2.0),
        ]
    )

    result = V.validate_raw_payload(payload)

    assert result.valid is True
    assert "utterance_order_irregular" in _codes(result.warnings)


# --------------------------------------------------------------------------- #
# KNOWN BUG tripwires                                                         #
# --------------------------------------------------------------------------- #


def test_hard_errors_carry_warning_severity_documents_known_bug() -> None:
    # KNOWN BUG (Tier 3 candidate): severity field hardcoded to
    # "warning" even for hard errors. Real signal lives in which
    # list (errors vs warnings). Locking current behavior so a
    # future "fix" requires explicit reasoning. See production
    # readiness review item #21.
    result = V.validate_raw_payload(_valid_payload(utterances=[]))

    assert result.valid is False
    assert result.errors  # a hard error was produced
    assert result.errors[0].severity == "warning"


def test_all_numeric_predicates_reject_bool() -> None:
    # bool is an int subclass; every numeric predicate must reject it so a
    # bool word_count is treated as invalid, the same as a bool start/end.
    assert V._is_number(True) is False
    assert V._is_probability(True) is False
    assert V._is_non_negative_number(True) is False
    assert V._is_non_negative_int(True) is False


# --------------------------------------------------------------------------- #
# Pure predicates                                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, True), (5.0, True), (0, True), (True, False), ("5", False), (None, False)],
)
def test_is_number(value, expected) -> None:
    assert V._is_number(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, True), (5, True), (-1.0, False), ("x", False), (None, False)],
)
def test_is_non_negative_number(value, expected) -> None:
    assert V._is_non_negative_number(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, True), (5, True), (-1, False), (1.5, False), ("3", False), (True, False), (False, False)],
)
def test_is_non_negative_int(value, expected) -> None:
    assert V._is_non_negative_int(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.0, True), (1.0, True), (0.5, True), (1.5, False), (-0.1, False), ("x", False)],
)
def test_is_probability(value, expected) -> None:
    assert V._is_probability(value) is expected
