"""Expand task fields with full API data

Revision ID: 0004_expand_task_fields
Revises: 0003_add_task_timestamps
Create Date: 2025-12-02 05:10:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0004_expand_task_fields"
down_revision = "0003_add_task_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("tasks")]

    # Additional metadata fields
    if "created_by" not in columns:
        op.add_column("tasks", sa.Column("created_by", sa.String(length=64), nullable=True))
    if "id_task_common" not in columns:
        op.add_column("tasks", sa.Column("id_task_common", sa.String(length=64), nullable=True))
    if "id_task_project" not in columns:
        op.add_column("tasks", sa.Column("id_task_project", sa.String(length=64), nullable=True))
    if "type" not in columns:
        op.add_column("tasks", sa.Column("type", sa.String(length=64), nullable=True))
    if "color" not in columns:
        op.add_column("tasks", sa.Column("color", sa.String(length=64), nullable=True))
    if "organization_id" not in columns:
        op.add_column("tasks", sa.Column("organization_id", sa.String(length=64), nullable=True))

    # Complex JSON fields
    if "subtasks" not in columns:
        op.add_column("tasks", sa.Column("subtasks", sa.JSON(), nullable=True))
    if "links" not in columns:
        op.add_column("tasks", sa.Column("links", sa.JSON(), nullable=True))
    if "blocked_points" not in columns:
        op.add_column("tasks", sa.Column("blocked_points", sa.JSON(), nullable=True))
    if "contact_person_ids" not in columns:
        op.add_column("tasks", sa.Column("contact_person_ids", sa.JSON(), nullable=True))
    if "deal" not in columns:
        op.add_column("tasks", sa.Column("deal", sa.JSON(), nullable=True))
    if "stopwatch" not in columns:
        op.add_column("tasks", sa.Column("stopwatch", sa.JSON(), nullable=True))
    if "timer" not in columns:
        op.add_column("tasks", sa.Column("timer", sa.JSON(), nullable=True))
    if "payload" not in columns:
        op.add_column("tasks", sa.Column("payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("tasks")]

    if "payload" in columns:
        op.drop_column("tasks", "payload")
    if "timer" in columns:
        op.drop_column("tasks", "timer")
    if "stopwatch" in columns:
        op.drop_column("tasks", "stopwatch")
    if "deal" in columns:
        op.drop_column("tasks", "deal")
    if "contact_person_ids" in columns:
        op.drop_column("tasks", "contact_person_ids")
    if "blocked_points" in columns:
        op.drop_column("tasks", "blocked_points")
    if "links" in columns:
        op.drop_column("tasks", "links")
    if "subtasks" in columns:
        op.drop_column("tasks", "subtasks")
    if "organization_id" in columns:
        op.drop_column("tasks", "organization_id")
    if "color" in columns:
        op.drop_column("tasks", "color")
    if "type" in columns:
        op.drop_column("tasks", "type")
    if "id_task_project" in columns:
        op.drop_column("tasks", "id_task_project")
    if "id_task_common" in columns:
        op.drop_column("tasks", "id_task_common")
    if "created_by" in columns:
        op.drop_column("tasks", "created_by")
