"""The pipeline's sentiment reaches the Insight Service as positivity.

SentimentService reports a label and the model's confidence in it; the
Insight Service reads the score as positivity (0 negative, 0.5 neutral,
1 positive). The adapter maps one onto the other.
"""

from __future__ import annotations

import pytest

from app.insights.adapters import VoiceIQInsightAdapter


def _sentiment(label, score):
    session = VoiceIQInsightAdapter.from_orchestrator(
        session_id="s1",
        asr_meta={},
        speaker_segments=[
            {"speaker": "S1", "start": 0.0, "end": 1.0, "text": "fine", "sentiment": label, "sentiment_score": score}
        ],
        speaker_stats={},
        conversation_stats={},
        warnings=[],
    )
    return session.utterances[0].sentiment


@pytest.mark.parametrize(
    ("label", "confidence", "positivity"),
    [
        ("positive", 0.9, 0.95),
        ("positive", 0.0, 0.5),
        ("positive", 1.0, 1.0),
        ("negative", 0.9, 0.05),
        ("negative", 1.0, 0.0),
        ("neutral", 0.8, 0.5),
        ("neutral", 0.0, 0.5),
    ],
)
def test_confidence_in_a_label_becomes_positivity(label: str, confidence: float, positivity: float) -> None:
    sentiment = _sentiment(label, confidence)

    assert sentiment is not None
    assert sentiment.score == pytest.approx(positivity)


def test_a_very_negative_utterance_scores_below_a_mildly_negative_one() -> None:
    """Before the mapping, a confident negative scored *higher* than a hesitant one."""
    assert _sentiment("negative", 0.95).score < _sentiment("negative", 0.55).score < 0.5


@pytest.mark.parametrize(
    ("label", "confidence"),
    [
        ("positive", None),
        ("negative", float("nan")),
        ("positive", 1.5),
        ("negative", -0.1),
        ("positive", True),
        ("positive", "0.9"),
        (None, 0.9),
    ],
)
def test_no_usable_label_or_confidence_gives_no_score(label, confidence) -> None:
    sentiment = _sentiment(label, confidence)

    assert sentiment is None or sentiment.score is None
