"""Add live trading tables

Revision ID: 005
Revises: 004
Create Date: 2026-05-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_session",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "status",
            sa.Enum("created", "running", "stopped", "error", name="live_session_status"),
            nullable=False,
            server_default="created",
        ),
        sa.Column(
            "order_type",
            sa.Enum("market", "limit", name="live_order_type"),
            nullable=False,
            server_default="market",
        ),
        sa.Column("position_size", sa.Float, nullable=False, server_default="1000.0"),
        sa.Column("max_entries", sa.Integer, nullable=False, server_default="5"),
        sa.Column("max_daily_loss", sa.Float, nullable=False, server_default="500.0"),
        sa.Column("strategy_state_json", sa.JSON, nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("stopped_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_live_session_status", "live_session", ["status"])

    op.create_table(
        "live_session_symbol",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_session.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("algorithm_id", sa.String(36), sa.ForeignKey("strategy_algorithm.id"), nullable=False),
        sa.Column("allocated_capital", sa.Float, nullable=False, server_default="10000.0"),
        sa.Column("position_size", sa.Float, nullable=False, server_default="1000.0"),
        sa.Column("max_entries", sa.Integer, nullable=False, server_default="5"),
        sa.Column("current_shares", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("current_cost", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("cash_remaining", sa.Float, nullable=False, server_default="10000.0"),
        sa.Column("realized_pnl", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("unrealized_pnl", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("daily_realized_pnl", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("last_price", sa.Float, nullable=True),
        sa.Column("strategy_state_json", sa.JSON, nullable=True),
        sa.UniqueConstraint("session_id", "symbol", name="uq_live_session_symbol"),
    )
    op.create_index("ix_live_session_symbol_session", "live_session_symbol", ["session_id"])
    op.create_index("ix_live_session_symbol_lookup", "live_session_symbol", ["session_id", "symbol"])

    op.create_table(
        "live_trade",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("live_session.id"), nullable=False),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("side", sa.Enum("buy", "sell", name="trade_side"), nullable=False),
        sa.Column("order_type", sa.Enum("market", "limit", name="trade_order_type"), nullable=False),
        sa.Column("shares", sa.Float, nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("cost", sa.Float, nullable=False),
        sa.Column("pnl", sa.Float, nullable=True),
        sa.Column("pnl_pct", sa.Float, nullable=True),
        sa.Column("ibkr_order_id", sa.Integer, nullable=True),
        sa.Column(
            "status",
            sa.Enum("pending", "filled", "cancelled", "error", name="trade_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_live_trade_session", "live_trade", ["session_id"])
    op.create_index("ix_live_trade_session_symbol", "live_trade", ["session_id", "symbol"])
    op.create_index("ix_live_trade_created", "live_trade", ["created_at"])

    op.create_table(
        "live_position_entry",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_symbol_id", sa.String(36), sa.ForeignKey("live_session_symbol.id"), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("shares", sa.Float, nullable=False),
        sa.Column("cost", sa.Float, nullable=False),
        sa.Column("ibkr_order_id", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_live_position_entry_symbol", "live_position_entry", ["session_symbol_id"])


def downgrade() -> None:
    op.drop_table("live_position_entry")
    op.drop_table("live_trade")
    op.drop_table("live_session_symbol")
    op.drop_table("live_session")
    op.execute("DROP TYPE IF EXISTS live_session_status")
    op.execute("DROP TYPE IF EXISTS live_order_type")
    op.execute("DROP TYPE IF EXISTS trade_side")
    op.execute("DROP TYPE IF EXISTS trade_order_type")
    op.execute("DROP TYPE IF EXISTS trade_status")
