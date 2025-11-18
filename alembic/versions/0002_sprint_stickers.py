"""Add sprint stickers tables

Revision ID: 0002_sprint_stickers
Revises: 0001_initial
Create Date: 2025-11-18 20:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_sprint_stickers"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if not insp.has_table("sprint_stickers"):
        op.create_table(
            "sprint_stickers",
            sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("deleted", sa.Boolean(), nullable=True),
        )

    if not insp.has_table("sprint_states"):
        op.create_table(
            "sprint_states",
            sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
            sa.Column(
                "sticker_id",
                sa.String(length=64),
                sa.ForeignKey("sprint_stickers.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("begin", sa.DateTime(), nullable=True),
            sa.Column("end", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("sprint_states"):
        op.drop_table("sprint_states")
    if insp.has_table("sprint_stickers"):
        op.drop_table("sprint_stickers")
