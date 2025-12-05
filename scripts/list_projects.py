from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Ensure project root and src/ are on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.localdb.models import Project  # noqa: E402


async def list_projects(db_url: str) -> None:
    """Print all projects from local YouGile DB."""
    engine = create_async_engine(db_url, echo=False, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(
            select(
                Project.id.label("project_id"),
                Project.title.label("project_title"),
            ).order_by(Project.title, Project.id)
        )
        rows = result.all()

    await engine.dispose()

    if not rows:
        print("No projects found in the database.")
        return

    print("Project ID | Project title")
    print("-" * 80)
    for project_id, project_title in rows:
        title = project_title or "<no title>"
        print(f"{project_id} | {title}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List all YouGile projects from local DB",
    )
    parser.add_argument(
        "--db",
        dest="db_url",
        default=os.environ.get(
            "YOUGILE_LOCAL_DB_URL",
            "postgresql+asyncpg://yougile:yougile@localhost:55432/yougile",
        ),
        help="Database URL (async SQLAlchemy format)",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    await list_projects(args.db_url)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
