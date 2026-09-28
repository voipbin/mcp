"""VoIPbin MCP server entry point."""

import json
import os
from importlib.metadata import PackageNotFoundError, version

from mcp.server.fastmcp import FastMCP

from voipbin_mcp.client import VALID_AUTH_TRANSPORTS, VoIPbinClient

try:
    __version__ = version("voipbin-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

# FastMCP has no version parameter, and without one it advertises the mcp SDK's
# own version in serverInfo -- so a client asking which voipbin-mcp it is
# talking to gets, say, "1.30.0". That matters here: 0.1.x could not start at
# all, so the version the server reports is the first thing anyone debugging a
# broken install looks at. The underlying low-level Server does take a version
# (in both 1.2.0 and current releases), so set it there. Guarded because it is a
# private attribute: a future rename must not stop the server from starting.
mcp = FastMCP("voipbin")
try:
    mcp._mcp_server.version = __version__
except AttributeError:  # pragma: no cover - SDK internals moved
    pass

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
