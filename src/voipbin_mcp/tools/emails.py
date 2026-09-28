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

      reference_type  Must be "recording". No other value attaches anything.
      reference_id    UUID of the recording to attach.

    Example:
        attachments=[{"reference_type": "recording", "reference_id": "<uuid>"}]

    Attachments are resolved AFTER the API has answered success: the send runs
    in the background, so nothing about an attachment is reported back. What
    happens to an unresolvable attachment (an unsupported reference_type, or a
    reference_id that does not exist) depends on which provider handles the
    message. The primary logs it and sends the email without it; the fallback,
    used when the primary fails, treats it as an error and sends NOTHING. So a
    success response here confirms only that the email was accepted, never that
    an attachment was included, and never that the email went out at all. To
    send with no attachments, omit this argument entirely rather than passing a
    placeholder entry.

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
