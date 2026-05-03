from app.insights.core.signal_aggregation import SignalAggregationEngine
from app.insights.models.input_models import EmotionInput, SentimentInput, UtteranceInput


def build_utterance(
    utt_id: str,
    speaker: str,
    start: float,
    end: float,
    sentiment_label=None,
    sentiment_score=None,
    emotion_values=None,
):
    return UtteranceInput(
        id=utt_id,
        speaker=speaker,
        start=start,
        end=end,
        text="sample text",
        word_count=2,
        sentiment=SentimentInput(label=sentiment_label, score=sentiment_score)
        if sentiment_label is not None and sentiment_score is not None
        else None,
        emotion=EmotionInput(values=emotion_values or {}) if emotion_values else None,
    )


def test_aggregate_session_sentiment_and_speaker_sentiment():
    utterances = [
        build_utterance("u1", "S1", 0.0, 1.0, "positive", 0.8),
        build_utterance("u2", "S1", 1.1, 2.0, "neutral", 0.5),
        build_utterance("u3", "S2", 2.1, 3.0, "negative", 0.2),
    ]

    result = SignalAggregationEngine.aggregate(utterances)

    assert result.session_sentiment.sample_count == 3
    assert result.session_sentiment.label in {"positive", "neutral", "negative"}
    assert "S1" in result.speaker_sentiment
    assert "S2" in result.speaker_sentiment


def test_aggregate_session_emotion_and_speaker_emotion():
    utterances = [
        build_utterance("u1", "S1", 0.0, 1.0, emotion_values={"calm": 0.8, "angry": 0.2}),
        build_utterance("u2", "S1", 1.1, 2.0, emotion_values={"calm": 0.7, "frustrated": 0.3}),
        build_utterance("u3", "S2", 2.1, 3.0, emotion_values={"angry": 0.6, "calm": 0.4}),
    ]

    result = SignalAggregationEngine.aggregate(utterances)

    assert result.session_emotion.sample_count == 3
    assert result.session_emotion.dominant is not None
    assert "S1" in result.speaker_emotion
    assert "S2" in result.speaker_emotion


def test_sentiment_trend_detects_decline():
    utterances = [
        build_utterance("u1", "S1", 0.0, 1.0, "positive", 0.9),
        build_utterance("u2", "S1", 1.1, 2.0, "neutral", 0.6),
        build_utterance("u3", "S2", 2.1, 3.0, "negative", 0.2),
    ]

    result = SignalAggregationEngine.aggregate(utterances)

    assert result.session_sentiment_trend.direction == "declining"


def test_missing_signals_fail_soft():
    utterances = [
        build_utterance("u1", "S1", 0.0, 1.0),
        build_utterance("u2", "S2", 1.1, 2.0),
    ]

    result = SignalAggregationEngine.aggregate(utterances)

    assert result.session_sentiment.sample_count == 0
    assert result.session_emotion.sample_count == 0
    assert result.emotion_volatility_score == 0.0
