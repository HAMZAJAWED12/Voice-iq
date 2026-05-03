# app/pipeline/orchestrator.py
from __future__ import annotations

import base64
import time
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
from app.utils.audio_quality import analyze_audio_quality
from app.utils.audio_utils import normalize_to_wav
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

        def warn(code: str):
            if code not in meta["warnings"]:
                meta["warnings"].append(code)

        def timing(name: str, start_ms: int):
            meta["timings_ms"][name] = _now_ms() - start_ms

        # Find uploaded file saved by API as input/original.<ext>
        input_candidates = list(job.input_dir.glob("original.*"))
        if not input_candidates:
            meta["status"] = "failed"
            warn("MISSING_INPUT_AUDIO")
            self.io.save_json(job, "meta.json", meta)
            return self._final_response(job, meta)

        input_audio_path = input_candidates[0]

        # -----------------------
        # Step A: Normalize audio
        # -----------------------
        t0 = _now_ms()
        normalized_wav = str(self.io.p(job, "artifacts/audio/normalized.wav"))
        try:
            normalize_to_wav(str(input_audio_path), normalized_wav, sr=16000)
            self.io.save_json(
                job,
                "artifacts/audio/normalize.status.json",
                {
                    "service": "audio_normalize",
                    "status": "ok",
                    "input": str(input_audio_path),
                    "output": normalized_wav,
                },
            )
        except Exception as e:
            meta["status"] = "failed"
            warn("AUDIO_NORMALIZATION_FAILED")
            self.io.save_json(
                job,
                "artifacts/audio/normalize.status.json",
                {
                    "service": "audio_normalize",
                    "status": "failed",
                    "error": str(e),
                },
            )
            timing("audio_normalize", t0)
            self.io.save_json(job, "meta.json", meta)
            return self._final_response(job, meta)
        timing("audio_normalize", t0)

        # -----------------------
        # Step B: Audio quality guardrails
        # -----------------------
        t0 = _now_ms()
        aq = None
        audio_quality_payload: dict[str, Any] = {}
        try:
            aq = analyze_audio_quality(normalized_wav)
            audio_quality_payload = aq.to_dict()
            self.io.save_json(job, "artifacts/audio/audio_quality.json", audio_quality_payload)
            self.io.save_json(
                job,
                "artifacts/audio/audio_quality.status.json",
                {
                    "service": "audio_quality",
                    "status": "ok",
                },
            )

            if aq.is_silent or aq.is_near_silent:
                meta["status"] = "failed"
                warn("AUDIO_SILENT_OR_NEAR_SILENT")
                self.io.save_json(job, "meta.json", meta)
                return self._final_response(job, meta)

            if aq.low_snr:
                warn("LOW_SNR_AUDIO")
            if aq.very_low_snr:
                warn("HEAVY_NOISE_AUDIO")

        except Exception as e:
            warn("AUDIO_QUALITY_FAILED")
            self.io.save_json(
                job,
                "artifacts/audio/audio_quality.status.json",
                {
                    "service": "audio_quality",
                    "status": "failed",
                    "error": str(e),
                },
            )
        timing("audio_quality", t0)

        low_snr_flag = bool(aq and (aq.low_snr or aq.very_low_snr))

        # -----------------------
        # Step C: ASR
        # -----------------------
        t0 = _now_ms()
        asr_out: dict[str, Any] = {"text": "", "segments": [], "meta": {}}
        try:
            asr = ASRService(model_name=whisper_model, language=language)
            asr_out = asr.transcribe(normalized_wav)

            text = (asr_out.get("text") or "").strip()
            segments = asr_out.get("segments") or []

            self.io.save_json(job, "artifacts/asr/whisper.json", asr_out)
            self.io.save_text(job, "artifacts/asr/transcript.txt", text)
            self.io.save_json(
                job,
                "artifacts/asr/asr.status.json",
                {
                    "service": "asr",
                    "status": "ok",
                    "model": whisper_model,
                    "num_segments": len(segments),
                    "total_chars": len(text),
                },
            )

            if not text:
                warn("EMPTY_TRANSCRIPT")

        except Exception as e:
            warn("ASR_FAILED")
            self.io.save_json(
                job,
                "artifacts/asr/asr.status.json",
                {
                    "service": "asr",
                    "status": "failed",
                    "error": str(e),
                },
            )
        timing("asr", t0)

        # Load transcript for downstream steps
        transcript_text = self.io.load_text(job, "artifacts/asr/transcript.txt", default="")

        # -----------------------
        # Step D: Diarization (fail-soft)
        # -----------------------
        t0 = _now_ms()
        diar_segments: list[dict[str, Any]] = []
        diar_warns: list[str] = []
        try:
            diar = DiarizationService()

            if hasattr(diar, "diarize_with_warnings"):
                diar_segments, diar_warns = diar.diarize_with_warnings(
                    normalized_wav,
                    expected_speakers=expected_speakers,
                    max_speakers_cap=max_speakers_cap,
                    low_snr=low_snr_flag,
                )
            else:
                diar_segments = diar.diarize(normalized_wav)

            for w in diar_warns:
                warn(w)

            self.io.save_json(job, "artifacts/diarization/diarization.json", diar_segments)
            self.io.save_json(
                job,
                "artifacts/diarization/diar.status.json",
                {
                    "service": "diarization",
                    "status": "ok",
                    "num_segments": len(diar_segments),
                    "num_speakers": len(set(d.get("speaker") for d in diar_segments if d.get("speaker"))),
                },
            )

        except Exception as e:
            warn("DIARIZATION_FAILED_FALLBACK")
            diar_segments = []
            self.io.save_json(
                job,
                "artifacts/diarization/diar.status.json",
                {
                    "service": "diarization",
                    "status": "failed",
                    "error": str(e),
                },
            )
        timing("diarization", t0)

        speaker_set = set([d.get("speaker") for d in diar_segments if d.get("speaker")])
        single_speaker_mode = (not diar_segments) or (len(speaker_set) <= 1)
        if single_speaker_mode:
            warn("SINGLE_SPEAKER_MODE")

        # -----------------------
        # Step E: Alignment (requires ASR + diarization)
        # -----------------------
        t0 = _now_ms()
        speaker_segments: list[dict[str, Any]] = []
        conversation: list[dict[str, Any]] = []
        try:
            asr_saved = self.io.load_json(job, "artifacts/asr/whisper.json", default=None)
            diar_saved = self.io.load_json(job, "artifacts/diarization/diarization.json", default=None)

            if not asr_saved or not diar_saved:
                meta["skipped_steps"].append("alignment")
                warn("ALIGNMENT_SKIPPED_MISSING_INPUT")
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
                speaker_segments = aligned.get("speaker_segments", []) or []
                conversation = aligner.build_conversation(asr_saved, diar_saved) or []

                self.io.save_json(job, "artifacts/alignment/speaker_segments.json", speaker_segments)
                self.io.save_json(job, "artifacts/alignment/conversation.json", conversation)
                self.io.save_json(
                    job,
                    "artifacts/alignment/alignment.status.json",
                    {
                        "service": "alignment",
                        "status": "ok",
                        "speaker_segments": len(speaker_segments),
                        "conversation_turns": len(conversation),
                    },
                )

        except Exception as e:
            warn("ALIGNMENT_FAILED")
            self.io.save_json(
                job,
                "artifacts/alignment/alignment.status.json",
                {
                    "service": "alignment",
                    "status": "failed",
                    "error": str(e),
                },
            )
        timing("alignment", t0)

        # -----------------------
        # Step F: Stats (safe)
        # -----------------------
        t0 = _now_ms()
        speaker_stats: dict[str, Any] = {}
        conversation_stats: dict[str, Any] = {}
        try:
            speaker_stats = MetadataExtractor.compute_speaker_stats(speaker_segments or [])
            conversation_stats = MetadataExtractor.compute_conversation_stats(
                speaker_segments or [], diar_segments or []
            )
            self.io.save_json(job, "artifacts/nlp/speaker_stats.json", speaker_stats)
            self.io.save_json(job, "artifacts/nlp/conversation_stats.json", conversation_stats)
        except Exception as e:
            warn("STATS_FAILED")
            self.io.save_json(
                job,
                "artifacts/nlp/stats.status.json",
                {
                    "service": "stats",
                    "status": "failed",
                    "error": str(e),
                },
            )
        timing("stats", t0)

        # -----------------------
        # Step G: NLP enrichment (modular)
        # -----------------------
        t0 = _now_ms()
        try:
            if speaker_segments:
                out = SentimentService.analyze_speaker_segments(speaker_segments)
                speaker_segments = out
                self.io.save_json(job, "artifacts/nlp/sentiment_segments.json", speaker_segments)
            else:
                meta["skipped_steps"].append("sentiment")
                warn("SENTIMENT_SKIPPED_NO_SPEAKER_SEGMENTS")
        except Exception as e:
            warn("SENTIMENT_FAILED")
            self.io.save_json(job, "artifacts/nlp/sentiment.status.json", {"status": "failed", "error": str(e)})
        timing("sentiment", t0)

        t0 = _now_ms()
        try:
            if speaker_segments:
                out = KeywordService.extract_keywords_per_segment(speaker_segments)
                speaker_segments = out
                self.io.save_json(job, "artifacts/nlp/keywords_segments.json", speaker_segments)
            else:
                meta["skipped_steps"].append("keywords")
                warn("KEYWORDS_SKIPPED_NO_SPEAKER_SEGMENTS")
        except Exception as e:
            warn("KEYWORDS_FAILED")
            self.io.save_json(job, "artifacts/nlp/keywords.status.json", {"status": "failed", "error": str(e)})
        timing("keywords", t0)

        t0 = _now_ms()
        try:
            if low_snr_flag:
                meta["skipped_steps"].append("gender")
                warn("GENDER_SKIPPED_LOW_SNR")
            elif speaker_segments:
                out = GenderService.add_gender_to_segments(speaker_segments, normalized_wav)
                speaker_segments = out
                self.io.save_json(job, "artifacts/nlp/gender_segments.json", speaker_segments)
            else:
                meta["skipped_steps"].append("gender")
                warn("GENDER_SKIPPED_NO_SPEAKER_SEGMENTS")
        except Exception as e:
            warn("GENDER_FAILED")
            self.io.save_json(job, "artifacts/nlp/gender.status.json", {"status": "failed", "error": str(e)})
        timing("gender", t0)

        t0 = _now_ms()
        emotion_overview: dict[str, Any] = {}
        try:
            if low_snr_flag:
                meta["skipped_steps"].append("emotion")
                warn("EMOTION_SKIPPED_LOW_SNR")
                emotion_overview = {}
            elif speaker_segments:
                out = EmotionService.analyze_speaker_segments(normalized_wav, speaker_segments)
                speaker_segments = out
                emotion_overview = EmotionService.summarize_emotions(speaker_segments)
                self.io.save_json(job, "artifacts/nlp/emotion_segments.json", speaker_segments)
                self.io.save_json(job, "artifacts/nlp/emotion_overview.json", emotion_overview)
            else:
                meta["skipped_steps"].append("emotion")
                warn("EMOTION_SKIPPED_NO_SPEAKER_SEGMENTS")
                emotion_overview = {}
        except Exception as e:
            warn("EMOTION_FAILED")
            self.io.save_json(job, "artifacts/nlp/emotion.status.json", {"status": "failed", "error": str(e)})
        timing("emotion", t0)

        # Topic / Summary from transcript
        t0 = _now_ms()
        topic: dict[str, Any] = {"topic": "unknown", "confidence": 0.0}
        try:
            topic = TopicService.classify(transcript_text or "")
            self.io.save_json(job, "artifacts/nlp/topic.json", topic)
        except Exception as e:
            warn("TOPIC_FAILED")
            self.io.save_json(job, "artifacts/nlp/topic.json", {"topic": "unknown", "confidence": 0.0, "error": str(e)})
        timing("topic", t0)

        t0 = _now_ms()
        summary_text = ""
        try:
            summary_text = SummaryService.generate_summary(transcript_text or "")
            self.io.save_text(job, "artifacts/nlp/summary.txt", summary_text or "")
        except Exception as e:
            warn("SUMMARY_FAILED")
            self.io.save_text(job, "artifacts/nlp/summary.txt", "")
            self.io.save_json(job, "artifacts/nlp/summary.status.json", {"status": "failed", "error": str(e)})
        timing("summary", t0)

        # Intent / flags / factcheck (fail-soft)
        t0 = _now_ms()
        conversation_with_intents: list[dict[str, Any]] = []
        intents_summary: dict[str, Any] = {}
        try:
            if conversation:
                conversation_with_intents = IntentService.annotate_conversation(conversation)
                intents_summary = IntentService.summarize_intents(conversation_with_intents)
            else:
                meta["skipped_steps"].append("intent")
                warn("INTENT_SKIPPED_NO_CONVERSATION")

            self.io.save_json(job, "artifacts/nlp/conversation_with_intents.json", conversation_with_intents)
            self.io.save_json(job, "artifacts/nlp/intents_summary.json", intents_summary)
        except Exception as e:
            warn("INTENT_FAILED")
            self.io.save_json(job, "artifacts/nlp/intents_summary.json", {})
            self.io.save_json(job, "artifacts/nlp/intent.status.json", {"status": "failed", "error": str(e)})
        timing("intent", t0)

        t0 = _now_ms()
        fact_checks: list[dict[str, Any]] = []
        try:
            fact_checks = FactCheckService.fact_check(transcript_text or "")
            self.io.save_json(job, "artifacts/nlp/fact_checks.json", fact_checks)
        except Exception as e:
            warn("FACTCHECK_FAILED")
            self.io.save_json(job, "artifacts/nlp/fact_checks.json", [])
            self.io.save_json(job, "artifacts/nlp/factcheck.status.json", {"status": "failed", "error": str(e)})
        timing("factcheck", t0)

        t0 = _now_ms()
        flags: list[dict[str, Any]] = []
        try:
            if single_speaker_mode:
                warn("FLAGS_LIMITED_SINGLE_SPEAKER")
            flags = FlagService.generate_flags(conversation_with_intents) if conversation_with_intents else []
            self.io.save_json(job, "artifacts/nlp/flags.json", flags)
        except Exception as e:
            warn("FLAGS_FAILED")
            self.io.save_json(job, "artifacts/nlp/flags.json", [])
            self.io.save_json(job, "artifacts/nlp/flags.status.json", {"status": "failed", "error": str(e)})
        timing("flags", t0)

        # -----------------------
        # Step H: Insight Service
        # -----------------------
        t0 = _now_ms()
        insight_payload: dict[str, Any] | None = None
        try:
            if speaker_segments:
                session_input = VoiceIQInsightAdapter.from_orchestrator(
                    session_id=job_id,
                    asr_meta=asr_out.get("meta", {}) if isinstance(asr_out, dict) else {},
                    speaker_segments=speaker_segments or [],
                    speaker_stats=speaker_stats or {},
                    conversation_stats=conversation_stats or {},
                    warnings=meta["warnings"],
                )

                insight_response = InsightService.generate(session_input)
                insight_payload = insight_response.model_dump()

                self.io.save_json(job, "artifacts/insights/insight_result.json", insight_payload)
                self.io.save_json(
                    job,
                    "artifacts/insights/insight.status.json",
                    {
                        "service": "insights",
                        "status": "ok",
                    },
                )
            else:
                meta["skipped_steps"].append("insights")
                warn("INSIGHTS_SKIPPED_NO_SPEAKER_SEGMENTS")
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
            warn("INSIGHTS_FAILED")
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
        timing("insights", t0)

        # -----------------------
        # Step I: PDF report
        # -----------------------
        t0 = _now_ms()
        try:
            pdf_bytes = PDFService.generate_pdf_report(
                transcript=transcript_text or "",
                speaker_segments=speaker_segments or [],
                summary=summary_text or "",
                topic=(topic or {}).get("topic", ""),
                conversation_stats=conversation_stats or {},
                speaker_stats=speaker_stats or {},
                emotion_overview=emotion_overview or {},
                intents_summary=intents_summary or {},
                flags=flags or [],
                fact_checks=fact_checks or [],
                warnings=meta["warnings"],
                audio_quality=audio_quality_payload or {},
            )

            self.io.save_bytes(job, "artifacts/report/report.pdf", pdf_bytes)
            pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
            self.io.save_text(job, "artifacts/report/report_base64.txt", pdf_b64)

            self.io.save_json(job, "artifacts/report/report.status.json", {"service": "pdf", "status": "ok"})
        except Exception as e:
            warn("PDF_FAILED")
            self.io.save_json(
                job, "artifacts/report/report.status.json", {"service": "pdf", "status": "failed", "error": str(e)}
            )
        timing("pdf", t0)

        meta["status"] = "ok"
        self.io.save_json(job, "meta.json", meta)
        return self._final_response(job, meta)

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
