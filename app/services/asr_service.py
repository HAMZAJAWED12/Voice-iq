# app/services/asr_service.py
import whisper
from app.utils.logger import logger

# Global model cache (loads once per process)
_MODEL_CACHE = {}  # {model_name: whisper_model}


class ASRService:
    """
    Automatic Speech Recognition (ASR) service using local OpenAI Whisper.

    Design goals:
    - Class-based API for consistency across services.
    - Model caching so Whisper loads only once per process.
    - Predictable output structure for evaluation + orchestration layers.
    """

    def __init__(self, model_name: str = "base", language: str | None = None):
        """
        Args:
            model_name: Whisper model size ('tiny', 'base', 'small', 'medium', 'large').
            language: Optional language hint (e.g., 'en'). If None, Whisper auto-detects.
        """
        self.model_name = model_name
        self.language = language
        self.model = self._load_model(model_name)

    @staticmethod
    def _load_model(model_name: str = "base"):
        """
        Loads and caches the Whisper model to avoid re-loading.
        """
        if model_name not in _MODEL_CACHE:
            logger.info(f"Loading Whisper model: {model_name}")
            _MODEL_CACHE[model_name] = whisper.load_model(model_name)
            logger.info(f"Whisper model '{model_name}' loaded successfully.")
        return _MODEL_CACHE[model_name]

    def transcribe(self, wav_path: str) -> dict:
        """
        Transcribes a 16kHz mono WAV file.

        Returns:
            dict with keys:
              - text: full transcript text (str)
              - segments: list of segment dicts from Whisper
              - meta: metadata dict (model, language, duration)
        """
        logger.info(f"Transcribing audio: {wav_path}")

        result = self.model.transcribe(wav_path, language=self.language)

        text = (result.get("text") or "").strip()
        segments = result.get("segments", [])
        meta = {
            "model": self.model_name,
            "language": result.get("language"),
            "duration": result.get("duration"),
            "num_segments": len(segments),
            "num_chars": len(text),
        }

        logger.info(
            f"Transcription complete — {meta['num_chars']} characters, {meta['num_segments']} segments."
        )

        return {"text": text, "segments": segments, "meta": meta}