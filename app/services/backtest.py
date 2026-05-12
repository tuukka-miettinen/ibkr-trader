from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import HTTPException

from app.models.market_data import Timeframe
from app.providers.base import MarketDataError
from app.services.candles import candle_service
from app.strategy import backtest as bt
from app.strategy.sandbox import run_user_script, validate_user_script

SYMBOL_ALIASES = {
    "RWD": "RDW",
}

RESERVED_KEYS = {"time", "signal", "markers"}
DEFAULT_LOOKBACK_DAYS = 21
TRADING_DAY_BARS = {
    Timeframe.FIVE_SECONDS: 4680,
    Timeframe.ONE_MINUTE: 390,
    Timeframe.THREE_MINUTES: 130,
    Timeframe.FIVE_MINUTES: 78,
    Timeframe.FIFTEEN_MINUTES: 26,
    Timeframe.ONE_HOUR: 7,
}

DEFAULT_SCRIPT = """\
# ta module is available: ta.sma, ta.ema, ta.vwap, ta.rsi, ta.atr, ta.bollinger, ta.macd
# Each candle has: .time (datetime), .open, .high, .low, .close, .volume
# Return a list of dicts with at least {"time": iso_str, "signal": "buy"|"sell"|None}
# Any extra numeric keys you include (e.g. "ema_20") will be plotted on the chart automatically.
# To force a separate pane, use e.g. "atr_14": {"value": atr14[i], "separate": True}
# Optional per-bar markers: "markers": [{"text": "RSI bull", "shape": "circle", "position": "belowBar", "color": "#f59e0b"}]

def signals(candles):
    ema20 = ta.ema(candles, 20)
    rsi14 = ta.rsi(candles, 14)
    vwap = ta.vwap(candles)
    results = []
    for i, bar in enumerate(candles):
        signal = None
        markers = []
        if i > 0 and ema20[i] is not None and rsi14[i] is not None and vwap[i] is not None:
            prev_rsi = rsi14[i - 1]
            prev_ema20 = ema20[i - 1]
            if prev_rsi is not None and prev_ema20 is not None:
                long_trend = bar.close > ema20[i] and bar.close > vwap[i]
                short_trend = bar.close < ema20[i] and bar.close < vwap[i]

                if candles[i - 1].close <= prev_ema20 and bar.close > ema20[i]:
                    markers.append({"text": "EMA20+", "shape": "circle", "position": "belowBar", "color": "#38bdf8"})
                elif candles[i - 1].close >= prev_ema20 and bar.close < ema20[i]:
                    markers.append({"text": "EMA20-", "shape": "circle", "position": "aboveBar", "color": "#38bdf8"})

                if prev_rsi <= 30 and rsi14[i] > 30:
                    markers.append({"text": "RSI bull", "shape": "square", "position": "belowBar", "color": "#f59e0b"})
                elif prev_rsi >= 70 and rsi14[i] < 70:
                    markers.append({"text": "RSI bear", "shape": "square", "position": "aboveBar", "color": "#f59e0b"})

                if long_trend and prev_rsi <= 30 and rsi14[i] > 30:
                    signal = "buy"
                elif short_trend and prev_rsi >= 70 and rsi14[i] < 70:
                    signal = "sell"
        results.append({
            "time": bar.time.isoformat(),
            "signal": signal,
            "ema_20": ema20[i],
            "rsi_14": rsi14[i],
            "vwap": vwap[i],
            "markers": markers,
        })
    return results
"""


def is_indicator_config(value: object) -> bool:
    return isinstance(value, dict) and "value" in value


def extract_markers(sigs: list[dict]) -> list[dict]:
    markers: list[dict] = []
    for sig in sigs:
        raw_markers = sig.get("markers")
        if raw_markers is None:
            continue
        if isinstance(raw_markers, dict):
            raw_markers = [raw_markers]
        if not isinstance(raw_markers, list):
            continue

        for marker in raw_markers:
            if not isinstance(marker, dict):
                continue
            markers.append({
                "time": marker.get("time", sig["time"]),
                "text": str(marker.get("text", "")),
                "shape": marker.get("shape", "circle"),
                "position": marker.get("position", "aboveBar"),
                "color": marker.get("color", "#94a3b8"),
            })
    return markers


def normalize_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_symbol in symbols:
        symbol = SYMBOL_ALIASES.get(raw_symbol.strip().upper(), raw_symbol.strip().upper())
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def resolve_lookback_limit(
    timeframe: Timeframe,
    limit: int | None = None,
    days: int | None = None,
) -> int:
    if days is not None:
        # +1 day so the provider fetches enough data; excess is trimmed later
        resolved_limit = TRADING_DAY_BARS[timeframe] * (days + 1)
    elif limit is not None:
        resolved_limit = limit
    else:
        resolved_limit = TRADING_DAY_BARS[timeframe] * DEFAULT_LOOKBACK_DAYS

    if not (1 <= resolved_limit <= 10000):
        raise HTTPException(status_code=422, detail="resolved lookback must be 1–10000 bars")

    return resolved_limit


def _trim_to_last_n_trading_days(candles: list, days: int) -> list:
    """Keep only candles from the last *days* unique trading dates."""
    if not candles:
        return candles
    seen: list[str] = []
    seen_set: set[str] = set()
    for c in candles:
        d = c.time.isoformat().split("T", 1)[0]
        if d not in seen_set:
            seen_set.add(d)
            seen.append(d)
    keep = set(seen[-days:])
    return [c for c in candles if c.time.isoformat().split("T", 1)[0] in keep]


def run_backtest_core(
    sym: str,
    timeframe: Timeframe,
    limit: int | None,
    script: str,
    starting_capital: float = 10000.0,
    position_size: float = 1000.0,
    max_entries: int = 5,
    days: int | None = None,
) -> tuple[list, list[dict], bt.BacktestResult]:
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")

    resolved_limit = resolve_lookback_limit(timeframe, limit, days)

    try:
        candles = candle_service.get_history(sym, timeframe, resolved_limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if days is not None:
        candles = _trim_to_last_n_trading_days(candles, days)

    try:
        sigs = run_user_script(script, candles)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    config = bt.BacktestConfig(
        starting_capital=starting_capital,
        position_size=position_size,
        max_entries=max_entries,
    )
    return candles, sigs, bt.run(candles, sigs, config)


def summarize_result(result: bt.BacktestResult) -> dict:
    return {
        "num_trades": result.num_trades,
        "total_pnl": result.total_pnl,
        "total_dollar_pnl": result.total_dollar_pnl,
        "total_pnl_pct": result.total_pnl_pct,
        "win_rate": result.win_rate,
        "starting_capital": result.starting_capital,
        "final_balance": result.final_balance,
    }


def summarize_trades(trades: list[bt.Trade]) -> dict:
    total_pnl = round(sum(trade.pnl for trade in trades), 4)
    total_cost = sum(trade.total_cost for trade in trades)
    total_pnl_pct = round(total_pnl / total_cost * 100, 4) if total_cost > 0 else 0.0
    wins = sum(1 for trade in trades if trade.pnl > 0)
    return {
        "num_trades": len(trades),
        "total_pnl": total_pnl,
        "total_dollar_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0.0,
    }


def summarize_daily_snapshots(result: bt.BacktestResult) -> list[dict]:
    return [
        {
            "date": snap.date,
            "num_trades": snap.realized_trades,
            "total_pnl": snap.realized_pnl,
            "total_dollar_pnl": snap.realized_pnl,
            "total_pnl_pct": round(snap.realized_pnl / snap.position_cost * 100, 4)
            if snap.realized_trades and snap.position_cost > 0
            else 0.0,
            "win_rate": snap.win_rate,
            "avg_trade_pct": snap.avg_trade_pct,
            "day_buys": snap.day_buys,
            "day_sells": snap.day_sells,
            "unrealized_pnl": snap.unrealized_pnl,
            "position_shares": snap.position_shares,
            "position_cost": snap.position_cost,
            "day_close_price": snap.day_close_price,
        }
        for snap in result.daily_snapshots
    ]


def summarize_result_with_daily(
    result: bt.BacktestResult,
    candles: list | None = None,
) -> dict:
    return {
        "summary": summarize_result(result),
        "daily": summarize_daily_snapshots(result),
    }


def _summarize_daily_trades_from_list(trades: list[bt.Trade]) -> list[dict]:
    """Group completed trades by exit date for batch aggregation."""
    daily_trades: dict[str, list[bt.Trade]] = defaultdict(list)
    for trade in trades:
        daily_trades[trade.exit_time.split("T", 1)[0]].append(trade)
    return [
        {"date": date, **summarize_trades(day_trades)}
        for date, day_trades in sorted(daily_trades.items())
    ]


def aggregate_batch_results(results: list[bt.BacktestResult]) -> dict:
    all_trades = [trade for result in results for trade in result.trades]
    total_starting_capital = round(sum(result.starting_capital for result in results), 4)
    total_final_balance = round(sum(result.final_balance for result in results), 4)
    overall = summarize_trades(all_trades)
    overall["starting_capital"] = total_starting_capital
    overall["final_balance"] = total_final_balance
    if total_starting_capital > 0:
        overall["total_pnl_pct"] = round(overall["total_pnl"] / total_starting_capital * 100, 4)

    return {
        "overall": overall,
        "daily": _summarize_daily_trades_from_list(all_trades),
    }


def build_backtest_summary(
    sym: str,
    timeframe: Timeframe,
    limit: int | None,
    script: str,
    starting_capital: float = 10000.0,
    position_size: float = 1000.0,
    max_entries: int = 5,
    days: int | None = None,
) -> dict:
    _, _, result = run_backtest_core(sym, timeframe, limit, script, starting_capital, position_size, max_entries, days)
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "summary": summarize_result(result),
    }


def build_backtest_response(
    sym: str,
    timeframe: Timeframe,
    limit: int | None,
    script: str,
    starting_capital: float = 10000.0,
    position_size: float = 1000.0,
    max_entries: int = 5,
    days: int | None = None,
) -> dict[str, Any]:
    candles, sigs, result = run_backtest_core(sym, timeframe, limit, script, starting_capital, position_size, max_entries, days)
    markers = extract_markers(sigs)

    indicator_keys: set[str] = set()
    for sig in sigs:
        indicator_keys.update(k for k in sig if k not in RESERVED_KEYS)

    indicators: dict[str, list] = {}
    indicator_separate: dict[str, bool] = {}
    for key in sorted(indicator_keys):
        series = []
        for index, sig in enumerate(sigs):
            val = sig.get(key)
            if is_indicator_config(val):
                indicator_separate[key] = bool(val.get("separate", False))
                series.append(val.get("value"))
                continue
            if isinstance(val, dict):
                for sub_k, sub_v in val.items():
                    full_key = f"{key}_{sub_k}"
                    indicators.setdefault(full_key, [None] * len(sigs))
                    indicators[full_key][index] = sub_v
            else:
                series.append(val)
        if series:
            indicators[key] = series

    return {
        "symbol": sym,
        "timeframe": timeframe,
        "candles": [
            {"time": c.time.isoformat(), "open": c.open, "high": c.high, "low": c.low, "close": c.close}
            for c in candles
        ],
        "signals": [{"time": s["time"], "signal": s["signal"]} for s in sigs],
        "markers": markers,
        "indicators": indicators,
        "indicator_separate": indicator_separate,
        "trades": [t.__dict__ for t in result.trades],
        "summary": summarize_result(result),
    }


def validate_strategy_script(script: str) -> None:
    try:
        validate_user_script(script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc