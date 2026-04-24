from __future__ import annotations

from typing import Dict, List

from app.insights.config.defaults import DEFAULT_THRESHOLDS, InsightThresholds
from app.insights.core.escalation_engine import InsightEscalationEngine
from app.insights.core.inconsistency_engine import InsightInconsistencyEngine
from app.insights.core.scoring_engine import InsightScoringEngine
from app.insights.core.signal_aggregation import SignalAggregationEngine
from app.insights.core.timeline_engine import InsightTimelineEngine
from app.insights.models.analytics_models import AnalyticsBundle
from app.insights.models.inconsistency_models import InconsistencyAssessment
from app.insights.models.input_models import SessionInput
from app.insights.models.insight_models import (
    InsightBundle,
    InsightFlag,
    SpeakerInsight,
    TimelineMarker,
)
from app.insights.models.signal_models import AggregatedSignals


class InsightRuleEngine:
    @classmethod
    def run(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        *,
        threshold_profile: str | None = None,
        thresholds: InsightThresholds | None = None,
    ) -> InsightBundle:
        thresholds = thresholds or DEFAULT_THRESHOLDS

        aggregated_signals = SignalAggregationEngine.aggregate(session.utterances)

        speaker_insights = cls._build_speaker_insights(
            session,
            analytics,
            aggregated_signals,
            thresholds,
        )

        # Timeline must be built BEFORE escalation, because the escalation
        # engine reads marker clusters from it. (Previously this was inverted
        # which raised NameError at runtime.)
        timeline = InsightTimelineEngine.build_timeline(
            session,
            analytics,
            aggregated_signals,
            thresholds,
        )

        escalation = InsightEscalationEngine.assess(
            session,
            analytics,
            aggregated_signals,
            timeline,
        )

        inconsistency = InsightInconsistencyEngine.assess(
            session,
            analytics,
            aggregated_signals,
        )

        # Add inconsistency-derived markers to the timeline, then re-sort so
        # downstream consumers see a consistent chronological view.
        inconsistency_markers = cls._build_inconsistency_markers(inconsistency)
        if inconsistency_markers:
            timeline = sorted(
                timeline + inconsistency_markers,
                key=lambda m: (m.time_sec, m.marker_id),
            )

        session_flags = cls._build_session_flags(
            analytics,
            aggregated_signals,
            thresholds,
            escalation,
            inconsistency,
        )

        scores = InsightScoringEngine.compute_scores(
            analytics,
            timeline,
            speaker_insights,
            aggregated_signals,
        )

        return InsightBundle(
            session_sentiment=aggregated_signals.session_sentiment,
            session_emotion=aggregated_signals.session_emotion,
            session_sentiment_trend=aggregated_signals.session_sentiment_trend,
            speaker_insights=speaker_insights,
            scores=scores,
            flags=session_flags,
            timeline=timeline,
            escalation=escalation,
            inconsistency=inconsistency,
        )

    @classmethod
    def _build_speaker_insights(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
        thresholds: InsightThresholds,
    ) -> Dict[str, SpeakerInsight]:
        result: Dict[str, SpeakerInsight] = {}

        for speaker, metric in analytics.speaker_metrics.items():
            flags: List[InsightFlag] = []

            if metric.interruption_count >= thresholds.frequent_interruptions_threshold:
                flags.append(
                    InsightFlag(
                        type="frequent_interruptions",
                        speaker=speaker,
                        severity="medium",
                        reason=f"{speaker} shows repeated interruption behavior.",
                        evidence={
                            "interruption_count": metric.interruption_count,
                            "speaking_ratio": round(metric.speaking_ratio, 3),
                        },
                    )
                )

            if metric.overlap_count >= thresholds.high_overlap_participation_threshold:
                flags.append(
                    InsightFlag(
                        type="high_overlap_participation",
                        speaker=speaker,
                        severity="medium",
                        reason=f"{speaker} is frequently involved in overlapping turns.",
                        evidence={
                            "overlap_count": metric.overlap_count,
                        },
                    )
                )

            if (
                metric.question_count == 0
                and metric.utterance_count >= thresholds.low_inquiry_min_utterances
            ):
                flags.append(
                    InsightFlag(
                        type="low_inquiry_behavior",
                        speaker=speaker,
                        severity="low",
                        reason=f"{speaker} asks very few or no questions.",
                        evidence={
                            "question_count": metric.question_count,
                            "utterance_count": metric.utterance_count,
                        },
                    )
                )

            speaker_emotion = aggregated_signals.speaker_emotion.get(speaker)
            if (
                speaker_emotion
                and speaker_emotion.dominant in {"angry", "frustrated", "anxious", "upset"}
                and speaker_emotion.sample_count >= 2
            ):
                flags.append(
                    InsightFlag(
                        type="speaker_emotional_strain",
                        speaker=speaker,
                        severity="low",
                        reason=f"{speaker} shows repeated {speaker_emotion.dominant} emotional signals.",
                        evidence={
                            "dominant_emotion": speaker_emotion.dominant,
                            "emotion_sample_count": speaker_emotion.sample_count,
                        },
                    )
                )

            result[speaker] = SpeakerInsight(
                speaker=speaker,
                sentiment=aggregated_signals.speaker_sentiment.get(speaker),
                emotion=aggregated_signals.speaker_emotion.get(speaker),
                dominance_ratio=round(metric.speaking_ratio, 4),
                engagement_ratio=round(metric.word_ratio, 4),
                flags=flags,
            )
            

        return result

    @classmethod
    def _build_session_flags(
        cls,
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
        thresholds: InsightThresholds,
        escalation=None,
        inconsistency: InconsistencyAssessment | None = None,
    ) -> List[InsightFlag]:
        flags: List[InsightFlag] = []

        for speaker, metric in analytics.speaker_metrics.items():
            is_dominant = (
                metric.speaking_ratio >= thresholds.dominance_speaking_ratio_threshold
                or metric.word_ratio >= thresholds.dominance_word_ratio_threshold
            )

            if is_dominant:
                severity = (
                    "high"
                    if (metric.speaking_ratio >= 0.75 or metric.word_ratio >= 0.75)
                    else "medium"
                )
                flags.append(
                    InsightFlag(
                        type="speaker_dominance",
                        speaker=speaker,
                        severity=severity,
                        reason=f"{speaker} appears to dominate the conversation.",
                        evidence={
                            "speaking_ratio": round(metric.speaking_ratio, 3),
                            "word_ratio": round(metric.word_ratio, 3),
                            "utterance_count": metric.utterance_count,
                        },
                    )
                )

            if (
                metric.interruption_count >= thresholds.high_tension_interruption_threshold
                or metric.overlap_count >= thresholds.high_tension_overlap_threshold
            ):
                severity = (
                    "high"
                    if (metric.interruption_count >= 4 or metric.overlap_count >= 4)
                    else "medium"
                )
                flags.append(
                    InsightFlag(
                        type="high_tension",
                        speaker=speaker,
                        severity=severity,
                        reason=(
                            f"{speaker} is involved in repeated interruptions or overlaps, "
                            "which may indicate elevated conversational tension."
                        ),
                        evidence={
                            "interruption_count": metric.interruption_count,
                            "overlap_count": metric.overlap_count,
                        },
                    )
                )

        session_sentiment = aggregated_signals.session_sentiment
        trend = aggregated_signals.session_sentiment_trend

        if (
            session_sentiment
            and session_sentiment.label == "negative"
            and session_sentiment.avg_score is not None
            and session_sentiment.avg_score <= 0.35
        ):
            flags.append(
                InsightFlag(
                    type="negative_session_mood",
                    severity="medium",
                    reason="The session shows a strongly negative overall sentiment pattern.",
                    evidence={
                        "label": session_sentiment.label,
                        "avg_score": round(session_sentiment.avg_score, 4),
                        "sample_count": session_sentiment.sample_count,
                    },
                )
            )

        if trend and trend.direction == "declining":
            flags.append(
                InsightFlag(
                    type="declining_session_tone",
                    severity="low",
                    reason="Session sentiment trends downward across the interaction.",
                    evidence={
                        "direction": trend.direction,
                        "slope": trend.slope if trend.slope is not None else 0.0,
                    },
                )
            )



        if escalation is not None and escalation.level in {"mild", "moderate", "severe"}:
            severity_map = {
                "mild": "low",
                "moderate": "medium",
                "severe": "high",
            }
            flags.append(
                InsightFlag(
                    type="conversation_escalation",
                    severity=severity_map[escalation.level],
                    reason=escalation.summary,
                    evidence={
                        "escalation_level": escalation.level,
                        "escalation_score": escalation.score,
                        "primary_speaker": escalation.primary_speaker or "",
                    },
                )
            )

        if inconsistency is not None and inconsistency.level in {"low", "medium", "high"}:
            severity_map = {"low": "low", "medium": "medium", "high": "high"}
            flags.append(
                InsightFlag(
                    type="conversation_inconsistency",
                    severity=severity_map[inconsistency.level],
                    reason=inconsistency.summary,
                    evidence={
                        "inconsistency_level": inconsistency.level,
                        "inconsistency_score": inconsistency.score,
                        "primary_speaker": inconsistency.primary_speaker or "",
                        "signal_count": len(inconsistency.signals),
                    },
                )
            )

        return flags

    @classmethod
    def _build_inconsistency_markers(
        cls,
        inconsistency: InconsistencyAssessment | None,
    ) -> List[TimelineMarker]:
        """Surface inconsistency windows as `inconsistency_candidate` markers.

        Only fire markers when the overall level is at least "low" to avoid
        noisy timeline output from isolated / ambiguous signals.
        """
        markers: List[TimelineMarker] = []
        if inconsistency is None or inconsistency.level == "none":
            return markers

        severity_map = {"low": "low", "medium": "medium", "high": "high"}

        for idx, window in enumerate(inconsistency.windows, start=1):
            severity = severity_map.get(window.level, "low")
            evidence = {k: v for k, v in window.evidence.items()}
            evidence.setdefault("window_level", window.level)

            markers.append(
                TimelineMarker(
                    marker_id=f"inconsistency_{idx}",
                    type="inconsistency_candidate",
                    time_sec=window.start_sec,
                    speaker=window.speaker,
                    severity=severity,
                    reason=window.reason,
                    start_sec=window.start_sec,
                    end_sec=window.end_sec,
                    evidence=evidence,
                )
            )

        return markers
