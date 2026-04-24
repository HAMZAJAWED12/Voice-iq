from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.insights.core.inconsistency_engine import InsightInconsistencyEngine
from app.insights.models.analytics_models import (
    AnalyticsBundle,
    SessionMetrics,
    SpeakerMetrics,
)
from app.insights.models.input_models import (
    EmotionInput,
    SentimentInput,
    SessionInput,
    UtteranceInput,
)
from app.insights.models.signal_models import AggregatedSignals


def _build_analytics(speakers=("S1", "S2")) -> AnalyticsBundle:
    speaker_metrics = {}
    for i, sp in enumerate(speakers):
        speaker_metrics[sp] = SpeakerMetrics(
            speaker=sp,
            speaking_time_sec=20.0,
            utterance_count=4,
            word_count=40,
            speaking_ratio=0.5,
            word_ratio=0.5,
            avg_utterance_length_words=10.0,
            avg_utterance_duration_sec=5.0,
            question_count=0,
            interruption_count=0,
            overlap_count=0,
            first_spoke_at_sec=float(i) * 2.0,
            last_spoke_at_sec=80.0 + float(i),
        )
    return AnalyticsBundle(
        session_metrics=SessionMetrics(
            total_duration_sec=100.0,
            total_speakers=len(speakers),
            total_utterances=8,
            total_words=80,
            avg_utterance_length_words=10.0,
            avg_utterance_duration_sec=5.0,
            total_questions=0,
            total_pauses=0,
            avg_pause_sec=0.0,
            max_pause_sec=0.0,
        ),
        speaker_metrics=speaker_metrics,
        pauses=[],
    )


def _utt(
    utt_id: str,
    speaker: str,
    start: float,
    end: float,
    text: str = "",
    sentiment_label=None,
    sentiment_score=None,
    emotion_values=None,
) -> UtteranceInput:
    sentiment = (
        SentimentInput(label=sentiment_label, score=sentiment_score)
        if sentiment_label is not None
        else None
    )
    emotion = EmotionInput(values=emotion_values or {}) if emotion_values else None
    return UtteranceInput(
        id=utt_id,
        speaker=speaker,
        start=start,
        end=end,
        text=text,
        word_count=max(1, len(text.split())),
        sentiment=sentiment,
        emotion=emotion,
    )


# --------------------------------------------------------------------------- #
# Edge-case tests: empty / neutral inputs must not raise and must score 0.
# --------------------------------------------------------------------------- #


def test_session_with_no_utterances_is_rejected_by_input_validation():
    # Defensive: SessionInput rejects empty utterance lists, so the engine is
    # never asked to handle that case. We assert the validation contract.
    with pytest.raises(ValidationError):
        SessionInput(session_id="s0", utterances=[])


def test_neutral_session_produces_no_inconsistency():
    session = SessionInput(
        session_id="s_neutral",
        utterances=[
            _utt("u1", "S1", 0.0, 1.0, "hello there", "neutral", 0.6),
            _utt("u2", "S2", 1.5, 2.5, "yes that sounds fine", "neutral", 0.6),
        ],
    )
    aggregated = AggregatedSignals()

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), aggregated
    )

    assert result.level == "none"
    assert result.score == 0.0
    assert result.signals == []
    assert result.windows == []


def test_session_with_missing_sentiment_and_emotion_is_safe():
    session = SessionInput(
        session_id="s_missing",
        utterances=[
            _utt("u1", "S1", 0.0, 1.0, ""),
            _utt("u2", "S2", 1.1, 2.0, ""),
        ],
    )
    aggregated = AggregatedSignals()

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), aggregated
    )

    assert result.level == "none"
    assert result.score == 0.0


# --------------------------------------------------------------------------- #
# Signal-specific tests
# --------------------------------------------------------------------------- #


def test_sentiment_text_mismatch_fires_when_label_disagrees_with_text():
    session = SessionInput(
        session_id="s_text",
        utterances=[
            _utt("u1", "S1", 0.0, 1.0, "this is terrible and awful", "positive", 0.9),
            _utt("u2", "S1", 1.5, 2.5, "I really hate this experience", "positive", 0.8),
            _utt("u3", "S2", 3.0, 4.0, "fantastic and wonderful work", "negative", 0.2),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )

    types = {s.signal_type for s in result.signals}
    assert "sentiment_text_mismatch" in types
    assert result.score > 0.0
    assert result.level in {"low", "medium", "high"}


def test_sentiment_emotion_contradiction_fires_when_polarities_clash():
    session = SessionInput(
        session_id="s_se",
        utterances=[
            _utt(
                "u1", "S1", 0.0, 1.0, "all good",
                "positive", 0.9,
                emotion_values={"angry": 0.8, "calm": 0.2},
            ),
            _utt(
                "u2", "S1", 1.5, 2.5, "fine fine",
                "positive", 0.85,
                emotion_values={"frustrated": 0.7, "calm": 0.3},
            ),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )
    types = {s.signal_type for s in result.signals}
    assert "sentiment_emotion_contradiction" in types
    assert result.primary_speaker == "S1"


def test_abrupt_emotional_reversal_fires_within_window():
    session = SessionInput(
        session_id="s_rev",
        utterances=[
            _utt(
                "u1", "S1", 0.0, 1.0, "ok",
                emotion_values={"happy": 0.9, "calm": 0.1},
            ),
            _utt(
                "u2", "S1", 2.0, 3.0, "fine",
                emotion_values={"angry": 0.85, "happy": 0.15},
            ),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )
    types = {s.signal_type for s in result.signals}
    assert "abrupt_emotional_reversal" in types

    # Window should be present and pin to S1.
    reversal_windows = [
        w for w in result.windows if "from_emotion" in w.evidence
    ]
    assert reversal_windows
    assert reversal_windows[0].speaker == "S1"


def test_abrupt_reversal_outside_window_does_not_fire():
    session = SessionInput(
        session_id="s_rev_far",
        utterances=[
            _utt(
                "u1", "S1", 0.0, 1.0, "ok",
                emotion_values={"happy": 0.9, "calm": 0.1},
            ),
            # Gap of >30s: should not be considered abrupt.
            _utt(
                "u2", "S1", 60.0, 61.0, "fine",
                emotion_values={"angry": 0.85, "happy": 0.15},
            ),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )
    types = {s.signal_type for s in result.signals}
    assert "abrupt_emotional_reversal" not in types


def test_contradictory_statements_fire_on_affirm_then_deny():
    session = SessionInput(
        session_id="s_contra",
        utterances=[
            _utt("u1", "S1", 0.0, 1.0, "yes I agree with that"),
            _utt("u2", "S1", 2.0, 3.0, "actually no I disagree"),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )
    types = {s.signal_type for s in result.signals}
    assert "contradictory_statements" in types


def test_masking_tone_fires_on_calm_emotion_with_negative_text():
    session = SessionInput(
        session_id="s_mask",
        utterances=[
            _utt(
                "u1", "S1", 0.0, 1.0, "this is terrible and awful",
                "negative", 0.2,
                emotion_values={"calm": 0.9, "neutral": 0.1},
            ),
            _utt(
                "u2", "S1", 1.5, 2.5, "I hate the broken process",
                "negative", 0.15,
                emotion_values={"calm": 0.8, "neutral": 0.2},
            ),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )
    types = {s.signal_type for s in result.signals}
    assert "masking_tone" in types


# --------------------------------------------------------------------------- #
# Aggregation contract: scores stay clamped, level mapping is consistent.
# --------------------------------------------------------------------------- #


def test_score_is_always_clamped_between_zero_and_one():
    # Build a worst-case session that triggers many signals at once.
    session = SessionInput(
        session_id="s_worst",
        utterances=[
            _utt(
                "u1", "S1", 0.0, 1.0,
                "yes I absolutely love this terrible awful broken experience",
                "positive", 0.9,
                emotion_values={"angry": 0.9, "calm": 0.1},
            ),
            _utt(
                "u2", "S1", 2.0, 3.0,
                "no I hate everything I lied earlier",
                "positive", 0.8,
                emotion_values={"calm": 0.9, "neutral": 0.1},
            ),
            _utt(
                "u3", "S1", 4.0, 5.0,
                "perfect wonderful amazing",
                "negative", 0.1,
                emotion_values={"frustrated": 0.85, "happy": 0.15},
            ),
        ],
    )

    result = InsightInconsistencyEngine.assess(
        session, _build_analytics(), AggregatedSignals()
    )

    assert 0.0 <= result.score <= 1.0
    assert result.level in {"none", "low", "medium", "high"}
    # All emitted signals must respect the per-signal cap discipline.
    for sig in result.signals:
        assert 0.0 <= sig.score <= 1.0
