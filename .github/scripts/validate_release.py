"""Validate the release ref, version and approved source before building."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

FINAL_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def release_target(version: str, event: str, ref: str, requested: str) -> str:
    if event == "workflow_dispatch" and requested == "testpypi":
        if ref not in {"refs/heads/master", "refs/heads/testpypi-preview"}:
            raise ValueError("TestPyPI requires master or testpypi-preview dispatch")
        return "testpypi"
    if event != "push":
        raise ValueError("PyPI requires a tag push; manual production dispatch is forbidden")
    if not re.fullmatch(FINAL_VERSION, version):
        raise ValueError("PyPI requires a final X.Y.Z version")
    if ref != f"refs/tags/v{version}":
        raise ValueError("PyPI requires a matching final release tag")
    return "pypi"


def main() -> int:
    try:
        version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        target = release_target(
            version,
            os.environ["EVENT_NAME"],
            os.environ["RELEASE_REF"],
            os.environ.get("DISPATCH_TARGET", ""),
        )
        if target == "pypi":
            # A tag on an unmerged feature commit must not bypass the master policy.
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", "HEAD", "refs/remotes/origin/master"],
                check=True,
            )
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"version={version}\ntarget={target}\n")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Release rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
