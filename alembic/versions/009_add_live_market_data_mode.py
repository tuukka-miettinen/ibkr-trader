"""Add market_data_mode to live_session

Revision ID: 009
Revises: 008
Create Date: 2026-05-21 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_session",
        sa.Column("market_data_mode", sa.String(length=16), nullable=False, server_default="realtime"),
    )


def downgrade() -> None:
    op.drop_column("live_session", "market_data_mode")
