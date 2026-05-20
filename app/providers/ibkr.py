from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from typing import Callable, TypeVar

from ib_insync import Stock

from app.models.market_data import Candle, Timeframe
from app.providers.base import MarketDataError, MarketDataProvider


BAR_SIZE_MAP = {
    Timeframe.FIVE_SECONDS: "5 secs",
    Timeframe.ONE_MINUTE: "1 min",
    Timeframe.THREE_MINUTES: "3 mins",
    Timeframe.FIVE_MINUTES: "5 mins",
    Timeframe.FIFTEEN_MINUTES: "15 mins",
    Timeframe.ONE_HOUR: "1 hour",
}

T = TypeVar("T")


class IBKRMarketDataProvider(MarketDataProvider):
    def __init__(
        self,
        exchange: str,
        currency: str,
        use_rth: bool,
    ) -> None:
        self._exchange = exchange
        self._currency = currency
        self._use_rth = use_rth

    @classmethod
    def from_env(cls) -> "IBKRMarketDataProvider":
        return cls(
            exchange=os.environ.get("IBKR_EXCHANGE", "SMART"),
            currency=os.environ.get("IBKR_CURRENCY", "USD"),
            use_rth=os.environ.get("IBKR_USE_RTH", "false").strip().lower() == "true",
        )

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        from app.providers.ibkr_shared import run_on_ib_thread
        return run_on_ib_thread(lambda: self._get_history_sync(symbol, timeframe, limit))

    def get_history_since(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime | None,
        limit: int,
    ) -> list[Candle]:
        if start_time is None:
            return self.get_history(symbol, timeframe, limit)
        from app.providers.ibkr_shared import run_on_ib_thread
        return run_on_ib_thread(lambda: self._get_history_since_sync(symbol, timeframe, start_time, limit))

    def _get_history_sync(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        contract = self._contract(symbol)
        self._ensure_connected()
        ib = self._get_ib()
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=self._duration_str(limit, timeframe),
            barSizeSetting=BAR_SIZE_MAP[timeframe],
            whatToShow="TRADES",
            useRTH=self._use_rth,
            formatDate=2,
            keepUpToDate=False,
        )

        if not bars:
            raise MarketDataError(f"IBKR returned no historical bars for {symbol} {timeframe}")

        candles = [self._to_candle(symbol, timeframe, bar) for bar in bars]
        return candles[-limit:]

    def _get_history_since_sync(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime,
        limit: int,
    ) -> list[Candle]:
        contract = self._contract(symbol)
        self._ensure_connected()
        ib = self._get_ib()
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=self._duration_since_str(start_time, timeframe),
            barSizeSetting=BAR_SIZE_MAP[timeframe],
            whatToShow="TRADES",
            useRTH=self._use_rth,
            formatDate=2,
            keepUpToDate=False,
        )

        if not bars:
            return []

        candles = [self._to_candle(symbol, timeframe, bar) for bar in bars]
        return [candle for candle in candles if candle.time > start_time][-limit:]

    def get_live_price(self, symbol: str) -> float | None:
        from app.providers.ibkr_shared import run_on_ib_thread
        return run_on_ib_thread(lambda: self._get_live_price_sync(symbol))

    def _get_live_price_sync(self, symbol: str) -> float | None:
        contract = self._contract(symbol)
        self._ensure_connected()
        ib = self._get_ib()
        ticker = ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
        ib.sleep(1)
        price = ticker.marketPrice()
        if price is None or math.isnan(price):
            price = ticker.last if ticker.last and not math.isnan(ticker.last) else ticker.close
        ib.cancelMktData(contract)

        if price is None or math.isnan(price):
            return None

        return round(float(price), 2)

    # ── Helpers (delegate to shared module) ───────────────────────────

    def _get_ib(self):
        from app.providers.ibkr_shared import get_ib
        return get_ib()

    def _ensure_connected(self) -> None:
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

    def _contract(self, symbol: str) -> Stock:
        return Stock(symbol.upper(), self._exchange, self._currency)

    def _to_candle(self, symbol: str, timeframe: Timeframe, bar: object) -> Candle:
        bar_time = getattr(bar, "date")
        if isinstance(bar_time, str):
            bar_time = datetime.fromisoformat(bar_time)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=UTC)

        return Candle(
            symbol=symbol.upper(),
            timeframe=timeframe,
            time=bar_time,
            open=round(float(getattr(bar, "open")), 2),
            high=round(float(getattr(bar, "high")), 2),
            low=round(float(getattr(bar, "low")), 2),
            close=round(float(getattr(bar, "close")), 2),
            volume=max(0, int(getattr(bar, "volume", 0) or 0)),
        )

    def _duration_str(self, limit: int, timeframe: Timeframe) -> str:
        step = self._step(timeframe)
        rth_seconds = 6 * 3600 + 30 * 60
        bars_per_day = max(rth_seconds // int(step.total_seconds()), 1)
        trading_days = math.ceil(max(limit, 1) / bars_per_day)
        calendar_days = math.ceil(trading_days * 7 / 5) + 1
        return self._duration_for_delta(timedelta(days=max(calendar_days, 1)))

    def _duration_since_str(self, start_time: datetime, timeframe: Timeframe) -> str:
        from app.models.market_data import utc_now
        current_time = utc_now()
        normalized_start = start_time if start_time.tzinfo is not None else start_time.replace(tzinfo=UTC)
        total = max(current_time - normalized_start, self._step(timeframe)) + self._step(timeframe)
        return self._duration_for_delta(total)

    def _duration_for_delta(self, total: timedelta) -> str:
        seconds = int(total.total_seconds())
        if total <= timedelta(days=1):
            return f"{max(seconds, 1800)} S"
        if total <= timedelta(days=30):
            return f"{max(total.days, 1)} D"
        if total <= timedelta(days=365):
            months = max(total.days // 30, 1)
            if months <= 12:
                return f"{months} M"
        years = max(total.days // 365, 1)
        return f"{years} Y"

    def _step(self, timeframe: Timeframe) -> timedelta:
        if timeframe == Timeframe.FIVE_SECONDS:
            return timedelta(seconds=5)
        if timeframe == Timeframe.ONE_MINUTE:
            return timedelta(minutes=1)
        if timeframe == Timeframe.THREE_MINUTES:
            return timedelta(minutes=3)
        if timeframe == Timeframe.FIVE_MINUTES:
            return timedelta(minutes=5)
        if timeframe == Timeframe.FIFTEEN_MINUTES:
            return timedelta(minutes=15)
        return timedelta(hours=1)
