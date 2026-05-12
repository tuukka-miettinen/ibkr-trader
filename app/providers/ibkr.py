from __future__ import annotations

import asyncio
import math
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Callable, TypeVar

from ib_insync import IB, Stock

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
        host: str,
        port: int,
        client_id: int,
        exchange: str,
        currency: str,
        use_rth: bool,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._exchange = exchange
        self._currency = currency
        self._use_rth = use_rth
        self._ib: IB | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ibkr-provider")
        self._executor_thread_id: int | None = None

    @classmethod
    def from_env(cls) -> "IBKRMarketDataProvider":
        return cls(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_PORT", "7497")),
            client_id=int(os.environ.get("IBKR_CLIENT_ID", "101")),
            exchange=os.environ.get("IBKR_EXCHANGE", "SMART"),
            currency=os.environ.get("IBKR_CURRENCY", "USD"),
            use_rth=os.environ.get("IBKR_USE_RTH", "false").strip().lower() == "true",
        )

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        return self._run_on_ib_thread(lambda: self._get_history_sync(symbol, timeframe, limit))

    def get_history_since(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime | None,
        limit: int,
    ) -> list[Candle]:
        if start_time is None:
            return self.get_history(symbol, timeframe, limit)
        return self._run_on_ib_thread(lambda: self._get_history_since_sync(symbol, timeframe, start_time, limit))

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
        return self._run_on_ib_thread(lambda: self._get_live_price_sync(symbol))

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

    def _run_on_ib_thread(self, fn: Callable[[], T]) -> T:
        if threading.get_ident() == self._executor_thread_id:
            return fn()
        future = self._executor.submit(self._run_with_loop, fn)
        return future.result()

    def _run_with_loop(self, fn: Callable[[], T]) -> T:
        self._executor_thread_id = threading.get_ident()
        self._ensure_thread_loop()
        return fn()

    def _get_ib(self) -> IB:
        """Get thread-local IB instance, creating one if needed."""
        if self._ib is None:
            self._ib = IB()
        return self._ib

    def _ensure_thread_loop(self) -> None:
        """Guarantee the calling thread owns a fresh, non-running asyncio event loop.

        uvloop (used by uvicorn) leaks into ThreadPoolExecutor worker threads via the
        event-loop policy.  ib_insync calls asyncio.get_event_loop() and, if the
        loop reports is_running()==True, tries to schedule a Task on it instead of
        calling run_until_complete() — causing an immediate disconnect.
        """
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is None or loop.is_running():
            asyncio.set_event_loop(asyncio.new_event_loop())

    def _ensure_connected(self) -> None:
        ib = self._get_ib()
        if ib.isConnected():
            return
        if not self._port_open():
            raise MarketDataError(
                f"Failed to connect to IBKR at {self._host}:{self._port}. "
                "Start TWS or IB Gateway, enable API access, and verify market-data subscriptions."
            )
        try:
            ib.connect(self._host, self._port, clientId=self._client_id, readonly=True, timeout=5)
        except Exception as exc:  # pragma: no cover - connection error path depends on local IBKR runtime
            raise MarketDataError(
                f"Failed to connect to IBKR at {self._host}:{self._port}. "
                "Start TWS or IB Gateway, enable API access, and verify market-data subscriptions."
            ) from exc

    def _port_open(self) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=2):
                return True
        except OSError:
            return False

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
        # IBKR durationStr units: S=seconds, D=days, W=weeks, M=months, Y=years
        # Convert bar count to calendar days, not naive wall-clock time.
        # RTH is 6.5 hours; bars only appear during trading hours, so
        # N bars of 5m ≠ N*5 minutes of calendar time.
        step = self._step(timeframe)
        rth_seconds = 6 * 3600 + 30 * 60  # 23400s = 6.5 hours
        bars_per_day = max(rth_seconds // int(step.total_seconds()), 1)
        trading_days = math.ceil(max(limit, 1) / bars_per_day)
        # 5 trading days ≈ 7 calendar days; +1 buffer for partial days/holidays
        calendar_days = math.ceil(trading_days * 7 / 5) + 1
        return self._duration_for_delta(timedelta(days=max(calendar_days, 1)))

    def _duration_since_str(self, start_time: datetime, timeframe: Timeframe) -> str:
        current_time = datetime.now(tz=UTC)
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
