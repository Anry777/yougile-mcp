from typing import Any, Dict, List, Optional, Tuple

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import boards as api_boards
from src.api import columns as api_columns
from src.api import tasks as api_tasks


async def _find_board_by_title(client: YouGileClient, project_id: str, title: str) -> Optional[Dict[str, Any]]:
    boards = await api_boards.get_boards(client, project_id=project_id, title=title, limit=50, offset=0)
    for b in boards:
        if b.get("title") == title:
            return b
    return None


async def _ensure_target_board(client: YouGileClient, project_id: str, title: str) -> Dict[str, Any]:
    board = await _find_board_by_title(client, project_id, title)
    if board:
        return board
    # Create board
    board_data = {"title": title, "projectId": project_id}
    return await api_boards.create_board(client, board_data)


async def _get_columns_by_board(client: YouGileClient, board_id: str) -> List[Dict[str, Any]]:
    return await api_columns.get_columns(client, board_id=board_id)


async def _ensure_columns_structure(
    client: YouGileClient,
    source_board_id: str,
    target_board_id: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    src_cols = await _get_columns_by_board(client, source_board_id)
    tgt_cols = await _get_columns_by_board(client, target_board_id)

    tgt_by_title = {c.get("title"): c for c in tgt_cols if c.get("title")}

    # First, update colors on existing columns
    for sc in src_cols:
        title = sc.get("title")
        if not title:
            continue
        src_color = sc.get("color")
        valid_color = None
        if isinstance(src_color, int) and 1 <= src_color <= 16:
            valid_color = src_color
        if title in tgt_by_title and valid_color is not None:
            tgt_col = tgt_by_title[title]
            tgt_color = tgt_col.get("color")
            if tgt_color != valid_color:
                await api_columns.update_column(client, tgt_col.get("id"), {"color": valid_color})

    # Then, create missing columns in reverse order to preserve overall order
    missing_titles = [sc.get("title") for sc in src_cols if sc.get("title") and sc.get("title") not in tgt_by_title]
    for title in reversed(missing_titles):
        # find source column to get its color
        sc = next((c for c in src_cols if c.get("title") == title), None)
        if not sc:
            continue
        src_color = sc.get("color")
        valid_color = None
        if isinstance(src_color, int) and 1 <= src_color <= 16:
            valid_color = src_color
        payload = {"title": title, "boardId": target_board_id}
        if valid_color is not None:
            payload["color"] = valid_color
        created = await api_columns.create_column(client, payload)
        tgt_by_title[title] = created
    # Refresh target columns list
    tgt_cols = await _get_columns_by_board(client, target_board_id)
    return src_cols, tgt_cols


async def _list_tasks_in_project(
    client: YouGileClient,
    allowed_columns: List[str],
    include_deleted: bool = False,
    max_fetch: int = 5000,
) -> List[Dict[str, Any]]:
    """Paginate project tasks (no column filter at API level) and filter by allowed columns.
    This minimizes requests vs per-column scans and respects global throttling.
    """
    results: List[Dict[str, Any]] = []
    offset = 0
    page = 1000  # API max per request per existing code
    while len(results) < max_fetch:
        current_limit = min(page, max_fetch - len(results))
        batch = await api_tasks.get_tasks(client, limit=current_limit, offset=offset, include_deleted=include_deleted)
        if not batch:
            break
        # filter by columns of interest
        for t in batch:
            cid = t.get("columnId")
            if cid in allowed_columns:
                results.append(t)
        if len(batch) < current_limit:
            break
        offset += len(batch)
    return results


def _build_task_payload_for_copy(task: Dict[str, Any], target_column_id: str) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "title": task.get("title"),
        "columnId": target_column_id,
    }
    # Optional safe fields
    for key_src, key_dst in [
        ("description", "description"),
        ("assigned", "assigned"),
        ("deadline", "deadline"),
        ("timeTracking", "timeTracking"),
        ("stickers", "stickers"),
        ("checklists", "checklists"),
    ]:
        val = task.get(key_src)
        if val is not None:
            payload[key_dst] = val
    # Ensure completed is False
    payload["completed"] = False
    # Do not set archived or subtasks to avoid mismatches
    return payload


async def sync_unfinished(
    project_id: str,
    source_title: str = "Все задачи",
    target_title: str = "Незавершенные",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Ensure target board exists with source columns and copy only unfinished tasks."""
    async with YouGileClient(core_auth.auth_manager) as client:
        # Locate source board
        src_board = await _find_board_by_title(client, project_id, source_title)
        if not src_board:
            raise ValueError(f"Источник доски не найден: {source_title}")
        # Ensure target board
        tgt_board = await _ensure_target_board(client, project_id, target_title)

        # Ensure columns structure
        src_cols, tgt_cols = await _ensure_columns_structure(client, src_board.get("id"), tgt_board.get("id"))
        tgt_by_title = {c.get("title"): c for c in tgt_cols if c.get("title")}

        # Build existing titles in target to avoid duplicates (project-wide pagination)
        tgt_allowed_columns = [tc.get("id") for tc in tgt_cols if tc.get("id")]
        tgt_tasks = await _list_tasks_in_project(client, tgt_allowed_columns, include_deleted=False, max_fetch=5000)
        existing_titles = {(t.get("columnId"), t.get("title")) for t in tgt_tasks if t.get("title")}

        created_count = 0
        skipped_count = 0
        examined_count = 0

        # Gather all unfinished tasks from source board in one paginated scan
        src_allowed_columns = [sc.get("id") for sc in src_cols if sc.get("id")]
        src_tasks_all = await _list_tasks_in_project(client, src_allowed_columns, include_deleted=False, max_fetch=5000)
        # Iterate and copy by mapping to corresponding target column by title
        for task in src_tasks_all:
            examined_count += 1
            if task.get("completed"):
                continue
            # map source column title -> target column id
            src_col_id = task.get("columnId")
            src_col = next((c for c in src_cols if c.get("id") == src_col_id), None)
            if not src_col:
                continue
            target_col = tgt_by_title.get(src_col.get("title"))
            if not target_col:
                continue
            target_col_id = target_col.get("id")
            key = (target_col_id, task.get("title"))
            if key in existing_titles:
                skipped_count += 1
                continue
            if dry_run:
                created_count += 1
                continue
            payload = _build_task_payload_for_copy(task, target_col_id)
            await api_tasks.create_task(client, payload)
            created_count += 1
            existing_titles.add(key)

    return {
        "success": True,
        "project_id": project_id,
        "source_board": source_title,
        "target_board": target_title,
        "created": created_count,
        "skipped": skipped_count,
        "examined": examined_count,
        "dry_run": dry_run,
    }
