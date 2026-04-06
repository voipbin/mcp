"""Queue tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_queues(page_size: int = 10, page_token: str = "") -> str:
    """List all call queues in your VoIPbin account.

    Queues hold incoming calls and distribute them to available agents.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/queues", params=params)
    return format_response(result)


@mcp.tool()
async def get_queue(queue_id: str) -> str:
    """Get details of a specific queue.

    Args:
        queue_id: The UUID of the queue.
    """
    client = get_client()
    result = await client.get(f"/queues/{queue_id}")
    return format_response(result)
