"""Smoke an exact wheel in a clean environment with hashed PyPI dependencies."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path


def smoke(directory: Path, requirements: Path, expected_version: str) -> None:
    wheels = list(directory.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("Expected exactly one wheel to smoke-test")
    wheel = wheels[0].resolve()
    requirements = requirements.resolve(strict=True)
    # Ignore pip configuration and inherited index/Python settings in every child.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PIP_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    with tempfile.TemporaryDirectory(prefix="dolphin-wheel-smoke-") as temporary:
        root = Path(temporary)
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        scripts = root / "venv" / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")

        def run(*arguments: str) -> None:
            subprocess.run([str(python), *arguments], cwd=root, env=environment, check=True)

        run(
            "-m",
            "pip",
            "install",
            "--index-url",
            "https://pypi.org/simple/",
            "--require-hashes",
            "--only-binary=:all:",
            "--no-deps",
            "-r",
            str(requirements),
        )
        run("-m", "pip", "install", "--no-deps", "--no-index", str(wheel))
        run("-m", "pip", "check")
        run(
            "-I",
            "-c",
            "import importlib.metadata, sys; import dolphin_desktop; "
            "assert dolphin_desktop.__version__ == sys.argv[1]; "
            "assert importlib.metadata.version('dolphin-desktop') == sys.argv[1]; "
            "print(dolphin_desktop.__version__)",
            expected_version,
        )
        cli = scripts / ("dolphin.exe" if os.name == "nt" else "dolphin")
        for arguments in (("--help",), ("info", "backends")):
            subprocess.run([str(cli), *arguments], cwd=root, env=environment, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel-directory", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    arguments = parser.parse_args()
    smoke(arguments.wheel_directory, arguments.requirements, arguments.expected_version)


if __name__ == "__main__":
    main()
