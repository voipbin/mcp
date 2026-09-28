#!/usr/bin/env python3
"""Start the MCP server over stdio and check that it answers `initialize`.

An import check cannot catch a server that dies as soon as it handles traffic,
which is what the 0.1.1 release did. This deliberately runs WITHOUT
VOIPBIN_API_KEY: the client is constructed lazily on the first tool call, so a
clean handshake here also proves the server does not need credentials to start.
A non-zero exit or a traceback on stderr fails the job.
"""

import json
import os
import subprocess
import sys

REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "dist-smoke", "version": "0"},
    },
}


def main() -> int:
    env = {k: v for k, v in os.environ.items() if k != "VOIPBIN_API_KEY"}

    proc = subprocess.run(
        [sys.executable, "-m", "voipbin_mcp.server"],
        input=json.dumps(REQUEST) + "\n",
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    if "Traceback" in proc.stderr:
        sys.stderr.write("server raised during the handshake:\n")
        sys.stderr.write(proc.stderr)
        return 1

    for line in proc.stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("id") == 1 and "result" in message:
            server = message["result"].get("serverInfo", {})
            print(f"initialize OK: {server.get('name')} {server.get('version')}")
            return 0

    sys.stderr.write("no initialize result on stdout\n")
    sys.stderr.write(f"stdout: {proc.stdout[:2000]}\n")
    sys.stderr.write(f"stderr: {proc.stderr[:2000]}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
