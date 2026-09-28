"""Email tools."""

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_emails(page_size: int = 10, page_token: str = "") -> str:
    """List all emails in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
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
    attachments: list[dict] | None = None,
) -> str:
    """Send an email.

    Attachments reference something already stored in VoIPbin rather than
    carrying file bytes. Each entry accepts exactly two keys:

      reference_type  "recording" to attach a call recording, or "" for none.
      reference_id    UUID of the referenced object.

    Example:
        attachments=[{"reference_type": "recording", "reference_id": "<uuid>"}]

    Args:
        destination_email: Recipient email address.
        subject: Email subject line.
        content: Email body (HTML or plain text).
        attachments: Required by the API, defaulted here. See the format above;
            omit it to send an email with no attachments.
    """
    client = get_client()
    body: dict = {
        "destinations": [{"type": "email", "target": destination_email}],
        "subject": subject,
        "content": content,
    }
    if attachments is not None:
        body["attachments"] = attachments
    result = await client.post("/emails", json=body)
    return format_response(result)
