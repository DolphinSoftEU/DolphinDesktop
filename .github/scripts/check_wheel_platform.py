"""KAN-586: install an exact wheel normally and verify its native platform contract."""

from __future__ import annotations

import argparse
import email
import hashlib
import json
import os
import subprocess
import tempfile
import venv
import zipfile
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

WINDOWS_DEPENDENCIES = {"comtypes", "pywin32"}

IMPORT_CHECK = """
import importlib.metadata as metadata
import json
import platform
import sys
import warnings
from pathlib import Path

expected, report = sys.argv[1:]
installed = {
    dist.metadata['Name'].lower().replace('_', '-'): dist.version
    for dist in metadata.distributions()
}
windows = platform.system() == 'Windows'
for name in ('pywin32', 'comtypes'):
    assert (name in installed) == windows, (platform.system(), name, installed)
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter('always')
    import dolphin_desktop
assert Path(dolphin_desktop.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
assert metadata.version('dolphin-desktop') == expected == dolphin_desktop.__version__
platform_warning = any(
    warning.category is RuntimeWarning and 'designed for Windows' in str(warning.message)
    for warning in caught
)
assert platform_warning == (not windows), [str(w.message) for w in caught]
Path(report).write_text(json.dumps({
    'system': platform.system(), 'python': platform.python_version(),
    'version': expected, 'installed': installed, 'platform_warning': platform_warning,
}, indent=2) + '\\n', encoding='utf-8')
print('Native wheel installation and import passed:', platform.system(), expected)
"""


def validate_metadata(wheel: Path, expected_version: str) -> bytes:
    """Reject mismatched artifacts and Windows requirements active on Linux/macOS."""
    name, version, _, _ = parse_wheel_filename(wheel.name)
    if name != "dolphin-desktop" or str(version) != expected_version:
        raise ValueError("Wheel filename does not match the requested product/version")
    with zipfile.ZipFile(wheel) as archive:
        (path,) = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        raw = archive.read(path)
    metadata = email.message_from_bytes(raw)
    if (
        canonicalize_name(metadata["Name"]) != "dolphin-desktop"
        or metadata["Version"] != expected_version
    ):
        raise ValueError("Wheel METADATA does not match the requested product/version")
    requirements = [Requirement(value) for value in metadata.get_all("Requires-Dist", [])]
    for system, sys_platform in (("Windows", "win32"), ("Linux", "linux"), ("Darwin", "darwin")):
        for python in ("3.11", "3.12", "3.13"):
            active = set()
            for requirement in requirements:
                name = canonicalize_name(requirement.name)
                if name not in WINDOWS_DEPENDENCIES:
                    continue
                if requirement.marker is None:
                    raise ValueError(f"Missing platform marker: {requirement}")
                for extra in ["", *metadata.get_all("Provides-Extra", [])]:
                    matches = requirement.marker.evaluate(
                        {
                            "platform_system": system,
                            "sys_platform": sys_platform,
                            "os_name": "nt" if system == "Windows" else "posix",
                            "python_version": python,
                            "python_full_version": python + ".0",
                            "extra": extra,
                        }
                    )
                    if matches and system != "Windows":
                        raise ValueError(f"Windows dependency active on {system}: {requirement}")
                    if matches and extra == "":
                        active.add(name)
            if system == "Windows" and active != WINDOWS_DEPENDENCIES:
                raise ValueError(f"Missing required Windows dependencies for Python {python}")
    return raw


def smoke(wheel: Path, output: Path, expected_version: str) -> None:
    """Record metadata, normal pip resolution and isolated installed-package import."""
    wheel = wheel.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    (output / "result.json").unlink(missing_ok=True)
    (output / "METADATA").write_bytes(validate_metadata(wheel, expected_version))
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("PIP_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment["PIP_CONFIG_FILE"] = os.devnull
    with (output / "commands.log").open("w", encoding="utf-8") as log:
        with tempfile.TemporaryDirectory(prefix="dolphin-platform-") as temporary:
            root = Path(temporary)
            venv.EnvBuilder(with_pip=True).create(root / "venv")
            python = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

            def run(*arguments: str) -> None:
                log.write(f"\n{arguments!r}\n")
                log.flush()
                try:
                    subprocess.run(
                        [str(python), *arguments],
                        cwd=root,
                        env=environment,
                        check=True,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=300,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    print((output / "commands.log").read_text(encoding="utf-8")[-8000:])
                    raise

            # Deliberately let pip resolve dependencies from the wheel metadata.
            # Preinstalling a lock or using --no-deps would hide KAN-586.
            run(
                "-m",
                "pip",
                "install",
                "--index-url",
                "https://pypi.org/simple/",
                "--only-binary=:all:",
                "--report",
                str(output / "install.json"),
                str(wheel),
            )
            run("-m", "pip", "check")
            run("-I", "-c", IMPORT_CHECK, expected_version, str(output / "import.json"))
    result = json.loads((output / "import.json").read_text(encoding="utf-8"))
    result.update(
        {
            "wheel": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "status": "passed",
        }
    )
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Wheel platform check passed: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, default=os.environ.get("DOLPHIN_DESKTOP_WHEEL"))
    parser.add_argument("--wheel-directory", type=Path)
    parser.add_argument("--expected-version", default=os.environ.get("DOLPHIN_DESKTOP_VERSION"))
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.wheel_directory:
        if arguments.wheel:
            parser.error("Select either a wheel or a wheel directory")
        wheels = list(arguments.wheel_directory.glob("*.whl"))
        if len(wheels) != 1:
            parser.error("Expected exactly one wheel in the selected directory")
        arguments.wheel = wheels[0]
    if not arguments.wheel or not arguments.expected_version:
        parser.error("Supply an exact wheel and version through arguments or DOLPHIN_DESKTOP_* env")
    smoke(arguments.wheel, arguments.output, arguments.expected_version)
