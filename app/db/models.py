from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

from app.models.market_data import Timeframe

Base = declarative_base()


class OptimizationJob(Base):
    """Stores optimization job metadata and results."""

    __tablename__ = "optimization_job"

    # Primary key: internal database ID
    id = Column(String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)

    # Job ID (UUID string) - what's returned to frontend
    job_id = Column(String(36), unique=True, nullable=False, index=True)

    # Status: queued, running, completed, failed
    status = Column(Enum("queued", "running", "completed", "failed", name="job_status"), nullable=False, index=True)

    # Provider: "fake" or "openai"
    provider = Column(String(50), nullable=False)

    # Serialized OptimizationRequest (plan)
    plan_json = Column(JSON, nullable=False)

    # Leaderboard: list of {candidate_name, parameters, score_details, ...}
    leaderboard_json = Column(JSON, nullable=True)

    # Best candidate so far: {parameters, rendered_script, score_details}
    best_candidate_json = Column(JSON, nullable=True)

    # Error message (if status=failed)
    error_message = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    # Indexes
    __table_args__ = (
        Index("ix_job_id_status", "job_id", "status"),
        Index("ix_status_created_at", "status", "created_at"),
    )


class JobLeaderboardEntry(Base):
    """Stores individual leaderboard entries for an optimization job (for efficient querying)."""

    __tablename__ = "job_leaderboard_entry"

    # Primary key
    id = Column(String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)

    # Foreign key to OptimizationJob
    job_id = Column(String(36), ForeignKey("optimization_job.job_id"), nullable=False, index=True)

    # Candidate name
    candidate_name = Column(String(255), nullable=False)

    # Parameters dict
    parameters_json = Column(JSON, nullable=False)

    # Score details: {overall_score, pnl_score, win_rate_score, trade_count_score, consistency_bonus}
    score_details_json = Column(JSON, nullable=False)

    # When this entry was proposed (iteration order)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Indexes
    __table_args__ = (Index("ix_job_id_created_at", "job_id", "created_at"),)


class HistoricalCandle(Base):
    """Stores persisted historical candles fetched from the market-data provider."""

    __tablename__ = "historical_candle"

    id = Column(String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
    symbol = Column(String(16), nullable=False)
    timeframe = Column(
        Enum(Timeframe, values_callable=lambda enum_cls: [member.value for member in enum_cls], native_enum=False),
        nullable=False,
    )
    time = Column(DateTime(timezone=True), nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "time", name="uq_historical_candle_key"),
        Index("ix_historical_candle_lookup", "symbol", "timeframe", "time"),
        Index("ix_historical_candle_time", "time"),
    )


class TickChunk(Base):
    """Stores 5-second bars for one symbol-hour as a single JSON blob."""

    __tablename__ = "tick_chunk"

    id = Column(String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
    symbol = Column(String(16), nullable=False)
    hour_start = Column(DateTime(timezone=True), nullable=False)
    data_json = Column(JSON, nullable=False)
    bar_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "hour_start", name="uq_tick_chunk_key"),
        Index("ix_tick_chunk_lookup", "symbol", "hour_start"),
    )


class StrategyAlgorithm(Base):
    """Stores versioned trading algorithms."""

    __tablename__ = "strategy_algorithm"

    id = Column(String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
    name = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    script = Column(Text, nullable=False)
    description = Column(Text, nullable=True)
    is_favorite = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_strategy_name_version"),
        Index("ix_strategy_name", "name"),
    )


class BacktestRun(Base):
    """Stores individual backtest run results for comparison."""

    __tablename__ = "backtest_run"

    id = Column(String(36), primary_key=True, default=lambda: __import__("uuid").uuid4().hex)
    algorithm_id = Column(String(36), ForeignKey("strategy_algorithm.id"), nullable=False, index=True)
    symbol = Column(String(16), nullable=False)
    mode = Column(String(10), nullable=False, default="tick")
    lookback_days = Column(Integer, nullable=True)
    config_json = Column(JSON, nullable=False)
    result_json = Column(JSON, nullable=False)
    num_trades = Column(Integer, nullable=False, default=0)
    total_pnl = Column(Float, nullable=False, default=0.0)
    total_pnl_pct = Column(Float, nullable=False, default=0.0)
    win_rate = Column(Float, nullable=False, default=0.0)
    final_balance = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_backtest_run_algo_symbol", "algorithm_id", "symbol"),
        Index("ix_backtest_run_pnl", "total_pnl_pct"),
    )
