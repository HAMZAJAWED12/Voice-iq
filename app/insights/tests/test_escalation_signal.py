from app.insights.core.escalation_engine import InsightEscalationEngine
from app.insights.models.analytics_models import (
    AnalyticsBundle,
    SessionMetrics,
    SpeakerMetrics,
)
from app.insights.models.input_models import SessionInput, UtteranceInput
from app.insights.models.insight_models import TimelineMarker
from app.insights.models.signal_models import (
    AggregatedSignals,
    SentimentAggregate,
    SessionSentimentTrend,
)


def build_analytics(*, with_tension: bool = True):
    """Helper for building a baseline AnalyticsBundle.

    The `with_tension` flag controls whether the speakers are loaded with
    interruption / overlap counts. Tests that exercise the "calm / stable"
    path should pass `with_tension=False` so the turn-tension signal does
    not fire spuriously.
    """
    s1_interruptions, s1_overlaps = (2, 1) if with_tension else (0, 0)
    s2_interruptions, s2_overlaps = (1, 1) if with_tension else (0, 0)

    return AnalyticsBundle(
        session_metrics=SessionMetrics(
            total_duration_sec=100.0,
            total_speakers=2,
            total_utterances=10,
            total_words=100,
            avg_utterance_length_words=10.0,
            avg_utterance_duration_sec=5.0,
            total_questions=1,
            total_pauses=2,
            avg_pause_sec=1.0,
            max_pause_sec=2.0,
        ),
        speaker_metrics={
            "S1": SpeakerMetrics(
                speaker="S1",
                speaking_time_sec=60.0,
                utterance_count=6,
                word_count=60,
                speaking_ratio=0.6,
                word_ratio=0.6,
                avg_utterance_length_words=10.0,
                avg_utterance_duration_sec=5.0,
                question_count=1,
                interruption_count=s1_interruptions,
                overlap_count=s1_overlaps,
                first_spoke_at_sec=0.0,
                last_spoke_at_sec=90.0,
            ),
            "S2": SpeakerMetrics(
                speaker="S2",
                speaking_time_sec=40.0,
                utterance_count=4,
                word_count=40,
                speaking_ratio=0.4,
                word_ratio=0.4,
                avg_utterance_length_words=10.0,
                avg_utterance_duration_sec=5.0,
                question_count=0,
                interruption_count=s2_interruptions,
                overlap_count=s2_overlaps,
                first_spoke_at_sec=5.0,
                last_spoke_at_sec=95.0,
            ),
        },
        pauses=[],
    )


def test_declining_trend_creates_escalation():
    aggregated = AggregatedSignals(
        session_sentiment=SentimentAggregate(
            label="negative",
            avg_score=0.32,
            distribution={"negative": 6, "neutral": 2},
            sample_count=8,
        ),
        session_sentiment_trend=SessionSentimentTrend(
            direction="declining",
            slope=-0.22,
            points=[],
        ),
    )

    timeline = [
        TimelineMarker(
            marker_id="m1",
            type="emotional_shift",
            time_sec=10.0,
            severity="medium",
            reason="shift",
        ),
        TimelineMarker(
            marker_id="m2",
            type="engagement_drop",
            time_sec=20.0,
            severity="medium",
            reason="pause",
        ),
        TimelineMarker(
            marker_id="m3",
            type="high_tension",
            time_sec=30.0,
            severity="high",
            reason="tension",
        ),
    ]

    session = SessionInput(
        session_id="s1",
        utterances=[
            UtteranceInput(
                id="u1",
                speaker="S1",
                start=0.0,
                end=1.0,
                text="Test utterance",
                word_count=2,
            )
        ],
    )

    result = InsightEscalationEngine.assess(
        session,
        build_analytics(),
        aggregated,
        timeline,
    )

    assert result.level in {"mild", "moderate", "severe"}
    assert result.score > 0.0
    assert len(result.signals) > 0


def test_stable_session_produces_no_escalation():
    aggregated = AggregatedSignals(
        session_sentiment=SentimentAggregate(
            label="neutral",
            avg_score=0.78,
            distribution={"neutral": 8, "positive": 2},
            sample_count=10,
        ),
        session_sentiment_trend=SessionSentimentTrend(
            direction="stable",
            slope=0.01,
            points=[],
        ),
    )

    session = SessionInput(
        session_id="s2",
        utterances=[
            UtteranceInput(
                id="u1",
                speaker="S1",
                start=0.0,
                end=1.0,
                text="Stable utterance",
                word_count=2,
            )
        ],
    )

    result = InsightEscalationEngine.assess(
        session,
        build_analytics(with_tension=False),
        aggregated,
        [],
    )

    assert result.level == "none"
    assert result.score == 0.0
