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

PYTEST_SMOKE = """def test_plugin_registration(pytestconfig):
    plugin = pytestconfig.pluginmanager.get_plugin("dolphin-desktop")
    assert plugin is not None, "dolphin-desktop entry point was not loaded"
    assert plugin.__name__ == "dolphin_desktop.pytest_plugin"
"""

EXTRA_IMPORTS = {
    "mss": "mss",
    "opencv-python": "cv2",
    "numpy": "numpy",
    "pytesseract": "pytesseract",
    "playwright": "playwright.sync_api",
    "mkdocs-material": "mkdocs",
    "mkdocstrings": "mkdocstrings",
    "sentry-sdk": "sentry_sdk",
}


def wheel_requirements(
    wheel: Path,
    *,
    extra: str | None = None,
    all_extras: bool = False,
    environment: dict[str, str] | None = None,
) -> tuple[list[str], set[str]]:
    """Select active requirements, expanding only selected self-referencing extras."""
    if extra is not None and all_extras:
        raise ValueError("Select either one extra or all extras")
    with zipfile.ZipFile(wheel) as archive:
        (metadata_path,) = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        metadata = email.message_from_bytes(archive.read(metadata_path))
    available = {canonicalize_name(name) for name in metadata.get_all("Provides-Extra", [])}
    selected = available.copy() if all_extras else ({canonicalize_name(extra)} if extra else set())
    requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
    active: set[str] = set()
    while True:
        if unknown := selected - available:
            raise ValueError(f"Unknown extras: {sorted(unknown)}")
        previous = selected.copy()
        for requirement in requirements:
            if requirement.marker and not any(
                requirement.marker.evaluate({**(environment or {}), "extra": name})
                for name in {"", *selected}
            ):
                continue
            if canonicalize_name(requirement.name) == canonicalize_name(metadata["Name"]):
                selected.update(canonicalize_name(name) for name in requirement.extras)
            else:
                # Preserve the parsed marker for the next expansion pass.
                plain = Requirement(str(requirement))
                plain.marker = None
                active.add(str(plain))
        if selected == previous:
            return sorted(active), selected


def check(wheel: Path, output: Path, *, all_extras: bool = False, extra: str | None = None) -> None:
    """Keep resolver, install, smoke and audit evidence outside the tool environment."""
    wheel = wheel.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    (output / "result.json").unlink(missing_ok=True)
    (output / "commands.log").write_text("", encoding="utf-8")
    requirements, selected = wheel_requirements(wheel, extra=extra, all_extras=all_extras)
    direct = output / "direct-requirements.txt"
    direct.write_text("\n".join(requirements) + "\n", encoding="utf-8")
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
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=300,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                # Native tools can still write Windows code-page bytes to the
                # redirected stream. Diagnostics must preserve the original
                # failure, even for mixed encodings or a legacy console.
                tail = (output / "commands.log").read_text(
                    encoding="utf-8", errors="backslashreplace"
                )[-8000:]
                console_encoding = sys.stdout.encoding or "utf-8"
                print(
                    tail.encode(console_encoding, errors="backslashreplace").decode(
                        console_encoding
                    )
                )
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
        cli = environment / ("Scripts/dolphin.exe" if os.name == "nt" else "bin/dolphin")
        run(str(cli), "--help")
        run(str(cli), "info", "backends")
        # Build backends may flatten `all` into direct requirements instead of
        # retaining a self-reference. Smoke the actual selected dependencies.
        dependencies = {canonicalize_name(Requirement(value).name) for value in requirements}
        imports = [modules for name, modules in EXTRA_IMPORTS.items() if name in dependencies]
        if imports:
            run(
                str(python),
                "-I",
                "-c",
                "import " + ", ".join(imports) + "; print('Extras import OK')",
            )
        if "pytest" in dependencies:
            # Exercise the installed pytest plugin, without repository conftests.
            smoke = root / "test_smoke.py"
            smoke.write_text(PYTEST_SMOKE, encoding="utf-8")
            run(
                str(python),
                "-I",
                "-m",
                "pytest",
                str(smoke),
                "-q",
                "--noconftest",
                "--confcutdir",
                str(root),
                "-c",
                os.devnull,
            )
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
                "profile": "all-extras" if all_extras else extra or "base",
                "selected_extras": sorted(selected),
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
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--all-extras", action="store_true")
    profile.add_argument("--extra", help="Check one extra, including its self-referenced extras")
    arguments = parser.parse_args()
    check(arguments.wheel, arguments.output, all_extras=arguments.all_extras, extra=arguments.extra)
