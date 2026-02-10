# app/services/summary_service.py

from transformers import pipeline
from app.utils.logger import logger

_summarizer = None


def _get_summarizer():
    """
    Lazily load and cache the summarization pipeline.
    """
    global _summarizer
    if _summarizer is None:
        logger.info("Loading summarization model (distilbart-cnn-12-6)...")
        _summarizer = pipeline(
            "summarization",
            model="sshleifer/distilbart-cnn-12-6",
            device="cpu",
        )
        logger.info("Summarization model loaded.")
    return _summarizer


class SummaryService:
    @staticmethod
    def summarize(text: str, max_chars: int = 4000) -> str:
        """
        Summarize the full transcript into a short, readable summary.
        """
        if not text:
            return ""

        text = text.strip()
        if not text:
            return ""

        # avoid extremely long inputs
        if len(text) > max_chars:
            text = text[:max_chars]

        summarizer = _get_summarizer()
        out = summarizer(
            text,
            max_length=180,
            min_length=60,
            do_sample=False,
        )
        return out[0]["summary_text"].strip()

    @staticmethod
    def generate_summary(text: str, max_chars: int = 4000) -> str:
        """
        Alias for summarize, for nicer naming in other modules.
        """
        return SummaryService.summarize(text, max_chars=max_chars)