from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.market_data import Timeframe
from app.providers.base import MarketDataError
from app.services.candles import candle_service
from app.strategy import backtest as bt
from app.strategy.sandbox import run_user_script, validate_user_script

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

SYMBOL_ALIASES = {
    "RWD": "RDW",
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

# Keys that are not indicator series
_RESERVED_KEYS = {"time", "signal", "markers"}


def _is_indicator_config(value: object) -> bool:
    return isinstance(value, dict) and "value" in value


def _extract_markers(sigs: list[dict]) -> list[dict]:
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


class CustomBacktestRequest(BaseModel):
    symbol: str = "AAPL"
    timeframe: Timeframe = Timeframe.ONE_HOUR
    limit: int = 1638
    script: str = DEFAULT_SCRIPT


class BatchBacktestRequest(BaseModel):
    symbols: list[str]
    timeframes: list[Timeframe] = [
        Timeframe.FIVE_MINUTES,
        Timeframe.FIFTEEN_MINUTES,
    ]
    limit: int = 1638
    script: str = DEFAULT_SCRIPT


class ValidateScriptRequest(BaseModel):
    script: str


@router.post("/validate-script")
def validate_script(body: ValidateScriptRequest) -> dict:
    try:
        validate_user_script(body.script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"valid": True}


def _normalise_symbols(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw_symbol in symbols:
        symbol = SYMBOL_ALIASES.get(raw_symbol.strip().upper(), raw_symbol.strip().upper())
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _run_backtest_core(sym: str, timeframe: Timeframe, limit: int, script: str) -> tuple[list, list[dict], bt.BacktestResult]:
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")
    if not (50 <= limit <= 10000):
        raise HTTPException(status_code=422, detail="limit must be 50–10000")

    try:
        candles = candle_service.get_history(sym, timeframe, limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    try:
        sigs = run_user_script(script, candles)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return candles, sigs, bt.run(candles, sigs)


def _summarize_result(result: bt.BacktestResult) -> dict:
    return {
        "num_trades": result.num_trades,
        "total_pnl": result.total_pnl,
        "total_pnl_pct": result.total_pnl_pct,
        "win_rate": result.win_rate,
    }


def _summarize_trades(trades: list[bt.Trade]) -> dict:
    total_pnl = round(sum(trade.pnl for trade in trades), 4)
    total_pnl_pct = round(sum(trade.pnl_pct for trade in trades), 4)
    wins = sum(1 for trade in trades if trade.pnl > 0)
    return {
        "num_trades": len(trades),
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "win_rate": round(wins / len(trades) * 100, 1) if trades else 0.0,
    }


def _aggregate_batch_results(results: list[bt.BacktestResult]) -> dict:
    all_trades = [trade for result in results for trade in result.trades]
    daily_trades: dict[str, list[bt.Trade]] = defaultdict(list)
    for trade in all_trades:
      daily_trades[trade.exit_time.split("T", 1)[0]].append(trade)

    daily = [
        {"date": date, **_summarize_trades(trades)}
        for date, trades in sorted(daily_trades.items())
    ]

    return {
        "overall": _summarize_trades(all_trades),
        "daily": daily,
    }


def _build_backtest_summary(sym: str, timeframe: Timeframe, limit: int, script: str) -> dict:
    _, _, result = _run_backtest_core(sym, timeframe, limit, script)
    return {
        "symbol": sym,
        "timeframe": timeframe,
        "summary": _summarize_result(result),
    }


def _build_backtest_response(sym: str, timeframe: Timeframe, limit: int, script: str) -> dict:
    candles, sigs, result = _run_backtest_core(sym, timeframe, limit, script)
    markers = _extract_markers(sigs)

    indicator_keys: set[str] = set()
    for sig in sigs:
        indicator_keys.update(k for k in sig if k not in _RESERVED_KEYS)

    indicators: dict[str, list] = {}
    indicator_separate: dict[str, bool] = {}
    for key in sorted(indicator_keys):
        series = []
        for index, sig in enumerate(sigs):
            val = sig.get(key)
            if _is_indicator_config(val):
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
        "summary": _summarize_result(result),
    }


@router.post("/batch")
def run_batch_backtest(body: BatchBacktestRequest) -> dict:
    try:
        validate_user_script(body.script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    symbols = _normalise_symbols(body.symbols)
    if not symbols:
        raise HTTPException(status_code=422, detail="at least one symbol is required")
    if not body.timeframes:
        raise HTTPException(status_code=422, detail="at least one timeframe is required")
    if not (50 <= body.limit <= 10000):
        raise HTTPException(status_code=422, detail="limit must be 50–10000")

    rows: list[dict] = []
    successful_results: list[bt.BacktestResult] = []
    for symbol in symbols:
        for timeframe in body.timeframes:
            try:
                _, _, result = _run_backtest_core(symbol, timeframe, body.limit, body.script)
                summary = _summarize_result(result)
                rows.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "ok",
                    "summary": summary,
                })
                successful_results.append(result)
            except HTTPException as exc:
                rows.append({
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "status": "error",
                    "error": str(exc.detail),
                })

    return {
        "symbols": symbols,
        "timeframes": body.timeframes,
        "rows": rows,
        "aggregate": _aggregate_batch_results(successful_results),
    }


@router.post("")
def run_backtest(body: CustomBacktestRequest) -> dict:
    sym = body.symbol.strip().upper()
    return _build_backtest_response(sym, body.timeframe, body.limit, body.script)
