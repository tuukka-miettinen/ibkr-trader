from fastapi import APIRouter, HTTPException, Query

from app.models.market_data import CandleSnapshot, Timeframe
from app.providers.base import MarketDataError
from app.services.candles import candle_service
from app.services.events import event_service

router = APIRouter(prefix="/api/candles", tags=["candles"])


@router.get("", response_model=CandleSnapshot)
async def get_candles(
    symbol: str = Query(default="NBIS", min_length=1, max_length=10),
    timeframe: Timeframe = Query(default=Timeframe.ONE_MINUTE),
    limit: int = Query(default=120, ge=20, le=300),
) -> CandleSnapshot:
    normalized_symbol = symbol.upper()
    try:
        candles = await candle_service.get_history_async(normalized_symbol, timeframe, limit)
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    events = event_service.get_events(normalized_symbol)
    return CandleSnapshot(symbol=normalized_symbol, timeframe=timeframe, candles=candles, events=events)
