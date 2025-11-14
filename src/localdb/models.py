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
