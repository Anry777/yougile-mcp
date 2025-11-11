import os
import sys
import json
from datetime import datetime
from typing import Any, Dict

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="YouGile Webhook Debug Server")

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # optional shared secret


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{ts} | {msg}", file=sys.stdout, flush=True)


@app.get("/health")
async def health() -> Dict[str, Any]:
    return {"ok": True}


@app.post("/webhook/yougile")
async def yougile_webhook(request: Request, x_webhook_secret: str | None = Header(default=None)):
    # Optional simple secret validation (not YouGile-specific). Set WEBHOOK_SECRET in env and pass header X-Webhook-Secret.
    if WEBHOOK_SECRET:
        if not x_webhook_secret or x_webhook_secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid webhook secret")

    try:
        payload = await request.json()
    except Exception:
        payload = {"raw": (await request.body()).decode("utf-8", errors="replace")}

    # Pretty print event to terminal
    evt = payload.get("event") if isinstance(payload, dict) else None
    obj_id = payload.get("id") if isinstance(payload, dict) else None
    _log(f"Webhook received: event={evt} id={obj_id}")
    _log(json.dumps(payload, ensure_ascii=False, indent=2))

    return JSONResponse({"success": True})
