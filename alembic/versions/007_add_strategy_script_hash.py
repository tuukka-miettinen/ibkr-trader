"""Add script_hash to strategy_algorithm

Revision ID: 007
Revises: 006
Create Date: 2026-05-20 00:00:00.000000

"""
from __future__ import annotations

import hashlib
import io
import tokenize

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def _script_hash(script: str) -> str:
    normalized = script.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[str] = []
    ignored = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
    for tok in tokenize.generate_tokens(io.StringIO(normalized).readline):
        if tok.type in ignored:
            continue
        tokens.append(f"{tok.type}:{tok.string}")
    canonical = "\x1f".join(tokens)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column(
        "strategy_algorithm",
        sa.Column("script_hash", sa.String(length=64), nullable=True),
    )

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, script FROM strategy_algorithm")).fetchall()
    for row in rows:
        bind.execute(
            sa.text("UPDATE strategy_algorithm SET script_hash = :script_hash WHERE id = :id"),
            {"id": row.id, "script_hash": _script_hash(row.script)},
        )

    op.alter_column("strategy_algorithm", "script_hash", nullable=False)
    op.create_index("ix_strategy_name_hash", "strategy_algorithm", ["name", "script_hash"])


def downgrade() -> None:
    op.drop_index("ix_strategy_name_hash", table_name="strategy_algorithm")
    op.drop_column("strategy_algorithm", "script_hash")
