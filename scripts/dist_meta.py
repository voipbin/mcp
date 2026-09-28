#!/usr/bin/env python3
"""Emit the packaging facts CI needs, as GITHUB_OUTPUT lines.

Single source of truth: both values are read from pyproject.toml so a release
cannot disagree with what the smoke jobs test. Requires Python 3.11+ for
tomllib, which is why the build job pins its interpreter instead of using the
runner default.
"""

import re
import sys
import tomllib
from pathlib import Path


def main() -> int:
    data = tomllib.loads(Path("pyproject.toml").read_text())
    project = data["project"]

    version = project.get("version", "")

    floor = ""
    for dep in project.get("dependencies", []):
        if re.match(r"^mcp\b", dep):
            match = re.search(r">=\s*([0-9][0-9A-Za-z.\-]*)", dep)
            if match:
                floor = match.group(1)
            break

    # Fail loudly here rather than letting an empty value flow into
    # `pip install "mcp=="`, which is a confusing parse error much later.
    if not version:
        sys.exit("could not read project.version from pyproject.toml")
    if not floor:
        sys.exit(
            "could not read a >= lower bound for mcp from "
            "project.dependencies in pyproject.toml"
        )

    print(f"version={version}")
    print(f"floor={floor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
