import os
from typing import Any, Dict, List, Optional

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import auth as api_auth


async def list_keys(login: str, password: str, company_id: str) -> List[Dict[str, Any]]:
    async with YouGileClient(core_auth.auth_manager) as client:
        return await api_auth.get_keys(client, login=login, password=password, company_id=company_id)


def _project_root() -> str:
    # cli/ is sibling of src/, project root is parent of cli/
    return os.path.dirname(os.path.dirname(__file__))


def _env_path() -> str:
    return os.path.join(_project_root(), ".env")


def write_api_key_to_env(api_key: str) -> None:
    """Write or replace YOUGILE_API_KEY in .env file."""
    path = _env_path()
    lines: List[str] = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    key_written = False
    new_lines: List[str] = []
    for line in lines:
        if line.strip().startswith("YOUGILE_API_KEY") and "=" in line:
            new_lines.append(f"YOUGILE_API_KEY = \"{api_key}\"")
            key_written = True
        else:
            new_lines.append(line)
    if not key_written:
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"YOUGILE_API_KEY = \"{api_key}\"")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(new_lines) + "\n")


async def set_api_key_from_latest(login: str, password: str, company_id: str) -> Optional[str]:
    """Fetch keys and store the latest by timestamp into .env. Returns the key used."""
    keys = await list_keys(login, password, company_id)
    if not keys:
        return None
    latest = max(keys, key=lambda k: k.get("timestamp", 0) or 0)
    key_value = latest.get("key")
    if not key_value:
        return None
    write_api_key_to_env(key_value)
    return key_value
