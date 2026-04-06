"""Contact management tools."""

from typing import Any

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_contacts(page_size: int = 10, page_token: str = "") -> str:
    """List all contacts in your VoIPbin account.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/contacts", params=params)
    return format_response(result)


@mcp.tool()
async def get_contact(contact_id: str) -> str:
    """Get details of a specific contact.

    Args:
        contact_id: The UUID of the contact.
    """
    client = get_client()
    result = await client.get(f"/contacts/{contact_id}")
    return format_response(result)


@mcp.tool()
async def create_contact(
    first_name: str = "",
    last_name: str = "",
    display_name: str = "",
    company: str = "",
    job_title: str = "",
    notes: str = "",
    phone_numbers: list[dict[str, Any]] | None = None,
    emails: list[dict[str, Any]] | None = None,
) -> str:
    """Create a new contact.

    Phone number format: [{"number": "+14155551234", "type": "mobile", "is_primary": true}]
    Email format: [{"address": "user@example.com", "type": "work", "is_primary": true}]

    Args:
        first_name: Contact's first name.
        last_name: Contact's last name.
        display_name: Display name (shown in UI).
        company: Company name.
        job_title: Job title.
        notes: Free-text notes.
        phone_numbers: List of phone number objects with number, type, and is_primary.
        emails: List of email objects with address, type, and is_primary.
    """
    client = get_client()
    body: dict[str, Any] = {}
    if first_name:
        body["first_name"] = first_name
    if last_name:
        body["last_name"] = last_name
    if display_name:
        body["display_name"] = display_name
    if company:
        body["company"] = company
    if job_title:
        body["job_title"] = job_title
    if notes:
        body["notes"] = notes
    if phone_numbers:
        body["phone_numbers"] = phone_numbers
    if emails:
        body["emails"] = emails
    result = await client.post("/contacts", json=body)
    return format_response(result)


@mcp.tool()
async def update_contact(contact_id: str, fields: dict[str, Any]) -> str:
    """Update an existing contact.

    Pass a dictionary of fields to update. Valid keys: first_name, last_name,
    display_name, company, job_title, notes, phone_numbers, emails.

    Args:
        contact_id: The UUID of the contact to update.
        fields: Dictionary of fields to update.
    """
    client = get_client()
    result = await client.put(f"/contacts/{contact_id}", json=fields)
    return format_response(result)


@mcp.tool()
async def delete_contact(contact_id: str) -> str:
    """Delete a contact.

    Args:
        contact_id: The UUID of the contact to delete.
    """
    client = get_client()
    result = await client.delete(f"/contacts/{contact_id}")
    return format_response(result)
