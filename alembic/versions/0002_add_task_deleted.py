"""Add deleted field to tasks

Revision ID: 0002_add_task_deleted
Revises: 0001_initial
Create Date: 2025-11-21 04:50:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0002_add_task_deleted"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("deleted", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "deleted")
