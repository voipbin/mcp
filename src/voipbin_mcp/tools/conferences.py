"""Conference tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_conferences(page_size: int = 10, page_token: str = "") -> str:
    """List all conferences in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
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
    conference_type: str = "conference",
    timeout: int = 3600,
    pre_flow_id: str = "",
    post_flow_id: str = "",
) -> str:
    """Create a new conference.

    Args:
        name: Display name for the conference.
        detail: Description.
        conference_type: Required by the API, defaulted here. One of:
            conference, connect, queue. The server does not validate this
            field: an unrecognised value is stored as given, and the underlying
            bridge then behaves as connect.
        timeout: Conference lifetime in SECONDS (default 3600, one hour).
            0 means the conference is never auto-deleted. Any other value
            below 60 is replaced by the server default of 86400.
        pre_flow_id: Optional flow ID to execute when a participant joins.
        post_flow_id: Stored but NOT executed. The field is accepted and
            persisted, and the API answers success, but no code path runs it
            when a participant leaves. Do not rely on it for cleanup work.
            (pre_flow_id, by contrast, is genuinely executed on join.)
    """
    client = get_client()
    body: dict = {
        "type": conference_type,
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
