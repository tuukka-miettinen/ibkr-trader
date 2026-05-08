from __future__ import annotations

import asyncio
from contextlib import suppress

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.models.market_data import SubscriptionRequest
from app.providers.base import MarketDataError
from app.services.candles import candle_service
from app.services.events import event_service

router = APIRouter(tags=["ws"])


@router.websocket("/ws/market")
async def market_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    symbol = "AAPL"
    timeframe = None
    loop = asyncio.get_event_loop()

    try:
        while True:
            try:
                payload = await asyncio.wait_for(websocket.receive_json(), timeout=1.5)
                request = SubscriptionRequest.model_validate(payload)
                if request.type not in {"subscribe", "change_symbol"}:
                    await websocket.send_json(
                        {"type": "error", "message": f"Unsupported message type: {request.type}"}
                    )
                    continue

                symbol = request.symbol.upper()
                timeframe = request.timeframe
                try:
                    candles = await loop.run_in_executor(
                        None, candle_service.get_history, symbol, timeframe
                    )
                except MarketDataError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                events = event_service.get_events(symbol)
                await websocket.send_json(
                    {
                        "type": "snapshot",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "candles": [candle.model_dump(mode="json") for candle in candles],
                        "events": [event.model_dump(mode="json") for event in events],
                    }
                )
                await websocket.send_json(
                    {
                        "type": "status",
                        "status": "subscribed",
                        "message": f"Streaming {symbol} {timeframe}",
                        "symbol": symbol,
                        "timeframe": timeframe,
                    }
                )
            except TimeoutError:
                if timeframe is None:
                    continue

                try:
                    candle = await loop.run_in_executor(
                        None, candle_service.next_candle, symbol, timeframe
                    )
                except MarketDataError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
                    continue
                await websocket.send_json(
                    {
                        "type": "candle_update",
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "candle": candle.model_dump(mode="json"),
                    }
                )
    except WebSocketDisconnect:
        return
    finally:
        with suppress(RuntimeError):
            await websocket.close()
