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
import threading
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
    # Resolved next to the interpreter running this script, not merely from
    # PATH: a stray voipbin-mcp from another environment would otherwise be
    # smoke-tested instead of the one just installed.
    candidate = Path(sys.executable).parent / "voipbin-mcp"
    if candidate.exists():
        executable = str(candidate)
    else:
        executable = shutil.which("voipbin-mcp")
    if not executable:
        sys.stderr.write(
            "voipbin-mcp is not on PATH: the console script did not install, "
            "so the entry point users rely on is broken.\n"
        )
        return 1

    env = {k: v for k, v in os.environ.items() if k != "VOIPBIN_API_KEY"}

    # subprocess.run(input=...) closes stdin as soon as the requests are
    # written, and the server tears its stdio task group down on EOF. That race
    # loses the tools/list response on roughly one run in six, which would make
    # this gate red on healthy code -- and the natural response to a flaky gate
    # is to weaken the assertion, which is exactly the vacuous smoke this check
    # replaced. So: keep stdin open, read until the response arrives, and only
    # then signal EOF.
    proc = subprocess.Popen(
        [executable],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    stderr_chunks: list[str] = []

    def drain_stderr():
        assert proc.stderr is not None
        stderr_chunks.append(proc.stderr.read())

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    timer = threading.Timer(60.0, proc.kill)
    timer.start()

    initialized = False
    served = None
    stdout_lines: list[str] = []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for request in REQUESTS:
            proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()

        for line in proc.stdout:
            stdout_lines.append(line)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1 and "result" in message:
                initialized = True
                info = message["result"].get("serverInfo", {})
                print(f"initialize OK: {info.get('name')} {info.get('version')}")
            if message.get("id") == 2:
                if "result" in message:
                    served = len(message["result"].get("tools", []))
                else:
                    sys.stderr.write(f"tools/list returned an error: {line}")
                break
    finally:
        timer.cancel()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except BrokenPipeError:
            pass
        proc.wait(timeout=30)
        stderr_thread.join(timeout=10)

    stderr_text = "".join(stderr_chunks)
    stdout_text = "".join(stdout_lines)

    if "Traceback" in stderr_text:
        sys.stderr.write("server raised while handling traffic:\n")
        sys.stderr.write(stderr_text)
        return 1

    if not initialized:
        sys.stderr.write("no initialize result on stdout\n")
        sys.stderr.write(f"stdout: {stdout_text[:2000]}\n")
        sys.stderr.write(f"stderr: {stderr_text[:2000]}\n")
        return 1

    if served is None:
        sys.stderr.write("server answered initialize but not tools/list\n")
        sys.stderr.write(f"stdout: {stdout_text[:2000]}\n")
        sys.stderr.write(f"stderr: {stderr_text[:2000]}\n")
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
