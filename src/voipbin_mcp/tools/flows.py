"""Flow management tools."""

from typing import Any

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_flows(page_size: int = 10, page_token: str = "") -> str:
    """List all flows in your VoIPbin account.

    Flows define call handling logic as a sequence of actions (answer, play, record, etc.).

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/flows", params=params)
    return format_response(result)


@mcp.tool()
async def get_flow(flow_id: str) -> str:
    """Get a flow by its ID, including its full action sequence.

    Args:
        flow_id: The UUID of the flow.
    """
    client = get_client()
    result = await client.get(f"/flows/{flow_id}")
    return format_response(result)


@mcp.tool()
async def create_flow(name: str, detail: str, actions: list[dict[str, Any]]) -> str:
    """Create a new flow with a sequence of actions.

    Each action has an id, next_id (for chaining), type, and option object.
    Action types include: answer, hangup, play, record, talk, echo, ivr, and more.

    Example actions for a simple answer-and-hangup flow:
    [
        {"id": "a1", "next_id": "a2", "type": "answer", "option": {}},
        {"id": "a2", "next_id": "", "type": "hangup", "option": {}}
    ]

    Args:
        name: Display name for the flow.
        detail: Description of the flow.
        actions: List of action objects defining the flow logic.
    """
    client = get_client()
    result = await client.post("/flows", json={
        "name": name,
        "detail": detail,
        "actions": actions,
    })
    return format_response(result)


@mcp.tool()
async def update_flow(flow_id: str, name: str, detail: str, actions: list[dict[str, Any]]) -> str:
    """Update an existing flow. All fields are required (full replacement).

    Args:
        flow_id: The UUID of the flow to update.
        name: New display name.
        detail: New description.
        actions: New list of action objects.
    """
    client = get_client()
    result = await client.put(f"/flows/{flow_id}", json={
        "name": name,
        "detail": detail,
        "actions": actions,
    })
    return format_response(result)


@mcp.tool()
async def delete_flow(flow_id: str) -> str:
    """Delete a flow.

    Args:
        flow_id: The UUID of the flow to delete.
    """
    client = get_client()
    result = await client.delete(f"/flows/{flow_id}")
    return format_response(result)
