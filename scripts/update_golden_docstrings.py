#!/usr/bin/env python3
"""Regenerate tests/golden_docstrings.json from the live tool descriptions.

Run this ONLY after re-reading the Go source named beside the claim in
PINNED_CLAIMS and confirming the backend still behaves the way the new
docstring says. Ten docstring claims in this package have been found false
against the Go source, so a docstring edit is the moment to re-verify, not a
formality.

    python scripts/update_golden_docstrings.py
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

GOLDEN = ROOT / "tests" / "golden_docstrings.json"


async def collect() -> dict[str, str]:
    from voipbin_mcp.server import mcp
    import voipbin_mcp.tools  # noqa: F401  registers every tool

    tools = await mcp.list_tools()
    return {t.name: " ".join((t.description or "").split()) for t in tools}


def main() -> int:
    descriptions = asyncio.run(collect())
    if not descriptions:
        print("refusing to write an empty golden file", file=sys.stderr)
        return 1

    before = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
    GOLDEN.write_text(
        json.dumps(descriptions, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )

    added = sorted(set(descriptions) - set(before))
    removed = sorted(set(before) - set(descriptions))
    changed = sorted(
        name
        for name in set(before) & set(descriptions)
        if before[name] != descriptions[name]
    )
    print(f"wrote {GOLDEN.relative_to(ROOT)} with {len(descriptions)} tools")
    for label, names in (("added", added), ("removed", removed), ("changed", changed)):
        if names:
            print(f"  {label}: {', '.join(names)}")
    if not (added or removed or changed):
        print("  no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
