# app/services/emotion_service.py

from typing import List, Dict
from app.utils.logger import logger

try:
    import torch
    # Example: hook in a real SER model here if you want
    # from some_library import load_emotion_model
    # _emotion_model = load_emotion_model(...)
    _HAS_TORCH = True
except Exception:
    _HAS_TORCH = False
    torch = None


class EmotionService:
    """
    Best-effort speech emotion recognition.

    - If you plug a real audio model here, you can score each segment from audio.
    - For now, we provide a simple heuristic fallback based on TEXT + sentiment.
    """

    BASIC_EMOTIONS = ["neutral", "happy", "sad", "angry", "fear"]

    @staticmethod
    def _fallback_from_text_segment(segment: Dict) -> Dict:
        """Map sentiment + keywords to a rough emotion label."""
        text = (segment.get("text") or "").lower()
        sentiment = (segment.get("sentiment") or "neutral").lower()

        if any(w in text for w in ["angry", "mad", "furious", "crap", "stupid", "idiot"]):
            emotion = "angry"
        elif any(w in text for w in ["sorry", "sad", "upset", "bad news"]):
            emotion = "sad"
        elif any(w in text for w in ["great", "awesome", "amazing", "nice holiday", "happy"]):
            emotion = "happy"
        else:
            # map from sentiment
            if sentiment == "positive":
                emotion = "happy"
            elif sentiment == "negative":
                emotion = "sad"
            else:
                emotion = "neutral"

        # simple scores: 1.0 for predicted emotion, 0 for others
        scores = {e: (1.0 if e == emotion else 0.0) for e in EmotionService.BASIC_EMOTIONS}
        return {"emotion": emotion, "emotion_scores": scores}

    @classmethod
    def analyze_speaker_segments(cls, wav_path: str, speaker_segments: List[Dict]) -> List[Dict]:
        """
        Main entry: enrich each speaker_segment with:
          - emotion: str
          - emotion_scores: Dict[str, float]

        For now, uses fallback based on text/sentiment.
        You can later replace the inside with a real audio model using 'wav_path' & timestamps.
        """
        if not speaker_segments:
            return speaker_segments

        if not _HAS_TORCH:
            logger.warning(
                "EmotionService: torch or SER model not available. "
                "Using text-based fallback for emotions."
            )

        enriched = []
        for seg in speaker_segments:
            emo_data = cls._fallback_from_text_segment(seg)
            seg = dict(seg)
            seg["emotion"] = emo_data["emotion"]
            seg["emotion_scores"] = emo_data["emotion_scores"]
            enriched.append(seg)

        return enriched

    @classmethod
    def summarize_emotions(cls, speaker_segments: List[Dict]) -> Dict[str, Dict[str, float]]:
        """
        Returns a simple per-speaker emotion distribution:

        {
          "SPEAKER_01": {"happy": 0.4, "neutral": 0.6, ...},
          "SPEAKER_00": {...}
        }
        """
        counts = {}
        for seg in speaker_segments or []:
            speaker = seg.get("speaker", "UNKNOWN")
            emotion = seg.get("emotion", "neutral")
            if speaker not in counts:
                counts[speaker] = {e: 0 for e in cls.BASIC_EMOTIONS}
            if emotion in counts[speaker]:
                counts[speaker][emotion] += 1

        # normalize
        for spk, emo_counts in counts.items():
            total = sum(emo_counts.values()) or 1.0
            counts[spk] = {e: c / total for e, c in emo_counts.items()}

        return counts