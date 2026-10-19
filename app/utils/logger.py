# app/utils/logger.py
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only; settings are passed in by the apps
    from app.insights.config.settings import InsightSettings

# httpx logs "HTTP Request: <METHOD> <full URL>" at INFO for every response
# (httpcore adds DEBUG traces). Source-client URLs carry API keys (appid,
# apikey) and transcript-derived claim subjects, so these loggers stay at
# WARNING whatever the root level. A logging config applied after this
# module is imported can override that; keep them out of any such config.
_QUIET_HTTP_LOGGERS = ("httpx", "httpcore")


def setup_logging(level: str = "INFO") -> None:
    """Set the root log level; the HTTP client loggers stay at WARNING."""
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")
    logging.getLogger().setLevel(level)  # basicConfig does nothing once the root has handlers
    for name in _QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


setup_logging()

logger = logging.getLogger("voiceiq")


def configure_logging(settings: InsightSettings) -> None:
    """Apply VOICEIQ_LOG_LEVEL and warn about settings that have no effect.

    Called from each app's startup.
    """
    setup_logging(settings.log_level)
    if "threshold_profile" in settings.model_fields_set:
        logger.warning(
            "VOICEIQ_THRESHOLD_PROFILE is set to %r, but threshold profiles are not implemented; "
            "the rule engines use their default thresholds.",
            settings.threshold_profile,
        )
