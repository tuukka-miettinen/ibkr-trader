from __future__ import annotations

import random
from collections import defaultdict
from datetime import timedelta

from app.models.market_data import Candle, Timeframe, utc_now
from app.providers.base import MarketDataProvider


TIMEFRAME_STEPS = {
    Timeframe.ONE_MINUTE: timedelta(minutes=1),
    Timeframe.FIVE_MINUTES: timedelta(minutes=5),
    Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    Timeframe.ONE_HOUR: timedelta(hours=1),
}

BASE_PRICES = {
    "AAPL": 184.0,
    "MSFT": 421.0,
    "NVDA": 911.0,
    "TSLA": 170.0,
}


class MockMarketDataProvider(MarketDataProvider):
    def __init__(self) -> None:
        self._store: dict[tuple[str, Timeframe], list[Candle]] = defaultdict(list)

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        if not self._store[key]:
            self._store[key] = self._seed_history(normalized_symbol, timeframe, limit)
        return self._store[key][-limit:]

    def get_live_price(self, symbol: str) -> float | None:
        history = self.get_history(symbol, Timeframe.ONE_MINUTE, 2)
        base_price = history[-1].close if history else BASE_PRICES.get(symbol.upper(), 100.0)
        return round(max(1.0, base_price + random.uniform(-1.0, 1.0)), 2)

    def _seed_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        step = TIMEFRAME_STEPS[timeframe]
        end_time = utc_now().replace(second=0, microsecond=0)
        start_price = BASE_PRICES.get(symbol, 100.0)
        candles: list[Candle] = []

        for index in range(limit):
            time = end_time - step * (limit - index)
            open_price = start_price + random.uniform(-2.5, 2.5)
            close_price = max(1.0, open_price + random.uniform(-2.0, 2.0))
            high_price = max(open_price, close_price) + random.uniform(0.2, 1.1)
            low_price = min(open_price, close_price) - random.uniform(0.2, 1.1)
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    time=time,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(low_price, 2),
                    close=round(close_price, 2),
                    volume=random.randint(1000, 8000),
                )
            )
            start_price = close_price

        return candles
