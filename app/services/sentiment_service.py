# app/services/sentiment_service.py

from __future__ import annotations

from typing import Dict, Any, List
import re

from transformers import (
    AutoTokenizer,
    AutoModelForSequenceClassification,
    pipeline,
)

from app.utils.logger import logger


class SentimentService:
    """
    Enhanced + stable sentiment engine.

    Improvements:
    - Stronger model: cardiffnlp/twitter-roberta-base-sentiment-latest
    - Much better text cleaning
    - Short-text heuristics improved
    - Confidence thresholds improved
    - Full compatibility with process_audio.py
    """

    _pipeline = None
    _model_name = "cardiffnlp/twitter-roberta-base-sentiment-latest"

    # Heuristics
    _min_words_for_strong_sentiment = 4
    _low_confidence_threshold = 0.65
    _very_low_confidence = 0.50

    @classmethod
    def _load_pipeline(cls):
        """Lazy-load HuggingFace sentiment pipeline."""
        if cls._pipeline is not None:
            return cls._pipeline

        try:
            logger.info("Loading HuggingFace sentiment model...")

            tokenizer = AutoTokenizer.from_pretrained(cls._model_name)
            model = AutoModelForSequenceClassification.from_pretrained(cls._model_name)

            cls._pipeline = pipeline(
                "sentiment-analysis",
                model=model,
                tokenizer=tokenizer,
                device=-1,   # CPU
            )
            logger.info("Sentiment model loaded successfully.")

        except Exception as e:
            logger.error(f"Failed to load sentiment model: {e}")
            cls._pipeline = None

        return cls._pipeline

    # ----------------------------------------------------------------------
    # CLEANER TEXT PREPROCESSING
    # ----------------------------------------------------------------------
    @staticmethod
    def _clean_text(text: str) -> str:
        """Improved text cleaning for better model performance."""
        if not text:
            return ""

        text = text.strip()

        # Remove weird chars
        text = re.sub(r"[^A-Za-z0-9\s.,!?;:'\"-]", " ", text)

        # Remove URLs
        text = re.sub(r"https?://\S+|www\.\S+", "", text)

        # Remove repeated characters (soooo -> soo)
        text = re.sub(r"(.)\1{2,}", r"\1\1", text)

        # Remove filler noises
        text = re.sub(r"\b(uh|um|hmm|erm|ahh|mm)\b", "", text, flags=re.I)

        # Normalize spaces
        text = re.sub(r"\s+", " ", text)

        return text.strip()

    # ----------------------------------------------------------------------
    # MAIN SENTIMENT ENTRY POINT
    # ----------------------------------------------------------------------
    @classmethod
    def analyze_text(cls, text: str) -> Dict[str, Any]:
        """
        Main public API.
        Returns {label: positive/neutral/negative, score: confidence}
        """

        if not text or not text.strip():
            return {"label": "neutral", "score": 0.0}

        clean = cls._clean_text(text)
        if not clean:
            return {"label": "neutral", "score": 0.0}

        pip = cls._load_pipeline()
        if pip is None:
            return {"label": "neutral", "score": 0.0}

        # -------------------------
        # SHORT TEXT LOGIC
        # -------------------------
        words = clean.split()
        if len(words) < cls._min_words_for_strong_sentiment:
            try:
                res = pip(clean[:512])[0]
            except Exception as e:
                logger.error(f"Sentiment model failure (short text): {e}")
                return {"label": "neutral", "score": 0.0}

            raw_label = res["label"].lower()
            score = float(res["score"])

            # short text → force neutral unless very confident
            if score < 0.80:
                return {"label": "neutral", "score": score}

            mapped = cls._map_label(raw_label)
            return {"label": mapped, "score": score}

        # -------------------------
        # NORMAL LENGTH TEXT
        # -------------------------
        try:
            res = pip(clean[:512])[0]
        except Exception as e:
            logger.error(f"Sentiment model failure: {e}")
            return {"label": "neutral", "score": 0.0}

        raw_label = res["label"].lower()
        score = float(res["score"])
        mapped = cls._map_label(raw_label)

        # Confidence rules
        if score < cls._very_low_confidence:
            return {"label": "neutral", "score": score}

        if mapped in ("positive", "negative") and score < cls._low_confidence_threshold:
            return {"label": "neutral", "score": score}

        return {"label": mapped, "score": score}

    # ----------------------------------------------------------------------
    # BATCH API REQUIRED BY process_audio.py
    # ----------------------------------------------------------------------
    @classmethod
    def analyze_speaker_segments(cls, segments):
        """
        Required function — process_audio.py uses this.
        Enhances each segment with:
            - sentiment
            - sentiment_score
            - keywords
        """

        from app.services.keyword_service import KeywordService

        output = []

        for seg in segments:
            text = seg.get("text", "") or ""

            sentiment = cls.analyze_text(text)
            keywords = KeywordService.extract_keywords(text, top_k=5)

            enriched = dict(seg)
            enriched["sentiment"] = sentiment["label"]
            enriched["sentiment_score"] = sentiment["score"]
            enriched["keywords"] = keywords

            output.append(enriched)

        return output

    # ----------------------------------------------------------------------
    # LABEL MAPPING
    # ----------------------------------------------------------------------
    @staticmethod
    def _map_label(label_raw: str) -> str:
        """Normalize labels from any HF model."""

        label_raw = label_raw.lower()

        if "pos" in label_raw:
            return "positive"
        if "neg" in label_raw:
            return "negative"

        return "neutral"