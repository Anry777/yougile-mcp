from typing import Any, Dict, List

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import projects as api_projects


async def list_projects() -> List[Dict[str, Any]]:
    async with YouGileClient(core_auth.auth_manager) as client:
        return await api_projects.get_projects(client)
