"""Call management tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_calls(page_size: int = 10, page_token: str = "") -> str:
    """List all calls in your VoIPbin account.

    Returns a paginated list of calls with their status, source/destination numbers,
    direction, and timestamps.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/calls", params=params)
    return format_response(result)


@mcp.tool()
async def get_call(call_id: str) -> str:
    """Get detailed information about a specific call.

    Returns the call's status, source, destination, direction, recording IDs,
    timestamps, and current action being executed.

    Args:
        call_id: The UUID of the call to retrieve.
    """
    client = get_client()
    result = await client.get(f"/calls/{call_id}")
    return format_response(result)


@mcp.tool()
async def create_call(
    source_type: str,
    source_target: str,
    destination_type: str,
    destination_target: str,
    flow_id: str = "",
) -> str:
    """Create a new outbound call.

    Args:
        source_type: Source address type. One of: tel, sip, agent.
        source_target: Source address (e.g., "+14155551234" for tel, UUID for agent).
        destination_type: Destination address type. One of: tel, sip, agent.
        destination_target: Destination address (e.g., "+14155555678" for tel).
        flow_id: Optional flow ID to execute when the call connects.
    """
    client = get_client()
    body: dict = {
        "source": {"type": source_type, "target": source_target},
        "destinations": [{"type": destination_type, "target": destination_target}],
    }
    if flow_id:
        body["flow_id"] = flow_id
    result = await client.post("/calls", json=body)
    return format_response(result)


@mcp.tool()
async def hangup_call(call_id: str) -> str:
    """Hang up an active call.

    Args:
        call_id: The UUID of the call to hang up.
    """
    client = get_client()
    result = await client.post(f"/calls/{call_id}/hangup")
    return format_response(result)
