from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.candles import CandleRepository
from app.db.models import HistoricalCandle
from app.models.market_data import Candle, Timeframe
from app.services.candles import CandleService


def build_candles(
    symbol: str,
    timeframe: Timeframe,
    end_time: datetime,
    count: int,
) -> list[Candle]:
    step = {
        Timeframe.ONE_MINUTE: timedelta(minutes=1),
        Timeframe.FIVE_MINUTES: timedelta(minutes=5),
        Timeframe.FIFTEEN_MINUTES: timedelta(minutes=15),
        Timeframe.ONE_HOUR: timedelta(hours=1),
    }[timeframe]
    start_time = end_time - step * (count - 1)
    candles: list[Candle] = []
    for index in range(count):
        candle_time = start_time + step * index
        price = 100.0 + index
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                time=candle_time,
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 0.5,
                volume=1000 + index,
            )
        )
    return candles


class StubProvider:
    def __init__(self, history: list[Candle], trailing: list[Candle] | None = None) -> None:
        self.history = history
        self.trailing = trailing or []
        self.get_history_calls: list[tuple[str, Timeframe, int]] = []
        self.get_history_since_calls: list[tuple[str, Timeframe, datetime | None, int]] = []

    def get_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        self.get_history_calls.append((symbol, timeframe, limit))
        return self.history[-limit:]

    def get_history_since(
        self,
        symbol: str,
        timeframe: Timeframe,
        start_time: datetime | None,
        limit: int,
    ) -> list[Candle]:
        self.get_history_since_calls.append((symbol, timeframe, start_time, limit))
        if start_time is None:
            return self.history[-limit:]
        return [candle for candle in self.trailing if candle.time > start_time][-limit:]

    def get_live_price(self, symbol: str) -> float | None:
        return None


@pytest.mark.asyncio
async def test_repository_upsert_reuses_existing_rows(test_db_session) -> None:
    repository = CandleRepository()
    end_time = datetime(2026, 5, 11, 15, 0, tzinfo=UTC)
    candles = build_candles("AAPL", Timeframe.FIVE_MINUTES, end_time, 3)

    await repository.upsert_candles(test_db_session, candles)
    updated = candles[-1].model_copy(update={"close": 999.0})
    await repository.upsert_candles(test_db_session, [updated])

    rows = await test_db_session.execute(select(HistoricalCandle))
    stored = rows.scalars().all()

    assert len(stored) == 3
    assert max(row.close for row in stored) == 999.0


@pytest.mark.asyncio
async def test_candle_service_persists_and_reuses_history(test_db_session, monkeypatch) -> None:
    now = datetime(2026, 5, 11, 15, 1, tzinfo=UTC)
    provider = StubProvider(build_candles("AAPL", Timeframe.FIVE_MINUTES, now - timedelta(minutes=1), 6))
    service = CandleService(provider)

    @asynccontextmanager
    async def session_context():
        yield test_db_session

    monkeypatch.setattr("app.services.candles.get_db_context", session_context)
    monkeypatch.setattr("app.services.candles.utc_now", lambda: now)

    first = await service._get_history_async("AAPL", Timeframe.FIVE_MINUTES, limit=6)
    second = await service._get_history_async("AAPL", Timeframe.FIVE_MINUTES, limit=6)

    assert len(first) == 6
    assert len(second) == 6
    assert provider.get_history_calls == [("AAPL", Timeframe.FIVE_MINUTES, 120)]
    assert provider.get_history_since_calls == []


@pytest.mark.asyncio
async def test_candle_service_fetches_only_missing_trailing_bars(test_db_session, monkeypatch) -> None:
    now = datetime(2026, 5, 11, 15, 15, tzinfo=UTC)
    existing = build_candles("AAPL", Timeframe.FIVE_MINUTES, now - timedelta(minutes=15), 6)
    trailing = build_candles("AAPL", Timeframe.FIVE_MINUTES, now, 3)
    provider = StubProvider(existing, trailing=trailing)
    service = CandleService(provider)
    repository = CandleRepository()

    @asynccontextmanager
    async def session_context():
        yield test_db_session

    monkeypatch.setattr("app.services.candles.get_db_context", session_context)
    monkeypatch.setattr("app.services.candles.utc_now", lambda: now)

    await repository.upsert_candles(test_db_session, existing)
    candles = await service._get_history_async("AAPL", Timeframe.FIVE_MINUTES, limit=6)

    assert len(candles) == 6
    assert [candle.time for candle in candles][-3:] == [candle.time for candle in trailing]
    assert provider.get_history_calls == []
    assert len(provider.get_history_since_calls) == 1
    _, _, start_time, trailing_limit = provider.get_history_since_calls[0]
    assert start_time == existing[-1].time
    assert trailing_limit == 4

    rows = await test_db_session.execute(select(HistoricalCandle).order_by(HistoricalCandle.time))
    stored = rows.scalars().all()
    assert len(stored) == 9