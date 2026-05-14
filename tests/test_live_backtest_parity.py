"""Verify that the tick backtest engine and the live trading engine produce
identical signals when fed the same tick stream.

The backtest side uses the real ``run_tick_backtest`` function.
The live side replicates the TickState construction from
``LiveTradingEngine._process_tick`` using the actual ``CANDLE_TIMEFRAMES``
constant, so any divergence between the two code-paths is caught.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from app.models.market_data import Candle, Timeframe
from app.services.live_engine import CANDLE_TIMEFRAMES
from app.strategy.sandbox import compile_tick_script
from app.strategy.tick_backtest import (
    CandleAggregator,
    PositionInfo,
    TickBacktestConfig,
    TickState,
    run_tick_backtest,
)

# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _make_ticks(symbol: str = "TEST", n_minutes: int = 60) -> list[Candle]:
    """Generate 5-second ticks with a clear dip → recovery pattern.

    Minutes  0–14: slight oscillation around 100 (RSI warm-up)
    Minutes 15–24: steady decline from ~100 → ~90  (RSI drops below 30)
    Minutes 25–44: recovery from ~90 → ~110         (RSI rises above 65)
    Minutes 45–59: stable around 110
    """
    ticks: list[Candle] = []
    base = datetime(2024, 6, 3, 13, 30, 0, tzinfo=timezone.utc)

    for minute in range(n_minutes):
        if minute < 15:
            # Oscillate ±0.3 around 100 so RSI has both gains and losses
            price = 100.0 + 0.3 * math.sin(minute * 0.8)
        elif minute < 25:
            # Decline: 100 → 90 over 10 minutes
            price = 100.0 - (minute - 14) * 1.0
        elif minute < 45:
            # Recovery: 90 → 110 over 20 minutes
            price = 90.0 + (minute - 24) * 1.0
        else:
            price = 110.0

        for tick_i in range(12):  # 12 × 5s = 1 minute
            idx = minute * 12 + tick_i
            t = base + timedelta(seconds=5 * idx)
            # Tiny intra-minute noise so each 5s bar differs slightly
            noise = 0.02 * (tick_i % 3 - 1)
            p = round(price + noise, 4)
            ticks.append(
                Candle(
                    symbol=symbol,
                    timeframe=Timeframe.FIVE_SECONDS,
                    time=t,
                    open=round(p - 0.01, 4),
                    high=round(p + 0.05, 4),
                    low=round(p - 0.05, 4),
                    close=p,
                    volume=1000 + tick_i * 10,
                )
            )

    return ticks


# ---------------------------------------------------------------------------
# Simple strategy executed via compile_tick_script (uses the ta module)
# ---------------------------------------------------------------------------

_STRATEGY_SCRIPT = """\
STRATEGY_NAME = "Parity Test Strategy"

def on_tick(state):
    closed_1m = state.closed.get("1m")
    candles_1m = state.candles.get("1m", [])

    if closed_1m is None or len(candles_1m) < 16:
        return {"signal": None}

    rsi_vals = ta.rsi(candles_1m, 14)
    current_rsi = rsi_vals[-1]
    if current_rsi is None:
        return {"signal": None}

    # Buy when RSI dips below 30
    if state.position is None and current_rsi < 30:
        return {"signal": "buy", "size": 1}

    # Sell when RSI recovers above 65
    if state.position is not None and current_rsi > 65:
        return {"signal": "sell"}

    return {"signal": None}
"""

# ---------------------------------------------------------------------------
# Config shared by both engines
# ---------------------------------------------------------------------------

_STARTING_CAPITAL = 10_000.0
_POSITION_SIZE = 1_000.0
_MAX_ENTRIES = 5


# ---------------------------------------------------------------------------
# Helpers to collect signals from each engine
# ---------------------------------------------------------------------------

def _collect_backtest_signals(
    ticks: list[Candle],
    on_tick_fn: callable,
) -> list[tuple[str, str | None]]:
    """Run the *actual* ``run_tick_backtest`` with a wrapping callback that
    records every signal the strategy emits."""
    signals: list[tuple[str, str | None]] = []

    def _capturing_wrapper(state: TickState):
        result = on_tick_fn(state)
        sig = result.get("signal") if isinstance(result, dict) else None
        signals.append((state.tick.time.isoformat(), sig))
        return result

    run_tick_backtest(
        ticks,
        _capturing_wrapper,
        config=TickBacktestConfig(
            starting_capital=_STARTING_CAPITAL,
            position_size=_POSITION_SIZE,
            max_entries=_MAX_ENTRIES,
            candle_timeframes=[
                Timeframe.ONE_MINUTE,
                Timeframe.FIVE_MINUTES,
                Timeframe.FIFTEEN_MINUTES,
            ],
            # Zero fees so position state stays in sync with live
            fee_per_share=0.0,
            fee_min_order=0.0,
            fee_max_pct=0.0,
        ),
    )
    return signals


def _collect_live_signals(
    ticks: list[Candle],
    on_tick_fn: callable,
) -> list[tuple[str, str | None]]:
    """Replay ticks through the same TickState construction used by
    ``LiveTradingEngine._process_tick``, using the actual
    ``CANDLE_TIMEFRAMES`` constant from the live engine module."""
    symbol = ticks[0].symbol
    aggregator = CandleAggregator(symbol, CANDLE_TIMEFRAMES)

    signals: list[tuple[str, str | None]] = []
    cash = _STARTING_CAPITAL
    position_entries: list[dict] = []
    position_shares = 0.0
    position_cost = 0.0
    strategy_state: dict = {}

    for candle in ticks:
        # --- identical to LiveTradingEngine._process_tick ---
        closed = aggregator.push(candle)

        position_info = None
        if position_shares > 0:
            position_info = PositionInfo(
                shares=round(position_shares, 8),
                avg_price=round(position_cost / position_shares, 4) if position_shares else 0.0,
                total_cost=round(position_cost, 4),
                entries=position_entries.copy(),
                unrealized_pnl=round(position_shares * candle.close - position_cost, 4),
            )

        market_value = position_shares * candle.close if position_shares > 0 else 0.0
        tick_state = TickState(
            tick=candle,
            candles={tf: aggregator.completed_candles(tf) for tf in CANDLE_TIMEFRAMES},
            current_candles={tf: aggregator.current_candle(tf) for tf in CANDLE_TIMEFRAMES},
            closed=closed,
            position=position_info,
            cash=round(cash, 4),
            portfolio_value=round(cash + market_value, 4),
            strategy=strategy_state,
        )

        try:
            result = on_tick_fn(tick_state)
        except Exception:
            result = None

        signal = result.get("signal") if isinstance(result, dict) else None
        signals.append((candle.time.isoformat(), signal))

        # --- position management mirrors _execute_signal (no fees) ---
        if signal == "buy":
            size_frac = result.get("size", 1.0)
            buy_amount = _POSITION_SIZE * size_frac
            if (
                candle.close > 0
                and cash >= buy_amount
                and len(position_entries) < _MAX_ENTRIES
            ):
                shares = buy_amount / candle.close
                cost = shares * candle.close
                cash -= cost
                position_cost += cost
                position_shares += shares
                position_entries.append({
                    "time": candle.time.isoformat(),
                    "price": round(candle.close, 4),
                    "shares": round(shares, 8),
                    "cost": round(cost, 4),
                })
        elif signal == "sell" and position_shares > 0:
            proceeds = position_shares * candle.close
            cash += proceeds
            position_entries = []
            position_shares = 0.0
            position_cost = 0.0

    return signals


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_backtest_and_live_produce_same_signals():
    """Feed identical ticks through run_tick_backtest and through the live
    engine's TickState construction.  Every tick must produce the same signal."""
    ticks = _make_ticks(n_minutes=60)
    on_tick_fn = compile_tick_script(_STRATEGY_SCRIPT)

    bt_signals = _collect_backtest_signals(ticks, on_tick_fn)
    live_signals = _collect_live_signals(ticks, on_tick_fn)

    assert len(bt_signals) == len(live_signals) == len(ticks)

    mismatches = []
    for i, (bt, live) in enumerate(zip(bt_signals, live_signals)):
        if bt != live:
            mismatches.append((i, bt, live))

    assert mismatches == [], (
        f"{len(mismatches)} signal mismatches between backtest and live:\n"
        + "\n".join(
            f"  tick {i}: backtest={bt}, live={live}"
            for i, bt, live in mismatches[:20]
        )
    )


def test_signals_include_buys_and_sells():
    """Ensure the test data actually triggers both buy and sell signals,
    otherwise the parity test would trivially pass with all-None signals."""
    ticks = _make_ticks(n_minutes=60)
    on_tick_fn = compile_tick_script(_STRATEGY_SCRIPT)

    signals = _collect_live_signals(ticks, on_tick_fn)

    buy_count = sum(1 for _, s in signals if s == "buy")
    sell_count = sum(1 for _, s in signals if s == "sell")

    assert buy_count >= 1, "Test data should trigger at least one buy signal"
    assert sell_count >= 1, "Test data should trigger at least one sell signal"


def test_candle_timeframes_match():
    """The live engine's CANDLE_TIMEFRAMES must match the backtest default."""
    bt_default = TickBacktestConfig().candle_timeframes
    assert CANDLE_TIMEFRAMES == bt_default, (
        f"CANDLE_TIMEFRAMES mismatch: live={CANDLE_TIMEFRAMES}, "
        f"backtest_default={bt_default}"
    )
