"""Add is_favorite column to strategy_algorithm

Revision ID: 004
Revises: 003
Create Date: 2026-05-12 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "strategy_algorithm",
        sa.Column("is_favorite", sa.Boolean, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("strategy_algorithm", "is_favorite")
