"""Agent tools."""

from voipbin_mcp.server import mcp, get_client, format_response


@mcp.tool()
async def list_agents(page_size: int = 10, page_token: str = "") -> str:
    """List all agents in your VoIPbin account.

    Agents are users who can handle calls, messages, and other interactions.

    Args:
        page_size: Number of results per page (default 10).
        page_token: Pagination cursor from a previous response.
    """
    client = get_client()
    params = {"page_size": page_size}
    if page_token:
        params["page_token"] = page_token
    result = await client.get("/agents", params=params)
    return format_response(result)


@mcp.tool()
async def get_agent(agent_id: str) -> str:
    """Get details of a specific agent.

    Args:
        agent_id: The UUID of the agent.
    """
    client = get_client()
    result = await client.get(f"/agents/{agent_id}")
    return format_response(result)
