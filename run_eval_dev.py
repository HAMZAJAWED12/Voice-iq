# run_eval_dev.py
from __future__ import annotations

from collections import Counter
from typing import Dict, Any, List

from app.services.asr_service import ASRService
from app.services.diarization_service import DiarizationService
from app.services.alignment_service import AlignmentService

# These services in your repo are used as class/static methods in process_audio.py
from app.services.sentiment_service import SentimentService
from app.services.topic_service import TopicService
from app.services.summary_service import SummaryService

from app.utils.audio_utils import normalize_audio

# evaluator (dev-only)
from app.evaluation.evaluator import VoiceIQEvaluator


def _sentiment_distribution(speaker_segments: List[Dict]) -> Dict[str, float]:
    labels = [s.get("sentiment") for s in speaker_segments if s.get("sentiment")]
    if not labels:
        return {}
    c = Counter(labels)
    total = sum(c.values()) or 1
    return {k: v / total for k, v in c.items()}


def pipeline(audio_path: str) -> Dict[str, Any]:
    # 1) Normalize to WAV (creates *_normalized.wav next to input)
    wav_path = normalize_audio(audio_path)

    # 2) ASR (Whisper)
    asr = ASRService(model_name="base", language=None)
    asr_out = asr.transcribe(wav_path)  # -> {"text","segments","meta"}

    # 3) Diarization (pyannote or mock)
    diar = DiarizationService()
    diar_segments = diar.diarize(wav_path)  # -> List[Dict]

    # 4) Alignment (speaker-attributed transcript segments)
    aligner = AlignmentService(max_gap_merge=0.75)
    aligned = aligner.align(asr_out, diar_segments)  # -> {"speaker_segments":[...]}
    speaker_segments = aligned.get("speaker_segments", [])

    # 5) NLP analytics (match your FastAPI route usage)

    # Sentiment is implemented as class/static method in your API route:
    # SentimentService.analyze_speaker_segments(speaker_segments)
    if speaker_segments:
        speaker_segments = SentimentService.analyze_speaker_segments(speaker_segments)

    # Topic + Summary match your API route too:
    topics = TopicService.classify(asr_out["text"] or "")
    summary = SummaryService.generate_summary(asr_out["text"] or "")

    # Optional: simple overall sentiment view for the evaluator output
    sentiment = {
        "distribution": _sentiment_distribution(speaker_segments),
        "num_labeled_segments": sum(1 for s in speaker_segments if s.get("sentiment")),
    }

    diar_source = "unknown"
    if diar_segments:
        diar_source = "mock" if diar_segments[0].get("source") == "mock" else "pyannote"

    return {
        "transcript": asr_out["text"],
        "asr_segments": asr_out["segments"],
        "diar_segments": diar_segments,
        "speaker_segments": speaker_segments,
        "sentiment": sentiment,
        "topics": topics,
        "summary": summary,
        "meta": {
            "asr": asr_out.get("meta", {}),
            "diarization_source": diar_source,
        },
    }


if __name__ == "__main__":
    audio = "data/DIALOGUE.mp3"
    evaluator = VoiceIQEvaluator(pipeline=pipeline)

    # No ground-truth case (proxy metrics only)
    results = evaluator.evaluate_case({"audio_path": audio})
    print(results)
