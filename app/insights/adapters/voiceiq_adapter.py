from __future__ import annotations

from typing import Any

from app.insights.models.input_models import SessionInput


class VoiceIQInsightAdapter:
    """
    Converts current VoiceIQ orchestrator outputs into Insight Service SessionInput.
    """

    @staticmethod
    def _metric(value: Any, unit: str | None = None) -> dict[str, Any]:
        if isinstance(value, dict):
            return {
                "value": value.get("value"),
                "unit": value.get("unit", unit),
            }
        return {
            "value": value,
            "unit": unit,
        }

    @classmethod
    def _normalize_conversation_stats(cls, conversation_stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
        conversation_stats = conversation_stats or {}
        return {
            "total_duration": cls._metric(conversation_stats.get("total_duration"), "seconds"),
            "total_segments": cls._metric(conversation_stats.get("total_segments")),
            "total_words": cls._metric(conversation_stats.get("total_words")),
            "avg_turn_length": cls._metric(conversation_stats.get("avg_turn_length"), "seconds"),
            "speaker_count": cls._metric(conversation_stats.get("speaker_count")),
            "conversation_start": cls._metric(conversation_stats.get("conversation_start"), "seconds"),
            "conversation_end": cls._metric(conversation_stats.get("conversation_end"), "seconds"),
        }

    @classmethod
    def _normalize_speaker_stats(cls, speaker_stats: dict[str, Any]) -> dict[str, dict[str, Any]]:
        speaker_stats = speaker_stats or {}
        normalized: dict[str, dict[str, Any]] = {}

        for speaker, stats in speaker_stats.items():
            if isinstance(stats, dict):
                normalized[str(speaker)] = stats
            else:
                normalized[str(speaker)] = {"value": stats}

        return normalized

    @staticmethod
    def from_orchestrator(
        *,
        session_id: str,
        asr_meta: dict[str, Any] | None,
        speaker_segments: list[dict[str, Any]] | None,
        speaker_stats: dict[str, Any] | None,
        conversation_stats: dict[str, Any] | None,
        warnings: list[str] | None,
    ) -> SessionInput:
        asr_meta = asr_meta or {}
        speaker_segments = speaker_segments or []
        speaker_stats = speaker_stats or {}
        conversation_stats = conversation_stats or {}
        warnings = warnings or []

        utterances: list[dict[str, Any]] = []

        for idx, seg in enumerate(speaker_segments):
            text = (seg.get("text") or "").strip()

            sentiment_payload = None
            if seg.get("sentiment") is not None or seg.get("sentiment_score") is not None:
                sentiment_payload = {
                    "label": seg.get("sentiment"),
                    "score": seg.get("sentiment_score"),
                }

            emotion_payload = None
            emotion_scores = seg.get("emotion_scores") or {}
            if isinstance(emotion_scores, dict) and emotion_scores:
                emotion_payload = {
                    "values": emotion_scores,
                }

            utterances.append(
                {
                    "id": str(seg.get("id") or f"utt_{idx + 1}"),
                    "speaker": str(seg.get("speaker") or "UNKNOWN"),
                    "start": float(seg.get("start", 0.0) or 0.0),
                    "end": float(seg.get("end", 0.0) or 0.0),
                    "text": text,
                    "word_count": len(text.split()),
                    "sentiment": sentiment_payload,
                    "emotion": emotion_payload,
                    "overlap": bool(seg.get("overlap", False)),
                    "confidence": seg.get("confidence"),
                    "diarization_confidence": seg.get("diarization_confidence"),
                }
            )

        speakers = sorted({u["speaker"] for u in utterances if u["speaker"]})

        duration_sec = (
            asr_meta.get("duration")
            or conversation_stats.get("total_duration")
            or conversation_stats.get("total_duration_sec")
            or 0.0
        )

        payload = {
            "session_id": session_id,
            "duration_sec": float(duration_sec),
            "speakers": speakers,
            "utterances": utterances,
            "meta": {
                "source": "voiceiq_pipeline",
                "language": asr_meta.get("language"),
                "pipeline_version": "voiceiq-ai/0.1.0",
            },
            "warnings": warnings,
            "speaker_stats": VoiceIQInsightAdapter._normalize_speaker_stats(speaker_stats),
            "conversation_stats": VoiceIQInsightAdapter._normalize_conversation_stats(conversation_stats),
        }

        return SessionInput.model_validate(payload)
