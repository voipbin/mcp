"""Customer tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def get_customer() -> str:
    """Get your VoIPbin customer account information.

    Returns account details including name, billing info, and webhook configuration.
    """
    client = get_client()
    result = await client.get("/customer")
    return format_response(result)
