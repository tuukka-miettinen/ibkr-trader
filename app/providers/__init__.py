from __future__ import annotations

import os

from app.providers.base import MarketDataProvider


def get_market_data_provider() -> MarketDataProvider:
    provider_name = os.environ.get("MARKET_DATA_PROVIDER", "ibkr").strip().lower()
    if provider_name == "mock":
        from app.providers.mock import MockMarketDataProvider

        return MockMarketDataProvider()

    if provider_name == "ibkr":
        from app.providers.ibkr import IBKRMarketDataProvider

        return IBKRMarketDataProvider.from_env()

    raise ValueError(f"Unsupported market data provider: {provider_name}")
