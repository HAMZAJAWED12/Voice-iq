"""Unit + edge-case coverage for InsightNormalizer.

The normalizer is the single entry point that turns an untrusted raw
payload (any `dict`) into a validated `SessionInput`. It must be
defensive: coerce types, fill defaults, drop garbage, and never crash on
upstream gaps — *except* it deliberately delegates the "no utterances"
rule to `SessionInput`, which raises. These tests pin both the happy
path and the long tail of defensive branches.

No heavy ML deps: pure dict -> pydantic, runs in milliseconds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.insights.core.normalizer import InsightNormalizer
from app.insights.models.input_models import SessionInput

N = InsightNormalizer


def _utt(**overrides):
    """Minimal valid raw utterance dict, override any field."""
    base = {"id": "u1", "speaker": "agent", "start": 0.0, "end": 1.0, "text": "hello world"}
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Public API: normalize_to_session_input                                      #
# --------------------------------------------------------------------------- #


def test_happy_path_full_payload() -> None:
    payload = {
        "session_id": "  sess-1  ",
        "utterances": [
            {
                "id": "u1",
                "speaker": "agent",
                "start": 0.0,
                "end": 2.4,
                "text": "Hi there",
                "word_count": 2,
                "sentiment": {"label": "positive", "score": 0.82},
                "emotion": {"values": {"happy": 0.6}},
                "confidence": 0.9,
            },
            {
                "id": "u2",
                "speaker": "customer",
                "start": 2.6,
                "end": 6.5,
                "text": "Order late",
                "sentiment": {"label": "negative", "score": 0.28},
            },
        ],
        "meta": {"source": "asr", "language": "en", "created_at": "2026-01-01", "pipeline_version": "1.0"},
        "warnings": ["w1", None, 2],
        "speaker_stats": {"AGENT": {"x": 1}},
        "conversation_stats": {"turns": {"n": 2}},
    }

    result = N.normalize_to_session_input(payload)

    assert isinstance(result, SessionInput)
    assert result.session_id == "sess-1"  # stripped
    assert len(result.utterances) == 2
    assert [u.id for u in result.utterances] == ["u1", "u2"]
    assert [u.speaker for u in result.utterances] == ["AGENT", "CUSTOMER"]
    assert result.speakers == ["AGENT", "CUSTOMER"]  # inferred from utterances
    assert result.meta is not None and result.meta.language == "en"
    assert result.warnings == ["w1", "2"]  # None filtered, int str()'d
    assert result.utterances[0].sentiment is not None
    assert result.utterances[0].sentiment.label == "positive"
    assert result.utterances[0].emotion is not None
    assert result.utterances[0].emotion.values == {"happy": 0.6}
    assert result.speaker_stats == {"AGENT": {"x": 1}}
    assert result.conversation_stats == {"turns": {"n": 2}}


def test_empty_session_no_utterances_raises() -> None:
    # Normalizer does NOT fail-soft here: SessionInput enforces >=1 utterance.
    with pytest.raises(ValidationError):
        N.normalize_to_session_input({"session_id": "s"})


def test_empty_session_all_non_dict_utterances_raises() -> None:
    # Non-dict utterances are silently dropped -> [] -> SessionInput raises.
    with pytest.raises(ValidationError):
        N.normalize_to_session_input({"session_id": "s", "utterances": ["str", 123, None]})


def test_single_speaker_session() -> None:
    payload = {
        "session_id": "s",
        "utterances": [
            _utt(id="u1", speaker="spk 1", start=0.0, end=1.0),
            _utt(id="u2", speaker="spk-1", start=1.0, end=2.0),
        ],
    }

    result = N.normalize_to_session_input(payload)

    # "spk 1" and "spk-1" both normalize to SPK_1 -> single speaker.
    assert result.speakers == ["SPK_1"]
    assert {u.speaker for u in result.utterances} == {"SPK_1"}


def test_missing_optional_fields_apply_defaults() -> None:
    # One utterance with only the bare minimum; everything else defaulted.
    payload = {"utterances": [{"start": 0.0, "end": 1.0, "text": "hello world"}]}

    result = N.normalize_to_session_input(payload)

    assert result.session_id == "unknown_session"
    utt = result.utterances[0]
    assert utt.id == "utt_1"
    assert utt.speaker == "UNKNOWN"
    assert utt.word_count == 2  # derived from text
    assert utt.sentiment is None
    assert utt.emotion is None
    assert utt.overlap is False
    assert utt.confidence is None
    assert result.meta is not None and result.meta.language == "unknown"
    assert result.meta.source is None
    assert result.warnings == []
    assert result.speaker_stats == {}
    assert result.conversation_stats == {}
    assert result.duration_sec == 1.0  # inferred from max end


def test_extreme_values() -> None:
    payload = {
        "session_id": "s",
        "utterances": [
            _utt(
                id="u1",
                speaker="S",
                start=1_000_000_000.0,
                end=1_000_000_005.0,
                text="x " * 5000,
                word_count=1,
                sentiment={"label": "positive", "score": 1.0},  # boundary inclusive
                confidence=0.0,  # boundary inclusive
                diarization_confidence=1.0,
                emotion={"values": {"e": 0.0, "f": 1.0}},  # boundaries inclusive
            )
        ],
    }

    result = N.normalize_to_session_input(payload)

    utt = result.utterances[0]
    assert utt.start == 1_000_000_000.0
    assert utt.end == 1_000_000_005.0
    assert utt.sentiment is not None and utt.sentiment.score == 1.0
    assert utt.confidence == 0.0
    assert utt.diarization_confidence == 1.0
    assert utt.emotion is not None and utt.emotion.values == {"e": 0.0, "f": 1.0}
    assert result.duration_sec == 1_000_000_005.0


def test_utterances_sorted_by_start_end_id() -> None:
    payload = {
        "session_id": "s",
        "utterances": [
            {"id": "late", "speaker": "A", "start": 5.0, "end": 6.0, "text": "third"},
            {"id": "early", "speaker": "B", "start": 1.0, "end": 2.0, "text": "first"},
            {"id": "mid", "speaker": "C", "start": 1.0, "end": 3.0, "text": "second"},
        ],
    }

    result = N.normalize_to_session_input(payload)

    # Sorted by (start, end, id): early(1,2) -> mid(1,3) -> late(5,6).
    assert [u.text for u in result.utterances] == ["first", "second", "third"]


def test_utterance_ids_not_monotonic_after_sort() -> None:
    # Auto-ids are assigned by ORIGINAL position (utt_{idx+1}) BEFORE sorting.
    # After sort the surviving ids are therefore NOT necessarily increasing.
    # This pins that surprise so future readers don't "fix" it.
    payload = {
        "session_id": "s",
        "utterances": [
            {"speaker": "A", "start": 5.0, "end": 6.0, "text": "late"},  # -> utt_1
            {"speaker": "B", "start": 1.0, "end": 2.0, "text": "early"},  # -> utt_2
        ],
    }

    result = N.normalize_to_session_input(payload)
    ids = [u.id for u in result.utterances]

    assert ids == ["utt_2", "utt_1"]  # earlier-starting utterance keeps its later id
    assert ids != sorted(ids), "ids are intentionally NOT monotonic after time-sort"


def test_duration_explicit_vs_inferred() -> None:
    base = {"session_id": "s", "utterances": [_utt(start=0.0, end=6.0)]}

    # Explicit non-negative value is honored.
    assert N.normalize_to_session_input({**base, "duration_sec": 99.0}).duration_sec == 99.0
    # Negative value falls back to inferred max-end.
    assert N.normalize_to_session_input({**base, "duration_sec": -3.0}).duration_sec == 6.0
    # Non-numeric value falls back to inferred max-end.
    assert N.normalize_to_session_input({**base, "duration_sec": "abc"}).duration_sec == 6.0


def test_speakers_explicit_vs_inferred() -> None:
    utts = [_utt(id="u1", speaker="X", start=0.0, end=1.0), _utt(id="u2", speaker="Y", start=1.0, end=2.0)]

    # Explicit list wins and is normalized + deduped.
    explicit = N.normalize_to_session_input(
        {"session_id": "s", "speakers": ["Agent-1", "agent 1", "B"], "utterances": utts}
    )
    assert explicit.speakers == ["AGENT_1", "B"]

    # Non-list -> inferred from utterances.
    non_list = N.normalize_to_session_input({"session_id": "s", "speakers": "nope", "utterances": utts})
    assert non_list.speakers == ["X", "Y"]

    # Empty list -> inferred from utterances.
    empty = N.normalize_to_session_input({"session_id": "s", "speakers": [], "utterances": utts})
    assert empty.speakers == ["X", "Y"]


def test_malformed_meta_warnings_stats_fallback() -> None:
    payload = {
        "session_id": "s",
        "utterances": [_utt()],
        "meta": ["not", "a", "dict"],
        "warnings": "oops-not-a-list",
        "speaker_stats": [1, 2, 3],
        "conversation_stats": "nope",
    }

    result = N.normalize_to_session_input(payload)

    assert result.meta is not None and result.meta.language == "unknown"
    assert result.meta.source is None
    assert result.warnings == []
    assert result.speaker_stats == {}
    assert result.conversation_stats == {}


def test_word_count_bool_true_coerced_to_one() -> None:
    # Regression: bool is a subclass of int, so _normalize_word_count keeps
    # True (passes `isinstance(int) and >= 0`). Pydantic then coerces it.
    # Locks the contract so a future refactor can't silently change it.
    result = N.normalize_to_session_input({"session_id": "s", "utterances": [_utt(word_count=True)]})
    assert result.utterances[0].word_count == 1


def test_utterance_end_clamped_to_start_when_inverted() -> None:
    # end < start would fail SessionInput's time-order validator; the
    # normalizer clamps end up to start so the session still validates.
    result = N.normalize_to_session_input({"session_id": "s", "utterances": [_utt(start=5.0, end=2.0)]})
    utt = result.utterances[0]
    assert utt.start == 5.0
    assert utt.end == 5.0


# --------------------------------------------------------------------------- #
# Helper: _normalize_speaker                                                  #
# --------------------------------------------------------------------------- #


def test_normalize_speaker_none_and_empty_to_default() -> None:
    assert N._normalize_speaker(None) == "UNKNOWN"
    assert N._normalize_speaker("   ") == "UNKNOWN"


def test_normalize_speaker_uppercases_and_separators() -> None:
    assert N._normalize_speaker("spk-1 a") == "SPK_1_A"


def test_normalize_speaker_collapses_repeated_separators() -> None:
    assert N._normalize_speaker("a--  b") == "A_B"


# --------------------------------------------------------------------------- #
# Helper: _normalize_sentiment                                               #
# --------------------------------------------------------------------------- #


def test_normalize_sentiment_non_dict_returns_none() -> None:
    assert N._normalize_sentiment("positive") is None


def test_normalize_sentiment_invalid_label_no_score_returns_none() -> None:
    assert N._normalize_sentiment({"label": "angry"}) is None


def test_normalize_sentiment_keeps_label_drops_out_of_range_score() -> None:
    # Valid label survives even when the score is rejected for being >1.
    assert N._normalize_sentiment({"label": "positive", "score": 5.0}) == {"label": "positive", "score": None}


# --------------------------------------------------------------------------- #
# Helper: _normalize_emotion                                                 #
# --------------------------------------------------------------------------- #


def test_normalize_emotion_non_dict_or_bad_values_returns_none() -> None:
    assert N._normalize_emotion("happy") is None
    assert N._normalize_emotion({"values": "not-a-dict"}) is None


def test_normalize_emotion_drops_out_of_range_keeps_valid() -> None:
    result = N._normalize_emotion({"values": {"happy": 0.6, "bad": 5.0, None: 0.4}})
    assert result == {"values": {"happy": 0.6}}


def test_normalize_emotion_all_invalid_returns_none() -> None:
    assert N._normalize_emotion({"values": {"x": 5.0, "y": -1.0}}) is None


# --------------------------------------------------------------------------- #
# Helper: scalar coercers                                                    #
# --------------------------------------------------------------------------- #


def test_normalize_bool_truthy_falsy_and_garbage_default() -> None:
    assert N._normalize_bool(True) is True  # real-bool passthrough
    assert N._normalize_bool("yes") is True
    assert N._normalize_bool("no") is False
    assert N._normalize_bool(0) is False
    assert N._normalize_bool("maybe", default=True) is True  # garbage -> default


def test_normalize_probability_in_and_out_of_range() -> None:
    assert N._normalize_probability_or_none(0.123456) == 0.1235  # rounded to 4dp
    assert N._normalize_probability_or_none(1.5) is None
    assert N._normalize_probability_or_none("not-a-number") is None


def test_normalize_word_count_valid_int() -> None:
    assert N._normalize_word_count(5, "a b c") == 5


def test_normalize_word_count_fallback_to_text() -> None:
    assert N._normalize_word_count(None, "a b c") == 3
    assert N._normalize_word_count(-2, "a b") == 2  # negative int rejected -> text count


def test_normalize_utterances_non_list_returns_empty() -> None:
    assert N._normalize_utterances("not-a-list") == []
    assert N._normalize_utterances(None) == []


def test_normalize_text_none_and_whitespace_collapse() -> None:
    assert N._normalize_text(None) == ""
    assert N._normalize_text("a\n b\t\tc   d ") == "a b c d"


def test_normalize_session_id_none_and_whitespace() -> None:
    assert N._normalize_session_id(None) == "unknown_session"
    assert N._normalize_session_id("   ") == "unknown_session"


def test_normalize_non_negative_float_negative_and_garbage_use_default() -> None:
    # Baked-in default (caller does not supply a fallback value).
    assert N._normalize_non_negative_float(-1.0, default=7.0) == 7.0
    assert N._normalize_non_negative_float("abc", default=7.0) == 7.0
    assert N._normalize_non_negative_float(3.5) == 3.5


def test_normalize_optional_non_negative_float_negative_and_garbage_use_fallback() -> None:
    # Caller-supplied fallback (different semantics from the baked default above).
    assert N._normalize_optional_non_negative_float(-1.0, fallback=9.0) == 9.0
    assert N._normalize_optional_non_negative_float("xyz", fallback=9.0) == 9.0
    assert N._normalize_optional_non_negative_float(4.0, fallback=9.0) == 4.0
