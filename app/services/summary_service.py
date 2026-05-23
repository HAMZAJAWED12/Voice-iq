# app/services/summary_service.py

import threading

from transformers import pipeline

from app.utils.logger import logger

_summarizer = None
# Guards the cold-init path so concurrent first requests do not race to
# load the summarisation pipeline twice (double HF download + double
# weights in RAM). Hot calls bypass the lock via double-check.
_summarizer_lock = threading.Lock()


def _get_summarizer():
    """Lazily load and cache the summarization pipeline (thread-safe)."""
    global _summarizer
    # Fast path: cache hit, no lock acquisition.
    if _summarizer is not None:
        return _summarizer
    # Slow path: serialise on the load lock and re-check the cache.
    with _summarizer_lock:
        if _summarizer is not None:
            return _summarizer
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
