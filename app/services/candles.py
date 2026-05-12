from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.db.candles import CandleRepository
from app.db.database import get_db_context
from app.models.market_data import Candle, Timeframe, utc_now
from app.providers import get_market_data_provider
from app.providers.base import MarketDataProvider


TIMEFRAME_STEPS = {
    Timeframe.FIVE_SECONDS: timedelta(seconds=5),
    Timeframe.ONE_MINUTE: timedelta(minutes=1),
    Timeframe.THREE_MINUTES: timedelta(minutes=3),
    Timeframe.FIVE_MINUTES: timedelta(minutes=5),
    Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    Timeframe.ONE_HOUR: timedelta(hours=1),
}


class CandleService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider
        self._repository = CandleRepository()
        self._cache: dict[tuple[str, Timeframe], list[Candle]] = {}

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int = 120) -> list[Candle]:
        return self._run_coroutine(self._get_history_async(symbol, timeframe, limit))

    async def _get_history_async(self, symbol: str, timeframe: Timeframe, limit: int = 120) -> list[Candle]:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        cached = self._cache.get(key)
        if self._is_history_fresh(cached, timeframe, limit):
            return cached[-limit:]

        async with get_db_context() as session:
            candles = await self._repository.get_recent_candles(session, normalized_symbol, timeframe, limit)
            if await self._needs_provider_refresh(candles, timeframe, limit):
                fetched = await self._fetch_missing_history(normalized_symbol, timeframe, candles, limit)
                if fetched:
                    await self._repository.upsert_candles(session, fetched)
                    candles = await self._repository.get_recent_candles(session, normalized_symbol, timeframe, limit)

        self._cache[key] = candles[-max(limit, 120):]
        return candles[-limit:]

    def next_candle(self, symbol: str, timeframe: Timeframe) -> Candle:
        return self._run_coroutine(self._next_candle_async(symbol, timeframe))

    async def _next_candle_async(self, symbol: str, timeframe: Timeframe) -> Candle:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        history = self._cache.get(key)
        if not history:
            history = await self._get_history_async(normalized_symbol, timeframe)

        last = history[-1]
        step = TIMEFRAME_STEPS[timeframe]

        # When a timeframe boundary has passed, refresh from IBKR historical bars
        # so the newly opened candle is structurally correct instead of guessed.
        if utc_now() - last.time >= step:
            refreshed = await self._get_history_async(normalized_symbol, timeframe, limit=max(len(history), 120))
            return refreshed[-1]

        live_price = await asyncio.to_thread(self._provider.get_live_price, normalized_symbol)
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

    def _run_coroutine(self, coroutine):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="candle-service") as executor:
            future = executor.submit(lambda: asyncio.run(coroutine))
            return future.result()

    def _is_history_fresh(
        self,
        candles: list[Candle] | None,
        timeframe: Timeframe,
        limit: int,
    ) -> bool:
        if not candles or len(candles) < limit:
            return False
        return utc_now() - candles[-1].time < TIMEFRAME_STEPS[timeframe]

    async def _needs_provider_refresh(self, candles: list[Candle], timeframe: Timeframe, limit: int) -> bool:
        if len(candles) < limit:
            return True
        return utc_now() - candles[-1].time >= TIMEFRAME_STEPS[timeframe]

    async def _fetch_missing_history(
        self,
        symbol: str,
        timeframe: Timeframe,
        candles: list[Candle],
        limit: int,
    ) -> list[Candle]:
        last_time = candles[-1].time if candles else None
        if last_time is None or len(candles) < limit:
            fetched = await asyncio.to_thread(self._provider.get_history, symbol, timeframe, max(limit, 120))
            return fetched[-max(limit, 120):]

        missing_count = self._missing_bar_count(last_time, timeframe)
        if missing_count <= 0:
            return []

        return await asyncio.to_thread(
            self._provider.get_history_since,
            symbol,
            timeframe,
            last_time,
            max(missing_count + 1, 2),
        )

    def _missing_bar_count(self, latest_time, timeframe: Timeframe) -> int:
        step = TIMEFRAME_STEPS[timeframe]
        elapsed = utc_now() - latest_time
        return max(int(elapsed // step), 0)


candle_service = CandleService(get_market_data_provider())
