#!/usr/bin/env python3
"""Wrapper script to run YouGile webhook server.

Starts FastAPI app from webhooks.server using uvicorn, fixing imports when run
from project root or as a daemon.
"""

import os
import sys
from pathlib import Path

import uvicorn

# Ensure project root is on sys.path so that "webhooks.server" is importable
PROJECT_ROOT = Path(__file__).parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    host = os.environ.get("YOUGILE_WEBHOOK_HOST", "0.0.0.0")
    port_str = os.environ.get("YOUGILE_WEBHOOK_PORT", "5533")
    try:
        port = int(port_str)
    except ValueError:
        port = 8001

    uvicorn.run(
        "webhooks.server:app",
        host=host,
        port=port,
        reload=os.environ.get("YOUGILE_WEBHOOK_RELOAD", "0") in {"1", "true", "True"},
    )


if __name__ == "__main__":
    main()
