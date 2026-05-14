"""Live trading engine — runs tick strategies against real-time IBKR data.

Reuses CandleAggregator & TickState from the backtest engine so that the same
``on_tick(state)`` strategies work identically in live and backtesting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.db.database import get_db_context
from app.db.live import LiveRepository
from app.db.models import LiveSession, LiveSessionSymbol
from app.models.market_data import Candle, Timeframe
from app.providers.ibkr_trading import IBKRTradingClient
from app.strategy.sandbox import compile_tick_script
from app.strategy.tick_backtest import CandleAggregator, PositionInfo, TickState

logger = logging.getLogger(__name__)

CANDLE_TIMEFRAMES = [Timeframe.ONE_MINUTE, Timeframe.FIVE_MINUTES, Timeframe.FIFTEEN_MINUTES]


@dataclass
class SymbolRuntime:
    """In-memory state for a single symbol within a live session."""
    session_symbol_id: str
    symbol: str
    algorithm_script: str
    on_tick_fn: callable
    aggregator: CandleAggregator
    position_entries: list[dict] = field(default_factory=list)
    position_shares: float = 0.0
    position_cost: float = 0.0
    cash: float = 10000.0
    allocated_capital: float = 10000.0
    position_size: float = 1000.0
    max_entries: int = 5
    realized_pnl: float = 0.0
    daily_realized_pnl: float = 0.0
    strategy_state: dict = field(default_factory=dict)
    last_price: float = 0.0
    tick_count: int = 0
    last_tick_time: datetime | None = None
    delayed: bool = False


class LiveTradingEngine:
    """Singleton engine that manages all live trading sessions."""

    def __init__(self) -> None:
        self._client: IBKRTradingClient | None = None
        self._repo = LiveRepository()
        self._sessions: dict[str, dict] = {}  # session_id → {session, symbols: {sym: SymbolRuntime}}
        self._ws_subscribers: dict[str, list[asyncio.Queue]] = {}  # session_id → list of queues
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Public API ────────────────────────────────────────────────────

    async def start_session(self, session_id: str) -> None:
        """Start a live trading session — connect IBKR, subscribe to bars."""
        if session_id in self._sessions:
            raise RuntimeError(f"Session {session_id} is already running")

        self._loop = asyncio.get_event_loop()

        # Load session + symbols from DB
        async with get_db_context() as db:
            live_session = await self._repo.get_session(db, session_id)
            if live_session is None:
                raise ValueError(f"Session {session_id} not found")
            if live_session.status not in ("created", "stopped", "running"):
                raise RuntimeError(f"Session is in '{live_session.status}' state, cannot start")

            session_symbols = await self._repo.get_session_symbols(db, session_id)
            if not session_symbols:
                raise ValueError("Session has no symbols configured")

            # Load algorithm scripts
            from app.db.strategies import StrategyRepository
            strat_repo = StrategyRepository()
            symbol_runtimes: dict[str, SymbolRuntime] = {}
            for ss in session_symbols:
                algo = await strat_repo.get_algorithm(db, ss.algorithm_id)
                if algo is None:
                    raise ValueError(f"Algorithm {ss.algorithm_id} not found for symbol {ss.symbol}")

                on_tick_fn = compile_tick_script(algo.script)

                # Restore position entries from DB
                entries = await self._repo.get_position_entries(db, ss.id)
                position_entries = [
                    {
                        "time": e.time.isoformat(),
                        "price": e.price,
                        "shares": e.shares,
                        "cost": e.cost,
                    }
                    for e in entries
                ]

                # Restore strategy state
                strategy_state = {}
                if ss.strategy_state_json:
                    try:
                        strategy_state = ss.strategy_state_json
                    except Exception:
                        strategy_state = {}

                runtime = SymbolRuntime(
                    session_symbol_id=ss.id,
                    symbol=ss.symbol,
                    algorithm_script=algo.script,
                    on_tick_fn=on_tick_fn,
                    aggregator=CandleAggregator(ss.symbol, CANDLE_TIMEFRAMES),
                    position_entries=position_entries,
                    position_shares=ss.current_shares,
                    position_cost=ss.current_cost,
                    cash=ss.cash_remaining,
                    allocated_capital=ss.allocated_capital,
                    position_size=ss.position_size,
                    max_entries=ss.max_entries,
                    realized_pnl=ss.realized_pnl,
                    daily_realized_pnl=ss.daily_realized_pnl,
                    strategy_state=strategy_state,
                    last_price=ss.last_price or 0.0,
                )
                symbol_runtimes[ss.symbol] = runtime

        # Connect IBKR trading client
        if self._client is None:
            self._client = IBKRTradingClient.from_env()

        if not self._client.is_connected:
            try:
                self._client.connect()
            except Exception as exc:
                async with get_db_context() as db:
                    await self._repo.update_session_status(
                        db, session_id, "error", error_message=str(exc)
                    )
                raise

        # Forward IBKR errors to the WebSocket so the UI can display them
        def _ibkr_error_handler(req_id: int, error_code: int, error_string: str) -> None:
            # Ignore non-critical / informational messages
            if 2100 <= error_code <= 2200:  # connection status
                return
            if error_code == 162:  # "no data" — market closed or no recent trades
                logger.debug("IBKR no data (reqId=%d): %s", req_id, error_string)
                return
            if error_code == 300:  # "can't find EId" — follows after 420
                return
            if error_code == 420:  # "no real-time permissions" — handled by polling fallback
                return
            logger.warning("IBKR error %d (reqId=%d): %s", error_code, req_id, error_string)
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._broadcast(session_id, {
                        "type": "error",
                        "message": f"IBKR error {error_code}: {error_string}",
                    }),
                    self._loop,
                )

        self._client.on_error = _ibkr_error_handler

        # Store session state
        self._sessions[session_id] = {
            "session": live_session,
            "symbols": symbol_runtimes,
            "order_type": live_session.order_type,
            "max_daily_loss": live_session.max_daily_loss,
        }

        # Subscribe to real-time bars for each symbol
        for sym, runtime in symbol_runtimes.items():
            # Pre-load historical candles so indicators (RSI, VWAP) have
            # warm-up data from the start instead of waiting 30+ minutes.
            for tf in CANDLE_TIMEFRAMES:
                try:
                    historical = self._client.get_historical_candles(sym, tf, "1 D")
                    if historical:
                        runtime.aggregator.seed_candles(tf, historical)
                        logger.info(
                            "Seeded %d %s candles for %s", len(historical), tf, sym,
                        )
                except Exception:
                    logger.warning("Could not pre-load %s candles for %s", tf, sym, exc_info=True)

            self._client.subscribe_realtime_bars(
                sym, lambda candle, _sid=session_id, _sym=sym: self._on_tick(_sid, _sym, candle)
            )
            # Check if the symbol fell back to polling (delayed data)
            if self._client.is_polling_symbol(sym):
                runtime.delayed = True
                logger.info("Symbol %s is using delayed data (polling)", sym)

        # Mark session as running
        async with get_db_context() as db:
            await self._repo.update_session_status(db, session_id, "running")

        delayed_syms = [s for s, rt in symbol_runtimes.items() if rt.delayed]
        await self._broadcast(session_id, {
            "type": "status",
            "status": "running",
            "message": f"Session started with {len(symbol_runtimes)} symbol(s)"
                       + (f" ({len(delayed_syms)} using delayed data)" if delayed_syms else ""),
            "symbols": list(symbol_runtimes.keys()),
            "delayed_symbols": delayed_syms,
        })

        logger.info("Live session %s started with symbols: %s", session_id, list(symbol_runtimes.keys()))

    async def stop_session(self, session_id: str, *, reason: str = "user") -> None:
        """Gracefully stop a live session."""
        state = self._sessions.get(session_id)
        if state is None:
            return

        # Unsubscribe bars
        for sym in state["symbols"]:
            try:
                self._client.unsubscribe_realtime_bars(sym)
            except Exception:
                logger.exception("Error unsubscribing %s", sym)

        # Persist final state
        await self._persist_all_symbols(session_id)

        # Update DB status
        status = "stopped" if reason == "user" else "error"
        error_msg = None if reason == "user" else reason
        async with get_db_context() as db:
            await self._repo.update_session_status(
                db, session_id, status, error_message=error_msg
            )

        await self._broadcast(session_id, {
            "type": "status",
            "status": status,
            "message": f"Session stopped: {reason}",
        })

        del self._sessions[session_id]
        logger.info("Live session %s stopped (%s)", session_id, reason)

    async def kill_session(self, session_id: str) -> None:
        """Emergency kill — cancel all orders and stop immediately."""
        if self._client and self._client.is_connected:
            try:
                self._client.cancel_all_orders()
            except Exception:
                logger.exception("Error cancelling orders during kill")

        await self.stop_session(session_id, reason="kill switch activated")

    def get_session_state(self, session_id: str) -> dict | None:
        """Return current in-memory state for a session (for API)."""
        state = self._sessions.get(session_id)
        if state is None:
            return None

        symbols = {}
        total_pnl = 0.0
        total_value = 0.0
        for sym, rt in state["symbols"].items():
            unrealized = (rt.position_shares * rt.last_price - rt.position_cost) if rt.position_shares > 0 else 0.0
            market_value = rt.position_shares * rt.last_price if rt.position_shares > 0 else 0.0
            portfolio_value = rt.cash + market_value
            total_pnl += rt.realized_pnl + unrealized
            total_value += portfolio_value
            symbols[sym] = {
                "symbol": sym,
                "last_price": round(rt.last_price, 4),
                "position_shares": round(rt.position_shares, 8),
                "position_cost": round(rt.position_cost, 4),
                "avg_price": round(rt.position_cost / rt.position_shares, 4) if rt.position_shares > 0 else 0.0,
                "unrealized_pnl": round(unrealized, 4),
                "realized_pnl": round(rt.realized_pnl, 4),
                "daily_realized_pnl": round(rt.daily_realized_pnl, 4),
                "cash": round(rt.cash, 4),
                "portfolio_value": round(portfolio_value, 4),
                "tick_count": rt.tick_count,
                "last_tick_time": rt.last_tick_time.isoformat() if rt.last_tick_time else None,
                "position_entries": rt.position_entries,
                "delayed": rt.delayed,
            }
        return {
            "session_id": session_id,
            "status": "running",
            "symbols": symbols,
            "total_pnl": round(total_pnl, 4),
            "total_value": round(total_value, 4),
        }

    def is_session_running(self, session_id: str) -> bool:
        return session_id in self._sessions

    # ── WebSocket subscription ────────────────────────────────────────

    def subscribe_ws(self, session_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._ws_subscribers.setdefault(session_id, []).append(q)
        return q

    def unsubscribe_ws(self, session_id: str, queue: asyncio.Queue) -> None:
        subs = self._ws_subscribers.get(session_id, [])
        if queue in subs:
            subs.remove(queue)

    async def _broadcast(self, session_id: str, event: dict) -> None:
        for q in self._ws_subscribers.get(session_id, []):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # drop events if consumer is slow

    # ── Tick processing (called from IB thread) ───────────────────────

    def _on_tick(self, session_id: str, symbol: str, candle: Candle) -> None:
        """Called on the IB event-loop thread when a new 5s bar arrives."""
        if self._loop is None:
            return
        # Schedule the async processing on the main event loop
        asyncio.run_coroutine_threadsafe(
            self._process_tick(session_id, symbol, candle), self._loop
        )

    async def _process_tick(self, session_id: str, symbol: str, candle: Candle) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        rt = state["symbols"].get(symbol)
        if rt is None:
            return

        rt.last_price = candle.close
        rt.tick_count += 1
        rt.last_tick_time = candle.time

        if rt.tick_count == 1:
            logger.info(
                "[%s] First tick received for %s: price=%.4f time=%s",
                session_id[:8], symbol, candle.close, candle.time,
            )

        # Push through aggregator
        closed = rt.aggregator.push(candle)

        # Log closed candles
        for tf, closed_candle in closed.items():
            if closed_candle is not None:
                logger.info(
                    "[%s] %s candle closed for %s: O=%.2f H=%.2f L=%.2f C=%.2f V=%d",
                    session_id[:8], tf, symbol,
                    closed_candle.open, closed_candle.high, closed_candle.low,
                    closed_candle.close, closed_candle.volume,
                )

        # Broadcast closed 1m candle for charts
        closed_1m = closed.get(Timeframe.ONE_MINUTE)
        if closed_1m:
            await self._broadcast(session_id, {
                "type": "candle",
                "symbol": symbol,
                "candle": {
                    "time": closed_1m.time.isoformat() if hasattr(closed_1m.time, "isoformat") else str(closed_1m.time),
                    "open": closed_1m.open,
                    "high": closed_1m.high,
                    "low": closed_1m.low,
                    "close": closed_1m.close,
                    "volume": closed_1m.volume,
                },
            })

        # Build position info (same as backtest)
        position_info = None
        if rt.position_shares > 0:
            position_info = PositionInfo(
                shares=round(rt.position_shares, 8),
                avg_price=round(rt.position_cost / rt.position_shares, 4) if rt.position_shares else 0.0,
                total_cost=round(rt.position_cost, 4),
                entries=rt.position_entries.copy(),
                unrealized_pnl=round(rt.position_shares * candle.close - rt.position_cost, 4),
            )

        market_value = rt.position_shares * candle.close if rt.position_shares > 0 else 0.0
        tick_state = TickState(
            tick=candle,
            candles={tf: rt.aggregator.completed_candles(tf) for tf in CANDLE_TIMEFRAMES},
            current_candles={tf: rt.aggregator.current_candle(tf) for tf in CANDLE_TIMEFRAMES},
            closed=closed,
            position=position_info,
            cash=round(rt.cash, 4),
            portfolio_value=round(rt.cash + market_value, 4),
            strategy=rt.strategy_state,
        )

        # Call strategy
        try:
            result = rt.on_tick_fn(tick_state)
        except Exception:
            logger.exception("Strategy error for %s in session %s", symbol, session_id)
            result = None

        if isinstance(result, dict):
            signal = result.get("signal")
            if signal in ("buy", "sell"):
                logger.info(
                    "[%s] Strategy signal: %s for %s (tick #%d, price=%.4f, position=%s)",
                    session_id[:8], signal, symbol, rt.tick_count, candle.close,
                    f"{rt.position_shares:.4f} shares" if rt.position_shares > 0 else "flat",
                )
                await self._execute_signal(session_id, symbol, signal, candle, result, state)

        # Broadcast every tick so the chart can show 5-second granularity
        unrealized = (rt.position_shares * candle.close - rt.position_cost) if rt.position_shares > 0 else 0.0
        await self._broadcast(session_id, {
            "type": "tick",
            "symbol": symbol,
            "time": candle.time.isoformat(),
            "open": round(candle.open, 4),
            "high": round(candle.high, 4),
            "low": round(candle.low, 4),
            "close": round(candle.close, 4),
            "price": round(candle.close, 4),
            "volume": candle.volume,
            "position_shares": round(rt.position_shares, 8),
            "unrealized_pnl": round(unrealized, 4),
            "realized_pnl": round(rt.realized_pnl, 4),
            "cash": round(rt.cash, 4),
            "portfolio_value": round(rt.cash + market_value, 4),
                "tick_count": rt.tick_count,
            })

        # Persist every 60 ticks (≈ 5 minutes) to avoid excessive DB writes
        if rt.tick_count % 60 == 0:
            await self._persist_symbol(session_id, symbol)

    async def _execute_signal(
        self,
        session_id: str,
        symbol: str,
        signal: str,
        candle: Candle,
        result: dict,
        state: dict,
    ) -> None:
        rt = state["symbols"][symbol]
        order_type = state["order_type"]

        if signal == "buy":
            size_frac = result.get("size", 1.0)
            buy_amount = rt.position_size * size_frac

            if (
                candle.close <= 0
                or rt.cash < buy_amount
                or len(rt.position_entries) >= rt.max_entries
            ):
                return

            shares = math.floor(buy_amount / candle.close)
            if shares < 1:
                return
            cost = shares * candle.close

            # Place order via IBKR (skip for delayed data — paper fills only)
            ibkr_order_id = None
            if rt.delayed:
                logger.info(
                    "[%s] Paper-only BUY for %s (delayed data): %d shares @ %.4f",
                    session_id[:8], symbol, shares, candle.close,
                )
            else:
                try:
                    if order_type == "limit":
                        trade = self._client.place_limit_order(symbol, "BUY", shares, candle.close)
                    else:
                        trade = self._client.place_market_order(symbol, "BUY", shares)
                    ibkr_order_id = trade.order.orderId if trade.order else None
                except Exception:
                    logger.exception("Failed to place BUY order for %s", symbol)
                    await self._broadcast(session_id, {
                        "type": "error",
                        "symbol": symbol,
                        "message": f"Failed to place BUY order for {symbol}",
                    })
                    return

            # Update in-memory state
            rt.cash -= cost
            rt.position_cost += cost
            rt.position_shares += shares
            entry = {
                "time": candle.time.isoformat(),
                "price": round(candle.close, 4),
                "shares": round(shares, 8),
                "cost": round(cost, 4),
            }
            rt.position_entries.append(entry)

            # Persist to DB
            async with get_db_context() as db:
                await self._repo.record_trade(
                    db,
                    session_id=session_id,
                    symbol=symbol,
                    side="buy",
                    order_type=order_type,
                    shares=round(shares, 8),
                    price=round(candle.close, 4),
                    cost=round(cost, 4),
                    ibkr_order_id=ibkr_order_id,
                )
                await self._repo.add_position_entry(
                    db,
                    session_symbol_id=rt.session_symbol_id,
                    time=candle.time,
                    price=round(candle.close, 4),
                    shares=round(shares, 8),
                    cost=round(cost, 4),
                    ibkr_order_id=ibkr_order_id,
                )

            await self._broadcast(session_id, {
                "type": "trade",
                "symbol": symbol,
                "side": "buy",
                "shares": round(shares, 8),
                "price": round(candle.close, 4),
                "cost": round(cost, 4),
                "time": candle.time.isoformat(),
                "cash_remaining": round(rt.cash, 4),
            })

            logger.info("BUY %s: %.4f shares @ %.4f ($%.2f)", symbol, shares, candle.close, cost)

        elif signal == "sell" and rt.position_shares > 0:
            proceeds = rt.position_shares * candle.close
            dollar_pnl = proceeds - rt.position_cost
            pnl_pct = dollar_pnl / rt.position_cost * 100 if rt.position_cost else 0.0

            # Check daily loss limit
            session_daily_pnl = sum(s.daily_realized_pnl for s in state["symbols"].values())
            if session_daily_pnl + dollar_pnl < -state["max_daily_loss"]:
                logger.warning(
                    "Daily loss limit would be exceeded for session %s — stopping",
                    session_id,
                )
                await self._broadcast(session_id, {
                    "type": "error",
                    "message": f"Daily loss limit (${state['max_daily_loss']}) reached — session stopping",
                })
                await self.stop_session(session_id, reason="daily loss limit reached")
                return

            # Place order via IBKR (skip for delayed data — paper fills only)
            ibkr_order_id = None
            if rt.delayed:
                logger.info(
                    "[%s] Paper-only SELL for %s (delayed data): %.4f shares @ %.4f",
                    session_id[:8], symbol, rt.position_shares, candle.close,
                )
            else:
                try:
                    if order_type == "limit":
                        trade = self._client.place_limit_order(symbol, "SELL", rt.position_shares, candle.close)
                    else:
                        trade = self._client.place_market_order(symbol, "SELL", rt.position_shares)
                    ibkr_order_id = trade.order.orderId if trade.order else None
                except Exception:
                    logger.exception("Failed to place SELL order for %s", symbol)
                    await self._broadcast(session_id, {
                        "type": "error",
                        "symbol": symbol,
                        "message": f"Failed to place SELL order for {symbol}",
                    })
                    return

            # Persist trade
            async with get_db_context() as db:
                await self._repo.record_trade(
                    db,
                    session_id=session_id,
                    symbol=symbol,
                    side="sell",
                    order_type=order_type,
                    shares=round(rt.position_shares, 8),
                    price=round(candle.close, 4),
                    cost=round(proceeds, 4),
                    pnl=round(dollar_pnl, 4),
                    pnl_pct=round(pnl_pct, 4),
                    ibkr_order_id=ibkr_order_id,
                )
                await self._repo.clear_position_entries(db, rt.session_symbol_id)

            # Update in-memory state
            sold_shares = rt.position_shares
            rt.cash += proceeds
            rt.realized_pnl += dollar_pnl
            rt.daily_realized_pnl += dollar_pnl
            rt.position_entries = []
            rt.position_shares = 0.0
            rt.position_cost = 0.0

            await self._broadcast(session_id, {
                "type": "trade",
                "symbol": symbol,
                "side": "sell",
                "shares": round(sold_shares, 8),
                "price": round(candle.close, 4),
                "proceeds": round(proceeds, 4),
                "pnl": round(dollar_pnl, 4),
                "pnl_pct": round(pnl_pct, 4),
                "time": candle.time.isoformat(),
                "cash_remaining": round(rt.cash, 4),
            })

            logger.info(
                "SELL %s: %.4f shares @ %.4f — P&L: $%.2f (%.2f%%)",
                symbol, sold_shares, candle.close, dollar_pnl, pnl_pct,
            )

        # Persist symbol state after every trade
        await self._persist_symbol(session_id, symbol)

    # ── Persistence ───────────────────────────────────────────────────

    async def _persist_symbol(self, session_id: str, symbol: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        rt = state["symbols"].get(symbol)
        if rt is None:
            return

        unrealized = (rt.position_shares * rt.last_price - rt.position_cost) if rt.position_shares > 0 else 0.0

        # Try to serialize strategy_state; skip if not JSON-serializable
        strategy_json = None
        try:
            json.dumps(rt.strategy_state)
            strategy_json = rt.strategy_state
        except (TypeError, ValueError):
            pass

        async with get_db_context() as db:
            await self._repo.update_symbol_state(
                db,
                rt.session_symbol_id,
                current_shares=round(rt.position_shares, 8),
                current_cost=round(rt.position_cost, 4),
                cash_remaining=round(rt.cash, 4),
                realized_pnl=round(rt.realized_pnl, 4),
                unrealized_pnl=round(unrealized, 4),
                daily_realized_pnl=round(rt.daily_realized_pnl, 4),
                last_price=round(rt.last_price, 4),
                strategy_state_json=strategy_json,
            )

    async def _persist_all_symbols(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        for sym in state["symbols"]:
            await self._persist_symbol(session_id, sym)

    # ── Startup recovery ──────────────────────────────────────────────

    async def recover_sessions(self) -> None:
        """Resume any sessions that were running when the server last stopped."""
        async with get_db_context() as db:
            running = await self._repo.list_running_sessions(db)

        for live_session in running:
            logger.info("Recovering live session %s (%s)", live_session.id, live_session.name)
            try:
                await self.start_session(live_session.id)
                logger.info("Session %s recovered successfully (warming up)", live_session.id)
            except Exception:
                logger.exception("Failed to recover session %s", live_session.id)
                async with get_db_context() as db:
                    await self._repo.update_session_status(
                        db, live_session.id, "error",
                        error_message="Failed to recover after server restart",
                    )


# Module-level singleton
live_engine = LiveTradingEngine()
