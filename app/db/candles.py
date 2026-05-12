from __future__ import annotations

from datetime import UTC

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import HistoricalCandle
from app.models.market_data import Candle, Timeframe


class CandleRepository:
    async def get_recent_candles(
        self,
        session: AsyncSession,
        symbol: str,
        timeframe: Timeframe,
        limit: int,
    ) -> list[Candle]:
        stmt = (
            select(HistoricalCandle)
            .where(HistoricalCandle.symbol == symbol.upper(), HistoricalCandle.timeframe == timeframe)
            .order_by(HistoricalCandle.time.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        records = list(result.scalars().all())
        records.reverse()
        return [self._to_domain(record) for record in records]

    async def upsert_candles(self, session: AsyncSession, candles: list[Candle]) -> None:
        if not candles:
            return

        normalized = [self._normalize(candle) for candle in candles]
        symbol = normalized[0].symbol
        timeframe = normalized[0].timeframe
        times = [candle.time for candle in normalized]
        stmt = select(HistoricalCandle).where(
            HistoricalCandle.symbol == symbol,
            HistoricalCandle.timeframe == timeframe,
            HistoricalCandle.time.in_(times),
        )
        result = await session.execute(stmt)
        existing_by_time = {self._normalize_time(record.time): record for record in result.scalars().all()}

        for candle in normalized:
            record = existing_by_time.get(candle.time)
            if record is None:
                session.add(
                    HistoricalCandle(
                        symbol=candle.symbol,
                        timeframe=candle.timeframe,
                        time=candle.time,
                        open=candle.open,
                        high=candle.high,
                        low=candle.low,
                        close=candle.close,
                        volume=candle.volume,
                    )
                )
                continue

            record.open = candle.open
            record.high = candle.high
            record.low = candle.low
            record.close = candle.close
            record.volume = candle.volume

        await session.commit()

    def _normalize(self, candle: Candle) -> Candle:
        normalized_time = self._normalize_time(candle.time)
        return candle.model_copy(update={"symbol": candle.symbol.upper(), "time": normalized_time})

    def _to_domain(self, record: HistoricalCandle) -> Candle:
        candle_time = self._normalize_time(record.time)
        return Candle(
            symbol=record.symbol,
            timeframe=record.timeframe,
            time=candle_time,
            open=record.open,
            high=record.high,
            low=record.low,
            close=record.close,
            volume=record.volume,
        )

    def _normalize_time(self, value):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value