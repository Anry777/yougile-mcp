from __future__ import annotations

import logging
import os
import secrets
import string
from typing import Any, Dict, List, Optional, Set

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.config import settings
from src.localdb.session import init_engine
from src.localdb.models import (
    User as LocalUser,
    Project as LocalProject,
    Board as LocalBoard,
    Task as LocalTask,
    Column as LocalColumn,
)


logger = logging.getLogger(__name__)

# Базовые (дефолтные) имена ролей Redmine для маппинга users.role → Redmine roles.
_DEFAULT_ADMIN_ROLE_NAME = "Manager"
_DEFAULT_USER_ROLE_NAME = "Reporter"


def _get_admin_role_name() -> str:
    """Имя роли для админов (YouGile role=admin) в Redmine.

    Приоритет источников:
      1) переменные окружения REDMINE_ADMIN_ROLE_NAME
      2) settings.redmine_admin_role_name (из .env через pydantic Settings)
      3) дефолт "Manager".
    """

    return (
        os.environ.get("REDMINE_ADMIN_ROLE_NAME")
        or getattr(settings, "redmine_admin_role_name", None)
        or _DEFAULT_ADMIN_ROLE_NAME
    )


def _get_user_role_name() -> str:
    """Имя роли для обычных пользователей (YouGile role=user) в Redmine."""

    return (
        os.environ.get("REDMINE_USER_ROLE_NAME")
        or getattr(settings, "redmine_user_role_name", None)
        or _DEFAULT_USER_ROLE_NAME
    )


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

            # Целевая форма полей в Redmine
            desired_login = email
            login_base = email.split("@", 1)[0] or u.id
            raw_name = (u.name or "").strip()
            desired_firstname, desired_lastname = _split_name(raw_name)
            if not desired_firstname or desired_firstname == email:
                desired_firstname = login_base
            # Фамилия в Redmine обязательна, поэтому если нормальной нет,
            # используем login_base (часть email до '@'), а не пустую строку
            if not desired_lastname or desired_lastname == email or desired_lastname == u.id:
                desired_lastname = login_base
            # Язык по умолчанию – русский
            desired_language = "ru"

            if rm_user:
                current_login = (rm_user.get("login") or "").strip()
                current_firstname = (rm_user.get("firstname") or "").strip()
                current_lastname = (rm_user.get("lastname") or "").strip()
                current_mail = (rm_user.get("mail") or "").strip()
                current_language = (rm_user.get("language") or "").strip()

                needs_update = (
                    current_login != desired_login
                    or current_mail != email
                    or current_firstname != desired_firstname
                    or current_lastname != desired_lastname
                    or current_language != desired_language
                )

                if not needs_update:
                    summary["existing"] += 1
                    summary["items"].append(
                        {
                            "yougile_user_id": u.id,
                            "email": email,
                            "action": "exists",
                            "redmine_user_id": rm_user.get("id"),
                            "redmine_login": current_login,
                        }
                    )
                    continue

                # Пользователь есть, но поля отличаются — обновляем
                update_payload = {
                    "login": desired_login,
                    "firstname": desired_firstname,
                    "lastname": desired_lastname,
                    "mail": email,
                    "language": desired_language,
                }

                if dry_run:
                    summary["items"].append(
                        {
                            "yougile_user_id": u.id,
                            "email": email,
                            "action": "would_update",
                            "redmine_user_id": rm_user.get("id"),
                            "current": {
                                "login": current_login,
                                "firstname": current_firstname,
                                "lastname": current_lastname,
                                "mail": current_mail,
                                "language": current_language,
                            },
                            "desired": update_payload,
                        }
                    )
                    summary["existing"] += 1
                    continue

                try:
                    resp = await client.put(
                        f"/users/{rm_user.get('id')}.json",
                        json={"user": update_payload},
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Failed to update Redmine user for %s", email)
                    summary["success"] = False
                    summary["errors"] += 1
                    summary["error_details"].append(
                        {
                            "yougile_user_id": u.id,
                            "email": email,
                            "action": "error_update",
                            "error": str(exc),
                        }
                    )
                    continue

                if resp.status_code >= 400:
                    logger.error(
                        "Failed to update Redmine user for %s: HTTP %s %s",
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
                            "action": "error_update_http",
                            "status_code": resp.status_code,
                            "body": resp.text,
                        }
                    )
                    continue

                # Успешно обновили существующего пользователя
                body_upd = resp.json() if resp.content else {}
                updated_user = body_upd.get("user") or rm_user
                existing_by_email[email_norm] = updated_user

                summary["existing"] += 1
                summary["items"].append(
                    {
                        "yougile_user_id": u.id,
                        "email": email,
                        "action": "updated",
                        "redmine_user_id": updated_user.get("id"),
                        "redmine_login": updated_user.get("login"),
                    }
                )
                continue

            # Not found in Redmine — создаём
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

            password = rm_cfg["default_password"]
            payload = {
                "login": desired_login,
                "firstname": desired_firstname,
                "lastname": desired_lastname,
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

                rm_subproject_id = rm_subproject.get("id")
                summary["items"].append(
                    {
                        "yougile_board_id": yg_board_id,
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "exists",
                        "redmine_project_id": rm_subproject_id,
                        "redmine_name": rm_subproject.get("name"),
                    }
                )

                # Обеспечиваем наследование участников от родительского проекта,
                # чтобы в подпроектах автоматически были те же участники, что и
                # в головном проекте.
                if dry_run:
                    summary["items"].append(
                        {
                            "yougile_board_id": yg_board_id,
                            "yougile_project_id": yg_project_id,
                            "identifier": identifier,
                            "action": "would_enable_inherit_members",
                            "redmine_project_id": rm_subproject_id,
                        }
                    )
                else:
                    try:
                        resp_inherit = await client.put(
                            f"/projects/{rm_subproject_id}.json",
                            json={"project": {"inherit_members": True}},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Failed to enable inherit_members for subproject %s",
                            rm_subproject_id,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "yougile_board_id": yg_board_id,
                                "yougile_project_id": yg_project_id,
                                "identifier": identifier,
                                "redmine_project_id": rm_subproject_id,
                                "action": "error_enable_inherit_members",
                                "error": str(exc),
                            }
                        )
                    else:
                        if resp_inherit.status_code >= 400:
                            logger.error(
                                "Failed to enable inherit_members for subproject %s: HTTP %s %s",
                                rm_subproject_id,
                                resp_inherit.status_code,
                                resp_inherit.text,
                            )
                            summary["success"] = False
                            summary["errors"] += 1
                            summary["error_details"].append(
                                {
                                    "yougile_board_id": yg_board_id,
                                    "yougile_project_id": yg_project_id,
                                    "identifier": identifier,
                                    "redmine_project_id": rm_subproject_id,
                                    "action": "error_enable_inherit_members_http",
                                    "status_code": resp_inherit.status_code,
                                    "body": resp_inherit.text,
                                }
                            )
                        else:
                            summary["items"].append(
                                {
                                    "yougile_board_id": yg_board_id,
                                    "yougile_project_id": yg_project_id,
                                    "identifier": identifier,
                                    "action": "enabled_inherit_members",
                                    "redmine_project_id": rm_subproject_id,
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
                # Включаем наследование участников сразу при создании подпроекта,
                # чтобы в нём были те же участники, что и в головном проекте.
                "inherit_members": True,
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


async def _fetch_redmine_roles(client: httpx.AsyncClient) -> Dict[str, int]:
    """Получить роли Redmine и построить индекс name -> id."""

    try:
        resp = await client.get("/roles.json")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to fetch Redmine roles: {exc}") from exc

    if resp.status_code >= 400:
        raise RuntimeError(
            f"Failed to fetch Redmine roles: HTTP {resp.status_code} {resp.text}",
        )

    data = resp.json()
    roles = data.get("roles") or []

    role_map: Dict[str, int] = {}
    for r in roles:
        name = (r.get("name") or "").strip()
        role_id = r.get("id")
        if name and role_id:
            role_map[name] = role_id

    return role_map


async def _fetch_redmine_memberships_for_project(
    client: httpx.AsyncClient,
    project_id: int,
) -> List[Dict[str, Any]]:
    """Получить membership'ы Redmine-проекта с учётом пагинации."""

    memberships: List[Dict[str, Any]] = []
    limit = 100
    offset = 0

    while True:
        try:
            resp = await client.get(
                f"/projects/{project_id}/memberships.json",
                params={"limit": limit, "offset": offset},
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Failed to fetch memberships for project {project_id}: {exc}",
            ) from exc

        if resp.status_code >= 400:
            raise RuntimeError(
                "Failed to fetch memberships for project "
                f"{project_id}: HTTP {resp.status_code} {resp.text}",
            )

        data = resp.json()
        items = data.get("memberships") or []
        total_count = int(data.get("total_count", len(items)))

        memberships.extend(items)

        if not items or offset + limit >= total_count:
            break
        offset += limit

    return memberships


async def sync_memberships(db_path: str | None = None, dry_run: bool = True) -> Dict[str, Any]:
    """Синхронизация membership пользователей в проектах Redmine.

    Логика:
    - Для каждого проекта YouGile, который не исключён, ищем соответствующий проект Redmine
      по identifier (yg-<yougile_project_id>).
    - Собираем множество пользователей проекта:
        * все company-admin'ы (User.role == "admin") → роль ADMIN_ROLE_NAME (Manager)
        * все пользователи, назначенные исполнителями задач в этом проекте → роль
          USER_ROLE_NAME (Reporter), если не admin.
    - В Redmine для каждого такого пользователя создаём membership или добавляем недостающую
      роль к существующему membership'у, не удаляя уже имеющиеся роли.
    """

    session_factory = _ensure_local_session_factory(db_path)
    rm_cfg = _get_redmine_base_config()

    summary: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "projects": 0,
        "projects_skipped_excluded": 0,
        "total": 0,
        "existing": 0,
        "to_create": 0,
        "created": 0,
        "to_update": 0,
        "updated": 0,
        "skipped_no_email": 0,
        "skipped_no_redmine_user": 0,
        "errors": 0,
        "error_details": [],
        "items": [],
    }

    excluded_project_ids = _load_excluded_project_ids()

    # Готовим данные из локальной БД: проекты, пользователи и связи проект → пользователи задач
    async with session_factory() as session:
        # Все проекты
        result_projects = await session.execute(select(LocalProject))
        projects: List[LocalProject] = result_projects.scalars().all()

        # Авто-исключаем проекты, помеченные как удалённые ("___deleted" в title)
        deleted_proj_result = await session.execute(
            select(LocalProject.id).where(LocalProject.title.contains("___deleted"))
        )
        deleted_project_ids = {row[0] for row in deleted_proj_result}
        if deleted_project_ids:
            excluded_project_ids |= deleted_project_ids

        # Все пользователи
        result_users = await session.execute(select(LocalUser))
        users: List[LocalUser] = result_users.scalars().all()
        users_by_id: Dict[str, LocalUser] = {u.id: u for u in users}

        admin_user_ids: Set[str] = {
            u.id
            for u in users
            if ((u.role or "").strip().lower() == "admin")
        }

        # Связи проект → пользователи, назначенные исполнителями задач.
        # Берём всех, кто когда-либо был назначен на задачи проекта (без фильтрации
        # по deleted/archived), чтобы роли в Redmine отражали полный состав
        # участников проекта.
        result_pairs = await session.execute(
            select(LocalProject.id, LocalUser.id)
            .join(LocalBoard, LocalBoard.project_id == LocalProject.id)
            .join(LocalColumn, LocalColumn.board_id == LocalBoard.id)
            .join(LocalTask, LocalTask.column_id == LocalColumn.id)
            .join(LocalTask.assignees)
            .distinct()
        )

        project_user_ids: Dict[str, Set[str]] = {}
        for proj_id, user_id in result_pairs:
            if not proj_id or not user_id:
                continue
            project_user_ids.setdefault(proj_id, set()).add(user_id)

    summary["projects"] = len(projects)

    headers = {"X-Redmine-API-Key": rm_cfg["api_key"]}
    async with httpx.AsyncClient(
        base_url=rm_cfg["url"],
        headers=headers,
        verify=rm_cfg["verify"],
    ) as client:
        # Предзагрузка данных из Redmine: проекты, пользователи, роли
        try:
            projects_by_identifier = await _fetch_redmine_projects_by_identifier(client)
            users_by_email = await _fetch_redmine_users_by_email(client)
            role_map = await _fetch_redmine_roles(client)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to prefetch Redmine data for memberships")
            summary["success"] = False
            summary["errors"] += 1
            summary["error_details"].append(
                {
                    "yougile_project_id": None,
                    "action": "error_prefetch",
                    "error": str(exc),
                }
            )
            return summary

        admin_role_name = _get_admin_role_name()
        user_role_name = _get_user_role_name()

        admin_role_id = role_map.get(admin_role_name)
        user_role_id = role_map.get(user_role_name)

        if not admin_role_id or not user_role_id:
            missing: List[str] = []
            if not admin_role_id:
                missing.append(admin_role_name)
            if not user_role_id:
                missing.append(user_role_name)
            msg = f"Required Redmine roles not found: {', '.join(missing)}"
            logger.error(msg)
            summary["success"] = False
            summary["errors"] += 1
            summary["error_details"].append(
                {
                    "yougile_project_id": None,
                    "action": "missing_roles",
                    "error": msg,
                }
            )
            return summary

        # Для каждого проекта YouGile настраиваем membership в соответствующем проекте Redmine
        for project in projects:
            yg_project_id = getattr(project, "id", None)
            if not yg_project_id:
                continue

            if yg_project_id in excluded_project_ids:
                summary["projects_skipped_excluded"] += 1
                summary["items"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "action": "skip_excluded",
                    }
                )
                continue

            identifier = _build_project_identifier(yg_project_id)
            rm_project = projects_by_identifier.get(identifier.lower())
            if not rm_project:
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "missing_redmine_project",
                        "error": "Redmine project not found. Sync projects first.",
                    }
                )
                continue

            rm_project_id = rm_project.get("id")
            if not rm_project_id:
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "identifier": identifier,
                        "action": "missing_redmine_project_id",
                        "error": "Redmine project ID is None",
                    }
                )
                continue

            # Желаемые пользователи проекта: все админы + все исполнители задач проекта
            desired_user_ids: Set[str] = set(project_user_ids.get(yg_project_id, set()))
            desired_user_ids |= admin_user_ids

            if not desired_user_ids:
                continue

            # Текущие membership'ы проекта
            try:
                memberships = await _fetch_redmine_memberships_for_project(client, rm_project_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Failed to fetch memberships for Redmine project %s", rm_project_id,
                )
                summary["success"] = False
                summary["errors"] += 1
                summary["error_details"].append(
                    {
                        "yougile_project_id": yg_project_id,
                        "redmine_project_id": rm_project_id,
                        "action": "error_fetch_memberships",
                        "error": str(exc),
                    }
                )
                continue

            existing_memberships_by_user_id: Dict[int, Dict[str, Any]] = {}
            for m in memberships:
                user_info = m.get("user") or {}
                uid = user_info.get("id")
                if uid:
                    existing_memberships_by_user_id[uid] = m

            # Обрабатываем каждого пользователя
            for user_id in desired_user_ids:
                local_user = users_by_id.get(user_id)
                if not local_user:
                    continue

                email = (local_user.email or "").strip().lower()
                if not email:
                    summary["skipped_no_email"] += 1
                    summary["items"].append(
                        {
                            "yougile_project_id": yg_project_id,
                            "yougile_user_id": user_id,
                            "action": "skip_no_email",
                        }
                    )
                    continue

                rm_user = users_by_email.get(email)
                if not rm_user:
                    summary["skipped_no_redmine_user"] += 1
                    summary["items"].append(
                        {
                            "yougile_project_id": yg_project_id,
                            "yougile_user_id": user_id,
                            "email": email,
                            "action": "skip_no_redmine_user",
                        }
                    )
                    continue

                role_name = (local_user.role or "").strip().lower()
                desired_role_id = admin_role_id if role_name == "admin" else user_role_id
                rm_user_id = rm_user.get("id")
                if not rm_user_id or not desired_role_id:
                    continue

                summary["total"] += 1

                membership = existing_memberships_by_user_id.get(rm_user_id)
                if membership:
                    existing_role_ids = {
                        r.get("id")
                        for r in (membership.get("roles") or [])
                        if r.get("id") is not None
                    }

                    if desired_role_id in existing_role_ids:
                        summary["existing"] += 1
                        summary["items"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "action": "exists",
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                            }
                        )
                        continue

                    new_role_ids = sorted(existing_role_ids | {desired_role_id})

                    if dry_run:
                        summary["to_update"] += 1
                        summary["items"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "action": "would_update",
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                                "current_role_ids": sorted(existing_role_ids),
                                "desired_role_ids": new_role_ids,
                            }
                        )
                        continue

                    membership_id = membership.get("id")
                    if not membership_id:
                        continue

                    try:
                        resp = await client.put(
                            f"/memberships/{membership_id}.json",
                            json={"membership": {"role_ids": new_role_ids}},
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Failed to update membership for user %s in project %s",
                            rm_user_id,
                            rm_project_id,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                                "action": "error_update_membership",
                                "error": str(exc),
                            }
                        )
                        continue

                    if resp.status_code >= 400:
                        logger.error(
                            "Failed to update membership for user %s in project %s: HTTP %s %s",
                            rm_user_id,
                            rm_project_id,
                            resp.status_code,
                            resp.text,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                                "action": "error_update_membership_http",
                                "status_code": resp.status_code,
                                "body": resp.text,
                            }
                        )
                        continue

                    summary["updated"] += 1
                    summary["items"].append(
                        {
                            "yougile_project_id": yg_project_id,
                            "yougile_user_id": user_id,
                            "email": email,
                            "action": "updated",
                            "redmine_project_id": rm_project_id,
                            "redmine_user_id": rm_user_id,
                            "role_ids": new_role_ids,
                        }
                    )
                else:
                    # membership ещё нет — создаём
                    if dry_run:
                        summary["to_create"] += 1
                        summary["items"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "action": "would_create",
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                                "role_ids": [desired_role_id],
                            }
                        )
                        continue

                    try:
                        resp = await client.post(
                            f"/projects/{rm_project_id}/memberships.json",
                            json={
                                "membership": {
                                    "user_id": rm_user_id,
                                    "role_ids": [desired_role_id],
                                }
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception(
                            "Failed to create membership for user %s in project %s",
                            rm_user_id,
                            rm_project_id,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                                "action": "error_create_membership",
                                "error": str(exc),
                            }
                        )
                        continue

                    if resp.status_code >= 400:
                        logger.error(
                            "Failed to create membership for user %s in project %s: HTTP %s %s",
                            rm_user_id,
                            rm_project_id,
                            resp.status_code,
                            resp.text,
                        )
                        summary["success"] = False
                        summary["errors"] += 1
                        summary["error_details"].append(
                            {
                                "yougile_project_id": yg_project_id,
                                "yougile_user_id": user_id,
                                "email": email,
                                "redmine_project_id": rm_project_id,
                                "redmine_user_id": rm_user_id,
                                "action": "error_create_membership_http",
                                "status_code": resp.status_code,
                                "body": resp.text,
                            }
                        )
                        continue

                    body = resp.json() if resp.content else {}
                    created_membership = body.get("membership") or {}

                    summary["created"] += 1
                    summary["items"].append(
                        {
                            "yougile_project_id": yg_project_id,
                            "yougile_user_id": user_id,
                            "email": email,
                            "action": "created",
                            "redmine_project_id": rm_project_id,
                            "redmine_user_id": rm_user_id,
                            "membership_id": created_membership.get("id"),
                            "role_ids": [desired_role_id],
                        }
                    )

    return summary
