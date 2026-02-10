# app/services/topic_service.py

from transformers import pipeline
from functools import lru_cache
from typing import Dict, List
from app.utils.logger import logger


class TopicService:

    # High-level taxonomy
    TOPIC_LABELS = [
        "sales",
        "support",
        "technical issue",
        "billing",
        "account management",
        "personal development",
        "health & wellness",
        "product inquiry",
        "customer success",
        "complaint",
        "general conversation",
    ]

    @staticmethod
    @lru_cache()
    def _load_model():
        """
        Zero-shot classifier (fast + safe).
        Cached for performance.
        """
        logger.info("Loading zero-shot topic model (bart-large-mnli)...")
        return pipeline(
            "zero-shot-classification",
            model="facebook/bart-large-mnli",
        )

    @classmethod
    def classify(cls, text: str) -> Dict:
        """
        Perform zero-shot topic detection on the entire transcript.

        Returns:
            {
              "topic": str,
              "confidence": float
            }
        """
        if not text or not text.strip():
            return {
                "topic": "unknown",
                "confidence": 0.0,
            }

        model = cls._load_model()

        result = model(
            text[:512],                 # truncate long transcripts safely
            candidate_labels=cls.TOPIC_LABELS,
            multi_label=False,
        )

        topic = result["labels"][0]
        score = float(result["scores"][0])

        return {
            "topic": topic,
            "confidence": score,
        }

    @classmethod
    def classify_per_speaker(cls, segments: List[Dict]) -> List[Dict]:
        """
        Optional speaker-level topic tagging.
        Not used in main API yet, but ready for future.
        """
        updated = []
        for seg in segments:
            t = cls.classify(seg.get("text", ""))
            updated.append({
                **seg,
                "topic": t["topic"],
                "topic_confidence": t["confidence"],
            })
        return updated