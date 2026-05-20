"""Add live paper-session capture tables for replay comparison

Revision ID: 006
Revises: 005
Create Date: 2026-05-15 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

from app.models.market_data import Timeframe


# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("live_trade", sa.Column("event_time", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "live_session_seed_candle",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_symbol_id", sa.String(36), sa.ForeignKey("live_session_symbol.id"), nullable=False),
        sa.Column(
            "timeframe",
            sa.Enum(Timeframe, values_callable=lambda enum_cls: [member.value for member in enum_cls], native_enum=False),
            nullable=False,
        ),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_symbol_id", "timeframe", "time", name="uq_live_session_seed_candle"),
    )
    op.create_index(
        "ix_live_session_seed_lookup",
        "live_session_seed_candle",
        ["session_symbol_id", "timeframe", "time"],
    )

    op.create_table(
        "live_session_tick",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_symbol_id", sa.String(36), sa.ForeignKey("live_session_symbol.id"), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_symbol_id", "time", name="uq_live_session_tick"),
    )
    op.create_index(
        "ix_live_session_tick_lookup",
        "live_session_tick",
        ["session_symbol_id", "time"],
    )


def downgrade() -> None:
    op.drop_table("live_session_tick")
    op.drop_table("live_session_seed_candle")
    op.drop_column("live_trade", "event_time")
