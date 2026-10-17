"""VOICEIQ_LOG_LEVEL sets the root logger; THRESHOLD_PROFILE warns that it does nothing.

The httpx and httpcore loggers stay at WARNING whatever the root level is,
because their INFO records carry full request URLs.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.insights.config.settings import InsightSettings
from app.utils import logger as logger_module
from app.utils.logger import configure_logging


@pytest.fixture(autouse=True)
def _restore_levels() -> Iterator[None]:
    names = ["", "httpx", "httpcore"]
    before = {name: logging.getLogger(name).level for name in names}
    yield
    for name, level in before.items():
        logging.getLogger(name).setLevel(level)


@pytest.mark.parametrize("level", ["DEBUG", "WARNING", "ERROR"])
def test_the_log_level_setting_sets_the_root_logger(level: str) -> None:
    configure_logging(InsightSettings(_env_file=None, log_level=level))

    assert logging.getLogger().level == logging.getLevelName(level)
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_a_threshold_profile_that_is_set_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="voiceiq"):
        configure_logging(InsightSettings(_env_file=None, threshold_profile="strict"))

    assert any("THRESHOLD_PROFILE" in r.getMessage() and "not implemented" in r.getMessage() for r in caplog.records)


def test_a_threshold_profile_from_the_environment_logs_a_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("VOICEIQ_THRESHOLD_PROFILE", "default")

    with caplog.at_level(logging.WARNING, logger="voiceiq"):
        configure_logging(InsightSettings(_env_file=None))

    assert any("THRESHOLD_PROFILE" in r.getMessage() for r in caplog.records)


def test_no_warning_when_the_threshold_profile_is_not_set(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("VOICEIQ_THRESHOLD_PROFILE", raising=False)

    with caplog.at_level(logging.WARNING, logger="voiceiq"):
        configure_logging(InsightSettings(_env_file=None))

    assert not any("THRESHOLD_PROFILE" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("module", ["app.insight_main", "app.main"])
def test_each_app_applies_the_logging_settings_at_startup(module: str, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = importlib.import_module(module)
    seen: list[InsightSettings] = []
    monkeypatch.setattr(mod, "configure_logging", seen.append)

    with TestClient(mod.app):
        pass

    assert seen == [mod.settings]


def test_importing_the_logger_still_pins_the_http_loggers() -> None:
    logging.getLogger("httpx").setLevel(logging.DEBUG)

    logger_module.setup_logging()

    assert logging.getLogger("httpx").level == logging.WARNING
