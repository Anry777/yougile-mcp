from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx


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
        # 1) взять любую задачу из /tasks
        resp = await client.get("/api-v2/tasks", params={"limit": 1, "offset": 0}, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content") or []
        if not content:
            print("API /tasks вернуло пустой список", file=sys.stderr)
            sys.exit(1)

        first = content[0]
        task_id = first.get("id")
        if not task_id:
            print("Первая задача из /tasks не содержит поля id", file=sys.stderr)
            print(json.dumps(first, ensure_ascii=False, indent=2))
            sys.exit(1)

        print(f"# Взята задача id={task_id} из /tasks", file=sys.stderr)

        # 2) получить полный объект задачи по id
        resp2 = await client.get(f"/api-v2/tasks/{task_id}", headers=headers)
        resp2.raise_for_status()
        full_task = resp2.json()

        # Печатаем сырой JSON
        print(json.dumps(full_task, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
