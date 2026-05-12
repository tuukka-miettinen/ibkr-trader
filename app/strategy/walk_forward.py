"""Split candle history into train and holdout windows for walk-forward evaluation."""
from __future__ import annotations

from app.models.market_data import Candle


def split_candles_walk_forward(
    candles: list[Candle], train_ratio: float = 0.67
) -> tuple[list[Candle], list[Candle]]:
    """Split candles into train and holdout windows preserving time order.

    Args:
        candles: List of candles in chronological order.
        train_ratio: Fraction of candles to use for training (default 0.67).

    Returns:
        A tuple of (train_candles, holdout_candles).

    Raises:
        ValueError: If candles list is empty or train_ratio is invalid.
    """
    if not candles:
        raise ValueError("candles list must not be empty")
    if not (0.5 < train_ratio < 0.95):
        raise ValueError("train_ratio must be between 0.5 and 0.95")

    split_index = int(len(candles) * train_ratio)
    return candles[:split_index], candles[split_index:]


def get_candle_count_by_trading_days(candles: list[Candle], target_days: int) -> int:
    """Estimate the candle count that corresponds to a target number of trading days.

    This is a helper for calendar-based splitting. For now, we use a simple heuristic:
    ~252 trading days per year, and each minute/5m/15m candle is one bar. The actual
    trading days depend on the timeframe and market hours.

    Args:
        candles: List of candles in chronological order.
        target_days: Target number of trading days.

    Returns:
        An estimated index into the candle list.
    """
    if not candles:
        return 0

    timeframe = candles[0].timeframe
    bars_per_day = {"1m": 6 * 60, "5m": 6 * 12, "15m": 6 * 4, "1h": 6}
    bars_needed = bars_per_day.get(timeframe, 6 * 4) * target_days
    return min(int(bars_needed), len(candles))
