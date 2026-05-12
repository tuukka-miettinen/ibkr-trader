from __future__ import annotations

import os

from app.providers.base import MarketDataProvider

_provider_instance: MarketDataProvider | None = None


def get_market_data_provider() -> MarketDataProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    provider_name = os.environ.get("MARKET_DATA_PROVIDER", "ibkr").strip().lower()
    if provider_name == "mock":
        from app.providers.mock import MockMarketDataProvider

        _provider_instance = MockMarketDataProvider()
    elif provider_name == "ibkr":
        from app.providers.ibkr import IBKRMarketDataProvider

        _provider_instance = IBKRMarketDataProvider.from_env()
    else:
        raise ValueError(f"Unsupported market data provider: {provider_name}")

    return _provider_instance
