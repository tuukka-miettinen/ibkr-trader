"""Repository for live paper-trading sessions, trades, and positions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    LivePositionEntry,
    LiveSession,
    LiveSessionSymbol,
    LiveTrade,
)


def _uuid() -> str:
    return uuid.uuid4().hex


class LiveRepository:
    # ── Session CRUD ──────────────────────────────────────────────────

    async def create_session(
        self,
        session: AsyncSession,
        *,
        name: str,
        order_type: str = "market",
        position_size: float = 1000.0,
        max_entries: int = 5,
        max_daily_loss: float = 500.0,
    ) -> LiveSession:
        live = LiveSession(
            id=_uuid(),
            name=name,
            status="created",
            order_type=order_type,
            position_size=position_size,
            max_entries=max_entries,
            max_daily_loss=max_daily_loss,
        )
        session.add(live)
        await session.commit()
        await session.refresh(live)
        return live

    async def get_session(self, session: AsyncSession, session_id: str) -> LiveSession | None:
        stmt = select(LiveSession).where(LiveSession.id == session_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_sessions(self, session: AsyncSession) -> list[LiveSession]:
        stmt = select(LiveSession).order_by(LiveSession.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def update_session_status(
        self,
        session: AsyncSession,
        session_id: str,
        status: str,
        *,
        error_message: str | None = None,
    ) -> None:
        values: dict = {"status": status}
        now = datetime.now(tz=timezone.utc)
        if status == "running":
            values["started_at"] = now
        elif status in ("stopped", "error"):
            values["stopped_at"] = now
        if error_message is not None:
            values["error_message"] = error_message
        stmt = update(LiveSession).where(LiveSession.id == session_id).values(**values)
        await session.execute(stmt)
        await session.commit()

    async def save_session_strategy_state(
        self,
        session: AsyncSession,
        session_id: str,
        state_json: dict | None,
    ) -> None:
        stmt = (
            update(LiveSession)
            .where(LiveSession.id == session_id)
            .values(strategy_state_json=state_json)
        )
        await session.execute(stmt)
        await session.commit()

    async def list_running_sessions(self, session: AsyncSession) -> list[LiveSession]:
        stmt = select(LiveSession).where(LiveSession.status == "running")
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ── Session symbols ───────────────────────────────────────────────

    async def add_session_symbol(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        symbol: str,
        algorithm_id: str,
        allocated_capital: float,
        position_size: float,
        max_entries: int,
    ) -> LiveSessionSymbol:
        sym = LiveSessionSymbol(
            id=_uuid(),
            session_id=session_id,
            symbol=symbol.upper(),
            algorithm_id=algorithm_id,
            allocated_capital=allocated_capital,
            position_size=position_size,
            max_entries=max_entries,
            cash_remaining=allocated_capital,
        )
        session.add(sym)
        await session.commit()
        await session.refresh(sym)
        return sym

    async def get_session_symbols(
        self, session: AsyncSession, session_id: str
    ) -> list[LiveSessionSymbol]:
        stmt = select(LiveSessionSymbol).where(LiveSessionSymbol.session_id == session_id)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def update_symbol_state(
        self,
        session: AsyncSession,
        symbol_id: str,
        *,
        current_shares: float | None = None,
        current_cost: float | None = None,
        cash_remaining: float | None = None,
        realized_pnl: float | None = None,
        unrealized_pnl: float | None = None,
        daily_realized_pnl: float | None = None,
        last_price: float | None = None,
        strategy_state_json: dict | None = ...,
    ) -> None:
        values: dict = {}
        if current_shares is not None:
            values["current_shares"] = current_shares
        if current_cost is not None:
            values["current_cost"] = current_cost
        if cash_remaining is not None:
            values["cash_remaining"] = cash_remaining
        if realized_pnl is not None:
            values["realized_pnl"] = realized_pnl
        if unrealized_pnl is not None:
            values["unrealized_pnl"] = unrealized_pnl
        if daily_realized_pnl is not None:
            values["daily_realized_pnl"] = daily_realized_pnl
        if last_price is not None:
            values["last_price"] = last_price
        if strategy_state_json is not ...:
            values["strategy_state_json"] = strategy_state_json
        if not values:
            return
        stmt = update(LiveSessionSymbol).where(LiveSessionSymbol.id == symbol_id).values(**values)
        await session.execute(stmt)
        await session.commit()

    async def reset_daily_pnl(self, session: AsyncSession, session_id: str) -> None:
        stmt = (
            update(LiveSessionSymbol)
            .where(LiveSessionSymbol.session_id == session_id)
            .values(daily_realized_pnl=0.0)
        )
        await session.execute(stmt)
        await session.commit()

    # ── Trades ────────────────────────────────────────────────────────

    async def record_trade(
        self,
        session: AsyncSession,
        *,
        session_id: str,
        symbol: str,
        side: str,
        order_type: str,
        shares: float,
        price: float,
        cost: float,
        pnl: float | None = None,
        pnl_pct: float | None = None,
        ibkr_order_id: int | None = None,
        status: str = "filled",
    ) -> LiveTrade:
        trade = LiveTrade(
            id=_uuid(),
            session_id=session_id,
            symbol=symbol.upper(),
            side=side,
            order_type=order_type,
            shares=shares,
            price=price,
            cost=cost,
            pnl=pnl,
            pnl_pct=pnl_pct,
            ibkr_order_id=ibkr_order_id,
            status=status,
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        return trade

    async def get_trades(
        self, session: AsyncSession, session_id: str, symbol: str | None = None
    ) -> list[LiveTrade]:
        stmt = select(LiveTrade).where(LiveTrade.session_id == session_id)
        if symbol:
            stmt = stmt.where(LiveTrade.symbol == symbol.upper())
        stmt = stmt.order_by(LiveTrade.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ── Position entries ──────────────────────────────────────────────

    async def add_position_entry(
        self,
        session: AsyncSession,
        *,
        session_symbol_id: str,
        time: datetime,
        price: float,
        shares: float,
        cost: float,
        ibkr_order_id: int | None = None,
    ) -> LivePositionEntry:
        entry = LivePositionEntry(
            id=_uuid(),
            session_symbol_id=session_symbol_id,
            time=time,
            price=price,
            shares=shares,
            cost=cost,
            ibkr_order_id=ibkr_order_id,
        )
        session.add(entry)
        await session.commit()
        await session.refresh(entry)
        return entry

    async def get_position_entries(
        self, session: AsyncSession, session_symbol_id: str
    ) -> list[LivePositionEntry]:
        stmt = (
            select(LivePositionEntry)
            .where(LivePositionEntry.session_symbol_id == session_symbol_id)
            .order_by(LivePositionEntry.time)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def clear_position_entries(
        self, session: AsyncSession, session_symbol_id: str
    ) -> None:
        stmt = delete(LivePositionEntry).where(
            LivePositionEntry.session_symbol_id == session_symbol_id
        )
        await session.execute(stmt)
        await session.commit()
