from typing import Any, Dict, List, Optional, Tuple

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import boards as api_boards
from src.api import columns as api_columns
from src.api import tasks as api_tasks
from src.api import users as api_users


def _norm_title(s: Optional[str]) -> str:
    return (s or "").strip().casefold()


async def _find_board_by_title(client: YouGileClient, project_id: str, title: str) -> Optional[Dict[str, Any]]:
    # Fetch boards by project and compare normalized titles to avoid API filter quirks
    boards = await api_boards.get_boards(client, project_id=project_id, limit=1000, offset=0)
    want = _norm_title(title)
    for b in boards:
        if _norm_title(b.get("title")) == want:
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
        "examined": len(src_tasks_all),
        "dry_run": dry_run,
    }


async def ensure_user_boards(
    project_id: str,
    target_title: str = "Незавершенные",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Ensure per-user boards exist for all users having tasks on target board.
    Creates boards titled exactly as user names.
    """
    async with YouGileClient(core_auth.auth_manager) as client:
        # 1) Find target board and its columns
        target_board = await _find_board_by_title(client, project_id, target_title)
        if not target_board:
            return {"success": False, "error": f"Target board '{target_title}' not found in project"}
        target_board_id = target_board.get("id")
        tgt_cols = await _get_columns_by_board(client, target_board_id)
        tgt_col_ids = [c.get("id") for c in tgt_cols if c.get("id")]

        # 2) List tasks on project filtered by target columns
        tasks = await _list_tasks_in_project(client, tgt_col_ids, include_deleted=False, max_fetch=5000)

        # 3) Collect assignee user IDs
        assignee_ids: set[str] = set()
        for t in tasks:
            if isinstance(t.get("assigned"), list) and t.get("assigned"):
                for uid in t["assigned"]:
                    if isinstance(uid, str):
                        assignee_ids.add(uid)
            elif isinstance(t.get("assignedUsers"), list):
                for u in t["assignedUsers"]:
                    uid = u.get("id") if isinstance(u, dict) else None
                    if isinstance(uid, str):
                        assignee_ids.add(uid)

        # 4) Map user id -> name
        users = await api_users.get_users(client)
        name_by_id: Dict[str, str] = {}
        for u in users:
            uid = u.get("id")
            name = u.get("name") or u.get("firstName") or u.get("email") or uid
            if isinstance(uid, str) and isinstance(name, str):
                name_by_id[uid] = name

        # 5) Ensure boards exist for each assignee
        created = 0
        skipped = 0
        processed: List[Dict[str, str]] = []
        for uid in sorted(assignee_ids):
            user_name = name_by_id.get(uid, uid)
            # Check board existence by user_name
            existing = await _find_board_by_title(client, project_id, user_name)
            if existing:
                skipped += 1
                # Ensure columns structure even for existing boards
                if not dry_run:
                    await _ensure_columns_structure(client, target_board_id, existing.get("id"))
                processed.append({"user_id": uid, "user_name": user_name, "board_id": existing.get("id")})
                continue
            # Not existing
            if dry_run:
                created += 1
                processed.append({"user_id": uid, "user_name": user_name, "board_id": None})
                continue
            # Use common ensure to create board (DRY)
            ensured = await _ensure_target_board(client, project_id, user_name)
            created += 1
            # Mirror columns from source target board (order + color)
            await _ensure_columns_structure(client, target_board_id, ensured.get("id"))
            processed.append({"user_id": uid, "user_name": user_name, "board_id": ensured.get("id")})

        return {
            "success": True,
            "project_id": project_id,
            "target_board": target_title,
            "users_detected": len(assignee_ids),
            "created": created,
            "skipped": skipped,
            "dry_run": dry_run,
            "details": processed,
        }


async def distribute_unfinished_by_user(
    project_id: str,
    target_title: str = "Незавершенные",
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Copy unfinished tasks from target board to each assignee's personal board.
    - For каждой задаче и назначенному пользователю создаёт копию на доске пользователя в соответствующей колонке.
    - Идемпотентность: не создаёт дубликаты, если в целевой колонке пользователя уже есть задача с таким же title.
    """
    async with YouGileClient(core_auth.auth_manager) as client:
        # 1) Target board and columns
        target_board = await _find_board_by_title(client, project_id, target_title)
        if not target_board:
            return {"success": False, "error": f"Target board '{target_title}' not found in project"}
        target_board_id = target_board.get("id")
        src_cols, _ = await _ensure_columns_structure(client, target_board_id, target_board_id)
        src_cols_by_id = {c.get("id"): c for c in src_cols}
        src_col_ids = list(src_cols_by_id.keys())

        # 2) Gather unfinished tasks from target board (project-wide pagination, filtered by target columns)
        src_tasks = await _list_tasks_in_project(client, src_col_ids, include_deleted=False, max_fetch=5000)
        # Filter: only unfinished
        src_tasks = [t for t in src_tasks if not t.get("completed", False)]

        # 3) Build users map
        users = await api_users.get_users(client)
        user_by_id = {u.get("id"): u for u in users if u.get("id")}

        # 4) Precompute per-user boards and existing titles per column
        per_user_board: Dict[str, Dict[str, Any]] = {}
        per_user_cols: Dict[str, List[Dict[str, Any]]] = {}
        per_user_col_by_title: Dict[str, Dict[str, Dict[str, Any]]] = {}
        per_user_existing_keys: Dict[str, set] = {}

        async def _ensure_user_board_and_index(uid: str) -> bool:
            user = user_by_id.get(uid)
            user_name = (user.get("name") or user.get("firstName") or user.get("email") or uid) if user else uid
            board = await _find_board_by_title(client, project_id, user_name)
            if not board:
                if dry_run:
                    # simulate existence for planning
                    per_user_board[uid] = {"id": None, "title": user_name}
                    per_user_cols[uid] = []
                    per_user_col_by_title[uid] = {}
                    per_user_existing_keys[uid] = set()
                    return True
                board = await _ensure_target_board(client, project_id, user_name)
            per_user_board[uid] = board
            if not dry_run:
                # Ensure columns mirror source board
                await _ensure_columns_structure(client, target_board_id, board.get("id"))
            cols = await _get_columns_by_board(client, board.get("id")) if board.get("id") else []
            per_user_cols[uid] = cols
            per_user_col_by_title[uid] = {c.get("title"): c for c in cols if c.get("title")}
            # Build existing keys (columnId, title)
            if board.get("id"):
                user_col_ids = [c.get("id") for c in cols if c.get("id")]
                existing_tasks = await _list_tasks_in_project(client, user_col_ids, include_deleted=False, max_fetch=5000)
                per_user_existing_keys[uid] = set((t.get("columnId"), t.get("title")) for t in existing_tasks if t.get("columnId") and t.get("title"))
            else:
                per_user_existing_keys[uid] = set()
            return True

        # Detect all assignees from source tasks
        assignee_ids: set[str] = set()
        for t in src_tasks:
            if isinstance(t.get("assigned"), list) and t.get("assigned"):
                assignee_ids.update([uid for uid in t["assigned"] if isinstance(uid, str)])
            elif isinstance(t.get("assignedUsers"), list):
                for u in t["assignedUsers"]:
                    uid = u.get("id") if isinstance(u, dict) else None
                    if isinstance(uid, str):
                        assignee_ids.add(uid)

        # Prepare boards and indexes
        for uid in sorted(assignee_ids):
            await _ensure_user_board_and_index(uid)

        created = 0
        skipped = 0
        examined = 0

        # 5) Distribute
        for task in src_tasks:
            examined += 1
            # find source column title
            src_col_title = None
            scid = task.get("columnId")
            if scid and scid in src_cols_by_id:
                src_col_title = src_cols_by_id[scid].get("title")
            # assignees of task
            task_assignees: List[str] = []
            if isinstance(task.get("assigned"), list) and task.get("assigned"):
                task_assignees = [uid for uid in task["assigned"] if isinstance(uid, str)]
            elif isinstance(task.get("assignedUsers"), list):
                for u in task["assignedUsers"]:
                    uid = u.get("id") if isinstance(u, dict) else None
                    if isinstance(uid, str):
                        task_assignees.append(uid)

            for uid in task_assignees:
                board = per_user_board.get(uid)
                if not board:
                    continue
                # map column by title
                target_col = None
                if src_col_title:
                    target_col = per_user_col_by_title.get(uid, {}).get(src_col_title)
                if not target_col:
                    # fallback: first column
                    cols = per_user_cols.get(uid, [])
                    target_col = cols[0] if cols else None
                if not target_col:
                    continue
                key = (target_col.get("id"), task.get("title"))
                if key in per_user_existing_keys.get(uid, set()):
                    skipped += 1
                    continue
                if dry_run:
                    created += 1
                    continue
                payload = _build_task_payload_for_copy(task, target_col.get("id"))
                await api_tasks.create_task(client, payload)
                created += 1
                per_user_existing_keys[uid].add(key)

        return {
            "success": True,
            "project_id": project_id,
            "target_board": target_title,
            "examined": examined,
            "created": created,
            "skipped": skipped,
            "dry_run": dry_run,
        }
