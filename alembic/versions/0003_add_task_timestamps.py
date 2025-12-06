"""Add task timestamp fields

Revision ID: 0003_add_task_timestamps
Revises: 0002_add_task_deleted
Create Date: 2025-12-02 05:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0003_add_task_timestamps"
down_revision = "0002_add_task_deleted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("tasks")]

    if "created_at" not in columns:
        op.add_column("tasks", sa.Column("created_at", sa.DateTime(), nullable=True))
    if "completed_at" not in columns:
        op.add_column("tasks", sa.Column("completed_at", sa.DateTime(), nullable=True))
    if "archived_at" not in columns:
        op.add_column("tasks", sa.Column("archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("tasks")]

    if "archived_at" in columns:
        op.drop_column("tasks", "archived_at")
    if "completed_at" in columns:
        op.drop_column("tasks", "completed_at")
    if "created_at" in columns:
        op.drop_column("tasks", "created_at")
