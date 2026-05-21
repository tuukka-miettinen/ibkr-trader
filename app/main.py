import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.backtest import router as backtest_router
from app.api.candles import router as candles_router
from app.api.events import router as events_router
from app.api.live import router as live_router
from app.api.optimize import router as optimize_router
from app.api.tick_backtest import router as tick_backtest_router
from app.api.ws import router as ws_router

logger = logging.getLogger(__name__)

# Comma-separated list of allowed origins. In Docker, Nginx proxies all traffic
# so no cross-origin requests are made. Override via CORS_ORIGINS env var.
_raw_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: recover any live sessions that were running before restart
    from app.services.live_engine import live_engine
    try:
        await live_engine.recover_sessions()
    except Exception:
        logger.exception("Error recovering live sessions on startup")
    try:
        yield
    finally:
        try:
            await live_engine.shutdown()
        except Exception:
            logger.exception("Error shutting down live engine")


app = FastAPI(title="Trader API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(backtest_router)
app.include_router(candles_router)
app.include_router(events_router)
app.include_router(live_router)
app.include_router(optimize_router)
app.include_router(tick_backtest_router)
app.include_router(ws_router)
