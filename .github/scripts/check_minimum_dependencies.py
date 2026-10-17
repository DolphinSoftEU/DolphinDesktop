"""Install and audit a wheel's lowest compatible direct dependencies (KAN-581)."""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def check(wheel: Path, output: Path, *, all_extras: bool) -> None:
    """Keep resolver, install, smoke and audit evidence outside the tool environment."""
    wheel = wheel.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    (output / "result.json").unlink(missing_ok=True)
    (output / "commands.log").write_text("", encoding="utf-8")
    with zipfile.ZipFile(wheel) as archive:
        (metadata_path,) = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        metadata = email.message_from_bytes(archive.read(metadata_path))
    extras = [""] + (metadata.get_all("Provides-Extra", []) if all_extras else [])
    requirements = []
    for value in metadata.get_all("Requires-Dist", []):
        requirement = Requirement(value)
        if requirement.marker and not any(
            requirement.marker.evaluate({"extra": extra}) for extra in extras
        ):
            continue
        # The `all` extra refers back to this distribution; every constituent
        # extra is already selected above. Never resolve the product from PyPI.
        if canonicalize_name(requirement.name) == canonicalize_name(metadata["Name"]):
            continue
        requirement.marker = None
        requirements.append(str(requirement))
    direct = output / "direct-requirements.txt"
    direct.write_text("\n".join(sorted(set(requirements))) + "\n", encoding="utf-8")
    resolved = output / "minimum-requirements.txt"

    def run(*command: str) -> None:
        with (output / "commands.log").open("a", encoding="utf-8") as log:
            log.write(f"\n{command!r}\n")
            log.flush()
            try:
                subprocess.run(
                    command,
                    check=True,
                    cwd=output,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                print((output / "commands.log").read_text(encoding="utf-8")[-8000:])
                raise

    run(
        "uv",
        "pip",
        "compile",
        str(direct),
        "--python",
        sys.executable,
        "--resolution",
        "lowest-direct",
        "--only-binary",
        ":all:",
        "--generate-hashes",
        "--output-file",
        str(resolved),
    )
    with tempfile.TemporaryDirectory(prefix="dolphin-minimum-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        run("uv", "venv", "--python", sys.executable, str(environment))
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--require-hashes",
            "--only-binary",
            ":all:",
            "-r",
            str(resolved),
        )
        run("uv", "pip", "install", "--python", str(python), "--no-deps", str(wheel))
        run("uv", "pip", "check", "--python", str(python))
        run(
            str(python),
            "-I",
            "-c",
            "import importlib.metadata as m; import dolphin_desktop; "
            "assert m.version('dolphin-desktop') == dolphin_desktop.__version__; "
            "print(dolphin_desktop.__version__)",
        )
        if all_extras:
            run(
                str(python),
                "-I",
                "-c",
                "import cv2, numpy, playwright.sync_api, pytesseract, "
                "sentry_sdk, mss, mkdocs, mkdocstrings; print('Extras import OK')",
            )
            # Exercise the installed pytest plugin, without repository conftests.
            smoke = root / "test_smoke.py"
            smoke.write_text("def test_import():\n    import dolphin_desktop\n", encoding="utf-8")
            run(str(python), "-I", "-m", "pytest", str(smoke), "-q", "--noconftest")
    run(
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        str(resolved),
        "--no-deps",
        "--disable-pip",
        "--strict",
        "--format",
        "json",
        "--output",
        str(output / "audit.json"),
    )
    (output / "result.json").write_text(
        json.dumps(
            {
                "python": sys.version,
                "profile": "all-extras" if all_extras else "base",
                "wheel": wheel.name,
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                "status": "passed",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Minimum dependency check passed: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-extras", action="store_true")
    arguments = parser.parse_args()
    check(arguments.wheel, arguments.output, all_extras=arguments.all_extras)
