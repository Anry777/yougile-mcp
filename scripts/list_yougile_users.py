from __future__ import annotations

"""Вывести пользователей YouGile из локальной БД с ролями.

Запуск (из корня репозитория, в venv):

  python scripts/list_yougile_users.py \
    --db "postgresql+asyncpg://yougile:yougile@10.1.2.124:55432/yougile"

Если не указывать --db, будет использован YOUGILE_LOCAL_DB_URL
из .env / переменных окружения.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.localdb.models import User  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List YouGile users with roles from local DB")
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


async def main_async(db_url: str) -> None:
    engine = create_async_engine(db_url, echo=False, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()

    await engine.dispose()

    print(f"Total users: {len(users)}")
    print(
        f"{'ID':36} | {'Email':30} | {'Name':25} | Role",
    )
    print("-" * 120)

    for u in users:
        uid = (u.id or "")[:36]
        email = (u.email or "")[:30]
        name = (u.name or "")[:25]
        role = u.role or ""
        print(f"{uid:36} | {email:30} | {name:25} | {role}")


def main() -> None:
    args = parse_args()
    asyncio.run(main_async(args.db_url))


if __name__ == "__main__":
    main()
