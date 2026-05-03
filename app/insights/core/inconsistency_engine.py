from __future__ import annotations

import re
from collections import defaultdict

from app.insights.models.analytics_models import AnalyticsBundle
from app.insights.models.inconsistency_models import (
    InconsistencyAssessment,
    InconsistencySignal,
    InconsistencyWindow,
)
from app.insights.models.input_models import SessionInput, UtteranceInput
from app.insights.models.signal_models import AggregatedSignals

# --- Lexicons -----------------------------------------------------------------
# Conservative, domain-agnostic word lists. They are used only to compute a
# coarse lexical polarity signal that we cross-check against ASR-derived
# sentiment / emotion labels. They are NOT a sentiment classifier.

_POSITIVE_LEXICON = {
    "happy",
    "great",
    "excellent",
    "wonderful",
    "love",
    "loved",
    "amazing",
    "fantastic",
    "good",
    "thanks",
    "thank",
    "appreciate",
    "appreciated",
    "perfect",
    "awesome",
    "delighted",
    "glad",
    "pleased",
    "enjoy",
    "enjoyed",
    "fine",
    "okay",
    "ok",
    "nice",
    "helpful",
    "satisfied",
    "smooth",
}

_NEGATIVE_LEXICON = {
    "hate",
    "hated",
    "terrible",
    "awful",
    "horrible",
    "bad",
    "worst",
    "angry",
    "furious",
    "frustrated",
    "annoyed",
    "upset",
    "disappointed",
    "sucks",
    "stupid",
    "ridiculous",
    "useless",
    "broken",
    "wrong",
    "unhappy",
    "miserable",
    "disgusting",
    "pathetic",
    "rude",
    "unacceptable",
    "lie",
    "lied",
    "lying",
    "complain",
    "complaining",
    "problem",
    "issue",
}

_NEGATION_TOKENS = {
    "not",
    "no",
    "never",
    "n't",
    "dont",
    "doesnt",
    "isnt",
    "arent",
    "wont",
    "wasnt",
    "werent",
    "cant",
    "cannot",
    "didnt",
    "wouldnt",
    "shouldnt",
    "couldnt",
}

_AFFIRMATION_TOKENS = {
    "yes",
    "yeah",
    "yep",
    "agree",
    "agreed",
    "absolutely",
    "definitely",
    "sure",
    "of course",
    "right",
    "correct",
    "indeed",
    "true",
}

_DENIAL_TOKENS = {
    "no",
    "nope",
    "disagree",
    "disagreed",
    "never",
    "wrong",
    "false",
    "incorrect",
    "actually no",
}

# Emotion polarity buckets (must match label keys produced by upstream NLP).
_NEGATIVE_EMOTIONS = {"angry", "frustrated", "anxious", "upset", "fear", "sad"}
_POSITIVE_EMOTIONS = {"happy", "joy", "joyful", "content", "calm"}
_NEUTRAL_EMOTIONS = {"neutral", "calm"}

# Tokenizer that keeps contractions reasonably handle-able.
_TOKEN_RE = re.compile(r"[a-zA-Z']+")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower().replace("'", "") for t in _TOKEN_RE.findall(text)]


def _lexical_polarity(text: str) -> str | None:
    """Coarse lexical polarity classifier.

    Returns "positive", "negative" or None when the signal is ambiguous /
    unavailable. Negation flips the polarity of the immediately following token
    in a small lookback window.
    """
    tokens = _tokenize(text)
    if not tokens:
        return None

    pos_hits = 0
    neg_hits = 0
    negate_window = 0  # how many tokens forward a negation still applies

    for tok in tokens:
        if tok in _NEGATION_TOKENS:
            negate_window = 3
            continue

        token_polarity = None
        if tok in _POSITIVE_LEXICON:
            token_polarity = "pos"
        elif tok in _NEGATIVE_LEXICON:
            token_polarity = "neg"

        if token_polarity is not None:
            if negate_window > 0:
                # flip
                token_polarity = "neg" if token_polarity == "pos" else "pos"
            if token_polarity == "pos":
                pos_hits += 1
            else:
                neg_hits += 1

        if negate_window > 0:
            negate_window -= 1

    if pos_hits == neg_hits:
        return None
    if pos_hits > neg_hits and pos_hits >= 1:
        return "positive"
    if neg_hits > pos_hits and neg_hits >= 1:
        return "negative"
    return None


def _dominant_emotion(utt: UtteranceInput) -> str | None:
    if not utt.emotion or not utt.emotion.values:
        return None
    try:
        return max(utt.emotion.values, key=utt.emotion.values.get)
    except ValueError:
        return None


def _emotion_polarity(emotion: str | None) -> str | None:
    if not emotion:
        return None
    e = emotion.lower()
    if e in _NEGATIVE_EMOTIONS:
        return "negative"
    if e in _POSITIVE_EMOTIONS:
        return "positive"
    if e in _NEUTRAL_EMOTIONS:
        return "neutral"
    return None


class InsightInconsistencyEngine:
    """Detects mismatches between sentiment, emotion, and lexical content.

    The engine fires on five distinct inconsistency families:

      1. Sentiment-vs-text mismatch: ASR sentiment label disagrees with the
         lexical polarity of the utterance text.
      2. Sentiment-vs-emotion contradiction: e.g. "positive" sentiment label
         while the dominant emotion is anger / frustration.
      3. Abrupt emotional reversal: same speaker flips between positive and
         negative emotion clusters in a short time window.
      4. Contradictory statements: same speaker affirms then denies (or vice
         versa) on closely-spaced utterances.
      5. Masking tone: calm / neutral emotion delivered with strongly negative
         lexical content (potential suppressed affect).

    All outputs include explicit reason + evidence and are bounded to [0, 1].
    """

    # Sub-signal score caps (each signal contributes at most this much before
    # the final clamp). Mirrors the discipline used in the Escalation Engine.
    _CAP_TEXT_MISMATCH = 0.30
    _CAP_SENT_EMOTION = 0.25
    _CAP_REVERSAL = 0.25
    _CAP_CONTRADICTION = 0.20
    _CAP_MASKING = 0.20

    # Time window (seconds) within which an emotional flip counts as "abrupt".
    _REVERSAL_WINDOW_SEC = 30.0
    # Time window within which an affirm/deny pair counts as contradictory.
    _CONTRADICTION_WINDOW_SEC = 60.0

    @classmethod
    def assess(
        cls,
        session: SessionInput,
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
    ) -> InconsistencyAssessment:
        # Defensive: empty / missing input must not raise.
        if not session or not session.utterances:
            return InconsistencyAssessment()

        utterances = sorted(session.utterances, key=lambda u: (u.start, u.end, u.id))

        signals: list[InconsistencySignal] = []
        windows: list[InconsistencyWindow] = []

        text_signal, text_windows = cls._detect_sentiment_text_mismatch(utterances)
        if text_signal:
            signals.append(text_signal)
        windows.extend(text_windows)

        sent_emotion_signal, sent_emotion_windows = cls._detect_sentiment_emotion_contradiction(utterances)
        if sent_emotion_signal:
            signals.append(sent_emotion_signal)
        windows.extend(sent_emotion_windows)

        reversal_signal, reversal_windows = cls._detect_abrupt_emotional_reversal(utterances)
        if reversal_signal:
            signals.append(reversal_signal)
        windows.extend(reversal_windows)

        contradiction_signal, contradiction_windows = cls._detect_contradictory_statements(utterances)
        if contradiction_signal:
            signals.append(contradiction_signal)
        windows.extend(contradiction_windows)

        masking_signal, masking_windows = cls._detect_masking_tone(utterances)
        if masking_signal:
            signals.append(masking_signal)
        windows.extend(masking_windows)

        score = round(_clamp(sum(s.score for s in signals)), 4)
        level = cls._score_to_level(score)
        primary_speaker = cls._infer_primary_speaker(signals, analytics, aggregated_signals)
        summary = cls._build_summary(level, signals, primary_speaker)

        return InconsistencyAssessment(
            level=level,
            score=score,
            signals=signals,
            windows=windows,
            primary_speaker=primary_speaker,
            summary=summary,
        )

    # ------------------------------------------------------------------ #
    # Signal: sentiment label vs. lexical polarity of the text
    # ------------------------------------------------------------------ #
    @classmethod
    def _detect_sentiment_text_mismatch(
        cls,
        utterances: list[UtteranceInput],
    ) -> tuple[InconsistencySignal | None, list[InconsistencyWindow]]:
        mismatches: list[tuple[UtteranceInput, str, str]] = []
        eligible = 0

        for utt in utterances:
            if not utt.sentiment or not utt.sentiment.label:
                continue
            label = utt.sentiment.label.lower()
            if label not in {"positive", "negative"}:
                continue
            lexical = _lexical_polarity(utt.text)
            if lexical is None:
                continue
            eligible += 1
            if lexical != label:
                mismatches.append((utt, label, lexical))

        if not mismatches or eligible == 0:
            return None, []

        ratio = len(mismatches) / max(eligible, 1)
        # Don't fire on a single noisy utterance unless it dominates the sample.
        if len(mismatches) < 2 and ratio < 0.5:
            return None, []

        score = _clamp(0.08 + ratio * 0.40, high=cls._CAP_TEXT_MISMATCH)

        windows: list[InconsistencyWindow] = []
        # Cap the number of emitted windows so we don't spam the timeline.
        for utt, label, lexical in mismatches[:5]:
            windows.append(
                InconsistencyWindow(
                    start_sec=utt.start,
                    end_sec=utt.end,
                    level="medium" if score >= 0.18 else "low",
                    speaker=utt.speaker,
                    reason=(f"Sentiment label '{label}' disagrees with lexical tone " f"'{lexical}' in this turn."),
                    evidence={
                        "utterance_id": utt.id,
                        "sentiment_label": label,
                        "lexical_polarity": lexical,
                    },
                )
            )

        # Track the most affected speaker for primary_speaker inference.
        per_speaker = defaultdict(int)
        for utt, _, _ in mismatches:
            per_speaker[utt.speaker] += 1
        top_speaker = max(per_speaker, key=per_speaker.get) if per_speaker else None

        return (
            InconsistencySignal(
                signal_type="sentiment_text_mismatch",
                severity="medium" if score >= 0.18 else "low",
                score=round(score, 4),
                speaker=top_speaker,
                reason=(
                    "ASR sentiment label disagrees with the lexical polarity of " "the spoken text on multiple turns."
                ),
                evidence={
                    "mismatch_count": len(mismatches),
                    "eligible_count": eligible,
                    "mismatch_ratio": round(ratio, 4),
                },
            ),
            windows,
        )

    # ------------------------------------------------------------------ #
    # Signal: sentiment label vs. dominant emotion polarity
    # ------------------------------------------------------------------ #
    @classmethod
    def _detect_sentiment_emotion_contradiction(
        cls,
        utterances: list[UtteranceInput],
    ) -> tuple[InconsistencySignal | None, list[InconsistencyWindow]]:
        contradictions: list[tuple[UtteranceInput, str, str]] = []
        eligible = 0

        for utt in utterances:
            if not utt.sentiment or not utt.sentiment.label:
                continue
            label = utt.sentiment.label.lower()
            emotion = _dominant_emotion(utt)
            emo_polarity = _emotion_polarity(emotion)
            if not emotion or emo_polarity is None:
                continue
            if label not in {"positive", "negative"}:
                continue
            eligible += 1

            is_contradiction = (label == "positive" and emo_polarity == "negative") or (
                label == "negative" and emo_polarity == "positive"
            )
            if is_contradiction:
                contradictions.append((utt, label, emotion))

        if not contradictions or eligible == 0:
            return None, []

        ratio = len(contradictions) / max(eligible, 1)
        if len(contradictions) < 2 and ratio < 0.5:
            return None, []

        score = _clamp(0.07 + ratio * 0.35, high=cls._CAP_SENT_EMOTION)

        windows: list[InconsistencyWindow] = []
        for utt, label, emotion in contradictions[:5]:
            windows.append(
                InconsistencyWindow(
                    start_sec=utt.start,
                    end_sec=utt.end,
                    level="medium" if score >= 0.16 else "low",
                    speaker=utt.speaker,
                    reason=(f"Sentiment '{label}' conflicts with dominant emotion " f"'{emotion}' on this turn."),
                    evidence={
                        "utterance_id": utt.id,
                        "sentiment_label": label,
                        "dominant_emotion": emotion,
                    },
                )
            )

        per_speaker = defaultdict(int)
        for utt, _, _ in contradictions:
            per_speaker[utt.speaker] += 1
        top_speaker = max(per_speaker, key=per_speaker.get) if per_speaker else None

        return (
            InconsistencySignal(
                signal_type="sentiment_emotion_contradiction",
                severity="medium" if score >= 0.16 else "low",
                score=round(score, 4),
                speaker=top_speaker,
                reason=(
                    "Sentiment label and dominant emotion disagree on multiple "
                    "turns, suggesting affective masking or noisy labels."
                ),
                evidence={
                    "contradiction_count": len(contradictions),
                    "eligible_count": eligible,
                    "contradiction_ratio": round(ratio, 4),
                },
            ),
            windows,
        )

    # ------------------------------------------------------------------ #
    # Signal: abrupt emotional reversal within a short same-speaker window
    # ------------------------------------------------------------------ #
    @classmethod
    def _detect_abrupt_emotional_reversal(
        cls,
        utterances: list[UtteranceInput],
    ) -> tuple[InconsistencySignal | None, list[InconsistencyWindow]]:
        last_by_speaker: dict[str, tuple[UtteranceInput, str, str]] = {}
        reversals: list[tuple[UtteranceInput, UtteranceInput, str, str]] = []

        for utt in utterances:
            emotion = _dominant_emotion(utt)
            polarity = _emotion_polarity(emotion)
            if not emotion or polarity is None or polarity == "neutral":
                continue

            previous = last_by_speaker.get(utt.speaker)
            if previous is not None:
                prev_utt, prev_emotion, prev_polarity = previous
                gap = utt.start - prev_utt.end
                if (
                    0.0 <= gap <= cls._REVERSAL_WINDOW_SEC
                    and prev_polarity != polarity
                    and {prev_polarity, polarity} == {"positive", "negative"}
                ):
                    reversals.append((prev_utt, utt, prev_emotion, emotion))

            last_by_speaker[utt.speaker] = (utt, emotion, polarity)

        if not reversals:
            return None, []

        # Each reversal contributes a small amount up to the cap.
        score = _clamp(0.10 + 0.08 * len(reversals), high=cls._CAP_REVERSAL)

        windows: list[InconsistencyWindow] = []
        for prev_utt, utt, prev_emotion, emotion in reversals[:5]:
            windows.append(
                InconsistencyWindow(
                    start_sec=prev_utt.start,
                    end_sec=utt.end,
                    level="medium" if score >= 0.18 else "low",
                    speaker=utt.speaker,
                    reason=(
                        f"{utt.speaker} flips emotion from '{prev_emotion}' to "
                        f"'{emotion}' within {cls._REVERSAL_WINDOW_SEC:.0f}s."
                    ),
                    evidence={
                        "speaker": utt.speaker,
                        "from_emotion": prev_emotion,
                        "to_emotion": emotion,
                        "gap_sec": round(utt.start - prev_utt.end, 3),
                        "previous_utterance_id": prev_utt.id,
                        "current_utterance_id": utt.id,
                    },
                )
            )

        per_speaker = defaultdict(int)
        for _, utt, _, _ in reversals:
            per_speaker[utt.speaker] += 1
        top_speaker = max(per_speaker, key=per_speaker.get) if per_speaker else None

        return (
            InconsistencySignal(
                signal_type="abrupt_emotional_reversal",
                severity="medium" if score >= 0.18 else "low",
                score=round(score, 4),
                speaker=top_speaker,
                reason=(
                    "Speaker emotion flips between opposing polarities within "
                    "a short window, suggesting affective instability or masking."
                ),
                evidence={
                    "reversal_count": len(reversals),
                    "window_sec": cls._REVERSAL_WINDOW_SEC,
                },
            ),
            windows,
        )

    # ------------------------------------------------------------------ #
    # Signal: affirmation followed by denial from the same speaker
    # ------------------------------------------------------------------ #
    @classmethod
    def _detect_contradictory_statements(
        cls,
        utterances: list[UtteranceInput],
    ) -> tuple[InconsistencySignal | None, list[InconsistencyWindow]]:
        last_stance_by_speaker: dict[str, tuple[UtteranceInput, str]] = {}
        contradictions: list[tuple[UtteranceInput, UtteranceInput, str, str]] = []

        for utt in utterances:
            tokens = _tokenize(utt.text)
            if not tokens:
                continue

            text_lower = " " + " ".join(tokens) + " "
            stance: str | None = None
            if any(f" {tok} " in text_lower for tok in _AFFIRMATION_TOKENS):
                stance = "affirm"
            # Denial check second so a turn containing both leans toward denial,
            # which is the more conservative call.
            if any(f" {tok} " in text_lower for tok in _DENIAL_TOKENS):
                stance = "deny"

            if stance is None:
                continue

            previous = last_stance_by_speaker.get(utt.speaker)
            if previous is not None:
                prev_utt, prev_stance = previous
                gap = utt.start - prev_utt.end
                if 0.0 <= gap <= cls._CONTRADICTION_WINDOW_SEC and prev_stance != stance:
                    contradictions.append((prev_utt, utt, prev_stance, stance))

            last_stance_by_speaker[utt.speaker] = (utt, stance)

        if not contradictions:
            return None, []

        score = _clamp(0.08 + 0.06 * len(contradictions), high=cls._CAP_CONTRADICTION)

        windows: list[InconsistencyWindow] = []
        for prev_utt, utt, prev_stance, stance in contradictions[:5]:
            windows.append(
                InconsistencyWindow(
                    start_sec=prev_utt.start,
                    end_sec=utt.end,
                    level="medium" if score >= 0.14 else "low",
                    speaker=utt.speaker,
                    reason=(
                        f"{utt.speaker} switches stance from '{prev_stance}' to "
                        f"'{stance}' within "
                        f"{cls._CONTRADICTION_WINDOW_SEC:.0f}s."
                    ),
                    evidence={
                        "speaker": utt.speaker,
                        "previous_stance": prev_stance,
                        "current_stance": stance,
                        "gap_sec": round(utt.start - prev_utt.end, 3),
                        "previous_utterance_id": prev_utt.id,
                        "current_utterance_id": utt.id,
                    },
                )
            )

        per_speaker = defaultdict(int)
        for _, utt, _, _ in contradictions:
            per_speaker[utt.speaker] += 1
        top_speaker = max(per_speaker, key=per_speaker.get) if per_speaker else None

        return (
            InconsistencySignal(
                signal_type="contradictory_statements",
                severity="medium" if score >= 0.14 else "low",
                score=round(score, 4),
                speaker=top_speaker,
                reason=("Same speaker delivers conflicting affirmation/denial cues " "on closely spaced turns."),
                evidence={
                    "contradiction_count": len(contradictions),
                    "window_sec": cls._CONTRADICTION_WINDOW_SEC,
                },
            ),
            windows,
        )

    # ------------------------------------------------------------------ #
    # Signal: calm / neutral emotion delivered with strongly negative content
    # ------------------------------------------------------------------ #
    @classmethod
    def _detect_masking_tone(
        cls,
        utterances: list[UtteranceInput],
    ) -> tuple[InconsistencySignal | None, list[InconsistencyWindow]]:
        masked: list[tuple[UtteranceInput, str]] = []

        for utt in utterances:
            emotion = _dominant_emotion(utt)
            if not emotion:
                continue
            if emotion.lower() not in _NEUTRAL_EMOTIONS and emotion.lower() not in {"calm"}:
                continue

            lexical = _lexical_polarity(utt.text)
            sentiment_label = utt.sentiment.label.lower() if utt.sentiment and utt.sentiment.label else None

            negative_text = lexical == "negative"
            negative_sentiment = sentiment_label == "negative"

            if not (negative_text or negative_sentiment):
                continue

            masked.append((utt, emotion))

        if not masked:
            return None, []

        # Single observation is suggestive but not enough to flag; require
        # at least two unless the masking ratio is overwhelming.
        if len(masked) < 2:
            return None, []

        score = _clamp(0.07 + 0.05 * len(masked), high=cls._CAP_MASKING)

        windows: list[InconsistencyWindow] = []
        for utt, emotion in masked[:5]:
            windows.append(
                InconsistencyWindow(
                    start_sec=utt.start,
                    end_sec=utt.end,
                    level="medium" if score >= 0.14 else "low",
                    speaker=utt.speaker,
                    reason=(f"{utt.speaker} delivers negative content with a " f"flat / '{emotion}' emotional tone."),
                    evidence={
                        "utterance_id": utt.id,
                        "dominant_emotion": emotion,
                    },
                )
            )

        per_speaker = defaultdict(int)
        for utt, _ in masked:
            per_speaker[utt.speaker] += 1
        top_speaker = max(per_speaker, key=per_speaker.get) if per_speaker else None

        return (
            InconsistencySignal(
                signal_type="masking_tone",
                severity="medium" if score >= 0.14 else "low",
                score=round(score, 4),
                speaker=top_speaker,
                reason=(
                    "Negative content is repeatedly delivered with calm or "
                    "neutral emotional tone, suggesting masked affect."
                ),
                evidence={
                    "masked_turn_count": len(masked),
                },
            ),
            windows,
        )

    # ------------------------------------------------------------------ #
    # Aggregation helpers
    # ------------------------------------------------------------------ #
    @classmethod
    def _infer_primary_speaker(
        cls,
        signals: list[InconsistencySignal],
        analytics: AnalyticsBundle,
        aggregated_signals: AggregatedSignals,
    ) -> str | None:
        speaker_scores: dict[str, float] = defaultdict(float)
        for signal in signals:
            if signal.speaker:
                speaker_scores[signal.speaker] += signal.score
            else:
                evidence_speaker = signal.evidence.get("speaker")
                if isinstance(evidence_speaker, str):
                    speaker_scores[evidence_speaker] += signal.score

        if speaker_scores:
            return max(speaker_scores, key=speaker_scores.get)

        if analytics and analytics.speaker_metrics:
            return max(
                analytics.speaker_metrics.values(),
                key=lambda m: m.speaking_ratio,
            ).speaker

        return None

    @staticmethod
    def _score_to_level(score: float) -> str:
        if score >= 0.65:
            return "high"
        if score >= 0.40:
            return "medium"
        if score >= 0.15:
            return "low"
        return "none"

    @staticmethod
    def _build_summary(
        level: str,
        signals: list[InconsistencySignal],
        primary_speaker: str | None,
    ) -> str:
        if level == "none" or not signals:
            return "No clear inconsistency pattern was detected."

        signal_names = ", ".join(s.signal_type for s in signals[:3])
        speaker_part = f" Primary speaker of concern: {primary_speaker}." if primary_speaker else ""
        return f"Inconsistency level is {level}, driven by {signal_names}." f"{speaker_part}"
