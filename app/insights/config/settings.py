"""Runtime settings for the Insight Service.

Loaded from environment variables (or a local `.env` file when present),
with safe defaults so the service can run without any configuration.

All knobs that change between dev / staging / prod live here so the
engines themselves stay pure.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field

try:
    # pydantic-settings is the v2 home for BaseSettings.
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError as exc:  # pragma: no cover - import-time guard
    raise ImportError(
        "pydantic-settings is required. Install it via:\n"
        "    pip install 'pydantic-settings>=2.0'\n"
        "or run: pip install -r requirements.txt"
    ) from exc


class InsightSettings(BaseSettings):
    """Application settings for the Insight Service.

    Settings are read from environment variables. A `.env` file at the
    project root will be honoured when present (see `.env.example`).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="VOICEIQ_",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Service metadata ------------------------------------------------- #
    service_name: str = Field(
        default="VoiceIQ Insight Service",
        description="Human-readable service name (surfaced in /docs and meta).",
    )
    service_version: str = Field(
        default="1.0.0",
        description="Service semantic version. Bump on contract changes.",
    )
    environment: Literal["development", "staging", "production", "test"] = Field(
        default="development",
        description="Deployment environment label.",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Root log level applied at startup.",
    )

    # --- Persistence ------------------------------------------------------ #
    database_url: str = Field(
        default="sqlite:///./data/insights.db",
        description=(
            "SQLAlchemy database URL. Defaults to a local SQLite file "
            "under ./data/insights.db. Override for Postgres etc."
        ),
    )
    database_echo: bool = Field(
        default=False,
        description="If True, SQLAlchemy logs every SQL statement (debug only).",
    )
    database_auto_create: bool = Field(
        default=True,
        description=(
            "If True, missing tables are created on startup. Set False in "
            "production environments where migrations are managed externally."
        ),
    )

    # --- Engine configuration -------------------------------------------- #
    threshold_profile: Literal["default", "strict", "lenient"] = Field(
        default="default",
        description=(
            "Named threshold profile applied to the rule engines. "
            "Engines fall back to DEFAULT_THRESHOLDS if no override is wired."
        ),
    )

    # --- API surface ----------------------------------------------------- #
    api_max_session_payload_kb: int = Field(
        default=2048,
        ge=1,
        description="Soft cap on incoming session payload size, in KB.",
    )

    @property
    def is_sqlite(self) -> bool:
        """Convenience: True when the active DB is SQLite."""
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> InsightSettings:
    """Return a process-wide singleton of the resolved settings.

    Cached so every request reuses the same object without re-parsing the
    environment. Tests can clear the cache via `get_settings.cache_clear()`
    after mutating the environment.
    """
    return InsightSettings()
