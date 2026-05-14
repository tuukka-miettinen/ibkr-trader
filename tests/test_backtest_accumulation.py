from datetime import datetime, timezone

from app.models.market_data import Candle, Timeframe
from app.strategy.backtest import BacktestConfig, run


def make_candle(index: int, close: float) -> Candle:
    return Candle(
        symbol="AAPL",
        timeframe=Timeframe.FIVE_MINUTES,
        time=datetime(2024, 1, 1, 9, 30 + index, tzinfo=timezone.utc),
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def test_run_accumulates_buys_and_exits_full_position() -> None:
    candles = [
        make_candle(0, 100.0),
        make_candle(1, 90.0),
        make_candle(2, 80.0),
        make_candle(3, 95.0),
    ]
    sigs = [
        {"time": candles[0].time.isoformat(), "signal": "buy"},
        {"time": candles[1].time.isoformat(), "signal": "buy"},
        {"time": candles[2].time.isoformat(), "signal": "buy"},
        {"time": candles[3].time.isoformat(), "signal": "sell"},
    ]

    result = run(
        candles,
        sigs,
        BacktestConfig(starting_capital=10000.0, position_size=1000.0, max_entries=5),
    )

    assert result.num_trades == 1
    assert result.total_dollar_pnl == result.trades[0].dollar_pnl
    assert result.final_balance == 10185.0

    trade = result.trades[0]
    assert len(trade.entries) == 3
    assert trade.entry_price == 89.3939
    assert trade.exit_price == 95.0
    assert trade.dollar_pnl == 185.0
    assert trade.shares == 33


def test_run_respects_max_entries_limit() -> None:
    candles = [
        make_candle(0, 100.0),
        make_candle(1, 90.0),
        make_candle(2, 80.0),
        make_candle(3, 95.0),
    ]
    sigs = [
        {"time": candles[0].time.isoformat(), "signal": "buy"},
        {"time": candles[1].time.isoformat(), "signal": "buy"},
        {"time": candles[2].time.isoformat(), "signal": "buy"},
        {"time": candles[3].time.isoformat(), "signal": "sell"},
    ]

    result = run(
        candles,
        sigs,
        BacktestConfig(starting_capital=10000.0, position_size=1000.0, max_entries=2),
    )

    trade = result.trades[0]
    assert len(trade.entries) == 2
    assert trade.total_cost == 1990.0
    assert trade.dollar_pnl == 5.0