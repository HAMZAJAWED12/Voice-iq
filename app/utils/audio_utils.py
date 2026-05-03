# app/utils/audio_utils.py
import subprocess

from app.utils.logger import logger


class AudioNormalizer:
    """
    Utility class for audio normalization using ffmpeg.

    Converts input audio into:
    - mono
    - fixed sample rate (default 16kHz)
    - WAV format

    Used by:
    - FastAPI /process-audio
    - Offline evaluation scripts
    """

    @staticmethod
    def normalize_to_wav(in_path: str, out_path: str, sr: int = 16000) -> str:
        """
        Normalize any audio file to mono WAV with target sample rate.

        Args:
            in_path: input audio path (.mp3, .wav, .m4a, etc.)
            out_path: output .wav path
            sr: target sample rate (default 16000)

        Returns:
            out_path
        """
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            in_path,
            "-ac",
            "1",
            "-ar",
            str(sr),
            out_path,
        ]

        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
            )
            logger.info(f"Audio normalized to WAV: {out_path}")
        except subprocess.CalledProcessError as e:
            logger.exception("ffmpeg failed during audio normalization")
            raise RuntimeError("Audio normalization failed") from e

        return out_path


# -------------------------------------------------------------------
# Compatibility helpers (IMPORTANT)
# -------------------------------------------------------------------


def normalize_to_wav(in_path: str, out_path: str, sr: int = 16000) -> str:
    """
    Backward-compatible wrapper for existing FastAPI code.
    """
    return AudioNormalizer.normalize_to_wav(in_path, out_path, sr)


def normalize_audio(in_path: str, sr: int = 16000) -> str:
    """
    Convenience helper for scripts/evaluation.

    Automatically generates output WAV path next to input.
    """
    out_path = in_path.rsplit(".", 1)[0] + "_normalized.wav"
    return AudioNormalizer.normalize_to_wav(in_path, out_path, sr)
