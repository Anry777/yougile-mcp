"""
Webhook events consumer for catch-up synchronization.
Reads unprocessed events from webhook_events table and applies them to local DB.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from .db import init_engine as init_webhook_engine
from .models import WebhookEvent

logger = logging.getLogger(__name__)


def _to_dt(value: Any) -> Optional[datetime]:
    """Convert numeric or ISO timestamp to naive UTC datetime.

    Supports both seconds and milliseconds since epoch, as well as
    ISO strings with optional trailing "Z".
    Returns None on any parsing error.
    """
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            v = float(value)
            # Treat very large numeric values as milliseconds
            if v > 10_000_000_000:
                v = v / 1000.0
            return datetime.utcfromtimestamp(v)
        if isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except Exception:
                return None
            # Drop timezone info to store as naive UTC in DB
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt
    except Exception:
        return None
    return None


async def _fetch_and_create_missing_entity(
    entity_type: str, entity_id: str, local_session: AsyncSession, yougile_client
) -> bool:
    """
    Fetch missing entity from YouGile API and create it in local DB.

    Returns True if entity was successfully fetched and created, False otherwise.
    """
    from src.api import tasks as api_tasks
    from src.api import projects as api_projects
    from src.api import boards as api_boards
    from src.api import columns as api_columns
    from src.api import users as api_users
    from src.api import stickers as api_stickers

    try:
        logger.info(f"Fetching missing {entity_type} {entity_id} from YouGile API")

        if entity_type == "task":
            data = await api_tasks.get_task(yougile_client, entity_id)
            if data:
                try:
                    await _upsert_task_from_payload(data, local_session)
                    await local_session.commit()
                    logger.info(f"Created missing task {entity_id}")
                    return True
                except IntegrityError as ie:
                    await local_session.rollback()
                    # If task creation fails due to missing column, create column first
                    if "column_id" in str(ie):
                        column_id = data.get("columnId")
                        if column_id:
                            if await _fetch_and_create_missing_entity(
                                "column",
                                column_id,
                                local_session,
                                yougile_client,
                            ):
                                # Retry task creation
                                await _upsert_task_from_payload(data, local_session)
                                await local_session.commit()
                                logger.info(
                                    f"Created missing task {entity_id} "
                                    "after resolving column"
                                )
                                return True
                    raise

        elif entity_type == "project":
            data = await api_projects.get_project(yougile_client, entity_id)
            if data:
                await _upsert_project_from_payload(data, local_session)
                await local_session.commit()
                logger.info(f"Created missing project {entity_id}")
                return True

        elif entity_type == "board":
            data = await api_boards.get_board(yougile_client, entity_id)
            if data:
                # First ensure project exists
                project_id = data.get("projectId")
                if project_id:
                    await _fetch_and_create_missing_entity(
                        "project", project_id, local_session, yougile_client
                    )

                await _upsert_board_from_payload(data, local_session)
                await local_session.commit()
                logger.info(f"Created missing board {entity_id}")
                return True

        elif entity_type == "column":
            data = await api_columns.get_column(yougile_client, entity_id)
            if data:
                # First ensure board exists (which will ensure project exists)
                board_id = data.get("boardId")
                if board_id:
                    await _fetch_and_create_missing_entity(
                        "board", board_id, local_session, yougile_client
                    )

                await _upsert_column_from_payload(data, local_session)
                await local_session.commit()
                logger.info(f"Created missing column {entity_id}")
                return True

        elif entity_type == "user":
            # Fetch user from company users list
            # Note: YouGile doesn't have a direct GET /users/{id} endpoint
            # We need to get all users and find the one we need
            try:
                users_list = await api_users.get_users(yougile_client)
                if isinstance(users_list, dict) and "content" in users_list:
                    users_list = users_list.get("content", [])

                for user_data in users_list or []:
                    if user_data.get("id") == entity_id:
                        await _upsert_user_from_payload(user_data, local_session)
                        await local_session.commit()
                        logger.info(f"Created missing user {entity_id}")
                        return True

                logger.warning(f"User {entity_id} not found in company users list")
                return False
            except Exception as e:
                logger.error(f"Failed to fetch user {entity_id}: {e}")
                return False

        elif entity_type == "sticker":
            # Fetch sticker from company stickers
            try:
                stickers_list = await api_stickers.get_sprint_stickers(
                    yougile_client
                )
                if isinstance(stickers_list, dict) and "content" in stickers_list:
                    stickers_list = stickers_list.get("content", [])

                for sticker_data in stickers_list or []:
                    if sticker_data.get("id") == entity_id:
                        await _upsert_sticker_from_payload(sticker_data, local_session)
                        await local_session.commit()
                        logger.info(f"Created missing sticker {entity_id}")
                        return True

                logger.warning(
                    f"Sticker {entity_id} not found in company stickers list"
                )
                return False
            except Exception as e:
                logger.error(f"Failed to fetch sticker {entity_id}: {e}")
                return False

        else:
            logger.warning(f"Unknown entity type for auto-fetch: {entity_type}")
            return False

    except Exception as e:
        logger.error(f"Failed to fetch/create missing {entity_type} {entity_id}: {e}")
        await local_session.rollback()
        return False

    return False


async def _upsert_task_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert task from webhook payload into local DB."""
    from src.localdb.models import Task

    task_id = payload.get("id")
    if not task_id:
        raise ValueError("Task payload missing 'id' field")

    # Load existing task if present to avoid blindly overwriting timestamp fields
    existing: Optional[Task] = await local_session.get(Task, task_id)

    # Map payload fields to Task model
    task_data: Dict[str, Any] = {
        "id": task_id,
        "title": payload.get("title", ""),
        "description": payload.get("description"),
        "column_id": payload.get("columnId"),
        "completed": payload.get("completed", False),
        "archived": payload.get("archived", False),
        "deleted": payload.get("deleted"),
        "deadline": payload.get("deadline"),
        "time_tracking": payload.get("timeTracking"),
        "stickers": payload.get("stickers"),
        "checklists": payload.get("checklists"),
    }

    # Timestamps: support both API payloads (createdAt/completedAt/archivedAt)
    # and webhook payloads (timestamp/completedTimestamp/archivedTimestamp).
    created_raw = payload.get("createdAt") or payload.get("timestamp")
    completed_raw = payload.get("completedAt") or payload.get("completedTimestamp")
    archived_raw = payload.get("archivedAt") or payload.get("archivedTimestamp")

    created_dt = _to_dt(created_raw)
    if created_dt is not None and (
        existing is None or getattr(existing, "created_at", None) is None
    ):
        task_data["created_at"] = created_dt

    completed_dt = _to_dt(completed_raw)
    if completed_dt is not None and (
        existing is None or getattr(existing, "completed_at", None) is None
    ):
        task_data["completed_at"] = completed_dt

    archived_dt = _to_dt(archived_raw)
    if archived_dt is not None and (
        existing is None or getattr(existing, "archived_at", None) is None
    ):
        task_data["archived_at"] = archived_dt

    # Upsert using merge
    task = await local_session.merge(Task(**task_data))
    logger.debug(f"Upserted task {task_id}: {task.title}")


async def _upsert_project_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert project from webhook payload into local DB."""
    from src.localdb.models import Project

    project_id = payload.get("id")
    if not project_id:
        raise ValueError("Project payload missing 'id' field")

    project_data = {
        "id": project_id,
        "title": payload.get("title", ""),
        "description": payload.get("description"),
    }

    project = await local_session.merge(Project(**project_data))
    logger.debug(f"Upserted project {project_id}: {project.title}")


async def _upsert_comment_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert comment/chat_message from webhook payload into local DB."""
    from src.localdb.models import Comment

    comment_id = payload.get("id")
    if not comment_id:
        raise ValueError("Comment payload missing 'id' field")

    # Convert comment_id to string (it's often a timestamp integer)
    comment_id_str = str(comment_id)

    # Extract task_id from chatId or properties.taskId
    task_id = payload.get("chatId") or payload.get("taskId")
    if not task_id:
        properties = payload.get("properties", {})
        task_id = properties.get("taskId")

    # Extract author from actionBy or userId
    author_id = (
        payload.get("actionBy") or payload.get("authorId") or payload.get("userId")
    )
    if not author_id:
        properties = payload.get("properties", {})
        author_id = properties.get("actionBy")

    comment_data = {
        "id": comment_id_str,
        "task_id": task_id,
        "author_id": author_id,
        "text": payload.get("text") or payload.get("message", ""),
        "timestamp": (
            datetime.fromtimestamp(payload["timestamp"] / 1000)
            if payload.get("timestamp")
            else datetime.utcnow()
        ),
    }

    await local_session.merge(Comment(**comment_data))
    logger.debug(f"Upserted comment {comment_id_str}")


async def _upsert_sticker_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert sticker (sprint or string) from webhook payload into local DB."""
    from src.localdb.models import (
        SprintSticker,
        SprintState,
        StringSticker,
        StringState,
    )
    from sqlalchemy import delete

    sticker_id = payload.get("id")
    if not sticker_id:
        raise ValueError("Sticker payload missing 'id' field")

    sticker_name = payload.get("name", "")
    deleted = payload.get("deleted", False)
    states = payload.get("states", [])

    # Determine sticker type by checking if states have begin/end (sprint) or not (string)
    is_sprint = any(state.get("begin") or state.get("end") for state in states)

    if is_sprint:
        # Upsert SprintSticker
        sticker_data = {
            "id": sticker_id,
            "name": sticker_name,
            "deleted": deleted,
        }
        await local_session.merge(SprintSticker(**sticker_data))

        # Delete existing states and recreate from payload
        await local_session.execute(
            delete(SprintState).where(SprintState.sticker_id == sticker_id)
        )

        for state_data in states:
            state_id = state_data.get("id")
            if not state_id:
                continue

            state = SprintState(
                id=state_id,
                sticker_id=sticker_id,
                name=state_data.get("name", ""),
                begin=(
                    datetime.fromtimestamp(state_data["begin"] / 1000)
                    if state_data.get("begin")
                    else None
                ),
                end=(
                    datetime.fromtimestamp(state_data["end"] / 1000)
                    if state_data.get("end")
                    else None
                ),
            )
            local_session.add(state)

        logger.debug(
            f"Upserted sprint sticker {sticker_id}: {sticker_name} with {len(states)} states"
        )
    else:
        # Upsert StringSticker
        sticker_data = {
            "id": sticker_id,
            "name": sticker_name,
            "deleted": deleted,
        }
        await local_session.merge(StringSticker(**sticker_data))

        # Delete existing states and recreate from payload
        await local_session.execute(
            delete(StringState).where(StringState.sticker_id == sticker_id)
        )

        for state_data in states:
            state_id = state_data.get("id")
            if not state_id:
                continue

            state = StringState(
                id=state_id,
                sticker_id=sticker_id,
                name=state_data.get("name", ""),
            )
            local_session.add(state)

        logger.debug(
            f"Upserted string sticker {sticker_id}: {sticker_name} with {len(states)} states"
        )


async def _upsert_board_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert board from webhook payload into local DB."""
    from src.localdb.models import Board

    board_id = payload.get("id")
    if not board_id:
        raise ValueError("Board payload missing 'id' field")

    board_data = {
        "id": board_id,
        "title": payload.get("title", ""),
        "project_id": payload.get("projectId"),
    }

    board = await local_session.merge(Board(**board_data))
    logger.debug(f"Upserted board {board_id}: {board.title}")


async def _upsert_column_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert column from webhook payload into local DB."""
    from src.localdb.models import Column

    column_id = payload.get("id")
    if not column_id:
        raise ValueError("Column payload missing 'id' field")

    column_data = {
        "id": column_id,
        "title": payload.get("title", ""),
        "color": payload.get("color"),
        "board_id": payload.get("boardId"),
    }

    column = await local_session.merge(Column(**column_data))
    logger.debug(f"Upserted column {column_id}: {column.title}")


async def _upsert_user_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert user from webhook payload into local DB."""
    from src.localdb.models import User

    user_id = payload.get("id")
    if not user_id:
        raise ValueError("User payload missing 'id' field")

    user_data = {
        "id": user_id,
        "name": payload.get("name") or payload.get("realName"),
        "email": payload.get("email") or payload.get("login"),
        "role": payload.get("role"),
    }

    user = await local_session.merge(User(**user_data))
    logger.debug(f"Upserted user {user_id}: {user.name}")


async def _upsert_department_from_payload(
    payload: Dict[str, Any], local_session: AsyncSession
) -> None:
    """Upsert department from webhook payload into local DB."""
    from src.localdb.models import Department

    dept_id = payload.get("id")
    if not dept_id:
        raise ValueError("Department payload missing 'id' field")

    dept_data = {
        "id": dept_id,
        "name": payload.get("name"),
        "parent_id": payload.get("parentId"),
        "deleted": payload.get("deleted", False),
    }

    dept = await local_session.merge(Department(**dept_data))
    logger.debug(f"Upserted department {dept_id}: {dept.name}")


async def catch_up(
    webhook_db_url: str,
    local_db_url: str | None = None,
    since: datetime | None = None,
    mark_processed: bool = True,
) -> Dict[str, Any]:
    """
    Catch up local DB by processing unprocessed webhook events.

    Args:
        webhook_db_url: Connection string for webhook events database
        local_db_url: Connection string for local yougile database (optional for now)
        since: Only process events received after this timestamp (optional)
        mark_processed: If True, mark events as processed after handling

    Returns:
        Summary dict with counts and details
    """
    # Initialize webhook DB engine
    init_webhook_engine(webhook_db_url)

    # Re-import session factory after initialization
    from .db import async_session as webhook_session_factory_local

    if webhook_session_factory_local is None:
        raise RuntimeError("Failed to initialize webhook DB session factory")

    # Initialize local DB engine for entity updates
    local_session_factory = None
    if local_db_url:
        from src.localdb.session import init_engine as init_local_engine

        init_local_engine(local_db_url)
        from src.localdb.session import async_session as local_async_session

        local_session_factory = local_async_session

    processed_count = 0
    examined_count = 0
    errors: List[Dict[str, Any]] = []
    event_summary: List[Dict[str, Any]] = []
    fk_resolved_count = 0

    # Initialize YouGile client for fetching missing entities
    yougile_client = None
    if local_session_factory:
        from src.core.client import YouGileClient
        from src.core import auth as core_auth

        yougile_client = YouGileClient(core_auth.auth_manager)
        await yougile_client.__aenter__()

        # Prefetch and sync company-wide entities (users, stickers, departments)
        # This is much more efficient than fetching them individually per event
        logger.info(
            "Prefetching company-wide entities (users, stickers, departments)..."
        )
        try:
            async with local_session_factory() as prefetch_session:
                # 1. Sync all users
                from src.api import users as api_users

                try:
                    users_list = await api_users.get_users(yougile_client)
                    if isinstance(users_list, dict) and "content" in users_list:
                        users_list = users_list.get("content", [])

                    user_count = 0
                    for user_data in users_list or []:
                        await _upsert_user_from_payload(user_data, prefetch_session)
                        user_count += 1

                    logger.info(f"Synced {user_count} users")
                except Exception as e:
                    logger.warning(f"Failed to prefetch users: {e}")

                # 2. Sync all stickers
                from src.api import stickers as api_stickers

                try:
                    stickers_list = await api_stickers.get_sprint_stickers(
                        yougile_client
                    )
                    if isinstance(stickers_list, dict) and "content" in stickers_list:
                        stickers_list = stickers_list.get("content", [])

                    sticker_count = 0
                    for sticker_data in stickers_list or []:
                        await _upsert_sticker_from_payload(
                            sticker_data, prefetch_session
                        )
                        sticker_count += 1

                    logger.info(f"Synced {sticker_count} stickers")
                except Exception as e:
                    logger.warning(f"Failed to prefetch stickers: {e}")

                # 3. Sync all departments (optional, if API exists)
                # Note: Uncomment if departments API is available
                # from src.api import departments as api_departments
                # try:
                #     departments_list = await api_departments.get_departments(yougile_client)
                #     if isinstance(departments_list, dict) and "content" in departments_list:
                #         departments_list = departments_list.get("content", [])
                #
                #     dept_count = 0
                #     for dept_data in departments_list or []:
                #         await _upsert_department_from_payload(dept_data, prefetch_session)
                #         dept_count += 1
                #
                #     logger.info(f"Synced {dept_count} departments")
                # except Exception as e:
                #     logger.warning(f"Failed to prefetch departments: {e}")

                await prefetch_session.commit()
                logger.info("Company-wide entities prefetch complete")
        except Exception as e:
            logger.error(f"Failed to prefetch company-wide entities: {e}")

    try:
        async with webhook_session_factory_local() as session:
            # Build query for unprocessed events
            query = select(WebhookEvent).where(
                WebhookEvent.processed.is_(False),
            )

            if since:
                query = query.where(WebhookEvent.received_at >= since)

            query = query.order_by(WebhookEvent.received_at.asc())

            result = await session.execute(query)
            events = result.scalars().all()

            for event in events:
                examined_count += 1

                try:
                    # Parse event type (e.g., "task-created", "project-updated")
                    event_type = event.event_type or "unknown"
                    entity_type = event.entity_type or "unknown"
                    entity_id = event.entity_id or "unknown"

                    # Log event
                    logger.info(
                        f"Event #{event.id}: {event_type} | entity={entity_type}/{entity_id} | "
                        f"received={event.received_at.isoformat()}"
                    )

                    # Parse full payload structure
                    full_payload = event.payload
                    entity_data = full_payload.get("payload", {})

                    # Process entity based on type if local DB is available
                    if local_session_factory and entity_data:
                        async with local_session_factory() as local_sess:
                            try:
                                # Determine entity type from event_type (e.g., "task-created" -> "task")
                                entity_prefix = (
                                    event_type.split("-")[0]
                                    if "-" in event_type
                                    else entity_type
                                )

                                if entity_prefix == "task":
                                    await _upsert_task_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "project":
                                    await _upsert_project_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix in ("chat_message", "comment"):
                                    await _upsert_comment_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "sticker":
                                    await _upsert_sticker_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "board":
                                    await _upsert_board_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "column":
                                    await _upsert_column_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "user":
                                    await _upsert_user_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "department":
                                    await _upsert_department_from_payload(
                                        entity_data, local_sess
                                    )
                                elif entity_prefix == "group_chat":
                                    # No model for group_chat yet, just log
                                    logger.debug(
                                        f"Skipping group_chat event (no model): {entity_data.get('id')}"
                                    )
                                else:
                                    logger.debug(
                                        f"No handler for entity type: {entity_prefix}"
                                    )

                                await local_sess.commit()
                            except IntegrityError as ie:
                                await local_sess.rollback()

                                # Check if it's a FK constraint error
                                error_msg = str(ie)
                                if (
                                    "foreign key" in error_msg.lower()
                                    or "violates foreign key constraint"
                                    in error_msg.lower()
                                ):
                                    logger.warning(
                                        f"FK constraint error for event #{event.id}, attempting to resolve"
                                    )

                                    # Try to extract missing entity info and fetch from API
                                    # Parse FK field from error (e.g., task_id, column_id, project_id)
                                    fk_field = None
                                    missing_id = None

                                    if "task_id" in error_msg:
                                        fk_field = "task"
                                        # For comments, task_id is in chatId field
                                        missing_id = entity_data.get(
                                            "taskId"
                                        ) or entity_data.get("chatId")
                                        if not missing_id:
                                            properties = entity_data.get(
                                                "properties", {}
                                            )
                                            missing_id = properties.get("taskId")
                                    elif "column_id" in error_msg:
                                        fk_field = "column"
                                        missing_id = entity_data.get("columnId")
                                    elif "board_id" in error_msg:
                                        fk_field = "board"
                                        missing_id = entity_data.get("boardId")
                                    elif "project_id" in error_msg:
                                        fk_field = "project"
                                        missing_id = entity_data.get("projectId")
                                    elif "author_id" in error_msg:
                                        fk_field = "user"
                                        missing_id = entity_data.get(
                                            "authorId"
                                        ) or entity_data.get("userId")

                                    # Try to fetch and create missing entity
                                    if fk_field and missing_id and yougile_client:
                                        resolved = (
                                            await _fetch_and_create_missing_entity(
                                                fk_field,
                                                missing_id,
                                                local_sess,
                                                yougile_client,
                                            )
                                        )

                                        if resolved:
                                            # Retry the original upsert
                                            try:
                                                if entity_prefix == "task":
                                                    await _upsert_task_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix == "project":
                                                    await _upsert_project_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix in (
                                                    "chat_message",
                                                    "comment",
                                                ):
                                                    await _upsert_comment_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix == "sticker":
                                                    await _upsert_sticker_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix == "board":
                                                    await _upsert_board_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix == "column":
                                                    await _upsert_column_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix == "user":
                                                    await _upsert_user_from_payload(
                                                        entity_data, local_sess
                                                    )
                                                elif entity_prefix == "department":
                                                    await (
                                                        _upsert_department_from_payload(
                                                            entity_data, local_sess
                                                        )
                                                    )

                                                await local_sess.commit()
                                                fk_resolved_count += 1
                                                logger.info(
                                                    f"Successfully resolved FK error for event #{event.id}"
                                                )
                                            except Exception as retry_err:
                                                logger.error(
                                                    f"Retry failed for event #{event.id}: {retry_err}"
                                                )
                                                await local_sess.rollback()
                                                raise
                                        else:
                                            logger.error(
                                                f"Could not resolve FK error for event #{event.id}"
                                            )
                                            raise
                                    else:
                                        logger.error(
                                            f"Cannot auto-resolve FK error for event #{event.id}: field={fk_field}, id={missing_id}"
                                        )
                                        raise
                                else:
                                    # Not a FK error, re-raise
                                    raise
                            except Exception as proc_err:
                                logger.error(
                                    f"Failed to process entity for event #{event.id}: {proc_err}"
                                )
                                await local_sess.rollback()
                                raise

                    event_summary.append(
                        {
                            "id": event.id,
                            "event_type": event_type,
                            "entity_type": entity_type,
                            "entity_id": entity_id,
                            "received_at": event.received_at.isoformat(),
                        }
                    )

                    # Mark as processed if requested
                    if mark_processed:
                        event.processed = True
                        event.processed_at = datetime.utcnow()
                        processed_count += 1

                except Exception as e:
                    logger.error(f"Failed to process event #{event.id}: {e}")
                    errors.append(
                        {
                            "event_id": event.id,
                            "error": str(e),
                        }
                    )
                    # Optionally update error field
                    event.error = str(e)
                    event.retry_count += 1

            # Commit all changes (processed flags, errors)
            await session.commit()
    finally:
        # Clean up YouGile client
        if yougile_client:
            await yougile_client.__aexit__(None, None, None)

    return {
        "examined": examined_count,
        "processed": processed_count,
        "fk_resolved": fk_resolved_count,
        "errors": len(errors),
        "error_details": errors,
        "event_summary": event_summary[:10],  # First 10 for brevity
    }


async def process_single_event(
    event_id: int, local_db_url: Optional[str] = None
) -> bool:
    """
    Process a single webhook event by ID immediately (for auto-sync).

    Args:
        event_id: ID of the webhook event to process
        local_db_url: Optional local DB URL (defaults to env YOUGILE_LOCAL_DB_URL)

    Returns:
        True if processed successfully, False otherwise
    """
    import os
    from src.config import settings

    # Get local DB URL
    if not local_db_url:
        local_db_url = (
            os.environ.get("YOUGILE_LOCAL_DB_URL") or settings.yougile_local_db_url
        )

    # Initialize local DB session
    from src.localdb.session import init_engine as init_local_engine

    init_local_engine(local_db_url)
    from src.localdb.session import async_session as local_async_session

    local_session_factory = local_async_session

    # Re-import webhook session factory
    from .db import async_session as webhook_session_factory_local

    if not webhook_session_factory_local or not local_session_factory:
        logger.error("Session factories not initialized")
        return False

    try:
        async with webhook_session_factory_local() as session:
            # Fetch the event
            result = await session.execute(
                select(WebhookEvent).where(WebhookEvent.id == event_id)
            )
            event = result.scalar_one_or_none()

            if not event:
                logger.error(f"Event #{event_id} not found")
                return False

            if event.processed:
                logger.debug(f"Event #{event_id} already processed")
                return True

            # Parse event data
            event_type = event.event_type or "unknown"
            full_payload = event.payload
            entity_data = full_payload.get("payload", {})

            # Process entity if data exists
            if entity_data and local_session_factory:
                async with local_session_factory() as local_sess:
                    try:
                        # Determine entity type
                        entity_prefix = (
                            event_type.split("-")[0]
                            if "-" in event_type
                            else (event.entity_type or "unknown")
                        )

                        # Route to appropriate handler
                        if entity_prefix == "task":
                            await _upsert_task_from_payload(entity_data, local_sess)
                        elif entity_prefix == "project":
                            await _upsert_project_from_payload(entity_data, local_sess)
                        elif entity_prefix in ("chat_message", "comment"):
                            await _upsert_comment_from_payload(entity_data, local_sess)
                        elif entity_prefix == "sticker":
                            await _upsert_sticker_from_payload(entity_data, local_sess)
                        elif entity_prefix == "board":
                            await _upsert_board_from_payload(entity_data, local_sess)
                        elif entity_prefix == "column":
                            await _upsert_column_from_payload(entity_data, local_sess)
                        elif entity_prefix == "user":
                            await _upsert_user_from_payload(entity_data, local_sess)
                        elif entity_prefix == "department":
                            await _upsert_department_from_payload(
                                entity_data, local_sess
                            )
                        else:
                            logger.debug(f"No handler for entity type: {entity_prefix}")

                        await local_sess.commit()

                        # Mark as processed
                        event.processed = True
                        event.processed_at = datetime.utcnow()
                        await session.commit()

                        return True

                    except Exception as e:
                        logger.error(f"Failed to process event #{event_id}: {e}")
                        await local_sess.rollback()

                        # Update error info
                        event.error = str(e)
                        event.retry_count += 1
                        await session.commit()

                        return False
            else:
                # No entity data, just mark as processed
                event.processed = True
                event.processed_at = datetime.utcnow()
                await session.commit()
                return True

    except Exception as e:
        logger.error(f"Failed to process event #{event_id}: {e}")
        return False
