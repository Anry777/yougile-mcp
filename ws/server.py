import os
import sys
import json
import logging
from pathlib import Path
from logging.handlers import RotatingFileHandler
from datetime import datetime
from typing import Any, Dict

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="YouGile Webhook Debug Server")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # optional shared secret


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("webhook_server")
    # Use LOG_LEVEL env if provided, else INFO
    level_name = (os.environ.get("LOG_LEVEL") or "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.handlers.clear()

    # logs/webhook_server.log with rotation
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "webhook_server.log"

    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler = RotatingFileHandler(str(log_file), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    file_handler.setLevel(logger.level)
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    console_fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S")
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)

    logger.info(f"Webhook logger initialized. Log file: {log_file}")
    return logger


logger = _setup_logger()


def _log(msg: str) -> None:
    logger.info(msg)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/webhook/yougile")
async def yougile_webhook(request: Request, x_webhook_secret: str | None = Header(default=None)):
    # Optional simple secret validation (not YouGile-specific). Set WEBHOOK_SECRET in env and pass header X-Webhook-Secret.
    if WEBHOOK_SECRET:
        if not x_webhook_secret or x_webhook_secret != WEBHOOK_SECRET:
            logger.warning("Invalid webhook secret")
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": (await request.body()).decode("utf-8", errors="replace")}

    # Pretty print event to terminal
    evt = payload.get("event") if isinstance(payload, dict) else None
    obj_id = payload.get("id") if isinstance(payload, dict) else None
    _log(f"Webhook received: event={evt} id={obj_id}")
    logger.info(json.dumps(payload, ensure_ascii=False, indent=2))

    return JSONResponse({"success": True})
