"""VoIPbin MCP server entry point."""

import json

from mcp.server.fastmcp import FastMCP

from voipbin_mcp.client import VoIPbinClient

mcp = FastMCP("voipbin")

# Lazy-initialized client (created on first tool call)
_client: VoIPbinClient | None = None


def get_client() -> VoIPbinClient:
    """Get or create the VoIPbin HTTP client."""
    global _client
    if _client is None:
        _client = VoIPbinClient()
    return _client


def format_response(data: dict) -> str:
    """Format API response as readable JSON string."""
    return json.dumps(data, indent=2, default=str)


def validate_page_size(page_size: int) -> int:
    """Clamp page_size to a safe range (1–100)."""
    return max(1, min(page_size, 100))


# Import tools to register them on the mcp instance
import voipbin_mcp.tools  # noqa: E402, F401


def main():
    """Run the VoIPbin MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
