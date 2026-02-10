# app/evaluation/evaluator.py
from __future__ import annotations

import time
from collections import Counter
from typing import Callable, Dict, Any, List

# Optional: if later you add ground truth WER/CER
try:
    from jiwer import wer, cer
    _HAS_JIWER = True
except ImportError:
    wer = cer = None
    _HAS_JIWER = False


class VoiceIQEvaluator:
    """
    Development-only evaluation harness for VoiceIQ-AI.

    Key behavior:
    - Always computes proxy metrics (no ground truth needed)
    - Optionally computes supervised metrics if ground truth is provided
    """

    def __init__(self, pipeline: Callable[[str], Dict[str, Any]]):
        self.pipeline = pipeline

    # ---------------------------
    # Proxy metrics (no GT needed)
    # ---------------------------
    @staticmethod
    def _asr_proxy(asr_segments: List[Dict], transcript: str) -> Dict[str, Any]:
        # duration from whisper segments if present
        duration = 0.0
        if asr_segments:
            duration = float(asr_segments[-1].get("end", 0.0))

        total_words = len((transcript or "").split())
        num_segments = len(asr_segments)

        words_per_sec = total_words / max(duration, 1e-6)
        avg_seg_sec = (
            sum((float(s["end"]) - float(s["start"])) for s in asr_segments) / max(num_segments, 1)
            if asr_segments else 0.0
        )

        # hallucination-ish signal: repeated token ratio
        toks = (transcript or "").lower().split()
        rep_ratio = 0.0
        if toks:
            rep_ratio = 1.0 - (len(set(toks)) / max(len(toks), 1))

        return {
            "duration_sec": round(duration, 3),
            "total_words": total_words,
            "num_segments": num_segments,
            "words_per_sec": round(words_per_sec, 3),
            "avg_segment_sec": round(avg_seg_sec, 3),
            "repetition_ratio": round(rep_ratio, 3),
        }

    @staticmethod
    def _diar_proxy(diar_segments: List[Dict]) -> Dict[str, Any]:
        if not diar_segments:
            return {"num_speakers": 0, "num_segments": 0, "avg_segment_sec": 0.0}

        speakers = {s.get("speaker") for s in diar_segments if s.get("speaker")}
        lengths = [(float(s["end"]) - float(s["start"])) for s in diar_segments if "start" in s and "end" in s]
        avg_len = sum(lengths) / max(len(lengths), 1) if lengths else 0.0

        return {
            "num_speakers": len(speakers),
            "num_segments": len(diar_segments),
            "avg_segment_sec": round(avg_len, 3),
            "source": diar_segments[0].get("source", "unknown"),
        }

    @staticmethod
    def _alignment_health(asr_segments: List[Dict], speaker_segments: List[Dict]) -> Dict[str, Any]:
        total_asr = len(asr_segments) if asr_segments else 0
        total_aligned = len(speaker_segments) if speaker_segments else 0
        coverage = total_aligned / max(total_asr, 1) if total_asr else 0.0

        return {
            "asr_segments": total_asr,
            "speaker_segments": total_aligned,
            "coverage": round(coverage, 3),
            "unassigned_asr_segments_est": max(total_asr - total_aligned, 0),
        }

    @staticmethod
    def _sentiment_proxy(sentiment_obj: Any) -> Dict[str, Any]:
        """
        In our pipeline we return:
        sentiment = { "distribution": {...}, "num_labeled_segments": N }
        """
        if isinstance(sentiment_obj, dict):
            return {
                "distribution": sentiment_obj.get("distribution", {}) or {},
                "num_labeled_segments": sentiment_obj.get("num_labeled_segments", 0),
            }
        return {"distribution": {}, "num_labeled_segments": 0}

    @staticmethod
    def _summary_proxy(transcript: str, summary: str | None) -> Dict[str, Any]:
        t_words = len((transcript or "").split())
        s_words = len((summary or "").split())
        compression = s_words / max(t_words, 1) if t_words else 0.0
        return {
            "summary_words": s_words,
            "transcript_words": t_words,
            "compression_ratio": round(compression, 4),
        }

    # ---------------------------
    # Supervised metrics (optional)
    # ---------------------------
    def _asr_supervised(self, gt_text: str, pred_text: str) -> Dict[str, Any]:
        if not _HAS_JIWER:
            return {"wer": None, "cer": None, "note": "jiwer not installed"}
        return {"wer": float(wer(gt_text, pred_text)), "cer": float(cer(gt_text, pred_text))}

    # ---------------------------
    # Full evaluation case
    # ---------------------------
    def evaluate_case(self, case: Dict[str, Any]) -> Dict[str, Any]:
        """
        case must contain:
          - audio_path

        optional ground truth:
          - transcript_gt_text  (string) OR transcript_gt_path (path to .txt)
        """
        t0 = time.time()
        output = self.pipeline(case["audio_path"])
        latency = time.time() - t0

        transcript = output.get("transcript", "") or ""
        asr_segments = output.get("asr_segments", []) or []
        diar_segments = output.get("diar_segments", []) or []
        speaker_segments = output.get("speaker_segments", []) or []
        topics = output.get("topics", None)
        summary = output.get("summary", None)
        sentiment = output.get("sentiment", None)

        results: Dict[str, Any] = {
            "latency_sec": round(latency, 3),
            "proxy": {
                "asr": self._asr_proxy(asr_segments, transcript),
                "diarization": self._diar_proxy(diar_segments),
                "alignment": self._alignment_health(asr_segments, speaker_segments),
                "sentiment": self._sentiment_proxy(sentiment),
                "summary": self._summary_proxy(transcript, summary),
            },
        }

        # Optional: basic topic debug
        if topics is not None:
            results["proxy"]["topics"] = {"raw": topics}

        # Optional supervised ASR metrics if user provided GT text
        gt_text = case.get("transcript_gt_text")
        if gt_text:
            results["supervised"] = {"asr": self._asr_supervised(gt_text, transcript)}

        return results
