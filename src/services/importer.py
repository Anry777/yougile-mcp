from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import projects as api_projects
from src.api import boards as api_boards
from src.api import columns as api_columns
from src.api import tasks as api_tasks
from src.api import users as api_users
from src.api import chats as api_chats

from src.localdb.session import Base, init_engine, make_sqlite_url
from src.localdb.models import Project, Board, Column, User, Task, TaskAssignee, Comment


async def _create_schema_if_needed() -> None:
    # Create tables if they do not exist (first run). Alembic can manage later migrations.
    from src.localdb.session import async_engine
    assert async_engine is not None
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _norm_str(s: Optional[str]) -> Optional[str]:
    if s is None:
        return None
    return s if isinstance(s, str) else str(s)


def _to_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        # Milliseconds epoch
        if isinstance(value, (int, float)) and value > 10_000_000:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        # Seconds epoch
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        # ISO string
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
    except Exception:
        return None
    return None


async def _upsert(session: AsyncSession, model, data: Dict[str, Any], pk_field: str = "id"):
    pk = data.get(pk_field)
    if not pk:
        return None
    obj = await session.get(model, pk)
    if obj is None:
        obj = model(**data)
        session.add(obj)
    else:
        for k, v in data.items():
            setattr(obj, k, v)
    return obj


async def _upsert_task_assignee(session: AsyncSession, task_id: str, user_id: str) -> None:
    if not task_id or not user_id:
        return
    obj = await session.get(TaskAssignee, (task_id, user_id))
    if obj is None:
        session.add(TaskAssignee(task_id=task_id, user_id=user_id))


def _extract_assigned_ids(task: Dict[str, Any]) -> List[str]:
    ids: List[str] = []
    if not isinstance(task, dict):
        return ids
    # 'assigned' may be a list of userId strings
    a = task.get("assigned")
    if isinstance(a, list):
        for v in a:
            if isinstance(v, str):
                ids.append(v)
            elif isinstance(v, dict):
                uid = v.get("id") if isinstance(v.get("id"), str) else None
                if uid:
                    ids.append(uid)
        if ids:
            return ids
    # 'assignedUsers' may be a list of dicts or strings
    au = task.get("assignedUsers")
    if isinstance(au, list):
        for v in au:
            if isinstance(v, str):
                ids.append(v)
            elif isinstance(v, dict):
                uid = v.get("id") if isinstance(v.get("id"), str) else None
                if uid:
                    ids.append(uid)
    return ids


async def _list_tasks_in_project(client: YouGileClient, allowed_columns: List[str], include_deleted: bool = False, max_fetch: int = 5000) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    offset = 0
    page = 1000
    while len(results) < max_fetch:
        current_limit = min(page, max_fetch - len(results))
        batch = await api_tasks.get_tasks(client, limit=current_limit, offset=offset, include_deleted=include_deleted)
        if not batch:
            break
        for t in batch:
            cid = t.get("columnId")
            if cid in allowed_columns:
                results.append(t)
        if len(batch) < current_limit:
            break
        offset += len(batch)
    return results


async def import_project(
    project_id: str,
    db_path: str = "./yougile_local.db",
    reset: bool = False,
    prune: bool = False,
) -> Dict[str, Any]:
    # Init DB
    db_url = make_sqlite_url(db_path)
    init_engine(db_url)
    await _create_schema_if_needed()

    # API client
    async with YouGileClient(core_auth.auth_manager) as client:
        # Project
        proj = await api_projects.get_project(client, project_id)
        # Begin DB tx
        from src.localdb.session import async_session
        assert async_session is not None
        async with async_session() as session:
            async with session.begin():
                if reset:
                    # delete existing project tree
                    await session.execute(delete(Project).where(Project.id == project_id))
                # Upsert project
                await _upsert(session, Project, {
                    "id": proj.get("id") or project_id,
                    "title": _norm_str(proj.get("title")) or "",
                    "description": proj.get("description"),
                })

            # Boards
            boards = await api_boards.get_boards(client, project_id=project_id, limit=1000, offset=0)
            board_ids: Set[str] = set()
            async with session.begin():
                for b in boards:
                    bid = b.get("id")
                    if not bid:
                        continue
                    board_ids.add(bid)
                    await _upsert(session, Board, {
                        "id": bid,
                        "title": _norm_str(b.get("title")) or "",
                        "project_id": project_id,
                    })

            # Columns
            col_ids: Set[str] = set()
            async with session.begin():
                for bid in board_ids:
                    cols = await api_columns.get_columns(client, board_id=bid)
                    for c in cols:
                        cid = c.get("id")
                        if not cid:
                            continue
                        col_ids.add(cid)
                        await _upsert(session, Column, {
                            "id": cid,
                            "title": _norm_str(c.get("title")) or "",
                            # API uses 1..16 ints for color
                            "color": c.get("color") if isinstance(c.get("color"), int) else None,
                            "board_id": bid,
                        })

            # Users (import all company users to resolve assignees)
            users = await api_users.get_users(client)
            user_ids: Set[str] = set()
            async with session.begin():
                for u in users:
                    uid = u.get("id")
                    if not uid:
                        continue
                    user_ids.add(uid)
                    await _upsert(session, User, {
                        "id": uid,
                        "name": _norm_str(u.get("name") or u.get("firstName")) if isinstance(u.get("name") or u.get("firstName"), str) else u.get("email"),
                        "email": _norm_str(u.get("email")),
                        "role": _norm_str(u.get("role")),
                    })

            # Tasks (project-wide pagination, filtered by columns)
            tasks = await _list_tasks_in_project(client, list(col_ids), include_deleted=False, max_fetch=10000)
            task_ids: Set[str] = set()
            async with session.begin():
                for t in tasks:
                    tid = t.get("id")
                    if not tid:
                        continue
                    task_ids.add(tid)
                    # Map fields
                    data = {
                        "id": tid,
                        "title": _norm_str(t.get("title")) or "",
                        "description": t.get("description"),
                        "column_id": t.get("columnId"),
                        "completed": t.get("completed"),
                        "archived": t.get("archived"),
                        "deadline": t.get("deadline"),
                        "time_tracking": t.get("timeTracking"),
                        "stickers": t.get("stickers"),
                        "checklists": t.get("checklists"),
                    }
                    await _upsert(session, Task, data)
                # Clear and set assignees for upserted tasks (enrich each task with full details to get reliable assignees)
                for t in tasks:
                    tid = t.get("id")
                    if not tid:
                        continue
                    # Fetch full task details to ensure assignees are present
                    t_full = t
                    try:
                        t_full = await api_tasks.get_task(client, tid)
                    except Exception:
                        t_full = t
                    assigned = _extract_assigned_ids(t_full) or _extract_assigned_ids(t)
                    # delete existing links
                    await session.execute(delete(TaskAssignee).where(TaskAssignee.task_id == tid))
                    for uid in assigned:
                        await _upsert_task_assignee(session, tid, uid)

            # Comments via chats: chatId == taskId
            async with session.begin():
                for tid in task_ids:
                    try:
                        msgs = await api_chats.get_chat_messages(client, tid)
                    except Exception:
                        msgs = []
                    # Handle paging wrapper { paging, content: [...] }
                    msg_list = []
                    if isinstance(msgs, dict):
                        msg_list = msgs.get("content", []) or []
                    elif isinstance(msgs, list):
                        msg_list = msgs
                    for m in msg_list:
                        if not isinstance(m, dict):
                            continue
                        mid = m.get("id")
                        if mid is None:
                            continue
                        author_id = (
                            m.get("authorId")
                            or m.get("author")
                            or m.get("userId")
                            or m.get("fromUserId")
                        )
                        ts = m.get("timestamp") or m.get("createdAt")
                        text = m.get("text") or m.get("message") or ""
                        await _upsert(session, Comment, {
                            "id": str(mid),
                            "task_id": tid,
                            "author_id": author_id if isinstance(author_id, str) else None,
                            "text": str(text),
                            "timestamp": _to_dt(ts) or datetime.now(timezone.utc),
                        })

            # Prune (optional): remove local entities of project not present remotely
            if prune:
                async with session.begin():
                    # Boards
                    if board_ids:
                        res = await session.execute(select(Board.id).where(Board.project_id == project_id))
                        local_board_ids = {r[0] for r in res.all()}
                        stale_boards = local_board_ids - board_ids
                        if stale_boards:
                            await session.execute(delete(Board).where(Board.id.in_(list(stale_boards))))
                    # Columns
                    if col_ids:
                        res = await session.execute(select(Column.id).join(Board).where(Board.project_id == project_id))
                        local_col_ids = {r[0] for r in res.all()}
                        stale_cols = local_col_ids - col_ids
                        if stale_cols:
                            await session.execute(delete(Column).where(Column.id.in_(list(stale_cols))))
                    # Tasks
                    res = await session.execute(select(Task.id).join(Column).join(Board).where(Board.project_id == project_id))
                    local_task_ids = {r[0] for r in res.all()}
                    stale_tasks = local_task_ids - task_ids
                    if stale_tasks:
                        await session.execute(delete(Task).where(Task.id.in_(list(stale_tasks))))

            await session.commit()

    return {
        "success": True,
        "project_id": project_id,
        "boards": len(board_ids),
        "columns": len(col_ids),
        "tasks": len(task_ids),
    }
