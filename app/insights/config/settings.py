"""Runtime settings for the Insight Service.

Loaded from environment variables (or a local `.env` file when present),
with safe defaults so the service can run without any configuration.

All knobs that change between dev / staging / prod live here so the
engines themselves stay pure.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

try:
    # pydantic-settings is the v2 home for BaseSettings.
    from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
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
        description=(
            "Hard cap on incoming JSON payload size (KB) for /v1/insights/* "
            "and /v1/fact-check. Enforced via Content-Length; requests "
            "above this are rejected with 413 before any body is read."
        ),
    )
    api_max_upload_mb: int = Field(
        default=200,
        ge=1,
        description=(
            "Hard cap on multipart upload size (MB) for /v1/process-audio. "
            "Enforced both via Content-Length and during streaming write so "
            "a lying Content-Length header cannot bypass it."
        ),
    )

    # --- Authentication -------------------------------------------------- #
    # `NoDecode` tells pydantic-settings to skip its default JSON-decode
    # pass on this field so a value like `key1,key2` from `.env` reaches
    # the `_split_api_keys` validator as a raw string rather than being
    # rejected by the JSON parser.
    api_keys: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description=(
            "Accepted values for the X-API-Key request header. Set "
            "VOICEIQ_API_KEYS as a comma-separated string. Empty list "
            "disables auth in non-production environments; in production "
            "an empty list causes every request to fail with 503."
        ),
    )

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_api_keys(cls, value: Any) -> Any:
        """Allow `VOICEIQ_API_KEYS=key1,key2,key3` to load as a list.

        Pydantic-settings reads env vars as strings; without this validator
        a CSV string would be rejected against the `list[str]` annotation.
        Empty entries (e.g. trailing commas) are dropped.
        """
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # --- Fact-Check source clients --------------------------------------- #
    openweather_api_key: str = Field(
        default="",
        description=(
            "OpenWeather API key for the WEATHER source client. Empty "
            "string disables the client (verdict: SOURCE_UNAVAILABLE)."
        ),
    )
    alphavantage_api_key: str = Field(
        default="",
        description=("Alpha Vantage API key for the STOCK_PRICE source client. " "Empty string disables the client."),
    )
    factcheck_http_timeout_sec: float = Field(
        default=5.0,
        ge=0.5,
        le=30.0,
        description="Per-call HTTP timeout for fact-check source clients.",
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
