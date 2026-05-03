"""Source clients for fact-check verification.

Each client wraps one external data source and exposes a single
``fetch(claim)`` method that returns an ``Evidence`` instance or ``None``
on any failure (timeout, HTTP error, missing field, missing API key).
"""

from __future__ import annotations

from app.insights.core.factcheck.source_clients.base_client import (
    BaseSourceClient,
)
from app.insights.core.factcheck.source_clients.coingecko_client import (
    CoinGeckoClient,
)
from app.insights.core.factcheck.source_clients.forex_client import (
    ForexClient,
)
from app.insights.core.factcheck.source_clients.openweather_client import (
    OpenWeatherClient,
)
from app.insights.core.factcheck.source_clients.static_facts_client import (
    StaticFactsClient,
)
from app.insights.core.factcheck.source_clients.stock_client import (
    StockClient,
)

__all__ = [
    "BaseSourceClient",
    "CoinGeckoClient",
    "ForexClient",
    "OpenWeatherClient",
    "StaticFactsClient",
    "StockClient",
]
