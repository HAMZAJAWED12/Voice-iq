"""Tests for the ffmpeg normalisation wrapper.

Lives under `app/insights/tests/` (rather than alongside `app/utils/`) so
the existing GitHub Actions workflow picks it up — CI scopes pytest to
that directory. All subprocess calls are mocked; we never invoke real
ffmpeg in the test suite.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from app.utils.audio_utils import (
    AudioNormalizationError,
    AudioNormalizationTimeout,
    normalize_to_wav,
)


def _completed_process() -> subprocess.CompletedProcess:
    """Stand-in for a healthy ffmpeg exit."""
    return subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout=b"", stderr=b"")


def test_success_returns_out_path_and_forwards_timeout() -> None:
    with patch("app.utils.audio_utils.subprocess.run", return_value=_completed_process()) as run:
        result = normalize_to_wav("in.mp3", "out.wav", timeout_sec=5.0)
    assert result == "out.wav"
    # The wall-clock timeout MUST be forwarded to subprocess.run, otherwise
    # the entire defence is bypassed.
    assert run.call_args.kwargs["timeout"] == 5.0
    assert run.call_args.kwargs["check"] is True


def test_timeout_raises_audio_normalization_timeout() -> None:
    err = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1.0)
    with patch("app.utils.audio_utils.subprocess.run", side_effect=err):
        with pytest.raises(AudioNormalizationTimeout) as exc:
            normalize_to_wav("in.mp3", "out.wav", timeout_sec=1.0)
    # The detail message must mention the timeout so logs are useful.
    assert "1.0" in str(exc.value)
    # Subclass of AudioNormalizationError so generic except clauses still catch.
    assert isinstance(exc.value, AudioNormalizationError)


def test_nonzero_exit_raises_audio_normalization_error_not_timeout() -> None:
    err = subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"], stderr=b"oops")
    with patch("app.utils.audio_utils.subprocess.run", side_effect=err):
        with pytest.raises(AudioNormalizationError) as exc:
            normalize_to_wav("in.mp3", "out.wav", timeout_sec=5.0)
    # MUST NOT be misclassified as a timeout — the HTTP layer routes them
    # to different status codes (400 vs 422).
    assert not isinstance(exc.value, AudioNormalizationTimeout)


def test_explicit_timeout_overrides_settings_default() -> None:
    """A caller-supplied timeout must win over the settings default."""
    with patch("app.utils.audio_utils.subprocess.run", return_value=_completed_process()) as run:
        normalize_to_wav("in.mp3", "out.wav", timeout_sec=12.5)
    assert run.call_args.kwargs["timeout"] == 12.5


def test_default_timeout_comes_from_settings() -> None:
    """When timeout_sec is omitted, the value is sourced from InsightSettings."""
    from app.insights.config.settings import get_settings

    expected = get_settings().ffmpeg_timeout_sec
    with patch("app.utils.audio_utils.subprocess.run", return_value=_completed_process()) as run:
        normalize_to_wav("in.mp3", "out.wav")
    assert run.call_args.kwargs["timeout"] == expected
