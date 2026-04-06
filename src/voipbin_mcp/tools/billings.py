"""Billing tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_billings(page_size: int = 10, page_token: str = "") -> str:
    """List all billing records in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/billings", params=params)
    return format_response(result)


@mcp.tool()
async def get_billing(billing_id: str) -> str:
    """Get details of a specific billing record.

    Args:
        billing_id: The UUID of the billing record.
    """
    client = get_client()
    result = await client.get(f"/billings/{billing_id}")
    return format_response(result)
