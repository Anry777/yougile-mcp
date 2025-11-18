from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy import select, delete

from src.api import stickers as api_stickers
from src.core import auth as core_auth
from src.core.client import YouGileClient
from src.config import settings
from src.localdb.session import init_engine, make_sqlite_url, async_engine, Base
from src.localdb.models import SprintSticker, SprintState


def _to_dt_ms(value: Any) -> datetime | None:
    """Convert millisecond / second epoch to aware datetime in UTC.

    API для спринтов, судя по дампу, отдаёт миллисекунды с 1970-01-01.
    Но на всякий случай поддерживаем и секунды.
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            # миллисекунды (больше 10^12) или просто большие числа
            if value > 10_000_000_000:
                return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except Exception:
        return None
    return None


async def sync_sprint_stickers(db_path: str = "./yougile_local.db") -> Dict[str, Any]:
    """Синхронизировать справочник sprint-stickers и их состояний в локальную БД.

    1. Определяет URL БД так же, как импортёр/статы.
    2. Создаёт таблицы при необходимости (через Base.metadata.create_all).
    3. Тянет /sprint-stickers из YouGile.
    4. Делает upsert sprint_stickers и sprint_states, очищая удалённые состояния.
    """

    # URL БД
    if db_path and db_path != "./yougile_local.db":
        db_url = make_sqlite_url(db_path)
    elif getattr(settings, "yougile_local_db_url", None):
        db_url = settings.yougile_local_db_url
    else:
        db_url = make_sqlite_url(db_path)

    init_engine(db_url)
    # гарантируем наличие таблиц (на случай запуска без alembic)
    assert async_engine is not None
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Тянем спринт-стикеры из API
    async with YouGileClient(core_auth.auth_manager) as client:
        resp = await api_stickers.get_sprint_stickers(client)

    # API иногда возвращает paging+content, иногда просто список
    if isinstance(resp, dict):
        items = resp.get("content") or []
    else:
        items = resp or []

    from src.localdb.session import async_session as session_factory

    if session_factory is None:
        raise RuntimeError("DB session factory is not initialized")

    total_stickers = 0
    total_states = 0

    async with session_factory() as session:
        async with session.begin():
            for s in items:
                if not isinstance(s, dict):
                    continue
                sid = s.get("id")
                if not sid:
                    continue
                name = s.get("name")
                deleted = s.get("deleted")

                sticker = await session.get(SprintSticker, sid)
                if sticker is None:
                    sticker = SprintSticker(id=sid, name=name or "", deleted=deleted)
                    session.add(sticker)
                else:
                    sticker.name = name or ""
                    sticker.deleted = deleted

                total_stickers += 1

                # States
                states = s.get("states") or []
                seen_state_ids: set[str] = set()
                for st in states:
                    if not isinstance(st, dict):
                        continue
                    st_id = st.get("id")
                    if not st_id:
                        continue
                    seen_state_ids.add(st_id)
                    st_name = st.get("name") or ""
                    begin = _to_dt_ms(st.get("begin"))
                    end = _to_dt_ms(st.get("end"))

                    state_obj = await session.get(SprintState, st_id)
                    if state_obj is None:
                        state_obj = SprintState(
                            id=st_id,
                            sticker_id=sid,
                            name=st_name,
                            begin=begin,
                            end=end,
                        )
                        session.add(state_obj)
                    else:
                        state_obj.sticker_id = sid
                        state_obj.name = st_name
                        state_obj.begin = begin
                        state_obj.end = end

                    total_states += 1

                # Удаляем локальные стейты, которых больше нет в API
                if seen_state_ids:
                    res = await session.execute(
                        select(SprintState.id).where(SprintState.sticker_id == sid)
                    )
                    local_ids = {row[0] for row in res.all()}
                    stale_ids = local_ids - seen_state_ids
                    if stale_ids:
                        await session.execute(
                            delete(SprintState).where(SprintState.id.in_(list(stale_ids)))
                        )

    return {
        "success": True,
        "db_url": db_url,
        "stickers": total_stickers,
        "states": total_states,
    }
