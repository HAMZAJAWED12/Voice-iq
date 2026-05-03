from __future__ import annotations

from collections import Counter

from app.insights.models.signal_models import (
    AggregatedSignals,
    EmotionAggregate,
    SentimentAggregate,
    SentimentTrendPoint,
    SessionSentimentTrend,
)


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def aggregate_sentiment(samples: list[dict]) -> SentimentAggregate:
    if not samples:
        return SentimentAggregate()

    labels = [str(s["label"]).lower() for s in samples if s.get("label")]
    scores = [clamp01(s["score"]) for s in samples if s.get("score") is not None]

    distribution = dict(Counter(labels))
    avg_score = round(sum(scores) / len(scores), 4) if scores else None
    dominant_label = max(distribution, key=distribution.get) if distribution else None

    return SentimentAggregate(
        label=dominant_label,
        avg_score=avg_score,
        distribution=distribution,
        sample_count=len(samples),
    )


def aggregate_emotion(samples: list[dict]) -> EmotionAggregate:
    if not samples:
        return EmotionAggregate()

    totals: dict[str, float] = {}
    count = 0

    for sample in samples:
        values = sample.get("values") or {}
        if not isinstance(values, dict) or not values:
            continue

        count += 1

        for label, score in values.items():
            try:
                key = str(label).lower()
                totals[key] = totals.get(key, 0.0) + float(score)
            except (TypeError, ValueError):
                continue

    if count == 0:
        return EmotionAggregate()

    averaged = {k: v / count for k, v in totals.items()}
    total_sum = sum(averaged.values()) or 1.0
    normalized = {k: round(v / total_sum, 4) for k, v in averaged.items()}
    dominant = max(normalized, key=normalized.get) if normalized else None

    return EmotionAggregate(
        dominant=dominant,
        distribution=normalized,
        sample_count=count,
    )


def compute_sentiment_trend(points: list[SentimentTrendPoint]) -> SessionSentimentTrend:
    if len(points) < 2:
        return SessionSentimentTrend(
            direction="stable",
            slope=0.0,
            points=points,
        )

    first = next((p.score for p in points if p.score is not None), None)
    last = next((p.score for p in reversed(points) if p.score is not None), None)

    if first is None or last is None:
        return SessionSentimentTrend(
            direction="mixed",
            slope=0.0,
            points=points,
        )

    slope = round(last - first, 4)

    if slope >= 0.08:
        direction = "improving"
    elif slope <= -0.08:
        direction = "declining"
    elif abs(slope) < 0.03:
        direction = "stable"
    else:
        direction = "mixed"

    return SessionSentimentTrend(
        direction=direction,
        slope=slope,
        points=points,
    )


def compute_emotion_volatility(
    points: list[SentimentTrendPoint],
    emotion_samples: list[dict],
) -> float:
    sentiment_deltas: list[float] = []

    valid_scores = [p.score for p in points if p.score is not None]
    for i in range(1, len(valid_scores)):
        sentiment_deltas.append(abs(valid_scores[i] - valid_scores[i - 1]))

    sentiment_component = sum(sentiment_deltas) / len(sentiment_deltas) if sentiment_deltas else 0.0

    dominant_seq: list[str] = []
    for item in emotion_samples:
        values = item.get("values") or {}
        if isinstance(values, dict) and values:
            dominant_seq.append(max(values, key=values.get))

    emotion_switches = 0
    for i in range(1, len(dominant_seq)):
        if dominant_seq[i] != dominant_seq[i - 1]:
            emotion_switches += 1

    switch_component = emotion_switches / max(1, len(dominant_seq) - 1) if len(dominant_seq) > 1 else 0.0

    raw = (0.65 * sentiment_component) + (0.35 * switch_component)
    return round(max(0.0, min(1.0, raw)), 4)


class SignalAggregationEngine:
    @classmethod
    def aggregate(cls, utterances: list) -> AggregatedSignals:
        sentiment_samples_by_speaker: dict[str, list[dict]] = {}
        emotion_samples_by_speaker: dict[str, list[dict]] = {}
        trend_points: list[SentimentTrendPoint] = []
        all_sentiment_samples: list[dict] = []
        all_emotion_samples: list[dict] = []

        for utt in utterances:
            speaker = getattr(utt, "speaker", None) or "UNKNOWN"
            utt_id = getattr(utt, "id", None) or "unknown"
            start = float(getattr(utt, "start", 0.0) or 0.0)
            end = float(getattr(utt, "end", 0.0) or 0.0)

            sentiment = getattr(utt, "sentiment", None)
            if (
                sentiment
                and getattr(sentiment, "label", None) is not None
                and getattr(sentiment, "score", None) is not None
            ):
                sample = {
                    "label": sentiment.label,
                    "score": float(sentiment.score),
                }
                sentiment_samples_by_speaker.setdefault(speaker, []).append(sample)
                all_sentiment_samples.append(sample)

                trend_points.append(
                    SentimentTrendPoint(
                        utterance_id=utt_id,
                        speaker=speaker,
                        start=start,
                        end=end,
                        label=sentiment.label,
                        score=float(sentiment.score),
                    )
                )

            emotion = getattr(utt, "emotion", None)
            values = getattr(emotion, "values", None) if emotion else None
            if isinstance(values, dict) and values:
                sample = {"values": values}
                emotion_samples_by_speaker.setdefault(speaker, []).append(sample)
                all_emotion_samples.append(sample)

        trend_points.sort(key=lambda x: (x.start, x.end, x.utterance_id))

        speaker_sentiment = {spk: aggregate_sentiment(samples) for spk, samples in sentiment_samples_by_speaker.items()}

        speaker_emotion = {spk: aggregate_emotion(samples) for spk, samples in emotion_samples_by_speaker.items()}

        return AggregatedSignals(
            session_sentiment=aggregate_sentiment(all_sentiment_samples),
            session_emotion=aggregate_emotion(all_emotion_samples),
            session_sentiment_trend=compute_sentiment_trend(trend_points),
            speaker_sentiment=speaker_sentiment,
            speaker_emotion=speaker_emotion,
            emotion_volatility_score=compute_emotion_volatility(
                trend_points,
                all_emotion_samples,
            ),
        )
