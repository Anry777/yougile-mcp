import argparse
import asyncio
import json
import os
import sys

# Ensure src/ is on sys.path to import project modules when running from repo root
PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
SRC_PATH = os.path.join(PROJECT_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

try:
    from src.config.settings import settings  # type: ignore
except Exception:
    settings = None  # type: ignore
from src.core import auth as core_auth  # type: ignore
from src.core.client import YouGileClient  # type: ignore
from src.api import auth as api_auth  # type: ignore

from . import tasks as tasks_cmd
from .config import resolve_project_id
from . import boards as boards_cmd


def _load_basic_env():
    """Basic .env loader (KEY=VALUE per line) into os.environ."""
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
                    if k and v and k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="yougile-cli", description="YouGile CLI utilities")
    parser.add_argument("--json", action="store_true", help="Output JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # tasks group
    tasks_parser = subparsers.add_parser("tasks", help="Task operations")
    tasks_parser.add_argument("--json", action="store_true", help="Output JSON")
    tasks_parser.add_argument(
        "--project-id",
        dest="project_id",
        type=str,
        default=None,
        help="Project UUID to scope all operations",
    )
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_cmd", required=True)

    # tasks list
    p_list = tasks_sub.add_parser("list", help="List tasks")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.add_argument("--offset", type=int, default=0)
    p_list.add_argument("--column-id", dest="column_id", type=str, default=None)
    p_list.add_argument("--assigned-to", dest="assigned_to", type=str, default=None)
    p_list.add_argument("--title", dest="title", type=str, default=None)
    p_list.add_argument("--include-deleted", dest="include_deleted", action="store_true")
    p_list.add_argument("--json", action="store_true", help="Output JSON")

    # tasks get
    p_get = tasks_sub.add_parser("get", help="Get task by id")
    p_get.add_argument("--id", required=True)
    p_get.add_argument("--json", action="store_true", help="Output JSON")

    # boards group
    boards_parser = subparsers.add_parser("boards", help="Board operations")
    boards_parser.add_argument("--json", action="store_true", help="Output JSON")
    boards_parser.add_argument(
        "--project-id",
        dest="project_id",
        type=str,
        default=None,
        help="Project UUID to scope all operations",
    )
    boards_sub = boards_parser.add_subparsers(dest="boards_cmd", required=True)
    p_sync = boards_sub.add_parser("sync-unfinished", help="Ensure 'Незавершенные' mirrors columns from 'Все задачи' and copy unfinished tasks")
    p_sync.add_argument("--source-title", dest="source_title", type=str, default="Все задачи")
    p_sync.add_argument("--target-title", dest="target_title", type=str, default="Незавершенные")
    p_sync.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_sync.add_argument("--json", action="store_true", help="Output JSON")

    # boards ensure-user-boards
    p_users = boards_sub.add_parser("ensure-user-boards", help="Create boards for each user having tasks on target board (title = user name)")
    p_users.add_argument("--target-title", dest="target_title", type=str, default="Незавершенные")
    p_users.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_users.add_argument("--json", action="store_true", help="Output JSON")

    # boards distribute-unfinished-by-user
    p_dist = boards_sub.add_parser("distribute-unfinished-by-user", help="Copy unfinished tasks from target board to per-user boards, preserving columns")
    p_dist.add_argument("--target-title", dest="target_title", type=str, default="Незавершенные")
    p_dist.add_argument("--dry-run", dest="dry_run", action="store_true")
    p_dist.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args(argv)

    async def _run():
        # Lightweight auth init for CLI to avoid importing MCP server
        api_key = None
        company_id = None
        if settings is not None:
            api_key = getattr(settings, "yougile_api_key", None)
            company_id = getattr(settings, "yougile_company_id", None)
        if not api_key or not company_id:
            # Fallback: load from .env and os.environ
            _load_basic_env()
            api_key = os.environ.get("YOUGILE_API_KEY") or os.environ.get("yougile_api_key")
            company_id = os.environ.get("YOUGILE_COMPANY_ID") or os.environ.get("yougile_company_id")
        if not api_key:
            # Try to create API key using login credentials if available
            email = os.environ.get("YOUGILE_EMAIL") or os.environ.get("yougile_email") or (
                getattr(settings, "yougile_email", None) if settings is not None else None
            )
            password = os.environ.get("YOUGILE_PASSWORD") or os.environ.get("yougile_password") or (
                getattr(settings, "yougile_password", None) if settings is not None else None
            )
            if email and password and company_id:
                try:
                    async with YouGileClient(core_auth.auth_manager.__class__()) as client:
                        api_key = await api_auth.create_api_key(client, email, password, company_id)
                except Exception as e:
                    print(f"Failed to auto-create API key: {e}", file=sys.stderr)
        if api_key and company_id:
            try:
                core_auth.auth_manager.set_credentials(api_key, company_id)
            except Exception as e:
                print(f"Failed to set credentials: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(
                "Missing credentials: set YOUGILE_API_KEY and YOUGILE_COMPANY_ID in environment or .env",
                file=sys.stderr,
            )
            sys.exit(1)
        # Resolve project id (CLI arg > env > default via cli.config)
        project_id = resolve_project_id(args.project_id)

        if args.command == "tasks":
            if args.tasks_cmd == "list":
                result = await tasks_cmd.list_tasks(
                    project_id=project_id,
                    limit=args.limit,
                    offset=args.offset,
                    column_id=args.column_id,
                    assigned_to=args.assigned_to,
                    title=args.title,
                    include_deleted=args.include_deleted,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    for t in result:
                        tid = t.get("id")
                        title = t.get("title")
                        col = t.get("columnId")
                        completed = t.get("completed")
                        print(f"{tid} | {title} | column={col} | completed={completed}")
            elif args.tasks_cmd == "get":
                result = await tasks_cmd.get_task(args.id, project_id=project_id)
                if args.json:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    # concise pretty
                    print(f"id: {result.get('id')}")
                    print(f"title: {result.get('title')}")
                    print(f"columnId: {result.get('columnId')}")
                    print(f"completed: {result.get('completed')}")
                    desc = result.get("description")
                    if desc:
                        print("description_html:")
                        print(desc)
        elif args.command == "boards":
            # resolve project id for boards
            project_id = resolve_project_id(args.project_id)
            if args.boards_cmd == "sync-unfinished":
                result = await boards_cmd.sync_unfinished(
                    project_id=project_id,
                    source_title=args.source_title,
                    target_title=args.target_title,
                    dry_run=args.dry_run,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        f"Sync finished: created={result['created']}, skipped={result['skipped']}, examined={result['examined']}, dry_run={result['dry_run']}"
                    )
            elif args.boards_cmd == "ensure-user-boards":
                result = await boards_cmd.ensure_user_boards(
                    project_id=project_id,
                    target_title=args.target_title,
                    dry_run=args.dry_run,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        f"User boards ensured: users_detected={result['users_detected']}, created={result['created']}, skipped={result['skipped']}, dry_run={result['dry_run']}"
                    )
            elif args.boards_cmd == "distribute-unfinished-by-user":
                result = await boards_cmd.distribute_unfinished_by_user(
                    project_id=project_id,
                    target_title=args.target_title,
                    dry_run=args.dry_run,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        f"Distributed: examined={result['examined']}, created={result['created']}, skipped={result['skipped']}, dry_run={result['dry_run']}"
                    )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
