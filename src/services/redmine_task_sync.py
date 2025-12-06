"""
Синхронизация задач YouGile в Redmine с использованием маппинга колонок в статусы.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Set

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import settings
from src.config.column_status_mapping import get_redmine_status, DEFAULT_STATUS
from src.localdb.session import init_engine
from src.localdb.models import (
    Task as LocalTask,
    Column as LocalColumn,
    Board as LocalBoard,
    TaskIssueLink as LocalTaskIssueLink,
)

logger = logging.getLogger(__name__)


def _ensure_local_session_factory(db_path: str | None = None):
    """Ensure async session factory for local YouGile DB is initialized."""
    from src.localdb.session import async_engine, async_session as session_factory

    if async_engine is not None and session_factory is not None:
        return session_factory

    if db_path and db_path != "./yougile_local.db":
        db_url = db_path
    elif getattr(settings, "yougile_local_db_url", None):
        db_url = settings.yougile_local_db_url
    else:
        raise RuntimeError("Database URL is not configured")

    init_engine(db_url)
    from src.localdb.session import async_session as session_factory2

    if session_factory2 is None:
        raise RuntimeError("DB session factory is not initialized")

    return session_factory2


def _get_redmine_base_config() -> Dict[str, Any]:
    """Получить базовые настройки подключения к Redmine."""
    url = (
        getattr(settings, "redmine_url", None)
        or os.environ.get("REDMINE_URL")
        or os.environ.get("redmine_url")
    )
    api_key = (
        getattr(settings, "redmine_api_key", None)
        or os.environ.get("REDMINE_API_KEY")
        or os.environ.get("redmine_api_key")
    )

    verify: bool = True
    verify_env = os.environ.get("REDMINE_VERIFY_SSL")
    if verify_env is not None:
        verify = verify_env not in {"0", "false", "False"}
    elif hasattr(settings, "redmine_verify_ssl"):
        verify = bool(getattr(settings, "redmine_verify_ssl"))

    if not url or not api_key:
        raise RuntimeError(
            "Redmine settings are not configured. Set REDMINE_URL and REDMINE_API_KEY in .env",
        )

    return {"url": url, "api_key": api_key, "verify": verify}


def _load_excluded_project_ids() -> Set[str]:
    """Загрузить список ID проектов YouGile, которые нужно игнорировать."""
    path = os.environ.get("REDMINE_SYNC_EXCLUDE_PROJECTS")
    if not path:
        current_dir = os.path.dirname(os.path.dirname(__file__))
        project_root = os.path.dirname(current_dir)
        default_path = os.path.join(project_root, "cli", "redmine_sync_exclude_projects.txt")
        path = default_path

    excluded: Set[str] = set()

    if not os.path.exists(path):
        return excluded

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                excluded.add(line)
    except Exception:
        return excluded

    return excluded


def _build_project_identifier(project_id: str | None) -> str:
    """Построить identifier для Redmine-проекта по ID проекта YouGile."""
    base = (project_id or "").strip().lower()
    if not base:
        base = "project"

    safe_chars: list[str] = []
    for ch in base:
        if "a" <= ch <= "z" or "0" <= ch <= "9" or ch in "-_":
            safe_chars.append(ch)
        else:
            safe_chars.append("-")

    identifier = "yg-" + "".join(safe_chars)
    return identifier[:100]


def _build_board_identifier(board_id: str | None) -> str:
    """Построить identifier для Redmine-подпроекта (доски) по ID доски YouGile."""
    base = (board_id or "").strip().lower()
    if not base:
        base = "board"

    safe_chars: list[str] = []
    for ch in base:
        if "a" <= ch <= "z" or "0" <= ch <= "9" or ch in "-_":
            safe_chars.append(ch)
        else:
            safe_chars.append("-")

    identifier = "yg-b-" + "".join(safe_chars)
    return identifier[:100]


async def _fetch_redmine_projects_by_identifier(client: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
    """Выгрузить все проекты Redmine и построить индекс по identifier."""
    projects_by_identifier: Dict[str, Dict[str, Any]] = {}
    limit = 100
    offset = 0

    while True:
        try:
            resp = await client.get("/projects.json", params={"limit": limit, "offset": offset})
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch Redmine projects: {exc}") from exc

        if resp.status_code >= 400:
            raise RuntimeError(
                f"Failed to fetch Redmine projects: HTTP {resp.status_code} {resp.text}",
            )

        data = resp.json()
        projects = data.get("projects") or []
        total_count = int(data.get("total_count", len(projects)))

        for p in projects:
            identifier = (p.get("identifier") or "").strip().lower()
            if identifier:
                projects_by_identifier[identifier] = p

        if not projects or offset + limit >= total_count:
            break
        offset += limit

    return projects_by_identifier


async def _fetch_redmine_users_by_email(client: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
    """Выгрузить всех пользователей Redmine и построить индекс по email."""
    users_by_email: Dict[str, Dict[str, Any]] = {}
    limit = 100
    offset = 0

    while True:
        try:
            resp = await client.get("/users.json", params={"limit": limit, "offset": offset})
        except Exception as exc:
            raise RuntimeError(f"Failed to fetch Redmine users: {exc}") from exc

        if resp.status_code >= 400:
            raise RuntimeError(f"Failed to fetch Redmine users: HTTP {resp.status_code} {resp.text}")

        data = resp.json()
        users = data.get("users") or []
        total_count = int(data.get("total_count", len(users)))

        for u in users:
            email = (u.get("mail") or "").strip().lower()
            if email:
                users_by_email[email] = u

        if not users or offset + limit >= total_count:
            break
        offset += limit

    return users_by_email


async def _fetch_redmine_statuses(client: httpx.AsyncClient) -> Dict[str, int]:
    """Получить список статусов Redmine и построить индекс название -> ID."""
    try:
        resp = await client.get("/issue_statuses.json")
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch Redmine statuses: {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to fetch Redmine statuses: HTTP {resp.status_code} {resp.text}")

    data = resp.json()
    statuses = data.get("issue_statuses") or []

    status_map: Dict[str, int] = {}
    for s in statuses:
        name = s.get("name", "").strip()
        status_id = s.get("id")
        if name and status_id:
            status_map[name] = status_id

    return status_map


async def _fetch_redmine_trackers(client: httpx.AsyncClient) -> Dict[str, int]:
    """Получить список трекеров Redmine и построить индекс название -> ID."""
    try:
        resp = await client.get("/trackers.json")
    except Exception as exc:
        raise RuntimeError(f"Failed to fetch Redmine trackers: {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(f"Failed to fetch Redmine trackers: HTTP {resp.status_code} {resp.text}")

    data = resp.json()
    trackers = data.get("trackers") or []

    tracker_map: Dict[str, int] = {}
    for t in trackers:
        name = t.get("name", "").strip()
        tracker_id = t.get("id")
        if name and tracker_id:
            tracker_map[name] = tracker_id

    return tracker_map


async def sync_tasks(db_path: str | None = None, dry_run: bool = True) -> Dict[str, Any]:
    """
    Синхронизация задач из локальной БД YouGile в Redmine.
    
    Для каждой задачи:
    - Определяется статус Redmine на основе колонки YouGile
    - Создаётся issue в соответствующем проекте Redmine
    - Устанавливаются assignees, description, etc.
    
    Args:
        db_path: Путь к БД YouGile (опционально)
        dry_run: Если True, только показывает что будет сделано
        
    Returns:
        Словарь с результатами синхронизации
    """
    session_factory = _ensure_local_session_factory(db_path)
    rm_cfg = _get_redmine_base_config()

    summary: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "total": 0,
        "skipped_excluded": 0,
        "skipped_deleted": 0,
        "skipped_archived": 0,
        "skipped_no_column": 0,
        "to_create": 0,
        "created": 0,
        "to_update": 0,
        "updated": 0,
        "errors": 0,
        "error_details": [],
        "items": [],
    }

    excluded_project_ids = _load_excluded_project_ids()

    # Загружаем задачи с их связями и существующие маппинги task_id -> redmine_issue_id
    async with session_factory() as session:
        result = await session.execute(
            select(LocalTask)
            .options(
                selectinload(LocalTask.column).selectinload(LocalColumn.board).selectinload(LocalBoard.project),
                selectinload(LocalTask.assignees),
            )
        )
        tasks: List[LocalTask] = result.scalars().all()

        links_result = await session.execute(select(LocalTaskIssueLink))
        links: Dict[str, int] = {l.task_id: l.redmine_issue_id for l in links_result.scalars().all()}

    summary["total"] = len(tasks)

    headers = {"X-Redmine-API-Key": rm_cfg["api_key"]}
    async with httpx.AsyncClient(
        base_url=rm_cfg["url"],
        headers=headers,
        verify=rm_cfg["verify"],
        timeout=30.0,
    ) as client:
        try:
            projects_by_identifier = await _fetch_redmine_projects_by_identifier(client)
            users_by_email = await _fetch_redmine_users_by_email(client)
            status_map = await _fetch_redmine_statuses(client)
            tracker_map = await _fetch_redmine_trackers(client)
        except Exception as exc:
            logger.exception("Failed to prefetch Redmine data")
            summary["success"] = False
            summary["errors"] += 1
            summary["error_details"].append(
                {
                    "task_id": None,
                    "action": "error_prefetch",
                    "error": str(exc),
                }
            )
            return summary

        # Выбираем дефолтный трекер (обычно "Задача" или первый доступный)
        default_tracker_id = (
            tracker_map.get("Задача")
            or tracker_map.get("Task")
            or next(iter(tracker_map.values()), 1)
        )

        # Отдельная сессия БД для обновления task_issue_links при apply
        async with session_factory() as link_session:
            from sqlalchemy.ext.asyncio import AsyncSession  # noqa: WPS433

            assert isinstance(link_session, AsyncSession)

            async def _upsert_link(task_id: str, issue_id: int) -> None:
                """Создать или обновить связь задачи с issue в Redmine."""

                if dry_run or not task_id or not issue_id:
                    return
                obj = await link_session.get(LocalTaskIssueLink, task_id)
                if obj is None:
                    link_session.add(LocalTaskIssueLink(task_id=task_id, redmine_issue_id=issue_id))
                else:
                    obj.redmine_issue_id = issue_id

            for task in tasks:
                task_id = task.id

                # Пропускаем удалённые и архивированные задачи
                if task.deleted:
                    summary["skipped_deleted"] += 1
                    summary["items"].append({"task_id": task_id, "action": "skip_deleted"})
                    continue

                if task.archived:
                    summary["skipped_archived"] += 1
                    summary["items"].append({"task_id": task_id, "action": "skip_archived"})
                    continue

                # Получаем колонку и доску
                column = task.column
                if not column:
                    summary["skipped_no_column"] += 1
                    summary["items"].append({"task_id": task_id, "action": "skip_no_column"})
                    continue

                board = column.board
                if not board:
                    summary["skipped_no_column"] += 1
                    summary["items"].append({"task_id": task_id, "action": "skip_no_board"})
                    continue

                project = board.project
                if not project:
                    summary["skipped_no_column"] += 1
                    summary["items"].append({"task_id": task_id, "action": "skip_no_project"})
                    continue

                # Проверяем, не исключён ли проект
                if project.id in excluded_project_ids:
                    summary["skipped_excluded"] += 1
                    summary["items"].append(
                        {
                            "task_id": task_id,
                            "project_id": project.id,
                            "action": "skip_excluded_project",
                        }
                    )
                    continue

                # Определяем Redmine-проект (доска = подпроект)
                board_identifier = _build_board_identifier(board.id).lower()
                redmine_project = projects_by_identifier.get(board_identifier)

                if not redmine_project:
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "task_id": task_id,
                            "board_id": board.id,
                            "action": "missing_redmine_project",
                            "error": f"Redmine project for board {board.title} not found. Sync boards first.",
                        }
                    )
                    continue

                redmine_project_id = redmine_project.get("id")

                if not redmine_project_id:
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "task_id": task_id,
                            "board_id": board.id,
                            "action": "missing_redmine_project_id",
                            "error": f"Redmine project ID is None for board {board.title}",
                        }
                    )
                    continue

                # Маппинг колонки в статус
                column_name = column.title
                status_name = get_redmine_status(column_name)
                status_id = status_map.get(status_name)

                if not status_id:
                    logger.warning("Status '%s' not found in Redmine, using default", status_name)
                    default_status_name = DEFAULT_STATUS
                    status_id = status_map.get(default_status_name, next(iter(status_map.values()), 1))

                if not status_id:
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "task_id": task_id,
                            "column": column_name,
                            "action": "missing_status_id",
                            "error": f"Status '{status_name}' not found in Redmine",
                        }
                    )
                    continue

                if not default_tracker_id:
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "task_id": task_id,
                            "action": "missing_tracker_id",
                            "error": "No trackers found in Redmine",
                        }
                    )
                    continue

                # Формируем payload для issue (используется и при создании, и при обновлении)
                issue_payload: Dict[str, Any] = {
                    "project_id": redmine_project_id,
                    "tracker_id": default_tracker_id,
                    "status_id": status_id,
                    "subject": task.title or f"Task {task_id}",
                    "description": task.description or "",
                }

                # Подробный лог того, что мы собираемся отправить в Redmine
                logger.info(
                    "Prepared issue payload for task %s: project_id=%s, tracker_id=%s, status_id=%s, "
                    "status_name=%s, column=%s",
                    task_id,
                    redmine_project_id,
                    default_tracker_id,
                    status_id,
                    status_name,
                    column_name,
                )

                # Добавляем assignees
                if task.assignees:
                    assignee_ids: List[int] = []
                    for assignee in task.assignees:
                        email = (assignee.email or "").strip().lower()
                        if email and email in users_by_email:
                            assignee_ids.append(users_by_email[email].get("id"))

                    if assignee_ids:
                        # Redmine поддерживает только одного assigned_to
                        issue_payload["assigned_to_id"] = assignee_ids[0]

                existing_issue_id = links.get(task_id)

                # Решаем, обновлять существующий issue или создавать новый
                if existing_issue_id:
                    summary["to_update"] += 1

                    if dry_run:
                        summary["items"].append(
                            {
                                "task_id": task_id,
                                "title": task.title,
                                "column": column_name,
                                "status": status_name,
                                "redmine_project_id": redmine_project_id,
                                "redmine_issue_id": existing_issue_id,
                                "action": "would_update",
                            }
                        )
                        continue

                    update_payload: Dict[str, Any] = {"issue": dict(issue_payload)}
                    # project_id на уровне issue при update не обязателен
                    update_payload["issue"].pop("project_id", None)

                    try:
                        resp = await client.put(
                            f"/issues/{existing_issue_id}.json", json=update_payload
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Failed to update Redmine issue for task %s", task_id)
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "task_id": task_id,
                                "redmine_issue_id": existing_issue_id,
                                "action": "error_update",
                                "error": str(exc),
                            }
                        )
                        continue

                    if resp.status_code >= 400:
                        logger.error(
                            "Failed to update Redmine issue for task %s: HTTP %s %s; payload=%r",
                            task_id,
                            resp.status_code,
                            resp.text,
                            update_payload,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "task_id": task_id,
                                "redmine_issue_id": existing_issue_id,
                                "action": "error_update_http",
                                "status_code": resp.status_code,
                                "body": resp.text,
                            }
                        )
                        continue

                    body_upd = resp.json() if resp.content else {}
                    updated_issue = body_upd.get("issue") or {}
                    new_id = updated_issue.get("id") or existing_issue_id
                    await _upsert_link(task_id, int(new_id))

                    summary["updated"] += 1
                    summary["items"].append(
                        {
                            "task_id": task_id,
                            "title": task.title,
                            "column": column_name,
                            "status": status_name,
                            "action": "updated",
                            "redmine_issue_id": new_id,
                        }
                    )
                    continue

                # Если сюда дошли, линка не было — создаём новый issue
                summary["to_create"] += 1

                if dry_run:
                    summary["items"].append(
                        {
                            "task_id": task_id,
                            "title": task.title,
                            "column": column_name,
                            "status": status_name,
                            "redmine_project_id": redmine_project_id,
                            "action": "would_create",
                        }
                    )
                    continue

                # Создаём issue в Redmine
                request_payload: Dict[str, Any] = {
                    "project_id": redmine_project_id,
                    "issue": issue_payload,
                }

                try:
                    resp = await client.post("/issues.json", json=request_payload)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to create Redmine issue for task %s", task_id)
                    summary["success"] = False
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "task_id": task_id,
                            "action": "error_create",
                            "error": str(exc),
                        }
                    )
                    continue

                if resp.status_code >= 400:
                    # Специальный случай: исполнитель невалиден для проекта.
                    # Пытаемся создать задачу повторно без assigned_to_id.
                    if (
                        resp.status_code == 422
                        and "Assignee is invalid" in (resp.text or "")
                        and issue_payload.get("assigned_to_id") is not None
                    ):
                        invalid_assignee = issue_payload.get("assigned_to_id")
                        logger.warning(
                            "Redmine returned 'Assignee is invalid' for task %s (project_id=%s, assignee_id=%s). "
                            "Retrying without assignee...",
                            task_id,
                            redmine_project_id,
                            invalid_assignee,
                        )

                        issue_payload_no_assignee = dict(issue_payload)
                        issue_payload_no_assignee.pop("assigned_to_id", None)
                        retry_payload: Dict[str, Any] = {
                            "project_id": redmine_project_id,
                            "issue": issue_payload_no_assignee,
                        }

                        try:
                            resp_retry = await client.post("/issues.json", json=retry_payload)
                        except Exception as exc:  # noqa: BLE001
                            logger.exception(
                                "Failed to create Redmine issue for task %s after removing assignee",
                                task_id,
                            )
                            summary["success"] = False
                            summary["errors"] += 1
                            summary["error_details"].append(
                                {
                                    "task_id": task_id,
                                    "action": "error_create_no_assignee",
                                    "error": str(exc),
                                    "project_id": redmine_project_id,
                                    "tracker_id": default_tracker_id,
                                    "status_id": status_id,
                                    "status_name": status_name,
                                    "column": column_name,
                                    "invalid_assignee_id": invalid_assignee,
                                }
                            )
                            continue

                        if resp_retry.status_code < 400:
                            body_retry = resp_retry.json() if resp_retry.content else {}
                            created_issue_retry = body_retry.get("issue") or {}
                            new_issue_id = created_issue_retry.get("id")

                            await _upsert_link(task_id, int(new_issue_id or 0))

                            summary["created"] += 1
                            summary["items"].append(
                                {
                                    "task_id": task_id,
                                    "title": task.title,
                                    "column": column_name,
                                    "status": status_name,
                                    "action": "created_no_assignee",
                                    "redmine_issue_id": new_issue_id,
                                    "invalid_assignee_id": invalid_assignee,
                                }
                            )
                            continue

                        # Если даже без исполнителя не получилось — логируем как обычную HTTP-ошибку
                        logger.error(
                            "Failed to create Redmine issue for task %s after removing assignee: HTTP %s %s; payload=%r",
                            task_id,
                            resp_retry.status_code,
                            resp_retry.text,
                            retry_payload,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "task_id": task_id,
                                "action": "error_create_http_no_assignee",
                                "status_code": resp_retry.status_code,
                                "body": resp_retry.text,
                                "project_id": redmine_project_id,
                                "tracker_id": default_tracker_id,
                                "status_id": status_id,
                                "status_name": status_name,
                                "column": column_name,
                                "invalid_assignee_id": invalid_assignee,
                            }
                        )
                        continue

                    # Общий случай HTTP-ошибки
                    logger.error(
                        "Failed to create Redmine issue for task %s: HTTP %s %s; payload=%r",
                        task_id,
                        resp.status_code,
                        resp.text,
                        request_payload,
                    )
                    summary["success"] = False
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "task_id": task_id,
                            "action": "error_create_http",
                            "status_code": resp.status_code,
                            "body": resp.text,
                            "project_id": redmine_project_id,
                            "tracker_id": default_tracker_id,
                            "status_id": status_id,
                            "status_name": status_name,
                            "column": column_name,
                        }
                    )
                    continue

                body = resp.json() if resp.content else {}
                created_issue = body.get("issue") or {}
                new_issue_id = created_issue.get("id")

                await _upsert_link(task_id, int(new_issue_id or 0))

                summary["created"] += 1
                summary["items"].append(
                    {
                        "task_id": task_id,
                        "title": task.title,
                        "column": column_name,
                        "status": status_name,
                        "action": "created",
                        "redmine_issue_id": new_issue_id,
                    }
                )

            if not dry_run:
                await link_session.commit()

    return summary
