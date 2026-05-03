from __future__ import annotations

from app.insights.models.analytics_models import AnalyticsBundle, SpeakerMetrics
from app.insights.models.api_models import SummaryBundle
from app.insights.models.input_models import SessionInput
from app.insights.models.insight_models import InsightBundle, InsightFlag, TimelineMarker


class InsightSummaryEngine:
    @classmethod
    def run(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        insights: InsightBundle,
    ) -> SummaryBundle:
        return SummaryBundle(
            overall_summary=cls._build_overall_summary(session, analytics, insights),
            speaker_summaries=cls._build_speaker_summaries(analytics, insights),
            notable_concerns=cls._build_notable_concerns(insights),
            key_moments_summary=cls._build_key_moments_summary(insights),
        )

    @classmethod
    def _build_overall_summary(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        insights: InsightBundle,
    ) -> str:
        sm = analytics.session_metrics
        speaker_metrics = analytics.speaker_metrics

        if sm.total_utterances == 0:
            return "No conversational content was available to summarize."

        dominant_speaker = cls._get_dominant_speaker(speaker_metrics)
        high_severity_flags = [f for f in insights.flags if f.severity == "high"]
        medium_severity_flags = [f for f in insights.flags if f.severity == "medium"]

        tone = cls._infer_session_tone(insights)
        balance = cls._infer_balance_label(speaker_metrics)
        pause_desc = cls._describe_pause_behavior(sm.avg_pause_sec, sm.max_pause_sec)
        conflict_desc = cls._describe_conflict(insights)
        sentiment_part = cls._describe_session_sentiment(insights)
        emotion_part = cls._describe_session_emotion(insights)
        escalation_part = cls._describe_escalation(insights)
        inconsistency_part = cls._describe_inconsistency(insights)

        parts: list[str] = [
            (
                f"This session contains {sm.total_utterances} utterances across "
                f"{sm.total_speakers} speaker(s), with a total measured speaking time "
                f"of {sm.total_duration_sec:.2f} seconds and {sm.total_words} words."
            )
        ]

        if sentiment_part:
            parts.append(sentiment_part)

        if emotion_part:
            parts.append(emotion_part)

        if dominant_speaker:
            parts.append(
                f"The interaction appears {balance}, with {dominant_speaker} contributing "
                f"the largest share of speaking time."
            )
        else:
            parts.append(f"The interaction appears {balance} overall.")

        parts.append(f"Overall engagement appears {tone}, and pause behavior suggests {pause_desc}.")
        parts.append(conflict_desc)

        if escalation_part:
            parts.append(escalation_part)

        if inconsistency_part:
            parts.append(inconsistency_part)

        if high_severity_flags:
            parts.append(
                f"There are {len(high_severity_flags)} high-severity concern(s) that may deserve closer review."
            )
        elif medium_severity_flags:
            parts.append(f"There are {len(medium_severity_flags)} medium-severity signal(s) worth monitoring.")
        else:
            parts.append("No major high-severity conversational risks were detected.")

        return " ".join(parts).strip()

    @staticmethod
    def _describe_escalation(insights: InsightBundle) -> str:
        escalation = insights.escalation
        if not escalation or escalation.level == "none":
            return ""

        return f"Escalation assessment is {escalation.level}, with score {escalation.score:.2f}. {escalation.summary}"

    @staticmethod
    def _describe_inconsistency(insights: InsightBundle) -> str:
        inconsistency = insights.inconsistency
        if not inconsistency or inconsistency.level == "none":
            return ""

        return (
            f"Inconsistency assessment is {inconsistency.level}, with score "
            f"{inconsistency.score:.2f}. {inconsistency.summary}"
        )

    @staticmethod
    def _describe_session_sentiment(insights: InsightBundle) -> str:
        sentiment = insights.session_sentiment
        trend = insights.session_sentiment_trend

        if not sentiment or sentiment.sample_count == 0:
            return ""

        trend_text = ""
        if trend and trend.direction:
            trend_text = f" with a {trend.direction} trend"

        avg = f" (average score {sentiment.avg_score:.2f})" if sentiment.avg_score is not None else ""

        return f"Overall sentiment appears {sentiment.label}{trend_text}{avg}."

    @staticmethod
    def _describe_session_emotion(insights: InsightBundle) -> str:
        emotion = insights.session_emotion
        if not emotion or emotion.sample_count == 0 or not emotion.dominant:
            return ""

        return f"Emotion signals are led by {emotion.dominant} across the session."

    @classmethod
    def _build_speaker_summaries(
        cls,
        analytics: AnalyticsBundle,
        insights: InsightBundle,
    ) -> dict[str, str]:
        summaries: dict[str, str] = {}

        for speaker, metric in analytics.speaker_metrics.items():
            speaker_insight = insights.speaker_insights.get(speaker)
            summaries[speaker] = cls._build_single_speaker_summary(
                metric,
                speaker_insight,
            )

        return summaries

    @classmethod
    def _build_single_speaker_summary(
        cls,
        metric: SpeakerMetrics,
        speaker_insight,
    ) -> str:
        style = cls._describe_speaker_style(metric)
        question_desc = cls._describe_question_behavior(
            metric.question_count,
            metric.utterance_count,
        )

        parts: list[str] = [
            (
                f"{metric.speaker} contributed {metric.utterance_count} utterances, "
                f"{metric.word_count} words, and {metric.speaking_time_sec:.2f} seconds of speech."
            ),
            f"Their speaking share was {metric.speaking_ratio:.2%}, suggesting {style} participation.",
            question_desc,
        ]

        if (
            speaker_insight
            and speaker_insight.sentiment
            and speaker_insight.sentiment.sample_count > 0
            and speaker_insight.sentiment.label
        ):
            parts.append(f"Their sentiment profile appears mostly {speaker_insight.sentiment.label}.")

        if (
            speaker_insight
            and speaker_insight.emotion
            and speaker_insight.emotion.sample_count > 0
            and speaker_insight.emotion.dominant
        ):
            parts.append(f"Their dominant emotional signal is {speaker_insight.emotion.dominant}.")

        if metric.interruption_count > 0:
            parts.append(f"They were associated with {metric.interruption_count} interruption event(s).")

        if metric.overlap_count > 0:
            parts.append(f"They were involved in {metric.overlap_count} overlapping turn(s).")

        if speaker_insight and speaker_insight.flags:
            top_flags = ", ".join(flag.type for flag in speaker_insight.flags[:3])
            parts.append(f"Notable speaker-level signals include: {top_flags}.")
        else:
            parts.append("No major speaker-level concerns were flagged.")

        return " ".join(parts).strip()

    @classmethod
    def _build_notable_concerns(cls, insights: InsightBundle) -> list[str]:
        if not insights.flags:
            return []

        ranked_flags = sorted(
            insights.flags,
            key=lambda f: cls._severity_rank(f.severity),
            reverse=True,
        )

        concerns: list[str] = []
        seen = set()

        for flag in ranked_flags:
            text = cls._flag_to_concern_text(flag)
            if text not in seen:
                concerns.append(text)
                seen.add(text)
            if len(concerns) >= 6:
                break

        return concerns

    @classmethod
    def _flag_to_concern_text(cls, flag: InsightFlag) -> str:
        speaker_prefix = f"{flag.speaker}: " if flag.speaker else ""
        return f"{speaker_prefix}{flag.reason}"

    @classmethod
    def _build_key_moments_summary(cls, insights: InsightBundle) -> str:
        if not insights.timeline:
            return "No notable timeline markers were generated for this session."

        important_markers = sorted(
            insights.timeline,
            key=lambda m: (cls._severity_rank(m.severity), m.time_sec),
            reverse=True,
        )

        selected = important_markers[:5]
        snippets = [cls._marker_to_sentence(marker) for marker in selected]
        return " ".join(snippets).strip()

    @classmethod
    def _marker_to_sentence(cls, marker: TimelineMarker) -> str:
        marker_type = marker.type.replace("_", " ")
        speaker_part = f"{marker.speaker} " if marker.speaker else ""

        if marker.reason:
            return (
                f"At {marker.time_sec:.2f}s, {speaker_part}showed a "
                f"{marker_type} signal ({marker.severity}) because {marker.reason.lower()}."
            )

        return f"At {marker.time_sec:.2f}s, a {marker_type} marker was detected."

    @staticmethod
    def _severity_rank(severity: str) -> int:
        return {"low": 1, "medium": 2, "high": 3}.get(severity, 0)

    @staticmethod
    def _get_dominant_speaker(speaker_metrics: dict[str, SpeakerMetrics]) -> str | None:
        if not speaker_metrics:
            return None
        return max(speaker_metrics.values(), key=lambda s: s.speaking_ratio).speaker

    @staticmethod
    def _infer_balance_label(speaker_metrics: dict[str, SpeakerMetrics]) -> str:
        if not speaker_metrics:
            return "balanced"

        max_ratio = max(metric.speaking_ratio for metric in speaker_metrics.values())
        if max_ratio >= 0.75:
            return "highly imbalanced"
        if max_ratio >= 0.60:
            return "somewhat imbalanced"
        return "fairly balanced"

    @staticmethod
    def _infer_session_tone(insights: InsightBundle) -> str:
        engagement = insights.scores.engagement_score
        conflict = insights.scores.conflict_score

        if conflict >= 0.7:
            return "strained"
        if engagement >= 0.7 and conflict < 0.3:
            return "active and reasonably cooperative"
        if engagement < 0.35:
            return "low-energy"
        return "moderately engaged"

    @staticmethod
    def _describe_pause_behavior(avg_pause_sec: float, max_pause_sec: float) -> str:
        if max_pause_sec >= 6.0:
            return "some substantial hesitation or disengagement windows"
        if avg_pause_sec >= 3.0:
            return "noticeable pauses between responses"
        if avg_pause_sec <= 1.0:
            return "a relatively smooth response flow"
        return "occasional pauses but generally steady pacing"

    @staticmethod
    def _describe_conflict(insights: InsightBundle) -> str:
        conflict = insights.scores.conflict_score
        if conflict >= 0.7:
            return "Turn-taking behavior suggests elevated tension, with frequent interruptions or overlaps."
        if conflict >= 0.4:
            return "There are moderate signs of friction in the turn-taking pattern."
        return "Turn-taking appears relatively orderly, with limited conflict signals."

    @staticmethod
    def _describe_speaker_style(metric: SpeakerMetrics) -> str:
        if metric.speaking_ratio >= 0.60:
            return "dominant"
        if metric.speaking_ratio <= 0.20:
            return "limited"
        return "moderately active"

    @staticmethod
    def _describe_question_behavior(question_count: int, utterance_count: int) -> str:
        if utterance_count == 0:
            return "Question behavior could not be assessed."

        ratio = question_count / utterance_count

        if question_count == 0:
            return "They did not ask questions during the captured interaction."
        if ratio >= 0.40:
            return "They used questions frequently, suggesting active probing or clarification."
        if ratio >= 0.15:
            return "They asked occasional questions during the conversation."
        return "They asked only a small number of questions."
