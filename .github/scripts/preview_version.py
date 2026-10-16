"""Give development wheels a unique version tied to the originating CI attempt."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path


def artifact_version(version: str, run_id: str, attempt: str) -> str:
    match = re.fullmatch(r"(\d+\.\d+\.\d+)\.dev\d+", version)
    if not match:
        return version
    if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
        raise ValueError("Preview requires positive CI run and attempt identifiers")
    if int(attempt) >= 1_000_000:
        raise ValueError("CI attempt exceeds the preview version allocation")
    return f"{match.group(1)}.dev{int(run_id) * 1_000_000 + int(attempt)}"


def main() -> None:
    project = Path("pyproject.toml")
    source = project.read_text(encoding="utf-8")
    version = tomllib.loads(source)["project"]["version"]
    expected = artifact_version(version, os.environ["CI_RUN_ID"], os.environ["CI_RUN_ATTEMPT"])
    if sys.argv[1:] == ["prepare"]:
        package = Path("src/dolphin_desktop/__init__.py")
        package_source = package.read_text(encoding="utf-8")
        if package_source.count(f'__version__ = "{version}"') != 1:
            raise ValueError("Project and package versions must agree before building")
        project.write_text(
            source.replace(f'version = "{version}"', f'version = "{expected}"', 1),
            encoding="utf-8",
        )
        package.write_text(
            package_source.replace(f'__version__ = "{version}"', f'__version__ = "{expected}"'),
            encoding="utf-8",
        )
    elif sys.argv[1:] != ["expected"]:
        raise ValueError("Expected prepare or expected")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"version={expected}\n")
    print(f"CI artifact version: {expected}")


if __name__ == "__main__":
    main()
