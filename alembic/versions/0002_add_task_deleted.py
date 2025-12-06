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
    # На проде колонка tasks.deleted могла быть добавлена ранее вручную или
    # другой миграцией. Чтобы alembic upgrade head не падал с DuplicateColumnError,
    # проверяем наличие колонки перед ALTER TABLE.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("tasks")]
    if "deleted" not in columns:
        op.add_column("tasks", sa.Column("deleted", sa.Boolean(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("tasks")]
    if "deleted" in columns:
        op.drop_column("tasks", "deleted")
