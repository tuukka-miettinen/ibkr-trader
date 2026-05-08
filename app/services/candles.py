from __future__ import annotations

from datetime import timedelta

from app.models.market_data import Candle, Timeframe, utc_now
from app.providers import get_market_data_provider
from app.providers.base import MarketDataProvider


TIMEFRAME_STEPS = {
    Timeframe.ONE_MINUTE: timedelta(minutes=1),
    Timeframe.FIVE_MINUTES: timedelta(minutes=5),
    Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    Timeframe.ONE_HOUR: timedelta(hours=1),
}


class CandleService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider
        self._cache: dict[tuple[str, Timeframe], list[Candle]] = {}

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int = 120) -> list[Candle]:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        cached = self._cache.get(key)
        if cached and len(cached) >= limit:
            step = TIMEFRAME_STEPS[timeframe]
            if utc_now() - cached[-1].time < step:
                return cached[-limit:]

        candles = self._provider.get_history(normalized_symbol, timeframe, limit)
        self._cache[(normalized_symbol, timeframe)] = candles[-max(limit, 120):]
        return candles[-limit:]

    def next_candle(self, symbol: str, timeframe: Timeframe) -> Candle:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        history = self._cache.get(key)
        if not history:
            history = self.get_history(normalized_symbol, timeframe)

        last = history[-1]
        step = TIMEFRAME_STEPS[timeframe]

        # When a timeframe boundary has passed, refresh from IBKR historical bars
        # so the newly opened candle is structurally correct instead of guessed.
        if utc_now() - last.time >= step:
            refreshed = self.get_history(normalized_symbol, timeframe, limit=max(len(history), 120))
            return refreshed[-1]

        live_price = self._provider.get_live_price(normalized_symbol)
        if live_price is None:
            return last

        updated = last.model_copy(
            update={
                "high": round(max(last.high, live_price), 2),
                "low": round(min(last.low, live_price), 2),
                "close": round(live_price, 2),
            }
        )
        history[-1] = updated
        self._cache[key] = history
        return updated


candle_service = CandleService(get_market_data_provider())
