"""Route tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_routes(page_size: int = 10, page_token: str = "") -> str:
    """List provider routing rules. Requires project superadmin permission.

    Routes are a platform-level resource. A customer access key cannot read
    them, so this tool answers 403 PERMISSION_DENIED unless your key carries
    project superadmin permission.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/routes", params=params)
    return format_response(result)


@mcp.tool()
async def get_route(route_id: str) -> str:
    """Get details of a specific route. Requires project superadmin permission.

    Routes are a platform-level resource. A customer access key cannot read
    them, so this tool answers 403 PERMISSION_DENIED unless your key carries
    project superadmin permission.

    Args:
        route_id: The UUID of the route.
    """
    client = get_client()
    result = await client.get(f"/routes/{route_id}")
    return format_response(result)
