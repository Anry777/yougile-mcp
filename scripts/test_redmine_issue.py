"""Простой скрипт для отладки создания одной задачи в Redmine.

Запуск из хоста (PowerShell), через docker-compose:

  docker compose run --rm --entrypoint python cli scripts/test_redmine_issue.py \
    --project-id 9 --tracker-id 1 --status-id 3 \
    --subject "Тестовая задача" --assigned-to 7

Скрипт использует переменные окружения REDMINE_URL и REDMINE_API_KEY
(как и основной код), а также REDMINE_VERIFY_SSL (опционально).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any, Dict

import httpx


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def _load_basic_env() -> None:
    """Простая загрузка .env (KEY=VALUE) в os.environ.

    Аналогично cli/__main__._load_basic_env, чтобы можно было
    запускать скрипт напрямую из репозитория.
    """

    env_path = os.path.join(PROJECT_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
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


async def _fetch_basic_info(client: httpx.AsyncClient) -> Dict[str, Any]:
    """Выгрузить трекеры и статусы Redmine для наглядности."""
    info: Dict[str, Any] = {}

    # Статусы
    try:
        resp_statuses = await client.get("/issue_statuses.json")
        info["statuses_status_code"] = resp_statuses.status_code
        info["statuses_raw"] = resp_statuses.text
        if resp_statuses.status_code < 400:
            data = resp_statuses.json()
            info["statuses"] = data.get("issue_statuses") or []
    except Exception as exc:  # noqa: BLE001
        info["statuses_error"] = str(exc)

    # Трекеры
    try:
        resp_trackers = await client.get("/trackers.json")
        info["trackers_status_code"] = resp_trackers.status_code
        info["trackers_raw"] = resp_trackers.text
        if resp_trackers.status_code < 400:
            data = resp_trackers.json()
            info["trackers"] = data.get("trackers") or []
    except Exception as exc:  # noqa: BLE001
        info["trackers_error"] = str(exc)

    return info


async def main() -> None:
    parser = argparse.ArgumentParser(description="Test single Redmine issue create")
    parser.add_argument("--project-id", type=int, required=True, help="Redmine project ID")
    parser.add_argument("--tracker-id", type=int, required=True, help="Redmine tracker ID")
    parser.add_argument("--status-id", type=int, required=True, help="Redmine status ID")
    parser.add_argument("--subject", type=str, required=True, help="Issue subject")
    parser.add_argument("--description", type=str, default="", help="Issue description")
    parser.add_argument(
        "--assigned-to",
        type=int,
        dest="assigned_to_id",
        default=None,
        help="Redmine user ID for assigned_to (optional)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only show payload, do not send request",
    )

    args = parser.parse_args()

    # Подтягиваем переменные из .env в os.environ
    _load_basic_env()

    base_url = _get_env("REDMINE_URL", required=True)
    api_key = _get_env("REDMINE_API_KEY", required=True)

    verify_env = os.getenv("REDMINE_VERIFY_SSL")
    verify: bool = True
    if verify_env is not None:
        verify = verify_env not in {"0", "false", "False"}

    headers = {"X-Redmine-API-Key": api_key}

    async with httpx.AsyncClient(base_url=base_url, headers=headers, verify=verify) as client:
        # Для наглядности сразу выведем статусы и трекеры
        info = await _fetch_basic_info(client)
        print("=== Redmine statuses / trackers info ===")
        print(json.dumps(info, ensure_ascii=False, indent=2))

        issue_payload: Dict[str, Any] = {
            "project_id": args.project_id,
            "tracker_id": args.tracker_id,
            "status_id": args.status_id,
            "subject": args.subject,
            "description": args.description,
        }
        if args.assigned_to_id is not None:
            issue_payload["assigned_to_id"] = args.assigned_to_id

        print("\n=== Prepared issue payload ===")
        print(json.dumps(issue_payload, ensure_ascii=False, indent=2))

        if args.dry_run:
            print("\nDRY-RUN: запрос в Redmine не выполнялся")
            return

        print("\n=== Sending POST /issues.json ===")
        request_payload: Dict[str, Any] = {
            "project_id": args.project_id,
            "issue": issue_payload,
        }
        print("Request payload:")
        print(json.dumps(request_payload, ensure_ascii=False, indent=2))

        try:
            resp = await client.post("/issues.json", json=request_payload)
        except Exception as exc:  # noqa: BLE001
            print(f"Request failed with exception: {exc}")
            return

        print(f"Response status: {resp.status_code}")
        print("Response body:")
        try:
            # Попробуем распарсить как JSON
            data = resp.json()
            print(json.dumps(data, ensure_ascii=False, indent=2))
        except Exception:
            print(resp.text)


if __name__ == "__main__":
    asyncio.run(main())
