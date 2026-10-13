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
    final_tag = re.fullmatch(rf"refs/tags/v({FINAL_VERSION})", ref)
    rc_tag = re.fullmatch(rf"refs/tags/v({FINAL_VERSION})-rc\.([0-9]+)", ref)
    if event == "push":
        if final_tag:
            target = "pypi"
        elif rc_tag:
            target = "testpypi"
        else:
            raise ValueError(f"Unsupported release ref: {ref}")
    elif event == "workflow_dispatch" and requested in {"pypi", "testpypi"}:
        target = requested
    else:
        raise ValueError("Unsupported release event or target")

    if final_tag and version != final_tag.group(1):
        raise ValueError("Final release tag does not match the project version")
    if rc_tag and version != f"{rc_tag.group(1)}rc{rc_tag.group(2)}":
        raise ValueError("Release candidate tag does not match the project version")

    if target == "pypi":
        if not re.fullmatch(FINAL_VERSION, version):
            raise ValueError("PyPI requires a final X.Y.Z version")
        if not final_tag:
            raise ValueError("PyPI requires a matching final release tag")
    elif ref not in {"refs/heads/master", "refs/heads/testpypi-preview"} and not (
        final_tag or rc_tag
    ):
        raise ValueError("TestPyPI requires master, testpypi-preview or a matching release tag")
    return target


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
