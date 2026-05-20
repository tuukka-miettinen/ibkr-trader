"""IBKR trading client — places orders and streams real-time 5-second bars.

Shares a single IB connection with IBKRMarketDataProvider via ibkr_shared,
so only ONE connection to TWS / IB Gateway exists at any time.
"""
from __future__ import annotations

import logging
import math
import os
import threading
from datetime import UTC, datetime
from typing import Callable

from ib_insync import LimitOrder, MarketOrder, Stock, Trade

from app.models.market_data import Candle, Timeframe
from app.providers.base import MarketDataError

logger = logging.getLogger(__name__)


class IBKRTradingClient:
    """Wraps ib_insync for order execution and real-time bar streaming."""

    def __init__(
        self,
        exchange: str = "SMART",
        currency: str = "USD",
    ) -> None:
        self._exchange = exchange
        self._currency = currency
        self._bar_subscriptions: dict[str, object] = {}  # symbol → bars handle
        self._bar_callbacks: dict[str, Callable] = {}  # symbol → callback
        self._polling_timers: dict[str, threading.Event] = {}  # symbol → stop event
        self._error_callback: Callable[[int, int, str], None] | None = None

    @classmethod
    def from_env(cls) -> "IBKRTradingClient":
        return cls(
            exchange=os.environ.get("IBKR_EXCHANGE", "SMART"),
            currency=os.environ.get("IBKR_CURRENCY", "USD"),
        )

    # ── Connection (delegates to shared module) ───────────────────────

    def connect(self) -> None:
        from app.providers.ibkr_shared import run_on_ib_thread, ensure_connected
        run_on_ib_thread(ensure_connected)

    @property
    def on_error(self) -> Callable[[int, int, str], None] | None:
        return self._error_callback

    @on_error.setter
    def on_error(self, callback: Callable[[int, int, str], None] | None) -> None:
        self._error_callback = callback
        ib = self._get_ib()
        ib.errorEvent.clear()
        if callback:
            def _on_ib_error(reqId, errorCode, errorString, *_args):
                try:
                    callback(reqId, errorCode, errorString)
                except Exception:
                    logger.exception("Error in IBKR error callback")
            ib.errorEvent += _on_ib_error

    def disconnect(self) -> None:
        from app.providers.ibkr_shared import run_on_ib_thread
        run_on_ib_thread(self._disconnect_sync)

    def _disconnect_sync(self) -> None:
        # Unsubscribe all real-time bars and stop polling
        for sym in list(self._bar_subscriptions):
            self._unsubscribe_bars_sync(sym)
        for sym in list(self._polling_timers):
            self._unsubscribe_bars_sync(sym)
        # Don't disconnect the shared IB — other components may still use it
        logger.info("IBKR trading client unsubscribed all bars (shared connection kept alive)")

    @property
    def is_connected(self) -> bool:
        from app.providers.ibkr_shared import is_connected
        return is_connected()

    # ── Helpers ───────────────────────────────────────────────────────

    def _get_ib(self):
        from app.providers.ibkr_shared import get_ib
        return get_ib()

    def _run_on_ib_thread(self, fn):
        from app.providers.ibkr_shared import run_on_ib_thread
        return run_on_ib_thread(fn)

    def _contract(self, symbol: str) -> Stock:
        return Stock(symbol.upper(), self._exchange, self._currency)

    # ── Real-time 5-second bars (with polling fallback) ─────────────

    def subscribe_realtime_bars(self, symbol: str, callback: Callable[[Candle], None]) -> None:
        self._run_on_ib_thread(lambda: self._subscribe_bars_sync(symbol, callback))

    def _subscribe_bars_sync(self, symbol: str, callback: Callable[[Candle], None]) -> None:
        sym = symbol.upper()
        if sym in self._bar_subscriptions or sym in self._polling_timers:
            return

        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

        contract = self._contract(sym)
        ib = self._get_ib()

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

        ib.sleep(2)
        ib.errorEvent -= _on_error

        if error_event.is_set() and not got_bar.is_set():
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
                stop_event.wait(5)

        t = threading.Thread(target=_poll_loop, name=f"poll-{symbol}", daemon=True)
        t.start()
        logger.info("Started historical polling for %s (5s interval)", symbol)

    def is_polling_symbol(self, symbol: str) -> bool:
        return symbol.upper() in self._polling_timers

    def _poll_latest_bar(self, symbol: str) -> Candle | None:
        sym = symbol.upper()
        contract = self._contract(sym)
        ib = self._get_ib()
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

    def _unsubscribe_bars_sync(self, symbol: str) -> None:
        sym = symbol.upper()
        bars = self._bar_subscriptions.pop(sym, None)
        self._bar_callbacks.pop(sym, None)
        if bars is not None:
            ib = self._get_ib()
            ib.cancelRealTimeBars(bars)
            logger.info("Unsubscribed from real-time bars for %s", sym)
        stop_event = self._polling_timers.pop(sym, None)
        if stop_event is not None:
            stop_event.set()
            logger.info("Stopped historical polling for %s", sym)

    # ── Historical candle fetch (for aggregator warm-up) ──────────────

    _BAR_SIZE_MAP = {
        Timeframe.ONE_MINUTE: "1 min",
        Timeframe.FIVE_MINUTES: "5 mins",
        Timeframe.FIFTEEN_MINUTES: "15 mins",
    }

    def get_historical_candles(
        self, symbol: str, timeframe: Timeframe, duration: str = "1 D"
    ) -> list[Candle]:
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

        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

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

    # ── Connection test ───────────────────────────────────────────────

    def test_symbol(self, symbol: str) -> dict:
        return self._run_on_ib_thread(lambda: self._test_symbol_sync(symbol))

    def _test_symbol_sync(self, symbol: str) -> dict:
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

        sym = symbol.upper()
        contract = self._contract(sym)
        ib = self._get_ib()

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
        return self._run_on_ib_thread(
            lambda: self._place_order_sync(symbol, action, quantity, "market")
        )

    def place_limit_order(
        self, symbol: str, action: str, quantity: float, limit_price: float
    ) -> Trade:
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
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

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
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

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
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()

        ib = self._get_ib()
        summary = ib.accountSummary()
        result = {}
        for item in summary:
            if item.tag in ("NetLiquidation", "TotalCashValue", "BuyingPower"):
                result[item.tag] = float(item.value)
        return result
