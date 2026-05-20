"""API routes for tick-level backtesting."""
from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.db.database import get_db_context
from app.db.strategies import StrategyRepository
from app.models.market_data import Timeframe
from app.services.backtest import summarize_daily_snapshots, summarize_result
from app.services.tick_fetcher import tick_fetcher
from app.strategy.sandbox import compile_tick_script, validate_tick_script
from app.strategy.tick_backtest import TickBacktestConfig, run_tick_backtest

router = APIRouter(prefix="/api/tick-backtest", tags=["tick-backtest"])
strategy_repo = StrategyRepository()

DEFAULT_TICK_SCRIPT = """\
STRATEGY_NAME = "unnamed"

# Tick-level strategy: called once for every 5-second bar.
#
# state.tick          — current 5s Candle (.time, .open, .high, .low, .close, .volume)
# state.candles       — dict[Timeframe, list[Candle]]  completed higher-TF candles
# state.current_candles — dict[Timeframe, Candle|None]  in-progress candles
# state.closed        — dict[Timeframe, Candle|None]   candle that just closed this tick
# state.position      — PositionInfo|None  (.shares, .avg_price, .unrealized_pnl)
# state.cash          — available cash
# state.portfolio_value — cash + market value
#
# Return {"signal": "buy"} or {"signal": "sell"} or {"signal": None}

def on_tick(state):
    # Example: buy when a 5m candle closes with RSI crossing above 30
    closed_5m = state.closed.get("5m")
    if closed_5m is None:
        return {"signal": None}

    candles_5m = state.candles.get("5m", [])
    if len(candles_5m) < 15:
        return {"signal": None}

    rsi = ta.rsi(candles_5m, 14)

    if state.position is None:
        if rsi[-2] is not None and rsi[-2] <= 30 and rsi[-1] is not None and rsi[-1] > 30:
            return {"signal": "buy"}
    else:
        if rsi[-2] is not None and rsi[-2] >= 70 and rsi[-1] is not None and rsi[-1] < 70:
            return {"signal": "sell"}

    return {"signal": None}
"""


class FetchTicksRequest(BaseModel):
    symbol: str = "NBIS"
    start_date: date | None = None
    end_date: date | None = None
    force: bool = False
    extended: bool = True


def _extract_strategy_name(script: str) -> str:
    """Extract STRATEGY_NAME variable from script, default to 'unnamed'."""
    m = re.search(r'^STRATEGY_NAME\s*=\s*["\'](.+?)["\']', script, re.MULTILINE)
    return m.group(1) if m else "unnamed"


class RunTickBacktestRequest(BaseModel):
    symbol: str = "NBIS"
    start_date: date | None = None
    end_date: date | None = None
    extended: bool = True
    script: str = DEFAULT_TICK_SCRIPT
    description: str | None = None
    starting_capital: float = Field(default=10000.0, gt=0)
    position_size: float = Field(default=1000.0, gt=0)
    max_entries: int = Field(default=5, ge=1, le=100)
    candle_timeframes: list[Timeframe] = Field(default=[
        Timeframe.ONE_MINUTE,
        Timeframe.FIVE_MINUTES,
        Timeframe.FIFTEEN_MINUTES,
    ])
    fee_per_share: float = Field(default=0.005, ge=0, description="USD per share (IBKR Fixed default: 0.005)")
    fee_min_order: float = Field(default=1.00, ge=0, description="Minimum commission per order (IBKR Fixed default: 1.00)")
    fee_max_pct: float = Field(default=1.0, ge=0, le=10, description="Maximum commission as % of trade value (IBKR Fixed default: 1%)")


@router.post("/fetch")
async def fetch_ticks(body: FetchTicksRequest) -> dict:
    """Fetch 5-second tick data from IBKR and store in the database."""
    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")

    try:
        resolved_start_date, resolved_end_date, trading_dates = tick_fetcher.resolve_date_range(
            body.start_date,
            body.end_date,
            extended=body.extended,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = await tick_fetcher.fetch_and_store(
        sym,
        start_date=body.start_date,
        end_date=body.end_date,
        force=body.force,
        extended=body.extended,
    )
    return {
        "symbol": sym,
        "start_date": body.start_date.isoformat() if body.start_date else None,
        "end_date": body.end_date.isoformat() if body.end_date else None,
        "resolved_start_date": resolved_start_date.isoformat(),
        "resolved_end_date": resolved_end_date.isoformat(),
        "trading_day_count": len(trading_dates),
        **result,
    }


@router.post("/run")
async def run_tick_backtest_endpoint(body: RunTickBacktestRequest):
    """Run a tick-level backtest, streaming progress as SSE events."""
    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")

    # Validate script early (before streaming starts)
    try:
        validate_tick_script(body.script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    on_tick_fn = compile_tick_script(body.script)

    try:
        resolved_start_date, resolved_end_date, trading_dates = tick_fetcher.resolve_date_range(
            body.start_date,
            body.end_date,
            extended=body.extended,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async def event_stream():
        def _sse(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        # Stage 1: Fetch data
        yield _sse({"stage": "fetch", "message": "Fetching data..."})

        fetch_result = await tick_fetcher.fetch_and_store(
            sym,
            start_date=body.start_date,
            end_date=body.end_date,
            extended=body.extended,
            on_progress=lambda p: None,  # progress tracked via total/cached
        )
        cached = fetch_result["cached_chunks"]
        fetched = fetch_result["fetched_chunks"]
        total = fetch_result["total_chunks"]
        if fetched > 0:
            yield _sse({"stage": "fetch", "message": f"Fetched {fetched} new chunks ({cached} cached, {total} total)"})
        else:
            yield _sse({"stage": "fetch", "message": f"All {total} chunks cached"})

        # Stage 2: Load ticks
        yield _sse({"stage": "load", "message": "Loading ticks..."})
        ticks = await tick_fetcher.load_ticks(
            sym,
            start_date=body.start_date,
            end_date=body.end_date,
            extended=body.extended,
        )
        if not ticks:
            yield _sse({"stage": "error", "message": f"No tick data available for {sym}"})
            return

        yield _sse({"stage": "load", "message": f"Loaded {len(ticks):,} ticks"})

        # Stage 3: Run backtest
        yield _sse({"stage": "backtest", "message": "Running backtest...", "total_days": 0})

        progress_queue: asyncio.Queue = asyncio.Queue()

        def backtest_progress(p: dict):
            progress_queue.put_nowait(p)

        config = TickBacktestConfig(
            starting_capital=body.starting_capital,
            position_size=body.position_size,
            max_entries=body.max_entries,
            candle_timeframes=body.candle_timeframes,
            fee_per_share=body.fee_per_share,
            fee_min_order=body.fee_min_order,
            fee_max_pct=body.fee_max_pct,
        )

        # Run backtest in thread, poll queue for progress
        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(
            None, lambda: run_tick_backtest(ticks, on_tick_fn, config, on_progress=backtest_progress)
        )

        while not task.done():
            try:
                p = await asyncio.wait_for(progress_queue.get(), timeout=0.1)
                yield _sse({
                    "stage": "backtest",
                    "message": f"Processing day {p['completed_days']}/{p['total_days']}",
                })
            except asyncio.TimeoutError:
                continue

        result = await task

        # Drain remaining progress events
        while not progress_queue.empty():
            p = progress_queue.get_nowait()
            yield _sse({
                "stage": "backtest",
                "message": f"Processing day {p['completed_days']}/{p['total_days']}",
            })

        yield _sse({"stage": "backtest", "message": f"Backtest complete — {len(ticks):,} ticks processed"})

        # Stage 4: Save results
        yield _sse({"stage": "save", "message": "Saving results..."})
        algo_name = _extract_strategy_name(body.script)
        async with get_db_context() as session:
            algo = await strategy_repo.save_algorithm(
                session, algo_name, body.script, body.description,
            )
            result_data = {
                "summary": summarize_result(result),
                "daily": summarize_daily_snapshots(result),
            }
            run = await strategy_repo.save_run(
                session,
                algorithm_id=algo.id,
                symbol=sym,
                config={
                    "starting_capital": body.starting_capital,
                    "position_size": body.position_size,
                    "max_entries": body.max_entries,
                    "start_date": body.start_date.isoformat() if body.start_date else None,
                    "end_date": body.end_date.isoformat() if body.end_date else None,
                    "resolved_start_date": resolved_start_date.isoformat(),
                    "resolved_end_date": resolved_end_date.isoformat(),
                    "candle_timeframes": [tf.value for tf in body.candle_timeframes],
                },
                result_data=result_data,
                mode="tick",
                lookback_days=len(trading_dates),
            )

        # Build 1-minute OHLCV candles with VWAP per trading day
        def _trading_date_str(candle) -> str:
            d = candle.time.date()
            if candle.time.hour < 8:
                d = d - timedelta(days=1)
            return d.isoformat()

        candles_by_day: dict[str, list] = defaultdict(list)
        vwap_state: dict[str, dict] = {}  # cum_vp, cum_vol per day
        tick_idx_by_day: dict[str, int] = defaultdict(int)

        # Group ticks into 1-minute bars
        bar_state: dict[str, dict] = {}  # day → current bar accumulator

        for t in ticks:
            day = _trading_date_str(t)
            tick_idx_by_day[day] += 1

            # VWAP accumulator
            tp = (t.high + t.low + t.close) / 3.0
            st = vwap_state.setdefault(day, {"cum_vp": 0.0, "cum_vol": 0})
            st["cum_vp"] += tp * t.volume
            st["cum_vol"] += t.volume

            # Truncate tick time to the minute for bar grouping
            bar_minute = t.time.replace(second=0, microsecond=0)
            bar_key = bar_minute.isoformat()

            cur = bar_state.get(day)
            if cur is None or cur["key"] != bar_key:
                # Close the previous bar (if any)
                if cur is not None:
                    vwap_at_close = cur["cum_vp"] / cur["cum_vol"] if cur["cum_vol"] > 0 else cur["c"]
                    candles_by_day[day].append({
                        "t": cur["key"],
                        "o": round(cur["o"], 4),
                        "h": round(cur["h"], 4),
                        "l": round(cur["l"], 4),
                        "c": round(cur["c"], 4),
                        "v": round(vwap_at_close, 4),
                    })
                # Start a new bar
                bar_state[day] = {
                    "key": bar_key,
                    "o": t.open,
                    "h": t.high,
                    "l": t.low,
                    "c": t.close,
                    "cum_vp": st["cum_vp"],
                    "cum_vol": st["cum_vol"],
                }
            else:
                cur["h"] = max(cur["h"], t.high)
                cur["l"] = min(cur["l"], t.low)
                cur["c"] = t.close
                cur["cum_vp"] = st["cum_vp"]
                cur["cum_vol"] = st["cum_vol"]

        # Flush the last open bar for each day
        for day, cur in bar_state.items():
            vwap_at_close = cur["cum_vp"] / cur["cum_vol"] if cur["cum_vol"] > 0 else cur["c"]
            candles_by_day[day].append({
                "t": cur["key"],
                "o": round(cur["o"], 4),
                "h": round(cur["h"], 4),
                "l": round(cur["l"], 4),
                "c": round(cur["c"], 4),
                "v": round(vwap_at_close, 4),
            })

        # Tick counts per day so the frontend can flag partial days
        ticks_per_day = dict(tick_idx_by_day)

        # Serialize trades
        trades_data = []
        for trade in result.trades:
            trades_data.append({
                "entry_time": trade.entry_time,
                "exit_time": trade.exit_time,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "dollar_pnl": trade.dollar_pnl,
                "pnl_pct": trade.pnl_pct,
                "shares": trade.shares,
                "entries": trade.entries,
            })

        # Open position entries (buys with no corresponding sell yet)
        open_entries_data = result.open_entries

        # Final result event
        yield _sse({
            "stage": "done",
            "result": {
                "algorithm": {
                    "id": algo.id,
                    "name": algo.name,
                    "version": algo.version,
                },
                "run": {"id": run.id},
                "tick_count": len(ticks),
                "resolved_start_date": resolved_start_date.isoformat(),
                "resolved_end_date": resolved_end_date.isoformat(),
                "trading_day_count": len(trading_dates),
                "trades": trades_data,
                "open_entries": open_entries_data,
                "price_series": dict(candles_by_day),
                "ticks_per_day": ticks_per_day,
                **result_data,
            },
        })

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/algorithms")
async def list_algorithms() -> dict:
    """List all saved strategy algorithms."""
    async with get_db_context() as session:
        algos = await strategy_repo.list_algorithms(session)

    return {
        "algorithms": [
            {
                "id": a.id,
                "name": a.name,
                "version": a.version,
                "description": a.description,
                "is_favorite": a.is_favorite,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in algos
        ],
    }


@router.get("/runs")
async def list_runs(algorithm_id: str | None = None, symbol: str | None = None) -> dict:
    """List backtest runs, optionally filtered by algorithm or symbol."""
    async with get_db_context() as session:
        runs = await strategy_repo.list_runs(session, algorithm_id, symbol)

    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(run_id: str) -> dict:
    """Get full details of a backtest run."""
    async with get_db_context() as session:
        run = await strategy_repo.get_run(session, run_id)

    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return {
        "id": run.id,
        "algorithm_id": run.algorithm_id,
        "symbol": run.symbol,
        "mode": run.mode,
        "lookback_days": run.lookback_days,
        "config": run.config_json,
        "result": run.result_json,
        "num_trades": run.num_trades,
        "total_pnl": run.total_pnl,
        "total_pnl_pct": run.total_pnl_pct,
        "win_rate": run.win_rate,
        "final_balance": run.final_balance,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/data-status/{symbol}")
async def get_data_status(symbol: str) -> dict:
    """Get information about available tick data for a symbol."""
    sym = symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")

    return await tick_fetcher.get_data_status(sym)


@router.get("/algorithms/favorites")
async def list_favorites() -> dict:
    """List favorite algorithms."""
    async with get_db_context() as session:
        algos = await strategy_repo.list_favorites(session)

    return {
        "algorithms": [
            {
                "id": a.id,
                "name": a.name,
                "version": a.version,
                "description": a.description,
                "script": a.script,
                "is_favorite": a.is_favorite,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in algos
        ],
    }


@router.get("/algorithms/{algorithm_id}")
async def get_algorithm(algorithm_id: str) -> dict:
    """Get a specific algorithm including its script."""
    async with get_db_context() as session:
        algo = await strategy_repo.get_algorithm(session, algorithm_id)

    if algo is None:
        raise HTTPException(status_code=404, detail="Algorithm not found")

    return {
        "id": algo.id,
        "name": algo.name,
        "version": algo.version,
        "script": algo.script,
        "description": algo.description,
        "is_favorite": algo.is_favorite,
        "created_at": algo.created_at.isoformat() if algo.created_at else None,
    }


@router.patch("/algorithms/{algorithm_id}/favorite")
async def toggle_favorite(algorithm_id: str) -> dict:
    """Toggle the favorite status of an algorithm."""
    async with get_db_context() as session:
        algo = await strategy_repo.get_algorithm(session, algorithm_id)
        if algo is None:
            raise HTTPException(status_code=404, detail="Algorithm not found")
        algo = await strategy_repo.set_favorite(session, algorithm_id, not algo.is_favorite)

    return {
        "id": algo.id,
        "name": algo.name,
        "version": algo.version,
        "is_favorite": algo.is_favorite,
    }
