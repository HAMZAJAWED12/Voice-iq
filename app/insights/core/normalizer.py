from __future__ import annotations

import re
from typing import Any

from app.insights.models.input_models import SessionInput


class InsightNormalizer:
    DEFAULT_SPEAKER = "UNKNOWN"
    DEFAULT_LANGUAGE = "unknown"

    @classmethod
    def normalize_to_session_input(cls, payload: dict[str, Any]) -> SessionInput:
        normalized_utterances = cls._normalize_utterances(payload.get("utterances", []))
        inferred_speakers = cls._infer_speakers(normalized_utterances)
        inferred_duration = cls._infer_duration(normalized_utterances)

        raw_meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        raw_warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []

        normalized_payload = {
            "session_id": cls._normalize_session_id(payload.get("session_id")),
            "duration_sec": cls._normalize_optional_non_negative_float(
                payload.get("duration_sec"),
                fallback=inferred_duration,
            ),
            "speakers": cls._normalize_speakers(payload.get("speakers"), fallback=inferred_speakers),
            "utterances": normalized_utterances,
            "meta": cls._normalize_meta(raw_meta),
            "warnings": [str(w) for w in raw_warnings if w is not None],
            "speaker_stats": payload.get("speaker_stats", {}) if isinstance(payload.get("speaker_stats"), dict) else {},
            "conversation_stats": payload.get("conversation_stats", {})
            if isinstance(payload.get("conversation_stats"), dict)
            else {},
        }

        return SessionInput.model_validate(normalized_payload)

    @staticmethod
    def _normalize_session_id(value: Any) -> str:
        if value is None:
            return "unknown_session"
        return str(value).strip() or "unknown_session"

    @classmethod
    def _normalize_speakers(cls, value: Any, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            speakers: list[str] = []
            for item in value:
                speaker = cls._normalize_speaker(item)
                if speaker not in speakers:
                    speakers.append(speaker)
            return speakers or fallback
        return fallback

    @classmethod
    def _normalize_meta(cls, meta: dict[str, Any]) -> dict[str, Any]:
        return {
            "source": cls._normalize_optional_string(meta.get("source")),
            "language": cls._normalize_optional_string(meta.get("language")) or cls.DEFAULT_LANGUAGE,
            "created_at": cls._normalize_optional_string(meta.get("created_at")),
            "pipeline_version": cls._normalize_optional_string(meta.get("pipeline_version")),
        }

    @classmethod
    def _normalize_utterances(cls, utterances: Any) -> list[dict[str, Any]]:
        if not isinstance(utterances, list):
            return []

        normalized: list[dict[str, Any]] = []
        for idx, utt in enumerate(utterances):
            if isinstance(utt, dict):
                normalized.append(cls._normalize_single_utterance(idx, utt))

        normalized.sort(key=lambda x: (x["start"], x["end"], x["id"]))
        return normalized

    @classmethod
    def _normalize_single_utterance(cls, idx: int, utt: dict[str, Any]) -> dict[str, Any]:
        start = cls._normalize_non_negative_float(utt.get("start"), default=0.0)
        end = cls._normalize_non_negative_float(utt.get("end"), default=start)
        if end < start:
            end = start

        text = cls._normalize_text(utt.get("text"))
        word_count = cls._normalize_word_count(utt.get("word_count"), text)

        return {
            "id": cls._normalize_utterance_id(utt.get("id"), idx),
            "speaker": cls._normalize_speaker(utt.get("speaker")),
            "start": round(start, 3),
            "end": round(end, 3),
            "text": text,
            "word_count": word_count,
            "sentiment": cls._normalize_sentiment(utt.get("sentiment")),
            "emotion": cls._normalize_emotion(utt.get("emotion")),
            "overlap": cls._normalize_bool(utt.get("overlap"), default=False),
            "confidence": cls._normalize_probability_or_none(utt.get("confidence")),
            "diarization_confidence": cls._normalize_probability_or_none(utt.get("diarization_confidence")),
        }

    @staticmethod
    def _normalize_utterance_id(value: Any, idx: int) -> str:
        if value is None:
            return f"utt_{idx + 1}"
        value = str(value).strip()
        return value or f"utt_{idx + 1}"

    @classmethod
    def _normalize_speaker(cls, value: Any) -> str:
        if value is None:
            return cls.DEFAULT_SPEAKER

        speaker = str(value).strip()
        if not speaker:
            return cls.DEFAULT_SPEAKER

        speaker = speaker.upper().replace("-", "_").replace(" ", "_")
        speaker = re.sub(r"_+", "_", speaker)
        return speaker

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).replace("\n", " ").replace("\t", " ")
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _normalize_word_count(cls, value: Any, text: str) -> int:
        if isinstance(value, int) and value >= 0:
            return value
        return len(text.split()) if text else 0

    @classmethod
    def _normalize_sentiment(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        label = value.get("label")
        score = value.get("score")

        label = str(label).lower().strip() if label is not None else None
        if label not in {"positive", "neutral", "negative"}:
            label = None

        score = cls._normalize_probability_or_none(score)

        if label is None and score is None:
            return None

        return {"label": label, "score": score}

    @classmethod
    def _normalize_emotion(cls, value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None

        values = value.get("values")
        if not isinstance(values, dict):
            return None

        cleaned: dict[str, float] = {}
        for emotion_name, emotion_value in values.items():
            if emotion_name is None:
                continue
            prob = cls._normalize_probability_or_none(emotion_value)
            if prob is not None:
                cleaned[str(emotion_name)] = prob

        if not cleaned:
            return None

        return {"values": cleaned}

    @classmethod
    def _infer_speakers(cls, utterances: list[dict[str, Any]]) -> list[str]:
        speakers: list[str] = []
        for utt in utterances:
            speaker = utt["speaker"]
            if speaker not in speakers:
                speakers.append(speaker)
        return speakers

    @staticmethod
    def _infer_duration(utterances: list[dict[str, Any]]) -> float:
        if not utterances:
            return 0.0
        return round(max(utt["end"] for utt in utterances), 3)

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    @staticmethod
    def _normalize_non_negative_float(value: Any, default: float = 0.0) -> float:
        try:
            value_f = float(value)
            return value_f if value_f >= 0.0 else default
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_optional_non_negative_float(cls, value: Any, fallback: float) -> float:
        try:
            value_f = float(value)
            return value_f if value_f >= 0.0 else fallback
        except (TypeError, ValueError):
            return fallback

    @staticmethod
    def _normalize_probability_or_none(value: Any) -> float | None:
        try:
            value_f = float(value)
            if 0.0 <= value_f <= 1.0:
                return round(value_f, 4)
            return None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value

        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False

        if isinstance(value, int | float):
            return bool(value)

        return default
