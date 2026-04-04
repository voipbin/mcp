"""Tag tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_tags(page_size: int = 10, page_token: str = "") -> str:
    """List all tags in your VoIPbin account.

    Tags can be applied to contacts, calls, and other resources for organization.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/tags", params=params)
    return format_response(result)


@mcp.tool()
async def get_tag(tag_id: str) -> str:
    """Get details of a specific tag.

    Args:
        tag_id: The UUID of the tag.
    """
    client = get_client()
    result = await client.get(f"/tags/{tag_id}")
    return format_response(result)
