"""YouGile Webhooks API client."""

from typing import Dict, Any, List
from ..core.client import YouGileClient
from ..utils.validation import validate_uuid


async def create_webhook(client: YouGileClient, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new webhook subscription.

    Args:
        client (YouGileClient): The client instance.
        webhook_data (Dict[str, Any]): The webhook data.

    Returns:
        Dict[str, Any]: The created webhook subscription.
    """
    return await client.post("/webhooks", json=webhook_data)


async def get_webhooks(client: YouGileClient, include_deleted: bool = False) -> List[Dict[str, Any]]:
    """Retrieve a list of webhook subscriptions.

    Args:
        client (YouGileClient): The client instance.

    Returns:
        List[Dict[str, Any]]: A list of webhook subscriptions.
    """
    params = {"includeDeleted": include_deleted}
    return await client.get("/webhooks", params=params)


async def update_webhook(client: YouGileClient, webhook_id: str, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing webhook subscription.

    Args:
        client (YouGileClient): The client instance.
        webhook_id (str): The ID of the webhook subscription.
        webhook_data (Dict[str, Any]): The updated webhook data.

    Returns:
        Dict[str, Any]: The updated webhook subscription.
    """
    return await client.put(f"/webhooks/{validate_uuid(webhook_id, 'webhook_id')}", json=webhook_data)


async def delete_webhook(client: YouGileClient, webhook_id: str) -> Dict[str, Any]:
    """Delete a webhook subscription.

    Args:
        client (YouGileClient): The client instance.
        webhook_id (str): The ID of the webhook subscription.

    Returns:
        Dict[str, Any]: The deleted webhook subscription.
    """
    return await client.delete(f"/webhooks/{validate_uuid(webhook_id, 'webhook_id')}")