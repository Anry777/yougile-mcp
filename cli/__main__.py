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
from src.api import stickers as api_stickers  # type: ignore

from . import tasks as tasks_cmd
from .config import resolve_project_id
from . import boards as boards_cmd
from . import webhooks as webhooks_cmd
from . import auth as auth_cmd
from . import projects as projects_cmd
from src.services import stats as stats_service


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
                    if k and v and (k not in os.environ or not os.environ.get(k)):
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

    # tasks comments-by-title (temporary helper)
    p_cbt = tasks_sub.add_parser("comments-by-title", help="Get task comments by board/column/task titles")
    p_cbt.add_argument("--board", dest="board_title", required=True, help="Board title")
    p_cbt.add_argument("--column", dest="column_title", required=True, help="Column title")
    p_cbt.add_argument("--task", dest="task_title", required=True, help="Task title")
    p_cbt.add_argument("--json", action="store_true", help="Output JSON")

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

    # webhooks group
    wh_parser = subparsers.add_parser("webhooks", help="Webhooks operations")
    wh_parser.add_argument("--json", action="store_true", help="Output JSON")
    wh_sub = wh_parser.add_subparsers(dest="webhooks_cmd", required=True)
    # webhooks create
    wh_create = wh_sub.add_parser("create", help="Create webhook subscription")
    wh_create.add_argument("--url", required=True, help="Webhook target URL")
    wh_create.add_argument("--event", required=True, help="Event pattern, e.g. task-* or .*")
    wh_create.add_argument("--json", action="store_true", help="Output JSON")
    # webhooks list
    wh_list = wh_sub.add_parser("list", help="List webhook subscriptions")
    wh_list.add_argument("--json", action="store_true", help="Output JSON")
    # webhooks delete
    wh_del = wh_sub.add_parser("delete", help="Delete webhook subscription")
    wh_del.add_argument("--id", required=True, help="Webhook ID")
    wh_del.add_argument("--json", action="store_true", help="Output JSON")
    # webhooks delete-all
    wh_del_all = wh_sub.add_parser("delete-all", help="Delete all webhook subscriptions (mark deleted=true)")
    wh_del_all.add_argument("--json", action="store_true", help="Output JSON")

    # webhooks update
    wh_upd = wh_sub.add_parser("update", help="Update webhook subscription")
    wh_upd.add_argument("--id", required=True, help="Webhook ID")
    wh_upd.add_argument("--url", required=False, help="New URL")
    wh_upd.add_argument("--event", required=False, help="New event pattern")
    wh_upd.add_argument("--disabled", action="store_true", help="Disable webhook")
    wh_upd.add_argument("--enabled", action="store_true", help="Enable webhook")
    wh_upd.add_argument("--deleted", action="store_true", help="Mark webhook as deleted")
    wh_upd.add_argument("--restore", action="store_true", help="Restore webhook (deleted=false)")
    wh_upd.add_argument("--json", action="store_true", help="Output JSON")
    
    # webhooks catch-up
    wh_catchup = wh_sub.add_parser("catch-up", help="Process unprocessed webhook events (catch-up sync)")
    wh_catchup.add_argument("--db", dest="db_path", type=str, default=None, help="Local DB URL (default: YOUGILE_LOCAL_DB_URL)")
    wh_catchup.add_argument("--webhook-db", dest="webhook_db_path", type=str, default=None, help="Webhook DB URL (default: YOUGILE_WEBHOOK_DB_URL)")
    wh_catchup.add_argument("--since", type=str, default=None, help="ISO timestamp: only process events received after this time")
    wh_catchup.add_argument("--no-mark-processed", dest="no_mark_processed", action="store_true", help="Do not mark events as processed (dry-run mode)")
    wh_catchup.add_argument("--json", action="store_true", help="Output JSON")

    auth_parser = subparsers.add_parser("auth", help="Authentication utilities")
    auth_parser.add_argument("--json", action="store_true", help="Output JSON")
    auth_sub = auth_parser.add_subparsers(dest="auth_cmd", required=True)
    # auth keys (list)
    auth_keys = auth_sub.add_parser("keys", help="List API keys via POST /auth/keys/get")
    auth_keys.add_argument("--login", required=False, help="User login (email). If omitted, takes YOUGILE_EMAIL from env")
    auth_keys.add_argument("--password", required=False, help="User password. If omitted, takes YOUGILE_PASSWORD from env")
    auth_keys.add_argument("--company-id", dest="company_id", required=False, help="Company ID. If omitted, takes YOUGILE_COMPANY_ID from env")
    auth_keys.add_argument("--json", action="store_true", help="Output JSON")
    # auth set-api-key
    auth_set = auth_sub.add_parser("set-api-key", help="Write YOUGILE_API_KEY to .env")
    group = auth_set.add_mutually_exclusive_group(required=True)
    group.add_argument("--key", dest="api_key", required=False, help="API key value to write")
    group.add_argument("--from-latest", dest="from_latest", action="store_true", help="Fetch latest key and write it")
    auth_set.add_argument("--login", required=False, help="User login (email) for --from-latest")
    auth_set.add_argument("--password", required=False, help="User password for --from-latest")
    auth_set.add_argument("--company-id", dest="company_id", required=False, help="Company ID for --from-latest")

    # import group
    imp_parser = subparsers.add_parser("import", help="Import data into local DB")
    imp_parser.add_argument("--json", action="store_true", help="Output JSON")
    imp_sub = imp_parser.add_subparsers(dest="import_cmd", required=True)
    # import project
    imp_proj = imp_sub.add_parser("project", help="Import full project into local SQLite DB")
    imp_proj.add_argument("--project-id", dest="project_id", type=str, default=None, help="Project UUID to import")
    imp_proj.add_argument("--db", dest="db_path", type=str, default="./yougile_local.db", help="SQLite DB file path")
    imp_proj.add_argument("--reset", dest="reset", action="store_true", help="Drop and reimport selected project")
    imp_proj.add_argument("--prune", dest="prune", action="store_true", help="Delete local records missing in cloud")
    imp_proj.add_argument("--json", action="store_true", help="Output JSON")

    # import all projects
    imp_all = imp_sub.add_parser("all-projects", help="Import all projects into local SQLite DB")
    imp_all.add_argument("--db", dest="db_path", type=str, default="./yougile_local.db", help="SQLite DB file path")
    imp_all.add_argument("--reset", dest="reset", action="store_true", help="Drop and reimport selected projects")
    imp_all.add_argument("--prune", dest="prune", action="store_true", help="Delete local records missing in cloud")
    imp_all.add_argument("--include-deleted", dest="include_deleted", action="store_true", help="Also import deleted/archived projects")
    imp_all.add_argument("--json", action="store_true", help="Output JSON")

    # projects group
    projects_parser = subparsers.add_parser("projects", help="Project operations")
    projects_parser.add_argument("--json", action="store_true", help="Output JSON")
    projects_sub = projects_parser.add_subparsers(dest="projects_cmd", required=True)
    p_plist = projects_sub.add_parser("list", help="List projects in company")
    p_plist.add_argument("--json", action="store_true", help="Output JSON")

    # sync group (e.g. YouGile -> Redmine)
    sync_parser = subparsers.add_parser("sync", help="Synchronization operations")
    sync_parser.add_argument("--json", action="store_true", help="Output JSON")
    sync_sub = sync_parser.add_subparsers(dest="sync_cmd", required=True)

    sync_redmine = sync_sub.add_parser("redmine", help="Sync data into Redmine from local DB")
    sync_redmine.add_argument(
        "--db",
        dest="db_path",
        type=str,
        default="./yougile_local.db",
        help="Local DB file path or URL (optional, default uses YOUGILE_LOCAL_DB_URL)",
    )
    sync_redmine.add_argument(
        "--entities",
        nargs="+",
        choices=["users", "projects", "boards", "all"],
        default=["users"],
        help="Entities to sync (users, projects, boards; all = all supported)",
    )
    sync_redmine.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Apply changes (by default runs in dry-run mode)",
    )
    sync_redmine.add_argument("--json", action="store_true", help="Output JSON")

    # db group
    db_parser = subparsers.add_parser("db", help="Local DB utilities")
    db_sub = db_parser.add_subparsers(dest="db_cmd", required=True)
    db_stats = db_sub.add_parser("stats", help="Show basic statistics for local DB")
    db_stats.add_argument("--db", dest="db_path", type=str, default="./yougile_local.db", help="SQLite DB file path (optional, default uses YOUGILE_LOCAL_DB_URL)")
    db_stats.add_argument("--json", action="store_true", help="Output JSON")
    db_sprints = db_sub.add_parser("sprints", help="Show sample tasks with stickers (sprints analysis)")
    db_sprints.add_argument("--db", dest="db_path", type=str, default="./yougile_local.db", help="SQLite DB file path (optional, default uses YOUGILE_LOCAL_DB_URL)")
    db_sprints.add_argument("--limit", type=int, default=20, help="How many tasks with stickers to show")
    db_sprints.add_argument("--json", action="store_true", help="Output JSON")
    db_sync_sprints = db_sub.add_parser("sync-sprints", help="Sync sprint stickers directory from YouGile into local DB")
    db_sync_sprints.add_argument("--db", dest="db_path", type=str, default="./yougile_local.db", help="SQLite DB file path (optional, default uses YOUGILE_LOCAL_DB_URL)")
    db_sync_sprints.add_argument("--json", action="store_true", help="Output JSON")

    # stickers group (for sprint/string stickers debug)
    stickers_parser = subparsers.add_parser("stickers", help="Sticker utilities (string/sprint stickers)")
    stickers_parser.add_argument("--json", action="store_true", help="Output JSON")
    stickers_sub = stickers_parser.add_subparsers(dest="stickers_cmd", required=True)
    s_sprint_dump = stickers_sub.add_parser("sprint-dump", help="Dump sprint stickers from YouGile API")
    s_sprint_dump.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args(argv)

    async def _run():
        if args.command == "auth" and args.auth_cmd == "keys":
            # Load .env before reading env fallbacks
            _load_basic_env()
            login = args.login or os.environ.get("YOUGILE_EMAIL") or os.environ.get("yougile_email")
            password = args.password or os.environ.get("YOUGILE_PASSWORD") or os.environ.get("yougile_password")
            company_id = args.company_id or os.environ.get("YOUGILE_COMPANY_ID") or os.environ.get("yougile_company_id")
            if not login or not password or not company_id:
                print("auth keys requires --login/--password/--company-id or corresponding env vars", file=sys.stderr)
                sys.exit(1)
            result = await auth_cmd.list_keys(login, password, company_id)
            if getattr(args, "json", False):
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                for k in result:
                    print(f"{k.get('key')} | company={k.get('companyId')} | deleted={k.get('deleted')} | ts={k.get('timestamp')}")
            return

        if args.command == "auth" and args.auth_cmd == "set-api-key":
            from . import auth as auth_cmd_mod
            _load_basic_env()
            if getattr(args, "from_latest", False):
                login = args.login or os.environ.get("YOUGILE_EMAIL") or os.environ.get("yougile_email")
                password = args.password or os.environ.get("YOUGILE_PASSWORD") or os.environ.get("yougile_password")
                company_id = args.company_id or os.environ.get("YOUGILE_COMPANY_ID") or os.environ.get("yougile_company_id")
                if not login or not password or not company_id:
                    print("set-api-key --from-latest requires --login/--password/--company-id or corresponding env vars", file=sys.stderr)
                    sys.exit(1)
                key = await auth_cmd_mod.set_api_key_from_latest(login, password, company_id)
                if not key:
                    print("No keys found to write", file=sys.stderr)
                    sys.exit(1)
                if getattr(args, "json", False):
                    print(json.dumps({"written": True, "source": "latest", "key_preview": key[:6] + "..."}, ensure_ascii=False))
                else:
                    print("YOUGILE_API_KEY written from latest key")
            else:
                if not getattr(args, "api_key", None):
                    print("--key is required unless --from-latest is specified", file=sys.stderr)
                    sys.exit(1)
                from . import auth as auth_cmd_mod2
                auth_cmd_mod2.write_api_key_to_env(args.api_key)
                if getattr(args, "json", False):
                    print(json.dumps({"written": True, "source": "provided", "key_preview": args.api_key[:6] + "..."}, ensure_ascii=False))
                else:
                    print("YOUGILE_API_KEY written to .env")
            return

        # Lightweight auth init for the rest of CLI to avoid importing MCP server
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
            # Only auto-create when explicitly allowed
            auto_create = os.environ.get("YOUGILE_AUTO_CREATE_API_KEY", "0") in {"1", "true", "True"}
            if auto_create and email and password and company_id:
                try:
                    async with YouGileClient(core_auth.auth_manager.__class__()) as client:
                        api_key = await api_auth.create_api_key(client, email, password, company_id)
                        if api_key:
                            auth_cmd.write_api_key_to_env(api_key)
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
        # Resolve project id only for commands that require it
        if args.command == "tasks":
            project_id = resolve_project_id(getattr(args, "project_id", None))
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
            elif args.tasks_cmd == "comments-by-title":
                result = await tasks_cmd.get_task_comments_by_titles(
                    project_id=project_id,
                    board_title=args.board_title,
                    column_title=args.column_title,
                    task_title=args.task_title,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    for m in result:
                        mid = m.get("id")
                        author = m.get("authorId") or m.get("author") or m.get("userId")
                        ts = m.get("timestamp") or m.get("createdAt")
                        text = m.get("text") or m.get("message")
                        print(f"{mid} | {author} | {ts} | {text}")
        elif args.command == "boards":
            # resolve project id for boards
            project_id = resolve_project_id(getattr(args, "project_id", None))
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
        elif args.command == "webhooks":
            if args.webhooks_cmd == "create":
                result = await webhooks_cmd.create(args.url, args.event)
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"Created webhook: {result}")
            elif args.webhooks_cmd == "list":
                result = await webhooks_cmd.list_all()
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    for w in result:
                        print(f"{w.get('id')} | {w.get('event')} -> {w.get('url')}")
            elif args.webhooks_cmd == "delete":
                result = await webhooks_cmd.delete(args.id)
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"Deleted webhook: {result}")
            elif args.webhooks_cmd == "delete-all":
                result = await webhooks_cmd.delete_all()
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"Deleted {result.get('deleted_count')} webhooks")
            elif args.webhooks_cmd == "update":
                disabled = True if getattr(args, "disabled", False) else (False if getattr(args, "enabled", False) else None)
                deleted = True if getattr(args, "deleted", False) else (False if getattr(args, "restore", False) else None)
                result = await webhooks_cmd.update(
                    webhook_id=args.id,
                    url=args.url,
                    event=args.event,
                    disabled=disabled,
                    deleted=deleted,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"Updated webhook: {result}")
            elif args.webhooks_cmd == "catch-up":
                from webhooks import consumer as webhook_consumer
                from datetime import datetime as dt
                
                # Resolve DB URLs with defaults
                _load_basic_env()
                local_db_url = args.db_path or os.environ.get("YOUGILE_LOCAL_DB_URL") or (
                    getattr(settings, "yougile_local_db_url", None) if settings else None
                )
                webhook_db_url = args.webhook_db_path or os.environ.get("YOUGILE_WEBHOOK_DB_URL") or (
                    getattr(settings, "yougile_webhook_db_url", None) if settings else None
                )
                
                if not webhook_db_url:
                    print("Error: YOUGILE_WEBHOOK_DB_URL is required for catch-up (set in .env or pass --webhook-db)", file=sys.stderr)
                    sys.exit(1)
                
                # Parse --since if provided
                since_dt = None
                if args.since:
                    try:
                        since_dt = dt.fromisoformat(args.since)
                    except ValueError:
                        print(f"Error: --since must be ISO format datetime, got: {args.since}", file=sys.stderr)
                        sys.exit(1)
                
                mark_processed = not getattr(args, "no_mark_processed", False)
                
                result = await webhook_consumer.catch_up(
                    webhook_db_url=webhook_db_url,
                    local_db_url=local_db_url,
                    since=since_dt,
                    mark_processed=mark_processed,
                )
                
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(f"Catch-up complete:")
                    print(f"  Examined:  {result.get('examined')}")
                    print(f"  Processed: {result.get('processed')}")
                    print(f"  Errors:    {result.get('errors')}")
                    if result.get("error_details"):
                        print("  Error details:")
                        for err in result.get("error_details", []):
                            print(f"    Event #{err.get('event_id')}: {err.get('error')}")
        elif args.command == "projects":
            if args.projects_cmd == "list":
                result = await projects_cmd.list_projects()
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    for p in result:
                        print(f"{p.get('id')} | {p.get('title')}")
        elif args.command == "sync":
            if args.sync_cmd == "redmine":
                try:
                    from src.services import redmine_sync as redmine_sync_service
                except ModuleNotFoundError as exc:
                    missing = exc.name or "required dependencies"
                    print(
                        f"Redmine sync requires optional dependency '{missing}'. Install the extra packages from requirements.txt and retry.",
                        file=sys.stderr,
                    )
                    sys.exit(1)

                entities = getattr(args, "entities", ["users"]) or ["users"]
                dry_run = not getattr(args, "apply", False)
                result: dict = {}

                if "all" in entities or "users" in entities:
                    result["users"] = await redmine_sync_service.sync_users(
                        db_path=getattr(args, "db_path", "./yougile_local.db"),
                        dry_run=dry_run,
                    )

                if "all" in entities or "projects" in entities:
                    result["projects"] = await redmine_sync_service.sync_projects(
                        db_path=getattr(args, "db_path", "./yougile_local.db"),
                        dry_run=dry_run,
                    )

                if "all" in entities or "boards" in entities:
                    result["boards"] = await redmine_sync_service.sync_boards(
                        db_path=getattr(args, "db_path", "./yougile_local.db"),
                        dry_run=dry_run,
                    )

                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    users_res = result.get("users") or {}
                    if users_res:
                        print("Redmine sync (users):")
                        print(f"  Total:           {users_res.get('total')}")
                        print(f"  Existing:        {users_res.get('existing')}")
                        print(f"  To create:       {users_res.get('to_create')}")
                        print(f"  Created:         {users_res.get('created')}")
                        print(f"  Skipped no email:{users_res.get('skipped_no_email')}")
                        print(f"  Errors:          {users_res.get('errors')}")
                        print(f"  Dry run:         {users_res.get('dry_run')}")

                    projects_res = result.get("projects") or {}
                    if projects_res:
                        print("\nRedmine sync (projects):")
                        print(f"  Total:           {projects_res.get('total')}")
                        print(f"  Existing:        {projects_res.get('existing')}")
                        print(f"  To create:       {projects_res.get('to_create')}")
                        print(f"  Created:         {projects_res.get('created')}")
                        print(f"  Errors:          {projects_res.get('errors')}")
                        print(f"  Dry run:         {projects_res.get('dry_run')}")

                    boards_res = result.get("boards") or {}
                    if boards_res:
                        print("\nRedmine sync (boards as subprojects):")
                        print(f"  Total:           {boards_res.get('total')}")
                        print(f"  Existing:        {boards_res.get('existing')}")
                        print(f"  To create:       {boards_res.get('to_create')}")
                        print(f"  Created:         {boards_res.get('created')}")
                        print(f"  Errors:          {boards_res.get('errors')}")
                        print(f"  Dry run:         {boards_res.get('dry_run')}")
        elif args.command == "import":
            try:
                from src.services import importer as importer_service  # Delay heavy optional deps
            except ModuleNotFoundError as exc:
                missing = exc.name or "required dependencies"
                print(
                    f"Import commands require optional dependency '{missing}'. Install the extra packages from requirements.txt and retry.",
                    file=sys.stderr,
                )
                sys.exit(1)

            if args.import_cmd == "project":
                # Resolve project id
                project_id = resolve_project_id(getattr(args, "project_id", None))
                # Run importer
                result = await importer_service.import_project(
                    project_id=project_id,
                    db_path=getattr(args, "db_path", "./yougile_local.db"),
                    reset=getattr(args, "reset", False),
                    prune=getattr(args, "prune", False),
                    sync_sprints=True,
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        f"Import done: project={result.get('project_id')} boards={result.get('boards')} columns={result.get('columns')} tasks={result.get('tasks')}"
                    )
            elif args.import_cmd == "all-projects":
                result = await importer_service.import_all_projects(
                    db_path=getattr(args, "db_path", "./yougile_local.db"),
                    reset=getattr(args, "reset", False),
                    prune=getattr(args, "prune", False),
                    include_deleted=getattr(args, "include_deleted", False),
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        f"Import all projects done: projects={result.get('projects')} boards={result.get('boards')} columns={result.get('columns')} tasks={result.get('tasks')}"
                    )
        elif args.command == "db":
            if args.db_cmd == "stats":
                result = await stats_service.get_db_stats(
                    db_path=getattr(args, "db_path", "./yougile_local.db"),
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print("Local DB statistics:")
                    print(f"  Projects:        {result.get('projects')}")
                    print(f"  Boards:          {result.get('boards')}")
                    print(f"  Columns:         {result.get('columns')}")
                    print(f"  Users:           {result.get('users')}")
                    print(f"  Tasks:           {result.get('tasks')}")
                    print(f"    Completed:     {result.get('tasks_completed')}")
                    print(f"    Active:        {result.get('tasks_active')}")
                    print(f"    Archived:      {result.get('tasks_archived')}")
                    print(f"  Comments:        {result.get('comments')}")
                    print(f"  Webhook events:  {result.get('webhook_events')}")
                    print()
                    print("Top projects by task count:")
                    for item in result.get("top_projects_by_tasks") or []:
                        print(f"  {item.get('tasks'):5} tasks | {item.get('project_id')} | {item.get('title')}")
                    print()
                    user_stats = result.get("user_task_stats") or []
                    if user_stats:
                        print("Tasks by user (assignments):")
                        for u in user_stats:
                            name = u.get("name") or "<no name>"
                            print(
                                f"  {name} ({u.get('user_id')}): total={u.get('tasks_total')} "
                                f"done={u.get('tasks_completed')} active={u.get('tasks_active')} archived={u.get('tasks_archived')}"
                            )
                    print()
                    proj_activity = result.get("project_last_activity") or []
                    if proj_activity:
                        print("Project last activity (by comments):")
                        for p in proj_activity:
                            ts = p.get("last_comment_at") or "-"
                            print(f"  {ts} | {p.get('project_id')} | {p.get('title')}")
            elif args.db_cmd == "sprints":
                rows = await stats_service.sample_tasks_with_stickers(
                    db_path=getattr(args, "db_path", "./yougile_local.db"),
                    limit=getattr(args, "limit", 20),
                )
                if getattr(args, "json", False):
                    print(json.dumps(rows, ensure_ascii=False, indent=2))
                else:
                    print("Sample tasks with stickers (possible sprint data):")
                    for r in rows:
                        print("-" * 80)
                        print(f"Task:    {r.get('task_id')}")
                        print(f"Project: {r.get('project_title')}")
                        print(f"Board:   {r.get('board_title')} | Column: {r.get('column_title')}")
                        print("Stickers JSON:")
                        print(json.dumps(r.get("stickers"), ensure_ascii=False, indent=2))
            elif args.db_cmd == "sync-sprints":
                try:
                    from src.services import stickers as stickers_service
                except ModuleNotFoundError as exc:
                    missing = exc.name or "required dependencies"
                    print(
                        f"Sync sprints requires optional dependency '{missing}'. Install the extra packages from requirements.txt and retry.",
                        file=sys.stderr,
                    )
                    sys.exit(1)

                result = await stickers_service.sync_sprint_stickers(
                    db_path=getattr(args, "db_path", "./yougile_local.db"),
                )
                if getattr(args, "json", False):
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    print(
                        f"Sprint stickers synced: stickers={result.get('stickers')} states={result.get('states')} db={result.get('db_url')}"
                    )
        elif args.command == "stickers":
            if args.stickers_cmd == "sprint-dump":
                async with YouGileClient(core_auth.auth_manager) as client:
                    result = await api_stickers.get_sprint_stickers(client)
                # По умолчанию выводим JSON, так как это отладочная команда
                print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(_run())

if __name__ == "__main__":
    main()
