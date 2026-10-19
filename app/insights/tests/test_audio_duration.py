"""Audio longer than VOICEIQ_MAX_AUDIO_SECONDS is rejected, never truncated.

ffprobe reads the duration before anything is decoded; ffmpeg also gets a
`-t` hard stop one second past the limit, and a decoded file that reaches it
is rejected. subprocess is mocked: real ffmpeg and ffprobe never run. A WAV
header with a 1 Hz frame rate stands in for long audio without writing
gigabytes.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from app.insights.config.settings import InsightSettings
from app.utils.audio_utils import AudioTooLong, normalize_to_wav


def _write_wav(path: str, seconds: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1)
        w.writeframes(b"\0\0" * seconds)


def _fake_run(probe_output: str | Exception, decoded_seconds: int, calls: list):
    def run(cmd, **_kwargs):
        calls.append(cmd)
        if cmd[0] == "ffprobe":
            if isinstance(probe_output, Exception):
                raise probe_output
            return subprocess.CompletedProcess(cmd, 0, stdout=probe_output.encode(), stderr=b"")
        _write_wav(cmd[-1], decoded_seconds)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    return run


def _ffmpeg_calls(calls: list) -> list:
    return [c for c in calls if c[0] == "ffmpeg"]


def test_audio_over_the_limit_is_rejected_before_decoding(tmp_path):
    calls: list = []
    out = tmp_path / "normalized.wav"

    with patch("app.utils.audio_utils.subprocess.run", side_effect=_fake_run("7300.5\n", 0, calls)):
        with pytest.raises(AudioTooLong):
            normalize_to_wav(str(tmp_path / "in.mp3"), str(out), max_seconds=7200)

    assert _ffmpeg_calls(calls) == []
    assert not out.exists()


def test_audio_within_the_limit_is_decoded_with_a_hard_stop(tmp_path):
    calls: list = []
    out = tmp_path / "normalized.wav"

    with patch("app.utils.audio_utils.subprocess.run", side_effect=_fake_run("100.0\n", 100, calls)):
        assert normalize_to_wav(str(tmp_path / "in.mp3"), str(out), max_seconds=7200) == str(out)

    (ffmpeg,) = _ffmpeg_calls(calls)
    assert ffmpeg[ffmpeg.index("-t") + 1] == "7201"
    assert out.exists()


@pytest.mark.parametrize(
    "probe",
    ["N/A\n", "", FileNotFoundError("ffprobe")],
    ids=["unknown-duration", "no-output", "ffprobe-missing"],
)
def test_a_decoded_file_that_reaches_the_hard_stop_is_rejected(probe, tmp_path):
    calls: list = []
    out = tmp_path / "normalized.wav"

    with patch("app.utils.audio_utils.subprocess.run", side_effect=_fake_run(probe, 7201, calls)):
        with pytest.raises(AudioTooLong):
            normalize_to_wav(str(tmp_path / "in.mp3"), str(out), max_seconds=7200)

    assert len(_ffmpeg_calls(calls)) == 1
    assert not out.exists()


def test_audio_exactly_at_the_limit_is_accepted(tmp_path):
    calls: list = []
    out = tmp_path / "normalized.wav"

    with patch("app.utils.audio_utils.subprocess.run", side_effect=_fake_run("N/A\n", 7200, calls)):
        assert normalize_to_wav(str(tmp_path / "in.mp3"), str(out), max_seconds=7200) == str(out)


def test_an_ffprobe_timeout_is_a_normalisation_timeout(tmp_path):
    from app.utils.audio_utils import AudioNormalizationTimeout

    calls: list = []
    timeout = subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=1.0)
    with patch("app.utils.audio_utils.subprocess.run", side_effect=_fake_run(timeout, 0, calls)):
        with pytest.raises(AudioNormalizationTimeout):
            normalize_to_wav(str(tmp_path / "in.mp3"), str(tmp_path / "o.wav"), max_seconds=7200)


def test_the_limit_defaults_to_two_hours():
    assert InsightSettings(_env_file=None).max_audio_seconds == 7200


def test_the_limit_comes_from_settings_by_default(tmp_path, monkeypatch):
    short_limit = InsightSettings(_env_file=None, max_audio_seconds=60)
    monkeypatch.setattr("app.utils.audio_utils.get_settings", lambda: short_limit)
    calls: list = []

    with patch("app.utils.audio_utils.subprocess.run", side_effect=_fake_run("61\n", 0, calls)):
        with pytest.raises(AudioTooLong):
            normalize_to_wav(str(tmp_path / "in.mp3"), str(Path(tmp_path) / "o.wav"))
