from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.market_data import Candle, Timeframe


class MarketDataError(RuntimeError):
    pass


class MarketDataProvider(ABC):
    @abstractmethod
    def get_history(self, symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        raise NotImplementedError

    @abstractmethod
    def get_live_price(self, symbol: str) -> float | None:
        raise NotImplementedError
