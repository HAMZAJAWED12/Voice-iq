"""Upload filename validation for the audio ingest route.

Extracted from ``app/routes/process_audio.py`` so the allowlist + extension
logic is unit-testable without importing the route (which pulls in the full
ML stack via the orchestrator). The route maps the raised ``ValueError`` to
HTTP 400 using the message verbatim - the messages here are safe, fixed
strings, never internal detail.
"""

from __future__ import annotations

import os

# The cheap first gate: extensions we accept before sniffing magic bytes.
ALLOWED_AUDIO_EXTS: tuple[str, ...] = (".mp3", ".wav", ".m4a", ".flac")


def resolve_audio_ext(filename: str | None) -> str:
    """Validate an upload filename and return its normalized extension.

    Returns the lowercased extension (e.g. ``".wav"``), defaulting to
    ``".mp3"`` when the name ends with an allowed type but splitext yields
    nothing unusual.

    Raises:
        ValueError: filename is missing/empty, or not an allowed audio type.
            The message is a fixed, client-safe string.
    """
    if not filename:
        raise ValueError("Missing filename")

    lower = filename.lower()
    if not lower.endswith(ALLOWED_AUDIO_EXTS):
        raise ValueError("Unsupported file format")

    return os.path.splitext(lower)[1] or ".mp3"
