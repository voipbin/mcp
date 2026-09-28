"""Contact management tools."""

from typing import Any

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size

# Raised when a caller passes a 0.1.x parameter that no longer exists. The text
# is what the LLM sees, so it names the replacement explicitly.
_REMOVED_IN_0_2_0 = (
    "{name} was removed in voipbin-mcp 0.2.0 because the VoIPbin API never "
    "accepted it: {reason} Use {replacement} instead."
)


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
    source: str = "",
    external_id: str = "",
    notes: str = "",
    addresses: list[dict[str, Any]] | None = None,
    tag_ids: list[str] | None = None,
    phone_numbers: list[dict[str, Any]] | None = None,
    emails: list[dict[str, Any]] | None = None,
) -> str:
    """Create a new contact.

    Addresses are how a contact is reached. Each entry accepts exactly these
    keys; anything else is ignored by the server:

      type       Required. Either "tel" or "email". Any other value is
                 rejected with a 400.
      target     Required. The phone number (E.164) or email address.
      name       Optional label for this address.
      detail     Optional free-text detail.
      is_primary Optional boolean; marks the primary address of its type.

    Addresses and tags given here are written on a best-effort basis: if one
    fails (for example because the number already belongs to another contact),
    the API still creates the contact and answers success, with that address
    missing. To be certain an address was stored, create the contact first and
    then call add_contact_address, which reports a duplicate as an error.

    Example:
        addresses=[{"type": "tel", "target": "+14155551234", "is_primary": true}]

    Args:
        first_name: Contact's first name.
        last_name: Contact's last name.
        display_name: Display name (shown in UI).
        company: Company name.
        job_title: Job title.
        source: Where the contact came from. The values the platform uses are
            manual, import, api and sync, but no layer validates this field, so
            an unrecognised string is stored as given. Omit it to get manual.
        external_id: Your own identifier for this contact.
        notes: Free-text notes.
        addresses: List of address objects; see the format above.
        tag_ids: List of tag UUIDs to attach to the contact.
        phone_numbers: Removed in 0.2.0. Use addresses with type "tel".
        emails: Removed in 0.2.0. Use addresses with type "email".
    """
    if phone_numbers is not None:
        raise ValueError(
            _REMOVED_IN_0_2_0.format(
                name="phone_numbers",
                reason="the request body has no phone_numbers field, so the "
                "numbers were silently dropped and the contact was created "
                "without any way to reach it.",
                replacement='addresses=[{"type": "tel", "target": "+14155551234"}]',
            )
        )
    if emails is not None:
        raise ValueError(
            _REMOVED_IN_0_2_0.format(
                name="emails",
                reason="the request body has no emails field, so the addresses "
                "were silently dropped and the contact was created without any "
                "way to reach it.",
                replacement='addresses=[{"type": "email", "target": "user@example.com"}]',
            )
        )

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
    if source:
        body["source"] = source
    if external_id:
        body["external_id"] = external_id
    if notes:
        body["notes"] = notes
    if addresses:
        body["addresses"] = addresses
    if tag_ids:
        body["tag_ids"] = tag_ids
    result = await client.post("/contacts", json=body)
    return format_response(result)


@mcp.tool()
async def update_contact(
    contact_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    display_name: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    external_id: str | None = None,
    notes: str | None = None,
    fields: dict[str, Any] | None = None,
) -> str:
    """Update an existing contact.

    Only the arguments you pass are sent. Any argument you leave out keeps its
    current value on the server. Passing an empty string clears that field.

    Addresses and tags are not part of this call. Use add_contact_address,
    update_contact_address, delete_contact_address, add_contact_tag and
    delete_contact_tag for those.

    Args:
        contact_id: The UUID of the contact to update.
        first_name: Contact's first name.
        last_name: Contact's last name.
        display_name: Display name (shown in UI).
        company: Company name.
        job_title: Job title.
        external_id: Your own identifier for this contact.
        notes: Free-text notes.
        fields: Removed in 0.2.0. Pass the named arguments instead.
    """
    if fields is not None:
        raise ValueError(
            _REMOVED_IN_0_2_0.format(
                name="fields",
                reason="it advertised phone_numbers and emails keys that the "
                "update endpoint does not accept, and an unrecognised key was "
                "silently discarded behind a 200 response.",
                replacement="the named arguments of this tool "
                "(first_name, last_name, display_name, company, job_title, "
                "external_id, notes)",
            )
        )

    client = get_client()
    body: dict[str, Any] = {}
    for key, value in (
        ("first_name", first_name),
        ("last_name", last_name),
        ("display_name", display_name),
        ("company", company),
        ("job_title", job_title),
        ("external_id", external_id),
        ("notes", notes),
    ):
        if value is not None:
            body[key] = value

    result = await client.put(f"/contacts/{contact_id}", json=body)
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


@mcp.tool()
async def add_contact_address(
    contact_id: str,
    address_type: str,
    target: str,
    name: str | None = None,
    detail: str | None = None,
    is_primary: bool | None = None,
) -> str:
    """Add a way to reach a contact.

    Args:
        contact_id: The UUID of the contact.
        address_type: Either "tel" or "email". Any other value is rejected
            with a 400.
        target: The phone number (E.164) or email address.
        name: Optional label for this address.
        detail: Optional free-text detail.
        is_primary: Whether this becomes the primary address of its type.
    """
    client = get_client()
    body: dict[str, Any] = {"type": address_type, "target": target}
    if name is not None:
        body["name"] = name
    if detail is not None:
        body["detail"] = detail
    if is_primary is not None:
        body["is_primary"] = is_primary
    result = await client.post(f"/contacts/{contact_id}/addresses", json=body)
    return format_response(result)


@mcp.tool()
async def update_contact_address(
    contact_id: str,
    address_id: str,
    target: str | None = None,
    is_primary: bool | None = None,
) -> str:
    """Update one of a contact's addresses.

    Only the arguments you pass are sent.

    Two limitations, both enforced by the API rather than by this tool:
    the address type cannot be changed, and the name and detail labels cannot
    be changed either. To change any of those, delete the address and add it
    again with add_contact_address.

    Args:
        contact_id: The UUID of the contact.
        address_id: The UUID of the address to update.
        target: The phone number (E.164) or email address.
        is_primary: Whether this becomes the primary address of its type.
    """
    client = get_client()
    body: dict[str, Any] = {}
    for key, value in (
        ("target", target),
        ("is_primary", is_primary),
    ):
        if value is not None:
            body[key] = value
    result = await client.put(
        f"/contacts/{contact_id}/addresses/{address_id}", json=body
    )
    return format_response(result)


@mcp.tool()
async def delete_contact_address(contact_id: str, address_id: str) -> str:
    """Remove one of a contact's addresses.

    Args:
        contact_id: The UUID of the contact.
        address_id: The UUID of the address to remove.
    """
    client = get_client()
    result = await client.delete(f"/contacts/{contact_id}/addresses/{address_id}")
    return format_response(result)


@mcp.tool()
async def add_contact_tag(contact_id: str, tag_id: str) -> str:
    """Attach a tag to a contact.

    Args:
        contact_id: The UUID of the contact.
        tag_id: The UUID of the tag to attach.
    """
    client = get_client()
    result = await client.post(f"/contacts/{contact_id}/tags", json={"tag_id": tag_id})
    return format_response(result)


@mcp.tool()
async def delete_contact_tag(contact_id: str, tag_id: str) -> str:
    """Detach a tag from a contact.

    Args:
        contact_id: The UUID of the contact.
        tag_id: The UUID of the tag to detach.
    """
    client = get_client()
    result = await client.delete(f"/contacts/{contact_id}/tags/{tag_id}")
    return format_response(result)
