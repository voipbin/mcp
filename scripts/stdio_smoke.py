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

import importlib.metadata
import json
import os
import signal
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



def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    """Signal the child's whole process group, not just the child.

    A server that forks a helper would otherwise leave the helper running after
    the direct child is reaped, and the gate would exit 0 with an orphan behind
    it. Falls back to the single process if the group is already gone.
    """
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


def main() -> int:
    # Resolved next to the interpreter running this script. No PATH fallback:
    # a stray voipbin-mcp from another environment would otherwise be
    # smoke-tested instead of the one just installed, and the missing-console-
    # script case would pass green.
    candidate = Path(sys.executable).parent / "voipbin-mcp"
    if not os.access(candidate, os.X_OK):
        sys.stderr.write(
            f"no executable voipbin-mcp next to {sys.executable}: the console "
            "script did not install, so the entry point users rely on is "
            "broken. Run this with the interpreter of the environment under "
            "test.\n"
        )
        return 1
    executable = str(candidate)

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
        # Own process group, so a server that forks a helper cannot leave the
        # helper running when we signal: we signal the whole group below.
        start_new_session=True,
    )

    stderr_chunks: list[str] = []

    def drain_stderr():
        assert proc.stderr is not None
        stderr_chunks.append(proc.stderr.read())

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    timer = threading.Timer(60.0, lambda: _signal_group(proc, signal.SIGKILL))
    timer.start()

    initialized = False
    served = None
    reported_version = None
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
                reported_version = info.get("version")
                print(f"initialize OK: {info.get('name')} {reported_version}")
            if message.get("id") == 2:
                if "result" in message:
                    served = len(message["result"].get("tools", []))
                else:
                    sys.stderr.write(f"tools/list returned an error: {line}")
                break
    finally:
        # The answer is in hand (or the server died). Two competing hazards:
        #
        #  - Waiting for the server to notice EOF stalls the gate when it keeps
        #    writing after answering: it blocks on a full 64KB stdout pipe and
        #    never exits, which used to turn a CORRECT server red.
        #  - Killing it immediately masks a server that answers and THEN dies.
        #    That is the exact shape of the 0.1.x failure this gate exists to
        #    catch, so it must not be traded away for speed.
        #
        # Give a dying server a brief window to die visibly, then end it. A
        # healthy server costs the full window; a crashing one is caught.
        timer.cancel()
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except BrokenPipeError:
            pass

        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

        if proc.poll() is None:
            _signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                _signal_group(proc, signal.SIGKILL)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    sys.stderr.write("server ignored SIGKILL; giving up on reaping it\n")

        # Only now close stdout: closing it while the child still runs can hand
        # it an EPIPE that looks like a crash.
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except (OSError, ValueError):
                pass
        stderr_thread.join(timeout=10)

    exit_status = proc.returncode

    stderr_text = "".join(stderr_chunks)
    stdout_text = "".join(stdout_lines)

    if "Traceback" in stderr_text:
        sys.stderr.write("server raised while handling traffic:\n")
        sys.stderr.write(stderr_text)
        return 1

    # A server that answers and then dies is broken for any client that holds
    # the session open -- and not every death prints a traceback (os._exit, a
    # segfault, a C-level abort). Negative statuses are our own SIGTERM/SIGKILL.
    if exit_status not in (None, 0, -signal.SIGTERM, -signal.SIGKILL):
        sys.stderr.write(
            f"server exited with status {exit_status} after answering. It "
            "completed the handshake and then died, which breaks any client "
            "holding the session open.\n"
        )
        if stderr_text:
            sys.stderr.write(f"stderr: {stderr_text[:2000]}\n")
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
    # serverInfo carries the mcp SDK's version unless we override it, which made
    # the server report e.g. "1.30.0" as its own version. Pin it: a client cannot
    # tell a fixed install from the broken 0.1.x without this.
    try:
        expected_version = importlib.metadata.version("voipbin-mcp")
    except importlib.metadata.PackageNotFoundError:
        sys.stderr.write(
            "voipbin-mcp is not installed in the environment of "
            f"{sys.executable}, so its version cannot be checked. Run this with "
            "the interpreter of the environment under test.\n"
        )
        return 1
    if reported_version != expected_version:
        sys.stderr.write(
            f"server reported version {reported_version!r} but the installed "
            f"distribution is {expected_version!r}. serverInfo.version defaults "
            "to the mcp SDK's version, so a client cannot identify which "
            "voipbin-mcp it is talking to.\n"
        )
        return 1

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
