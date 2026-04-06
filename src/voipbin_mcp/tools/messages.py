"""SMS message tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_messages(page_size: int = 10, page_token: str = "") -> str:
    """List all SMS messages in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/messages", params=params)
    return format_response(result)


@mcp.tool()
async def get_message(message_id: str) -> str:
    """Get details of a specific message.

    Args:
        message_id: The UUID of the message.
    """
    client = get_client()
    result = await client.get(f"/messages/{message_id}")
    return format_response(result)


@mcp.tool()
async def send_message(
    source_number: str,
    destination_number: str,
    text: str,
) -> str:
    """Send an SMS message.

    Args:
        source_number: Your VoIPbin phone number in E.164 format (e.g., "+14155551234").
        destination_number: Recipient phone number in E.164 format.
        text: The message text to send.
    """
    client = get_client()
    result = await client.post("/messages", json={
        "source": {"type": "tel", "target": source_number},
        "destinations": [{"type": "tel", "target": destination_number}],
        "text": text,
    })
    return format_response(result)
