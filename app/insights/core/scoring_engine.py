from __future__ import annotations

from typing import Dict, List

from app.insights.models.analytics_models import AnalyticsBundle
from app.insights.models.insight_models import (
    InsightScores,
    ScoreBreakdownItem,
    SpeakerInsight,
    TimelineMarker,
)
from app.insights.models.signal_models import AggregatedSignals


class InsightScoringEngine:
    @classmethod
    def compute_scores(
        cls,
        analytics: AnalyticsBundle,
        timeline: List[TimelineMarker],
        speaker_insights: Dict[str, SpeakerInsight],
        aggregated_signals: AggregatedSignals,
    ) -> InsightScores:
        dominance_score = cls._compute_dominance_score(analytics)

        # FIX: compute engagement ONCE and reuse components
        engagement_score, pause_component, question_component, trend_value = cls._compute_engagement_score_components(
            analytics, aggregated_signals
        )

        conflict_score = cls._compute_conflict_score(analytics, timeline)

        cooperation_score = cls._compute_cooperation_score(
            dominance_score=dominance_score,
            conflict_score=conflict_score,
            engagement_score=engagement_score,
        )

        emotion_volatility_score = cls._clamp(
            aggregated_signals.emotion_volatility_score
        )

        breakdown = {
            "dominance_score": [
                ScoreBreakdownItem(
                    component="max_speaking_ratio",
                    weight=1.0,
                    value=round(dominance_score, 3),
                    reason="Higher concentration of speaking time increases dominance score.",
                )
            ],
            "engagement_score": [
                ScoreBreakdownItem(
                    component="pause_behavior",
                    weight=0.7,
                    value=round(pause_component, 3),
                    reason="Shorter pauses and steadier turn flow improve engagement.",
                ),
                ScoreBreakdownItem(
                    component="question_behavior",
                    weight=0.3,
                    value=round(question_component, 3),
                    reason="More clarifying questions can indicate active engagement.",
                ),
                ScoreBreakdownItem(
                    component="sentiment_trend_adjustment",
                    weight=0.05,
                    value=round(trend_value, 3),
                    reason="Sentiment trend slightly adjusts engagement score.",
                ),
            ],
            "conflict_score": [
                ScoreBreakdownItem(
                    component="interruptions_and_overlaps",
                    weight=1.0,
                    value=round(conflict_score, 3),
                    reason="More interruptions and overlaps increase conflict score.",
                )
            ],
            "cooperation_score": [
                ScoreBreakdownItem(
                    component="balance_and_low_conflict",
                    weight=1.0,
                    value=round(cooperation_score, 3),
                    reason="Balanced participation with low conflict improves cooperation.",
                )
            ],
            "emotion_volatility_score": [
                ScoreBreakdownItem(
                    component="aggregated_signal_variation",
                    weight=1.0,
                    value=round(emotion_volatility_score, 3),
                    reason="Higher sentiment swings and more emotion switching increase volatility.",
                )
            ],
        }

        return InsightScores(
            dominance_score=dominance_score,
            engagement_score=engagement_score,
            conflict_score=conflict_score,
            cooperation_score=cooperation_score,
            emotion_volatility_score=emotion_volatility_score,
            breakdown=breakdown,
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, round(float(value), 4)))

    @classmethod
    def _compute_dominance_score(cls, analytics: AnalyticsBundle) -> float:
        if not analytics.speaker_metrics:
            return 0.0

        max_ratio = max(
            metric.speaking_ratio for metric in analytics.speaker_metrics.values()
        )
        return cls._clamp(max_ratio)

    @classmethod
    def _compute_engagement_score_components(
        cls,
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
    ):
        sm = analytics.session_metrics

        if sm.total_utterances == 0:
            return 0.0, 0.0, 0.0, 0.0

        pause_penalty = min(sm.avg_pause_sec / 5.0, 1.0)
        pause_component = 1.0 - pause_penalty

        question_component = min(
            sm.total_questions / max(sm.total_utterances, 1), 1.0
        )

        trend_bonus = 0.0
        trend = aggregated_signals.session_sentiment_trend

        if trend:
            if trend.direction == "improving":
                trend_bonus = 0.05
            elif trend.direction == "declining":
                trend_bonus = -0.05

        raw = 0.7 * pause_component + 0.3 * question_component + trend_bonus

        return cls._clamp(raw), pause_component, question_component, trend_bonus

    @classmethod
    def _compute_conflict_score(
        cls,
        analytics: AnalyticsBundle,
        timeline: List[TimelineMarker],
    ) -> float:
        total_interruptions = sum(
            metric.interruption_count for metric in analytics.speaker_metrics.values()
        )
        total_overlaps = sum(
            metric.overlap_count for metric in analytics.speaker_metrics.values()
        )
        utterances = max(analytics.session_metrics.total_utterances, 1)

        raw = (total_interruptions + total_overlaps) / utterances
        return cls._clamp(raw)

    @classmethod
    def _compute_cooperation_score(
        cls,
        *,
        dominance_score: float,
        conflict_score: float,
        engagement_score: float,
    ) -> float:
        raw = (
            (1.0 - dominance_score) * 0.35
            + (1.0 - conflict_score) * 0.40
            + engagement_score * 0.25
        )
        return cls._clamp(raw)