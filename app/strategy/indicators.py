"""Pure-Python technical indicators.

Every function takes a list of Candle objects and returns a list of the same
length.  Leading values that cannot be computed (insufficient history) are None.

Injected as `ta` into the backtest sandbox so user scripts can call:
    ta.sma(candles, 20)
    ta.ema(candles, 9)
    ta.vwap(candles)
    ta.rsi(candles, 14)
    ta.atr(candles, 14)
    ta.bollinger(candles, 20, 2.0)   -> list[dict | None]  keys: upper, mid, lower
    ta.macd(candles, 12, 26, 9)      -> list[dict | None]  keys: macd, signal, hist
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.market_data import Candle


# ---------------------------------------------------------------------------
# Simple Moving Average
# ---------------------------------------------------------------------------

def sma(candles: list, period: int) -> list[float | None]:
    closes = [c.close for c in candles]
    result: list[float | None] = []
    for i in range(len(closes)):
        if i + 1 < period:
            result.append(None)
        else:
            result.append(sum(closes[i + 1 - period : i + 1]) / period)
    return result


# ---------------------------------------------------------------------------
# Exponential Moving Average
# ---------------------------------------------------------------------------

def ema(candles: list, period: int) -> list[float | None]:
    closes = [c.close for c in candles]
    result: list[float | None] = [None] * len(closes)
    k = 2.0 / (period + 1)
    # seed with SMA of first `period` bars
    seed_end = period - 1
    if seed_end >= len(closes):
        return result
    result[seed_end] = sum(closes[:period]) / period
    for i in range(seed_end + 1, len(closes)):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)  # type: ignore[operator]
    return result


# ---------------------------------------------------------------------------
# VWAP — resets at the start of each calendar day
# ---------------------------------------------------------------------------

def vwap(candles: list) -> list[float | None]:
    result: list[float | None] = []
    cum_tp_vol = 0.0
    cum_vol = 0
    prev_date = None

    for c in candles:
        day = c.time.date()
        if day != prev_date:
            cum_tp_vol = 0.0
            cum_vol = 0
            prev_date = day
        typical = (c.high + c.low + c.close) / 3
        cum_tp_vol += typical * c.volume
        cum_vol += c.volume
        result.append(round(cum_tp_vol / cum_vol, 4) if cum_vol else None)

    return result


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi(candles: list, period: int = 14) -> list[float | None]:
    closes = [c.close for c in candles]
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result

    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def _rsi_val(ag: float, al: float) -> float:
        if al == 0:
            return 100.0
        rs = ag / al
        return 100.0 - (100.0 / (1 + rs))

    result[period] = _rsi_val(avg_gain, avg_loss)

    for i in range(period + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        result[i] = _rsi_val(avg_gain, avg_loss)

    return result


# ---------------------------------------------------------------------------
# ATR — Average True Range
# ---------------------------------------------------------------------------

def atr(candles: list, period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period + 1:
        return result

    def _tr(i: int) -> float:
        c = candles[i]
        prev_close = candles[i - 1].close
        return max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))

    # seed
    trs = [_tr(i) for i in range(1, period + 1)]
    avg = sum(trs) / period
    result[period] = avg

    for i in range(period + 1, len(candles)):
        avg = (avg * (period - 1) + _tr(i)) / period
        result[i] = round(avg, 4)

    return result


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def bollinger(candles: list, period: int = 20, std_dev: float = 2.0) -> list[dict | None]:
    closes = [c.close for c in candles]
    result: list[dict | None] = []
    for i in range(len(closes)):
        if i + 1 < period:
            result.append(None)
            continue
        window = closes[i + 1 - period : i + 1]
        mid = sum(window) / period
        variance = sum((x - mid) ** 2 for x in window) / period
        sd = math.sqrt(variance)
        result.append({
            "upper": round(mid + std_dev * sd, 4),
            "mid": round(mid, 4),
            "lower": round(mid - std_dev * sd, 4),
        })
    return result


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    candles: list,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> list[dict | None]:
    fast_ema = ema(candles, fast)
    slow_ema = ema(candles, slow)

    macd_line: list[float | None] = [
        round(f - s, 4) if f is not None and s is not None else None
        for f, s in zip(fast_ema, slow_ema)
    ]

    # EMA of macd_line for the signal line
    result: list[dict | None] = [None] * len(candles)
    valid_indices = [i for i, v in enumerate(macd_line) if v is not None]
    if len(valid_indices) < signal_period:
        return result

    # seed signal EMA from first `signal_period` MACD values
    start = valid_indices[signal_period - 1]
    seed_vals = [macd_line[i] for i in valid_indices[:signal_period]]
    sig_val = sum(seed_vals) / signal_period  # type: ignore[arg-type]
    k = 2.0 / (signal_period + 1)

    result[start] = {
        "macd": macd_line[start],
        "signal": round(sig_val, 4),
        "hist": round(macd_line[start] - sig_val, 4),  # type: ignore[operator]
    }

    for i in range(start + 1, len(candles)):
        if macd_line[i] is None:
            continue
        sig_val = macd_line[i] * k + sig_val * (1 - k)  # type: ignore[operator]
        result[i] = {
            "macd": macd_line[i],
            "signal": round(sig_val, 4),
            "hist": round(macd_line[i] - sig_val, 4),  # type: ignore[operator]
        }

    return result
