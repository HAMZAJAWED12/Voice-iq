from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from app.insights.models.analytics_models import AnalyticsBundle
from app.insights.models.escalation_models import (
    EscalationAssessment,
    EscalationSignal,
    EscalationWindow,
)
from app.insights.models.input_models import SessionInput
from app.insights.models.insight_models import TimelineMarker
from app.insights.models.signal_models import AggregatedSignals


class InsightEscalationEngine:
    NEGATIVE_LABELS = {"negative"}
    STRAIN_EMOTIONS = {"angry", "frustrated", "anxious", "upset", "fear", "sad"}

    @classmethod
    def assess(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
        timeline: List[TimelineMarker],
    ) -> EscalationAssessment:
        signals: List[EscalationSignal] = []
        windows: List[EscalationWindow] = []

        trend_signal = cls._detect_declining_trend(aggregated_signals)
        if trend_signal:
            signals.append(trend_signal)

        negative_density_signal = cls._detect_negative_density(aggregated_signals)
        if negative_density_signal:
            signals.append(negative_density_signal)

        strain_signal = cls._detect_speaker_emotional_strain(aggregated_signals)
        if strain_signal:
            signals.append(strain_signal)

        interruption_signal = cls._detect_turn_tension(analytics)
        if interruption_signal:
            signals.append(interruption_signal)

        marker_signal, marker_windows = cls._detect_marker_clusters(timeline)
        if marker_signal:
            signals.append(marker_signal)
        windows.extend(marker_windows)

        score = round(sum(signal.score for signal in signals), 4)
        score = max(0.0, min(1.0, score))

        level = cls._score_to_level(score)
        primary_speaker = cls._infer_primary_speaker(
            aggregated_signals,
            analytics,
            signals,
        )
        summary = cls._build_summary(level, signals, primary_speaker)

        return EscalationAssessment(
            level=level,
            score=score,
            signals=signals,
            windows=windows,
            primary_speaker=primary_speaker,
            summary=summary,
        )

    @classmethod
    def _detect_declining_trend(
        cls,
        aggregated_signals: AggregatedSignals,
    ) -> Optional[EscalationSignal]:
        trend = aggregated_signals.session_sentiment_trend
        if not trend or trend.direction != "declining":
            return None

        slope = abs(trend.slope or 0.0)
        score = min(0.30, 0.12 + slope)

        return EscalationSignal(
            signal_type="declining_session_trend",
            severity="medium" if score < 0.22 else "high",
            score=round(score, 4),
            reason="Session sentiment trends downward across the interaction.",
            evidence={
                "trend_direction": trend.direction,
                "trend_slope": round(trend.slope or 0.0, 4),
            },
        )

    @classmethod
    def _detect_negative_density(
        cls,
        aggregated_signals: AggregatedSignals,
    ) -> Optional[EscalationSignal]:
        sentiment = aggregated_signals.session_sentiment
        if not sentiment or sentiment.sample_count == 0:
            return None

        negative_count = sentiment.distribution.get("negative", 0)
        ratio = negative_count / max(sentiment.sample_count, 1)

        if ratio < 0.15:
            return None

        score = min(0.25, 0.10 + ratio)

        return EscalationSignal(
            signal_type="negative_sentiment_density",
            severity="medium" if ratio < 0.30 else "high",
            score=round(score, 4),
            reason="Negative sentiment appears repeatedly across the session.",
            evidence={
                "negative_count": negative_count,
                "sample_count": sentiment.sample_count,
                "negative_ratio": round(ratio, 4),
            },
        )

    @classmethod
    def _detect_speaker_emotional_strain(
        cls,
        aggregated_signals: AggregatedSignals,
    ) -> Optional[EscalationSignal]:
        best_speaker = None
        best_score = 0.0
        best_emotion = None
        best_samples = 0

        for speaker, emotion in aggregated_signals.speaker_emotion.items():
            if not emotion or emotion.sample_count == 0 or not emotion.dominant:
                continue

            if emotion.dominant not in cls.STRAIN_EMOTIONS:
                continue

            dominant_ratio = emotion.distribution.get(emotion.dominant, 0.0)
            score = min(0.25, 0.08 + dominant_ratio * 0.25)

            if score > best_score:
                best_score = score
                best_speaker = speaker
                best_emotion = emotion.dominant
                best_samples = emotion.sample_count

        if not best_speaker:
            return None

        return EscalationSignal(
            signal_type="speaker_emotional_strain",
            severity="medium" if best_score < 0.20 else "high",
            score=round(best_score, 4),
            reason=f"{best_speaker} shows repeated emotionally strained signals.",
            evidence={
                "speaker": best_speaker,
                "dominant_emotion": best_emotion or "",
                "sample_count": best_samples,
            },
        )

    @classmethod
    def _detect_turn_tension(
        cls,
        analytics: AnalyticsBundle,
    ) -> Optional[EscalationSignal]:
        total_interruptions = sum(
            metric.interruption_count for metric in analytics.speaker_metrics.values()
        )
        total_overlaps = sum(
            metric.overlap_count for metric in analytics.speaker_metrics.values()
        )

        raw = total_interruptions + total_overlaps
        if raw <= 0:
            return None

        score = min(0.30, raw / max(analytics.session_metrics.total_utterances, 1))

        return EscalationSignal(
            signal_type="turn_tension",
            severity="medium" if score < 0.20 else "high",
            score=round(score, 4),
            reason="Interruptions and overlaps indicate rising turn-taking tension.",
            evidence={
                "interruptions": total_interruptions,
                "overlaps": total_overlaps,
            },
        )

    @classmethod
    def _detect_marker_clusters(
        cls,
        timeline: List[TimelineMarker],
    ) -> tuple[Optional[EscalationSignal], List[EscalationWindow]]:
        relevant = [
            m
            for m in timeline
            if m.type in {
                "high_tension",
                "emotional_shift",
                "interruption",
                "engagement_drop",
            }
        ]

        if len(relevant) < 3:
            return None, []

        relevant = sorted(relevant, key=lambda m: m.time_sec)
        windows: List[EscalationWindow] = []
        cluster_count = 0

        for i in range(len(relevant) - 2):
            a, b, c = relevant[i], relevant[i + 1], relevant[i + 2]
            if c.time_sec - a.time_sec <= 45.0:
                cluster_count += 1
                windows.append(
                    EscalationWindow(
                        start_sec=a.time_sec,
                        end_sec=c.time_sec,
                        level="moderate",
                        primary_speaker=a.speaker or b.speaker or c.speaker,
                        reason="Multiple risk markers occur in a short time window.",
                        evidence={
                            "marker_count": 3,
                            "window_span_sec": round(c.time_sec - a.time_sec, 3),
                        },
                    )
                )

        if cluster_count == 0:
            return None, []

        score = min(0.25, 0.08 * cluster_count)

        return (
            EscalationSignal(
                signal_type="marker_cluster_density",
                severity="medium" if score < 0.20 else "high",
                score=round(score, 4),
                reason="Multiple tension-related markers cluster within short windows.",
                evidence={
                    "cluster_count": cluster_count,
                },
            ),
            windows,
        )

    @classmethod
    def _infer_primary_speaker(
        cls,
        aggregated_signals: AggregatedSignals,
        analytics: AnalyticsBundle,
        signals: List[EscalationSignal],
    ) -> Optional[str]:
        speaker_scores: Dict[str, float] = defaultdict(float)

        for signal in signals:
            speaker = signal.evidence.get("speaker")
            if isinstance(speaker, str):
                speaker_scores[speaker] += signal.score

        if speaker_scores:
            return max(speaker_scores, key=speaker_scores.get)

        if analytics.speaker_metrics:
            return max(
                analytics.speaker_metrics.values(),
                key=lambda m: m.speaking_ratio,
            ).speaker

        return None

    @staticmethod
    def _score_to_level(score: float) -> str:
        if score >= 0.70:
            return "severe"
        if score >= 0.45:
            return "moderate"
        if score >= 0.20:
            return "mild"
        return "none"

    @staticmethod
    def _build_summary(
        level: str,
        signals: List[EscalationSignal],
        primary_speaker: Optional[str],
    ) -> str:
        if level == "none":
            return "No clear escalation pattern was detected."

        signal_names = ", ".join(signal.signal_type for signal in signals[:3])
        speaker_part = (
            f" Primary speaker of concern: {primary_speaker}."
            if primary_speaker
            else ""
        )
        return (
            f"Escalation level is {level}, driven by {signal_names}."
            f"{speaker_part}"
        )