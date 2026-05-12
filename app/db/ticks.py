"""Repository for 5-second tick data stored as hourly JSON chunks."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TickChunk
from app.models.market_data import Candle, Timeframe


class TickRepository:
    async def get_chunks(
        self,
        session: AsyncSession,
        symbol: str,
        start_hour: datetime,
        end_hour: datetime,
    ) -> list[TickChunk]:
        stmt = (
            select(TickChunk)
            .where(
                TickChunk.symbol == symbol.upper(),
                TickChunk.hour_start >= start_hour,
                TickChunk.hour_start <= end_hour,
            )
            .order_by(TickChunk.hour_start)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_available_hours(
        self,
        session: AsyncSession,
        symbol: str,
    ) -> list[datetime]:
        stmt = (
            select(TickChunk.hour_start)
            .where(TickChunk.symbol == symbol.upper())
            .order_by(TickChunk.hour_start)
        )
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    async def get_available_range(
        self,
        session: AsyncSession,
        symbol: str,
    ) -> tuple[datetime, datetime] | None:
        stmt = select(
            func.min(TickChunk.hour_start),
            func.max(TickChunk.hour_start),
        ).where(TickChunk.symbol == symbol.upper())
        result = await session.execute(stmt)
        row = result.one_or_none()
        if row is None or row[0] is None:
            return None
        return (row[0], row[1])

    async def upsert_chunk(
        self,
        session: AsyncSession,
        symbol: str,
        hour_start: datetime,
        bars: list[dict],
    ) -> None:
        normalized_symbol = symbol.upper()
        stmt = select(TickChunk).where(
            TickChunk.symbol == normalized_symbol,
            TickChunk.hour_start == hour_start,
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing is not None:
            existing.data_json = bars
            existing.bar_count = len(bars)
        else:
            session.add(TickChunk(
                symbol=normalized_symbol,
                hour_start=hour_start,
                data_json=bars,
                bar_count=len(bars),
            ))
        await session.commit()

    def chunks_to_candles(self, chunks: list[TickChunk]) -> list[Candle]:
        candles: list[Candle] = []
        for chunk in chunks:
            for bar in chunk.data_json:
                t = bar["t"]
                if isinstance(t, str):
                    dt = datetime.fromisoformat(t)
                else:
                    dt = datetime.fromtimestamp(t, tz=UTC)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                candles.append(Candle(
                    symbol=chunk.symbol,
                    timeframe=Timeframe.FIVE_SECONDS,
                    time=dt,
                    open=bar["o"],
                    high=bar["h"],
                    low=bar["l"],
                    close=bar["c"],
                    volume=bar.get("v", 0),
                ))
        return candles
