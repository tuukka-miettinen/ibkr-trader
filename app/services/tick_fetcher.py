"""Fetch 5-second bars from IBKR and store as hourly chunks in the database."""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta

from app.db.database import get_db_context
from app.db.ticks import TickRepository
from app.models.market_data import Candle, Timeframe
from app.providers import get_market_data_provider
from app.providers.base import MarketDataProvider

logger = logging.getLogger(__name__)

# US trading hours in UTC
MARKET_OPEN_UTC = time(13, 30)
MARKET_CLOSE_UTC = time(20, 0)
# Extended hours: pre-market 4:00 AM ET (08:00 UTC) to after-hours 8:00 PM ET (00:00 UTC)
EXT_OPEN_UTC = time(8, 0)
EXT_CLOSE_HOUR = 24  # midnight = end of day

# IBKR limitations for 5-second bars
MAX_DURATION_SECONDS = 1800  # 30 minutes per request
REQUEST_DELAY_SECONDS = 2.0  # rate-limit spacing
MAX_LOOKBACK_DAYS = 7


def _trading_hours_for_date(date: datetime, extended: bool = False) -> list[datetime]:
    """Return the start of each trading hour for a given date (UTC)."""
    if date.weekday() >= 5:
        return []
    day = date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    if extended:
        # Extended: 08:00-24:00 UTC (pre-market 4AM ET through after-hours 8PM ET)
        return [day.replace(hour=h) for h in range(8, 24)]
    # RTH hours: 13:30-20:00 UTC → hour starts at 13, 14, 15, 16, 17, 18, 19
    return [day.replace(hour=h) for h in range(13, 20)]


def _trading_dates_for_range(start_date: datetime, end_date: datetime) -> list[datetime]:
    """Return weekday dates in the range [start_date, end_date]."""
    dates = []
    current = start_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    end = end_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=UTC)
    while current <= end:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


class TickFetcher:
    def __init__(self, provider: MarketDataProvider | None = None) -> None:
        self._provider = provider or get_market_data_provider()
        self._repo = TickRepository()

    def _required_hours(self, days: int, extended: bool = False) -> list[datetime]:
        """Compute the list of trading hours we expect data for over *days* trading days."""
        if days > MAX_LOOKBACK_DAYS:
            days = MAX_LOOKBACK_DAYS

        now = datetime.now(tz=UTC)
        start_date = now - timedelta(days=days + 3)  # buffer for weekends

        trading_dates = _trading_dates_for_range(start_date, now)
        trading_dates = trading_dates[-days:]

        hours: list[datetime] = []
        for date in trading_dates:
            for h in _trading_hours_for_date(date, extended=extended):
                hour_end = h + timedelta(hours=1)
                if hour_end <= now:
                    hours.append(h)

        # If today has no completed hours, extend lookback to get more history
        if not hours and trading_dates:
            extra_start = start_date - timedelta(days=3)
            extra_dates = _trading_dates_for_range(extra_start, trading_dates[0] - timedelta(days=1))
            for date in reversed(extra_dates):
                for h in _trading_hours_for_date(date, extended=extended):
                    hours.append(h)
                if hours:
                    break
            hours.sort()

        return hours

    async def fetch_and_store(
        self,
        symbol: str,
        days: int = 1,
        on_progress: callable | None = None,
        force: bool = False,
        extended: bool = False,
    ) -> dict:
        """Fetch 5-second bars for *symbol* over the last *days* trading days.

        Returns a progress dict: {total_chunks, fetched_chunks, cached_chunks}.
        """
        required_hours = self._required_hours(days, extended=extended)

        # Check which chunks we already have
        if force:
            existing_hours = set()
        else:
            async with get_db_context() as session:
                existing_hours = set(await self._repo.get_available_hours(session, symbol))

        missing_hours = [h for h in required_hours if h not in existing_hours]
        total = len(required_hours)
        cached = total - len(missing_hours)

        logger.info(
            "Tick fetch %s: %d total chunks, %d cached, %d to fetch",
            symbol, total, cached, len(missing_hours),
        )

        # Fetch missing chunks by making 30-minute requests to IBKR
        fetched = 0
        for hour_start in missing_hours:
            bars = await self._fetch_hour(symbol, hour_start, use_rth=not extended)
            if bars:
                bar_dicts = [
                    {
                        "t": bar.time.isoformat(),
                        "o": bar.open,
                        "h": bar.high,
                        "l": bar.low,
                        "c": bar.close,
                        "v": bar.volume,
                    }
                    for bar in bars
                ]
                async with get_db_context() as session:
                    await self._repo.upsert_chunk(session, symbol, hour_start, bar_dicts)

            fetched += 1
            if on_progress:
                on_progress({
                    "total_chunks": total,
                    "fetched_chunks": fetched,
                    "cached_chunks": cached,
                    "current": hour_start.isoformat(),
                })

        return {
            "total_chunks": total,
            "fetched_chunks": fetched,
            "cached_chunks": cached,
        }

    async def _fetch_hour(self, symbol: str, hour_start: datetime, use_rth: bool = True) -> list[Candle]:
        """Fetch one hour of 5-second bars by making two 30-minute requests."""
        all_bars: list[Candle] = []

        # Two 30-minute windows per hour
        hour_end = hour_start + timedelta(hours=1)
        for offset_minutes in [0, 30]:
            window_start = hour_start + timedelta(minutes=offset_minutes)
            window_end = window_start + timedelta(minutes=30)

            try:
                bars = await asyncio.to_thread(
                    self._fetch_window, symbol, window_start, window_end, use_rth,
                )
                # IBKR may return bars from a prior RTH session when using
                # useRTH=True.  Keep only bars within the target hour.
                bars = [b for b in bars if hour_start <= b.time < hour_end]
                all_bars.extend(bars)
            except Exception:
                logger.exception("Failed to fetch %s window %s", symbol, window_start.isoformat())

            # Rate-limit spacing
            await asyncio.sleep(REQUEST_DELAY_SECONDS)

        return all_bars

    def _fetch_window(self, symbol: str, window_start: datetime, window_end: datetime, use_rth: bool = True) -> list[Candle]:
        """Fetch a 30-minute window of 5-second bars from IBKR."""
        from app.providers.ibkr import IBKRMarketDataProvider

        if not isinstance(self._provider, IBKRMarketDataProvider):
            return self._mock_window(symbol, window_start, window_end)

        provider = self._provider
        end_dt_str = window_end.strftime("%Y%m%d-%H:%M:%S")

        # Route through the provider's own thread so ib_insync gets
        # a proper event loop and the connection is reused.
        def _do_fetch():
            from ib_insync import Stock

            provider._ensure_connected()
            ib = provider._get_ib()
            contract = Stock(symbol.upper(), provider._exchange, provider._currency)
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_dt_str,
                durationStr=f"{MAX_DURATION_SECONDS} S",
                barSizeSetting="5 secs",
                whatToShow="TRADES",
                useRTH=use_rth,
                formatDate=2,
                keepUpToDate=False,
            )
            if not bars:
                return []
            return [provider._to_candle(symbol, Timeframe.FIVE_SECONDS, bar) for bar in bars]

        return provider._run_on_ib_thread(_do_fetch)

    def _mock_window(self, symbol: str, window_start: datetime, window_end: datetime) -> list[Candle]:
        """Generate synthetic 5-second bars for mock/dev mode."""
        import random

        if window_start.weekday() >= 5:
            return []
        t = window_start.time()
        if t < MARKET_OPEN_UTC or t >= MARKET_CLOSE_UTC:
            return []

        candles: list[Candle] = []
        cursor = window_start
        price = 100.0 + random.uniform(-5, 5)

        while cursor < window_end:
            if cursor.time() >= MARKET_CLOSE_UTC:
                break
            if cursor.time() < MARKET_OPEN_UTC:
                cursor += timedelta(seconds=5)
                continue

            open_price = price + random.uniform(-0.05, 0.05)
            close_price = max(1.0, open_price + random.uniform(-0.1, 0.1))
            high_price = max(open_price, close_price) + random.uniform(0.01, 0.05)
            low_price = min(open_price, close_price) - random.uniform(0.01, 0.05)
            candles.append(Candle(
                symbol=symbol.upper(),
                timeframe=Timeframe.FIVE_SECONDS,
                time=cursor,
                open=round(open_price, 4),
                high=round(high_price, 4),
                low=round(low_price, 4),
                close=round(close_price, 4),
                volume=random.randint(100, 2000),
            ))
            price = close_price
            cursor += timedelta(seconds=5)

        return candles

    async def load_ticks(self, symbol: str, days: int, extended: bool = False) -> list[Candle]:
        """Load stored tick data from the database."""
        required_hours = self._required_hours(days, extended=extended)
        if not required_hours:
            return []

        required_set = set(required_hours)

        async with get_db_context() as session:
            chunks = await self._repo.get_chunks(
                session,
                symbol,
                required_hours[0],
                required_hours[-1],
            )

        # Filter to only the exact required hours (the range query may include extra)
        chunks = [c for c in chunks if c.hour_start in required_set]

        candles = self._repo.chunks_to_candles(chunks)
        # Sort by time — old chunks may contain phantom bars from adjacent days
        candles.sort(key=lambda c: c.time)
        return candles

    async def get_data_status(self, symbol: str) -> dict:
        """Return information about available tick data for a symbol."""
        async with get_db_context() as session:
            available_hours = await self._repo.get_available_hours(session, symbol)
            range_result = await self._repo.get_available_range(session, symbol)

        # Group by date
        dates: dict[str, int] = defaultdict(int)
        for h in available_hours:
            dates[h.isoformat().split("T", 1)[0]] += 1

        return {
            "symbol": symbol.upper(),
            "total_chunks": len(available_hours),
            "dates": [
                {"date": d, "chunks": c, "complete": c >= 7}
                for d, c in sorted(dates.items())
            ],
            "range": {
                "start": range_result[0].isoformat() if range_result else None,
                "end": range_result[1].isoformat() if range_result else None,
            },
        }


# Module-level singleton
tick_fetcher = TickFetcher()
