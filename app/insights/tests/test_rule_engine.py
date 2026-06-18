"""Unit + edge-case coverage for InsightRuleEngine (the orchestrator).

`run` composes five sub-engines (signal aggregation -> timeline ->
escalation -> inconsistency -> scoring) and three own flag/marker
builders into an `InsightBundle`. It is the engine called from
`service.py`.

Strategy (deliberately all-real, no sub-engine mocks): the five
sub-engines are pure-Python, fast, and already tested, so wiring them
for real gives an honest end-to-end smoke test for free. The three
private builders are pure functions of plain models, exercised directly
for branch coverage. The ONE surgical exception is a monkeypatch of
`_build_inconsistency_markers` to deterministically force the
timeline-resort branch in `run` (real inconsistency windows are
nondeterministic to provoke from raw input).

No heavy ML deps: pure threshold logic, runs in well under a second.
"""

from __future__ import annotations

from app.insights.config.defaults import InsightThresholds
from app.insights.core.analytics_engine import InsightAnalyticsEngine
from app.insights.core.rule_engine import InsightRuleEngine
from app.insights.models.analytics_models import AnalyticsBundle, SessionMetrics, SpeakerMetrics
from app.insights.models.escalation_models import EscalationAssessment
from app.insights.models.inconsistency_models import InconsistencyAssessment, InconsistencyWindow
from app.insights.models.input_models import SentimentInput, SessionInput, UtteranceInput
from app.insights.models.insight_models import InsightBundle, TimelineMarker
from app.insights.models.signal_models import (
    AggregatedSignals,
    EmotionAggregate,
    SentimentAggregate,
    SessionSentimentTrend,
)

R = InsightRuleEngine
THRESH = InsightThresholds()  # dominance .60, tension/interrupt/overlap 2, low_inquiry utt 4


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def _metric(speaker, *, ratio=0.0, word_ratio=0.0, interruptions=0, overlaps=0, q=0, utt=2):
    return SpeakerMetrics(
        speaker=speaker,
        speaking_ratio=ratio,
        word_ratio=word_ratio,
        interruption_count=interruptions,
        overlap_count=overlaps,
        question_count=q,
        utterance_count=utt,
    )


def _analytics_with(speaker_metrics):
    return AnalyticsBundle(session_metrics=SessionMetrics(), speaker_metrics=speaker_metrics)


def _signals(*, speaker_emotion=None, speaker_sentiment=None, session_sentiment=None, trend=None):
    return AggregatedSignals(
        session_sentiment=session_sentiment or SentimentAggregate(),
        session_sentiment_trend=trend or SessionSentimentTrend(),
        speaker_sentiment=speaker_sentiment or {},
        speaker_emotion=speaker_emotion or {},
    )


def _bare_session():
    return SessionInput(
        session_id="s", utterances=[UtteranceInput(id="u1", speaker="A", start=0.0, end=1.0, text="hi")]
    )


def _rich_session():
    """Two-speaker conflict: A dominates time, B interrupts twice, all negative."""

    def U(uid, spk, start, end, score, overlap=False):
        return UtteranceInput(
            id=uid,
            speaker=spk,
            start=start,
            end=end,
            text="some words here?",
            sentiment=SentimentInput(label="negative", score=score),
            overlap=overlap,
        )

    return SessionInput(
        session_id="conflict",
        utterances=[
            U("u1", "A", 0.0, 5.0, 0.2),
            U("u2", "B", 4.0, 6.0, 0.2, overlap=True),  # interrupts u1
            U("u3", "A", 6.0, 12.0, 0.25),
            U("u4", "B", 11.0, 13.0, 0.2, overlap=True),  # interrupts u3
            U("u5", "A", 13.0, 20.0, 0.3),
        ],
    )


# --------------------------------------------------------------------------- #
# run() — real end-to-end integration                                         #
# --------------------------------------------------------------------------- #


def test_run_happy_end_to_end_smoke() -> None:
    session = _rich_session()
    analytics = InsightAnalyticsEngine.run(session)

    bundle = R.run(session, analytics)

    assert isinstance(bundle, InsightBundle)
    assert set(bundle.speaker_insights) == {"A", "B"}
    assert bundle.flags  # session-level flags produced
    assert bundle.timeline  # interruption / dominance markers produced
    assert bundle.scores is not None
    assert bundle.escalation is not None
    assert bundle.inconsistency is not None
    # B interrupted twice -> per-speaker flags present
    assert {f.type for f in bundle.speaker_insights["B"].flags} >= {
        "frequent_interruptions",
        "high_overlap_participation",
    }


def test_run_merges_and_resorts_inconsistency_markers(monkeypatch) -> None:
    # Surgical patch: force one inconsistency marker at t=0 so the merge +
    # resort branch in run() runs deterministically. Everything else real.
    fake = TimelineMarker(
        marker_id="inconsistency_1",
        type="inconsistency_candidate",
        time_sec=0.0,
        severity="low",
        reason="forced",
        evidence={"window_level": "low"},
    )
    monkeypatch.setattr(R, "_build_inconsistency_markers", classmethod(lambda cls, inc: [fake]))

    session = _rich_session()
    analytics = InsightAnalyticsEngine.run(session)
    bundle = R.run(session, analytics)

    assert fake in bundle.timeline  # merged in
    keys = [(m.time_sec, m.marker_id) for m in bundle.timeline]
    assert keys == sorted(keys)  # re-sorted chronologically after merge


def test_all_flags_carry_reason_and_evidence_compliance() -> None:
    # CLAUDE.md mandate: every output must include reason + evidence.
    # This test fails if a new flag type is added without populating both.
    # DO NOT loosen the assertions.
    session = _rich_session()
    analytics = InsightAnalyticsEngine.run(session)
    bundle = R.run(session, analytics)

    all_flags = list(bundle.flags)
    for insight in bundle.speaker_insights.values():
        all_flags.extend(insight.flags)

    assert all_flags, "fixture must produce flags"
    for f in all_flags:
        assert f.reason and isinstance(f.reason, str), f"flag {f.type} has empty reason"
        assert f.evidence and isinstance(f.evidence, dict), f"flag {f.type} has empty evidence"


# --------------------------------------------------------------------------- #
# _build_speaker_insights                                                     #
# --------------------------------------------------------------------------- #


def test_speaker_insights_all_four_flags_fire() -> None:
    metric = _metric("A", ratio=0.5, word_ratio=0.5, interruptions=2, overlaps=2, q=0, utt=5)
    signals = _signals(speaker_emotion={"A": EmotionAggregate(dominant="angry", sample_count=2)})

    result = R._build_speaker_insights(_bare_session(), _analytics_with({"A": metric}), signals, THRESH)

    assert {f.type for f in result["A"].flags} == {
        "frequent_interruptions",
        "high_overlap_participation",
        "low_inquiry_behavior",
        "speaker_emotional_strain",
    }
    assert result["A"].dominance_ratio == 0.5
    assert result["A"].engagement_ratio == 0.5


def test_speaker_insights_quiet_speaker_no_flags_and_no_emotion() -> None:
    metric = _metric("B", interruptions=1, overlaps=1, q=3, utt=3)  # all below thresholds
    result = R._build_speaker_insights(_bare_session(), _analytics_with({"B": metric}), _signals(), THRESH)
    assert result["B"].flags == []
    assert result["B"].emotion is None


def test_speaker_insights_low_inquiry_boundary() -> None:
    fires = R._build_speaker_insights(
        _bare_session(), _analytics_with({"A": _metric("A", q=0, utt=4)}), _signals(), THRESH
    )
    skips = R._build_speaker_insights(
        _bare_session(), _analytics_with({"A": _metric("A", q=0, utt=3)}), _signals(), THRESH
    )

    assert any(f.type == "low_inquiry_behavior" for f in fires["A"].flags)
    assert not any(f.type == "low_inquiry_behavior" for f in skips["A"].flags)


def test_speaker_insights_emotional_strain_negatives() -> None:
    wrong_emotion = _signals(speaker_emotion={"A": EmotionAggregate(dominant="happy", sample_count=5)})
    too_few = _signals(speaker_emotion={"A": EmotionAggregate(dominant="angry", sample_count=1)})
    quiet = _metric("A")

    a = R._build_speaker_insights(_bare_session(), _analytics_with({"A": quiet}), wrong_emotion, THRESH)
    b = R._build_speaker_insights(_bare_session(), _analytics_with({"A": quiet}), too_few, THRESH)

    assert not any(f.type == "speaker_emotional_strain" for f in a["A"].flags)
    assert not any(f.type == "speaker_emotional_strain" for f in b["A"].flags)


def test_speaker_insights_empty_metrics_is_empty_dict() -> None:
    assert R._build_speaker_insights(_bare_session(), _analytics_with({}), _signals(), THRESH) == {}


# --------------------------------------------------------------------------- #
# _build_session_flags                                                        #
# --------------------------------------------------------------------------- #


def _session_flags(metric, **kw):
    return R._build_session_flags(_analytics_with({metric.speaker: metric}), _signals(), THRESH, **kw)


def _flag(flags, ftype):
    matches = [f for f in flags if f.type == ftype]
    return matches[0] if matches else None


def test_session_flag_dominance_severity() -> None:
    high = _session_flags(_metric("A", ratio=0.80))
    medium = _session_flags(_metric("A", ratio=0.65))
    none = _session_flags(_metric("A", ratio=0.30))

    assert _flag(high, "speaker_dominance").severity == "high"
    assert _flag(medium, "speaker_dominance").severity == "medium"
    assert _flag(none, "speaker_dominance") is None


def test_session_flag_high_tension_severity() -> None:
    high = _session_flags(_metric("A", interruptions=4))
    medium = _session_flags(_metric("A", interruptions=2))
    none = _session_flags(_metric("A", interruptions=1, overlaps=1))

    assert _flag(high, "high_tension").severity == "high"
    assert _flag(medium, "high_tension").severity == "medium"
    assert _flag(none, "high_tension") is None


def test_session_flag_negative_mood_on_and_off() -> None:
    def build(sentiment):
        return R._build_session_flags(_analytics_with({}), _signals(session_sentiment=sentiment), THRESH)

    on = build(SentimentAggregate(label="negative", avg_score=0.3, sample_count=5))
    off_label = build(SentimentAggregate(label="positive", avg_score=0.3, sample_count=5))
    off_no_score = build(SentimentAggregate(label="negative", avg_score=None, sample_count=5))
    off_high_score = build(SentimentAggregate(label="negative", avg_score=0.5, sample_count=5))

    assert _flag(on, "negative_session_mood") is not None
    assert _flag(off_label, "negative_session_mood") is None
    assert _flag(off_no_score, "negative_session_mood") is None
    assert _flag(off_high_score, "negative_session_mood") is None


def test_session_flag_declining_tone_on_and_off() -> None:
    on = R._build_session_flags(
        _analytics_with({}), _signals(trend=SessionSentimentTrend(direction="declining")), THRESH
    )
    off = R._build_session_flags(
        _analytics_with({}), _signals(trend=SessionSentimentTrend(direction="improving")), THRESH
    )

    assert _flag(on, "declining_session_tone") is not None
    assert _flag(off, "declining_session_tone") is None


def test_session_flag_escalation_severity_mapping() -> None:
    def build(escalation):
        return R._build_session_flags(_analytics_with({}), _signals(), THRESH, escalation=escalation)

    assert _flag(build(None), "conversation_escalation") is None
    assert _flag(build(EscalationAssessment(level="none")), "conversation_escalation") is None
    assert _flag(build(EscalationAssessment(level="mild", summary="s")), "conversation_escalation").severity == "low"
    assert (
        _flag(build(EscalationAssessment(level="moderate", summary="s")), "conversation_escalation").severity
        == "medium"
    )
    assert _flag(build(EscalationAssessment(level="severe", summary="s")), "conversation_escalation").severity == "high"


def test_session_flag_inconsistency_levels() -> None:
    def build(inconsistency):
        return R._build_session_flags(_analytics_with({}), _signals(), THRESH, inconsistency=inconsistency)

    assert _flag(build(None), "conversation_inconsistency") is None
    assert _flag(build(InconsistencyAssessment(level="none")), "conversation_inconsistency") is None
    assert (
        _flag(build(InconsistencyAssessment(level="low", summary="s")), "conversation_inconsistency").severity == "low"
    )
    assert (
        _flag(build(InconsistencyAssessment(level="medium", summary="s")), "conversation_inconsistency").severity
        == "medium"
    )
    assert (
        _flag(build(InconsistencyAssessment(level="high", summary="s")), "conversation_inconsistency").severity
        == "high"
    )


# --------------------------------------------------------------------------- #
# _build_inconsistency_markers                                                #
# --------------------------------------------------------------------------- #


def test_inconsistency_markers_none_and_level_none() -> None:
    assert R._build_inconsistency_markers(None) == []
    assert R._build_inconsistency_markers(InconsistencyAssessment(level="none")) == []


def test_inconsistency_markers_from_windows() -> None:
    inc = InconsistencyAssessment(
        level="medium",
        windows=[
            InconsistencyWindow(
                start_sec=2.0, end_sec=4.0, level="high", speaker="A", reason="contradiction", evidence={"x": 1}
            ),
            InconsistencyWindow(start_sec=5.0, end_sec=6.0, level="none", reason="weak"),  # unknown -> "low" fallback
        ],
    )

    markers = R._build_inconsistency_markers(inc)

    assert [m.marker_id for m in markers] == ["inconsistency_1", "inconsistency_2"]
    assert markers[0].type == "inconsistency_candidate"
    assert markers[0].severity == "high"
    assert markers[0].evidence["x"] == 1
    assert markers[0].evidence["window_level"] == "high"  # setdefault populated
    assert markers[0].time_sec == 2.0
    assert markers[1].severity == "low"  # window.level "none" -> .get default


# --------------------------------------------------------------------------- #
# Tier 3 candidates surfaced during this test pass
# (resolved: threshold_profile dead param removed -> S2;
#  dead severity_map identity dict removed -> C1.)
# 1. rule_engine.py — run() has NO fault isolation around the five
#    sub-engines; any sub-engine exception aborts the whole bundle. This
#    is intentional: fault handling lives in service.py (see CLAUDE.md
#    "Orchestration + fault isolation"). Documented, not a code fix.
# --------------------------------------------------------------------------- #
