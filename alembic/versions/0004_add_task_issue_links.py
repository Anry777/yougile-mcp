"""Add task_issue_links table for YouGile↔Redmine mapping

Revision ID: 0004_add_task_issue_links
Revises: 0003_add_task_timestamps
Create Date: 2025-12-06 09:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_add_task_issue_links"
down_revision = "0003_add_task_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_issue_links",
        sa.Column("task_id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("redmine_issue_id", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("task_issue_links")
