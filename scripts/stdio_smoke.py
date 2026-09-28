#!/usr/bin/env python3
"""Drive the installed MCP server over stdio and assert it serves every tool.

An import check cannot catch a server that dies as soon as it handles traffic,
which is what the 0.1.1 release did. And a bare `initialize` handshake cannot
catch a server that answers politely while serving zero tools. So this speaks
the real protocol: initialize, then tools/list, comparing the count against the
number of @mcp.tool() decorators in the source tree.

It launches the console script -- the binary every documented client config
actually runs -- rather than `python -m`, so the gate exercises the same entry
point users do.

Runs deliberately WITHOUT VOIPBIN_API_KEY: the client is constructed lazily on
the first tool call, so a clean handshake also proves the server does not need
credentials to start.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REQUESTS = [
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "dist-smoke", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]


def expected_tool_count() -> int:
    """Count the decorators in the source tree, so CI holds no literal."""
    tools_dir = Path(__file__).resolve().parent.parent / "src" / "voipbin_mcp" / "tools"
    if not tools_dir.is_dir():
        sys.exit(f"cannot find the tools source at {tools_dir}")
    # Summed across files: a per-file `grep -c` would emit one count per file.
    total = sum(path.read_text().count("@mcp.tool()") for path in tools_dir.glob("*.py"))
    if total == 0:
        sys.exit("found no @mcp.tool() decorators; the count would be vacuous")
    return total


def main() -> int:
    executable = shutil.which("voipbin-mcp")
    if not executable:
        sys.stderr.write(
            "voipbin-mcp is not on PATH: the console script did not install, "
            "so the entry point users rely on is broken.\n"
        )
        return 1

    env = {k: v for k, v in os.environ.items() if k != "VOIPBIN_API_KEY"}

    proc = subprocess.run(
        [executable],
        input="".join(json.dumps(request) + "\n" for request in REQUESTS),
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    if "Traceback" in proc.stderr:
        sys.stderr.write("server raised while handling traffic:\n")
        sys.stderr.write(proc.stderr)
        return 1

    initialized = False
    served = None
    for line in proc.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == 1 and "result" in message:
            initialized = True
            info = message["result"].get("serverInfo", {})
            print(f"initialize OK: {info.get('name')} {info.get('version')}")
        if message.get("id") == 2 and "result" in message:
            served = len(message["result"].get("tools", []))

    if not initialized:
        sys.stderr.write("no initialize result on stdout\n")
        sys.stderr.write(f"stdout: {proc.stdout[:2000]}\n")
        sys.stderr.write(f"stderr: {proc.stderr[:2000]}\n")
        return 1

    if served is None:
        sys.stderr.write("server answered initialize but not tools/list\n")
        sys.stderr.write(f"stdout: {proc.stdout[:2000]}\n")
        sys.stderr.write(f"stderr: {proc.stderr[:2000]}\n")
        return 1

    expected = expected_tool_count()
    if served != expected:
        sys.stderr.write(
            f"served {served} tools over stdio but the source declares "
            f"{expected}. A server that starts and serves nothing is exactly "
            "the failure this check exists to catch.\n"
        )
        return 1

    print(f"tools/list OK: {served} tools served")
    return 0


if __name__ == "__main__":
    sys.exit(main())
