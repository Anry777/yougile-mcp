from typing import Any, Dict, List, Optional

# These imports rely on sys.path tweak in cli/__main__.py
from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import tasks as api_tasks
from src.api import boards as api_boards
from src.api import columns as api_columns


async def list_tasks(
    project_id: str,
    limit: int = 50,
    offset: int = 0,
    column_id: Optional[str] = None,
    assigned_to: Optional[str] = None,
    title: Optional[str] = None,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    # Collect allowed columns for the project
    async with YouGileClient(core_auth.auth_manager) as client:
        project_boards = await api_boards.get_boards(client, project_id=project_id, limit=1000, offset=0)
        board_ids = [b.get("id") for b in project_boards if b.get("id")]
        allowed_columns: List[str] = []
        for bid in board_ids:
            cols = await api_columns.get_columns(client, board_id=bid)
            allowed_columns.extend([c.get("id") for c in cols if c.get("id")])

        # If user specified a column, ensure it belongs to project
        if column_id is not None and column_id not in allowed_columns:
            return []

        target_columns = [column_id] if column_id else allowed_columns

        # Aggregate tasks from all target columns, then apply offset/limit
        all_tasks: List[Dict[str, Any]] = []
        for cid in target_columns:
            if not cid:
                continue
            batch = await api_tasks.get_tasks(
                client,
                column_id=cid,
                assigned_to=assigned_to,
                title=title,
                limit=limit,  # fetch per-column, we'll slice later
                offset=0,
                include_deleted=include_deleted,
            )
            all_tasks.extend(batch)

    # Apply offset/limit across aggregated results
    if offset:
        all_tasks = all_tasks[offset:]
    if limit is not None:
        all_tasks = all_tasks[:limit]
    return all_tasks


async def get_task(task_id: str, project_id: str) -> Dict[str, Any]:
    async with YouGileClient(core_auth.auth_manager) as client:
        task = await api_tasks.get_task(client, task_id)

        # Verify task belongs to the specified project by checking its column's board
        task_column_id = task.get("columnId")
        if not task_column_id:
            return {}

        # Build allowed columns for project
        project_boards = await api_boards.get_boards(client, project_id=project_id, limit=1000, offset=0)
        board_ids = [b.get("id") for b in project_boards if b.get("id")]
        allowed_columns: List[str] = []
        for bid in board_ids:
            cols = await api_columns.get_columns(client, board_id=bid)
            allowed_columns.extend([c.get("id") for c in cols if c.get("id")])

        if task_column_id not in allowed_columns:
            return {}

        return task
