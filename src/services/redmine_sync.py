from __future__ import annotations

import logging
import os
import secrets
import string
from typing import Any, Dict, List, Optional, Set

import httpx
from sqlalchemy import select

from src.config import settings
from src.localdb.session import init_engine
from src.localdb.models import User as LocalUser, Project as LocalProject, Board as LocalBoard


logger = logging.getLogger(__name__)


def _load_excluded_project_ids() -> Set[str]:
    """Загрузить список ID проектов YouGile, которые нужно игнорировать при sync в Redmine.

    Формат файла: один UUID проекта на строку. Пустые строки и строки, начинающиеся с '#', игнорируются.
    Путь к файлу настраивается через переменную окружения REDMINE_SYNC_EXCLUDE_PROJECTS,
    по умолчанию используется cli/redmine_sync_exclude_projects.txt относительно корня проекта.
    """

    # Определяем путь к файлу: env override -> значение из settings (если появится) -> дефолт
    path = os.environ.get("REDMINE_SYNC_EXCLUDE_PROJECTS")
    if not path:
        # Попытка построить путь относительно src/ (мы сейчас в src/services/)
        current_dir = os.path.dirname(os.path.dirname(__file__))  # src/
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
        # В случае ошибки чтения файла не блокируем sync, просто считаем, что исключений нет
        return excluded

    return excluded


def _ensure_local_session_factory(db_path: str | None = None):
    """Ensure async session factory for local YouGile DB is initialized.

    db_path трактуется как полный DB URL override; иначе берём settings.yougile_local_db_url,
    как в других сервисах (importer/stats).
    """
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
    """Получить базовые настройки подключения к Redmine (URL, API key, verify, default password)."""
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

    default_password = (
        getattr(settings, "redmine_default_password", None)
        or os.environ.get("REDMINE_DEFAULT_PASSWORD")
        or os.environ.get("redmine_default_password")
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
    if not default_password:
        raise RuntimeError(
            "Redmine default password is not configured. Set REDMINE_DEFAULT_PASSWORD in .env",
        )

    return {"url": url, "api_key": api_key, "verify": verify, "default_password": default_password}


def _split_name(name: Optional[str]) -> tuple[str, str]:
    """Грубый разбор имени на first/last name."""
    if not name:
        return "", ""
    parts = name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _build_project_identifier(project_id: str | None) -> str:
    """Построить стабильный identifier для Redmine-проекта по ID проекта YouGile.

    identifier в Redmine должен быть уникальным и состоять из латинских букв/цифр/"-"/"_".
    Берём UUID проекта YouGile, нормализуем и добавляем префикс "yg-".
    """
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
    # Redmine ограничивает длину identifier 100 символами
    return identifier[:100]


def _build_board_identifier(board_id: str | None) -> str:
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


def _generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def _fetch_redmine_users_by_email(client: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
    """Выгрузить всех пользователей Redmine и построить индекс по email (в нижнем регистре)."""
    users_by_email: Dict[str, Dict[str, Any]] = {}
    limit = 100
    offset = 0

    while True:
        try:
            resp = await client.get("/users.json", params={"limit": limit, "offset": offset})
        except Exception as exc:  # noqa: BLE001
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


async def _fetch_redmine_projects_by_identifier(client: httpx.AsyncClient) -> Dict[str, Dict[str, Any]]:
    """Выгрузить все проекты Redmine и построить индекс по identifier (в нижнем регистре)."""
    projects_by_identifier: Dict[str, Dict[str, Any]] = {}
    limit = 100
    offset = 0

    while True:
        try:
            resp = await client.get("/projects.json", params={"limit": limit, "offset": offset})
        except Exception as exc:  # noqa: BLE001
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


async def sync_users(db_path: str | None = None, dry_run: bool = True) -> Dict[str, Any]:
    """Синхронизация пользователей из локальной БД YouGile в Redmine через REST API.

    Идемпотентно: для каждого пользователя ищется Redmine-пользователь по email; если
    найден, новый не создаётся.

    Возвращает сводку по результатам.
    """
    session_factory = _ensure_local_session_factory(db_path)
    rm_cfg = _get_redmine_base_config()

    summary: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "total": 0,
        "skipped_no_email": 0,
        "existing": 0,
        "to_create": 0,
        "created": 0,
        "errors": 0,
        "error_details": [],
        "items": [],
    }

    async with session_factory() as session:
        result = await session.execute(select(LocalUser))
        users: List[LocalUser] = result.scalars().all()

    summary["total"] = len(users)

    headers = {"X-Redmine-API-Key": rm_cfg["api_key"]}
    async with httpx.AsyncClient(base_url=rm_cfg["url"], headers=headers, verify=rm_cfg["verify"]) as client:
        try:
            existing_by_email = await _fetch_redmine_users_by_email(client)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to prefetch Redmine users")
            summary["success"] = False
            summary["errors"] += 1
            summary["error_details"].append(
                {
                    "yougile_user_id": None,
                    "email": None,
                    "action": "error_prefetch",
                    "error": str(exc),
                }
            )
            return summary

        for u in users:
            email = (u.email or "").strip()
            if not email:
                summary["skipped_no_email"] += 1
                summary["items"].append(
                    {
                        "yougile_user_id": u.id,
                        "email": None,
                        "action": "skip_no_email",
                    }
                )
                continue

            email_norm = email.lower()
            rm_user = existing_by_email.get(email_norm)

            if rm_user:
                summary["existing"] += 1
                summary["items"].append(
                    {
                        "yougile_user_id": u.id,
                        "email": email,
                        "action": "exists",
                        "redmine_user_id": rm_user.get("id"),
                        "redmine_login": rm_user.get("login"),
                    }
                )
                continue

            # Not found in Redmine
            summary["to_create"] += 1
            if dry_run:
                summary["items"].append(
                    {
                        "yougile_user_id": u.id,
                        "email": email,
                        "action": "would_create",
                    }
                )
                continue

            login_base = email.split("@", 1)[0] or u.id
            login = login_base

            firstname, lastname = _split_name(u.name or "")

            if not firstname:
                firstname = login_base
            if not lastname:
                lastname = u.id

            password = rm_cfg["default_password"]
            payload = {
                "login": login,
                "firstname": firstname,
                "lastname": lastname,
                "mail": email,
                "password": password,
                "must_change_passwd": True,
            }

            try:
                resp = await client.post("/users.json", json={"user": payload})
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to create Redmine user for %s", email)
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_user_id": u.id,
                        "email": email,
                        "action": "error_create",
                        "error": str(exc),
                    }
                )
                continue

            if resp.status_code >= 400:
                logger.error(
                    "Failed to create Redmine user for %s: HTTP %s %s",
                    email,
                    resp.status_code,
                    resp.text,
                )
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_user_id": u.id,
                        "email": email,
                        "action": "error_create_http",
                        "status_code": resp.status_code,
                        "body": resp.text,
                    }
                )
                continue

            body = resp.json() if resp.content else {}
            created_user = body.get("user") or {}
            existing_by_email[email_norm] = created_user

            summary["created"] += 1
            summary["items"].append(
                {
                    "yougile_user_id": u.id,
                    "email": email,
                    "action": "created",
                    "redmine_user_id": created_user.get("id"),
                    "redmine_login": created_user.get("login"),
                }
            )

    return summary


async def sync_projects(db_path: str | None = None, dry_run: bool = True) -> Dict[str, Any]:
    """Синхронизация проектов из локальной БД YouGile в Redmine через REST API.

    Идемпотентно: для каждого проекта строим стабильный identifier вида "yg-<yougile_id>".
    Если в Redmine уже есть проект с таким identifier, новый не создаём.
    """

    session_factory = _ensure_local_session_factory(db_path)
    rm_cfg = _get_redmine_base_config()

    summary: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "total": 0,
        "existing": 0,
        "to_create": 0,
        "created": 0,
        "skipped_excluded": 0,
        "errors": 0,
        "error_details": [],
        "items": [],
    }

    excluded_ids = _load_excluded_project_ids()

    async with session_factory() as session:
        result = await session.execute(select(LocalProject))
        projects: List[LocalProject] = result.scalars().all()

    summary["total"] = len(projects)

    headers = {"X-Redmine-API-Key": rm_cfg["api_key"]}
    async with httpx.AsyncClient(
        base_url=rm_cfg["url"],
        headers=headers,
        verify=rm_cfg["verify"],
    ) as client:
        try:
            existing_by_identifier = await _fetch_redmine_projects_by_identifier(client)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to prefetch Redmine projects")
            summary["success"] = False
            summary["errors"] += 1
            summary["error_details"].append(
                {
                    "yougile_project_id": None,
                    "identifier": None,
                    "action": "error_prefetch",
                    "error": str(exc),
                }
            )
            return summary

        for p in projects:
            yg_project_id = getattr(p, "id", None)
            title = getattr(p, "title", None) or ""

            if yg_project_id and (yg_project_id in excluded_ids or "___deleted" in title):
                summary["skipped_excluded"] += 1
                summary["items"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": None,
                        "title": title,
                        "action": "skip_excluded",
                    }
                )
                continue

            identifier = _build_project_identifier(yg_project_id)
            identifier_norm = identifier.lower()

            rm_project = existing_by_identifier.get(identifier_norm)
            if rm_project:
                summary["existing"] += 1
                summary["items"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "exists",
                        "redmine_project_id": rm_project.get("id"),
                        "redmine_name": rm_project.get("name"),
                    }
                )
                continue

            summary["to_create"] += 1
            if dry_run:
                summary["items"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "title": getattr(p, "title", None),
                        "action": "would_create",
                    }
                )
                continue

            name = getattr(p, "title", None) or (yg_project_id or identifier)
            description = getattr(p, "description", None) or ""

            payload = {
                "name": name,
                "identifier": identifier,
                "description": description,
                "is_public": False,
            }

            try:
                resp = await client.post("/projects.json", json={"project": payload})
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to create Redmine project for %s", yg_project_id)
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "error_create",
                        "error": str(exc),
                    }
                )
                continue

            if resp.status_code >= 400:
                logger.error(
                    "Failed to create Redmine project for %s: HTTP %s %s",
                    yg_project_id,
                    resp.status_code,
                    resp.text,
                )
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "error_create_http",
                        "status_code": resp.status_code,
                        "body": resp.text,
                    }
                )
                continue

            body = resp.json() if resp.content else {}
            created_project = body.get("project") or {}
            existing_by_identifier[identifier_norm] = created_project

            summary["created"] += 1
            summary["items"].append(
                {
                    "yougile_project_id": yg_project_id,
                    "identifier": identifier,
                    "action": "created",
                    "redmine_project_id": created_project.get("id"),
                    "redmine_name": created_project.get("name"),
                }
            )

    return summary


async def sync_boards(db_path: str | None = None, dry_run: bool = True) -> Dict[str, Any]:
    session_factory = _ensure_local_session_factory(db_path)
    rm_cfg = _get_redmine_base_config()

    summary: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "total": 0,
        "existing": 0,
        "to_create": 0,
        "created": 0,
        "skipped_excluded": 0,
        "errors": 0,
        "error_details": [],
        "items": [],
    }

    excluded_project_ids = _load_excluded_project_ids()

    async with session_factory() as session:
        result = await session.execute(select(LocalBoard))
        boards: List[LocalBoard] = result.scalars().all()

        # Авто-исключаем проекты, помеченные как удалённые ("___deleted" в title)
        deleted_proj_result = await session.execute(
            select(LocalProject.id).where(LocalProject.title.contains("___deleted"))
        )
        deleted_project_ids = {row[0] for row in deleted_proj_result}
        if deleted_project_ids:
            excluded_project_ids |= deleted_project_ids

    summary["total"] = len(boards)

    headers = {"X-Redmine-API-Key": rm_cfg["api_key"]}
    async with httpx.AsyncClient(
        base_url=rm_cfg["url"],
        headers=headers,
        verify=rm_cfg["verify"],
    ) as client:
        try:
            existing_by_identifier = await _fetch_redmine_projects_by_identifier(client)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to prefetch Redmine projects for boards")
            summary["success"] = False
            summary["errors"] += 1
            summary["error_details"].append(
                {
                    "yougile_board_id": None,
                    "identifier": None,
                    "action": "error_prefetch",
                    "error": str(exc),
                }
            )
            return summary

        for b in boards:
            yg_board_id = getattr(b, "id", None)
            yg_project_id = getattr(b, "project_id", None)

            if yg_project_id and yg_project_id in excluded_project_ids:
                summary["skipped_excluded"] += 1
                summary["items"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": None,
                        "action": "skip_excluded_project",
                    }
                )
                continue

            identifier = _build_board_identifier(yg_board_id)
            identifier_norm = identifier.lower()

            rm_subproject = existing_by_identifier.get(identifier_norm)
            if rm_subproject:
                summary["existing"] += 1
                summary["items"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "exists",
                        "redmine_project_id": rm_subproject.get("id"),
                        "redmine_name": rm_subproject.get("name"),
                    }
                )
                continue

            parent_identifier = _build_project_identifier(yg_project_id)
            parent = existing_by_identifier.get(parent_identifier.lower())
            if not parent:
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "missing_parent_project",
                        "error": "Parent Redmine project not found. Sync projects first.",
                    }
                )
                continue

            summary["to_create"] += 1
            if dry_run:
                summary["items"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "title": getattr(b, "title", None),
                        "parent_redmine_project_id": parent.get("id"),
                        "action": "would_create",
                    }
                )
                continue

            name = getattr(b, "title", None) or (yg_board_id or identifier)

            payload = {
                "name": name,
                "identifier": identifier,
                "parent_id": parent.get("id"),
                "is_public": False,
            }

            try:
                resp = await client.post("/projects.json", json={"project": payload})
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to create Redmine subproject for board %s", yg_board_id)
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "error_create",
                        "error": str(exc),
                    }
                )
                continue

            if resp.status_code >= 400:
                logger.error(
                    "Failed to create Redmine subproject for board %s: HTTP %s %s",
                    yg_board_id,
                    resp.status_code,
                    resp.text,
                )
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "error_create_http",
                        "status_code": resp.status_code,
                        "body": resp.text,
                    }
                )
                continue

            body = resp.json() if resp.content else {}
            created_project = body.get("project") or {}
            existing_by_identifier[identifier_norm] = created_project

            summary["created"] += 1
            summary["items"].append(
                {
                    "yougile_board_id": yg_board_id,
                    "yougile_project_id": yg_project_id,
                    "identifier": identifier,
                    "action": "created",
                    "redmine_project_id": created_project.get("id"),
                    "redmine_name": created_project.get("name"),
                }
            )

    return summary
