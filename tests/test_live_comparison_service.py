from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.live import LiveRepository
from app.db.strategies import StrategyRepository
from app.models.market_data import Candle, Timeframe
from app.services.live_comparison import LiveComparisonService


SCRIPT = """\
STRATEGY_NAME = "Comparison Test Strategy"

def on_tick(state):
    closed_1m = state.closed.get("1m")
    candles_1m = state.candles.get("1m", [])

    if closed_1m is None or len(candles_1m) < 2:
        return {"signal": None}

    if state.position is None and candles_1m[-1].close > candles_1m[-2].close:
        return {"signal": "buy", "size": 1}

    if state.position is not None and candles_1m[-1].close < candles_1m[-2].close:
        return {"signal": "sell"}

    return {"signal": None}
"""


def _make_tick(symbol: str, when: datetime, price: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.FIVE_SECONDS,
        time=when,
        open=round(price, 4),
        high=round(price + 0.05, 4),
        low=round(price - 0.05, 4),
        close=round(price, 4),
        volume=100,
    )


def _make_minute_ticks(symbol: str, start: datetime, prices: list[float]) -> list[Candle]:
    ticks: list[Candle] = []
    for minute_index, minute_close in enumerate(prices):
        minute_start = start + timedelta(minutes=minute_index)
        for tick_index in range(12):
            when = minute_start + timedelta(seconds=tick_index * 5)
            ticks.append(_make_tick(symbol, when, minute_close))
    return ticks


@pytest.mark.asyncio
async def test_compare_session_symbol_flags_trade_mismatch(test_db_session):
    live_repo = LiveRepository()
    strategy_repo = StrategyRepository()
    compare_service = LiveComparisonService()

    algo = await strategy_repo.save_algorithm(test_db_session, "Comparison Test Strategy", SCRIPT)
    session = await live_repo.create_session(
        test_db_session,
        name="Compare Session",
        position_size=1000.0,
        max_entries=4,
        max_daily_loss=500.0,
    )
    session_symbol = await live_repo.add_session_symbol(
        test_db_session,
        session_id=session.id,
        symbol="TEST",
        algorithm_id=algo.id,
        allocated_capital=10000.0,
        position_size=1000.0,
        max_entries=4,
    )

    seed_candle = Candle(
        symbol="TEST",
        timeframe=Timeframe.ONE_MINUTE,
        time=datetime(2024, 6, 3, 13, 29, tzinfo=timezone.utc),
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        volume=1000,
    )
    await live_repo.replace_seed_candles(
        test_db_session,
        session_symbol_id=session_symbol.id,
        timeframe=Timeframe.ONE_MINUTE,
        candles=[seed_candle],
    )

    start = datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc)
    captured_ticks = _make_minute_ticks("TEST", start, [101.0, 99.0, 99.0])
    await live_repo.record_ticks(
        test_db_session,
        session_symbol_id=session_symbol.id,
        ticks=captured_ticks,
    )

    await live_repo.record_trade(
        test_db_session,
        session_id=session.id,
        symbol="TEST",
        side="buy",
        order_type="market",
        shares=10,
        price=99.0,
        cost=990.0,
        event_time=start + timedelta(minutes=1, seconds=5),
    )
    await live_repo.record_trade(
        test_db_session,
        session_id=session.id,
        symbol="TEST",
        side="sell",
        order_type="market",
        shares=10,
        price=99.0,
        cost=990.0,
        pnl=0.0,
        pnl_pct=0.0,
        event_time=start + timedelta(minutes=2),
    )

    result = await compare_service.compare_session_symbol(
        test_db_session,
        session_id=session.id,
        symbol="TEST",
        minutes=5,
    )

    assert result["captured_tick_count"] == len(captured_ticks)
    assert result["matched"] is False
    assert result["mismatch_count"] >= 1
    assert result["mismatches"][0]["reason"] in {"field_mismatch", "missing_trade"}
    assert result["replay_trades"][0]["side"] == "buy"
    assert result["live_trades"][0]["time"] != result["replay_trades"][0]["time"]
