"""Campaign tools."""

from typing import Any

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_campaigns(page_size: int = 10, page_token: str = "") -> str:
    """List all outbound campaigns.

    Campaigns automate outbound calling, SMS, or email to contact lists.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/campaigns", params=params)
    return format_response(result)


@mcp.tool()
async def get_campaign(campaign_id: str) -> str:
    """Get details of a specific campaign.

    Args:
        campaign_id: The UUID of the campaign.
    """
    client = get_client()
    result = await client.get(f"/campaigns/{campaign_id}")
    return format_response(result)


@mcp.tool()
async def create_campaign(
    name: str,
    detail: str,
    campaign_type: str,
    actions: list[dict[str, Any]],
    service_level: int = 0,
    end_handle: str = "stop",
) -> str:
    """Create a new outbound campaign.

    Args:
        name: Campaign name.
        detail: Description.
        campaign_type: Type of campaign: "call", "sms", or "email".
        actions: Flow actions to execute for each campaign contact.
        service_level: Service level target in milliseconds (default 0).
        end_handle: What to do when campaign ends: "stop", "loop", or "next".
    """
    client = get_client()
    result = await client.post("/campaigns", json={
        "name": name,
        "detail": detail,
        "type": campaign_type,
        "actions": actions,
        "service_level": service_level,
        "end_handle": end_handle,
    })
    return format_response(result)


@mcp.tool()
async def update_campaign(campaign_id: str, fields: dict[str, Any]) -> str:
    """Update a campaign.

    Args:
        campaign_id: The UUID of the campaign.
        fields: Dictionary of fields to update (name, detail, actions, etc.).
    """
    client = get_client()
    result = await client.put(f"/campaigns/{campaign_id}", json=fields)
    return format_response(result)


@mcp.tool()
async def delete_campaign(campaign_id: str) -> str:
    """Delete a campaign.

    Args:
        campaign_id: The UUID of the campaign.
    """
    client = get_client()
    result = await client.delete(f"/campaigns/{campaign_id}")
    return format_response(result)
