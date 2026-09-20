"""L5-2: the pipeline's Java callback dispatch and the min_confidence filter.

`min_confidence` has existed in `AgentBrainSettings` since Sprint 6,
documented as "Recommendations below this are dropped before callback" — and
nothing read it, because nothing ever called the callback. These tests pin
both the filter and the fire-and-forget contract.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.agent_brain.config.settings import get_agent_settings
from app.agent_brain.models.recommendation import (
    Entities,
    Recommendation,
    RecommendationResponse,
    Source,
)
from app.pipeline.orchestrator import VoiceIQOrchestrator


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_agent_settings.cache_clear()
    yield
    get_agent_settings.cache_clear()


def _rec(title: str, confidence: float) -> Recommendation:
    return Recommendation(
        agent_type="TASK_AGENT",
        action_type="TASK",
        title=title,
        description="d",
        confidence=confidence,
        source=Source(segment_id="seg", text="t"),
        entities=Entities(),
        explanation="e",
    )


def _response(*recs: Recommendation) -> RecommendationResponse:
    return RecommendationResponse(session_id="sess-1", recommendations=list(recs))


def _enable(monkeypatch: pytest.MonkeyPatch, *, min_confidence: str = "0.50") -> None:
    monkeypatch.setenv("VOICEIQ_AGENT_CALLBACK_URL", "https://java.example.com/hook")
    monkeypatch.setenv("VOICEIQ_AGENT_CALLBACK_SECRET", "s3cret")
    monkeypatch.setenv("VOICEIQ_AGENT_MIN_CONFIDENCE", min_confidence)
    get_agent_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Off by default                                                              #
# --------------------------------------------------------------------------- #


def test_no_callback_without_url_and_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same posture as every other egress: configured or silent."""
    monkeypatch.delenv("VOICEIQ_AGENT_CALLBACK_URL", raising=False)
    monkeypatch.delenv("VOICEIQ_AGENT_CALLBACK_SECRET", raising=False)
    get_agent_settings.cache_clear()

    sent: list[object] = []
    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        lambda self, payload, *, trace_id: sent.append(payload) or True,
    )

    assert VoiceIQOrchestrator._dispatch_agent_callback("sess-1", _response(_rec("t", 0.9))) is None
    assert sent == []


def test_url_without_secret_does_not_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_AGENT_CALLBACK_URL", "https://java.example.com/hook")
    monkeypatch.delenv("VOICEIQ_AGENT_CALLBACK_SECRET", raising=False)
    get_agent_settings.cache_clear()
    assert VoiceIQOrchestrator._dispatch_agent_callback("sess-1", _response(_rec("t", 0.9))) is None


# --------------------------------------------------------------------------- #
# The min_confidence filter                                                   #
# --------------------------------------------------------------------------- #


def test_callback_carries_only_recommendations_at_or_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, min_confidence="0.60")
    captured: list = []
    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        lambda self, payload, *, trace_id: captured.append(payload) or True,
    )

    response = _response(_rec("high", 0.9), _rec("exactly", 0.6), _rec("low", 0.59))
    thread = VoiceIQOrchestrator._dispatch_agent_callback("sess-1", response)
    assert thread is not None
    thread.join(timeout=5)

    assert len(captured) == 1
    titles = [r.title for r in captured[0].recommendations]
    assert titles == ["high", "exactly"], "threshold must be inclusive and drop only below it"


def test_response_keeps_everything_the_callback_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The filter is callback-only.

    A low-confidence recommendation stays visible to a human in the API
    response; it is only not pushed at Java as actionable.
    """
    _enable(monkeypatch, min_confidence="0.60")
    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        lambda self, payload, *, trace_id: True,
    )

    response = _response(_rec("high", 0.9), _rec("low", 0.1))
    thread = VoiceIQOrchestrator._dispatch_agent_callback("sess-1", response)
    if thread:
        thread.join(timeout=5)

    assert [r.title for r in response.recommendations] == ["high", "low"]


def test_no_callback_when_nothing_meets_the_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch, min_confidence="0.95")
    sent: list[object] = []
    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        lambda self, payload, *, trace_id: sent.append(payload) or True,
    )

    assert VoiceIQOrchestrator._dispatch_agent_callback("sess-1", _response(_rec("low", 0.5))) is None
    assert sent == []


def test_no_callback_with_zero_recommendations(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    assert VoiceIQOrchestrator._dispatch_agent_callback("sess-1", _response()) is None


# --------------------------------------------------------------------------- #
# Fire-and-forget contract                                                    #
# --------------------------------------------------------------------------- #


def test_dispatch_returns_a_daemon_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """It must not be on the request path, and must not hold shutdown open."""
    _enable(monkeypatch)
    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        lambda self, payload, *, trace_id: True,
    )

    thread = VoiceIQOrchestrator._dispatch_agent_callback("sess-1", _response(_rec("t", 0.9)))
    assert thread is not None
    assert thread.daemon is True
    thread.join(timeout=5)


def test_callback_failure_never_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Java being down must not affect the audio response in any way."""
    _enable(monkeypatch)

    def _boom(self, payload, *, trace_id):
        raise RuntimeError("java is down")

    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        _boom,
    )

    thread = VoiceIQOrchestrator._dispatch_agent_callback("sess-1", _response(_rec("t", 0.9)))
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()


def test_payload_carries_session_and_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    captured: list = []
    monkeypatch.setattr(
        "app.agent_brain.integrations.java_callback_client.JavaCallbackClient.send",
        lambda self, payload, *, trace_id: captured.append((payload, trace_id)) or True,
    )

    thread = VoiceIQOrchestrator._dispatch_agent_callback("sess-42", _response(_rec("t", 0.9)))
    assert thread is not None
    thread.join(timeout=5)

    payload, trace_id = captured[0]
    assert payload.session_id == "sess-42"
    assert payload.generated_at.endswith("+00:00"), "generated_at must be explicit UTC"
    assert len(trace_id) == 32, "trace id is what Java de-duplicates on"
