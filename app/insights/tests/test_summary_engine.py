"""Unit + edge-case coverage for InsightSummaryEngine.

The summary engine is a *text generator*: it turns an `AnalyticsBundle`
+ `InsightBundle` into human-readable `SummaryBundle` strings. No float
math, but dense threshold branching (tone / balance / pause / conflict /
style / question bands) plus optional-part assembly.

Two-pronged coverage:
  * pure threshold/describe helpers tested directly (cheap, parametrized)
  * the four orchestrators (`run`, overall, per-speaker, concerns,
    key-moments) tested through crafted bundles.

Contract note: the engine's "empty" path is a *zeroed* `AnalyticsBundle`
(total_utterances == 0). It reads only analytics + insights — there is no
raw-session input to the summary text.

No heavy ML deps: pure string assembly, runs in milliseconds.
"""

from __future__ import annotations

import pytest

from app.insights.core.summary_engine import InsightSummaryEngine
from app.insights.models.analytics_models import AnalyticsBundle, SessionMetrics, SpeakerMetrics
from app.insights.models.api_models import SummaryBundle
from app.insights.models.escalation_models import EscalationAssessment
from app.insights.models.inconsistency_models import InconsistencyAssessment
from app.insights.models.insight_models import (
    InsightBundle,
    InsightFlag,
    InsightScores,
    SpeakerInsight,
    TimelineMarker,
)
from app.insights.models.signal_models import (
    EmotionAggregate,
    SentimentAggregate,
    SessionSentimentTrend,
)

S = InsightSummaryEngine


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def _spk_metric(speaker, *, ratio=0.5, utt=2, words=4, time=2.0, q=0, interruptions=0, overlaps=0, word_ratio=0.5):
    return SpeakerMetrics(
        speaker=speaker,
        speaking_ratio=ratio,
        utterance_count=utt,
        word_count=words,
        speaking_time_sec=time,
        question_count=q,
        interruption_count=interruptions,
        overlap_count=overlaps,
        word_ratio=word_ratio,
    )


def _analytics(
    *, total_utt=2, total_speakers=2, total_dur=5.0, total_words=9, avg_pause=0.5, max_pause=1.0, speaker_metrics=None
):
    sm = SessionMetrics(
        total_utterances=total_utt,
        total_speakers=total_speakers,
        total_duration_sec=total_dur,
        total_words=total_words,
        avg_pause_sec=avg_pause,
        max_pause_sec=max_pause,
    )
    return AnalyticsBundle(session_metrics=sm, speaker_metrics=speaker_metrics or {})


def _insights(**kw):
    base = {"scores": InsightScores()}
    base.update(kw)
    return InsightBundle(**base)


def _scores_insights(*, engagement=0.0, conflict=0.0):
    return _insights(scores=InsightScores(engagement_score=engagement, conflict_score=conflict))


# --------------------------------------------------------------------------- #
# Pure threshold helpers                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("severity", "rank"), [("low", 1), ("medium", 2), ("high", 3), ("unknown", 0), ("", 0)])
def test_severity_rank(severity, rank) -> None:
    # "unknown"/"" -> 0 is a KNOWN QUIRK (see punch list #3): unrecognised
    # severities silently sort lowest instead of raising.
    assert S._severity_rank(severity) == rank


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.80, "highly imbalanced"), (0.65, "somewhat imbalanced"), (0.50, "fairly balanced")],
)
def test_infer_balance_label(ratio, expected) -> None:
    assert S._infer_balance_label({"a": _spk_metric("a", ratio=ratio)}) == expected


def test_infer_balance_label_empty_is_balanced() -> None:
    assert S._infer_balance_label({}) == "balanced"


@pytest.mark.parametrize(
    ("eng", "conf", "expected"),
    [
        (0.1, 0.8, "strained"),
        (0.8, 0.1, "active and reasonably cooperative"),
        (0.2, 0.1, "low-energy"),
        (0.5, 0.5, "moderately engaged"),
    ],
)
def test_infer_session_tone(eng, conf, expected) -> None:
    assert S._infer_session_tone(_scores_insights(engagement=eng, conflict=conf)) == expected


@pytest.mark.parametrize(
    ("avg", "mx", "expected"),
    [
        (0.5, 6.5, "some substantial hesitation or disengagement windows"),
        (3.5, 4.0, "noticeable pauses between responses"),
        (0.5, 2.0, "a relatively smooth response flow"),
        (2.0, 2.0, "occasional pauses but generally steady pacing"),
    ],
)
def test_describe_pause_behavior(avg, mx, expected) -> None:
    assert S._describe_pause_behavior(avg, mx) == expected


@pytest.mark.parametrize(
    ("conflict", "needle"),
    [(0.8, "elevated tension"), (0.5, "moderate signs of friction"), (0.2, "relatively orderly")],
)
def test_describe_conflict(conflict, needle) -> None:
    assert needle in S._describe_conflict(_scores_insights(conflict=conflict))


@pytest.mark.parametrize(("ratio", "expected"), [(0.70, "dominant"), (0.10, "limited"), (0.40, "moderately active")])
def test_describe_speaker_style(ratio, expected) -> None:
    assert S._describe_speaker_style(_spk_metric("a", ratio=ratio)) == expected


@pytest.mark.parametrize(
    ("q", "utt", "needle"),
    [
        (0, 0, "could not be assessed"),
        (0, 5, "did not ask questions"),
        (3, 5, "frequently"),
        (1, 5, "occasional"),
        (1, 10, "small number"),
    ],
)
def test_describe_question_behavior(q, utt, needle) -> None:
    assert needle in S._describe_question_behavior(q, utt)


def test_get_dominant_speaker_empty_is_none() -> None:
    assert S._get_dominant_speaker({}) is None


def test_get_dominant_speaker_picks_max_ratio() -> None:
    sm = {"a": _spk_metric("a", ratio=0.3), "b": _spk_metric("b", ratio=0.7)}
    assert S._get_dominant_speaker(sm) == "b"


# --------------------------------------------------------------------------- #
# Describe sub-objects                                                        #
# --------------------------------------------------------------------------- #


def test_describe_escalation_empty_and_none_level() -> None:
    assert S._describe_escalation(_insights()) == ""  # escalation is None
    assert S._describe_escalation(_insights(escalation=EscalationAssessment(level="none"))) == ""


def test_describe_escalation_populated() -> None:
    ins = _insights(escalation=EscalationAssessment(level="severe", score=0.9, summary="Tension rose."))
    out = S._describe_escalation(ins)
    assert "severe" in out and "0.90" in out and "Tension rose." in out


def test_describe_inconsistency_empty_and_none_level() -> None:
    assert S._describe_inconsistency(_insights()) == ""
    assert S._describe_inconsistency(_insights(inconsistency=InconsistencyAssessment(level="none"))) == ""


def test_describe_inconsistency_populated() -> None:
    ins = _insights(inconsistency=InconsistencyAssessment(level="high", score=0.8, summary="Story changed."))
    out = S._describe_inconsistency(ins)
    assert "high" in out and "0.80" in out and "Story changed." in out


def test_describe_session_sentiment_empty() -> None:
    assert S._describe_session_sentiment(_insights()) == ""
    assert S._describe_session_sentiment(_insights(session_sentiment=SentimentAggregate(sample_count=0))) == ""


def test_describe_session_sentiment_populated_with_trend_and_avg() -> None:
    ins = _insights(
        session_sentiment=SentimentAggregate(label="positive", avg_score=0.7, sample_count=5),
        session_sentiment_trend=SessionSentimentTrend(direction="improving"),
    )
    out = S._describe_session_sentiment(ins)
    assert "positive" in out and "improving" in out and "0.70" in out


def test_describe_session_sentiment_no_avg_score_omits_average() -> None:
    ins = _insights(session_sentiment=SentimentAggregate(label="neutral", avg_score=None, sample_count=3))
    out = S._describe_session_sentiment(ins)
    assert "neutral" in out and "average score" not in out


def test_describe_session_emotion_empty_paths() -> None:
    assert S._describe_session_emotion(_insights()) == ""
    assert S._describe_session_emotion(_insights(session_emotion=EmotionAggregate(sample_count=0))) == ""
    assert S._describe_session_emotion(_insights(session_emotion=EmotionAggregate(dominant=None, sample_count=3))) == ""


def test_describe_session_emotion_populated() -> None:
    ins = _insights(session_emotion=EmotionAggregate(dominant="frustration", sample_count=3))
    assert "frustration" in S._describe_session_emotion(ins)


# --------------------------------------------------------------------------- #
# Orchestrators                                                               #
# --------------------------------------------------------------------------- #


def test_run_happy_populates_all_four_fields() -> None:
    analytics = _analytics(
        total_utt=4,
        total_speakers=2,
        speaker_metrics={
            "agent": _spk_metric("agent", ratio=0.7, q=2, utt=3, interruptions=1, overlaps=1),
            "customer": _spk_metric("customer", ratio=0.3),
        },
    )
    insights = _insights(
        scores=InsightScores(engagement_score=0.8, conflict_score=0.1),
        session_sentiment=SentimentAggregate(label="positive", avg_score=0.6, sample_count=4),
        session_emotion=EmotionAggregate(dominant="calm", sample_count=4),
        session_sentiment_trend=SessionSentimentTrend(direction="stable"),
        escalation=EscalationAssessment(level="mild", score=0.3, summary="Slight rise."),
        inconsistency=InconsistencyAssessment(level="low", score=0.2, summary="Minor drift."),
        flags=[InsightFlag(type="aggression", severity="high", reason="raised voice", speaker="agent")],
        timeline=[
            TimelineMarker(marker_id="m1", type="high_tension", time_sec=10.0, severity="high", reason="conflict")
        ],
        speaker_insights={
            "agent": SpeakerInsight(
                speaker="agent",
                sentiment=SentimentAggregate(label="negative", sample_count=3),
                emotion=EmotionAggregate(dominant="anger", sample_count=3),
                flags=[InsightFlag(type="aggression", severity="high", reason="raised voice")],
            )
        },
    )

    bundle = S.run(analytics, insights)

    assert isinstance(bundle, SummaryBundle)
    assert "utterances" in bundle.overall_summary
    assert set(bundle.speaker_summaries) == {"agent", "customer"}
    assert bundle.notable_concerns  # non-empty
    assert bundle.key_moments_summary  # non-empty


def test_overall_summary_zeroed_analytics_returns_no_content() -> None:
    out = S._build_overall_summary(_analytics(total_utt=0, speaker_metrics={}), _insights())
    assert out == "No conversational content was available to summarize."


def test_overall_summary_flag_severity_cascade() -> None:
    spk = {"a": _spk_metric("a", ratio=1.0)}

    def build(flags):
        return S._build_overall_summary(_analytics(total_utt=2, speaker_metrics=spk), _insights(flags=flags))

    assert "high-severity concern" in build([InsightFlag(type="t", severity="high", reason="r")])
    assert "medium-severity signal" in build([InsightFlag(type="t", severity="medium", reason="r")])
    assert "No major high-severity" in build([])


def test_overall_summary_no_speakers_uses_balanced_overall_branch() -> None:
    # total_utterances > 0 but no per-speaker metrics -> dominant speaker None.
    out = S._build_overall_summary(_analytics(total_utt=3, speaker_metrics={}), _insights())
    assert "balanced overall" in out


def test_single_speaker_summary_none_insight() -> None:
    out = S._build_single_speaker_summary(_spk_metric("agent", ratio=0.5), None)
    assert "agent" in out
    assert "No major speaker-level concerns were flagged." in out


def test_single_speaker_summary_full_insight() -> None:
    si = SpeakerInsight(
        speaker="agent",
        sentiment=SentimentAggregate(label="negative", sample_count=4),
        emotion=EmotionAggregate(dominant="anger", sample_count=4),
        flags=[InsightFlag(type="aggression", severity="high", reason="r")],
    )
    out = S._build_single_speaker_summary(
        _spk_metric("agent", ratio=0.7, q=3, utt=5, interruptions=2, overlaps=1),
        si,
    )
    assert "mostly negative" in out
    assert "anger" in out
    assert "interruption event" in out
    assert "overlapping turn" in out
    assert "aggression" in out


def test_notable_concerns_empty_when_no_flags() -> None:
    assert S._build_notable_concerns(_insights(flags=[])) == []


def test_notable_concerns_dedupes_identical_text() -> None:
    flag = InsightFlag(type="t", severity="high", reason="late delivery", speaker="agent")
    out = S._build_notable_concerns(_insights(flags=[flag, flag]))
    assert out == ["agent: late delivery"]  # deduped + speaker prefix


def test_notable_concerns_caps_at_six() -> None:
    flags = [InsightFlag(type="t", severity="high", reason=f"reason {i}") for i in range(8)]
    out = S._build_notable_concerns(_insights(flags=flags))
    assert len(out) == 6  # capped; no speaker -> no prefix


def test_key_moments_empty_timeline_fallback() -> None:
    out = S._build_key_moments_summary(_insights(timeline=[]))
    assert out == "No notable timeline markers were generated for this session."


def test_key_moments_populated_uses_reason() -> None:
    marker = TimelineMarker(
        marker_id="m1", type="high_tension", time_sec=12.5, severity="high", reason="Voices raised", speaker="agent"
    )
    out = S._build_key_moments_summary(_insights(timeline=[marker]))
    assert "12.50s" in out and "high tension" in out and "voices raised" in out  # reason lower-cased


def test_marker_to_sentence_empty_reason_falls_back() -> None:
    # KNOWN QUIRK (punch list #4): reason is a required str but may be "",
    # in which case the sentence degrades to a generic detected-marker line.
    marker = TimelineMarker(marker_id="m1", type="interruption", time_sec=3.0, severity="low", reason="")
    assert S._marker_to_sentence(marker) == "At 3.00s, a interruption marker was detected."


# --------------------------------------------------------------------------- #
# Tier 3 candidates surfaced during this test pass
# (resolved: speaker_insight type hint -> commit 4466c45;
#  vestigial session param removed -> S1.)
# 1. _severity_rank silently ranks unknown severities at 0.
#    Either raise on unknown OR add explicit "unknown" rank
#    above 0. Pinned via tripwire test in this file.
# 2. _marker_to_sentence falls back to "marker was detected"
#    when reason is empty string. Either require non-empty
#    reason at the Pydantic model level OR keep the fallback
#    explicit. Pinned via tripwire test.
# --------------------------------------------------------------------------- #
