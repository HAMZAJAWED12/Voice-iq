from __future__ import annotations

from collections import defaultdict

from app.insights.models.analytics_models import (
    AnalyticsBundle,
    PauseMetric,
    SessionMetrics,
    SpeakerMetrics,
)
from app.insights.models.input_models import SessionInput, UtteranceInput


class InsightAnalyticsEngine:
    @classmethod
    def run(cls, session: SessionInput) -> AnalyticsBundle:
        utterances = sorted(session.utterances, key=lambda u: (u.start, u.end, u.id))

        session_metrics = cls._compute_session_metrics(utterances)
        speaker_metrics = cls._compute_speaker_metrics(utterances)
        pauses = cls._compute_pauses(utterances)

        return AnalyticsBundle(
            session_metrics=session_metrics,
            speaker_metrics=speaker_metrics,
            pauses=pauses,
        )

    @staticmethod
    def _compute_session_metrics(utterances: list[UtteranceInput]) -> SessionMetrics:
        total_words = 0
        total_questions = 0
        total_duration = 0.0
        pauses: list[float] = []

        prev_end = None
        speakers = set()

        for u in utterances:
            words = u.word_count if u.word_count is not None else len(u.text.split())
            total_words += words
            total_duration += max(0.0, u.end - u.start)
            speakers.add(u.speaker)

            if "?" in u.text:
                total_questions += 1

            if prev_end is not None:
                gap = u.start - prev_end
                if gap > 0:
                    pauses.append(gap)

            prev_end = u.end

        utterance_count = len(utterances)
        avg_pause = sum(pauses) / len(pauses) if pauses else 0.0
        max_pause = max(pauses) if pauses else 0.0
        avg_words_per_utt = total_words / utterance_count if utterance_count else 0.0
        avg_utt_duration = total_duration / utterance_count if utterance_count else 0.0

        return SessionMetrics(
            total_duration_sec=round(total_duration, 3),
            total_speakers=len(speakers),
            total_utterances=utterance_count,
            total_words=total_words,
            avg_utterance_length_words=round(avg_words_per_utt, 3),
            avg_utterance_duration_sec=round(avg_utt_duration, 3),
            total_questions=total_questions,
            total_pauses=len(pauses),
            avg_pause_sec=round(avg_pause, 3),
            max_pause_sec=round(max_pause, 3),
        )

    @staticmethod
    def _compute_speaker_metrics(utterances: list[UtteranceInput]) -> dict[str, SpeakerMetrics]:
        speaker_stats = defaultdict(
            lambda: {
                "time": 0.0,
                "utterances": 0,
                "words": 0,
                "questions": 0,
                "overlaps": 0,
                "interruptions": 0,
                "first": None,
                "last": None,
            }
        )

        prev = None

        for u in utterances:
            stats = speaker_stats[u.speaker]
            duration = max(0.0, u.end - u.start)
            words = u.word_count if u.word_count is not None else len(u.text.split())

            stats["time"] += duration
            stats["utterances"] += 1
            stats["words"] += words

            if "?" in u.text:
                stats["questions"] += 1

            if stats["first"] is None:
                stats["first"] = u.start
            stats["last"] = u.end

            if u.overlap:
                stats["overlaps"] += 1

            if prev is not None and prev.speaker != u.speaker and u.start < prev.end:
                stats["interruptions"] += 1

            prev = u

        total_words = sum(s["words"] for s in speaker_stats.values())
        total_time = sum(s["time"] for s in speaker_stats.values())

        metrics: dict[str, SpeakerMetrics] = {}
        for speaker, s in speaker_stats.items():
            utterance_count = s["utterances"]
            avg_len = s["words"] / utterance_count if utterance_count else 0.0
            avg_duration = s["time"] / utterance_count if utterance_count else 0.0

            metrics[speaker] = SpeakerMetrics(
                speaker=speaker,
                speaking_time_sec=round(s["time"], 3),
                utterance_count=utterance_count,
                word_count=s["words"],
                speaking_ratio=round((s["time"] / total_time) if total_time else 0.0, 4),
                word_ratio=round((s["words"] / total_words) if total_words else 0.0, 4),
                avg_utterance_length_words=round(avg_len, 3),
                avg_utterance_duration_sec=round(avg_duration, 3),
                question_count=s["questions"],
                interruption_count=s["interruptions"],
                overlap_count=s["overlaps"],
                first_spoke_at_sec=s["first"],
                last_spoke_at_sec=s["last"],
            )

        return metrics

    @staticmethod
    def _compute_pauses(utterances: list[UtteranceInput]) -> list[PauseMetric]:
        pauses: list[PauseMetric] = []
        prev = None

        for u in utterances:
            if prev is not None:
                gap = u.start - prev.end
                if gap > 0:
                    pauses.append(
                        PauseMetric(
                            start_after_utterance_id=prev.id,
                            end_before_utterance_id=u.id,
                            duration_sec=round(gap, 3),
                            speaker_before=prev.speaker,
                            speaker_after=u.speaker,
                        )
                    )
            prev = u

        return pauses
