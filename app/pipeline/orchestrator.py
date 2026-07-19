# app/pipeline/orchestrator.py
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.insights.adapters import VoiceIQInsightAdapter
from app.insights.service import InsightService
from app.services.alignment_service import AlignmentService
from app.services.asr_service import ASRService
from app.services.diarization_service import DiarizationService
from app.services.emotion_service import EmotionService
from app.services.factcheck_service import FactCheckService
from app.services.flag_service import FlagService
from app.services.gender_service import GenderService
from app.services.intent_service import IntentService
from app.services.keyword_service import KeywordService
from app.services.metadata_service import MetadataExtractor
from app.services.pdf_service import PDFService
from app.services.sentiment_service import SentimentService
from app.services.summary_service import SummaryService
from app.services.topic_service import TopicService
from app.utils.audio_quality import AudioQualityReport, analyze_audio_quality
from app.utils.audio_utils import (
    AudioNormalizationTimeout,
    normalize_to_wav,
)
from app.utils.job_io import JobIO, JobPaths


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_ctor_kwargs(cls, kwargs: dict[str, Any]) -> dict[str, Any]:
    """
    Filter kwargs to only those accepted by a class constructor.
    Prevents runtime errors if your service signature differs.
    """
    import inspect

    try:
        sig = inspect.signature(cls.__init__)
        allowed = set(sig.parameters.keys())
        # remove self
        allowed.discard("self")
        return {k: v for k, v in kwargs.items() if k in allowed}
    except Exception:
        return kwargs


@dataclass
class _PipelineState:
    """Mutable state threaded through the pipeline stages.

    Every value that one stage produces and a later stage consumes lives
    here, so the sequencing contract between stages is visible in one
    place instead of being implied by ~20 locals in a single long
    function. Stage-internal temporaries stay as locals.
    """

    # Per-run context
    job: JobPaths
    meta: dict[str, Any]
    job_id: str
    expected_speakers: int | None = None
    max_speakers_cap: int = 8
    whisper_model: str = "base"
    language: str | None = None

    # Stage outputs, in production order
    input_audio_path: Path | None = None
    normalized_wav: str = ""
    aq: AudioQualityReport | None = None
    audio_quality_payload: dict[str, Any] = field(default_factory=dict)
    low_snr_flag: bool = False
    asr_out: dict[str, Any] = field(default_factory=lambda: {"text": "", "segments": [], "meta": {}})
    transcript_text: str = ""
    diar_segments: list[dict[str, Any]] = field(default_factory=list)
    single_speaker_mode: bool = False
    speaker_segments: list[dict[str, Any]] = field(default_factory=list)
    conversation: list[dict[str, Any]] = field(default_factory=list)
    speaker_stats: dict[str, Any] = field(default_factory=dict)
    conversation_stats: dict[str, Any] = field(default_factory=dict)
    emotion_overview: dict[str, Any] = field(default_factory=dict)
    topic: dict[str, Any] = field(default_factory=lambda: {"topic": "unknown", "confidence": 0.0})
    summary_text: str = ""
    conversation_with_intents: list[dict[str, Any]] = field(default_factory=list)
    intents_summary: dict[str, Any] = field(default_factory=dict)
    fact_checks: list[dict[str, Any]] = field(default_factory=list)
    flags: list[dict[str, Any]] = field(default_factory=list)
    insight_payload: dict[str, Any] | None = None

    def warn(self, code: str) -> None:
        if code not in self.meta["warnings"]:
            self.meta["warnings"].append(code)

    def timing(self, name: str, start_ms: int) -> None:
        self.meta["timings_ms"][name] = _now_ms() - start_ms

    def skip(self, name: str) -> None:
        self.meta["skipped_steps"].append(name)


class VoiceIQOrchestrator:
    """
    Modular pipeline:
      - each step reads inputs from disk
      - writes outputs to disk
      - fail-soft (skip steps) where possible
    """

    def __init__(self, job_io: JobIO | None = None):
        self.io = job_io or JobIO()

    def run(
        self,
        job_id: str,
        expected_speakers: int | None = None,
        max_speakers_cap: int = 8,
        whisper_model: str = "base",
        language: str | None = None,
    ) -> dict[str, Any]:
        job = self.io.init_job(job_id)

        meta: dict[str, Any] = {
            "job_id": job_id,
            "paths": job.to_dict(),
            "warnings": [],
            "timings_ms": {},
            "skipped_steps": [],
            "status": "running",
        }

        st = _PipelineState(
            job=job,
            meta=meta,
            job_id=job_id,
            expected_speakers=expected_speakers,
            max_speakers_cap=max_speakers_cap,
            whisper_model=whisper_model,
            language=language,
        )

        # Find uploaded file saved by API as input/original.<ext>
        input_candidates = list(job.input_dir.glob("original.*"))
        if not input_candidates:
            meta["status"] = "failed"
            st.warn("MISSING_INPUT_AUDIO")
            self.io.save_json(job, "meta.json", meta)
            return self._final_response(job, meta)

        st.input_audio_path = input_candidates[0]

        # -----------------------
        # Step A: Normalize audio
        # -----------------------
        t0 = _now_ms()
        st.normalized_wav = str(self.io.p(job, "artifacts/audio/normalized.wav"))
        try:
            normalize_to_wav(str(st.input_audio_path), st.normalized_wav, sr=16000)
            self.io.save_json(
                job,
                "artifacts/audio/normalize.status.json",
                {
                    "service": "audio_normalize",
                    "status": "ok",
                    "input": str(st.input_audio_path),
                    "output": st.normalized_wav,
                },
            )
        except AudioNormalizationTimeout as e:
            # Distinct warning code so the HTTP layer can map this to 422
            # (caller's fault, retry not useful) vs. AUDIO_NORMALIZATION_FAILED
            # which maps to 400 (malformed file).
            meta["status"] = "failed"
            st.warn("AUDIO_NORMALIZATION_TIMEOUT")
            self.io.save_json(
                job,
                "artifacts/audio/normalize.status.json",
                {
                    "service": "audio_normalize",
                    "status": "timeout",
                    "error": str(e),
                },
            )
            st.timing("audio_normalize", t0)
            self.io.save_json(job, "meta.json", meta)
            return self._final_response(job, meta)
        except Exception as e:
            meta["status"] = "failed"
            st.warn("AUDIO_NORMALIZATION_FAILED")
            self.io.save_json(
                job,
                "artifacts/audio/normalize.status.json",
                {
                    "service": "audio_normalize",
                    "status": "failed",
                    "error": str(e),
                },
            )
            st.timing("audio_normalize", t0)
            self.io.save_json(job, "meta.json", meta)
            return self._final_response(job, meta)
        st.timing("audio_normalize", t0)

        # -----------------------
        # Step B: Audio quality guardrails
        # -----------------------
        t0 = _now_ms()
        try:
            st.aq = analyze_audio_quality(st.normalized_wav)
            st.audio_quality_payload = st.aq.to_dict()
            self.io.save_json(job, "artifacts/audio/audio_quality.json", st.audio_quality_payload)
            self.io.save_json(
                job,
                "artifacts/audio/audio_quality.status.json",
                {
                    "service": "audio_quality",
                    "status": "ok",
                },
            )

            if st.aq.is_silent or st.aq.is_near_silent:
                meta["status"] = "failed"
                st.warn("AUDIO_SILENT_OR_NEAR_SILENT")
                self.io.save_json(job, "meta.json", meta)
                return self._final_response(job, meta)

            if st.aq.low_snr:
                st.warn("LOW_SNR_AUDIO")
            if st.aq.very_low_snr:
                st.warn("HEAVY_NOISE_AUDIO")

        except Exception as e:
            st.warn("AUDIO_QUALITY_FAILED")
            self.io.save_json(
                job,
                "artifacts/audio/audio_quality.status.json",
                {
                    "service": "audio_quality",
                    "status": "failed",
                    "error": str(e),
                },
            )
        st.timing("audio_quality", t0)

        st.low_snr_flag = bool(st.aq and (st.aq.low_snr or st.aq.very_low_snr))

        # -----------------------
        # Step C: ASR
        # -----------------------
        t0 = _now_ms()
        try:
            asr = ASRService(model_name=st.whisper_model, language=st.language)
            st.asr_out = asr.transcribe(st.normalized_wav)

            text = (st.asr_out.get("text") or "").strip()
            segments = st.asr_out.get("segments") or []

            self.io.save_json(job, "artifacts/asr/whisper.json", st.asr_out)
            self.io.save_text(job, "artifacts/asr/transcript.txt", text)
            self.io.save_json(
                job,
                "artifacts/asr/asr.status.json",
                {
                    "service": "asr",
                    "status": "ok",
                    "model": st.whisper_model,
                    "num_segments": len(segments),
                    "total_chars": len(text),
                },
            )

            if not text:
                st.warn("EMPTY_TRANSCRIPT")

        except Exception as e:
            st.warn("ASR_FAILED")
            self.io.save_json(
                job,
                "artifacts/asr/asr.status.json",
                {
                    "service": "asr",
                    "status": "failed",
                    "error": str(e),
                },
            )
        st.timing("asr", t0)

        # Load transcript for downstream steps
        st.transcript_text = self.io.load_text(job, "artifacts/asr/transcript.txt", default="") or ""

        # -----------------------
        # Step D: Diarization (fail-soft)
        # -----------------------
        t0 = _now_ms()
        diar_warns: list[str] = []
        try:
            diar = DiarizationService()

            if hasattr(diar, "diarize_with_warnings"):
                st.diar_segments, diar_warns = diar.diarize_with_warnings(
                    st.normalized_wav,
                    expected_speakers=st.expected_speakers,
                    max_speakers_cap=st.max_speakers_cap,
                    low_snr=st.low_snr_flag,
                )
            else:
                st.diar_segments = diar.diarize(st.normalized_wav)

            for w in diar_warns:
                st.warn(w)

            self.io.save_json(job, "artifacts/diarization/diarization.json", st.diar_segments)
            self.io.save_json(
                job,
                "artifacts/diarization/diar.status.json",
                {
                    "service": "diarization",
                    "status": "ok",
                    "num_segments": len(st.diar_segments),
                    "num_speakers": len(set(d.get("speaker") for d in st.diar_segments if d.get("speaker"))),
                },
            )

        except Exception as e:
            st.warn("DIARIZATION_FAILED_FALLBACK")
            st.diar_segments = []
            self.io.save_json(
                job,
                "artifacts/diarization/diar.status.json",
                {
                    "service": "diarization",
                    "status": "failed",
                    "error": str(e),
                },
            )
        st.timing("diarization", t0)

        speaker_set = set([d.get("speaker") for d in st.diar_segments if d.get("speaker")])
        st.single_speaker_mode = (not st.diar_segments) or (len(speaker_set) <= 1)
        if st.single_speaker_mode:
            st.warn("SINGLE_SPEAKER_MODE")

        # -----------------------
        # Step E: Alignment (requires ASR + diarization)
        # -----------------------
        t0 = _now_ms()
        try:
            asr_saved = self.io.load_json(job, "artifacts/asr/whisper.json", default=None)
            diar_saved = self.io.load_json(job, "artifacts/diarization/diarization.json", default=None)

            if not asr_saved or not diar_saved:
                st.skip("alignment")
                st.warn("ALIGNMENT_SKIPPED_MISSING_INPUT")
            else:
                kwargs = _safe_ctor_kwargs(
                    AlignmentService,
                    {
                        "max_gap_merge": 0.75,
                        "overlap_policy": "mark_overlap",
                        "unknown_label": "SPEAKER_UNKNOWN",
                    },
                )
                aligner = AlignmentService(**kwargs)

                aligned = aligner.align(asr_saved, diar_saved)
                st.speaker_segments = aligned.get("speaker_segments", []) or []
                st.conversation = aligner.build_conversation(asr_saved, diar_saved) or []

                self.io.save_json(job, "artifacts/alignment/speaker_segments.json", st.speaker_segments)
                self.io.save_json(job, "artifacts/alignment/conversation.json", st.conversation)
                self.io.save_json(
                    job,
                    "artifacts/alignment/alignment.status.json",
                    {
                        "service": "alignment",
                        "status": "ok",
                        "speaker_segments": len(st.speaker_segments),
                        "conversation_turns": len(st.conversation),
                    },
                )

        except Exception as e:
            st.warn("ALIGNMENT_FAILED")
            self.io.save_json(
                job,
                "artifacts/alignment/alignment.status.json",
                {
                    "service": "alignment",
                    "status": "failed",
                    "error": str(e),
                },
            )
        st.timing("alignment", t0)

        # Step F: Stats (safe)
        self._run_stats(st)

        # Step G: NLP enrichment (modular)
        self._run_sentiment(st)
        self._run_keywords(st)
        self._run_gender(st)
        self._run_emotion(st)

        # Topic / Summary from transcript
        self._run_topic(st)
        self._run_summary(st)

        # Intent / flags / factcheck (fail-soft)
        t0 = _now_ms()
        try:
            if st.conversation:
                st.conversation_with_intents = IntentService.annotate_conversation(st.conversation)
                st.intents_summary = IntentService.summarize_intents(st.conversation_with_intents)
            else:
                st.skip("intent")
                st.warn("INTENT_SKIPPED_NO_CONVERSATION")

            self.io.save_json(job, "artifacts/nlp/conversation_with_intents.json", st.conversation_with_intents)
            self.io.save_json(job, "artifacts/nlp/intents_summary.json", st.intents_summary)
        except Exception as e:
            st.warn("INTENT_FAILED")
            self.io.save_json(job, "artifacts/nlp/intents_summary.json", {})
            self.io.save_json(job, "artifacts/nlp/intent.status.json", {"status": "failed", "error": str(e)})
        st.timing("intent", t0)

        t0 = _now_ms()
        try:
            st.fact_checks = FactCheckService.fact_check(st.transcript_text or "")
            self.io.save_json(job, "artifacts/nlp/fact_checks.json", st.fact_checks)
        except Exception as e:
            st.warn("FACTCHECK_FAILED")
            self.io.save_json(job, "artifacts/nlp/fact_checks.json", [])
            self.io.save_json(job, "artifacts/nlp/factcheck.status.json", {"status": "failed", "error": str(e)})
        st.timing("factcheck", t0)

        t0 = _now_ms()
        try:
            if st.single_speaker_mode:
                st.warn("FLAGS_LIMITED_SINGLE_SPEAKER")
            st.flags = FlagService.generate_flags(st.conversation_with_intents) if st.conversation_with_intents else []
            self.io.save_json(job, "artifacts/nlp/flags.json", st.flags)
        except Exception as e:
            st.warn("FLAGS_FAILED")
            self.io.save_json(job, "artifacts/nlp/flags.json", [])
            self.io.save_json(job, "artifacts/nlp/flags.status.json", {"status": "failed", "error": str(e)})
        st.timing("flags", t0)

        # -----------------------
        # Step H: Insight Service
        # -----------------------
        t0 = _now_ms()
        try:
            if st.speaker_segments:
                session_input = VoiceIQInsightAdapter.from_orchestrator(
                    session_id=st.job_id,
                    asr_meta=st.asr_out.get("meta", {}) if isinstance(st.asr_out, dict) else {},
                    speaker_segments=st.speaker_segments or [],
                    speaker_stats=st.speaker_stats or {},
                    conversation_stats=st.conversation_stats or {},
                    warnings=meta["warnings"],
                )

                insight_response = InsightService.generate(session_input)
                st.insight_payload = insight_response.model_dump()

                self.io.save_json(job, "artifacts/insights/insight_result.json", st.insight_payload)
                self.io.save_json(
                    job,
                    "artifacts/insights/insight.status.json",
                    {
                        "service": "insights",
                        "status": "ok",
                    },
                )
            else:
                st.skip("insights")
                st.warn("INSIGHTS_SKIPPED_NO_SPEAKER_SEGMENTS")
                self.io.save_json(job, "artifacts/insights/insight_result.json", {})
                self.io.save_json(
                    job,
                    "artifacts/insights/insight.status.json",
                    {
                        "service": "insights",
                        "status": "skipped",
                        "reason": "no_speaker_segments",
                    },
                )
        except Exception as e:
            st.warn("INSIGHTS_FAILED")
            self.io.save_json(job, "artifacts/insights/insight_result.json", {})
            self.io.save_json(
                job,
                "artifacts/insights/insight.status.json",
                {
                    "service": "insights",
                    "status": "failed",
                    "error": str(e),
                },
            )
        st.timing("insights", t0)

        # -----------------------
        # Step I: PDF report
        # -----------------------
        t0 = _now_ms()
        try:
            pdf_bytes = PDFService.generate_pdf_report(
                transcript=st.transcript_text or "",
                speaker_segments=st.speaker_segments or [],
                summary=st.summary_text or "",
                topic=(st.topic or {}).get("topic", ""),
                conversation_stats=st.conversation_stats or {},
                speaker_stats=st.speaker_stats or {},
                emotion_overview=st.emotion_overview or {},
                intents_summary=st.intents_summary or {},
                flags=st.flags or [],
                fact_checks=st.fact_checks or [],
                warnings=meta["warnings"],
                audio_quality=st.audio_quality_payload or {},
            )

            self.io.save_bytes(job, "artifacts/report/report.pdf", pdf_bytes)
            pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
            self.io.save_text(job, "artifacts/report/report_base64.txt", pdf_b64)

            self.io.save_json(job, "artifacts/report/report.status.json", {"service": "pdf", "status": "ok"})
        except Exception as e:
            st.warn("PDF_FAILED")
            self.io.save_json(
                job, "artifacts/report/report.status.json", {"service": "pdf", "status": "failed", "error": str(e)}
            )
        st.timing("pdf", t0)

        meta["status"] = "ok"
        self.io.save_json(job, "meta.json", meta)
        return self._final_response(job, meta)

    # ------------------------------------------------------------------
    # Stages
    #
    # One method per pipeline step, in production order. Each stage reads
    # what it needs from _PipelineState, writes its artifacts to disk, and
    # records its own timing. All are fail-soft (warn + continue) except
    # the four hard-fail gates, which raise _HardFail.
    # ------------------------------------------------------------------

    def _run_stats(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            st.speaker_stats = MetadataExtractor.compute_speaker_stats(st.speaker_segments or [])
            st.conversation_stats = MetadataExtractor.compute_conversation_stats(
                st.speaker_segments or [], st.diar_segments or []
            )
            self.io.save_json(st.job, "artifacts/nlp/speaker_stats.json", st.speaker_stats)
            self.io.save_json(st.job, "artifacts/nlp/conversation_stats.json", st.conversation_stats)
        except Exception as e:
            st.warn("STATS_FAILED")
            self.io.save_json(
                st.job,
                "artifacts/nlp/stats.status.json",
                {
                    "service": "stats",
                    "status": "failed",
                    "error": str(e),
                },
            )
        st.timing("stats", t0)

    def _run_sentiment(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            if st.speaker_segments:
                out = SentimentService.analyze_speaker_segments(st.speaker_segments)
                st.speaker_segments = out
                self.io.save_json(st.job, "artifacts/nlp/sentiment_segments.json", st.speaker_segments)
            else:
                st.skip("sentiment")
                st.warn("SENTIMENT_SKIPPED_NO_SPEAKER_SEGMENTS")
        except Exception as e:
            st.warn("SENTIMENT_FAILED")
            self.io.save_json(st.job, "artifacts/nlp/sentiment.status.json", {"status": "failed", "error": str(e)})
        st.timing("sentiment", t0)

    def _run_keywords(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            if st.speaker_segments:
                out = KeywordService.extract_keywords_per_segment(st.speaker_segments)
                st.speaker_segments = out
                self.io.save_json(st.job, "artifacts/nlp/keywords_segments.json", st.speaker_segments)
            else:
                st.skip("keywords")
                st.warn("KEYWORDS_SKIPPED_NO_SPEAKER_SEGMENTS")
        except Exception as e:
            st.warn("KEYWORDS_FAILED")
            self.io.save_json(st.job, "artifacts/nlp/keywords.status.json", {"status": "failed", "error": str(e)})
        st.timing("keywords", t0)

    def _run_gender(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            if st.low_snr_flag:
                st.skip("gender")
                st.warn("GENDER_SKIPPED_LOW_SNR")
            elif st.speaker_segments:
                out = GenderService.add_gender_to_segments(st.speaker_segments, st.normalized_wav)
                st.speaker_segments = out
                self.io.save_json(st.job, "artifacts/nlp/gender_segments.json", st.speaker_segments)
            else:
                st.skip("gender")
                st.warn("GENDER_SKIPPED_NO_SPEAKER_SEGMENTS")
        except Exception as e:
            st.warn("GENDER_FAILED")
            self.io.save_json(st.job, "artifacts/nlp/gender.status.json", {"status": "failed", "error": str(e)})
        st.timing("gender", t0)

    def _run_emotion(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            if st.low_snr_flag:
                st.skip("emotion")
                st.warn("EMOTION_SKIPPED_LOW_SNR")
                st.emotion_overview = {}
            elif st.speaker_segments:
                out = EmotionService.analyze_speaker_segments(st.normalized_wav, st.speaker_segments)
                st.speaker_segments = out
                st.emotion_overview = EmotionService.summarize_emotions(st.speaker_segments)
                self.io.save_json(st.job, "artifacts/nlp/emotion_segments.json", st.speaker_segments)
                self.io.save_json(st.job, "artifacts/nlp/emotion_overview.json", st.emotion_overview)
            else:
                st.skip("emotion")
                st.warn("EMOTION_SKIPPED_NO_SPEAKER_SEGMENTS")
                st.emotion_overview = {}
        except Exception as e:
            st.warn("EMOTION_FAILED")
            self.io.save_json(st.job, "artifacts/nlp/emotion.status.json", {"status": "failed", "error": str(e)})
        st.timing("emotion", t0)

    def _run_topic(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            st.topic = TopicService.classify(st.transcript_text or "")
            self.io.save_json(st.job, "artifacts/nlp/topic.json", st.topic)
        except Exception as e:
            st.warn("TOPIC_FAILED")
            self.io.save_json(
                st.job, "artifacts/nlp/topic.json", {"topic": "unknown", "confidence": 0.0, "error": str(e)}
            )
        st.timing("topic", t0)

    def _run_summary(self, st: _PipelineState) -> None:
        t0 = _now_ms()
        try:
            st.summary_text = SummaryService.generate_summary(st.transcript_text or "")
            self.io.save_text(st.job, "artifacts/nlp/summary.txt", st.summary_text or "")
        except Exception as e:
            st.warn("SUMMARY_FAILED")
            self.io.save_text(st.job, "artifacts/nlp/summary.txt", "")
            self.io.save_json(st.job, "artifacts/nlp/summary.status.json", {"status": "failed", "error": str(e)})
        st.timing("summary", t0)

    def _final_response(self, job: JobPaths, meta: dict[str, Any]) -> dict[str, Any]:
        transcript = self.io.load_text(job, "artifacts/asr/transcript.txt", default="")
        asr_out = self.io.load_json(
            job, "artifacts/asr/whisper.json", default={"text": transcript, "segments": [], "meta": {}}
        )
        diar = self.io.load_json(job, "artifacts/diarization/diarization.json", default=[])

        speaker_segments = (
            self.io.load_json(job, "artifacts/nlp/emotion_segments.json", default=None)
            or self.io.load_json(job, "artifacts/nlp/gender_segments.json", default=None)
            or self.io.load_json(job, "artifacts/nlp/keywords_segments.json", default=None)
            or self.io.load_json(job, "artifacts/alignment/speaker_segments.json", default=[])
        )

        conversation = self.io.load_json(
            job, "artifacts/nlp/conversation_with_intents.json", default=None
        ) or self.io.load_json(job, "artifacts/alignment/conversation.json", default=[])

        speaker_stats = self.io.load_json(job, "artifacts/nlp/speaker_stats.json", default={})
        conversation_stats = self.io.load_json(job, "artifacts/nlp/conversation_stats.json", default={})
        topic = self.io.load_json(job, "artifacts/nlp/topic.json", default={"topic": "unknown", "confidence": 0.0})
        summary = self.io.load_text(job, "artifacts/nlp/summary.txt", default="")

        intents_summary = self.io.load_json(job, "artifacts/nlp/intents_summary.json", default={})
        flags = self.io.load_json(job, "artifacts/nlp/flags.json", default=[])
        fact_checks = self.io.load_json(job, "artifacts/nlp/fact_checks.json", default=[])
        emotion_overview = self.io.load_json(job, "artifacts/nlp/emotion_overview.json", default={})
        insights = self.io.load_json(job, "artifacts/insights/insight_result.json", default=None)

        pdf_b64 = self.io.load_text(job, "artifacts/report/report_base64.txt", default=None)
        audio_quality = self.io.load_json(job, "artifacts/audio/audio_quality.json", default=None)

        single_speaker_mode = "SINGLE_SPEAKER_MODE" in (meta.get("warnings") or [])

        return {
            "request_id": meta.get("job_id"),
            "transcript": transcript or "",
            "asr_meta": {
                "model": (asr_out.get("meta") or {}).get("model"),
                "language": (asr_out.get("meta") or {}).get("language"),
                "duration": (asr_out.get("meta") or {}).get("duration"),
                "segments": asr_out.get("segments") or [],
            },
            "segments": diar or [],
            "speaker_segments": speaker_segments or [],
            "conversation": conversation or [],
            "speaker_stats": speaker_stats or {},
            "conversation_stats": conversation_stats or {},
            "topic": topic or {"topic": "unknown", "confidence": 0.0},
            "summary": summary or "",
            "report_pdf_base64": pdf_b64,
            "intents_summary": intents_summary or {},
            "fact_checks": fact_checks or [],
            "flags": flags or [],
            "insights": insights,
            "timeline": [
                {
                    "start": t.get("start", 0.0),
                    "end": t.get("end", 0.0),
                    "speaker": t.get("speaker", "UNKNOWN"),
                    "text": t.get("text", ""),
                    "intent": t.get("intent", "other"),
                    "overlap": bool(t.get("overlap", False)),
                }
                for t in (conversation or [])
            ],
            "emotion_overview": emotion_overview or {},
            "warnings": meta.get("warnings") or [],
            "single_speaker_mode": single_speaker_mode,
            "audio_quality": audio_quality,
            "pipeline_meta": {
                "status": meta.get("status"),
                "timings_ms": meta.get("timings_ms"),
                "skipped_steps": meta.get("skipped_steps"),
                "job_dir": str(job.root),
            },
        }
