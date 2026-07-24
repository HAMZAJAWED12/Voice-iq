"""Unit coverage for the upload filename validator (app/utils/upload.py).

The route (`/v1/process-audio`) maps a raised ValueError to HTTP 400. These
pin the validation directly - the route itself can't be imported on the
lightweight stack (it pulls in the heavy orchestrator).
"""

from __future__ import annotations

import pytest

from app.utils.upload import ALLOWED_AUDIO_EXTS, resolve_audio_ext


@pytest.mark.parametrize(
    ("filename", "expected_ext"),
    [
        ("call.mp3", ".mp3"),
        ("call.wav", ".wav"),
        ("call.m4a", ".m4a"),
        ("call.flac", ".flac"),
        ("CALL.WAV", ".wav"),  # case-insensitive
        ("my.long.name.mp3", ".mp3"),  # splitext takes the last ext
    ],
)
def test_valid_filenames_return_ext(filename: str, expected_ext: str) -> None:
    assert resolve_audio_ext(filename) == expected_ext


def test_none_filename_raises_not_crashes() -> None:
    # Regression: previously `file.filename.lower()` on a None filename threw
    # AttributeError -> unhandled 500. Now a clean ValueError -> 400.
    with pytest.raises(ValueError, match="Missing filename"):
        resolve_audio_ext(None)


def test_empty_filename_raises() -> None:
    with pytest.raises(ValueError, match="Missing filename"):
        resolve_audio_ext("")


@pytest.mark.parametrize("filename", ["notes.txt", "evil.exe", "archive.zip", "noext", "trailingdot."])
def test_unsupported_extension_raises(filename: str) -> None:
    with pytest.raises(ValueError, match="Unsupported file format"):
        resolve_audio_ext(filename)


def test_message_is_client_safe() -> None:
    # The messages are fixed strings the route echoes verbatim to the client;
    # they must never carry a path or other internal detail.
    for bad in (None, "", "secret/path/to/file.txt"):
        try:
            resolve_audio_ext(bad)  # type: ignore[arg-type]
        except ValueError as e:
            assert str(e) in {"Missing filename", "Unsupported file format"}


def test_allowlist_is_the_four_audio_types() -> None:
    assert ALLOWED_AUDIO_EXTS == (".mp3", ".wav", ".m4a", ".flac")
