"""API routes for live paper-trading sessions."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.db.database import get_db_context
from app.db.live import LiveRepository
from app.services.live_engine import live_engine

router = APIRouter(prefix="/api/live", tags=["live"])
repo = LiveRepository()


def _iso_utc(dt: datetime | None) -> str | None:
    """Serialize a datetime as ISO-8601 with explicit UTC offset."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.isoformat() + "Z"
    return dt.isoformat()


# ── Request / response models ────────────────────────────────────────

class SymbolConfig(BaseModel):
    symbol: str
    algorithm_id: str


class CreateSessionRequest(BaseModel):
    name: str = "Paper Session"
    symbols: list[SymbolConfig]
    default_algorithm_id: str | None = None
    capital_per_symbol: float = Field(default=10000.0, gt=0)
    position_size: float = Field(default=1000.0, gt=0)
    max_entries: int = Field(default=5, ge=1, le=100)
    max_daily_loss: float = Field(default=500.0, gt=0)
    order_type: str = Field(default="market", pattern=r"^(market|limit)$")


# ── REST endpoints ───────────────────────────────────────────────────

@router.post("/sessions")
async def create_session(body: CreateSessionRequest) -> dict:
    """Create a new live trading session."""
    if not body.symbols and not body.default_algorithm_id:
        raise HTTPException(status_code=422, detail="Provide symbols or a default algorithm")

    # Resolve algorithm IDs — fill in default if not set per symbol
    symbol_configs = []
    for sc in body.symbols:
        algo_id = sc.algorithm_id or body.default_algorithm_id
        if not algo_id:
            raise HTTPException(
                status_code=422,
                detail=f"No algorithm specified for {sc.symbol} and no default set",
            )
        symbol_configs.append({"symbol": sc.symbol.upper(), "algorithm_id": algo_id})

    async with get_db_context() as db:
        live_session = await repo.create_session(
            db,
            name=body.name,
            order_type=body.order_type,
            position_size=body.position_size,
            max_entries=body.max_entries,
            max_daily_loss=body.max_daily_loss,
        )

        symbols_out = []
        for sc in symbol_configs:
            ss = await repo.add_session_symbol(
                db,
                session_id=live_session.id,
                symbol=sc["symbol"],
                algorithm_id=sc["algorithm_id"],
                allocated_capital=body.capital_per_symbol,
                position_size=body.position_size,
                max_entries=body.max_entries,
            )
            symbols_out.append({
                "id": ss.id,
                "symbol": ss.symbol,
                "algorithm_id": ss.algorithm_id,
                "allocated_capital": ss.allocated_capital,
            })

    return {
        "session": {
            "id": live_session.id,
            "name": live_session.name,
            "status": live_session.status,
            "order_type": live_session.order_type,
            "position_size": live_session.position_size,
            "max_entries": live_session.max_entries,
            "max_daily_loss": live_session.max_daily_loss,
            "created_at": _iso_utc(live_session.created_at),
        },
        "symbols": symbols_out,
    }


@router.get("/test-symbol/{symbol}")
async def test_symbol_connection(symbol: str) -> dict:
    """Test IBKR connectivity and market data permissions for a symbol."""
    from app.providers.ibkr_trading import IBKRTradingClient

    # Reuse the live engine's client; create + persist one if needed
    client = live_engine._client  # noqa: SLF001
    if client is None:
        client = IBKRTradingClient.from_env()
        live_engine._client = client  # noqa: SLF001
    try:
        if not client.is_connected:
            client.connect()
    except Exception as exc:
        return {
            "symbol": symbol.upper(),
            "ok": False,
            "error": f"Cannot connect to IBKR: {exc}",
            "exchange": None,
            "last_price": None,
        }

    try:
        result = client.test_symbol(symbol)
    except Exception as exc:
        result = {
            "symbol": symbol.upper(),
            "ok": False,
            "error": str(exc),
            "exchange": None,
            "last_price": None,
        }
    return result


@router.get("/sessions")
async def list_sessions() -> dict:
    """List all live trading sessions."""
    async with get_db_context() as db:
        sessions = await repo.list_sessions(db)

    return {
        "sessions": [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status,
                "order_type": s.order_type,
                "max_daily_loss": s.max_daily_loss,
                "created_at": _iso_utc(s.created_at),
                "started_at": _iso_utc(s.started_at),
                "stopped_at": _iso_utc(s.stopped_at),
                "error_message": s.error_message,
                "is_running": live_engine.is_session_running(s.id),
            }
            for s in sessions
        ]
    }


@router.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get detailed session info including live state if running."""
    async with get_db_context() as db:
        s = await repo.get_session(db, session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Session not found")
        session_symbols = await repo.get_session_symbols(db, session_id)

    live_state = live_engine.get_session_state(session_id)

    symbols_data = []
    for ss in session_symbols:
        sym_data = {
            "id": ss.id,
            "symbol": ss.symbol,
            "algorithm_id": ss.algorithm_id,
            "allocated_capital": ss.allocated_capital,
            "position_size": ss.position_size,
            "max_entries": ss.max_entries,
            "current_shares": ss.current_shares,
            "current_cost": ss.current_cost,
            "cash_remaining": ss.cash_remaining,
            "realized_pnl": ss.realized_pnl,
            "unrealized_pnl": ss.unrealized_pnl,
            "daily_realized_pnl": ss.daily_realized_pnl,
            "last_price": ss.last_price,
        }
        # Overlay live in-memory state if running
        if live_state and ss.symbol in live_state.get("symbols", {}):
            sym_data.update(live_state["symbols"][ss.symbol])
        symbols_data.append(sym_data)

    return {
        "session": {
            "id": s.id,
            "name": s.name,
            "status": s.status if not live_engine.is_session_running(s.id) else "running",
            "order_type": s.order_type,
            "position_size": s.position_size,
            "max_entries": s.max_entries,
            "max_daily_loss": s.max_daily_loss,
            "error_message": s.error_message,
            "created_at": _iso_utc(s.created_at),
            "started_at": _iso_utc(s.started_at),
            "stopped_at": _iso_utc(s.stopped_at),
        },
        "symbols": symbols_data,
        "is_running": live_engine.is_session_running(s.id),
        "total_pnl": live_state["total_pnl"] if live_state else None,
        "total_value": live_state["total_value"] if live_state else None,
    }


@router.post("/sessions/{session_id}/start")
async def start_session(session_id: str) -> dict:
    """Start a live trading session."""
    try:
        await live_engine.start_session(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {"status": "running", "session_id": session_id}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str) -> dict:
    """Gracefully stop a live session."""
    if not live_engine.is_session_running(session_id):
        raise HTTPException(status_code=409, detail="Session is not running")
    await live_engine.stop_session(session_id)
    return {"status": "stopped", "session_id": session_id}


@router.post("/sessions/{session_id}/kill")
async def kill_session(session_id: str) -> dict:
    """Emergency kill switch — cancel all orders and stop immediately."""
    if not live_engine.is_session_running(session_id):
        raise HTTPException(status_code=409, detail="Session is not running")
    await live_engine.kill_session(session_id)
    return {"status": "killed", "session_id": session_id}


@router.get("/sessions/{session_id}/trades")
async def get_trades(session_id: str, symbol: str | None = None) -> dict:
    """Get trade log for a session."""
    async with get_db_context() as db:
        s = await repo.get_session(db, session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Session not found")
        trades = await repo.get_trades(db, session_id, symbol=symbol)

    return {
        "trades": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side,
                "order_type": t.order_type,
                "shares": t.shares,
                "price": t.price,
                "cost": t.cost,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "ibkr_order_id": t.ibkr_order_id,
                "status": t.status,
                "created_at": _iso_utc(t.created_at),
            }
            for t in trades
        ]
    }


@router.post("/sessions/{session_id}/clone")
async def clone_session(session_id: str) -> dict:
    """Clone a session's configuration into a new session (fresh state)."""
    async with get_db_context() as db:
        source = await repo.get_session(db, session_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Session not found")
        source_symbols = await repo.get_session_symbols(db, session_id)

        new_session = await repo.create_session(
            db,
            name=f"{source.name} (copy)",
            order_type=source.order_type,
            position_size=source.position_size,
            max_entries=source.max_entries,
            max_daily_loss=source.max_daily_loss,
        )

        symbols_out = []
        for ss in source_symbols:
            new_sym = await repo.add_session_symbol(
                db,
                session_id=new_session.id,
                symbol=ss.symbol,
                algorithm_id=ss.algorithm_id,
                allocated_capital=ss.allocated_capital,
                position_size=ss.position_size,
                max_entries=ss.max_entries,
            )
            symbols_out.append({
                "id": new_sym.id,
                "symbol": new_sym.symbol,
                "algorithm_id": new_sym.algorithm_id,
                "allocated_capital": new_sym.allocated_capital,
            })

    return {
        "session": {
            "id": new_session.id,
            "name": new_session.name,
            "status": new_session.status,
            "order_type": new_session.order_type,
            "position_size": new_session.position_size,
            "max_entries": new_session.max_entries,
            "max_daily_loss": new_session.max_daily_loss,
            "created_at": _iso_utc(new_session.created_at),
        },
        "symbols": symbols_out,
    }


# ── WebSocket — real-time session stream ─────────────────────────────


@router.get("/sessions/{session_id}/candles/{symbol}")
async def get_session_candles(session_id: str, symbol: str) -> dict:
    """Return aggregated 1m candles for a running session's symbol."""
    state = live_engine._sessions.get(session_id)  # noqa: SLF001
    if state is None:
        raise HTTPException(status_code=404, detail="Session not running")
    rt = state["symbols"].get(symbol.upper())
    if rt is None:
        raise HTTPException(status_code=404, detail="Symbol not in session")

    from app.models.market_data import Timeframe
    completed = rt.aggregator.completed_candles(Timeframe.ONE_MINUTE)
    current = rt.aggregator.current_candle(Timeframe.ONE_MINUTE)

    candles_out = [
        {
            "time": c.time.isoformat() if hasattr(c.time, "isoformat") else str(c.time),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in completed
    ]
    if current:
        candles_out.append({
            "time": current.time.isoformat() if hasattr(current.time, "isoformat") else str(current.time),
            "open": current.open,
            "high": current.high,
            "low": current.low,
            "close": current.close,
            "volume": current.volume,
        })

    return {"symbol": symbol.upper(), "candles": candles_out}


@router.websocket("/ws/{session_id}")
async def live_ws(websocket: WebSocket, session_id: str) -> None:
    """Stream real-time events for a live session."""
    await websocket.accept()

    if not live_engine.is_session_running(session_id):
        await websocket.send_json({"type": "error", "message": "Session is not running"})
        await websocket.close()
        return

    queue = live_engine.subscribe_ws(session_id)

    try:
        # Send current state snapshot
        state = live_engine.get_session_state(session_id)
        if state:
            await websocket.send_json({"type": "snapshot", **state})

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=5.0)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                # Send heartbeat / check connection
                if not live_engine.is_session_running(session_id):
                    await websocket.send_json({"type": "status", "status": "stopped"})
                    break
                await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
    finally:
        live_engine.unsubscribe_ws(session_id, queue)
