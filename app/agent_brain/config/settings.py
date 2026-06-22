"""Agent Brain configuration (separate from InsightSettings, by composition)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentBrainSettings(BaseSettings):
    """Phase-1 settings. Java callback is disabled unless URL + secret are set."""

    model_config = SettingsConfigDict(env_prefix="VOICEIQ_AGENT_", env_file=".env", extra="ignore")

    callback_url: str = Field(default="", description="Java Action Layer callback URL; empty disables callback.")
    callback_secret: str = Field(default="", description="Shared secret for the HMAC-SHA256 request signature.")
    callback_timeout_sec: float = Field(default=5.0, ge=1.0, le=60.0)
    min_confidence: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Recommendations below this are dropped before callback (doc 11 threshold).",
    )

    @property
    def callback_enabled(self) -> bool:
        return bool(self.callback_url and self.callback_secret)


@lru_cache
def get_agent_settings() -> AgentBrainSettings:
    return AgentBrainSettings()
