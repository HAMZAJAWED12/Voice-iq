"""Unit + edge-case coverage for InsightAnalyticsEngine.

The analytics engine turns a validated `SessionInput` into an
`AnalyticsBundle` of pure timing/word/question math — it never reads
sentiment or emotion. Public entry is `run`; the three private
computors are exercised both through `run` (which sorts first) and
directly (to reach the empty-input fail-soft paths that `run` can never
hit, since `SessionInput` rejects an empty session).

Three KNOWN-QUIRK tripwires lock surprising-but-current math behavior.

No heavy ML deps: pure arithmetic, runs in milliseconds.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.insights.core.analytics_engine import InsightAnalyticsEngine
from app.insights.models.analytics_models import AnalyticsBundle, SessionMetrics
from app.insights.models.input_models import EmotionInput, SentimentInput, SessionInput, UtteranceInput

E = InsightAnalyticsEngine


def _utt(uid, speaker, start, end, text="", word_count=None, overlap=False, **kw):
    return UtteranceInput(
        id=uid,
        speaker=speaker,
        start=start,
        end=end,
        text=text,
        word_count=word_count,
        overlap=overlap,
        **kw,
    )


def _session(utterances, session_id="s"):
    return SessionInput(session_id=session_id, utterances=utterances)


# --------------------------------------------------------------------------- #
# run() — happy / single speaker                                              #
# --------------------------------------------------------------------------- #


def test_happy_multi_speaker() -> None:
    session = _session(
        [
            _utt("u1", "agent", 0.0, 2.0, "Hello there", word_count=2),
            _utt("u2", "customer", 2.5, 5.0, "My order is late?", word_count=4),
            _utt("u3", "agent", 6.0, 7.0, "Sorry about that", word_count=3),
        ]
    )

    bundle = E.run(session)
    sm = bundle.session_metrics

    assert isinstance(bundle, AnalyticsBundle)
    assert sm.total_utterances == 3
    assert sm.total_speakers == 2
    assert sm.total_words == 9
    assert sm.total_questions == 1
    assert sm.total_duration_sec == 5.5  # 2.0 + 2.5 + 1.0
    assert sm.total_pauses == 2  # gaps 0.5 and 1.0
    assert sm.avg_pause_sec == 0.75
    assert sm.max_pause_sec == 1.0
    assert sm.avg_utterance_length_words == 3.0
    assert sm.avg_utterance_duration_sec == pytest.approx(1.833, abs=1e-3)

    assert set(bundle.speaker_metrics) == {"agent", "customer"}
    agent = bundle.speaker_metrics["agent"]
    customer = bundle.speaker_metrics["customer"]
    assert agent.speaking_ratio == 0.5455  # 3.0 / 5.5
    assert customer.speaking_ratio == 0.4545  # 2.5 / 5.5
    assert agent.speaking_ratio + customer.speaking_ratio == pytest.approx(1.0, abs=1e-3)
    assert customer.question_count == 1
    assert agent.interruption_count == 0
    assert len(bundle.pauses) == 2


def test_single_speaker_ratio_one_no_interruptions() -> None:
    session = _session(
        [
            _utt("u1", "agent", 0.0, 2.0, "hi", word_count=1),
            _utt("u2", "agent", 3.0, 5.0, "bye", word_count=1),
        ]
    )

    bundle = E.run(session)

    assert bundle.session_metrics.total_speakers == 1
    agent = bundle.speaker_metrics["agent"]
    assert agent.speaking_ratio == 1.0
    assert agent.word_ratio == 1.0
    assert agent.interruption_count == 0


def test_interruption_credited_to_interrupter() -> None:
    # customer starts (3.0) before agent finishes (5.0): time overlap.
    session = _session(
        [
            _utt("u1", "agent", 0.0, 5.0, "long turn", word_count=2),
            _utt("u2", "customer", 3.0, 6.0, "cutting in", word_count=2, overlap=True),
        ]
    )

    bundle = E.run(session)

    assert bundle.speaker_metrics["customer"].interruption_count == 1
    assert bundle.speaker_metrics["agent"].interruption_count == 0
    assert bundle.speaker_metrics["customer"].overlap_count == 1
    # Negative gap -> no pause recorded.
    assert bundle.session_metrics.total_pauses == 0


# --------------------------------------------------------------------------- #
# run() — extreme values                                                      #
# --------------------------------------------------------------------------- #


def test_zero_second_and_identical_timestamps() -> None:
    session = _session(
        [
            _utt("u1", "agent", 1.0, 1.0, "a", word_count=1),
            _utt("u2", "customer", 1.0, 1.0, "b", word_count=1),
        ]
    )

    bundle = E.run(session)
    sm = bundle.session_metrics

    assert sm.total_duration_sec == 0.0
    assert sm.avg_utterance_duration_sec == 0.0
    # Identical boundary (gap == 0) is NOT a pause.
    assert sm.total_pauses == 0
    assert sm.avg_pause_sec == 0.0
    assert sm.max_pause_sec == 0.0
    # Zero total speaking time -> ratios guard to 0.0 (see quirk test below).
    assert bundle.speaker_metrics["agent"].speaking_ratio == 0.0


def test_huge_pause_over_60s() -> None:
    session = _session(
        [
            _utt("u1", "agent", 0.0, 1.0, "hi", word_count=1),
            _utt("u2", "customer", 100.0, 101.0, "back", word_count=1),
        ]
    )

    bundle = E.run(session)

    assert bundle.session_metrics.total_pauses == 1
    assert bundle.session_metrics.max_pause_sec == 99.0  # 100.0 - 1.0, no cap
    assert bundle.session_metrics.avg_pause_sec == 99.0
    assert len(bundle.pauses) == 1
    assert bundle.pauses[0].duration_sec == 99.0


def test_questions_counted_as_substring_not_per_mark() -> None:
    session = _session([_utt("u1", "agent", 0.0, 1.0, "what? really??", word_count=2)])

    bundle = E.run(session)

    # Two '?' but one utterance -> counts once.
    assert bundle.session_metrics.total_questions == 1
    assert bundle.speaker_metrics["agent"].question_count == 1


# --------------------------------------------------------------------------- #
# run() — missing optional fields / sort                                      #
# --------------------------------------------------------------------------- #


def test_missing_word_count_falls_back_to_text_and_ignores_sentiment_emotion() -> None:
    session = _session(
        [
            _utt(
                "u1",
                "agent",
                0.0,
                1.0,
                "one two three",
                word_count=None,  # -> len(text.split()) == 3
                sentiment=SentimentInput(label="positive", score=0.8),
                emotion=EmotionInput(values={"happy": 0.5}),
            )
        ]
    )

    bundle = E.run(session)

    assert bundle.session_metrics.total_words == 3
    assert bundle.speaker_metrics["agent"].word_count == 3


def test_unsorted_input_is_sorted_before_compute() -> None:
    # Provided late-then-early; run() must sort so the pause runs early->late.
    session = _session(
        [
            _utt("late", "agent", 5.0, 6.0, "second", word_count=1),
            _utt("early", "customer", 0.0, 1.0, "first", word_count=1),
        ]
    )

    bundle = E.run(session)

    assert bundle.session_metrics.total_pauses == 1
    assert bundle.pauses[0].speaker_before == "customer"  # the early utterance
    assert bundle.pauses[0].speaker_after == "agent"  # the late utterance


# --------------------------------------------------------------------------- #
# Direct helper calls — empty fail-soft (unreachable through run)             #
# --------------------------------------------------------------------------- #


def test_session_metrics_empty_is_zeroed() -> None:
    sm = E._compute_session_metrics([])

    assert isinstance(sm, SessionMetrics)
    assert sm.total_utterances == 0
    assert sm.total_speakers == 0
    assert sm.total_words == 0
    assert sm.total_duration_sec == 0.0
    assert sm.total_questions == 0
    assert sm.total_pauses == 0
    assert sm.avg_pause_sec == 0.0
    assert sm.max_pause_sec == 0.0
    assert sm.avg_utterance_length_words == 0.0
    assert sm.avg_utterance_duration_sec == 0.0


def test_speaker_metrics_empty_is_empty_dict() -> None:
    assert E._compute_speaker_metrics([]) == {}


def test_pauses_empty_is_empty_list() -> None:
    assert E._compute_pauses([]) == []


# --------------------------------------------------------------------------- #
# KNOWN-QUIRK tripwires                                                       #
# --------------------------------------------------------------------------- #


def test_zero_total_time_and_words_force_ratios_to_zero_quirk() -> None:
    # KNOWN QUIRK (Tier 3 candidate): a single speaker would intuitively
    # have speaking_ratio == 1.0, but when every utterance is zero-duration
    # and textless the denominators (total_time, total_words) are 0 and the
    # guard returns 0.0 instead. Locking current behavior so a future
    # "normalize to 1.0" change is a conscious decision, not a silent drift.
    utts = [_utt("u1", "agent", 1.0, 1.0, ""), _utt("u2", "agent", 2.0, 2.0, "")]

    metrics = E._compute_speaker_metrics(utts)

    assert metrics["agent"].speaking_ratio == 0.0
    assert metrics["agent"].word_ratio == 0.0


def test_touching_utterances_record_no_pause_offbyone_quirk() -> None:
    # KNOWN QUIRK / off-by-one: pause requires gap > 0 (strict). Utterances
    # that touch exactly (next.start == prev.end) produce NO pause. Pinned so
    # a future switch to >= is deliberate.
    utts = [_utt("u1", "a", 0.0, 2.0, "x"), _utt("u2", "b", 2.0, 4.0, "y")]

    assert E._compute_pauses(utts) == []


def test_session_and_list_pause_counts_agree_crosscheck() -> None:
    # CROSS-CHECK tripwire: pauses are computed twice independently
    # (_compute_session_metrics for the count, _compute_pauses for the list).
    # They must agree; this breaks if one side's gap threshold drifts.
    session = _session(
        [
            _utt("u1", "agent", 0.0, 1.0, "a", word_count=1),
            _utt("u2", "customer", 3.0, 4.0, "b", word_count=1),
            _utt("u3", "agent", 4.0, 5.0, "c", word_count=1),  # touches u2 -> no pause
            _utt("u4", "customer", 10.0, 11.0, "d", word_count=1),
        ]
    )

    bundle = E.run(session)

    assert bundle.session_metrics.total_pauses == len(bundle.pauses)
    assert bundle.session_metrics.total_pauses == 2  # gaps before u2 and u4 only


# --------------------------------------------------------------------------- #
# PauseMetric field correctness + empty-session contract                      #
# --------------------------------------------------------------------------- #


def test_pause_metric_fields_are_populated() -> None:
    utts = [_utt("u1", "agent", 0.0, 1.0, "x"), _utt("u2", "customer", 2.5, 3.0, "y")]

    pauses = E._compute_pauses(utts)

    assert len(pauses) == 1
    p = pauses[0]
    assert p.start_after_utterance_id == "u1"
    assert p.end_before_utterance_id == "u2"
    assert p.duration_sec == 1.5
    assert p.speaker_before == "agent"
    assert p.speaker_after == "customer"


def test_empty_session_cannot_be_constructed() -> None:
    # run() can never see an empty session: SessionInput rejects it upstream.
    with pytest.raises(ValidationError):
        _session([])
