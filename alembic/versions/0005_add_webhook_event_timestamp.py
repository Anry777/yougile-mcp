"""Add event_timestamp to webhook_events

Revision ID: 0005_add_webhook_event_timestamp
Revises: 0004_expand_task_fields
Create Date: 2025-12-02 05:15:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_add_webhook_event_timestamp"
down_revision = "0004_expand_task_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add event_timestamp column to store original Yougile event time
    op.add_column("webhook_events", sa.Column("event_timestamp", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("webhook_events", "event_timestamp")
