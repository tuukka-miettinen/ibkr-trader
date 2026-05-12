from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

from app.models.market_data import Candle, Timeframe, utc_now
from app.providers.base import MarketDataProvider


TIMEFRAME_STEPS = {
    Timeframe.FIVE_SECONDS: timedelta(seconds=5),
    Timeframe.ONE_MINUTE: timedelta(minutes=1),
    Timeframe.THREE_MINUTES: timedelta(minutes=3),
    Timeframe.FIVE_MINUTES: timedelta(minutes=5),
    Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    Timeframe.ONE_HOUR: timedelta(hours=1),
}

# US market regular trading hours in UTC (EDT offset: UTC-4)
MARKET_OPEN_UTC = time(13, 30)
MARKET_CLOSE_UTC = time(20, 0)

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
            self._store[key] = self._seed_history(normalized_symbol, timeframe, max(limit, 120))
        return self._store[key][-limit:]

    def get_history_since(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time,
        limit: int,
    ) -> list[Candle]:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        if not self._store[key]:
            self._store[key] = self._seed_history(normalized_symbol, timeframe, max(limit, 120))
        if start_time is None:
            return self.get_history(normalized_symbol, timeframe, limit)

        step = TIMEFRAME_STEPS[timeframe]
        while self._store[key][-1].time <= start_time:
            self._extend_history(normalized_symbol, timeframe, 1)

        target_time = utc_now().replace(second=0, microsecond=0)
        while self._store[key][-1].time + step <= target_time:
            self._extend_history(normalized_symbol, timeframe, 1)

        return [candle for candle in self._store[key] if candle.time > start_time][-limit:]

    def get_live_price(self, symbol: str) -> float | None:
        history = self.get_history(symbol, Timeframe.ONE_MINUTE, 2)
        base_price = history[-1].close if history else BASE_PRICES.get(symbol.upper(), 100.0)
        return round(max(1.0, base_price + random.uniform(-1.0, 1.0)), 2)

    @staticmethod
    def _is_trading_time(dt: datetime) -> bool:
        """Return True if *dt* falls within US RTH on a weekday."""
        if dt.weekday() >= 5:
            return False
        t = dt.time()
        return MARKET_OPEN_UTC <= t < MARKET_CLOSE_UTC

    def _trading_times(self, timeframe: Timeframe, limit: int) -> list[datetime]:
        """Walk backwards from *now* and collect *limit* trading-hours timestamps."""
        step = TIMEFRAME_STEPS[timeframe]
        cursor = utc_now().replace(second=0, microsecond=0)
        times: list[datetime] = []
        # Safety cap to avoid infinite loop
        max_iterations = limit * 20
        for _ in range(max_iterations):
            cursor -= step
            if self._is_trading_time(cursor):
                times.append(cursor)
                if len(times) >= limit:
                    break
        times.reverse()
        return times

    def _seed_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        times = self._trading_times(timeframe, limit)
        start_price = BASE_PRICES.get(symbol, 100.0)
        candles: list[Candle] = []

        for bar_time in times:
            open_price = start_price + random.uniform(-2.5, 2.5)
            close_price = max(1.0, open_price + random.uniform(-2.0, 2.0))
            high_price = max(open_price, close_price) + random.uniform(0.2, 1.1)
            low_price = min(open_price, close_price) - random.uniform(0.2, 1.1)
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    time=bar_time,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(low_price, 2),
                    close=round(close_price, 2),
                    volume=random.randint(1000, 8000),
                )
            )
            start_price = close_price

        return candles

    def _extend_history(self, symbol: str, timeframe: Timeframe, count: int) -> None:
        key = (symbol.upper(), timeframe)
        history = self._store[key]
        if not history:
            self._store[key] = self._seed_history(symbol.upper(), timeframe, max(count, 120))
            return

        step = TIMEFRAME_STEPS[timeframe]
        added = 0
        cursor = history[-1].time
        max_iterations = count * 20
        for _ in range(max_iterations):
            cursor += step
            if not self._is_trading_time(cursor):
                continue
            last = history[-1]
            open_price = last.close
            close_price = max(1.0, open_price + random.uniform(-2.0, 2.0))
            high_price = max(open_price, close_price) + random.uniform(0.2, 1.1)
            low_price = min(open_price, close_price) - random.uniform(0.2, 1.1)
            history.append(
                Candle(
                    symbol=symbol.upper(),
                    timeframe=timeframe,
                    time=cursor,
                    open=round(open_price, 2),
                    high=round(high_price, 2),
                    low=round(low_price, 2),
                    close=round(close_price, 2),
                    volume=random.randint(1000, 8000),
                )
            )
            added += 1
            if added >= count:
                break
