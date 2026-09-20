"""Detection-signal matching (Phase 1: case-insensitive substring).

N4b-1 Phase 1: `signals` may now be either a plain sequence (as before) or
a language-keyed table, in which case `language` selects the entry.

Worth recording, because it shapes Phase 2: this matcher **already works in
Arabic script**. `.lower()` is a no-op there — verified `text.lower() ==
text` for every Urdu and Arabic probe case — and substring matching hits
normally. Urdu/Arabic signal detection therefore needs translated word
lists and nothing else: no code change here, no model, no dependency. That
content is decision **L1**.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.agent_brain.extraction.language import vocabulary_for

# Either the legacy flat list, or a table keyed by language code.
SignalSource = Mapping[str, Sequence[str]] | Sequence[str]


def _resolve(signals: SignalSource, language: str | None) -> Sequence[str]:
    # A Mapping is a language table; anything else is a plain list of
    # phrases, kept so existing callers and tests work unchanged.
    if isinstance(signals, Mapping):
        return vocabulary_for(signals, language)
    return signals


def find_signals(text: str, signals: SignalSource, *, language: str | None = None) -> list[str]:
    """Return the signal phrases present in `text` (case-insensitive)."""
    if not text:
        return []
    lowered = text.lower()
    return [s for s in _resolve(signals, language) if s.lower() in lowered]


def has_signal(text: str, signals: SignalSource, *, language: str | None = None) -> bool:
    """True if any signal phrase is present in `text`."""
    return bool(find_signals(text, signals, language=language))
