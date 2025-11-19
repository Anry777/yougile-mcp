"""
Initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2025-11-13 21:59:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # projects
    op.create_table(
        'projects',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
    )

    # departments
    op.create_table(
        'departments',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('parent_id', sa.String(length=64), nullable=True),
        sa.Column('deleted', sa.Boolean(), nullable=True),
    )

    # boards
    op.create_table(
        'boards',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
    )

    # columns
    op.create_table(
        'columns',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('color', sa.Integer(), nullable=True),
        sa.Column('board_id', sa.String(length=64), sa.ForeignKey('boards.id', ondelete='CASCADE'), nullable=False),
    )

    # users
    op.create_table(
        'users',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('role', sa.String(length=64), nullable=True),
    )

    # tasks
    op.create_table(
        'tasks',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('title', sa.String(length=1000), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('column_id', sa.String(length=64), sa.ForeignKey('columns.id', ondelete='SET NULL'), nullable=True),
        sa.Column('completed', sa.Boolean(), nullable=True),
        sa.Column('archived', sa.Boolean(), nullable=True),
        sa.Column('deadline', sa.JSON(), nullable=True),
        sa.Column('time_tracking', sa.JSON(), nullable=True),
        sa.Column('stickers', sa.JSON(), nullable=True),
        sa.Column('checklists', sa.JSON(), nullable=True),
    )

    # task_assignees
    op.create_table(
        'task_assignees',
        sa.Column('task_id', sa.String(length=64), sa.ForeignKey('tasks.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('user_id', sa.String(length=64), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.UniqueConstraint('task_id', 'user_id', name='uq_task_user'),
    )

    # comments
    op.create_table(
        'comments',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('task_id', sa.String(length=64), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('author_id', sa.String(length=64), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
    )

    # sprint_stickers
    op.create_table(
        'sprint_stickers',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('deleted', sa.Boolean(), nullable=True),
    )

    # sprint_states
    op.create_table(
        'sprint_states',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('sticker_id', sa.String(length=64), sa.ForeignKey('sprint_stickers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('begin', sa.DateTime(), nullable=True),
        sa.Column('end', sa.DateTime(), nullable=True),
    )

    # string_stickers
    op.create_table(
        'string_stickers',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('deleted', sa.Boolean(), nullable=True),
    )

    # string_states
    op.create_table(
        'string_states',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('sticker_id', sa.String(length=64), sa.ForeignKey('string_stickers.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
    )

    # project_roles
    op.create_table(
        'project_roles',
        sa.Column('id', sa.String(length=64), primary_key=True, nullable=False),
        sa.Column('project_id', sa.String(length=64), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('permissions', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('project_roles')
    op.drop_table('string_states')
    op.drop_table('string_stickers')
    op.drop_table('sprint_states')
    op.drop_table('sprint_stickers')
    op.drop_table('comments')
    op.drop_table('task_assignees')
    op.drop_table('tasks')
    op.drop_table('users')
    op.drop_table('columns')
    op.drop_table('boards')
    op.drop_table('departments')
    op.drop_table('projects')
