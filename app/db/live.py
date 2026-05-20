"""Repository for live paper-trading sessions, trades, and positions."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    LivePositionEntry,
    LiveSession,
    LiveSessionSeedCandle,
    LiveSessionSymbol,
    LiveSessionTick,
    LiveTrade,
)
from app.models.market_data import Candle, Timeframe


def _uuid() -> str:
    return uuid.uuid4().hex


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


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
        max_total_exposure: float = 50000.0,
    ) -> LiveSession:
        live = LiveSession(
            id=_uuid(),
            name=name,
            status="created",
            order_type=order_type,
            position_size=position_size,
            max_entries=max_entries,
            max_daily_loss=max_daily_loss,
            max_total_exposure=max_total_exposure,
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

    async def rename_session(
        self,
        session: AsyncSession,
        session_id: str,
        name: str,
    ) -> LiveSession | None:
        cleaned_name = name.strip()
        if not cleaned_name:
            raise ValueError("Session name cannot be empty")
        stmt = (
            update(LiveSession)
            .where(LiveSession.id == session_id)
            .values(name=cleaned_name)
        )
        await session.execute(stmt)
        await session.commit()
        return await self.get_session(session, session_id)

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
        max_daily_entries: int = 10,
    ) -> LiveSessionSymbol:
        sym = LiveSessionSymbol(
            id=_uuid(),
            session_id=session_id,
            symbol=symbol.upper(),
            algorithm_id=algorithm_id,
            allocated_capital=allocated_capital,
            position_size=position_size,
            max_entries=max_entries,
            max_daily_entries=max_daily_entries,
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

    async def get_session_symbol(
        self,
        session: AsyncSession,
        session_id: str,
        symbol: str,
    ) -> LiveSessionSymbol | None:
        stmt = select(LiveSessionSymbol).where(
            LiveSessionSymbol.session_id == session_id,
            LiveSessionSymbol.symbol == symbol.upper(),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

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
        daily_entry_count: int | None = None,
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
        if daily_entry_count is not None:
            values["daily_entry_count"] = daily_entry_count
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
            .values(daily_realized_pnl=0.0, daily_entry_count=0)
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
        event_time: datetime | None = None,
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
            event_time=event_time,
            ibkr_order_id=ibkr_order_id,
            status=status,
        )
        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        return trade

    async def get_trades(
        self,
        session: AsyncSession,
        session_id: str,
        symbol: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        ascending: bool = False,
    ) -> list[LiveTrade]:
        stmt = select(LiveTrade).where(LiveTrade.session_id == session_id)
        if symbol:
            stmt = stmt.where(LiveTrade.symbol == symbol.upper())
        if start_time is not None:
            stmt = stmt.where(LiveTrade.event_time.is_not(None), LiveTrade.event_time >= start_time)
        if end_time is not None:
            stmt = stmt.where(LiveTrade.event_time.is_not(None), LiveTrade.event_time <= end_time)
        if ascending:
            stmt = stmt.order_by(LiveTrade.event_time.asc(), LiveTrade.created_at.asc())
        else:
            stmt = stmt.order_by(LiveTrade.event_time.desc(), LiveTrade.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def replace_seed_candles(
        self,
        session: AsyncSession,
        *,
        session_symbol_id: str,
        timeframe: Timeframe,
        candles: list[Candle],
    ) -> None:
        await session.execute(
            delete(LiveSessionSeedCandle).where(
                LiveSessionSeedCandle.session_symbol_id == session_symbol_id,
                LiveSessionSeedCandle.timeframe == timeframe,
            )
        )
        session.add_all([
            LiveSessionSeedCandle(
                id=_uuid(),
                session_symbol_id=session_symbol_id,
                timeframe=timeframe,
                time=candle.time,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                volume=candle.volume,
            )
            for candle in candles
        ])
        await session.commit()

    async def get_seed_candles(
        self,
        session: AsyncSession,
        session_symbol_id: str,
    ) -> dict[Timeframe, list[Candle]]:
        stmt = (
            select(LiveSessionSeedCandle)
            .where(LiveSessionSeedCandle.session_symbol_id == session_symbol_id)
            .order_by(LiveSessionSeedCandle.timeframe, LiveSessionSeedCandle.time)
        )
        result = await session.execute(stmt)
        candles_by_tf: dict[Timeframe, list[Candle]] = {}
        for row in result.scalars().all():
            candles_by_tf.setdefault(row.timeframe, []).append(Candle(
                symbol="",
                timeframe=row.timeframe,
                time=_ensure_utc(row.time),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            ))
        return candles_by_tf

    async def record_ticks(
        self,
        session: AsyncSession,
        *,
        session_symbol_id: str,
        ticks: list[Candle],
    ) -> None:
        if not ticks:
            return
        session.add_all([
            LiveSessionTick(
                id=_uuid(),
                session_symbol_id=session_symbol_id,
                time=tick.time,
                open=tick.open,
                high=tick.high,
                low=tick.low,
                close=tick.close,
                volume=tick.volume,
            )
            for tick in ticks
        ])
        await session.commit()

    async def get_ticks(
        self,
        session: AsyncSession,
        session_symbol_id: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        stmt = (
            select(LiveSessionTick)
            .where(LiveSessionTick.session_symbol_id == session_symbol_id)
            .order_by(LiveSessionTick.time)
        )
        if start_time is not None:
            stmt = stmt.where(LiveSessionTick.time >= start_time)
        if end_time is not None:
            stmt = stmt.where(LiveSessionTick.time <= end_time)
        result = await session.execute(stmt)
        return [
            Candle(
                symbol="",
                timeframe=Timeframe.FIVE_SECONDS,
                time=_ensure_utc(row.time),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in result.scalars().all()
        ]

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
