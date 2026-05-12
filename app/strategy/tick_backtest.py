"""Tick-level backtest engine.

Replays 5-second bars through a user-supplied ``on_tick(state)`` callback,
aggregating them into higher-timeframe candles so the strategy can react to
both individual ticks and completed candle closes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from app.models.market_data import Candle, Timeframe
from app.strategy.backtest import BacktestConfig, BacktestResult, DailySnapshot, Trade, _normalize_config


# ── Candle aggregation ─────────────────────────────────────────────────────

AGGREGATION_STEPS = {
    Timeframe.ONE_MINUTE: timedelta(minutes=1),
    Timeframe.THREE_MINUTES: timedelta(minutes=3),
    Timeframe.FIVE_MINUTES: timedelta(minutes=5),
    Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
    Timeframe.ONE_HOUR: timedelta(hours=1),
}


def _bar_start(tick_time, step: timedelta) -> "datetime":
    """Round *tick_time* down to the beginning of its bar."""
    from datetime import datetime, timezone
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = tick_time - epoch
    step_secs = int(step.total_seconds())
    bar_epoch = int(elapsed.total_seconds()) // step_secs * step_secs
    return epoch + timedelta(seconds=bar_epoch)


@dataclass
class _InProgressCandle:
    symbol: str
    timeframe: Timeframe
    bar_start: "datetime"
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    def update(self, tick: Candle) -> None:
        self.high = max(self.high, tick.high)
        self.low = min(self.low, tick.low)
        self.close = tick.close
        self.volume += tick.volume

    def to_candle(self) -> Candle:
        return Candle(
            symbol=self.symbol,
            timeframe=self.timeframe,
            time=self.bar_start,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


class CandleAggregator:
    """Accumulates 5-second ticks into higher-timeframe OHLCV candles."""

    def __init__(self, symbol: str, timeframes: list[Timeframe] | None = None) -> None:
        self._symbol = symbol.upper()
        self._timeframes = timeframes or [
            Timeframe.ONE_MINUTE,
            Timeframe.FIVE_MINUTES,
            Timeframe.FIFTEEN_MINUTES,
        ]
        self._steps = {tf: AGGREGATION_STEPS[tf] for tf in self._timeframes}
        self._in_progress: dict[Timeframe, _InProgressCandle | None] = {tf: None for tf in self._timeframes}
        self._completed: dict[Timeframe, list[Candle]] = {tf: [] for tf in self._timeframes}

    def push(self, tick: Candle) -> dict[Timeframe, Candle | None]:
        """Push a 5-second tick.  Returns a dict of timeframe → just-closed candle (or None)."""
        closed: dict[Timeframe, Candle | None] = {}
        for tf in self._timeframes:
            step = self._steps[tf]
            bs = _bar_start(tick.time, step)
            current = self._in_progress[tf]

            if current is None:
                # First tick for this timeframe
                self._in_progress[tf] = _InProgressCandle(
                    symbol=self._symbol,
                    timeframe=tf,
                    bar_start=bs,
                    open=tick.open,
                    high=tick.high,
                    low=tick.low,
                    close=tick.close,
                    volume=tick.volume,
                )
                closed[tf] = None
            elif bs != current.bar_start:
                # New bar started → close the previous one
                completed_candle = current.to_candle()
                self._completed[tf].append(completed_candle)
                self._in_progress[tf] = _InProgressCandle(
                    symbol=self._symbol,
                    timeframe=tf,
                    bar_start=bs,
                    open=tick.open,
                    high=tick.high,
                    low=tick.low,
                    close=tick.close,
                    volume=tick.volume,
                )
                closed[tf] = completed_candle
            else:
                current.update(tick)
                closed[tf] = None

        return closed

    def completed_candles(self, tf: Timeframe) -> list[Candle]:
        return self._completed[tf]

    def current_candle(self, tf: Timeframe) -> Candle | None:
        ip = self._in_progress[tf]
        return ip.to_candle() if ip else None

    def flush(self) -> dict[Timeframe, Candle | None]:
        """Flush any in-progress candles as completed (end of data)."""
        closed: dict[Timeframe, Candle | None] = {}
        for tf in self._timeframes:
            ip = self._in_progress[tf]
            if ip is not None:
                completed_candle = ip.to_candle()
                self._completed[tf].append(completed_candle)
                self._in_progress[tf] = None
                closed[tf] = completed_candle
            else:
                closed[tf] = None
        return closed


# ── Tick state ────────────────────────────────────────────────────────────

@dataclass
class PositionInfo:
    shares: float
    avg_price: float
    total_cost: float
    entries: list[dict]
    unrealized_pnl: float = 0.0


@dataclass
class TickState:
    """State passed to the user's ``on_tick(state)`` callback."""
    tick: Candle
    candles: dict  # Timeframe → list[Candle] (completed)
    current_candles: dict  # Timeframe → Candle | None (in-progress)
    closed: dict  # Timeframe → Candle | None (just closed this tick)
    position: PositionInfo | None
    cash: float
    portfolio_value: float
    strategy: dict = field(default_factory=dict)  # persistent user state across ticks


# ── Tick backtest engine ──────────────────────────────────────────────────

@dataclass
class TickBacktestConfig(BacktestConfig):
    candle_timeframes: list[Timeframe] = field(default_factory=lambda: [
        Timeframe.ONE_MINUTE,
        Timeframe.FIVE_MINUTES,
        Timeframe.FIFTEEN_MINUTES,
    ])


def run_tick_backtest(
    ticks: list[Candle],
    on_tick_fn: callable,
    config: TickBacktestConfig | None = None,
    on_progress: callable | None = None,
) -> BacktestResult:
    """Run a tick-level backtest.

    *on_tick_fn* is called with a ``TickState`` for every 5-second bar and must
    return ``{"signal": "buy" | "sell" | None}``.
    """
    resolved = config or TickBacktestConfig()
    base_config = _normalize_config(resolved)

    if not ticks:
        return BacktestResult(starting_capital=base_config.starting_capital, final_balance=base_config.starting_capital)

    symbol = ticks[0].symbol
    aggregator = CandleAggregator(symbol, resolved.candle_timeframes)

    trades: list[Trade] = []
    daily_snapshots: list[DailySnapshot] = []
    cash = base_config.starting_capital
    position_entries: list[dict] = []
    position_shares = 0.0
    position_cost = 0.0
    strategy_state: dict = {}  # persistent state shared across all ticks

    current_date: str | None = None
    day_realized_trades: list[Trade] = []
    day_buys = 0
    day_sells = 0
    prev_close = 0.0

    # Pre-compute unique trading dates for progress reporting
    # Ticks after 20:00 UTC belong to the same trading day (extended hours)
    def _trading_date(t):
        d = t.time.date()
        if t.time.hour < 8:  # before 08:00 UTC = previous trading day's after-hours
            d = d - timedelta(days=1)
        return d.isoformat()

    all_trading_dates = sorted({_trading_date(t) for t in ticks})
    total_days = len(all_trading_dates)
    completed_days = 0

    def _flush_day(date: str, close_price: float) -> None:
        nonlocal day_buys, day_sells
        unrealized = (position_shares * close_price - position_cost) if position_shares > 0 else 0.0
        realized_pnl = round(sum(t.dollar_pnl for t in day_realized_trades), 4)
        wins = sum(1 for t in day_realized_trades if t.dollar_pnl > 0)
        avg_pct = round(sum(t.pnl_pct for t in day_realized_trades) / len(day_realized_trades), 4) if day_realized_trades else 0.0
        daily_snapshots.append(DailySnapshot(
            date=date,
            realized_trades=len(day_realized_trades),
            realized_pnl=realized_pnl,
            unrealized_pnl=round(unrealized, 4),
            position_shares=round(position_shares, 8),
            position_cost=round(position_cost, 4),
            day_close_price=round(close_price, 4),
            win_rate=round(wins / len(day_realized_trades) * 100, 1) if day_realized_trades else 0.0,
            avg_trade_pct=avg_pct,
            day_buys=day_buys,
            day_sells=day_sells,
        ))
        day_buys = 0
        day_sells = 0

    for tick in ticks:
        tick_date = _trading_date(tick)

        if current_date is not None and tick_date != current_date:
            _flush_day(current_date, prev_close)
            day_realized_trades = []
            day_buys = 0
            day_sells = 0
            completed_days += 1
            if on_progress:
                on_progress({"completed_days": completed_days, "total_days": total_days})

        current_date = tick_date
        prev_close = tick.close

        # Update aggregator
        closed = aggregator.push(tick)

        # Build position info
        position_info = None
        if position_shares > 0:
            position_info = PositionInfo(
                shares=round(position_shares, 8),
                avg_price=round(position_cost / position_shares, 4) if position_shares else 0.0,
                total_cost=round(position_cost, 4),
                entries=position_entries.copy(),
                unrealized_pnl=round(position_shares * tick.close - position_cost, 4),
            )

        market_value = position_shares * tick.close if position_shares > 0 else 0.0
        state = TickState(
            tick=tick,
            candles={tf: aggregator.completed_candles(tf) for tf in resolved.candle_timeframes},
            current_candles={tf: aggregator.current_candle(tf) for tf in resolved.candle_timeframes},
            closed=closed,
            position=position_info,
            cash=round(cash, 4),
            portfolio_value=round(cash + market_value, 4),
            strategy=strategy_state,
        )

        # Call user strategy
        try:
            result = on_tick_fn(state)
        except Exception:
            result = None

        if not isinstance(result, dict):
            continue

        signal = result.get("signal")

        if signal == "buy":
            size_frac = result.get("size", 1.0)
            buy_amount = base_config.position_size * size_frac
            if (
                tick.close > 0
                and cash >= buy_amount
                and len(position_entries) < base_config.max_entries
            ):
                shares = buy_amount / tick.close
                cost = shares * tick.close
                cash -= cost
                position_cost += cost
                position_shares += shares
                position_entries.append({
                    "time": tick.time.isoformat(),
                    "price": round(tick.close, 4),
                    "shares": round(shares, 8),
                    "cost": round(cost, 4),
                })
                day_buys += 1
        elif signal == "sell" and position_shares > 0:
            proceeds = position_shares * tick.close
            dollar_pnl = proceeds - position_cost
            avg_entry_price = position_cost / position_shares
            pnl_pct = dollar_pnl / position_cost * 100 if position_cost else 0.0
            trade = Trade(
                entry_time=position_entries[0]["time"],
                exit_time=tick.time.isoformat(),
                entry_price=round(avg_entry_price, 4),
                exit_price=round(tick.close, 4),
                pnl=round(dollar_pnl, 4),
                pnl_pct=round(pnl_pct, 4),
                shares=round(position_shares, 8),
                total_cost=round(position_cost, 4),
                dollar_pnl=round(dollar_pnl, 4),
                entries=position_entries.copy(),
            )
            trades.append(trade)
            day_realized_trades.append(trade)
            cash += proceeds
            position_entries = []
            position_shares = 0.0
            position_cost = 0.0
            day_sells += 1

    # Flush last day
    if current_date is not None and ticks:
        _flush_day(current_date, ticks[-1].close)
        completed_days += 1
        if on_progress:
            on_progress({"completed_days": completed_days, "total_days": total_days})

    final_market_value = position_shares * ticks[-1].close if ticks and position_shares > 0 else 0.0
    final_balance = round(cash + final_market_value, 4)
    total_pnl = round(final_balance - base_config.starting_capital, 4)
    wins = sum(1 for t in trades if t.dollar_pnl > 0)

    return BacktestResult(
        trades=trades,
        daily_snapshots=daily_snapshots,
        total_pnl=total_pnl,
        total_pnl_pct=round(total_pnl / base_config.starting_capital * 100, 4),
        win_rate=round(wins / len(trades) * 100, 1) if trades else 0.0,
        num_trades=len(trades),
        starting_capital=round(base_config.starting_capital, 4),
        final_balance=final_balance,
        total_dollar_pnl=total_pnl,
    )
