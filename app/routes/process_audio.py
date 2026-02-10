# app/routes/process_audio.py

from fastapi import APIRouter, UploadFile, File, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional
import tempfile
import os
import uuid

from app.utils.audio_utils import normalize_to_wav
from app.utils.logger import logger

# ✅ FIX: your audio_quality.py uses analyze_audio_quality() not AudioQualityService
from app.utils.audio_quality import analyze_audio_quality

# services
from app.services.asr_service import ASRService
from app.services.diarization_service import DiarizationService
from app.services.alignment_service import AlignmentService

from app.services.metadata_service import MetadataExtractor
from app.services.sentiment_service import SentimentService
from app.services.keyword_service import KeywordService
from app.services.topic_service import TopicService
from app.services.summary_service import SummaryService
from app.services.gender_service import GenderService
from app.services.pdf_service import PDFService
from app.services.emotion_service import EmotionService
from app.services.intent_service import IntentService
from app.services.factcheck_service import FactCheckService
from app.services.flag_service import FlagService


router = APIRouter()


# --------------------------
# Response Models
# --------------------------

class ASRMeta(BaseModel):
    model: Optional[str]
    language: Optional[str]
    duration: Optional[float]
    segments: Optional[List[Dict]]


class SpeakerSegment(BaseModel):
    start: float
    end: float
    speaker: str
    text: Optional[str]

    sentiment: Optional[str] = None
    sentiment_score: Optional[float] = None
    keywords: Optional[List[str]] = None

    confidence: Optional[float] = None
    diarization_confidence: Optional[float] = None
    overlap: Optional[bool] = None

    gender: Optional[str] = None
    gender_confidence: Optional[float] = None


class SpeakerStats(BaseModel):
    total_speaking_time: float
    segment_count: int
    total_words: int
    longest_monologue: float
    first_spoke_at: float
    last_spoke_at: float
    avg_segment_length: float
    wpm: float
    speaking_ratio: float
    word_ratio: float


class ConversationStats(BaseModel):
    total_duration: float
    total_segments: int
    total_words: int
    avg_turn_length: float
    speaker_count: int
    conversation_start: float
    conversation_end: float


class TopicInfo(BaseModel):
    topic: str
    confidence: float


class FlagItem(BaseModel):
    type: str
    speaker: str
    start: float
    end: float
    text: str
    score: float
    note: Optional[str] = None


class FactCheckItem(BaseModel):
    type: str
    value: str
    status: str
    source: Optional[str] = None
    note: Optional[str] = None


class ProcessAudioResponse(BaseModel):
    request_id: str
    transcript: str
    asr_meta: ASRMeta

    segments: List[Dict]                # diarization segments
    speaker_segments: List[SpeakerSegment]
    conversation: List[SpeakerSegment]

    speaker_stats: Dict[str, SpeakerStats]
    conversation_stats: ConversationStats

    topic: TopicInfo

    summary: Optional[str] = None
    report_pdf_base64: Optional[str] = None

    intents_summary: Optional[Dict[str, int]] = None
    fact_checks: Optional[List[FactCheckItem]] = None
    flags: Optional[List[FlagItem]] = None
    timeline: Optional[List[Dict]] = None
    emotion_overview: Optional[Dict[str, Dict[str, float]]] = None

    # NEW
    warnings: Optional[List[str]] = None
    single_speaker_mode: Optional[bool] = None
    audio_quality: Optional[Dict] = None


# --------------------------
# Main Route
# --------------------------

@router.post("/process-audio", response_model=ProcessAudioResponse)
async def process_audio(file: UploadFile = File(...)):
    request_id = str(uuid.uuid4())
    logger.info(f"[{request_id}] Received: {file.filename}")

    if not file.filename.lower().endswith((".mp3", ".wav", ".m4a", ".flac")):
        raise HTTPException(status_code=400, detail="Unsupported file format")

    tmpdir = tempfile.mkdtemp()
    in_path = os.path.join(tmpdir, file.filename)
    wav_path = os.path.join(tmpdir, "normalized.wav")

    warnings: List[str] = []
    single_speaker_mode = False

    # DEV knobs
    expected_speakers: Optional[int] = None
    max_speakers_cap: int = 8

    try:
        # Save uploaded audio
        with open(in_path, "wb") as f:
            f.write(await file.read())

        # Normalize to mono 16k WAV
        normalize_to_wav(in_path, wav_path, sr=16000)

        # -----------------------
        # Step 0: Audio quality checks
        # -----------------------
        aq = analyze_audio_quality(wav_path)
        audio_quality_payload = aq.to_dict()

        # Hard reject silent/near-silent
        if aq.is_silent or aq.is_near_silent:
            raise HTTPException(
                status_code=400,
                detail="Audio is silent or near-silent (rejecting to avoid unreliable outputs)."
            )

        # Warnings based on SNR
        if aq.low_snr:
            warnings.append("LOW_SNR_AUDIO")
        if aq.very_low_snr:
            warnings.append("HEAVY_NOISE_AUDIO")

        # -----------------------
        # Step 1: ASR
        # -----------------------
        asr = ASRService(model_name="base", language=None)
        asr_out = asr.transcribe(wav_path)

        text = (asr_out.get("text") or "").strip()
        segments_asr = asr_out.get("segments") or []

        meta = {
            "model": asr_out.get("meta", {}).get("model", "base"),
            "language": asr_out.get("meta", {}).get("language"),
            "duration": asr_out.get("meta", {}).get("duration"),
            "segments": segments_asr,
        }

        if not text:
            warnings.append("EMPTY_TRANSCRIPT")

        # -----------------------
        # Step 2: Diarization (fail-soft)
        # -----------------------
        diar_segments: List[Dict] = []

        try:
            diar = DiarizationService()
            diar_segments, diar_warn = diar.diarize_with_warnings(
                wav_path,
                expected_speakers=expected_speakers,
                max_speakers_cap=max_speakers_cap,
                low_snr=bool(aq.low_snr or aq.very_low_snr),
            )
            warnings.extend(diar_warn or [])
        except Exception as e:
            logger.error(f"Diarization failed (fail-soft): {e}")
            warnings.append("DIARIZATION_FAILED_FALLBACK")
            diar_segments = []

        # -----------------------
        # Step 2b: Single-speaker mode
        # -----------------------
        speaker_set = set([d.get("speaker") for d in (diar_segments or []) if d.get("speaker")])
        if (not diar_segments) or (len(speaker_set) <= 1):
            single_speaker_mode = True
            warnings.append("SINGLE_SPEAKER_MODE")

        # -----------------------
        # Step 3: Alignment (overlap policy)
        # -----------------------
        speaker_segments: List[Dict] = []
        conversation: List[Dict] = []

        if diar_segments:
            aligner = AlignmentService(
                max_gap_merge=0.75,
                overlap_policy="mark_overlap",
                unknown_label="SPEAKER_UNKNOWN",
            )

            try:
                aligned = aligner.align(
                    {"text": text, "segments": segments_asr, "meta": meta},
                    diar_segments
                )
                speaker_segments = aligned.get("speaker_segments", []) or []
            except Exception as e:
                logger.error(f"Alignment failed (fail-soft): {e}")
                warnings.append("ALIGNMENT_FAILED")
                speaker_segments = []

            try:
                conversation = aligner.build_conversation(
                    {"text": text, "segments": segments_asr, "meta": meta},
                    diar_segments
                ) or []
            except Exception as e:
                logger.error(f"Conversation build failed (fail-soft): {e}")
                warnings.append("CONVERSATION_BUILD_FAILED")
                conversation = []
        else:
            speaker_segments = []
            conversation = []

        # -----------------------
        # Step 4: Stats
        # -----------------------
        speaker_stats = MetadataExtractor.compute_speaker_stats(speaker_segments or [])
        conversation_stats = MetadataExtractor.compute_conversation_stats(
            speaker_segments or [],
            diar_segments or []
        )

        # -----------------------
        # Step 5: Segment NLP enrichment
        # -----------------------
        if speaker_segments:
            speaker_segments = SentimentService.analyze_speaker_segments(speaker_segments)
            speaker_segments = KeywordService.extract_keywords_per_segment(speaker_segments)

            if not (aq.low_snr or aq.very_low_snr):
                speaker_segments = GenderService.add_gender_to_segments(speaker_segments, wav_path)
            else:
                warnings.append("GENDER_SKIPPED_LOW_SNR")

        if speaker_segments and not (aq.low_snr or aq.very_low_snr):
            speaker_segments = EmotionService.analyze_speaker_segments(wav_path, speaker_segments)
            emotion_overview = EmotionService.summarize_emotions(speaker_segments)
        else:
            if speaker_segments:
                warnings.append("EMOTION_SKIPPED_LOW_SNR")
            emotion_overview = {}

        # -----------------------
        # Step 6: Topic + summary
        # -----------------------
        topic = TopicService.classify(text or "")
        summary = SummaryService.generate_summary(text or "")

        # -----------------------
        # Step 7: Intent classification
        # -----------------------
        if conversation:
            conversation_with_intents = IntentService.annotate_conversation(conversation)
        else:
            conv_fallback = [{
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "speaker": seg.get("speaker", "UNKNOWN"),
                "text": seg.get("text", ""),
                "overlap": bool(seg.get("overlap", False)),
            } for seg in (speaker_segments or [])]

            conversation_with_intents = IntentService.annotate_conversation(conv_fallback) if conv_fallback else []

        intents_summary = IntentService.summarize_intents(conversation_with_intents) if conversation_with_intents else {}

        # -----------------------
        # Step 8: Fact checks + flags
        # -----------------------
        fact_checks = FactCheckService.fact_check(text or "")

        if single_speaker_mode:
            warnings.append("FLAGS_LIMITED_SINGLE_SPEAKER")
        flags = FlagService.generate_flags(conversation_with_intents) if conversation_with_intents else []

        timeline = [{
            "start": turn.get("start", 0.0),
            "end": turn.get("end", 0.0),
            "speaker": turn.get("speaker", "UNKNOWN"),
            "text": turn.get("text", ""),
            "intent": turn.get("intent", "other"),
            "overlap": bool(turn.get("overlap", False)),
        } for turn in (conversation_with_intents or [])]

        # -----------------------
        # Step 9: PDF report
        # -----------------------
        pdf_bytes = PDFService.generate_pdf_report(
            transcript=text,
            speaker_segments=speaker_segments,
            summary=summary,
            topic=topic.get("topic", ""),
            conversation_stats=conversation_stats,
            speaker_stats=speaker_stats,
            emotion_overview=emotion_overview,
            intents_summary=intents_summary,
            flags=flags,
            fact_checks=fact_checks,
            warnings=warnings,
            audio_quality=audio_quality_payload,   # ✅ NEW: include in PDF
        )

        pdf_b64 = PDFService.to_base64(pdf_bytes)

        return {
            "request_id": request_id,
            "transcript": text or "",
            "asr_meta": meta,
            "segments": diar_segments or [],
            "speaker_segments": speaker_segments or [],
            "conversation": conversation_with_intents or [],
            "speaker_stats": speaker_stats or {},
            "conversation_stats": conversation_stats or {},
            "topic": topic or {"topic": "unknown", "confidence": 0.0},
            "summary": summary,
            "report_pdf_base64": pdf_b64,
            "intents_summary": intents_summary,
            "fact_checks": fact_checks,
            "flags": flags,
            "timeline": timeline,
            "emotion_overview": emotion_overview,
            "warnings": warnings,
            "single_speaker_mode": single_speaker_mode,
            "audio_quality": audio_quality_payload,
        }

    finally:
        try:
            if os.path.exists(in_path):
                os.remove(in_path)
            if os.path.exists(wav_path):
                os.remove(wav_path)
            os.rmdir(tmpdir)
        except Exception as e:
            logger.warning(f"Cleanup warning: {e}")