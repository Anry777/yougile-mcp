from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for webhook DB models."""


async_engine: AsyncEngine | None = None
async_session: async_sessionmaker[AsyncSession] | None = None


def init_engine(db_url: str) -> AsyncEngine:
    """Initialize async engine and session factory for webhook DB."""
    global async_engine, async_session
    async_engine = create_async_engine(db_url, echo=False, future=True)
    async_session = async_sessionmaker(async_engine, expire_on_commit=False)
    return async_engine


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if async_session is None:
        raise RuntimeError("Webhook DB is not initialized. Call init_engine() first.")
    async with async_session() as session:
        yield session
