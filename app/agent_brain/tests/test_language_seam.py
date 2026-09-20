"""The N4b-1 language seam: resolution, table fallback, and that it is READ.

These test the **seam**, not vocabulary. No Urdu or Arabic terms ship in
Phase 1 — decision L1 gates that — so wherever a test needs a non-English
table entry it injects an obviously-synthetic sentinel. A real-looking word
here would be mistaken for shipped vocabulary.

The finding this file exists to kill permanently: before N4b-1,
`AgentContext.language` was **write-only**. The literal, the model field and
the adapter parameter all existed and nothing anywhere read it. Any
regression to that state should fail `test_language_actually_switches_*`.
"""

from __future__ import annotations

import pytest

from app.agent_brain.adapters.pipeline_adapter import PipelineAdapter
from app.agent_brain.core import escalation_agent as escalation_mod
from app.agent_brain.core.escalation_agent import EscalationAgent
from app.agent_brain.extraction import priority_classifier as priority_mod
from app.agent_brain.extraction.language import (
    DEFAULT_LANGUAGE,
    resolve_language,
    vocabulary_for,
)
from app.agent_brain.extraction.priority_classifier import classify_priority
from app.agent_brain.extraction.signals import find_signals
from app.agent_brain.models.agent_context import AgentContext, TranscriptSegment
from app.insights.models.input_models import SessionInput, UtteranceInput

# Unmistakably not vocabulary.
_SENTINEL = "__n4b_seam_sentinel__"


def _session() -> SessionInput:
    return SessionInput(
        session_id="sess-1",
        utterances=[UtteranceInput(id="u1", speaker="A", start=0.0, end=2.0, text="Ali will send the report.")],
    )


# --------------------------------------------------------------------------- #
# resolve_language                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", ["en", "ur", "ar", "mixed"])
def test_resolve_language_passes_through_contract_values(code: str) -> None:
    assert resolve_language(code) == code


@pytest.mark.parametrize("code", ["hi", "fa", "pa", "pt", "zz", "en-US", "urdu"])
def test_resolve_language_falls_back_for_codes_outside_the_contract(code: str) -> None:
    """Whisper can emit any ISO-639-1 code; hi/fa/pa are plausible on real
    Pakistani audio. An unexpected language must degrade, never raise."""
    assert resolve_language(code) == DEFAULT_LANGUAGE


@pytest.mark.parametrize("code", [None, "", "   "])
def test_resolve_language_falls_back_for_missing(code: str | None) -> None:
    assert resolve_language(code) == DEFAULT_LANGUAGE


@pytest.mark.parametrize("code", ["UR", " ur ", "Ar"])
def test_resolve_language_normalizes_case_and_whitespace(code: str) -> None:
    assert resolve_language(code) in {"ur", "ar"}


# --------------------------------------------------------------------------- #
# vocabulary_for — the fallback rule                                          #
# --------------------------------------------------------------------------- #


def test_vocabulary_for_selects_a_populated_entry() -> None:
    table = {"en": ["e"], "ur": ["u"]}
    assert vocabulary_for(table, "ur") == ["u"]


def test_vocabulary_for_falls_back_when_key_absent() -> None:
    """The Phase-1 state: ur/ar/mixed have no entry at all."""
    table = {"en": ["e"]}
    assert vocabulary_for(table, "ur") == ["e"]


def test_vocabulary_for_falls_back_when_key_present_but_empty() -> None:
    """Absent and empty must behave identically — that is what lets L1 land
    as pure data rather than a code change."""
    table: dict[str, list[str]] = {"en": ["e"], "ur": []}
    assert vocabulary_for(table, "ur") == ["e"]


@pytest.mark.parametrize("language", [None, "", "zz"])
def test_vocabulary_for_falls_back_for_missing_or_unknown(language: str | None) -> None:
    assert vocabulary_for({"en": ["e"]}, language) == ["e"]


# --------------------------------------------------------------------------- #
# Adapter: asr_meta.language -> AgentContext.language                         #
# --------------------------------------------------------------------------- #


def test_adapter_reads_detected_language_from_asr_meta() -> None:
    """The wiring this phase exists for. Whisper detects it, ASRService
    records it, orchestrator.py surfaces it as result["asr_meta"]["language"]
    — and before N4b-1 it stopped there."""
    ctx = PipelineAdapter.to_context(_session(), asr_meta={"language": "ur"})
    assert ctx.language == "ur"


def test_adapter_falls_back_when_asr_meta_language_is_outside_the_contract() -> None:
    ctx = PipelineAdapter.to_context(_session(), asr_meta={"language": "hi"})
    assert ctx.language == "en"


@pytest.mark.parametrize("meta", [{}, {"language": None}, {"language": ""}])
def test_adapter_falls_back_when_asr_meta_has_no_language(meta: dict) -> None:
    assert PipelineAdapter.to_context(_session(), asr_meta=meta).language == "en"


def test_adapter_explicit_language_beats_asr_meta() -> None:
    """Precedence: an explicit caller argument wins over detection."""
    ctx = PipelineAdapter.to_context(_session(), language="ar", asr_meta={"language": "ur"})
    assert ctx.language == "ar"


def test_adapter_defaults_to_english_with_neither() -> None:
    assert PipelineAdapter.to_context(_session()).language == "en"


# --------------------------------------------------------------------------- #
# The language is actually READ — not write-only any more                     #
# --------------------------------------------------------------------------- #


def test_language_actually_switches_extractor_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prove selection happens, without shipping vocabulary.

    Inject a sentinel term under a non-English key: it must classify under
    that language and NOT under English. If `language` were ignored, both
    assertions could not hold at once.
    """
    monkeypatch.setitem(priority_mod._CRITICAL_TERMS, "ur", (_SENTINEL,))

    text = f"this is {_SENTINEL}"
    assert classify_priority(text, language="ur") == "CRITICAL"
    assert classify_priority(text, language="en") == "MEDIUM"


def test_language_actually_switches_signal_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    table: dict[str, list[str]] = {"en": ["hello"], "ur": [_SENTINEL]}
    assert find_signals(f"say {_SENTINEL}", table, language="ur") == [_SENTINEL]
    assert find_signals(f"say {_SENTINEL}", table, language="en") == []
    monkeypatch.undo()


def test_language_reaches_the_agents_from_agent_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end for the seam: AgentContext.language -> agent -> table.

    This is the one that kills the write-only finding. The sentinel is only
    reachable if the agent read `context.language` and passed it down.
    """
    monkeypatch.setitem(escalation_mod._ANGER, "ur", [_SENTINEL])

    segment = TranscriptSegment(segment_id="s1", speaker_id="A", text=f"customer is {_SENTINEL}")

    ur_context = AgentContext(session_id="s", language="ur", transcript=[segment])
    en_context = AgentContext(session_id="s", language="en", transcript=[segment])

    assert EscalationAgent().detect(ur_context), "language never reached the agent"
    assert not EscalationAgent().detect(en_context), "English table wrongly matched the sentinel"


def test_unknown_context_language_degrades_to_english_behaviour() -> None:
    """A language with no table must behave exactly as English did before."""
    segment = TranscriptSegment(segment_id="s1", speaker_id="A", text="customer is upset")

    ar_context = AgentContext(session_id="s", language="ar", transcript=[segment])
    en_context = AgentContext(session_id="s", language="en", transcript=[segment])

    ar_found = EscalationAgent().detect(ar_context)
    en_found = EscalationAgent().detect(en_context)

    assert len(ar_found) == len(en_found) == 1
    assert ar_found[0].title == en_found[0].title
