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

from src.localdb.models import Project, Board, Column  # noqa: E402


async def list_columns(db_url: str) -> None:
    """Print all columns from local YouGile DB with project and board titles."""
    engine = create_async_engine(db_url, echo=False, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(
            select(
                Project.title.label("project_title"),
                Board.title.label("board_title"),
                Column.id.label("column_id"),
                Column.title.label("column_title"),
            )
            .join(Board, Board.project_id == Project.id, isouter=True)
            .join(Column, Column.board_id == Board.id, isouter=True)
            .order_by(Project.title, Board.title, Column.title, Column.id)
        )
        rows = result.all()

    await engine.dispose()

    if not rows:
        print("No columns found in the database.")
        return

    print("Project | Board | Column ID | Column title")
    print("-" * 80)
    for project_title, board_title, column_id, column_title in rows:
        p = project_title or "<no project>"
        b = board_title or "<no board>"
        ctitle = column_title or "<no title>"
        print(f"{p} | {b} | {column_id} | {ctitle}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List all YouGile columns from local DB (project/board/column)",
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
    await list_columns(args.db_url)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
