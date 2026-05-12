import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# Read database URL from environment; fallback to localhost for development
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:trading123@localhost:5432/trader")

# Create async engine
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set to True for SQL debugging
    pool_pre_ping=True,  # Verify connections are alive before using them
    pool_size=10,
    max_overflow=20,
)

# Session factory
async_session_factory = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    """Dependency injection function for FastAPI routes."""
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def get_db_context():
    """Context manager for use outside of FastAPI dependency injection."""
    async with async_session_factory() as session:
        yield session
