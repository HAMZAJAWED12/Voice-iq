# app/utils/audio_utils.py
import subprocess

from app.insights.config.settings import get_settings
from app.utils.logger import logger


class AudioNormalizationError(RuntimeError):
    """Base class for failures during ffmpeg audio normalisation.

    Distinct from generic `RuntimeError` so callers can catch the audio
    family of failures without swallowing unrelated errors.
    """


class AudioNormalizationTimeout(AudioNormalizationError):
    """The ffmpeg normalisation process did not finish in time.

    Almost always indicates corrupt or pathologically large input audio;
    honest audio finishes within seconds on commodity hardware. The HTTP
    layer surfaces this as 422 (Unprocessable Entity) — different from a
    generic `AudioNormalizationError`, which surfaces as 400 (the input
    was syntactically a file but ffmpeg couldn't decode it).
    """


class AudioNormalizer:
    """ffmpeg-backed audio normalisation: mono, 16 kHz, WAV.

    Wraps `ffmpeg` with a wall-clock timeout. On timeout, `subprocess.run`
    kills the child process and `AudioNormalizationTimeout` is raised so
    the HTTP layer can return 422 rather than waiting on a stuck worker.
    """

    @staticmethod
    def normalize_to_wav(
        in_path: str,
        out_path: str,
        sr: int = 16000,
        timeout_sec: float | None = None,
    ) -> str:
        """Normalise input audio to mono WAV at the target sample rate.

        Args:
            in_path: Input audio path (.mp3 / .wav / .m4a / .flac / ...).
            out_path: Destination .wav path.
            sr: Target sample rate (default 16000).
            timeout_sec: Optional override for the wall-clock timeout
                (seconds). When None, the value from
                `InsightSettings.ffmpeg_timeout_sec` is used.

        Returns:
            `out_path` on success.

        Raises:
            AudioNormalizationTimeout: ffmpeg exceeded the wall-clock cap.
            AudioNormalizationError: ffmpeg exited non-zero for any other
                reason (corrupt header, unsupported codec, etc.).
        """
        timeout = timeout_sec if timeout_sec is not None else get_settings().ffmpeg_timeout_sec

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
                timeout=timeout,
            )
            logger.info(f"Audio normalized to WAV: {out_path}")
        except subprocess.TimeoutExpired as exc:
            logger.warning(
                "ffmpeg timed out after %.1fs while normalising %s",
                timeout,
                in_path,
            )
            raise AudioNormalizationTimeout(f"ffmpeg exceeded the {timeout:.1f}s timeout for {in_path}") from exc
        except subprocess.CalledProcessError as exc:
            logger.exception("ffmpeg failed during audio normalization")
            raise AudioNormalizationError("Audio normalization failed") from exc

        return out_path


# -------------------------------------------------------------------
# Compatibility helpers (IMPORTANT)
# -------------------------------------------------------------------


def normalize_to_wav(
    in_path: str,
    out_path: str,
    sr: int = 16000,
    timeout_sec: float | None = None,
) -> str:
    """Backward-compatible wrapper for existing FastAPI / orchestrator code."""
    return AudioNormalizer.normalize_to_wav(in_path, out_path, sr, timeout_sec)


def normalize_audio(in_path: str, sr: int = 16000) -> str:
    """Convenience helper for scripts / evaluation runs.

    Generates the output WAV path next to the input file.
    """
    out_path = in_path.rsplit(".", 1)[0] + "_normalized.wav"
    return AudioNormalizer.normalize_to_wav(in_path, out_path, sr)
