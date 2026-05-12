"""Tests for walk-forward splitting functionality."""
from datetime import datetime, timedelta

from pytest import raises

from app.models.market_data import Candle, Timeframe
from app.strategy.walk_forward import split_candles_walk_forward


def test_split_candles_default_ratio() -> None:
    base_time = datetime(2025, 4, 10, 9, 30)
    candles = [
        Candle(
            symbol="AAPL",
            timeframe=Timeframe.FIVE_MINUTES,
            time=base_time + timedelta(minutes=5 * i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000000,
        )
        for i in range(100)
    ]

    train, holdout = split_candles_walk_forward(candles, train_ratio=0.67)
    assert len(train) == 67
    assert len(holdout) == 33
    assert train[-1].time < holdout[0].time


def test_split_candles_preserves_order() -> None:
    base_time = datetime(2025, 4, 10, 9, 30)
    candles = [
        Candle(
            symbol="AAPL",
            timeframe=Timeframe.FIVE_MINUTES,
            time=base_time + timedelta(minutes=5 * i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000000,
        )
        for i in range(50)
    ]

    train, holdout = split_candles_walk_forward(candles, train_ratio=0.6)
    assert len(train) == 30
    assert len(holdout) == 20
    assert [c.time for c in train] == sorted(c.time for c in train)
    assert [c.time for c in holdout] == sorted(c.time for c in holdout)


def test_split_empty_candles() -> None:
    with raises(ValueError, match="must not be empty"):
        split_candles_walk_forward([], train_ratio=0.67)


def test_split_invalid_train_ratio_low() -> None:
    base_time = datetime(2025, 4, 10, 9, 30)
    candles = [
        Candle(
            symbol="AAPL",
            timeframe=Timeframe.FIVE_MINUTES,
            time=base_time + timedelta(minutes=5 * i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000000,
        )
        for i in range(10)
    ]

    with raises(ValueError, match="between 0.5 and 0.95"):
        split_candles_walk_forward(candles, train_ratio=0.45)


def test_split_invalid_train_ratio_high() -> None:
    base_time = datetime(2025, 4, 10, 9, 30)
    candles = [
        Candle(
            symbol="AAPL",
            timeframe=Timeframe.FIVE_MINUTES,
            time=base_time + timedelta(minutes=5 * i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000000,
        )
        for i in range(10)
    ]

    with raises(ValueError, match="between 0.5 and 0.95"):
        split_candles_walk_forward(candles, train_ratio=0.99)
