from __future__ import annotations

"""Обновить колонку users.role в локальной БД по данным YouGile API (isAdmin).

Запуск (из корня репозитория, в venv):

  python scripts/update_yougile_user_roles_from_api.py \
    --db "postgresql+asyncpg://yougile:yougile@10.1.2.124:55432/yougile"

Если не указывать --db, будет использован YOUGILE_LOCAL_DB_URL
из .env / переменных окружения.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.localdb.models import User  # noqa: E402
from src.core import auth as core_auth  # noqa: E402
from src.core.client import YouGileClient  # noqa: E402
from src.config.settings import settings  # noqa: E402
from src.api import users as api_users  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update local DB users.role from YouGile API isAdmin flag",
    )
    parser.add_argument(
        "--db",
        dest="db_url",
        default=os.environ.get(
            "YOUGILE_LOCAL_DB_URL",
            "postgresql+asyncpg://yougile:yougile@10.1.2.124:55432/yougile",
        ),
        help="Database URL (async SQLAlchemy format)",
    )
    return parser.parse_args()


async def _init_auth() -> None:
    """Инициализировать auth_manager из настроек / переменных окружения."""

    api_key: Optional[str] = None
    company_id: Optional[str] = None

    if settings is not None:
        api_key = getattr(settings, "yougile_api_key", None)
        company_id = getattr(settings, "yougile_company_id", None)

    if not api_key:
        api_key = os.environ.get("YOUGILE_API_KEY") or os.environ.get("yougile_api_key")
    if not company_id:
        company_id = os.environ.get("YOUGILE_COMPANY_ID") or os.environ.get(
            "yougile_company_id"
        )

    if not api_key or not company_id:
        raise RuntimeError(
            "Missing YouGile credentials: set YOUGILE_API_KEY and YOUGILE_COMPANY_ID in .env/env",
        )

    core_auth.auth_manager.set_credentials(api_key, company_id)


def _role_from_is_admin(user: Dict[str, Any]) -> Optional[str]:
    """Сконвертировать isAdmin -> строковое значение роли."""

    is_admin_value = user.get("isAdmin")
    if isinstance(is_admin_value, bool):
        return "admin" if is_admin_value else "user"
    return None


async def update_roles_from_api(db_url: str) -> Dict[str, Any]:
    await _init_auth()

    # 1) Забираем всех пользователей из YouGile API
    async with YouGileClient(core_auth.auth_manager) as client:
        remote_users: List[Dict[str, Any]] = await api_users.get_users(client)

    # 2) Грузим пользователей из локальной БД
    engine = create_async_engine(db_url, echo=False, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    stats: Dict[str, Any] = {
        "total_api_users": len(remote_users),
        "matched_in_db": 0,
        "updated": 0,
        "unchanged": 0,
        "missing_in_db": 0,
    }

    async with session_factory() as session:
        async with session.begin():
            result = await session.execute(select(User))
            users_db: Dict[str, User] = {u.id: u for u in result.scalars().all()}

            for ru in remote_users:
                uid = ru.get("id")
                if not uid:
                    continue

                db_user = users_db.get(uid)
                if not db_user:
                    stats["missing_in_db"] += 1
                    continue

                stats["matched_in_db"] += 1

                new_role = _role_from_is_admin(ru)
                old_role = db_user.role

                # Нормализуем пустоту
                if old_role is not None and not str(old_role).strip():
                    old_role = None

                if new_role == old_role:
                    stats["unchanged"] += 1
                    continue

                db_user.role = new_role
                stats["updated"] += 1

    await engine.dispose()

    return stats


async def main_async(db_url: str) -> None:
    stats = await update_roles_from_api(db_url)
    print("YouGile user roles sync (from API to local DB):")
    print(f"  Total users from API:  {stats['total_api_users']}")
    print(f"  Matched in DB:        {stats['matched_in_db']}")
    print(f"  Updated roles:        {stats['updated']}")
    print(f"  Unchanged roles:      {stats['unchanged']}")
    print(f"  Missing in DB:        {stats['missing_in_db']}")


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args.db_url))


if __name__ == "__main__":
    main()
