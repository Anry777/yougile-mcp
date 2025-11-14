from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text


class Base(DeclarativeBase):
    pass


async_engine: AsyncEngine | None = None
async_session: async_sessionmaker[AsyncSession] | None = None


def make_sqlite_url(db_path: str) -> str:
    # Use file path; for in-memory use: 'sqlite+aiosqlite://'
    if db_path.startswith("sqlite+"):
        return db_path
    if db_path.startswith("file:"):
        # already URI
        return f"sqlite+aiosqlite:///{db_path}"
    return f"sqlite+aiosqlite:///{db_path}"


def init_engine(db_url: str) -> AsyncEngine:
    global async_engine, async_session
    async_engine = create_async_engine(db_url, echo=False, future=True)
    async_session = async_sessionmaker(async_engine, expire_on_commit=False)
    return async_engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if async_session is None:
        raise RuntimeError("DB is not initialized. Call init_engine() first.")
    async with async_session() as session:
        yield session


async def init_db(create_sql: str | None = None) -> None:
    """Initialize database. If create_sql is provided, executes it for initial setup.
    Alembic is preferred for schema migrations, but this ensures first run works.
    """
    if async_engine is None:
        raise RuntimeError("Engine is not initialized. Call init_engine() first.")
    if create_sql:
        async with async_engine.begin() as conn:
            await conn.execute(text(create_sql))
