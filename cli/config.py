import os
from typing import Optional

# Default project scope for CLI
PROJECT_ID_DEFAULT = "07f0ce13-43b1-4723-aeb8-681642009d01"


def resolve_project_id(cli_value: Optional[str]) -> str:
    """Resolve project id with precedence: CLI > env > default.
    Supports both upper/lower-case env var names for convenience.
    """
    return (
        cli_value
        or os.environ.get("YOUGILE_PROJECT_ID")
        or os.environ.get("yougile_project_id")
        or PROJECT_ID_DEFAULT
    )
