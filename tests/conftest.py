"""Test configuration and fixtures."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base
from app.models.market_data import Candle, Timeframe


# Database fixtures for async tests
@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for pytest-asyncio."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def test_db_engine():
    """Create a test database engine with in-memory SQLite."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
    )
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup
    await engine.dispose()


@pytest.fixture
async def test_db_session(test_db_engine):
    """Create a test database session."""
    async_session = sessionmaker(
        test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with async_session() as session:
        yield session
    
    # Clear data after test
    async with test_db_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())
        await conn.commit()


@pytest.fixture
def mock_candle_data() -> list[Candle]:
    """Generate synthetic candle data for testing."""
    candles = []
    base_time = datetime(2024, 1, 1, 9, 30)
    open_price = 100.0
    symbol = "AAPL"
    timeframe = Timeframe.FIVE_MINUTES
    
    for i in range(288):  # 1 day of 5-minute bars
        time = base_time + timedelta(minutes=5*i)
        # Simulate slight uptrend with some noise
        close_price = open_price + (i * 0.05) + (i % 3) * 0.02
        high_price = close_price + 0.5
        low_price = min(open_price, close_price) - 0.3
        volume = 1000000 + (i * 100) % 500000
        
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                time=time,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
            )
        )
        open_price = close_price
    
    return candles


@pytest.fixture
def mock_candle_service(mock_candle_data: list[Candle]) -> MagicMock:
    """Mock the candle service to return synthetic data instead of connecting to IBKR."""
    mock = MagicMock()
    
    def get_history_impl(symbol: str, timeframe: Timeframe, limit: int) -> list[Candle]:
        # Return a slice of the synthetic data
        return mock_candle_data[-limit:] if limit <= len(mock_candle_data) else mock_candle_data
    
    mock.get_history = MagicMock(side_effect=get_history_impl)
    return mock


@pytest.fixture(autouse=True)
def patch_candle_service(mock_candle_service: MagicMock) -> None:
    """Auto-patch the candle service in all tests."""
    with patch("app.services.backtest.candle_service", mock_candle_service):
        yield
