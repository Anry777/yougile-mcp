from __future__ import annotations

"""Вывести роли Redmine через REST API.

Использует переменные окружения / .env:
  REDMINE_URL, REDMINE_API_KEY, (опционально) REDMINE_VERIFY_SSL.

Запуск из корня репозитория (в venv):

  python scripts/list_redmine_roles.py
"""

import asyncio
import json
import os
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_basic_env() -> None:
    """Простая загрузка .env (KEY=VALUE) в os.environ.

    Аналогично cli/__main__._load_basic_env и scripts/test_redmine_issue._load_basic_env,
    чтобы можно было запускать скрипт напрямую из репозитория.
    """

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and v and (k not in os.environ or not os.environ.get(k)):
                        os.environ[k] = v
    except Exception:
        # Не падаем, если .env не читается
        pass


def _get_env(name: str, required: bool = True) -> str | None:
    value = os.getenv(name) or os.getenv(name.lower())
    if required and not value:
        raise SystemExit(f"Environment variable {name} is not set")
    return value


async def main() -> None:
    _load_basic_env()

    base_url = _get_env("REDMINE_URL", required=True)
    api_key = _get_env("REDMINE_API_KEY", required=True)

    verify_env = os.getenv("REDMINE_VERIFY_SSL")
    verify: bool = True
    if verify_env is not None:
        verify = verify_env not in {"0", "false", "False"}

    headers = {"X-Redmine-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, verify=verify) as client:
        resp = await client.get("/roles.json")
        print(f"HTTP status: {resp.status_code}")

        try:
            data = resp.json()
        except Exception:
            print("Raw body:")
            print(resp.text)
            return

        roles = data.get("roles") or []
        print("Roles from Redmine (id | name):")
        for r in roles:
            rid = r.get("id")
            name = r.get("name")
            print(f"  {rid:3} | {name}")

        # На всякий случай покажем и сырые данные при --debug
        if os.getenv("LIST_REDMINE_ROLES_DEBUG") in {"1", "true", "True"}:
            print("\nRaw JSON:")
            print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
