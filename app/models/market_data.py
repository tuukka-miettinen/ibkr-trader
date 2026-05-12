from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field


class Timeframe(StrEnum):
    FIVE_SECONDS = "5s"
    ONE_MINUTE = "1m"
    THREE_MINUTES = "3m"
    FIVE_MINUTES = "5m"
    FIFTEEN_MINUTES = "15m"
    ONE_HOUR = "1h"


class Candle(BaseModel):
    symbol: str
    timeframe: Timeframe
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = Field(ge=0)


class TimelineEventType(StrEnum):
    EARNINGS = "earnings"
    DIVIDEND = "dividend"
    SPLIT = "split"


class TimelineEvent(BaseModel):
    id: str
    symbol: str
    event_type: TimelineEventType
    time: datetime
    title: str
    summary: str
    details: dict[str, float | str | int | None] = Field(default_factory=dict)


class CandleSnapshot(BaseModel):
    symbol: str
    timeframe: Timeframe
    candles: list[Candle]
    events: list[TimelineEvent]


class StatusMessage(BaseModel):
    status: str
    message: str
    symbol: str | None = None
    timeframe: Timeframe | None = None


class SubscriptionRequest(BaseModel):
    type: str
    symbol: str
    timeframe: Timeframe


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)
