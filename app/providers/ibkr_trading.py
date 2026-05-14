"""IBKR trading client — places orders and streams real-time 5-second bars.

Uses a separate connection (readonly=False, client_id=102) from the data-fetching
provider so the two can coexist.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Callable, TypeVar

from ib_insync import IB, LimitOrder, MarketOrder, Stock, Trade

from app.models.market_data import Candle, Timeframe
from app.providers.base import MarketDataError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class IBKRTradingClient:
    """Wraps ib_insync for order execution and real-time bar streaming."""

    def __init__(
        self,
        host: str,
        port: int,
        client_id: int,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._exchange = exchange
        self._currency = currency
        self._ib: IB | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ibkr-trade")
        self._executor_thread_id: int | None = None
        self._bar_subscriptions: dict[str, object] = {}  # symbol → bars handle
        self._bar_callbacks: dict[str, Callable] = {}  # symbol → callback
        self._polling_timers: dict[str, threading.Event] = {}  # symbol → stop event
        self._error_callback: Callable[[int, int, str], None] | None = None
        self._connected = False

    @classmethod
    def from_env(cls) -> "IBKRTradingClient":
        return cls(
            host=os.environ.get("IBKR_HOST", "127.0.0.1"),
            port=int(os.environ.get("IBKR_TRADE_PORT", os.environ.get("IBKR_PORT", "4004"))),
            client_id=int(os.environ.get("IBKR_TRADE_CLIENT_ID", "102")),
            exchange=os.environ.get("IBKR_EXCHANGE", "SMART"),
            currency=os.environ.get("IBKR_CURRENCY", "USD"),
        )

    # ── Connection ────────────────────────────────────────────────────

    def connect(self) -> None:
        self._run_on_ib_thread(self._connect_sync)

    def _connect_sync(self) -> None:
        ib = self._get_ib()
        if ib.isConnected():
            self._connected = True
            return
        if not self._port_open():
            raise MarketDataError(
                f"Cannot reach IBKR at {self._host}:{self._port}. "
                "Start TWS / IB Gateway and enable API access."
            )
        ib.connect(self._host, self._port, clientId=self._client_id, readonly=False, timeout=10)
        # Request delayed data (type 3) so accounts without real-time
        # subscriptions still receive free 15-min delayed market data.
        ib.reqMarketDataType(3)
        self._connected = True
        logger.info("IBKR trading client connected (client_id=%s, port=%s, delayed data enabled)", self._client_id, self._port)

    @property
    def on_error(self) -> Callable[[int, int, str], None] | None:
        """Return the current error callback, if any."""
        return self._error_callback

    @on_error.setter
    def on_error(self, callback: Callable[[int, int, str], None] | None) -> None:
        """Set an error callback: ``callback(reqId, errorCode, errorString)``."""
        self._error_callback = callback
        ib = self._get_ib()
        # Remove any existing handler and add new one
        ib.errorEvent.clear()
        if callback:
            def _on_ib_error(reqId, errorCode, errorString, *_args):
                try:
                    callback(reqId, errorCode, errorString)
                except Exception:
                    logger.exception("Error in IBKR error callback")
            ib.errorEvent += _on_ib_error

    def disconnect(self) -> None:
        self._run_on_ib_thread(self._disconnect_sync)

    def _disconnect_sync(self) -> None:
        ib = self._get_ib()
        # Unsubscribe all real-time bars and stop polling
        for sym in list(self._bar_subscriptions):
            self._unsubscribe_bars_sync(sym)
        for sym in list(self._polling_timers):
            self._unsubscribe_bars_sync(sym)
        if ib.isConnected():
            ib.disconnect()
        self._connected = False
        logger.info("IBKR trading client disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ib is not None and self._ib.isConnected()

    # ── Real-time 5-second bars (with polling fallback) ─────────────

    def subscribe_realtime_bars(self, symbol: str, callback: Callable[[Candle], None]) -> None:
        """Subscribe to 5-second OHLCV bars for *symbol*.

        Tries real-time bars first.  If the account lacks market-data
        permissions (error 420), automatically falls back to polling
        ``reqHistoricalData`` every 5 seconds.
        """
        self._run_on_ib_thread(lambda: self._subscribe_bars_sync(symbol, callback))

    def _subscribe_bars_sync(self, symbol: str, callback: Callable[[Candle], None]) -> None:
        sym = symbol.upper()
        if sym in self._bar_subscriptions or sym in self._polling_timers:
            return  # already subscribed

        contract = self._contract(sym)
        ib = self._get_ib()

        # Track whether the subscription was rejected so we can fall back
        error_event = threading.Event()
        got_bar = threading.Event()

        def _on_bar_update(bars, has_new_bar):
            if not has_new_bar or not bars:
                return
            got_bar.set()
            bar = bars[-1]
            candle = Candle(
                symbol=sym,
                timeframe=Timeframe.FIVE_SECONDS,
                time=bar.time.replace(tzinfo=UTC) if bar.time.tzinfo is None else bar.time,
                open=round(float(bar.open_), 4),
                high=round(float(bar.high), 4),
                low=round(float(bar.low), 4),
                close=round(float(bar.close), 4),
                volume=max(0, int(bar.volume or 0)),
            )
            try:
                callback(candle)
            except Exception:
                logger.exception("Error in real-time bar callback for %s", sym)

        def _on_error(reqId, errorCode, errorString, *_args):
            if errorCode == 420:
                error_event.set()

        ib.errorEvent += _on_error
        bars = ib.reqRealTimeBars(contract, barSize=5, whatToShow="TRADES", useRTH=False)
        bars.updateEvent += _on_bar_update

        # Give IBKR a moment to respond with an error or first bar
        ib.sleep(2)
        ib.errorEvent -= _on_error

        if error_event.is_set() and not got_bar.is_set():
            # Real-time rejected — cancel and fall back to polling
            ib.cancelRealTimeBars(bars)
            logger.warning(
                "Real-time bars unavailable for %s — falling back to historical polling",
                sym,
            )
            self._start_polling(sym, callback)
        else:
            self._bar_subscriptions[sym] = bars
            self._bar_callbacks[sym] = callback
            logger.info("Subscribed to real-time bars for %s", sym)

    def _start_polling(self, symbol: str, callback: Callable[[Candle], None]) -> None:
        """Poll ``reqHistoricalData`` every 5 seconds as a fallback."""
        stop_event = threading.Event()
        self._polling_timers[symbol] = stop_event
        self._bar_callbacks[symbol] = callback

        def _poll_loop():
            last_time: datetime | None = None
            while not stop_event.is_set():
                try:
                    candle = self._run_on_ib_thread(lambda: self._poll_latest_bar(symbol))
                    if candle and (last_time is None or candle.time > last_time):
                        last_time = candle.time
                        try:
                            callback(candle)
                        except Exception:
                            logger.exception("Error in polling callback for %s", symbol)
                except Exception:
                    logger.exception("Polling error for %s", symbol)
                stop_event.wait(5)  # sleep 5 seconds between polls

        t = threading.Thread(target=_poll_loop, name=f"poll-{symbol}", daemon=True)
        t.start()
        logger.info("Started historical polling for %s (5s interval)", symbol)

    def is_polling_symbol(self, symbol: str) -> bool:
        """Return True if *symbol* is using polling (delayed data) instead of real-time."""
        return symbol.upper() in self._polling_timers

    def _poll_latest_bar(self, symbol: str) -> Candle | None:
        """Fetch the latest 5-sec bar via ``reqHistoricalData``."""
        sym = symbol.upper()
        contract = self._contract(sym)
        ib = self._get_ib()
        # Use 1800 S (30 min) window to cover the ~15-min delayed data lag
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="1800 S",
            barSizeSetting="5 secs",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=2,
            keepUpToDate=False,
        )
        if not bars:
            return None
        bar = bars[-1]
        bar_time = bar.date
        if isinstance(bar_time, str):
            bar_time = datetime.fromisoformat(bar_time)
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=UTC)
        return Candle(
            symbol=sym,
            timeframe=Timeframe.FIVE_SECONDS,
            time=bar_time,
            open=round(float(getattr(bar, "open")), 4),
            high=round(float(bar.high), 4),
            low=round(float(bar.low), 4),
            close=round(float(bar.close), 4),
            volume=max(0, int(bar.volume or 0)),
        )

    def unsubscribe_realtime_bars(self, symbol: str) -> None:
        self._run_on_ib_thread(lambda: self._unsubscribe_bars_sync(symbol))

    # ── Historical candle fetch (for aggregator warm-up) ──────────────

    _BAR_SIZE_MAP = {
        Timeframe.ONE_MINUTE: "1 min",
        Timeframe.FIVE_MINUTES: "5 mins",
        Timeframe.FIFTEEN_MINUTES: "15 mins",
    }

    def get_historical_candles(
        self, symbol: str, timeframe: Timeframe, duration: str = "1 D"
    ) -> list[Candle]:
        """Fetch historical candles for pre-loading the live aggregator."""
        return self._run_on_ib_thread(
            lambda: self._get_historical_sync(symbol, timeframe, duration)
        )

    def _get_historical_sync(
        self, symbol: str, timeframe: Timeframe, duration: str
    ) -> list[Candle]:
        sym = symbol.upper()
        bar_size = self._BAR_SIZE_MAP.get(timeframe)
        if bar_size is None:
            return []

        contract = self._contract(sym)
        ib = self._get_ib()
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=False,
            formatDate=2,
            keepUpToDate=False,
        )
        if not bars:
            return []

        candles: list[Candle] = []
        for bar in bars:
            bar_time = bar.date
            if isinstance(bar_time, str):
                bar_time = datetime.fromisoformat(bar_time)
            if bar_time.tzinfo is None:
                bar_time = bar_time.replace(tzinfo=UTC)
            candles.append(Candle(
                symbol=sym,
                timeframe=timeframe,
                time=bar_time,
                open=round(float(getattr(bar, "open")), 4),
                high=round(float(bar.high), 4),
                low=round(float(bar.low), 4),
                close=round(float(bar.close), 4),
                volume=max(0, int(bar.volume or 0)),
            ))
        return candles

    def _unsubscribe_bars_sync(self, symbol: str) -> None:
        sym = symbol.upper()
        # Stop real-time bars
        bars = self._bar_subscriptions.pop(sym, None)
        self._bar_callbacks.pop(sym, None)
        if bars is not None:
            ib = self._get_ib()
            ib.cancelRealTimeBars(bars)
            logger.info("Unsubscribed from real-time bars for %s", sym)
        # Stop polling
        stop_event = self._polling_timers.pop(sym, None)
        if stop_event is not None:
            stop_event.set()
            logger.info("Stopped historical polling for %s", sym)

    # ── Connection test ───────────────────────────────────────────────

    def test_symbol(self, symbol: str) -> dict:
        """Test whether market data is available for *symbol*.

        Returns a dict with connection status, last price, and exchange info.
        """
        return self._run_on_ib_thread(lambda: self._test_symbol_sync(symbol))

    def _test_symbol_sync(self, symbol: str) -> dict:
        sym = symbol.upper()
        contract = self._contract(sym)
        ib = self._get_ib()

        # Qualify contract to check it's valid
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            return {
                "symbol": sym,
                "ok": False,
                "error": f"Contract not found for {sym}",
                "exchange": None,
                "last_price": None,
            }
        c = qualified[0]
        exchange = c.primaryExchange or c.exchange

        # Try fetching historical data — works with delayed data
        last_price = None
        has_data = False
        try:
            bars = ib.reqHistoricalData(
                c,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
            )
            if bars:
                last_price = round(float(bars[-1].close), 4)
                has_data = True
        except Exception as exc:
            return {
                "symbol": sym,
                "ok": False,
                "error": str(exc),
                "exchange": exchange,
                "last_price": None,
            }

        if not has_data:
            return {
                "symbol": sym,
                "ok": False,
                "error": "No data available — market may be closed",
                "exchange": exchange,
                "last_price": None,
            }

        return {
            "symbol": sym,
            "ok": True,
            "error": None,
            "exchange": exchange,
            "last_price": last_price,
            "note": "Will use real-time bars if available, otherwise delayed polling",
        }

    # ── Order execution ───────────────────────────────────────────────

    def place_market_order(self, symbol: str, action: str, quantity: float) -> Trade:
        """Place a market order.  *action* is 'BUY' or 'SELL'."""
        return self._run_on_ib_thread(
            lambda: self._place_order_sync(symbol, action, quantity, "market")
        )

    def place_limit_order(
        self, symbol: str, action: str, quantity: float, limit_price: float
    ) -> Trade:
        """Place a limit order at *limit_price*."""
        return self._run_on_ib_thread(
            lambda: self._place_order_sync(symbol, action, quantity, "limit", limit_price)
        )

    def _place_order_sync(
        self,
        symbol: str,
        action: str,
        quantity: float,
        order_type: str,
        limit_price: float | None = None,
    ) -> Trade:
        contract = self._contract(symbol.upper())
        ib = self._get_ib()

        if order_type == "limit" and limit_price is not None:
            order = LimitOrder(action.upper(), quantity, limit_price)
        else:
            order = MarketOrder(action.upper(), quantity)

        trade = ib.placeOrder(contract, order)
        logger.info(
            "Placed %s %s order: %s %.4f shares of %s",
            order_type,
            action,
            "limit @" + str(limit_price) if limit_price else "market",
            quantity,
            symbol,
        )
        return trade

    def cancel_order(self, trade: Trade) -> None:
        self._run_on_ib_thread(lambda: self._get_ib().cancelOrder(trade.order))

    def cancel_all_orders(self) -> None:
        self._run_on_ib_thread(lambda: self._get_ib().reqGlobalCancel())
        logger.warning("Cancelled ALL open orders (global cancel)")

    # ── Account info ──────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        return self._run_on_ib_thread(self._get_positions_sync)

    def _get_positions_sync(self) -> list[dict]:
        ib = self._get_ib()
        ib.reqPositions()
        ib.sleep(0.5)
        return [
            {
                "symbol": p.contract.symbol,
                "shares": float(p.position),
                "avg_cost": float(p.avgCost),
                "market_value": float(p.position * p.avgCost),
            }
            for p in ib.positions()
        ]

    def get_account_summary(self) -> dict:
        return self._run_on_ib_thread(self._get_account_summary_sync)

    def _get_account_summary_sync(self) -> dict:
        ib = self._get_ib()
        summary = ib.accountSummary()
        result = {}
        for item in summary:
            if item.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower"):
                result[item.tag] = float(item.value)
        return result

    # ── Internal helpers ──────────────────────────────────────────────

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
        if self._ib is None:
            self._ib = IB()
        return self._ib

    def _ensure_thread_loop(self) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop is None or loop.is_running():
            asyncio.set_event_loop(asyncio.new_event_loop())

    def _port_open(self) -> bool:
        try:
            with socket.create_connection((self._host, self._port), timeout=2):
                return True
        except OSError:
            return False

    def _contract(self, symbol: str) -> Stock:
        return Stock(symbol.upper(), self._exchange, self._currency)
