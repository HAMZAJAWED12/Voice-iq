"""Behavioral harness for :class:`VoiceIQOrchestrator.run` (Tier 3 E2, Phase 1).

Purpose
-------
`orchestrator.run()` is the 678-LOC critical path with, until now, zero
direct coverage. This module is the safety net that Phase 2's decomposition
must not break: it *proves current behavior*, it does not change it. No
production code is touched by this phase.

Design
------
* **Real ``JobIO(base_dir=tmp_path)``** throughout. ``_final_response`` reads
  every field back *from disk*, so mocked stages must save through the real
  stage code for the returned dict to see their values.
* **The 11 side-effect points are mocked** (7 heavy ML services + the two
  heavy audio utils + the network-bound FactCheckService + the byte-producing
  PDFService), all ``autospec=True`` so call-signature drift fails loudly.
* **The 6 cheap, pure-python services run REAL** (alignment, metadata,
  keyword, intent, flag, insight + adapter). They are deterministic and
  side-effect-free, so running them real gives truer coverage than hand-built
  return shapes — and it exercises the disk round-trip for free (stage E's
  ``align`` reads the whisper.json / diarization.json that stages C/D saved).
* Fixtures are **module-local, not a shared conftest**, to avoid leaking this
  wiring into the 118 sibling insight tests.

The golden fixture (:func:`_wire_happy`) makes the whole pipeline green; each
test perturbs exactly one stage to its failure/skip branch.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.pipeline.orchestrator import VoiceIQOrchestrator
from app.utils.audio_quality import AudioQualityReport
from app.utils.audio_utils import AudioNormalizationTimeout
from app.utils.job_io import JobIO

_ORCH = "app.pipeline.orchestrator"

# Every timing key run() records, in order. Used to assert stage completeness.
ALL_TIMINGS = [
    "audio_normalize",
    "audio_quality",
    "asr",
    "diarization",
    "alignment",
    "stats",
    "sentiment",
    "keywords",
    "gender",
    "emotion",
    "topic",
    "summary",
    "intent",
    "factcheck",
    "flags",
    "insights",
    "pdf",
]

# --------------------------------------------------------------------------- #
# Golden inputs — shaped exactly as the real stage code saves / consumes.      #
# --------------------------------------------------------------------------- #
GOLDEN_ASR: dict[str, Any] = {
    "text": "Hello, I need help with my billing. Sure, I can help with that.",
    "segments": [
        {"start": 0.0, "end": 2.0, "text": "Hello, I need help with my billing."},
        {"start": 2.0, "end": 4.0, "text": "Sure, I can help with that."},
    ],
    "meta": {"model": "base", "language": "en", "duration": 4.0},
}

# Two distinct speakers → NOT single-speaker mode on the happy path.
GOLDEN_DIAR: list[dict[str, Any]] = [
    {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
    {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0},
]


def _golden_aq(**overrides: Any) -> AudioQualityReport:
    """A clean (non-silent, good-SNR) audio-quality report; override to perturb."""
    fields: dict[str, Any] = {
        "duration_sec": 4.0,
        "sample_rate": 16000,
        "channels": 1,
        "rms_db": -20.0,
        "peak_db": -3.0,
        "silence_ratio": 0.1,
        "snr_db": 30.0,
        "is_silent": False,
        "is_near_silent": False,
        "low_snr": False,
        "very_low_snr": False,
    }
    fields.update(overrides)
    return AudioQualityReport(**fields)


def _wire_happy(m: SimpleNamespace) -> None:
    """Point every mocked side-effect stage at its golden return value.

    Heavy NLP enrichers use pass-through ``side_effect`` so the in-place
    ``speaker_segments`` reassignment chain is preserved (real keyword
    enrichment survives between the mocked sentiment/gender/emotion steps).
    """
    m.normalize.return_value = None
    m.aq.return_value = _golden_aq()
    m.asr.return_value.transcribe.return_value = dict(GOLDEN_ASR)
    m.diar.return_value.diarize_with_warnings.return_value = (list(GOLDEN_DIAR), [])
    m.sentiment.analyze_speaker_segments.side_effect = lambda segs: segs
    m.gender.add_gender_to_segments.side_effect = lambda segs, wav: segs
    m.emotion.analyze_speaker_segments.side_effect = lambda wav, segs: segs
    m.emotion.summarize_emotions.return_value = {"SPEAKER_00": {"neutral": 1.0}}
    m.topic.classify.return_value = {"topic": "billing", "confidence": 0.9}
    m.summary.generate_summary.return_value = "Customer asked about a billing issue."
    m.factcheck.fact_check.return_value = []
    m.pdf.generate_pdf_report.return_value = b"%PDF-1.4 fake-report"


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
JOB_ID = "job-under-test"


@pytest.fixture
def io(tmp_path: Any) -> JobIO:
    return JobIO(base_dir=str(tmp_path / "jobs"))


@pytest.fixture
def orch(io: JobIO) -> VoiceIQOrchestrator:
    return VoiceIQOrchestrator(job_io=io)


def _seed_input(io: JobIO, job_id: str = JOB_ID) -> None:
    """Write the ``input/original.wav`` the pre-stage gate globs for."""
    job = io.init_job(job_id)
    (job.input_dir / "original.wav").write_bytes(b"RIFF0000WAVEfake-audio-bytes")


@pytest.fixture
def mx() -> Iterator[SimpleNamespace]:
    """Patch the 11 side-effect points (autospec) and wire the happy path.

    Cheap pure-python services (alignment/metadata/keyword/intent/flag/insight
    + adapter) are deliberately left real.
    """
    with contextlib.ExitStack() as stack:

        def _p(name: str) -> Any:
            return stack.enter_context(patch(f"{_ORCH}.{name}", autospec=True))

        ns = SimpleNamespace(
            normalize=_p("normalize_to_wav"),
            aq=_p("analyze_audio_quality"),
            asr=_p("ASRService"),
            diar=_p("DiarizationService"),
            sentiment=_p("SentimentService"),
            gender=_p("GenderService"),
            emotion=_p("EmotionService"),
            topic=_p("TopicService"),
            summary=_p("SummaryService"),
            factcheck=_p("FactCheckService"),
            pdf=_p("PDFService"),
        )
        _wire_happy(ns)
        yield ns


# --------------------------------------------------------------------------- #
# Pre-stage gate                                                               #
# --------------------------------------------------------------------------- #
class TestInputGate:
    def test_missing_input_audio_hard_fails(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        # No input file seeded → the glob finds nothing.
        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "failed"
        assert "MISSING_INPUT_AUDIO" in res["warnings"]
        # Hard fail returns before any stage runs.
        mx.normalize.assert_not_called()
        mx.asr.assert_not_called()

    def test_missing_input_records_no_timings(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        res = orch.run(JOB_ID)
        assert res["pipeline_meta"]["timings_ms"] == {}


# --------------------------------------------------------------------------- #
# Stage A — audio_normalize                                                    #
# --------------------------------------------------------------------------- #
class TestNormalizeStage:
    def test_happy_path_full_pipeline(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        # Every stage ran → every timing key present.
        assert set(res["pipeline_meta"]["timings_ms"]) == set(ALL_TIMINGS)
        # No warnings on a fully-clean run.
        assert res["warnings"] == []
        # PDF stage produced a base64 report.
        assert res["report_pdf_base64"] is not None
        mx.normalize.assert_called_once()

    def test_timeout_hard_fails_422_code(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.normalize.side_effect = AudioNormalizationTimeout("ffmpeg took too long")

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "failed"
        assert "AUDIO_NORMALIZATION_TIMEOUT" in res["warnings"]
        # Distinct from the generic-failure code (route maps this one to 422).
        assert "AUDIO_NORMALIZATION_FAILED" not in res["warnings"]
        # Downstream stages must not run.
        mx.aq.assert_not_called()
        mx.asr.assert_not_called()

    def test_generic_failure_hard_fails_400_code(
        self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace
    ) -> None:
        _seed_input(io)
        mx.normalize.side_effect = RuntimeError("corrupt input")

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "failed"
        assert "AUDIO_NORMALIZATION_FAILED" in res["warnings"]
        assert "AUDIO_NORMALIZATION_TIMEOUT" not in res["warnings"]
        mx.aq.assert_not_called()

    def test_timeout_still_records_normalize_timing(
        self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace
    ) -> None:
        _seed_input(io)
        mx.normalize.side_effect = AudioNormalizationTimeout("x")
        res = orch.run(JOB_ID)
        # The stage times itself even on the hard-fail path.
        assert "audio_normalize" in res["pipeline_meta"]["timings_ms"]


# --------------------------------------------------------------------------- #
# Stage B — audio_quality                                                      #
# --------------------------------------------------------------------------- #
class TestAudioQualityStage:
    def test_silent_hard_fails(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.aq.return_value = _golden_aq(is_silent=True)

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "failed"
        assert "AUDIO_SILENT_OR_NEAR_SILENT" in res["warnings"]
        # Quality is stage B → ASR (stage C) must not run.
        mx.asr.assert_not_called()

    def test_near_silent_hard_fails(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.aq.return_value = _golden_aq(is_near_silent=True)

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "failed"
        assert "AUDIO_SILENT_OR_NEAR_SILENT" in res["warnings"]
        mx.asr.assert_not_called()

    def test_low_snr_warns_soft_and_continues(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.aq.return_value = _golden_aq(low_snr=True)

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert "LOW_SNR_AUDIO" in res["warnings"]
        # low_snr_flag propagates: gender + emotion skip on low SNR.
        assert "GENDER_SKIPPED_LOW_SNR" in res["warnings"]
        assert "EMOTION_SKIPPED_LOW_SNR" in res["warnings"]

    def test_very_low_snr_warns_heavy_noise(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.aq.return_value = _golden_aq(very_low_snr=True)

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert "HEAVY_NOISE_AUDIO" in res["warnings"]

    def test_quality_exception_is_soft(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.aq.side_effect = RuntimeError("librosa blew up")

        res = orch.run(JOB_ID)

        # Exception in quality is fail-soft — pipeline completes.
        assert res["pipeline_meta"]["status"] == "ok"
        assert "AUDIO_QUALITY_FAILED" in res["warnings"]
        # ASR still ran.
        mx.asr.assert_called()


# --------------------------------------------------------------------------- #
# Stage C — asr                                                                #
# --------------------------------------------------------------------------- #
class TestASRStage:
    def test_ok_transcript_reaches_response(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert res["transcript"] == GOLDEN_ASR["text"]
        assert "asr" in res["pipeline_meta"]["timings_ms"]

    def test_empty_transcript_warns(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.asr.return_value.transcribe.return_value = {"text": "   ", "segments": [], "meta": {}}

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert "EMPTY_TRANSCRIPT" in res["warnings"]

    def test_asr_exception_is_soft(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.asr.return_value.transcribe.side_effect = RuntimeError("whisper crashed")

        res = orch.run(JOB_ID)

        # ASR failure is fail-soft — pipeline continues, status stays ok.
        assert res["pipeline_meta"]["status"] == "ok"
        assert "ASR_FAILED" in res["warnings"]
        # No whisper.json saved → alignment has no ASR input to consume.
        assert "ALIGNMENT_SKIPPED_MISSING_INPUT" in res["warnings"]


# --------------------------------------------------------------------------- #
# Stage D — diarization                                                        #
# --------------------------------------------------------------------------- #
class TestDiarizeStage:
    def test_ok_two_speakers_not_single_mode(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert "SINGLE_SPEAKER_MODE" not in res["warnings"]
        assert res["single_speaker_mode"] is False
        assert res["segments"] == GOLDEN_DIAR

    def test_forwarded_service_warnings_propagate(
        self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace
    ) -> None:
        _seed_input(io)
        mx.diar.return_value.diarize_with_warnings.return_value = (
            list(GOLDEN_DIAR),
            ["SPEAKER_CAP_APPLIED", "LOW_SNR_DIARIZATION_UNRELIABLE"],
        )

        res = orch.run(JOB_ID)

        assert "SPEAKER_CAP_APPLIED" in res["warnings"]
        assert "LOW_SNR_DIARIZATION_UNRELIABLE" in res["warnings"]

    def test_single_speaker_mode(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.diar.return_value.diarize_with_warnings.return_value = (
            [{"speaker": "SPEAKER_00", "start": 0.0, "end": 4.0}],
            [],
        )

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert "SINGLE_SPEAKER_MODE" in res["warnings"]
        assert res["single_speaker_mode"] is True
        # Single-speaker also limits flag generation.
        assert "FLAGS_LIMITED_SINGLE_SPEAKER" in res["warnings"]

    def test_empty_diarization_is_single_speaker(
        self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace
    ) -> None:
        _seed_input(io)
        mx.diar.return_value.diarize_with_warnings.return_value = ([], [])

        res = orch.run(JOB_ID)

        assert "SINGLE_SPEAKER_MODE" in res["warnings"]
        # Empty diarization.json is falsy → alignment skips.
        assert "ALIGNMENT_SKIPPED_MISSING_INPUT" in res["warnings"]

    def test_diarization_exception_falls_back(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.diar.return_value.diarize_with_warnings.side_effect = RuntimeError("pyannote died")

        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        # Real literal is DIARIZATION_FAILED_FALLBACK (not "DIARIZATION_FALLBACK").
        assert "DIARIZATION_FAILED_FALLBACK" in res["warnings"]
        # Empty fallback segments → single-speaker mode.
        assert "SINGLE_SPEAKER_MODE" in res["warnings"]

    def test_legacy_service_without_warnings_api(
        self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace
    ) -> None:
        # An older DiarizationService lacking diarize_with_warnings falls back
        # to the plain diarize() path. Drop the attr so hasattr() is False.
        del mx.diar.return_value.diarize_with_warnings
        mx.diar.return_value.diarize.return_value = list(GOLDEN_DIAR)

        _seed_input(io)
        res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert res["segments"] == GOLDEN_DIAR
        mx.diar.return_value.diarize.assert_called_once()


# --------------------------------------------------------------------------- #
# Stage E — alignment (real service; proves the disk round-trip)               #
# --------------------------------------------------------------------------- #
class TestAlignmentStage:
    def test_reads_asr_and_diar_from_disk(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        """Crown-jewel invariant #3: stage E consumes what stages C/D saved.

        Alignment loads whisper.json + diarization.json *from disk*, not from
        in-memory state. Prove the artifacts exist and drove a non-empty
        alignment that reached the response.
        """
        _seed_input(io)
        res = orch.run(JOB_ID)

        job = io.init_job(JOB_ID)
        # Stages C/D wrote their inputs to disk...
        assert io.exists(job, "artifacts/asr/whisper.json")
        assert io.exists(job, "artifacts/diarization/diarization.json")
        # ...and stage E consumed them into a non-empty alignment artifact.
        on_disk = io.load_json(job, "artifacts/alignment/speaker_segments.json", default=[])
        assert on_disk, "alignment produced no speaker_segments from disk inputs"
        # The conversation in the response derives from the golden transcript.
        assert res["conversation"], "conversation should be non-empty on happy path"
        assert any("billing" in (turn.get("text") or "") for turn in res["conversation"])

    def test_skipped_when_asr_output_missing(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        mx.asr.return_value.transcribe.side_effect = RuntimeError("no asr")

        res = orch.run(JOB_ID)

        assert "ALIGNMENT_SKIPPED_MISSING_INPUT" in res["warnings"]
        assert res["speaker_segments"] == []

    def test_alignment_exception_is_soft(self, orch: VoiceIQOrchestrator, io: JobIO, mx: SimpleNamespace) -> None:
        _seed_input(io)
        with patch(f"{_ORCH}.AlignmentService", autospec=True) as align_cls:
            align_cls.return_value.align.side_effect = RuntimeError("align blew up")
            res = orch.run(JOB_ID)

        assert res["pipeline_meta"]["status"] == "ok"
        assert "ALIGNMENT_FAILED" in res["warnings"]
