"""A failed ffmpeg run leaves no partial WAV behind, and ffmpeg never reads stdin.

subprocess is mocked; real ffmpeg never runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.audio_utils import AudioNormalizationError, AudioNormalizationTimeout, normalize_to_wav


def _partial_output_then(error: Exception):
    """A subprocess.run stand-in that writes part of the output, then fails."""

    def run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"RIFF partial")
        raise error

    return run


@pytest.mark.parametrize(
    ("error", "raised"),
    [
        (subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1.0), AudioNormalizationTimeout),
        (subprocess.CalledProcessError(returncode=1, cmd=["ffmpeg"]), AudioNormalizationError),
    ],
    ids=["timeout", "ffmpeg-error"],
)
def test_a_failed_run_removes_its_partial_output(error, raised, tmp_path):
    out = tmp_path / "normalized.wav"

    with patch("app.utils.audio_utils.subprocess.run", side_effect=_partial_output_then(error)):
        with pytest.raises(raised):
            normalize_to_wav(str(tmp_path / "in.mp3"), str(out))

    assert not out.exists()


def test_a_successful_run_keeps_its_output(tmp_path):
    out = tmp_path / "normalized.wav"

    def run(cmd, **_kwargs):
        Path(cmd[-1]).write_bytes(b"RIFF whole")
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    with patch("app.utils.audio_utils.subprocess.run", side_effect=run):
        assert normalize_to_wav(str(tmp_path / "in.mp3"), str(out)) == str(out)

    assert out.read_bytes() == b"RIFF whole"


def test_ffmpeg_does_not_read_standard_input():
    done = subprocess.CompletedProcess(args=["ffmpeg"], returncode=0, stdout=b"", stderr=b"")
    with patch("app.utils.audio_utils.subprocess.run", return_value=done) as run:
        normalize_to_wav("in.mp3", "out.wav", timeout_sec=5.0)

    assert "-nostdin" in run.call_args.args[0]
