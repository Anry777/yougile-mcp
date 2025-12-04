from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import defaultdict

import httpx


_RATE_LIMIT_PER_MINUTE = 50
_LAST_REQUEST_TS: float = 0.0


async def _rate_limited_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict,
) -> httpx.Response:
    """Сделать GET с простым rate limiting и обработкой 429."""
    global _LAST_REQUEST_TS
    min_interval = 60.0 / max(1, _RATE_LIMIT_PER_MINUTE)

    for attempt in range(3):
        # межзапросный интервал
        now = time.monotonic()
        wait = (_LAST_REQUEST_TS + min_interval) - now
        if wait > 0:
            await asyncio.sleep(wait)

        resp = await client.get(url, params=params, headers=headers)
        if resp.status_code != 429:
            _LAST_REQUEST_TS = time.monotonic()
            resp.raise_for_status()
            return resp

        # 429 Too Many Requests – подождать и попробовать ещё раз
        retry_after = resp.headers.get("Retry-After")
        sleep_sec = 30.0
        if retry_after:
            try:
                sleep_sec = max(sleep_sec, float(retry_after))
            except ValueError:
                pass
        if attempt == 2:
            resp.raise_for_status()  # бросаем 429 наружу
        await asyncio.sleep(sleep_sec)

    return resp


async def _fetch_projects(client: httpx.AsyncClient, headers: dict) -> list[dict]:
    resp = await _rate_limited_get(client, "/api-v2/projects", params=None, headers=headers)
    data = resp.json()
    if isinstance(data, dict):
        return data.get("content") or []
    if isinstance(data, list):
        return data
    return []


async def _fetch_task_counts_by_project(
    client: httpx.AsyncClient,
    headers: dict,
    projects: list[dict],
) -> dict[str, int]:
    """Посчитать количество задач по проектам через /projects -> /boards -> /columns -> /tasks.

    Используем ту же логику привязки, что и импортер: проекты -> доски -> колонки -> задачи.
    Так мы считаем именно по реальным project.id, а не по полю idTaskProject.
    """
    counts: dict[str, int] = {}
    limit = 1000
    total_projects = len(projects)

    for idx, proj in enumerate(projects, 1):
        if not isinstance(proj, dict):
            continue
        project_id = proj.get("id")
        if not project_id:
            continue

        total = 0

        title = proj.get("title") or proj.get("name") or ""
        print(f"[{idx}/{total_projects}] Project {project_id} | {title} ...", flush=True)

        # Boards в проекте
        resp_boards = await _rate_limited_get(
            client,
            "/api-v2/boards",
            params={
                "projectId": project_id,
                "limit": 1000,
                "offset": 0,
                "includeDeleted": False,
            },
            headers=headers,
        )
        data_boards = resp_boards.json()
        boards = []
        if isinstance(data_boards, dict):
            boards = data_boards.get("content") or []
        elif isinstance(data_boards, list):
            boards = data_boards

        for b in boards:
            if not isinstance(b, dict):
                continue
            board_id = b.get("id")
            if not board_id:
                continue

            # Колонки на доске
            resp_cols = await _rate_limited_get(
                client,
                "/api-v2/columns",
                params={"boardId": board_id},
                headers=headers,
            )
            data_cols = resp_cols.json()
            if isinstance(data_cols, dict):
                cols = data_cols.get("content") or []
            else:
                cols = data_cols or []

            for c in cols:
                if not isinstance(c, dict):
                    continue
                col_id = c.get("id")
                if not col_id:
                    continue

                # Задачи в колонке (постранично)
                offset = 0
                while True:
                    resp_tasks = await _rate_limited_get(
                        client,
                        "/api-v2/tasks",
                        params={
                            "columnId": col_id,
                            "limit": limit,
                            "offset": offset,
                            "includeDeleted": False,
                        },
                        headers=headers,
                    )
                    data_tasks = resp_tasks.json()
                    if isinstance(data_tasks, dict):
                        content = data_tasks.get("content") or []
                    else:
                        content = data_tasks or []

                    if not content:
                        break

                    total += len(content)

                    if len(content) < limit:
                        break
                    offset += limit

        counts[str(project_id)] = total
        print(f"    tasks: {total}", flush=True)

    return counts


async def main() -> None:
    base_url = os.environ.get("YOUGILE_BASE_URL", "https://yougile.com")
    api_key = os.environ.get("YOUGILE_API_KEY") or os.environ.get("yougile_api_key")

    if not api_key:
        print("YOUGILE_API_KEY is not set in environment", file=sys.stderr)
        sys.exit(1)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        projects = await _fetch_projects(client, headers)
        counts = await _fetch_task_counts_by_project(client, headers, projects)

        projects_by_id: dict[str, dict] = {}
        for p in projects:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            if not pid:
                continue
            projects_by_id[str(pid)] = p

        # Печатаем агрегированную статистику по проектам
        print("# Projects with task counts (from YouGile API)\n")

        # Суммарное количество задач по API
        total_tasks = sum(counts.values())
        print(f"Total tasks (API): {total_tasks}")
        print(f"Projects:         {len(projects_by_id)}\n")

        # Сортировка по количеству задач по убыванию
        for pid in sorted(projects_by_id.keys(), key=lambda k: counts.get(k, 0), reverse=True):
            proj = projects_by_id[pid]
            title = proj.get("title") or proj.get("name") or ""
            cnt = counts.get(pid, 0)
            print(f"{cnt:6} | {pid} | {title}")

        # Проекты, которые есть в задачах, но нет в списке /projects
        orphan_ids = [pid for pid in counts.keys() if pid not in projects_by_id]
        if orphan_ids:
            print("\n# Tasks linked to unknown project IDs:")
            for pid in sorted(orphan_ids, key=lambda k: counts.get(k, 0), reverse=True):
                print(f"{counts.get(pid, 0):6} | {pid} | <unknown project>")


if __name__ == "__main__":
    asyncio.run(main())
