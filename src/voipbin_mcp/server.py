"""VoIPbin MCP server entry point."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voipbin")


def main():
    """Run the VoIPbin MCP server over stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
