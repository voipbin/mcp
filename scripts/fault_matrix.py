#!/usr/bin/env python3
"""Fault matrix for scripts/stdio_smoke.py.

The smoke gate exists to answer one question: did the server survive being
talked to? It was rewritten six times, and every rewrite but the last shipped a
new way to answer "yes" about a server that had died:

  v1  closed stdin early                     -> flaked 34/40
  v2  waited for EOF                         -> healthy server writing >64KB hung
  v3  killed on first answer                 -> crash after answering read GREEN
  v4  single blocking read()-to-EOF          -> worker on fd 2 withheld EOF, GREEN
  v5  read one byte at a time                -> 20x slower, back-pressured a
                                                verbose child, traceback lost at
                                                48MB, GREEN
  v6  TextIOWrapper.read(65536)              -> NOT a block read; it loops until
                                                65536 CHARS or EOF, so any
                                                traceback under 64KB was
                                                invisible whenever a worker held
                                                stderr open, GREEN

Each defect was found by a reviewer, one round after the previous fix shipped,
because the fakes that proved the fix lived in a scratch directory and were
thrown away. This file is those fakes, in the repo, run by CI. A regression in
the shutdown path now fails here instead of being rediscovered by a human.

Every case asserts BOTH the exit code and that no process survives.

    python scripts/fault_matrix.py            # all cases
    python scripts/fault_matrix.py --case worker_holds_stderr
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time

SMOKE = pathlib.Path(__file__).resolve().parent / "stdio_smoke.py"

# A fake console script answers initialize + tools/list, then misbehaves. The
# body of each fake is spliced in where MISBEHAVE sits.
FAKE = '''\
#!/usr/bin/env python3
import json, os, sys, time, subprocess, signal, traceback

def reply(obj):
    sys.stdout.write(json.dumps(obj) + "\\n")
    sys.stdout.flush()

def init():
    reply({"jsonrpc": "2.0", "id": 1, "result": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "serverInfo": {"name": "voipbin", "version": "0.2.0"}}})

def tools(n=58):
    reply({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
        {"name": "t%d" % i, "description": "d", "inputSchema": {"type": "object"}}
        for i in range(n)]}})

PRELUDE
for line in sys.stdin:
    if not line.strip().startswith("{"):
        continue
    msg = json.loads(line)
    if msg.get("id") == 1:
        init()
    elif msg.get("id") == 2:
        tools(NTOOLS)
        MISBEHAVE
'''


def _traceback_then(exit_code: int) -> str:
    return textwrap.dedent(
        f"""\
        try:
            raise RuntimeError("server died right after answering")
        except RuntimeError:
            traceback.print_exc()
        sys.stderr.flush()
        os._exit({exit_code})
        """
    )


# name -> (prelude, misbehave, tool count, expected rc)
#
# Expected rc 1 means "the gate must notice". Expected rc 0 means the server
# genuinely behaved, and a gate that reports RED here is crying wolf.
CASES: dict[str, tuple[str, str, int, int]] = {
    # --- healthy servers: these must stay GREEN or the gate is useless noise
    "clean": ("", "time.sleep(30)", 58, 0),
    "chatty_stderr": (
        "",
        'sys.stderr.write("info " * 20000); sys.stderr.flush(); time.sleep(30)',
        58,
        0,
    ),
    "huge_stderr": (
        "",
        'sys.stderr.write("x" * (48 * 1024 * 1024)); sys.stderr.flush(); time.sleep(30)',
        58,
        0,
    ),
    "worker_in_group_healthy": (
        'subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])',
        "time.sleep(30)",
        58,
        0,
    ),
    # --- dead or wrong servers: every one of these was GREEN at some point
    "crash_after_answering": ("", _traceback_then(0), 58, 1),
    "exit_zero_silently": ("", "os._exit(0)", 58, 1),
    "exit_nonzero": ("", "os._exit(3)", 58, 1),
    "crash_after_48mb_stderr": (
        "",
        'sys.stderr.write("x" * (48 * 1024 * 1024)); sys.stderr.flush(); '
        + _traceback_then(0).replace("\n", "\n"),
        58,
        1,
    ),
    "short_traceback_worker_holds_stderr": (
        'subprocess.Popen(["/bin/sleep", "120"])',
        _traceback_then(0),
        58,
        1,
    ),
    "worker_escapes_process_group": (
        'subprocess.Popen(["/bin/sleep", "120"], start_new_session=True)',
        _traceback_then(0),
        58,
        1,
    ),
    "worker_ignores_sigterm": (
        'subprocess.Popen([sys.executable, "-c", '
        '"import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(120)"])',
        _traceback_then(0),
        58,
        1,
    ),
    "zero_tools": ("", "time.sleep(30)", 0, 1),
    "tool_count_short": ("", "time.sleep(30)", 57, 1),
    "ignores_sigterm": (
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
        "time.sleep(300)",
        58,
        0,
    ),
    "hangs_without_answering": ("", "time.sleep(300)", -1, 1),
    "garbage_on_stdout": ("", 'sys.stdout.write("not json\\n"); sys.stdout.flush(); time.sleep(30)', -2, 1),
}


def build_fake(root: pathlib.Path, prelude: str, misbehave: str, ntools: int) -> pathlib.Path:
    binroot = root / "bin"
    binroot.mkdir(parents=True, exist_ok=True)

    body = FAKE.replace("PRELUDE", prelude or "pass")
    body = body.replace(
        "MISBEHAVE", textwrap.indent(misbehave, " " * 8).lstrip()
    )
    if ntools == -1:  # never answers tools/list
        body = body.replace("        tools(NTOOLS)\n", "        time.sleep(300)\n")
    elif ntools == -2:  # answers with garbage
        body = body.replace("        tools(NTOOLS)\n", "")
    body = body.replace("NTOOLS", str(max(ntools, 0)))

    script = binroot / "voipbin-mcp"
    script.write_text(body)
    script.chmod(0o755)
    (binroot / "python3").symlink_to(sys.executable)

    meta = root / "meta" / "voipbin_mcp-0.2.0.dist-info"
    meta.mkdir(parents=True, exist_ok=True)
    (meta / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: voipbin-mcp\nVersion: 0.2.0\n"
    )
    (meta / "RECORD").write_text("")
    return binroot


def surviving_fakes() -> int:
    out = subprocess.run(
        ["pgrep", "-x", "voipbin-mcp"], capture_output=True, text=True
    )
    return len([line for line in out.stdout.split() if line.strip()])


def run_case(name: str) -> tuple[bool, str]:
    prelude, misbehave, ntools, want_rc = CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        binroot = build_fake(root, prelude, misbehave, ntools)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "meta")
        env["PATH"] = f"{binroot}:{env.get('PATH', '')}"
        started = time.time()
        proc = subprocess.run(
            [str(binroot / "python3"), str(SMOKE)],
            capture_output=True,
            text=True,
            env=env,
            timeout=180,
        )
        took = time.time() - started

    time.sleep(0.5)
    leaked = surviving_fakes()
    rc_ok = proc.returncode == want_rc
    ok = rc_ok and leaked == 0
    detail = f"rc={proc.returncode} (want {want_rc}) leaked={leaked} {took:.1f}s"
    if not rc_ok and want_rc == 1:
        detail += "  <-- a dead server was reported healthy"
    return ok, detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", dest="cases")
    args = parser.parse_args()

    names = args.cases or list(CASES)
    failures = []
    for name in names:
        ok, detail = run_case(name)
        print(f"  {'ok  ' if ok else 'FAIL'} {name:38} {detail}", flush=True)
        if not ok:
            failures.append(name)

    print()
    if failures:
        print(f"{len(failures)}/{len(names)} fault cases FAILED: {failures}")
        return 1
    print(f"all {len(names)} fault cases behaved correctly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
