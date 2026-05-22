from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from app.db.candles import CandleRepository
from app.db.database import get_db_context
from app.models.market_data import Candle, Timeframe, utc_now
from app.providers import get_market_data_provider
from app.providers.base import MarketDataError, MarketDataProvider


TIMEFRAME_STEPS = {
    Timeframe.FIVE_SECONDS: timedelta(seconds=5),
    Timeframe.ONE_MINUTE: timedelta(minutes=1),
    Timeframe.THREE_MINUTES: timedelta(minutes=3),
    Timeframe.FIVE_MINUTES: timedelta(minutes=5),
    Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    Timeframe.ONE_HOUR: timedelta(hours=1),
}

logger = logging.getLogger(__name__)
HISTORY_FETCH_TIMEOUT_SECONDS = float(os.environ.get("IBKR_HISTORY_TIMEOUT_SECONDS", "12"))
LIVE_PRICE_TIMEOUT_SECONDS = float(os.environ.get("IBKR_LIVE_PRICE_TIMEOUT_SECONDS", "3"))


class CandleService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider
        self._repository = CandleRepository()
        self._cache: dict[tuple[str, Timeframe], list[Candle]] = {}

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int = 120) -> list[Candle]:
        return self._run_coroutine(self.get_history_async(symbol, timeframe, limit))

    async def get_history_async(self, symbol: str, timeframe: Timeframe, limit: int = 120) -> list[Candle]:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        cached = self._cache.get(key)
        if self._is_history_fresh(cached, timeframe, limit):
            return cached[-limit:]

        async with get_db_context() as session:
            candles = await self._repository.get_recent_candles(session, normalized_symbol, timeframe, limit)
            if await self._needs_provider_refresh(candles, timeframe, limit):
                try:
                    fetched = await self._fetch_missing_history(normalized_symbol, timeframe, candles, limit)
                except MarketDataError as exc:
                    fallback = candles[-limit:] if candles else (cached[-limit:] if cached else [])
                    if fallback:
                        logger.warning(
                            "Using cached candles for %s %s after provider refresh failed: %s",
                            normalized_symbol,
                            timeframe,
                            exc,
                        )
                        self._cache[key] = fallback[-max(limit, 120):]
                        return fallback
                    raise

                if fetched:
                    await self._repository.upsert_candles(session, fetched)
                    candles = await self._repository.get_recent_candles(session, normalized_symbol, timeframe, limit)

        if not candles and cached:
            candles = cached[-limit:]
        if not candles:
            raise MarketDataError(
                f"No candle data available for {normalized_symbol} {timeframe}. "
                "IBKR historical data timed out and no cached candles were available."
            )

        self._cache[key] = candles[-max(limit, 120):]
        return candles[-limit:]

    def next_candle(self, symbol: str, timeframe: Timeframe) -> Candle:
        return self._run_coroutine(self.next_candle_async(symbol, timeframe))

    async def next_candle_async(self, symbol: str, timeframe: Timeframe) -> Candle:
        normalized_symbol = symbol.upper()
        key = (normalized_symbol, timeframe)
        history = self._cache.get(key)
        if not history:
            history = await self.get_history_async(normalized_symbol, timeframe)

        last = history[-1]
        step = TIMEFRAME_STEPS[timeframe]

        # When a timeframe boundary has passed, refresh from IBKR historical bars
        # so the newly opened candle is structurally correct instead of guessed.
        if utc_now() - last.time >= step:
            refreshed = await self.get_history_async(normalized_symbol, timeframe, limit=max(len(history), 120))
            return refreshed[-1]

        try:
            live_price = await asyncio.wait_for(
                asyncio.to_thread(self._provider.get_live_price, normalized_symbol),
                timeout=LIVE_PRICE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning("Timed out fetching live price for %s; keeping last candle", normalized_symbol)
            return last
        except MarketDataError:
            logger.warning("Live price unavailable for %s; keeping last candle", normalized_symbol)
            return last

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
            fetched = await self._provider_call_with_timeout(
                self._provider.get_history,
                symbol,
                timeframe,
                max(limit, 120),
            )
            return fetched[-max(limit, 120):]

        missing_count = self._missing_bar_count(last_time, timeframe)
        if missing_count <= 0:
            return []

        return await self._provider_call_with_timeout(
            self._provider.get_history_since,
            symbol,
            timeframe,
            last_time,
            max(missing_count + 1, 2),
        )

    async def _provider_call_with_timeout(self, fn, *args):
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(fn, *args),
                timeout=HISTORY_FETCH_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            raise MarketDataError(
                f"IBKR historical data request timed out after {HISTORY_FETCH_TIMEOUT_SECONDS:.0f}s"
            ) from exc

    def _missing_bar_count(self, latest_time, timeframe: Timeframe) -> int:
        step = TIMEFRAME_STEPS[timeframe]
        elapsed = utc_now() - latest_time
        return max(int(elapsed // step), 0)


candle_service = CandleService(get_market_data_provider())
