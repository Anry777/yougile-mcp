from __future__ import annotations

from typing import Dict, Any, List
from datetime import datetime

from sqlalchemy import select, func, case

from src.localdb.session import init_engine, async_session, make_sqlite_url
from src.localdb.models import Project, Board, Column, User, Task, TaskAssignee, Comment, WebhookEvent
from src.config import settings


async def _ensure_engine(db_path: str | None = None) -> None:
    """Ensure async engine/session are initialized for the given DB URL.

    Respects YOUGILE_LOCAL_DB_URL the same way as importer/webhook server.
    """
    from src.localdb.session import async_engine

    if async_engine is not None:
        return

    if db_path and db_path != "./yougile_local.db":
        db_url = make_sqlite_url(db_path)
    elif getattr(settings, "yougile_local_db_url", None):
        db_url = settings.yougile_local_db_url
    else:
        db_url = make_sqlite_url("./yougile_local.db")
    init_engine(db_url)


async def get_db_stats(db_path: str | None = None) -> Dict[str, Any]:
    """Compute basic statistics over local DB.

    Returns counts of core entities and some simple aggregations.
    """
    await _ensure_engine(db_path)
    from src.localdb.session import async_session as session_factory

    if session_factory is None:
        raise RuntimeError("DB session factory is not initialized")

    async with session_factory() as session:
        # Simple counts
        project_count = (await session.execute(select(func.count(Project.id)))).scalar_one()
        board_count = (await session.execute(select(func.count(Board.id)))).scalar_one()
        column_count = (await session.execute(select(func.count(Column.id)))).scalar_one()
        user_count = (await session.execute(select(func.count(User.id)))).scalar_one()
        task_count = (await session.execute(select(func.count(Task.id)))).scalar_one()
        comment_count = (await session.execute(select(func.count(Comment.id)))).scalar_one()
        webhook_count = (await session.execute(select(func.count(WebhookEvent.id)))).scalar_one()

        # Top projects by task count
        top_projects_raw = (
            await session.execute(
                select(Project.id, Project.title, func.count(Task.id).label("task_count"))
                .join(Board, Board.project_id == Project.id, isouter=True)
                .join(Column, Column.board_id == Board.id, isouter=True)
                .join(Task, Task.column_id == Column.id, isouter=True)
                .group_by(Project.id, Project.title)
                .order_by(func.count(Task.id).desc())
                .limit(10)
            )
        ).all()

        top_projects: List[Dict[str, Any]] = [
            {"project_id": pid, "title": title, "tasks": tc or 0}
            for pid, title, tc in top_projects_raw
        ]

        # Tasks by completion/archived flags
        completed_count = (
            await session.execute(select(func.count(Task.id)).where(Task.completed.is_(True)))
        ).scalar_one()
        active_count = (
            await session.execute(select(func.count(Task.id)).where(Task.completed.is_(False)))
        ).scalar_one()
        archived_count = (
            await session.execute(select(func.count(Task.id)).where(Task.archived.is_(True)))
        ).scalar_one()

        # Per-user task statistics (based on TaskAssignee links)
        user_stats_raw = (
            await session.execute(
                select(
                    User.id,
                    User.name,
                    func.count(Task.id).label("total"),
                    func.sum(
                        case((Task.completed.is_(True), 1), else_=0)
                    ).label("completed"),
                    func.sum(
                        case((Task.completed.is_(False), 1), else_=0)
                    ).label("active"),
                    func.sum(
                        case((Task.archived.is_(True), 1), else_=0)
                    ).label("archived"),
                )
                .join(TaskAssignee, TaskAssignee.user_id == User.id)
                .join(Task, Task.id == TaskAssignee.task_id)
                .group_by(User.id, User.name)
                .order_by(func.count(Task.id).desc())
            )
        ).all()

        user_task_stats: List[Dict[str, Any]] = [
            {
                "user_id": uid,
                "name": name,
                "tasks_total": total or 0,
                "tasks_completed": completed or 0,
                "tasks_active": active or 0,
                "tasks_archived": archived or 0,
            }
            for uid, name, total, completed, active, archived in user_stats_raw
        ]

        # Per-project last activity (by latest comment timestamp)
        project_activity_raw = (
            await session.execute(
                select(
                    Project.id,
                    Project.title,
                    func.max(Comment.timestamp).label("last_comment_at"),
                )
                .join(Board, Board.project_id == Project.id, isouter=True)
                .join(Column, Column.board_id == Board.id, isouter=True)
                .join(Task, Task.column_id == Column.id, isouter=True)
                .join(Comment, Comment.task_id == Task.id, isouter=True)
                .group_by(Project.id, Project.title)
            )
        ).all()

        project_last_activity: List[Dict[str, Any]] = [
            {
                "project_id": pid,
                "title": title,
                "last_comment_at": ts.isoformat() if isinstance(ts, datetime) else None,
            }
            for pid, title, ts in project_activity_raw
        ]

    return {
        "projects": project_count,
        "boards": board_count,
        "columns": column_count,
        "users": user_count,
        "tasks": task_count,
        "comments": comment_count,
        "webhook_events": webhook_count,
        "tasks_completed": completed_count,
        "tasks_active": active_count,
        "tasks_archived": archived_count,
        "top_projects_by_tasks": top_projects,
        "user_task_stats": user_task_stats,
        "project_last_activity": project_last_activity,
    }


async def sample_tasks_with_stickers(db_path: str | None = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Вернуть несколько задач с непустыми stickers для анализа (в т.ч. спринтов).

    Возвращает список словарей: task_id, project_title, board_title, column_title, stickers.
    """
    await _ensure_engine(db_path)
    from src.localdb.session import async_session as session_factory

    if session_factory is None:
        raise RuntimeError("DB session factory is not initialized")

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(
                    Task.id,
                    Project.title.label("project_title"),
                    Board.title.label("board_title"),
                    Column.title.label("column_title"),
                    Task.stickers,
                )
                .join(Column, Column.id == Task.column_id, isouter=True)
                .join(Board, Board.id == Column.board_id, isouter=True)
                .join(Project, Project.id == Board.project_id, isouter=True)
                .where(Task.stickers.is_not(None))
                .limit(limit)
            )
        ).all()

    result: List[Dict[str, Any]] = []
    for tid, proj_title, board_title, col_title, stickers in rows:
        result.append(
            {
                "task_id": tid,
                "project_title": proj_title,
                "board_title": board_title,
                "column_title": col_title,
                "stickers": stickers,
            }
        )
    return result
