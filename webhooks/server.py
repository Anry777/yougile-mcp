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

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import webhooks as api_webhooks
from . import db
from .models import WebhookEvent

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


@app.on_event("startup")
async def init_webhook_db() -> None:
    """Инициализация отдельной БД для хранения событий вебхуков."""
    db_url = (
        os.environ.get("YOUGILE_WEBHOOK_DB_URL")
        or "sqlite+aiosqlite:///data/yougile_webhooks.db"
    )
    db.init_engine(db_url)
    if db.async_engine is not None:
        async with db.async_engine.begin() as conn:
            await conn.run_sync(db.Base.metadata.create_all)
        logger.info(f"Webhook DB initialized at {db_url}")
    else:
        logger.error("async_engine is not initialized after init_engine call")
        return

    # После инициализации БД пробуем проверить наличие подписки на вебхуки
    public_url = os.environ.get("YOUGILE_WEBHOOK_PUBLIC_URL")
    if not public_url:
        logger.info("YOUGILE_WEBHOOK_PUBLIC_URL is not set; skipping webhook subscription check")
        return

    api_key = (
        os.environ.get("YOUGILE_API_KEY")
        or os.environ.get("yougile_api_key")
    )
    company_id = (
        os.environ.get("YOUGILE_COMPANY_ID")
        or os.environ.get("yougile_company_id")
    )
    if not api_key or not company_id:
        logger.warning("Cannot check webhook subscription: YOUGILE_API_KEY/YOUGILE_COMPANY_ID are not configured")
        return

    try:
        core_auth.auth_manager.set_credentials(api_key, company_id)
    except Exception as e:
        logger.warning(f"Failed to set auth credentials for webhook check: {e}")
        return

    try:
        async with YouGileClient(core_auth.auth_manager) as client:
            hooks = await api_webhooks.get_webhooks(client, include_deleted=False)
        if isinstance(hooks, dict) and "content" in hooks:
            hooks = hooks.get("content", [])
        exists = False
        for h in hooks or []:
            if not isinstance(h, dict):
                continue
            if h.get("deleted"):
                continue
            if h.get("url") == public_url:
                exists = True
                break
        if exists:
            logger.info(f"Webhook subscription for {public_url} is present")
        else:
            logger.warning(
                f"No active webhook subscription for {public_url}. "
                "Use CLI 'python -m cli webhooks create --url ... --event task-*' to register."
            )
    except Exception as e:
        logger.warning(f"Failed to check webhook subscriptions: {e}")


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

    # Pretty print event to terminal with concise summary
    evt = payload.get("event") if isinstance(payload, dict) else None
    new = payload.get("payload") if isinstance(payload, dict) else None
    old = payload.get("prevData") if isinstance(payload, dict) else None
    task_id = None
    if isinstance(new, dict):
        task_id = new.get("id") or task_id
    if isinstance(old, dict):
        task_id = task_id or old.get("id")

    title_new = new.get("title") if isinstance(new, dict) else None
    title_old = old.get("title") if isinstance(old, dict) else None
    col_new = new.get("columnId") if isinstance(new, dict) else None
    col_old = old.get("columnId") if isinstance(old, dict) else None

    summary_bits = []
    if title_old or title_new:
        if title_old and title_new and title_old != title_new:
            summary_bits.append(f"title: '{title_old}' -> '{title_new}'")
        elif title_new:
            summary_bits.append(f"title: '{title_new}'")
        elif title_old:
            summary_bits.append(f"title: '{title_old}'")
    if col_old or col_new:
        if col_old and col_new and col_old != col_new:
            summary_bits.append(f"column: {col_old} -> {col_new}")
        elif col_new:
            summary_bits.append(f"column: {col_new}")
        elif col_old:
            summary_bits.append(f"column: {col_old}")

    summary = "; ".join(summary_bits) if summary_bits else ""
    _log(f"Webhook received: event={evt} id={task_id} {summary}")

    # Sanitize large/noisy fields before pretty-printing full payload
    def _sanitize(d: Any) -> Any:
        try:
            if not isinstance(d, dict):
                return d
            d2 = dict(d)
            for key in ("payload", "prevData"):
                if isinstance(d2.get(key), dict):
                    inner = dict(d2[key])
                    dl = inner.get("deadline")
                    if isinstance(dl, dict) and "history" in dl:
                        dl2 = dict(dl)
                        # keep only the last item in history to reduce noise
                        hist = dl2.get("history")
                        if isinstance(hist, list) and len(hist) > 1:
                            dl2["history"] = hist[-1:]
                        inner["deadline"] = dl2
                    # Avoid logging massive subtasks arrays fully
                    st = inner.get("subtasks")
                    if isinstance(st, list) and len(st) > 20:
                        inner["subtasks"] = st[:20] + ["…", f"total={len(st)}"]
                    d2[key] = inner
            return d2
        except Exception:
            return d

    logger.info(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2))

    # Сохраняем событие в отдельную БД вебхуков
    try:
        if db.async_session is not None:
            async with db.async_session() as session:
                entity_id = None
                if isinstance(new, dict):
                    entity_id = new.get("id") or entity_id
                if isinstance(old, dict):
                    entity_id = entity_id or old.get("id")
                entity_type = None
                if isinstance(new, dict):
                    entity_type = new.get("entityType") or entity_type
                if not entity_type and isinstance(payload, dict):
                    entity_type = payload.get("entityType")
                event = WebhookEvent(
                    source="yougile",
                    event_type=evt if isinstance(evt, str) else None,
                    entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None,
                    event_external_id=None,
                    received_at=datetime.utcnow(),
                    processed=False,
                    retry_count=0,
                    error=None,
                    payload=payload,
                )
                session.add(event)
                await session.commit()
        else:
            logger.warning("async_session is not initialized for webhook DB, skipping event persistence")
    except Exception as e:
        logger.exception(f"Failed to persist webhook event: {e}")

    return JSONResponse({"success": True})
