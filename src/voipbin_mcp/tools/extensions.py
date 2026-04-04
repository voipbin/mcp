"""Extension tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_extensions(page_size: int = 10, page_token: str = "") -> str:
    """List all extensions in your VoIPbin account.

    Extensions are internal phone lines assigned to agents.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/extensions", params=params)
    return format_response(result)


@mcp.tool()
async def get_extension(extension_id: str) -> str:
    """Get details of a specific extension.

    Args:
        extension_id: The UUID of the extension.
    """
    client = get_client()
    result = await client.get(f"/extensions/{extension_id}")
    return format_response(result)
