"""Email tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_emails(page_size: int = 10, page_token: str = "") -> str:
    """List all emails in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/emails", params=params)
    return format_response(result)


@mcp.tool()
async def get_email(email_id: str) -> str:
    """Get details of a specific email.

    Args:
        email_id: The UUID of the email.
    """
    client = get_client()
    result = await client.get(f"/emails/{email_id}")
    return format_response(result)


@mcp.tool()
async def send_email(
    destination_email: str,
    subject: str,
    content: str,
) -> str:
    """Send an email.

    Args:
        destination_email: Recipient email address.
        subject: Email subject line.
        content: Email body (HTML or plain text).
    """
    client = get_client()
    result = await client.post("/emails", json={
        "destinations": [{"type": "email", "target": destination_email}],
        "subject": subject,
        "content": content,
    })
    return format_response(result)
