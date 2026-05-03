from __future__ import annotations

from app.insights.config.defaults import InsightThresholds
from app.insights.models.analytics_models import AnalyticsBundle
from app.insights.models.input_models import SessionInput, UtteranceInput
from app.insights.models.insight_models import TimelineMarker
from app.insights.models.signal_models import AggregatedSignals


class InsightTimelineEngine:
    @classmethod
    def build_timeline(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
        thresholds: InsightThresholds,
    ) -> list[TimelineMarker]:
        utterances = sorted(session.utterances, key=lambda u: (u.start, u.end, u.id))

        markers: list[TimelineMarker] = []
        markers.extend(cls._detect_dominance_markers(analytics, thresholds))
        markers.extend(cls._detect_pause_markers(utterances, thresholds))
        markers.extend(cls._detect_interruption_markers(utterances))
        markers.extend(
            cls._detect_emotional_shift_markers(
                utterances,
                thresholds,
                aggregated_signals,
            )
        )
        markers.extend(cls._detect_high_tension_markers(analytics, thresholds))

        markers.sort(key=lambda m: (m.time_sec, m.marker_id))
        return markers

    @classmethod
    def _detect_dominance_markers(
        cls,
        analytics: AnalyticsBundle,
        thresholds: InsightThresholds,
    ) -> list[TimelineMarker]:
        markers: list[TimelineMarker] = []

        for speaker, metric in analytics.speaker_metrics.items():
            is_dominant = (
                metric.speaking_ratio >= thresholds.dominance_speaking_ratio_threshold
                or metric.word_ratio >= thresholds.dominance_word_ratio_threshold
            )
            if not is_dominant:
                continue

            severity = "high" if (metric.speaking_ratio >= 0.75 or metric.word_ratio >= 0.75) else "medium"

            markers.append(
                TimelineMarker(
                    marker_id=f"dominance_{speaker}",
                    type="dominance_window",
                    time_sec=metric.first_spoke_at_sec or 0.0,
                    speaker=speaker,
                    severity=severity,
                    reason=f"{speaker} shows dominant participation across the session.",
                    start_sec=metric.first_spoke_at_sec,
                    end_sec=metric.last_spoke_at_sec,
                    evidence={
                        "speaking_ratio": round(metric.speaking_ratio, 3),
                        "word_ratio": round(metric.word_ratio, 3),
                    },
                )
            )

        return markers

    @classmethod
    def _detect_pause_markers(
        cls,
        utterances: list[UtteranceInput],
        thresholds: InsightThresholds,
    ) -> list[TimelineMarker]:
        markers: list[TimelineMarker] = []
        prev: UtteranceInput | None = None
        index = 0

        for utt in utterances:
            if prev is not None:
                gap = utt.start - prev.end
                if gap >= thresholds.engagement_drop_pause_threshold_sec:
                    index += 1
                    severity = "high" if gap >= thresholds.severe_engagement_drop_pause_threshold_sec else "medium"

                    markers.append(
                        TimelineMarker(
                            marker_id=f"engagement_drop_{index}",
                            type="engagement_drop",
                            time_sec=prev.end,
                            speaker=utt.speaker,
                            severity=severity,
                            reason=(f"Long pause of {gap:.2f}s may indicate disengagement " "or response hesitation."),
                            start_sec=prev.end,
                            end_sec=utt.start,
                            evidence={
                                "pause_duration_sec": round(gap, 3),
                                "speaker_before": prev.speaker,
                                "speaker_after": utt.speaker,
                            },
                        )
                    )

            prev = utt

        return markers

    @classmethod
    def _detect_interruption_markers(
        cls,
        utterances: list[UtteranceInput],
    ) -> list[TimelineMarker]:
        markers: list[TimelineMarker] = []
        prev: UtteranceInput | None = None
        index = 0

        for current in utterances:
            if prev is not None:
                different_speaker = prev.speaker != current.speaker
                overlap = current.start < prev.end

                if different_speaker and overlap:
                    index += 1
                    overlap_amount = round(prev.end - current.start, 3)

                    markers.append(
                        TimelineMarker(
                            marker_id=f"interruption_{index}",
                            type="interruption",
                            time_sec=current.start,
                            speaker=current.speaker,
                            severity="medium",
                            reason=f"{current.speaker} begins before {prev.speaker} finished speaking.",
                            start_sec=current.start,
                            end_sec=current.end,
                            evidence={
                                "interrupted_speaker": prev.speaker,
                                "overlap_sec": overlap_amount,
                                "utterance_id": current.id,
                            },
                        )
                    )

            prev = current

        return markers

    @classmethod
    def _detect_emotional_shift_markers(
        cls,
        utterances: list[UtteranceInput],
        thresholds: InsightThresholds,
        aggregated_signals: AggregatedSignals,
    ) -> list[TimelineMarker]:
        markers: list[TimelineMarker] = []
        previous_by_speaker: dict[str, UtteranceInput] = {}
        index = 0

        # Small changes below this are ignored to reduce noisy timeline output.
        minimum_shift_delta = 0.06

        for utt in utterances:
            if not utt.sentiment or utt.sentiment.score is None:
                previous_by_speaker[utt.speaker] = utt
                continue

            prev = previous_by_speaker.get(utt.speaker)
            if prev and prev.sentiment and prev.sentiment.score is not None:
                raw_delta = utt.sentiment.score - prev.sentiment.score
                delta = abs(raw_delta)

                label_changed = (
                    utt.sentiment.label != prev.sentiment.label
                    if utt.sentiment.label and prev.sentiment.label
                    else False
                )

                if delta < minimum_shift_delta and not label_changed:
                    previous_by_speaker[utt.speaker] = utt
                    continue

                if delta >= thresholds.emotional_shift_delta_threshold or label_changed:
                    index += 1
                    severity = "high" if delta >= thresholds.severe_emotional_shift_delta_threshold else "medium"

                    markers.append(
                        TimelineMarker(
                            marker_id=f"emotional_shift_{index}",
                            type="emotional_shift",
                            time_sec=utt.start,
                            speaker=utt.speaker,
                            severity=severity,
                            reason=f"{utt.speaker} shows a notable sentiment shift between turns.",
                            start_sec=prev.start,
                            end_sec=utt.end,
                            evidence={
                                "previous_score": round(prev.sentiment.score, 3),
                                "current_score": round(utt.sentiment.score, 3),
                                "delta": round(delta, 3),
                                "direction": "up" if raw_delta > 0 else "down",
                                "previous_label": prev.sentiment.label or "",
                                "current_label": utt.sentiment.label or "",
                            },
                        )
                    )

            previous_by_speaker[utt.speaker] = utt

        trend = aggregated_signals.session_sentiment_trend
        if trend and trend.direction == "declining" and trend.points:
            first_negative = None

            for i in range(1, len(trend.points)):
                prev_score = trend.points[i - 1].score
                curr_score = trend.points[i].score
                if prev_score is None or curr_score is None:
                    continue

                delta = curr_score - prev_score
                if delta <= -0.08:
                    first_negative = trend.points[i]
                    break

            if first_negative:
                markers.append(
                    TimelineMarker(
                        marker_id="session_tone_decline",
                        type="session_tone_decline",
                        time_sec=first_negative.start,
                        speaker=first_negative.speaker,
                        severity="medium",
                        reason="Session sentiment begins a noticeable downward shift.",
                        start_sec=first_negative.start,
                        end_sec=first_negative.end,
                        evidence={
                            "trend_direction": trend.direction,
                            "trend_slope": (trend.slope if trend.slope is not None else 0.0),
                        },
                    )
                )

        return markers

    @classmethod
    def _detect_high_tension_markers(
        cls,
        analytics: AnalyticsBundle,
        thresholds: InsightThresholds,
    ) -> list[TimelineMarker]:
        markers: list[TimelineMarker] = []

        for speaker, metric in analytics.speaker_metrics.items():
            if (
                metric.interruption_count < thresholds.high_tension_interruption_threshold
                and metric.overlap_count < thresholds.high_tension_overlap_threshold
            ):
                continue

            severity = "high" if (metric.interruption_count >= 4 or metric.overlap_count >= 4) else "medium"

            markers.append(
                TimelineMarker(
                    marker_id=f"high_tension_{speaker}",
                    type="high_tension",
                    time_sec=metric.first_spoke_at_sec or 0.0,
                    speaker=speaker,
                    severity=severity,
                    reason=(
                        f"{speaker} is involved in repeated interruptions or overlaps, "
                        "which may indicate elevated conversational tension."
                    ),
                    start_sec=metric.first_spoke_at_sec,
                    end_sec=metric.last_spoke_at_sec,
                    evidence={
                        "interruption_count": metric.interruption_count,
                        "overlap_count": metric.overlap_count,
                    },
                )
            )

        return markers
