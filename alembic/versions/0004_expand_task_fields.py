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
    # Additional metadata fields
    op.add_column("tasks", sa.Column("created_by", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("id_task_common", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("id_task_project", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("type", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("color", sa.String(length=64), nullable=True))
    op.add_column("tasks", sa.Column("organization_id", sa.String(length=64), nullable=True))
    
    # Complex JSON fields
    op.add_column("tasks", sa.Column("subtasks", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("links", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("blocked_points", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("contact_person_ids", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("deal", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("stopwatch", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("timer", sa.JSON(), nullable=True))
    op.add_column("tasks", sa.Column("payload", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "payload")
    op.drop_column("tasks", "timer")
    op.drop_column("tasks", "stopwatch")
    op.drop_column("tasks", "deal")
    op.drop_column("tasks", "contact_person_ids")
    op.drop_column("tasks", "blocked_points")
    op.drop_column("tasks", "links")
    op.drop_column("tasks", "subtasks")
    op.drop_column("tasks", "organization_id")
    op.drop_column("tasks", "color")
    op.drop_column("tasks", "type")
    op.drop_column("tasks", "id_task_project")
    op.drop_column("tasks", "id_task_common")
    op.drop_column("tasks", "created_by")
