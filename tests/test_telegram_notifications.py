from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

import app.services.live_engine as live_engine_module
from app.models.market_data import Candle, Timeframe
from app.services.live_engine import CANDLE_TIMEFRAMES, LiveTradingEngine, SymbolRuntime
from app.services.telegram import build_trade_notification_text
from app.strategy.tick_backtest import CandleAggregator


def _make_candle(symbol: str, price: float) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.FIVE_SECONDS,
        time=datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=100,
    )


def _make_runtime(symbol: str, *, cash: float = 10000.0) -> SymbolRuntime:
    return SymbolRuntime(
        session_symbol_id=f"{symbol.lower()}-1",
        symbol=symbol,
        algorithm_name="Telegram Test Strategy",
        algorithm_script="STRATEGY_NAME = 'Telegram Test Strategy'",
        on_tick_fn=lambda state: {"signal": None},
        aggregator=CandleAggregator(symbol, CANDLE_TIMEFRAMES),
        cash=cash,
        position_size=1000.0,
        last_price=100.0,
    )


def test_build_trade_notification_text_includes_position_summary():
    text = build_trade_notification_text(
        session_name="Paper Session",
        symbol="AAPL",
        strategy_name="Telegram Test Strategy",
        side="buy",
        order_type="market",
        shares=10,
        price=100.0,
        notional=1000.0,
        cash_remaining=9000.0,
        executed_at=datetime(2024, 6, 3, 13, 30, tzinfo=timezone.utc),
        positions=[
            {
                "symbol": "AAPL",
                "shares": 10,
                "avg_price": 100.0,
                "last_price": 100.5,
                "market_value": 1005.0,
                "unrealized_pnl": 5.0,
            }
        ],
    )

    assert "BUY executed" in text
    assert "Session: Paper Session" in text
    assert "Strategy: Telegram Test Strategy" in text
    assert "Open positions (1)" in text
    assert "AAPL: 10 sh @ $100.0000 | last $100.5000 | U-PnL +$5.00" in text


@pytest.mark.asyncio
async def test_live_engine_sends_telegram_notification_on_buy(monkeypatch):
    engine = LiveTradingEngine()
    engine._repo.record_trade = AsyncMock()
    engine._repo.add_position_entry = AsyncMock()
    engine._repo.update_symbol_state = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._telegram.send_trade_notification = AsyncMock(return_value=True)

    @asynccontextmanager
    async def fake_db_context():
        yield None

    monkeypatch.setattr(live_engine_module, "get_db_context", fake_db_context)

    runtime = _make_runtime("AAPL")
    runtime.delayed = True
    state = {
        "session_name": "Paper Session",
        "symbols": {"AAPL": runtime},
        "order_type": "market",
        "max_daily_loss": 500.0,
        "max_total_exposure": 50000.0,
    }

    await engine._execute_signal(
        "session-1",
        "AAPL",
        "buy",
        _make_candle("AAPL", 100.0),
        {"size": 1.0},
        state,
    )

    engine._telegram.send_trade_notification.assert_awaited_once()
    kwargs = engine._telegram.send_trade_notification.await_args.kwargs
    assert kwargs["session_name"] == "Paper Session"
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["strategy_name"] == "Telegram Test Strategy"
    assert kwargs["side"] == "buy"
    assert kwargs["shares"] == 10
    assert kwargs["notional"] == 1000.0
    assert kwargs["cash_remaining"] == 9000.0
    assert kwargs["positions"][0]["symbol"] == "AAPL"
    assert kwargs["positions"][0]["shares"] == 10.0


@pytest.mark.asyncio
async def test_live_engine_sends_telegram_notification_on_sell(monkeypatch):
    engine = LiveTradingEngine()
    engine._repo.record_trade = AsyncMock()
    engine._repo.clear_position_entries = AsyncMock()
    engine._repo.update_symbol_state = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._telegram.send_trade_notification = AsyncMock(return_value=True)

    @asynccontextmanager
    async def fake_db_context():
        yield None

    monkeypatch.setattr(live_engine_module, "get_db_context", fake_db_context)

    runtime = _make_runtime("AAPL", cash=9000.0)
    runtime.delayed = True
    runtime.position_entries = [{"time": "2024-06-03T13:20:00+00:00", "price": 95.0, "shares": 10.0, "cost": 950.0}]
    runtime.position_shares = 10.0
    runtime.position_cost = 950.0
    runtime.last_price = 100.0

    state = {
        "session_name": "Paper Session",
        "symbols": {"AAPL": runtime},
        "order_type": "market",
        "max_daily_loss": 500.0,
        "max_total_exposure": 50000.0,
    }

    await engine._execute_signal(
        "session-1",
        "AAPL",
        "sell",
        _make_candle("AAPL", 100.0),
        {},
        state,
    )

    engine._telegram.send_trade_notification.assert_awaited_once()
    kwargs = engine._telegram.send_trade_notification.await_args.kwargs
    assert kwargs["side"] == "sell"
    assert kwargs["shares"] == 10.0
    assert kwargs["notional"] == 1000.0
    assert kwargs["cash_remaining"] == 10000.0
    assert kwargs["pnl"] == 50.0
    assert kwargs["pnl_pct"] == round(50.0 / 950.0 * 100, 4)
    assert kwargs["positions"] == []
