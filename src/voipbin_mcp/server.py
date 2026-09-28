"""VoIPbin MCP server entry point."""

import json
import os

from mcp.server.fastmcp import FastMCP

from voipbin_mcp.client import VALID_AUTH_TRANSPORTS, VoIPbinClient

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
    """Clamp page_size to a safe integer range (1–100)."""
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 10
    return max(1, min(page_size, 100))


# Import tools to register them on the mcp instance
import voipbin_mcp.tools  # noqa: E402, F401


def main():
    """Run the VoIPbin MCP server over stdio."""
    # Validate the auth transport before serving. The HTTP client is built
    # lazily on the first tool call, so without this an unrecognised value
    # would not surface until a tool was invoked.
    transport = os.environ.get("VOIPBIN_AUTH_TRANSPORT")
    if transport is not None and transport.strip().lower() not in VALID_AUTH_TRANSPORTS:
        raise SystemExit(
            f"VOIPBIN_AUTH_TRANSPORT must be one of "
            f"{', '.join(VALID_AUTH_TRANSPORTS)}; got {transport!r}. "
            "Leave it unset to send the key as a cookie."
        )

    mcp.run(transport="stdio")


if __name__ == "__main__":
    # Running this file as a script (python -m voipbin_mcp.server) executes it
    # under the name __main__, which creates a SECOND FastMCP instance while
    # the @mcp.tool() decorators register on the separately imported
    # voipbin_mcp.server. Delegate to the imported module so the server that
    # runs is the one the tools are attached to.
    from voipbin_mcp.server import main as _main

    _main()
