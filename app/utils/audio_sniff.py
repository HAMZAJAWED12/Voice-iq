"""Magic-byte content sniffing for audio uploads.

Defense-in-depth layered on top of the filename-extension allowlist in the
upload route: a renamed executable/archive/document carries none of these
signatures and is rejected before the pipeline runs. Dependency-free (no
libmagic) so it imports cleanly on the lightweight CI stack.
"""

from __future__ import annotations

# Minimum bytes needed to evaluate every signature below (RIFF needs 12).
_MIN_HEADER_BYTES = 12


def is_recognized_audio(head: bytes) -> bool:
    """True if `head` (the first bytes of an upload) looks like supported audio.

    Recognizes WAV (RIFF/WAVE), FLAC (fLaC), MP4/M4A (ftyp box), and MP3
    (ID3v2 tag or a bare MPEG frame sync). Returns True only for genuine
    audio signatures; anything else (MZ/ELF/PK/%PDF/plain text/...) is False.
    """
    if len(head) < _MIN_HEADER_BYTES:
        return False
    if head[0:4] == b"RIFF" and head[8:12] == b"WAVE":
        return True  # WAV
    if head[0:4] == b"fLaC":
        return True  # FLAC
    if head[4:8] == b"ftyp":
        return True  # MP4 / M4A container
    if head[0:3] == b"ID3":
        return True  # MP3 with an ID3v2 tag
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return True  # MPEG audio frame sync (e.g. MP3 with no ID3 tag)
    return False
