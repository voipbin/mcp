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
    destination_number: str,
    text: str,
    source_number: str | None = None,
) -> str:
    """Send an SMS message.

    The sender number is optional. Omit source_number to send from the
    default sender number configured for your account. If the account has no
    default sender number, the API rejects the request with SOURCE_REQUIRED
    (400). A source_number that is empty or only whitespace counts as omitted.

    A source_number that is given is sent exactly as written, without
    trimming, and must be a normal, active phone number that belongs to your
    account, in E.164 format. Otherwise the API rejects the request:
    SOURCE_INVALID (400) for a malformed number, SOURCE_NOT_OWNED (403) for a
    number that is not an active, normal number of your account,
    SOURCE_LOOKUP_FAILED (500) when the number could not be verified. A default sender number that is no longer a normal,
    active number of your account is rejected with DEFAULT_SOURCE_INVALID
    (409).

    Argument order: destination_number and text come first, and
    source_number is last. Positional calls written for the earlier order
    (source_number, destination_number, text) now send to the wrong numbers,
    so pass source_number by name.

    Args:
        destination_number: Recipient phone number in E.164 format.
        text: The message text to send.
        source_number: Optional sender phone number in E.164 format (e.g., "+141****1234"). Omit it to use the account's default sender number.
    """
    client = get_client()
    body: dict = {
        "destinations": [{"type": "tel", "target": destination_number}],
        "text": text,
    }
    if source_number is not None and source_number.strip():
        body["source"] = {"type": "tel", "target": source_number}
    result = await client.post("/messages", json=body)
    return format_response(result)
