"""Detection-signal matching (Phase 1: case-insensitive substring)."""

from __future__ import annotations


def find_signals(text: str, signals: list[str]) -> list[str]:
    """Return the signal phrases present in `text` (case-insensitive)."""
    if not text:
        return []
    lowered = text.lower()
    return [s for s in signals if s.lower() in lowered]


def has_signal(text: str, signals: list[str]) -> bool:
    """True if any signal phrase is present in `text`."""
    return bool(find_signals(text, signals))
