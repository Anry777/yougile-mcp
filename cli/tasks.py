from typing import Any, Dict, List, Optional

# These imports rely on sys.path tweak in cli/__main__.py
from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import tasks as api_tasks
from src.api import boards as api_boards
from src.api import columns as api_columns
from src.api import chats as api_chats


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


def _norm_text(s: Optional[str]) -> str:
    return (s or "").strip().casefold()


async def get_task_comments_by_titles(
    project_id: str,
    board_title: str,
    column_title: str,
    task_title: str,
) -> List[Dict[str, Any]]:
    """Resolve board/column/task by titles and return chat messages (comments).
    Notes: chats API uses chatId == taskId per importer logic.
    """
    async with YouGileClient(core_auth.auth_manager) as client:
        # Find board by title within project
        boards = await api_boards.get_boards(client, project_id=project_id, limit=1000, offset=0)
        want_board = _norm_text(board_title)
        board = next((b for b in boards if _norm_text(b.get("title")) == want_board), None)
        if not board:
            return []
        # Find column by title within board
        cols = await api_columns.get_columns(client, board_id=board.get("id"))
        want_col = _norm_text(column_title)
        col = next((c for c in cols if _norm_text(c.get("title")) == want_col), None)
        if not col:
            return []
        # Find task by title within column
        tasks = await api_tasks.get_tasks(client, column_id=col.get("id"), title=task_title, limit=100, offset=0, include_deleted=False)
        want_task = _norm_text(task_title)
        task = next((t for t in tasks if _norm_text(t.get("title")) == want_task), None)
        if not task:
            return []
        task_id = task.get("id")
        if not task_id:
            return []
        # Fetch chat messages for the task (chatId == taskId)
        try:
            msgs = await api_chats.get_chat_messages(client, task_id)
        except Exception:
            msgs = []
        return msgs or []
