"""Unit coverage for the audio magic-byte sniffer (app/utils/audio_sniff.py).

The upload route (`/v1/process-audio`) maps `is_recognized_audio(head) is
False` to HTTP 415, so the renamed-executable attack is rejected before the
pipeline runs. These tests pin the signature logic directly (the route
itself can't be imported on the lightweight stack — it pulls in the heavy
orchestrator).
"""

from __future__ import annotations

import pytest

from app.utils.audio_sniff import is_recognized_audio

_WAV = b"RIFF\x24\x08\x00\x00WAVEfmt "
_FLAC = b"fLaC\x00\x00\x00\x22extra"
_M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00"
_MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00rest"
_MP3_SYNC = b"\xff\xfb\x90\x64extrabytes!"  # MPEG frame sync, no ID3
_EXE = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00"  # DOS/PE header
_PDF = b"%PDF-1.7\x0a%abc"
_ZIP = b"PK\x03\x04\x14\x00\x00\x00\x08\x00\x00\x00"


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (_WAV, True),
        (_FLAC, True),
        (_M4A, True),
        (_MP3_ID3, True),
        (_MP3_SYNC, True),
        (_EXE, False),  # .exe renamed to .mp3 -> rejected (the attack)
        (_PDF, False),
        (_ZIP, False),
        (b"plain text content here", False),
    ],
)
def test_is_recognized_audio(head, expected) -> None:
    assert is_recognized_audio(head) is expected


def test_too_short_header_is_rejected() -> None:
    assert is_recognized_audio(b"RIFF") is False
    assert is_recognized_audio(b"") is False
