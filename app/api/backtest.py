from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.market_data import Timeframe
from app.services.backtest import (
    DEFAULT_SCRIPT,
    aggregate_batch_results,
    build_backtest_response,
    normalize_symbols,
    run_backtest_core,
    summarize_result,
    summarize_result_with_daily,
    validate_strategy_script,
)

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class BacktestConfigFields(BaseModel):
    starting_capital: float = Field(default=10000.0, gt=0)
    position_size: float = Field(default=1000.0, gt=0)
    max_entries: int = Field(default=5, ge=1, le=100)


class CustomBacktestRequest(BacktestConfigFields):
    symbol: str = "NBIS"
    timeframe: Timeframe = Timeframe.ONE_HOUR
    limit: int = 1638
    days: int | None = Field(default=None, ge=1, le=365)
    script: str = DEFAULT_SCRIPT


class BatchBacktestRequest(BacktestConfigFields):
    symbols: list[str]
    timeframes: list[Timeframe] = [
        Timeframe.FIVE_MINUTES,
        Timeframe.FIFTEEN_MINUTES,
    ]
    limit: int = 1638
    days: int | None = Field(default=None, ge=1, le=365)
    script: str = DEFAULT_SCRIPT


class QuickBacktestRequest(BacktestConfigFields):
    symbol: str = "NBIS"
    timeframes: list[Timeframe] = [
        Timeframe.ONE_MINUTE,
        Timeframe.THREE_MINUTES,
        Timeframe.FIVE_MINUTES,
        Timeframe.FIFTEEN_MINUTES,
    ]
    limit: int = 1638
    days: int | None = Field(default=None, ge=1, le=365)
    script: str = DEFAULT_SCRIPT


class ValidateScriptRequest(BaseModel):
    script: str


def pick_best_timeframe(rows: list[dict]) -> Timeframe | None:
    successful_rows = [row for row in rows if row["status"] == "ok" and row.get("summary")]
    if not successful_rows:
        return None

    best_row = max(
        successful_rows,
        key=lambda row: (
            row["summary"]["total_pnl_pct"],
            row["summary"]["total_pnl"],
            row["summary"]["win_rate"],
        ),
    )
    return best_row["timeframe"]


@router.post("/validate-script")
def validate_script(body: ValidateScriptRequest) -> dict:
    validate_strategy_script(body.script)
    return {"valid": True}


@router.post("/batch")
def run_batch_backtest(body: BatchBacktestRequest) -> dict:
    validate_strategy_script(body.script)

    symbols = normalize_symbols(body.symbols)
    if not symbols:
        raise HTTPException(status_code=422, detail="at least one symbol is required")
    if not body.timeframes:
        raise HTTPException(status_code=422, detail="at least one timeframe is required")

    rows: list[dict] = []
    successful_results = []
    for symbol in symbols:
        for timeframe in body.timeframes:
            try:
                _, _, result = run_backtest_core(
                    symbol,
                    timeframe,
                    body.limit,
                    body.script,
                    body.starting_capital,
                    body.position_size,
                    body.max_entries,
                    body.days,
                )
                summary = summarize_result(result)
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
        "aggregate": aggregate_batch_results(successful_results),
    }


@router.post("/quick")
def run_quick_backtest(body: QuickBacktestRequest) -> dict:
    validate_strategy_script(body.script)

    sym = body.symbol.strip().upper()
    if not sym:
        raise HTTPException(status_code=422, detail="symbol is required")
    if not body.timeframes:
        raise HTTPException(status_code=422, detail="at least one timeframe is required")

    rows: list[dict] = []
    for timeframe in body.timeframes:
        try:
            candles, _, result = run_backtest_core(
                sym,
                timeframe,
                body.limit,
                body.script,
                body.starting_capital,
                body.position_size,
                body.max_entries,
                body.days,
            )
            row = {
                "timeframe": timeframe,
                "status": "ok",
                **summarize_result_with_daily(result, candles),
            }
            rows.append(row)
        except HTTPException as exc:
            rows.append({
                "timeframe": timeframe,
                "status": "error",
                "error": str(exc.detail),
            })

    return {
        "symbol": sym,
        "rows": rows,
        "best_timeframe": pick_best_timeframe(rows),
    }


@router.post("")
def run_backtest(body: CustomBacktestRequest) -> dict:
    sym = body.symbol.strip().upper()
    return build_backtest_response(
        sym,
        body.timeframe,
        body.limit,
        body.script,
        body.starting_capital,
        body.position_size,
        body.max_entries,
        body.days,
    )
