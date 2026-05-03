"""Weather source client (OpenWeather Current Weather Data API)."""

from __future__ import annotations

from typing import ClassVar, Optional

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.models.factcheck_models import DetectedClaim, Evidence


class OpenWeatherClient(BaseSourceClient):
    """Resolve WEATHER claims via OpenWeather. Requires an API key."""

    name: ClassVar[str] = "openweather"
    BASE_URL: ClassVar[str] = "https://api.openweathermap.org/data/2.5/weather"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_sec: float = 5.0,
        client=None,
    ) -> None:
        super().__init__(timeout_sec=timeout_sec, client=client)
        self._api_key = (api_key or "").strip()

    def fetch(self, claim: DetectedClaim) -> Optional[Evidence]:
        if claim.claim_type != "WEATHER":
            return None
        if not self._api_key:
            # Key absent - treat as SOURCE_UNAVAILABLE.
            return None

        city = claim.subject.get("city")
        if not city:
            return None

        # Map claim unit ("C"/"F") to OpenWeather `units` parameter.
        unit = (claim.subject.get("unit") or "C").upper()
        units_param = "metric" if unit == "C" else "imperial"

        payload = self._get_json(
            self.BASE_URL,
            params={"q": city, "appid": self._api_key, "units": units_param},
        )
        if not payload:
            return None

        main = payload.get("main") or {}
        temp = main.get("temp")
        if temp is None:
            return None
        try:
            value = float(temp)
        except (TypeError, ValueError):
            return None

        return Evidence(
            source=self.name,
            value=value,
            unit=unit,
            raw={
                "city": city,
                "temp": value,
                "units": units_param,
                "weather_id": (payload.get("id") if isinstance(payload, dict) else None),
            },
        )
