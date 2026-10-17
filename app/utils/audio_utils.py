# app/utils/audio_utils.py
import math
import subprocess
import wave
from pathlib import Path

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


class AudioTooLong(AudioNormalizationError):
    """The audio is longer than `InsightSettings.max_audio_seconds`.

    Rejected, never truncated. The HTTP layer surfaces this as 413 with the
    warning code AUDIO_TOO_LONG.
    """


def _probe_seconds(in_path: str, timeout: float) -> float | None:
    """The container's duration in seconds per ffprobe, or None if unknown.

    A missing ffprobe, a failed probe or an unreadable duration gives None;
    the ffmpeg `-t` hard stop and the decoded-length check still apply.
    """
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", in_path]
    try:
        done = subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AudioNormalizationTimeout(f"ffprobe exceeded the {timeout:.1f}s timeout") from exc
    except (OSError, subprocess.CalledProcessError):
        return None
    try:
        seconds = float((done.stdout or b"").decode("ascii", "replace").strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0 else None


def _wav_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


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
        max_seconds: float | None = None,
    ) -> str:
        """Normalise input audio to mono WAV at the target sample rate.

        Audio longer than `max_seconds` (default
        `InsightSettings.max_audio_seconds`) is rejected with `AudioTooLong`:
        ffprobe checks the duration before decoding, ffmpeg stops one second
        past the limit, and a decoded file longer than the limit is deleted
        and rejected. Nothing is truncated.

        Args:
            in_path: Input audio path (.mp3 / .wav / .m4a / .flac / ...).
            out_path: Destination .wav path.
            sr: Target sample rate (default 16000).
            timeout_sec: Optional override for the wall-clock timeout
                (seconds). When None, the value from
                `InsightSettings.ffmpeg_timeout_sec` is used.
            max_seconds: Optional override for the duration limit.

        Returns:
            `out_path` on success.

        Raises:
            AudioTooLong: the audio is longer than the duration limit.
            AudioNormalizationTimeout: ffprobe or ffmpeg exceeded the
                wall-clock cap.
            AudioNormalizationError: ffmpeg exited non-zero for any other
                reason (corrupt header, unsupported codec, etc.).
        """
        settings = get_settings()
        timeout = timeout_sec if timeout_sec is not None else settings.ffmpeg_timeout_sec
        limit = max_seconds if max_seconds is not None else settings.max_audio_seconds

        probed = _probe_seconds(in_path, timeout)
        if probed is not None and probed > limit:
            logger.warning("audio rejected: %.0f s is over the %.0f s limit", probed, limit)
            raise AudioTooLong(f"Audio is longer than {limit:.0f} seconds.")

        cmd = [
            "ffmpeg",
            "-nostdin",  # never wait on standard input
            "-y",
            "-i",
            in_path,
            "-ac",
            "1",
            "-ar",
            str(sr),
            "-t",  # hard stop one second past the limit; a file that reaches it is rejected below
            str(int(limit) + 1),
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
            Path(out_path).unlink(missing_ok=True)  # no partial WAV left behind
            logger.warning(
                "ffmpeg timed out after %.1fs while normalising %s",
                timeout,
                in_path,
            )
            raise AudioNormalizationTimeout(f"ffmpeg exceeded the {timeout:.1f}s timeout for {in_path}") from exc
        except subprocess.CalledProcessError as exc:
            Path(out_path).unlink(missing_ok=True)  # no partial WAV left behind
            logger.exception("ffmpeg failed during audio normalization")
            raise AudioNormalizationError("Audio normalization failed") from exc

        try:
            decoded = _wav_seconds(out_path)
        except (OSError, EOFError, wave.Error) as exc:
            Path(out_path).unlink(missing_ok=True)
            raise AudioNormalizationError("Audio normalization produced no readable output") from exc
        if decoded > limit:
            Path(out_path).unlink(missing_ok=True)
            logger.warning("audio rejected: decoded length passed the %.0f s limit", limit)
            raise AudioTooLong(f"Audio is longer than {limit:.0f} seconds.")
        return out_path


# -------------------------------------------------------------------
# Compatibility helpers (IMPORTANT)
# -------------------------------------------------------------------


def normalize_to_wav(
    in_path: str,
    out_path: str,
    sr: int = 16000,
    timeout_sec: float | None = None,
    max_seconds: float | None = None,
) -> str:
    """Backward-compatible wrapper for existing FastAPI / orchestrator code."""
    return AudioNormalizer.normalize_to_wav(in_path, out_path, sr, timeout_sec, max_seconds)


def normalize_audio(in_path: str, sr: int = 16000) -> str:
    """Convenience helper for scripts / evaluation runs.

    Generates the output WAV path next to the input file.
    """
    out_path = in_path.rsplit(".", 1)[0] + "_normalized.wav"
    return AudioNormalizer.normalize_to_wav(in_path, out_path, sr)
