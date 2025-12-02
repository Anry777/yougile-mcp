from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import func, select, case
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from src.localdb.models import Board, Column, Comment, Project, Task, TaskAssignee, User  # noqa: E402

# Import webhook models if available
try:
    import sys
    WEBHOOKS_PATH = PROJECT_ROOT / "webhooks"
    if str(WEBHOOKS_PATH) not in sys.path:
        sys.path.insert(0, str(WEBHOOKS_PATH))
    from models import WebhookEvent  # noqa: E402
    WEBHOOKS_AVAILABLE = True
except ImportError:
    WEBHOOKS_AVAILABLE = False
    WebhookEvent = None


async def gather_stats(db_url: str, days: int, webhook_db_url: str | None = None) -> Dict[str, Any]:
    engine = create_async_engine(db_url, echo=False, future=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    stats: Dict[str, Any] = {}
    async with session_factory() as session:
        stats["projects"] = (await session.execute(select(func.count(Project.id)))).scalar_one()
        stats["boards"] = (await session.execute(select(func.count(Board.id)))).scalar_one()
        stats["columns"] = (await session.execute(select(func.count(Column.id)))).scalar_one()
        stats["users"] = (await session.execute(select(func.count(User.id)))).scalar_one()
        stats["tasks_total"] = (await session.execute(select(func.count(Task.id)))).scalar_one()
        stats["tasks_completed"] = (
            await session.execute(select(func.count(Task.id)).where(Task.completed.is_(True)))
        ).scalar_one()
        stats["tasks_active"] = (
            await session.execute(select(func.count(Task.id)).where(Task.completed.is_(False)))
        ).scalar_one()
        stats["tasks_archived"] = (
            await session.execute(select(func.count(Task.id)).where(Task.archived.is_(True)))
        ).scalar_one()
        stats["comments"] = (await session.execute(select(func.count(Comment.id)))).scalar_one()

        stats["top_projects"] = (
            await session.execute(
                select(Project.title, func.count(Task.id).label("task_count"))
                .join(Board, Board.project_id == Project.id, isouter=True)
                .join(Column, Column.board_id == Board.id, isouter=True)
                .join(Task, Task.column_id == Column.id, isouter=True)
                .group_by(Project.id, Project.title)
                .order_by(func.count(Task.id).desc())
                .limit(5)
            )
        ).all()

        stats["user_load"] = (
            await session.execute(
                select(
                    User.name,
                    func.count(Task.id).label("total"),
                    func.sum(case((Task.completed.is_(True), 1), else_=0)).label("completed"),
                )
                .join(TaskAssignee, TaskAssignee.user_id == User.id, isouter=True)
                .join(Task, Task.id == TaskAssignee.task_id, isouter=True)
                .group_by(User.id, User.name)
                .order_by(func.count(Task.id).desc())
            )
        ).all()

        since = datetime.now(UTC) - timedelta(days=days)
        created_column = getattr(Task, "created_at", None)
        completed_column = getattr(Task, "completed_at", None)

        if created_column is not None:
            stats["new_tasks"] = (
                await session.execute(select(func.count()).where(created_column >= since))
            ).scalar_one()
        else:
            stats["new_tasks"] = None

        if completed_column is not None:
            stats["completed_tasks"] = (
                await session.execute(select(func.count()).where(completed_column >= since))
            ).scalar_one()
        else:
            stats["completed_tasks"] = None

    await engine.dispose()
    
    # Webhook statistics (if webhook DB URL provided)
    if webhook_db_url and WEBHOOKS_AVAILABLE:
        webhook_engine = create_async_engine(webhook_db_url, echo=False, future=True)
        webhook_session_factory = async_sessionmaker(webhook_engine, expire_on_commit=False)
        
        async with webhook_session_factory() as wh_session:
            # Total webhook events
            stats["webhook_events_total"] = (
                await wh_session.execute(select(func.count(WebhookEvent.id)))
            ).scalar_one()
            
            # Last webhook received
            last_webhook = (
                await wh_session.execute(
                    select(WebhookEvent.received_at, WebhookEvent.event_type)
                    .order_by(WebhookEvent.received_at.desc())
                    .limit(1)
                )
            ).first()
            stats["last_webhook_at"] = last_webhook[0] if last_webhook else None
            stats["last_webhook_type"] = last_webhook[1] if last_webhook else None
            
            # Webhooks in last N days
            since = datetime.now(UTC) - timedelta(days=days)
            stats["webhook_events_recent"] = (
                await wh_session.execute(
                    select(func.count(WebhookEvent.id))
                    .where(WebhookEvent.received_at >= since)
                )
            ).scalar_one()
            
            # Task events breakdown (created/updated/completed)
            task_created = (
                await wh_session.execute(
                    select(func.count(WebhookEvent.id))
                    .where(WebhookEvent.received_at >= since)
                    .where(WebhookEvent.event_type.like("%task-created%"))
                )
            ).scalar_one()
            
            task_updated = (
                await wh_session.execute(
                    select(func.count(WebhookEvent.id))
                    .where(WebhookEvent.received_at >= since)
                    .where(WebhookEvent.event_type.like("%task-updated%"))
                )
            ).scalar_one()
            
            task_completed = (
                await wh_session.execute(
                    select(func.count(WebhookEvent.id))
                    .where(WebhookEvent.received_at >= since)
                    .where(WebhookEvent.event_type.like("%task-completed%"))
                )
            ).scalar_one()
            
            stats["webhook_task_created"] = task_created
            stats["webhook_task_updated"] = task_updated
            stats["webhook_task_completed"] = task_completed
        
        await webhook_engine.dispose()
    else:
        stats["webhook_events_total"] = None
        stats["last_webhook_at"] = None
        stats["last_webhook_type"] = None
        stats["webhook_events_recent"] = None
        stats["webhook_task_created"] = None
        stats["webhook_task_updated"] = None
        stats["webhook_task_completed"] = None
    
    return stats


def print_report(data: Dict[str, Any], days: int) -> None:
    print("=== YouGile DB statistics ===")
    print(f"Projects:        {data['projects']}")
    print(f"Boards:          {data['boards']}")
    print(f"Columns:         {data['columns']}")
    print(f"Users:           {data['users']}")
    print(f"Tasks (total):   {data['tasks_total']}")
    print(f"  Completed:     {data['tasks_completed']}")
    print(f"  Active:        {data['tasks_active']}")
    print(f"  Archived:      {data['tasks_archived']}")
    print(f"Comments:        {data['comments']}")
    
    # Webhook statistics
    if data.get("webhook_events_total") is not None:
        print(f"\nWebhook events:  {data['webhook_events_total']}")
        last_wh = data.get("last_webhook_at")
        if last_wh:
            print(f"  Last received: {last_wh} ({data.get('last_webhook_type', 'unknown')})") 
        else:
            print(f"  Last received: Never")
        print(f"  Recent ({days} days): {data.get('webhook_events_recent', 0)}")
        print(f"    Tasks created:   {data.get('webhook_task_created', 0)}")
        print(f"    Tasks updated:   {data.get('webhook_task_updated', 0)}")
        print(f"    Tasks completed: {data.get('webhook_task_completed', 0)}")

    new_tasks = data.get("new_tasks")
    if new_tasks is not None:
        print(f"New tasks (last {days} days): {new_tasks}")
    else:
        print("New tasks:       N/A (created_at column not found)")

    completed_tasks = data.get("completed_tasks")
    if completed_tasks is not None:
        print(f"Completed tasks (last {days} days): {completed_tasks}")
    else:
        print("Completed tasks: N/A (completed_at column not found)")

    print("\nTop projects by task count:")
    for title, task_count in data.get("top_projects", []):
        print(f"  {task_count:5} | {title}")

    print("\nTasks per user:")
    if not data.get("user_load"):
        print("  <no assignments>")
    else:
        for name, total, completed in data["user_load"]:
            safe_name = name or "<no name>"
            print(f"  {safe_name}: total={total or 0}, completed={completed or 0}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone DB stats reporter for YouGile MCP")
    parser.add_argument(
        "--db",
        dest="db_url",
        default=os.environ.get(
            "YOUGILE_LOCAL_DB_URL",
            "postgresql+asyncpg://yougile:yougile@10.1.2.124:55432/yougile",
        ),
        help="Database URL (async SQLAlchemy format)",
    )
    parser.add_argument(
        "--days",
        dest="days",
        type=int,
        default=7,
        help="Time window in days for weekly metrics",
    )
    parser.add_argument(
        "--webhook-db",
        dest="webhook_db_url",
        default=os.environ.get("YOUGILE_WEBHOOK_DB_URL"),
        help="Webhook database URL (optional, for webhook statistics)",
    )
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    stats = await gather_stats(args.db_url, args.days, args.webhook_db_url)
    print_report(stats, args.days)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
