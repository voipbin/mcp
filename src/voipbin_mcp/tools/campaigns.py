"""Campaign tools."""

from typing import Any

from voipbin_mcp.server import mcp, get_client, format_response, validate_page_size


@mcp.tool()
async def list_campaigns(page_size: int = 10, page_token: str = "") -> str:
    """List all outbound campaigns.

    Campaigns automate outbound calling, SMS, or email to contact lists.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": validate_page_size(page_size)}
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
    outplan_id: str | None = None,
    outdial_id: str | None = None,
    queue_id: str | None = None,
    next_campaign_id: str | None = None,
) -> str:
    """Create a new outbound campaign.

    A campaign needs an outplan (the dialing schedule) and an outdial (the list
    of targets) to place calls. Omit either and the campaign is still created
    and answers success, but it never dials: the server skips validation for an
    empty reference, then finds no schedule or no targets at execution time.
    Create or look up those resources first and pass their UUIDs.

    queue_id behaves differently, and not the way it reads. It is where answered
    calls are delivered, and passing it ENABLES a pacing gate: the server then
    only dials while
    available_agents * service_level / 100 > calls_already_dialing,
    using integer division. Omitting queue_id skips that check entirely. So a
    queued campaign needs available_agents * service_level >= 100 before it
    dials at all, which the default service_level of 0 never satisfies, and
    neither does one available agent at service_level 50.

    Args:
        name: Campaign name.
        detail: Description.
        campaign_type: Type of campaign. One of: call, flow.
        actions: Flow actions to execute for each campaign contact.
        service_level: Target service level percentage, 0-100 (default 0).
            Only consulted when queue_id is set, and then
            available_agents * service_level must reach 100; see above.
        end_handle: What to do when the outdial list is exhausted. One of:
            stop, continue.
        outplan_id: UUID of the outplan that defines the dialing schedule.
            Without it the campaign never dials.
        outdial_id: UUID of the outdial list holding the targets. Without it
            the campaign never dials.
        queue_id: UUID of the queue that answered calls are delivered to.
            Passing it enables the service_level pacing gate described above;
            omitting it disables that gate.
        next_campaign_id: UUID of the campaign to chain to when this one ends.
    """
    client = get_client()
    body: dict[str, Any] = {
        "name": name,
        "detail": detail,
        "type": campaign_type,
        "actions": actions,
        "service_level": service_level,
        "end_handle": end_handle,
    }
    for key, value in (
        ("outplan_id", outplan_id),
        ("outdial_id", outdial_id),
        ("queue_id", queue_id),
        ("next_campaign_id", next_campaign_id),
    ):
        if value is not None:
            body[key] = value
    result = await client.post("/campaigns", json=body)
    return format_response(result)


@mcp.tool()
async def update_campaign(
    campaign_id: str,
    name: str | None = None,
    detail: str | None = None,
    campaign_type: str | None = None,
    service_level: int | None = None,
    end_handle: str | None = None,
    fields: dict[str, Any] | None = None,
) -> str:
    """Update a campaign.

    Only the arguments you pass are sent. Any argument you leave out keeps its
    current value on the server.

    This endpoint does not change a campaign's actions. Use
    update_campaign_actions for that.

    Args:
        campaign_id: The UUID of the campaign.
        name: Campaign name.
        detail: Description.
        campaign_type: Type of campaign. One of: call, flow.
        service_level: Target service level percentage, 0-100. Only consulted
            when the campaign has a queue; see create_campaign for how it
            gates dialing.
        end_handle: What to do when the outdial list is exhausted. One of:
            stop, continue.
        fields: Removed in 0.2.0. Pass the named arguments instead.
    """
    if fields is not None:
        raise ValueError(
            "fields was removed in voipbin-mcp 0.2.0 because it advertised an "
            "actions key that this endpoint does not accept, and an "
            "unrecognised key was silently discarded behind a 200 response. "
            "Use the named arguments of this tool (name, detail, "
            "campaign_type, service_level, end_handle), or "
            "update_campaign_actions to change actions."
        )

    client = get_client()
    body: dict[str, Any] = {}
    for key, value in (
        ("name", name),
        ("detail", detail),
        ("type", campaign_type),
        ("service_level", service_level),
        ("end_handle", end_handle),
    ):
        if value is not None:
            body[key] = value

    result = await client.put(f"/campaigns/{campaign_id}", json=body)
    return format_response(result)


@mcp.tool()
async def update_campaign_actions(
    campaign_id: str, actions: list[dict[str, Any]]
) -> str:
    """Replace the flow actions a campaign runs for each contact.

    Args:
        campaign_id: The UUID of the campaign.
        actions: The full list of flow actions. This replaces the existing
            list rather than appending to it.
    """
    client = get_client()
    result = await client.put(
        f"/campaigns/{campaign_id}/actions", json={"actions": actions}
    )
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
