"""Coverage for the fact-check opt-in gate (S3 / wave S-D).

Fact-check transmits transcript-derived claim subjects to external
providers, so /v1/process-audio only runs it when explicitly asked. The
policy lives in app/utils/factcheck_gate.py (extracted from the route,
which pulls the heavy ML stack and can't be imported on the light lane).
"""

from __future__ import annotations

import pytest

from app.insights.config.settings import get_settings
from app.utils.factcheck_gate import DISABLED_STUB, factcheck_requested


def test_default_is_opt_in_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOICEIQ_FACTCHECK_AUTO_ENRICH", raising=False)
    get_settings.cache_clear()
    try:
        # Privacy-first default: nothing leaves the trust boundary unasked.
        assert get_settings().factcheck_auto_enrich is False
        assert factcheck_requested(None) is False
    finally:
        get_settings.cache_clear()


def test_setting_enables_globally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_FACTCHECK_AUTO_ENRICH", "true")
    get_settings.cache_clear()
    try:
        assert factcheck_requested(None) is True
    finally:
        get_settings.cache_clear()


def test_query_param_overrides_setting_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_FACTCHECK_AUTO_ENRICH", "false")
    get_settings.cache_clear()
    try:
        assert factcheck_requested(True) is True  # ?fact_check=true beats the off setting
    finally:
        get_settings.cache_clear()


def test_query_param_overrides_setting_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICEIQ_FACTCHECK_AUTO_ENRICH", "true")
    get_settings.cache_clear()
    try:
        assert factcheck_requested(False) is False  # ?fact_check=false beats the on setting
    finally:
        get_settings.cache_clear()


def test_disabled_stub_shape() -> None:
    # The route returns this as fact_checks_v2 when enrichment is skipped, so
    # a client can tell "disabled" apart from "failed" / "skipped".
    assert DISABLED_STUB["status"] == "disabled"
    assert "opt-in" in DISABLED_STUB["reason"]
