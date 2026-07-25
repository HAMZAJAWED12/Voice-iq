"""Characterization suite for AlignmentService (N3a / Tier N).

`alignment_service.py` had no dedicated tests — its coverage was incidental,
via the orchestrator harness. This suite pins CURRENT behavior so the N3b
two-pointer/bisect optimization can be proven output-identical.

Rule for N3b: these assertions describe what the code does today, not what
it arguably should do. If an optimization changes an output here, that is
drift — stop and report, do not edit the assertion.

The service is pure python (no ML imports), so this runs on the light lane.
"""

from __future__ import annotations

import random

import pytest

from app.services.alignment_service import AlignmentService

# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


def _svc(**kw) -> AlignmentService:
    return AlignmentService(**kw)


def _asr(segments: list[tuple[float, float, str]]) -> dict:
    return {
        "segments": [
            {"start": s, "end": e, "text": t, "avg_logprob": -0.2, "no_speech_prob": 0.1} for s, e, t in segments
        ]
    }


def _diar(turns: list[tuple[float, float, str]]) -> list[dict]:
    return [{"start": s, "end": e, "speaker": spk} for s, e, spk in turns]


# --------------------------------------------------------------------------- #
# Constructor                                                                 #
# --------------------------------------------------------------------------- #


def test_rejects_unknown_overlap_policy() -> None:
    with pytest.raises(ValueError, match="overlap_policy"):
        AlignmentService(overlap_policy="nonsense")


@pytest.mark.parametrize("policy", ["mark_overlap", "dominant"])
def test_accepts_valid_policies(policy: str) -> None:
    assert _svc(overlap_policy=policy).overlap_policy == policy


# --------------------------------------------------------------------------- #
# _confidence_from_whisper                                                    #
# --------------------------------------------------------------------------- #


def test_confidence_is_clamped_and_penalised_by_no_speech() -> None:
    high = AlignmentService._confidence_from_whisper({"avg_logprob": 0.0, "no_speech_prob": 0.0})
    low = AlignmentService._confidence_from_whisper({"avg_logprob": 0.0, "no_speech_prob": 1.0})
    assert high == pytest.approx(0.5)  # sigmoid(0) with no penalty
    assert low == 0.0  # fully penalised
    assert 0.0 <= AlignmentService._confidence_from_whisper({}) <= 1.0  # defaults are safe


def test_confidence_defaults_when_fields_missing() -> None:
    # avg_logprob defaults to -1.0, no_speech_prob to 0.0
    assert AlignmentService._confidence_from_whisper({}) == pytest.approx(1.0 / (1.0 + pow(2.718281828, 1.0)), abs=1e-3)


# --------------------------------------------------------------------------- #
# _overlap                                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((0.0, 2.0), (1.0, 3.0), 1.0),  # partial
        ((0.0, 2.0), (2.0, 3.0), 0.0),  # touching, not overlapping
        ((0.0, 5.0), (1.0, 2.0), 1.0),  # contained
        ((0.0, 1.0), (3.0, 4.0), 0.0),  # disjoint
    ],
)
def test_overlap_arithmetic(a, b, expected) -> None:
    assert AlignmentService._overlap(a[0], a[1], b[0], b[1]) == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# _extract_asr_segments — accepts several shapes                              #
# --------------------------------------------------------------------------- #


def test_extract_accepts_whisper_dict_meta_and_list() -> None:
    seg = {"start": 0.0, "end": 1.0, "text": " hi "}
    from_dict = AlignmentService._extract_asr_segments({"segments": [seg]})
    from_meta = AlignmentService._extract_asr_segments({"meta": {"segments": [seg]}})
    from_list = AlignmentService._extract_asr_segments([seg])

    assert from_dict == from_meta == from_list
    assert from_dict[0]["text"] == "hi"  # stripped
    assert from_dict[0]["avg_logprob"] == -1.0  # default
    assert from_dict[0]["no_speech_prob"] == 0.0


def test_extract_skips_segments_without_timings() -> None:
    segs = AlignmentService._extract_asr_segments({"segments": [{"text": "no times"}, {"start": 0, "end": 1}]})
    assert len(segs) == 1


@pytest.mark.parametrize("bad", [None, {}, {"segments": []}, "string", 42])
def test_extract_returns_empty_for_unusable_input(bad) -> None:
    assert AlignmentService._extract_asr_segments(bad) == []


# --------------------------------------------------------------------------- #
# _to_word_segments — uniform slicing                                         #
# --------------------------------------------------------------------------- #


def test_word_slicing_divides_duration_evenly() -> None:
    words = AlignmentService._to_word_segments([{"start": 0.0, "end": 2.0, "text": "a b c d"}])
    assert len(words) == 4
    assert words[0]["start"] == pytest.approx(0.0)
    assert words[-1]["end"] == pytest.approx(2.0)  # last word snaps to segment end
    assert [w["word"] for w in words] == ["a", "b", "c", "d"]


def test_word_slicing_skips_empty_text() -> None:
    assert AlignmentService._to_word_segments([{"start": 0.0, "end": 1.0, "text": ""}]) == []
    assert AlignmentService._to_word_segments([{"start": 0.0, "end": 1.0, "text": "   "}]) == []


def test_word_slicing_handles_zero_duration() -> None:
    words = AlignmentService._to_word_segments([{"start": 1.0, "end": 1.0, "text": "x"}])
    assert len(words) == 1  # 1e-6 floor prevents a divide-by-zero


# --------------------------------------------------------------------------- #
# _align_words_to_diarization — THE O(D*W) hot loop                           #
# --------------------------------------------------------------------------- #


def test_align_words_assigns_by_time_window() -> None:
    words = [
        {"start": 0.0, "end": 0.5, "word": "hello"},
        {"start": 0.5, "end": 1.0, "word": "there"},
        {"start": 2.0, "end": 2.5, "word": "hi"},
    ]
    diar = _diar([(0.0, 1.0, "S0"), (2.0, 3.0, "S1")])

    out = AlignmentService._align_words_to_diarization(words, diar)

    assert [s["speaker"] for s in out] == ["S0", "S1"]
    assert out[0]["text"] == "hello there"
    assert out[1]["text"] == "hi"


def test_align_words_windows_may_overlap_word_counted_twice() -> None:
    """Pins a load-bearing behaviour: diarization windows can overlap, and a
    word inside both is emitted for BOTH speakers. Any N3b two-pointer must
    NOT consume words, or this breaks."""
    words = [{"start": 1.0, "end": 1.5, "word": "shared"}]
    diar = _diar([(0.0, 2.0, "S0"), (1.0, 3.0, "S1")])

    out = AlignmentService._align_words_to_diarization(words, diar)

    assert len(out) == 2
    assert out[0]["text"] == "shared"
    assert out[1]["text"] == "shared"


def test_align_words_boundary_is_half_open() -> None:
    # excluded when w.end <= d_start or w.start >= d_end
    words = [{"start": 1.0, "end": 2.0, "word": "w"}]
    assert AlignmentService._align_words_to_diarization(words, _diar([(2.0, 3.0, "S")])) == []
    assert AlignmentService._align_words_to_diarization(words, _diar([(0.0, 1.0, "S")])) == []
    assert len(AlignmentService._align_words_to_diarization(words, _diar([(1.5, 3.0, "S")]))) == 1


def test_align_words_skips_windows_with_no_words() -> None:
    words = [{"start": 0.0, "end": 1.0, "word": "only"}]
    out = AlignmentService._align_words_to_diarization(words, _diar([(0.0, 1.0, "S0"), (5.0, 6.0, "S1")]))
    assert len(out) == 1  # the silent window is dropped entirely


def test_align_words_propagates_diarization_confidence() -> None:
    words = [{"start": 0.0, "end": 1.0, "word": "w"}]
    explicit = AlignmentService._align_words_to_diarization(
        words, [{"start": 0.0, "end": 1.0, "speaker": "S", "diarization_confidence": 0.42}]
    )
    fallback = AlignmentService._align_words_to_diarization(
        words, [{"start": 0.0, "end": 1.0, "speaker": "S", "confidence": 0.33}]
    )
    default = AlignmentService._align_words_to_diarization(words, _diar([(0.0, 1.0, "S")]))

    assert explicit[0]["diarization_confidence"] == pytest.approx(0.42)
    assert fallback[0]["diarization_confidence"] == pytest.approx(0.33)
    assert default[0]["diarization_confidence"] == pytest.approx(1.0)


def test_align_words_defaults_missing_speaker_to_unknown() -> None:
    out = AlignmentService._align_words_to_diarization(
        [{"start": 0.0, "end": 1.0, "word": "w"}], [{"start": 0.0, "end": 1.0}]
    )
    assert out[0]["speaker"] == "UNKNOWN"


@pytest.mark.parametrize(("words", "diar"), [([], [{"start": 0, "end": 1}]), ([{"start": 0, "end": 1}], [])])
def test_align_words_empty_inputs(words, diar) -> None:
    assert AlignmentService._align_words_to_diarization(words, diar) == []


# --------------------------------------------------------------------------- #
# _best_asr_for_window — THE O(M*A) hot loop                                   #
# --------------------------------------------------------------------------- #


def test_best_asr_picks_max_overlap() -> None:
    asr = [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 0.9, "end": 3.0, "text": "b"},
    ]
    best = AlignmentService._best_asr_for_window({"start": 0.8, "end": 3.0}, asr)
    assert best["text"] == "b"


def test_best_asr_returns_none_when_no_overlap() -> None:
    asr = [{"start": 0.0, "end": 1.0, "text": "a"}]
    assert AlignmentService._best_asr_for_window({"start": 5.0, "end": 6.0}, asr) is None


def test_best_asr_first_max_wins_on_tie() -> None:
    """Strict `>` means the FIRST maximum wins. A bisect-bounded N3b rewrite
    must preserve this ordering."""
    asr = [
        {"start": 0.0, "end": 1.0, "text": "first"},
        {"start": 0.0, "end": 1.0, "text": "second"},
    ]
    assert AlignmentService._best_asr_for_window({"start": 0.0, "end": 1.0}, asr)["text"] == "first"


def test_best_asr_empty_list() -> None:
    assert AlignmentService._best_asr_for_window({"start": 0.0, "end": 1.0}, []) is None


# --- N3b: the bisect fast path must equal the full scan -------------------- #


@pytest.mark.parametrize("seed", list(range(15)))
def test_best_asr_bounded_scan_equals_full_scan(seed: int) -> None:
    """Differential test: passing `_starts` (bisect path) must return the
    exact same segment as the unbounded scan, for every window."""
    rng = random.Random(seed)
    asr, t = [], 0.0
    for i in range(30):
        dur = rng.uniform(0.3, 2.0)
        asr.append({"start": t, "end": t + dur, "text": f"s{i}"})
        t += dur + rng.uniform(0.0, 0.5)
    starts = [a["start"] for a in asr]

    for _ in range(40):
        w_start = rng.uniform(-1.0, t + 1.0)
        window = {"start": w_start, "end": w_start + rng.uniform(0.0, 3.0)}
        full = AlignmentService._best_asr_for_window(window, asr)
        bounded = AlignmentService._best_asr_for_window(window, asr, _starts=starts)
        assert full is bounded  # identity, not just equality


def test_align_falls_back_when_asr_unsorted() -> None:
    """Unsorted ASR disables the bisect path; output must still be correct."""
    unsorted_asr = {
        "segments": [
            {"start": 2.0, "end": 4.0, "text": "second here"},
            {"start": 0.0, "end": 2.0, "text": "first here"},
        ]
    }
    out = _svc().align(unsorted_asr, _diar([(0.0, 2.0, "S0"), (2.0, 4.0, "S1")]))["speaker_segments"]
    assert [s["speaker"] for s in out] == ["S0", "S1"]
    for s in out:
        assert 0.0 <= s["confidence"] <= 1.0


# --------------------------------------------------------------------------- #
# _merge_blocks                                                               #
# --------------------------------------------------------------------------- #


def test_merge_joins_same_speaker_within_gap() -> None:
    svc = _svc(max_gap_merge=0.75)
    segs = [
        {"start": 0.0, "end": 1.0, "speaker": "S0", "text": "one", "diarization_confidence": 1.0},
        {"start": 1.5, "end": 2.0, "speaker": "S0", "text": "two", "diarization_confidence": 0.5},
    ]
    out = svc._merge_blocks(segs)
    assert len(out) == 1
    assert out[0]["text"] == "one two"
    assert out[0]["end"] == 2.0
    assert out[0]["diarization_confidence"] == pytest.approx(0.75)  # averaged


def test_merge_does_not_join_across_speakers() -> None:
    segs = [
        {"start": 0.0, "end": 1.0, "speaker": "S0", "text": "a"},
        {"start": 1.1, "end": 2.0, "speaker": "S1", "text": "b"},
    ]
    assert len(_svc()._merge_blocks(segs)) == 2


def test_merge_does_not_join_beyond_gap() -> None:
    segs = [
        {"start": 0.0, "end": 1.0, "speaker": "S0", "text": "a"},
        {"start": 5.0, "end": 6.0, "speaker": "S0", "text": "b"},
    ]
    assert len(_svc(max_gap_merge=0.75)._merge_blocks(segs)) == 2


def test_merge_empty() -> None:
    assert _svc()._merge_blocks([]) == []


# --------------------------------------------------------------------------- #
# Overlap policy                                                              #
# --------------------------------------------------------------------------- #


def test_mark_overlap_relabels_speaker_and_degrades_confidence() -> None:
    svc = _svc(overlap_policy="mark_overlap", unknown_label="SPEAKER_UNKNOWN")
    merged = [{"start": 0.0, "end": 3.0, "speaker": "S0", "text": "x", "confidence": 0.9}]
    diar = _diar([(0.0, 2.0, "S0"), (1.0, 3.0, "S1")])  # 1s overlap

    out = svc._apply_overlap_policy(merged, diar)

    assert out[0]["overlap"] is True
    assert out[0]["speaker"] == "SPEAKER_UNKNOWN"
    assert out[0]["confidence"] == pytest.approx(0.55)


def test_dominant_policy_keeps_speaker() -> None:
    svc = _svc(overlap_policy="dominant")
    merged = [{"start": 0.0, "end": 3.0, "speaker": "S0", "text": "x", "confidence": 0.9}]
    out = svc._apply_overlap_policy(merged, _diar([(0.0, 2.0, "S0"), (1.0, 3.0, "S1")]))

    assert out[0]["overlap"] is True
    assert out[0]["speaker"] == "S0"  # not relabelled


def test_no_overlap_windows_sets_flag_false() -> None:
    merged = [{"start": 0.0, "end": 1.0, "speaker": "S0", "text": "x"}]
    out = _svc()._apply_overlap_policy(merged, _diar([(0.0, 1.0, "S0"), (2.0, 3.0, "S1")]))
    assert out[0]["overlap"] is False


def test_overlap_below_min_sec_is_ignored() -> None:
    svc = _svc(overlap_min_sec=0.5)
    merged = [{"start": 0.0, "end": 3.0, "speaker": "S0", "text": "x"}]
    out = svc._apply_overlap_policy(merged, _diar([(0.0, 2.0, "S0"), (1.9, 3.0, "S1")]))  # 0.1s
    assert out[0]["overlap"] is False


# --------------------------------------------------------------------------- #
# Public API: align()                                                         #
# --------------------------------------------------------------------------- #


def test_align_end_to_end_two_speakers() -> None:
    asr = _asr([(0.0, 2.0, "hello I need help"), (2.0, 4.0, "sure I can help")])
    diar = _diar([(0.0, 2.0, "SPEAKER_00"), (2.0, 4.0, "SPEAKER_01")])

    out = _svc().align(asr, diar)["speaker_segments"]

    assert [s["speaker"] for s in out] == ["SPEAKER_00", "SPEAKER_01"]
    for seg in out:
        assert 0.0 <= seg["confidence"] <= 1.0
        assert seg["gender"] is None  # placeholders added by align()
        assert seg["gender_confidence"] is None
        assert seg["overlap"] is False


@pytest.mark.parametrize(("asr", "diar"), [(None, [{"start": 0, "end": 1}]), ({"segments": []}, []), (None, None)])
def test_align_missing_inputs_returns_empty(asr, diar) -> None:
    assert _svc().align(asr, diar) == {"speaker_segments": []}


# --------------------------------------------------------------------------- #
# build_conversation                                                          #
# --------------------------------------------------------------------------- #


def test_build_conversation_assigns_roles_by_speaking_time() -> None:
    # SPEAKER_00 talks longest -> CUSTOMER, next -> AGENT
    asr = _asr([(0.0, 6.0, "a b c d e f"), (6.0, 8.0, "g h")])
    diar = _diar([(0.0, 6.0, "SPEAKER_00"), (6.0, 8.0, "SPEAKER_01")])

    conv = _svc().build_conversation(asr, diar)

    roles = [t["speaker"] for t in conv]
    assert "CUSTOMER" in roles and "AGENT" in roles
    assert conv[0]["speaker"] == "CUSTOMER"  # the longest talker
    assert conv[0]["speaker_raw"] == "SPEAKER_00"


def test_build_conversation_single_speaker_is_customer() -> None:
    asr = _asr([(0.0, 2.0, "just me talking")])
    conv = _svc().build_conversation(asr, _diar([(0.0, 2.0, "SPEAKER_00")]))
    assert [t["speaker"] for t in conv] == ["CUSTOMER"]


def test_build_conversation_empty_when_no_alignment() -> None:
    assert _svc().build_conversation(None, []) == []


# --------------------------------------------------------------------------- #
# Remaining guards + compatibility wrappers                                   #
# --------------------------------------------------------------------------- #


def test_overlap_windows_empty_diarization() -> None:
    assert AlignmentService._find_diarization_overlap_windows([], min_overlap=0.1) == []


def test_apply_overlap_policy_short_circuits_on_empty() -> None:
    svc = _svc()
    assert svc._apply_overlap_policy([], _diar([(0.0, 1.0, "S")])) == []
    merged = [{"start": 0.0, "end": 1.0, "speaker": "S", "text": "x"}]
    assert svc._apply_overlap_policy(merged, []) is merged  # returned untouched


def test_module_level_compat_wrappers() -> None:
    from app.services.alignment_service import align_transcript_with_speakers
    from app.services.alignment_service import build_conversation as build_conv_fn

    asr = _asr([(0.0, 2.0, "hello there")])
    diar = _diar([(0.0, 2.0, "SPEAKER_00")])

    assert align_transcript_with_speakers(asr, diar)["speaker_segments"]
    assert build_conv_fn(asr, diar)[0]["speaker"] == "CUSTOMER"


# --------------------------------------------------------------------------- #
# Golden baseline — the N3b regression net                                    #
# --------------------------------------------------------------------------- #


def _random_case(seed: int) -> tuple[dict, list[dict]]:
    rng = random.Random(seed)
    asr_segs, t = [], 0.0
    for _ in range(rng.randint(3, 12)):
        dur = rng.uniform(0.5, 3.0)
        n_words = rng.randint(1, 6)
        asr_segs.append((t, t + dur, " ".join(f"w{i}" for i in range(n_words))))
        t += dur + rng.uniform(0.0, 0.4)

    diar_turns, t = [], 0.0
    speakers = ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
    for i in range(rng.randint(3, 12)):
        dur = rng.uniform(0.5, 3.0)
        # deliberately allow overlap with the previous turn
        start = max(0.0, t - rng.choice([0.0, 0.0, 0.6]))
        diar_turns.append((start, start + dur, speakers[i % rng.randint(1, 3)]))
        t = start + dur + rng.uniform(0.0, 0.3)

    return _asr(asr_segs), _diar(diar_turns)


@pytest.mark.parametrize("seed", list(range(25)))
def test_golden_alignment_is_stable(seed: int) -> None:
    """Randomised-but-deterministic inputs through the full public API.

    This is the N3b safety net: run before the optimization to confirm it
    passes, and after to confirm the output is unchanged. It asserts
    structural invariants that a two-pointer/bisect rewrite could plausibly
    break (ordering, clamping, text preservation, overlap flags).
    """
    asr, diar = _random_case(seed)
    svc = _svc()

    segments = svc.align(asr, diar)["speaker_segments"]
    conversation = svc.build_conversation(asr, diar)

    # Ordering + clamping invariants
    starts = [s["start"] for s in segments]
    assert starts == sorted(starts)
    for s in segments:
        assert s["end"] >= s["start"]
        assert 0.0 <= s["confidence"] <= 1.0
        assert 0.0 <= s["diarization_confidence"] <= 1.0
        assert isinstance(s["overlap"], bool)

    conv_starts = [t["start"] for t in conversation]
    assert conv_starts == sorted(conv_starts)

    # Determinism: identical inputs -> identical outputs
    assert svc.align(asr, diar)["speaker_segments"] == segments
    assert svc.build_conversation(asr, diar) == conversation
