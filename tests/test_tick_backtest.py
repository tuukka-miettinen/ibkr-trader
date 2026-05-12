"""Tests for CandleAggregator and tick-level backtest engine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.market_data import Candle, Timeframe
from app.strategy.tick_backtest import (
    CandleAggregator,
    TickBacktestConfig,
    run_tick_backtest,
)


def _make_tick(symbol: str, time: datetime, price: float, volume: int = 100) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.FIVE_SECONDS,
        time=time,
        open=price,
        high=price + 0.01,
        low=price - 0.01,
        close=price,
        volume=volume,
    )


def _make_ticks(symbol: str, start: datetime, count: int, base_price: float = 100.0) -> list[Candle]:
    return [
        _make_tick(symbol, start + timedelta(seconds=5 * i), base_price + i * 0.01)
        for i in range(count)
    ]


class TestCandleAggregator:
    def test_single_tick_no_close(self) -> None:
        agg = CandleAggregator("AAPL", [Timeframe.ONE_MINUTE])
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        tick = _make_tick("AAPL", start, 150.0)
        closed = agg.push(tick)
        assert closed[Timeframe.ONE_MINUTE] is None
        assert len(agg.completed_candles(Timeframe.ONE_MINUTE)) == 0
        assert agg.current_candle(Timeframe.ONE_MINUTE) is not None

    def test_minute_closes_after_12_ticks(self) -> None:
        """12 five-second ticks = 1 minute → should produce 1 closed 1m candle."""
        agg = CandleAggregator("AAPL", [Timeframe.ONE_MINUTE])
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        ticks = _make_ticks("AAPL", start, 13, 150.0)

        closed_candles = []
        for tick in ticks:
            closed = agg.push(tick)
            if closed[Timeframe.ONE_MINUTE] is not None:
                closed_candles.append(closed[Timeframe.ONE_MINUTE])

        assert len(closed_candles) == 1
        c = closed_candles[0]
        assert c.timeframe == Timeframe.ONE_MINUTE
        assert c.symbol == "AAPL"
        assert c.time == start

    def test_five_minute_closes_after_60_ticks(self) -> None:
        """60 five-second ticks = 5 minutes."""
        agg = CandleAggregator("AAPL", [Timeframe.FIVE_MINUTES])
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        ticks = _make_ticks("AAPL", start, 61, 150.0)

        closed_candles = []
        for tick in ticks:
            closed = agg.push(tick)
            if closed[Timeframe.FIVE_MINUTES] is not None:
                closed_candles.append(closed[Timeframe.FIVE_MINUTES])

        assert len(closed_candles) == 1

    def test_flush_emits_in_progress(self) -> None:
        agg = CandleAggregator("AAPL", [Timeframe.ONE_MINUTE])
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        tick = _make_tick("AAPL", start, 150.0)
        agg.push(tick)

        flushed = agg.flush()
        assert flushed[Timeframe.ONE_MINUTE] is not None
        assert len(agg.completed_candles(Timeframe.ONE_MINUTE)) == 1

    def test_ohlcv_correctness(self) -> None:
        agg = CandleAggregator("AAPL", [Timeframe.ONE_MINUTE])
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)

        prices = [150.0, 151.0, 149.0, 150.5]
        for i, price in enumerate(prices):
            tick = _make_tick("AAPL", start + timedelta(seconds=5 * i), price)
            agg.push(tick)

        # Push one tick into the next minute to close the first
        next_min_tick = _make_tick("AAPL", start + timedelta(seconds=60), 150.0)
        closed = agg.push(next_min_tick)

        c = closed[Timeframe.ONE_MINUTE]
        assert c is not None
        assert c.open == 150.0
        assert c.high >= 151.0
        assert c.low <= 149.0
        assert c.close == 150.5


class TestRunTickBacktest:
    def test_empty_ticks_returns_empty_result(self) -> None:
        result = run_tick_backtest([], lambda s: {"signal": None})
        assert result.num_trades == 0
        assert result.starting_capital == 10000.0

    def test_simple_buy_sell(self) -> None:
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        ticks = _make_ticks("AAPL", start, 24, 100.0)  # 2 minutes of ticks

        call_count = 0

        def strategy(state):
            nonlocal call_count
            call_count += 1
            # Buy on tick 3, sell on tick 15
            if call_count == 3:
                return {"signal": "buy"}
            if call_count == 15:
                return {"signal": "sell"}
            return {"signal": None}

        config = TickBacktestConfig(
            starting_capital=10000.0,
            position_size=1000.0,
            max_entries=5,
        )
        result = run_tick_backtest(ticks, strategy, config)
        assert result.num_trades == 1
        assert len(result.daily_snapshots) == 1

    def test_daily_snapshots_with_overnight_position(self) -> None:
        # Day 1: buy, don't sell
        day1_start = datetime(2026, 5, 11, 14, 0, 0, tzinfo=timezone.utc)
        day1_ticks = _make_ticks("AAPL", day1_start, 12, 100.0)

        # Day 2: sell
        day2_start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        day2_ticks = _make_ticks("AAPL", day2_start, 12, 101.0)

        all_ticks = day1_ticks + day2_ticks
        call_count = 0

        def strategy(state):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return {"signal": "buy"}
            if call_count == 20:
                return {"signal": "sell"}
            return {"signal": None}

        config = TickBacktestConfig(starting_capital=10000.0, position_size=1000.0)
        result = run_tick_backtest(all_ticks, strategy, config)

        assert len(result.daily_snapshots) == 2
        day1_snap = result.daily_snapshots[0]
        assert day1_snap.realized_trades == 0
        assert day1_snap.unrealized_pnl != 0  # position carried overnight
        assert day1_snap.position_shares > 0

        day2_snap = result.daily_snapshots[1]
        assert day2_snap.realized_trades == 1

    def test_strategy_receives_candle_context(self) -> None:
        start = datetime(2026, 5, 12, 14, 0, 0, tzinfo=timezone.utc)
        # 13 ticks = should close one 1m candle at the 13th tick
        ticks = _make_ticks("AAPL", start, 13, 100.0)

        saw_closed_1m = False

        def strategy(state):
            nonlocal saw_closed_1m
            closed_1m = state.closed.get(Timeframe.ONE_MINUTE)
            if closed_1m is not None:
                saw_closed_1m = True
            # Always check that state has the expected fields
            assert hasattr(state, "tick")
            assert hasattr(state, "candles")
            assert hasattr(state, "current_candles")
            assert hasattr(state, "cash")
            assert hasattr(state, "portfolio_value")
            return {"signal": None}

        run_tick_backtest(ticks, strategy)
        assert saw_closed_1m, "Strategy should have seen a closed 1m candle"
