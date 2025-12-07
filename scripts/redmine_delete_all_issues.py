from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.services import redmine_sync  # noqa: E402


def _load_basic_env() -> None:
    """Простая загрузка .env (KEY=VALUE) в os.environ.

    Аналогично cli/__main__._load_basic_env, чтобы можно было
    запускать скрипт напрямую из репозитория.
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


async def main_async() -> None:
    parser = argparse.ArgumentParser(
        description="Удаление всех задач (issues) в Redmine через REST API",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Выполнить реальное удаление (по умолчанию только dry-run)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести полный JSON-результат",
    )

    args = parser.parse_args()

    _load_basic_env()

    dry_run = not args.apply
    summary: Dict[str, Any] = await redmine_sync.delete_all_issues(dry_run=dry_run)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    print("Redmine delete_all_issues:")
    print(f"  Dry run:   {summary.get('dry_run')}")
    print(f"  Total:     {summary.get('total')}")
    print(f"  Deleted:   {summary.get('deleted')}")
    print(f"  Errors:    {summary.get('errors')}")
    if summary.get("errors"):
        print("  См. error_details в JSON-режиме (--json) для подробностей")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
