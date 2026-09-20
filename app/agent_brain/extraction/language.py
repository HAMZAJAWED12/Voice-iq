"""Language routing for the extraction layer.

N4b-1 Phase 1 is **wiring only**. This module exists so that the extractors
can select a per-language vocabulary without any extractor knowing how
languages are resolved, and so that adding Urdu/Arabic later is *data* —
a new key in a table — rather than a code change.

No vocabulary lives here, and none ships in Phase 1. Every table currently
carries exactly one populated key, ``"en"``, holding the same terms the
extractors used before this seam existed. Everything else falls back to it,
so behaviour is byte-identical to pre-N4b.

See `DOCS/N4B-MULTILINGUAL-STRATEGY.md`. Sourcing the real ur/ar vocabulary
is decision **L1** (native-speaker work, not engineering) and gates Phase 2.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar, cast, get_args

from app.agent_brain.models.enums import LanguageCode

DEFAULT_LANGUAGE: LanguageCode = "en"

# The wire contract's accepted values. Whisper emits ISO-639-1, which
# overlaps this set for en/ur/ar; "mixed" is never emitted by Whisper and is
# only ever set by a caller.
_SUPPORTED: frozenset[str] = frozenset(get_args(LanguageCode))

T = TypeVar("T")


def resolve_language(code: str | None) -> LanguageCode:
    """Map a detected language code onto the `LanguageCode` contract.

    Whisper auto-detects and can return any ISO-639-1 code — `hi`, `fa`,
    `pa` and friends are all plausible on real Pakistani audio. Anything
    outside the contract resolves to English rather than raising: an
    unexpected language must degrade extraction, never fail a pipeline run
    that has already succeeded.
    """
    if not code:
        return DEFAULT_LANGUAGE
    normalized = code.strip().lower()
    if normalized in _SUPPORTED:
        return cast(LanguageCode, normalized)
    return DEFAULT_LANGUAGE


def vocabulary_for(table: Mapping[str, T], language: str | None) -> T:
    """Select `table`'s entry for `language`, falling back to English.

    Falls back on **both** of the cases Phase 1 produces:

    * the key is absent — the language has no table yet;
    * the key is present but empty — someone has stubbed it ahead of L1.

    Treating those identically is what lets L1 land as pure data: fill an
    entry and it takes effect; leave it empty and English still applies.

    Compiled `re.Pattern` values are always truthy, so a populated pattern
    table is never mistaken for an empty one.
    """
    entry = table.get(language or DEFAULT_LANGUAGE)
    if entry:
        return entry
    return table[DEFAULT_LANGUAGE]
