"""Add max_total_exposure to live_session and max_daily_entries to live_session_symbol

Revision ID: 008
Revises: 007
Create Date: 2026-05-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Session-level: shared max total exposure across all symbols
    op.add_column(
        "live_session",
        sa.Column("max_total_exposure", sa.Float, nullable=False, server_default="50000.0"),
    )

    # Per-symbol: max number of buy entries per day
    op.add_column(
        "live_session_symbol",
        sa.Column("max_daily_entries", sa.Integer, nullable=False, server_default="10"),
    )
    op.add_column(
        "live_session_symbol",
        sa.Column("daily_entry_count", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("live_session_symbol", "daily_entry_count")
    op.drop_column("live_session_symbol", "max_daily_entries")
    op.drop_column("live_session", "max_total_exposure")
