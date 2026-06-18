"""Unit + edge-case coverage for InsightTimelineEngine.

The timeline engine runs five independent detectors over a session and
returns chronologically sorted `TimelineMarker`s:

  dominance_window, engagement_drop (pause), interruption,
  emotional_shift (+ session_tone_decline), high_tension

Every marker must carry a populated `reason` AND `evidence` dict — that
is a CLAUDE.md mandate, enforced here by a compliance tripwire that
triggers all detectors at once and inspects every emitted marker.

No heavy ML deps: pure threshold math, runs in milliseconds.
"""

from __future__ import annotations

from app.insights.config.defaults import InsightThresholds
from app.insights.core.timeline_engine import InsightTimelineEngine
from app.insights.models.analytics_models import AnalyticsBundle, SessionMetrics, SpeakerMetrics
from app.insights.models.input_models import SentimentInput, SessionInput, UtteranceInput
from app.insights.models.signal_models import (
    AggregatedSignals,
    SentimentTrendPoint,
    SessionSentimentTrend,
)

T = InsightTimelineEngine
THRESH = InsightThresholds()  # defaults: dominance .60/.75, pause 3/6s, tension 2/4, shift .45/.70


# --------------------------------------------------------------------------- #
# Builders                                                                    #
# --------------------------------------------------------------------------- #


def _utt(uid, speaker, start, end, label=None, score=None):
    sentiment = SentimentInput(label=label, score=score) if (label is not None or score is not None) else None
    return UtteranceInput(id=uid, speaker=speaker, start=start, end=end, text="t", sentiment=sentiment)


def _metric(speaker, *, ratio=0.0, word_ratio=0.0, interruptions=0, overlaps=0, first=0.0, last=10.0):
    return SpeakerMetrics(
        speaker=speaker,
        speaking_ratio=ratio,
        word_ratio=word_ratio,
        interruption_count=interruptions,
        overlap_count=overlaps,
        first_spoke_at_sec=first,
        last_spoke_at_sec=last,
    )


def _analytics(speaker_metrics=None):
    return AnalyticsBundle(session_metrics=SessionMetrics(), speaker_metrics=speaker_metrics or {})


def _signals(direction=None, points=None, slope=None):
    return AggregatedSignals(
        session_sentiment_trend=SessionSentimentTrend(direction=direction, slope=slope, points=points or [])
    )


def _point(uid, speaker, start, score):
    return SentimentTrendPoint(utterance_id=uid, speaker=speaker, start=start, end=start + 1.0, score=score)


def _session(utts):
    return SessionInput(session_id="s", utterances=utts)


def _max_coverage_inputs():
    """One fixture that triggers ALL detectors / all 6 marker types.

    u1 A 0-5 (pos .9) ... u2 B 4-6 interrupts u1 ... 3.5s gap ...
    u3 A 9.5-10 (neg .2) -> emotional shift delta .7 (high).
    A dominant (.8) + 2 interruptions (tension). Trend declining
    with a -.4 drop -> session_tone_decline.
    """
    utts = [
        _utt("u1", "A", 0.0, 5.0, "positive", 0.9),
        _utt("u2", "B", 4.0, 6.0),
        _utt("u3", "A", 9.5, 10.0, "negative", 0.2),
    ]
    analytics = _analytics(
        {
            "A": _metric("A", ratio=0.8, word_ratio=0.8, interruptions=2, first=0.0, last=10.0),
            "B": _metric("B", ratio=0.2, word_ratio=0.2, first=4.0, last=6.0),
        }
    )
    signals = _signals(
        direction="declining",
        points=[_point("u1", "A", 0.0, 0.9), _point("u3", "A", 9.5, 0.5)],
        slope=-0.04,
    )
    return _session(utts), analytics, signals


# --------------------------------------------------------------------------- #
# build_timeline (public)                                                     #
# --------------------------------------------------------------------------- #


def test_build_timeline_happy_emits_all_marker_types_sorted() -> None:
    session, analytics, signals = _max_coverage_inputs()

    markers = T.build_timeline(session, analytics, signals, THRESH)

    types = {m.type for m in markers}
    assert types == {
        "dominance_window",
        "engagement_drop",
        "interruption",
        "emotional_shift",
        "session_tone_decline",
        "high_tension",
    }
    keys = [(m.time_sec, m.marker_id) for m in markers]
    assert keys == sorted(keys)


def test_build_timeline_calm_session_is_empty() -> None:
    # Single quiet utterance, no speaker metrics, no trend -> nothing fires.
    markers = T.build_timeline(_session([_utt("u1", "A", 0.0, 1.0)]), _analytics(), _signals(), THRESH)
    assert markers == []


def test_marker_evidence_and_reason_compliance() -> None:
    # CLAUDE.md mandate: every output must include reason + evidence.
    # This test fails if a new marker type is added without populating
    # both. DO NOT loosen the assertions.
    session, analytics, signals = _max_coverage_inputs()

    markers = T.build_timeline(session, analytics, signals, THRESH)

    assert markers, "fixture must trigger every detector"
    for m in markers:
        assert m.evidence, f"marker {m.marker_id} ({m.type}) has empty evidence"
        assert isinstance(m.evidence, dict)
        assert m.reason and isinstance(m.reason, str), f"marker {m.marker_id} ({m.type}) has empty reason"


# --------------------------------------------------------------------------- #
# _detect_dominance_markers                                                   #
# --------------------------------------------------------------------------- #


def test_dominance_medium_high_and_none() -> None:
    analytics = _analytics(
        {
            "med": _metric("med", ratio=0.65),
            "high": _metric("high", ratio=0.80),
            "quiet": _metric("quiet", ratio=0.30),
        }
    )

    markers = {m.speaker: m for m in T._detect_dominance_markers(analytics, THRESH)}

    assert set(markers) == {"med", "high"}  # quiet below 0.60 -> no marker
    assert markers["med"].severity == "medium"
    assert markers["high"].severity == "high"
    assert markers["med"].evidence["speaking_ratio"] == 0.65


def test_dominance_word_ratio_alone_triggers() -> None:
    analytics = _analytics({"w": _metric("w", ratio=0.1, word_ratio=0.65)})
    markers = T._detect_dominance_markers(analytics, THRESH)
    assert len(markers) == 1
    assert markers[0].severity == "medium"


def test_dominance_none_first_spoke_falls_back_to_zero() -> None:
    metric = _metric("a", ratio=0.7, first=None, last=None)
    markers = T._detect_dominance_markers(_analytics({"a": metric}), THRESH)
    assert markers[0].time_sec == 0.0
    assert markers[0].start_sec is None


# --------------------------------------------------------------------------- #
# _detect_pause_markers                                                       #
# --------------------------------------------------------------------------- #


def test_pause_medium_and_high() -> None:
    utts = [
        _utt("u1", "A", 0.0, 1.0),
        _utt("u2", "B", 5.0, 6.0),  # gap 4.0 -> medium
        _utt("u3", "A", 13.0, 14.0),  # gap 7.0 -> high
    ]

    markers = T._detect_pause_markers(utts, THRESH)

    assert [m.severity for m in markers] == ["medium", "high"]
    assert markers[0].evidence["pause_duration_sec"] == 4.0
    assert markers[0].evidence["speaker_before"] == "A"
    assert markers[0].evidence["speaker_after"] == "B"


def test_pause_exact_threshold_boundary_fires() -> None:
    # KNOWN QUIRK / off-by-zero tripwire: the pause check is `gap >=
    # threshold` (non-strict), unlike the analytics engine's `gap > 0`.
    # An exactly-3.00s gap IS an engagement_drop. Pinned so a future
    # switch to strict `>` is a conscious decision.
    utts = [_utt("u1", "A", 0.0, 1.0), _utt("u2", "B", 4.0, 5.0)]  # gap exactly 3.0

    markers = T._detect_pause_markers(utts, THRESH)

    assert len(markers) == 1
    assert markers[0].evidence["pause_duration_sec"] == 3.0


def test_pause_below_threshold_is_silent() -> None:
    utts = [_utt("u1", "A", 0.0, 1.0), _utt("u2", "B", 3.5, 4.0)]  # gap 2.5 < 3.0
    assert T._detect_pause_markers(utts, THRESH) == []


# --------------------------------------------------------------------------- #
# _detect_interruption_markers                                                #
# --------------------------------------------------------------------------- #


def test_interruption_detected_with_evidence() -> None:
    utts = [_utt("u1", "A", 0.0, 5.0), _utt("u2", "B", 3.0, 6.0)]

    markers = T._detect_interruption_markers(utts)

    assert len(markers) == 1
    m = markers[0]
    assert m.speaker == "B"
    assert m.severity == "medium"
    assert m.evidence["interrupted_speaker"] == "A"
    assert m.evidence["overlap_sec"] == 2.0
    assert m.evidence["utterance_id"] == "u2"


def test_interruption_requires_different_speaker_and_overlap() -> None:
    same_speaker = [_utt("u1", "A", 0.0, 5.0), _utt("u2", "A", 3.0, 6.0)]
    no_overlap = [_utt("u1", "A", 0.0, 2.0), _utt("u2", "B", 2.0, 4.0)]  # touching, not overlapping

    assert T._detect_interruption_markers(same_speaker) == []
    assert T._detect_interruption_markers(no_overlap) == []


# --------------------------------------------------------------------------- #
# _detect_emotional_shift_markers                                             #
# --------------------------------------------------------------------------- #


def test_emotional_shift_medium_down_and_high_up() -> None:
    down = [
        _utt("u1", "A", 0.0, 1.0, "positive", 0.9),
        _utt("u2", "A", 2.0, 3.0, "positive", 0.4),  # delta .5 -> medium, down
    ]
    up = [
        _utt("u1", "A", 0.0, 1.0, "negative", 0.1),
        _utt("u2", "A", 2.0, 3.0, "negative", 0.85),  # delta .75 -> high, up
    ]

    m_down = T._detect_emotional_shift_markers(down, THRESH, _signals())
    m_up = T._detect_emotional_shift_markers(up, THRESH, _signals())

    assert len(m_down) == 1 and m_down[0].severity == "medium"
    assert m_down[0].evidence["direction"] == "down"
    assert len(m_up) == 1 and m_up[0].severity == "high"
    assert m_up[0].evidence["direction"] == "up"
    assert m_up[0].evidence["delta"] == 0.75


def test_emotional_shift_label_flip_with_small_delta_does_not_fire() -> None:
    # A label change alone no longer fires a marker: the swing must also
    # clear the delta threshold. A 0.01 positive->negative flip is below
    # threshold, so no marker is emitted.
    utts = [
        _utt("u1", "A", 0.0, 1.0, "positive", 0.50),
        _utt("u2", "A", 2.0, 3.0, "negative", 0.49),  # delta .01, label flipped
    ]

    markers = T._detect_emotional_shift_markers(utts, THRESH, _signals())

    assert markers == []


def test_emotional_shift_label_flip_with_large_delta_fires() -> None:
    # A flip that also clears the threshold still fires (delta does the work).
    utts = [
        _utt("u1", "A", 0.0, 1.0, "positive", 0.90),
        _utt("u2", "A", 2.0, 3.0, "negative", 0.20),  # delta .70, label flipped
    ]

    markers = T._detect_emotional_shift_markers(utts, THRESH, _signals())

    assert len(markers) == 1
    assert markers[0].evidence["previous_label"] == "positive"
    assert markers[0].evidence["current_label"] == "negative"


def test_emotional_shift_mid_band_and_noise_band_are_silent() -> None:
    mid_band = [  # 0.06 <= delta < 0.45, same label -> no marker
        _utt("u1", "A", 0.0, 1.0, "positive", 0.50),
        _utt("u2", "A", 2.0, 3.0, "positive", 0.70),
    ]
    noise = [  # delta < 0.06, same label -> skipped at noise gate
        _utt("u1", "A", 0.0, 1.0, "positive", 0.50),
        _utt("u2", "A", 2.0, 3.0, "positive", 0.51),
    ]

    assert T._detect_emotional_shift_markers(mid_band, THRESH, _signals()) == []
    assert T._detect_emotional_shift_markers(noise, THRESH, _signals()) == []


def test_emotional_shift_missing_sentiment_skipped() -> None:
    utts = [
        _utt("u1", "A", 0.0, 1.0),  # no sentiment -> tracked, no compare
        _utt("u2", "A", 2.0, 3.0, "positive", 0.9),  # prev has no score -> no compare
        _utt("u3", "B", 4.0, 5.0, score=0.2),  # different speaker, first sample
    ]

    assert T._detect_emotional_shift_markers(utts, THRESH, _signals()) == []


def test_emotional_shift_none_labels_fall_back_to_empty_string() -> None:
    # Score-only sentiments: delta .7 fires, label fields default to "".
    utts = [
        _utt("u1", "A", 0.0, 1.0, score=0.9),
        _utt("u2", "A", 2.0, 3.0, score=0.2),
    ]

    markers = T._detect_emotional_shift_markers(utts, THRESH, _signals())

    assert len(markers) == 1
    assert markers[0].evidence["previous_label"] == ""
    assert markers[0].evidence["current_label"] == ""


def test_session_tone_decline_marker_and_slope_none_fallback() -> None:
    # KNOWN QUIRK tripwire: evidence trend_slope falls back to 0.0 when the
    # trend has no slope value (None). Pinned: 0.0 is indistinguishable
    # from "flat slope" in the evidence — a future change should emit the
    # absence explicitly instead.
    signals = _signals(
        direction="declining",
        points=[
            _point("p1", "A", 0.0, 0.9),
            _point("p2", "A", 5.0, None),  # None score -> skipped pair
            _point("p3", "A", 10.0, 0.5),  # vs p2 None skipped; needs prev numeric
            _point("p4", "A", 15.0, 0.3),  # 0.5 -> 0.3 = -0.2 <= -0.08 -> fires here
        ],
        slope=None,
    )

    markers = T._detect_emotional_shift_markers([_utt("u1", "A", 0.0, 1.0)], THRESH, signals)

    assert len(markers) == 1
    m = markers[0]
    assert m.type == "session_tone_decline"
    assert m.time_sec == 15.0  # first qualifying drop point
    assert m.evidence["trend_slope"] == 0.0  # None -> 0.0 fallback
    assert m.evidence["trend_direction"] == "declining"


def test_session_tone_decline_requires_declining_direction_and_big_drop() -> None:
    improving = _signals(direction="improving", points=[_point("p1", "A", 0.0, 0.2), _point("p2", "A", 5.0, 0.9)])
    shallow = _signals(direction="declining", points=[_point("p1", "A", 0.0, 0.9), _point("p2", "A", 5.0, 0.85)])

    assert T._detect_emotional_shift_markers([_utt("u1", "A", 0.0, 1.0)], THRESH, improving) == []
    assert T._detect_emotional_shift_markers([_utt("u1", "A", 0.0, 1.0)], THRESH, shallow) == []


# --------------------------------------------------------------------------- #
# _detect_high_tension_markers                                                #
# --------------------------------------------------------------------------- #


def test_high_tension_medium_high_and_none() -> None:
    analytics = _analytics(
        {
            "med": _metric("med", interruptions=2),
            "high": _metric("high", interruptions=4),
            "calm": _metric("calm", interruptions=1, overlaps=1),
        }
    )

    markers = {m.speaker: m for m in T._detect_high_tension_markers(analytics, THRESH)}

    assert set(markers) == {"med", "high"}
    assert markers["med"].severity == "medium"
    assert markers["high"].severity == "high"
    assert markers["med"].evidence["interruption_count"] == 2


def test_high_tension_overlap_count_alone_triggers() -> None:
    analytics = _analytics({"o": _metric("o", overlaps=4)})
    markers = T._detect_high_tension_markers(analytics, THRESH)
    assert len(markers) == 1
    assert markers[0].severity == "high"
    assert markers[0].evidence["overlap_count"] == 4
