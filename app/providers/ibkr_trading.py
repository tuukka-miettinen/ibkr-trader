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
from typing import Callable, Literal

from ib_insync import LimitOrder, MarketOrder, Stock, Trade

from app.models.market_data import Candle, Timeframe
from app.providers.base import MarketDataError

logger = logging.getLogger(__name__)

MarketDataMode = Literal["realtime", "delayed"]


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

    def set_market_data_mode(self, mode: MarketDataMode) -> None:
        self._run_on_ib_thread(lambda: self._set_market_data_mode_sync(mode))

    def _set_market_data_mode_sync(self, mode: MarketDataMode) -> None:
        from app.providers.ibkr_shared import ensure_connected, set_market_data_mode
        ensure_connected()
        set_market_data_mode(mode)

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

    def _qualified_contract(self, symbol: str) -> Stock:
        sym = symbol.upper()
        contract = self._contract(sym)
        ib = self._get_ib()
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise MarketDataError(f"Contract not found for {sym}")
        return qualified[0]

    # ── 5-second bars ───────────────────────────────────────────────

    def subscribe_realtime_bars(
        self,
        symbol: str,
        callback: Callable[[Candle], None],
        *,
        market_data_mode: MarketDataMode = "realtime",
    ) -> bool:
        return self._run_on_ib_thread(
            lambda: self._subscribe_bars_sync(symbol, callback, market_data_mode=market_data_mode)
        )

    def _subscribe_bars_sync(
        self,
        symbol: str,
        callback: Callable[[Candle], None],
        *,
        market_data_mode: MarketDataMode = "realtime",
    ) -> bool:
        sym = symbol.upper()
        if sym in self._bar_subscriptions or sym in self._polling_timers:
            return sym in self._polling_timers

        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()
        self._set_market_data_mode_sync(market_data_mode)

        if market_data_mode == "delayed":
            self._start_polling(sym, callback)
            return True

        contract = self._qualified_contract(sym)
        ib = self._get_ib()

        error_event = threading.Event()
        got_bar = threading.Event()
        latest_error: dict[str, str | int | None] = {"code": None, "message": None}
        request_req_id: dict[str, int | None] = {"value": None}

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
            if errorCode not in {420, 354, 10167}:
                return
            tracked_req_id = request_req_id["value"]
            if tracked_req_id is not None and reqId not in {-1, tracked_req_id}:
                return
            latest_error["code"] = errorCode
            latest_error["message"] = errorString
            error_event.set()

        ib.errorEvent += _on_error
        bars = ib.reqRealTimeBars(contract, barSize=5, whatToShow="TRADES", useRTH=False)
        request_req_id["value"] = getattr(bars, "reqId", None)
        logger.info(
            "Requested real-time bars for %s (reqId=%s, conId=%s, exchange=%s, primaryExchange=%s)",
            sym,
            request_req_id["value"],
            getattr(contract, "conId", None),
            getattr(contract, "exchange", None),
            getattr(contract, "primaryExchange", None),
        )
        bars.updateEvent += _on_bar_update

        ib.sleep(2)
        ib.errorEvent -= _on_error

        if error_event.is_set() and not got_bar.is_set():
            ib.cancelRealTimeBars(bars)
            error_code = latest_error["code"]
            error_message = latest_error["message"] or "unknown error"
            raise MarketDataError(
                f"Real-time market data unavailable for {sym} "
                f"(IBKR error {error_code}: {error_message}; "
                f"reqId={request_req_id['value']}, conId={getattr(contract, 'conId', None)}, "
                f"exchange={getattr(contract, 'exchange', None)}, primaryExchange={getattr(contract, 'primaryExchange', None)}). "
                "No delayed fallback was attempted."
            )

        self._bar_subscriptions[sym] = bars
        self._bar_callbacks[sym] = callback
        logger.info("Subscribed to real-time bars for %s", sym)
        return False

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
        self._set_market_data_mode_sync("delayed")
        contract = self._qualified_contract(sym)
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
        self,
        symbol: str,
        timeframe: Timeframe,
        duration: str = "1 D",
        *,
        market_data_mode: MarketDataMode = "realtime",
    ) -> list[Candle]:
        return self._run_on_ib_thread(
            lambda: self._get_historical_sync(symbol, timeframe, duration, market_data_mode=market_data_mode)
        )

    def _get_historical_sync(
        self,
        symbol: str,
        timeframe: Timeframe,
        duration: str,
        *,
        market_data_mode: MarketDataMode = "realtime",
    ) -> list[Candle]:
        sym = symbol.upper()
        bar_size = self._BAR_SIZE_MAP.get(timeframe)
        if bar_size is None:
            return []

        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()
        self._set_market_data_mode_sync(market_data_mode)

        contract = self._qualified_contract(sym)
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

    def test_symbol(self, symbol: str, market_data_mode: MarketDataMode = "realtime") -> dict:
        return self._run_on_ib_thread(lambda: self._test_symbol_sync(symbol, market_data_mode))

    def debug_market_data(self, symbol: str, market_data_mode: MarketDataMode = "realtime") -> dict:
        return self._run_on_ib_thread(lambda: self._debug_market_data_sync(symbol, market_data_mode))

    def _test_symbol_sync(self, symbol: str, market_data_mode: MarketDataMode) -> dict:
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()
        self._set_market_data_mode_sync(market_data_mode)

        sym = symbol.upper()
        ib = self._get_ib()

        try:
            c = self._qualified_contract(sym)
        except MarketDataError as exc:
            return {
                "symbol": sym,
                "ok": False,
                "error": str(exc),
                "exchange": None,
                "last_price": None,
                "market_data_mode": market_data_mode,
            }
        exchange = c.primaryExchange or c.exchange

        if market_data_mode == "delayed":
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
            except Exception as exc:
                return {
                    "symbol": sym,
                    "ok": False,
                    "error": str(exc),
                    "exchange": exchange,
                    "last_price": None,
                    "market_data_mode": market_data_mode,
                }

            if not bars:
                return {
                    "symbol": sym,
                    "ok": False,
                    "error": "Delayed historical data unavailable for this symbol",
                    "exchange": exchange,
                    "last_price": None,
                    "market_data_mode": market_data_mode,
                }

            return {
                "symbol": sym,
                "ok": True,
                "error": None,
                "exchange": exchange,
                "last_price": round(float(bars[-1].close), 4),
                "market_data_mode": market_data_mode,
                "note": "Delayed market data is enabled; orders stay paper-only.",
            }

        error_event = threading.Event()
        latest_error: dict[str, str | int | None] = {"code": None, "message": None}
        request_req_id: dict[str, int | None] = {"value": None}

        def _on_error(reqId, errorCode, errorString, *_args):
            if errorCode not in {420, 354, 10167}:
                return
            tracked_req_id = request_req_id["value"]
            if tracked_req_id is not None and reqId not in {-1, tracked_req_id}:
                return
            latest_error["code"] = errorCode
            latest_error["message"] = errorString
            error_event.set()

        ib.errorEvent += _on_error
        bars = ib.reqRealTimeBars(c, barSize=5, whatToShow="TRADES", useRTH=False)
        request_req_id["value"] = getattr(bars, "reqId", None)
        logger.info(
            "Testing real-time bars for %s (reqId=%s, conId=%s, exchange=%s, primaryExchange=%s)",
            sym,
            request_req_id["value"],
            getattr(c, "conId", None),
            getattr(c, "exchange", None),
            getattr(c, "primaryExchange", None),
        )
        ib.sleep(2)
        ib.cancelRealTimeBars(bars)
        ib.errorEvent -= _on_error

        if error_event.is_set():
            return {
                "symbol": sym,
                "ok": False,
                "error": (
                    f"Real-time market data unavailable for {sym} "
                    f"(IBKR error {latest_error['code']}: {latest_error['message']}; "
                    f"reqId={request_req_id['value']}, conId={getattr(c, 'conId', None)}, "
                    f"exchange={getattr(c, 'exchange', None)}, primaryExchange={getattr(c, 'primaryExchange', None)}). "
                    "No delayed fallback will be used."
                ),
                "exchange": exchange,
                "last_price": None,
                "market_data_mode": market_data_mode,
            }

        return {
            "symbol": sym,
            "ok": True,
            "error": None,
            "exchange": exchange,
            "last_price": None,
            "market_data_mode": market_data_mode,
            "note": (
                "Real-time market data request accepted. "
                f"reqId={request_req_id['value']}, conId={getattr(c, 'conId', None)}, "
                f"exchange={getattr(c, 'exchange', None)}, primaryExchange={getattr(c, 'primaryExchange', None)}. "
                "No delayed fallback will be used."
            ),
        }

    def _debug_market_data_sync(self, symbol: str, market_data_mode: MarketDataMode) -> dict:
        from app.providers.ibkr_shared import ensure_connected
        ensure_connected()
        self._set_market_data_mode_sync(market_data_mode)

        sym = symbol.upper()
        ib = self._get_ib()

        result: dict[str, object] = {
            "symbol": sym,
            "market_data_mode": market_data_mode,
            "contract": {"ok": False},
            "historical_1m": {"ok": False},
            "realtime_bars": {"ok": False},
        }

        try:
            contract = self._qualified_contract(sym)
        except MarketDataError as exc:
            result["contract"] = {"ok": False, "error": str(exc)}
            result["historical_1m"] = {"ok": False, "error": "Skipped because contract qualification failed"}
            result["realtime_bars"] = {"ok": False, "error": "Skipped because contract qualification failed"}
            return result

        result["contract"] = {
            "ok": True,
            "conId": getattr(contract, "conId", None),
            "exchange": getattr(contract, "exchange", None),
            "primaryExchange": getattr(contract, "primaryExchange", None),
            "localSymbol": getattr(contract, "localSymbol", None),
            "tradingClass": getattr(contract, "tradingClass", None),
        }

        hist_error = threading.Event()
        hist_latest_error: dict[str, str | int | None] = {"code": None, "message": None}
        hist_req_id: dict[str, int | None] = {"value": None}

        def _on_hist_error(reqId, errorCode, errorString, *_args):
            tracked_req_id = hist_req_id["value"]
            if tracked_req_id is not None and reqId not in {-1, tracked_req_id}:
                return
            if errorCode not in {162, 200, 420, 354, 10167}:
                return
            hist_latest_error["code"] = errorCode
            hist_latest_error["message"] = errorString
            hist_error.set()

        ib.errorEvent += _on_hist_error
        historical = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="1 D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=2,
            keepUpToDate=False,
        )
        hist_req_id["value"] = getattr(historical, "reqId", None)
        ib.sleep(1)
        ib.errorEvent -= _on_hist_error

        if hist_error.is_set():
            result["historical_1m"] = {
                "ok": False,
                "reqId": hist_req_id["value"],
                "error": f"IBKR error {hist_latest_error['code']}: {hist_latest_error['message']}",
            }
        elif not historical:
            result["historical_1m"] = {
                "ok": False,
                "reqId": hist_req_id["value"],
                "error": "No bars returned",
            }
        else:
            last_bar = historical[-1]
            last_time = last_bar.date
            if isinstance(last_time, str):
                last_time = datetime.fromisoformat(last_time)
            if isinstance(last_time, datetime) and last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=UTC)
            result["historical_1m"] = {
                "ok": True,
                "reqId": hist_req_id["value"],
                "bars": len(historical),
                "last_time": last_time.isoformat() if hasattr(last_time, "isoformat") else str(last_time),
                "last_close": round(float(last_bar.close), 4),
            }

        if market_data_mode == "delayed":
            result["realtime_bars"] = {
                "ok": False,
                "error": "Skipped in delayed mode",
            }
            return result

        rt_error = threading.Event()
        got_bar = threading.Event()
        rt_latest_error: dict[str, str | int | None] = {"code": None, "message": None}
        rt_req_id: dict[str, int | None] = {"value": None}

        def _on_rt_error(reqId, errorCode, errorString, *_args):
            tracked_req_id = rt_req_id["value"]
            if tracked_req_id is not None and reqId not in {-1, tracked_req_id}:
                return
            if errorCode not in {420, 354, 10167}:
                return
            rt_latest_error["code"] = errorCode
            rt_latest_error["message"] = errorString
            rt_error.set()

        def _on_rt_update(bars, has_new_bar):
            if has_new_bar and bars:
                got_bar.set()

        ib.errorEvent += _on_rt_error
        realtime = ib.reqRealTimeBars(contract, barSize=5, whatToShow="TRADES", useRTH=False)
        rt_req_id["value"] = getattr(realtime, "reqId", None)
        realtime.updateEvent += _on_rt_update
        ib.sleep(2)
        try:
            ib.cancelRealTimeBars(realtime)
        finally:
            ib.errorEvent -= _on_rt_error

        if rt_error.is_set() and not got_bar.is_set():
            result["realtime_bars"] = {
                "ok": False,
                "reqId": rt_req_id["value"],
                "error": f"IBKR error {rt_latest_error['code']}: {rt_latest_error['message']}",
            }
        else:
            first_time = realtime[0].time if realtime else None
            last_time = realtime[-1].time if realtime else None
            if isinstance(first_time, datetime) and first_time.tzinfo is None:
                first_time = first_time.replace(tzinfo=UTC)
            if isinstance(last_time, datetime) and last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=UTC)
            result["realtime_bars"] = {
                "ok": True,
                "reqId": rt_req_id["value"],
                "bar_count": len(realtime),
                "first_bar_time": first_time.isoformat() if hasattr(first_time, "isoformat") else None,
                "last_bar_time": last_time.isoformat() if hasattr(last_time, "isoformat") else None,
                "received_bar": got_bar.is_set(),
            }

        return result

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
