"""Phone number tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_numbers(page_size: int = 10, page_token: str = "") -> str:
    """List all phone numbers in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/numbers", params=params)
    return format_response(result)


@mcp.tool()
async def get_number(number_id: str) -> str:
    """Get details of a specific phone number.

    Args:
        number_id: The UUID of the number.
    """
    client = get_client()
    result = await client.get(f"/numbers/{number_id}")
    return format_response(result)
