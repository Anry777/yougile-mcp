from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy import String, Text, ForeignKey, Boolean, Integer, DateTime, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .session import Base


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    boards: Mapped[List["Board"]] = relationship("Board", back_populates="project", cascade="all, delete-orphan")


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))

    project: Mapped["Project"] = relationship("Project", back_populates="boards")
    columns: Mapped[List["Column"]] = relationship("Column", back_populates="board", cascade="all, delete-orphan")


class Column(Base):
    __tablename__ = "columns"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    color: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id", ondelete="CASCADE"))

    board: Mapped["Board"] = relationship("Board", back_populates="columns")
    tasks: Mapped[List["Task"]] = relationship("Task", back_populates="column", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="author")


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(1000))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    column_id: Mapped[Optional[str]] = mapped_column(ForeignKey("columns.id", ondelete="SET NULL"), nullable=True)
    completed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    archived: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    # Stickers and complex fields as JSON blobs for SQLite simplicity
    deadline: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    time_tracking: Mapped[Optional[dict]] = mapped_column("time_tracking", JSON, nullable=True)
    stickers: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    checklists: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)

    column: Mapped[Optional["Column"]] = relationship("Column", back_populates="tasks")
    assignees: Mapped[List[User]] = relationship(
        secondary=lambda: TaskAssignee.__table__,
        backref="tasks",
        lazy="selectin",
    )
    comments: Mapped[List["Comment"]] = relationship("Comment", back_populates="task", cascade="all, delete-orphan")


class TaskAssignee(Base):
    __tablename__ = "task_assignees"
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_user"),
    )

    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    author_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime)

    task: Mapped["Task"] = relationship("Task", back_populates="comments")
    author: Mapped[Optional["User"]] = relationship("User", back_populates="comments")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32))
    event_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_external_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("event_external_id", name="uq_webhook_event_external_id"),
    )


class SprintSticker(Base):
    __tablename__ = "sprint_stickers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    deleted: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    states: Mapped[List["SprintState"]] = relationship(
        "SprintState", back_populates="sticker", cascade="all, delete-orphan"
    )


class SprintState(Base):
    __tablename__ = "sprint_states"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sticker_id: Mapped[str] = mapped_column(
        ForeignKey("sprint_stickers.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255))
    begin: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    sticker: Mapped["SprintSticker"] = relationship("SprintSticker", back_populates="states")
