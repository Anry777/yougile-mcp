from typing import Any, Dict, List

from src.core.client import YouGileClient
from src.core import auth as core_auth
from src.api import webhooks as api_webhooks


async def create(url: str, event: str) -> Dict[str, Any]:
    """Create webhook subscription with given url and event."""
    async with YouGileClient(core_auth.auth_manager) as client:
        data = {"url": url, "event": event}
        result = await api_webhooks.create_webhook(client, data)
        return result


async def list_all() -> List[Dict[str, Any]]:
    """List all webhook subscriptions in the company."""
    async with YouGileClient(core_auth.auth_manager) as client:
        result = await api_webhooks.get_webhooks(client)
        # API may return dict; normalize to list of content if needed
        if isinstance(result, dict) and "content" in result:
            return result.get("content", [])
        return result if isinstance(result, list) else []


async def delete(webhook_id: str) -> Dict[str, Any]:
    """Mark webhook subscription as deleted by ID.
    Uses update endpoint with deleted=true for compatibility.
    """
    async with YouGileClient(core_auth.auth_manager) as client:
        payload = {"deleted": True}
        result = await api_webhooks.update_webhook(client, webhook_id, payload)
        return result


async def update(
    webhook_id: str,
    url: str | None = None,
    event: str | None = None,
    disabled: bool | None = None,
    deleted: bool | None = None,
) -> Dict[str, Any]:
    """Update webhook subscription fields.
    Only provided fields are sent to API.
    """
    payload: Dict[str, Any] = {}
    if url is not None:
        payload["url"] = url
    if event is not None:
        payload["event"] = event
    if disabled is not None:
        payload["disabled"] = disabled
    if deleted is not None:
        payload["deleted"] = deleted
    async with YouGileClient(core_auth.auth_manager) as client:
        return await api_webhooks.update_webhook(client, webhook_id, payload)


async def delete_all() -> Dict[str, Any]:
    """Mark all webhook subscriptions as deleted.
    Returns summary with processed IDs and counts.
    """
    processed: List[str] = []
    errors: List[Dict[str, Any]] = []
    async with YouGileClient(core_auth.auth_manager) as client:
        hooks = await api_webhooks.get_webhooks(client, include_deleted=False)
        # Normalize in case API returns dict with content
        if isinstance(hooks, dict) and "content" in hooks:
            hooks = hooks.get("content", [])
        for h in hooks or []:
            wid = h.get("id")
            if not wid:
                continue
            try:
                await api_webhooks.update_webhook(client, wid, {"deleted": True})
                processed.append(wid)
            except Exception as e:
                errors.append({"id": wid, "error": str(e)})
    return {"deleted": processed, "deleted_count": len(processed), "errors": errors}
