"""Add tick_chunk, strategy_algorithm, and backtest_run tables

Revision ID: 003
Revises: 002
Create Date: 2026-05-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- tick_chunk ---
    op.create_table(
        "tick_chunk",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("hour_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_json", sa.JSON, nullable=False),
        sa.Column("bar_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "hour_start", name="uq_tick_chunk_key"),
    )
    op.create_index("ix_tick_chunk_lookup", "tick_chunk", ["symbol", "hour_start"])

    # --- strategy_algorithm ---
    op.create_table(
        "strategy_algorithm",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("script", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_strategy_name_version"),
    )
    op.create_index("ix_strategy_name", "strategy_algorithm", ["name"])

    # --- backtest_run ---
    op.create_table(
        "backtest_run",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("algorithm_id", sa.String(length=36), sa.ForeignKey("strategy_algorithm.id"), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False, server_default="tick"),
        sa.Column("lookback_days", sa.Integer, nullable=True),
        sa.Column("config_json", sa.JSON, nullable=False),
        sa.Column("result_json", sa.JSON, nullable=False),
        sa.Column("num_trades", sa.Integer, nullable=False, server_default="0"),
        sa.Column("total_pnl", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_pnl_pct", sa.Float, nullable=False, server_default="0"),
        sa.Column("win_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("final_balance", sa.Float, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backtest_run_algorithm_id", "backtest_run", ["algorithm_id"])
    op.create_index("ix_backtest_run_algo_symbol", "backtest_run", ["algorithm_id", "symbol"])
    op.create_index("ix_backtest_run_pnl", "backtest_run", ["total_pnl_pct"])


def downgrade() -> None:
    op.drop_index("ix_backtest_run_pnl", table_name="backtest_run")
    op.drop_index("ix_backtest_run_algo_symbol", table_name="backtest_run")
    op.drop_index("ix_backtest_run_algorithm_id", table_name="backtest_run")
    op.drop_table("backtest_run")

    op.drop_index("ix_strategy_name", table_name="strategy_algorithm")
    op.drop_table("strategy_algorithm")

    op.drop_index("ix_tick_chunk_lookup", table_name="tick_chunk")
    op.drop_table("tick_chunk")
