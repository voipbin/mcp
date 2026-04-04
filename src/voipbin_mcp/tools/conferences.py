"""Conference tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_conferences(page_size: int = 10, page_token: str = "") -> str:
    """List all conferences in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/conferences", params=params)
    return format_response(result)


@mcp.tool()
async def get_conference(conference_id: str) -> str:
    """Get details of a specific conference.

    Args:
        conference_id: The UUID of the conference.
    """
    client = get_client()
    result = await client.get(f"/conferences/{conference_id}")
    return format_response(result)


@mcp.tool()
async def create_conference(
    name: str,
    detail: str = "",
    timeout: int = 3600000,
    pre_flow_id: str = "",
    post_flow_id: str = "",
) -> str:
    """Create a new conference.

    Args:
        name: Display name for the conference.
        detail: Description.
        timeout: Conference timeout in milliseconds (default 1 hour).
        pre_flow_id: Optional flow ID to execute when a participant joins.
        post_flow_id: Optional flow ID to execute when a participant leaves.
    """
    client = get_client()
    body: dict = {
        "type": "conference",
        "name": name,
        "detail": detail,
        "timeout": timeout,
        "data": {},
    }
    if pre_flow_id:
        body["pre_flow_id"] = pre_flow_id
    if post_flow_id:
        body["post_flow_id"] = post_flow_id
    result = await client.post("/conferences", json=body)
    return format_response(result)


@mcp.tool()
async def delete_conference(conference_id: str) -> str:
    """Delete a conference.

    Args:
        conference_id: The UUID of the conference to delete.
    """
    client = get_client()
    result = await client.delete(f"/conferences/{conference_id}")
    return format_response(result)
