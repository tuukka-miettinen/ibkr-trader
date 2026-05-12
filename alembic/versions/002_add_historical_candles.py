"""Add historical candle storage

Revision ID: 002
Revises: 001
Create Date: 2026-05-11 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_candle",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column(
            "timeframe",
            sa.Enum("1m", "5m", "15m", "1h", name="timeframe", native_enum=False),
            nullable=False,
        ),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Float(), nullable=False),
        sa.Column("high", sa.Float(), nullable=False),
        sa.Column("low", sa.Float(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "timeframe", "time", name="uq_historical_candle_key"),
    )
    op.create_index("ix_historical_candle_lookup", "historical_candle", ["symbol", "timeframe", "time"])
    op.create_index("ix_historical_candle_time", "historical_candle", ["time"])


def downgrade() -> None:
    op.drop_index("ix_historical_candle_time", table_name="historical_candle")
    op.drop_index("ix_historical_candle_lookup", table_name="historical_candle")
    op.drop_table("historical_candle")