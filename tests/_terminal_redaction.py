"""Exercise the installed production plugin in an isolated pytest subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

IS_WINDOWS = os.name == "nt"


def windows_drive_paths() -> SimpleNamespace:
    """Use lexical paths on distinct drives; neither drive needs to exist."""
    return SimpleNamespace(
        rootpath=Path("C:/project"), invocation_params=SimpleNamespace(dir=Path("D:/run"))
    )


def invocation_paths(root: Path, subdir: str = "") -> SimpleNamespace:
    return SimpleNamespace(rootpath=root, invocation_params=SimpleNamespace(dir=root / subdir))


def run_terminal_case(directory: Path, *, workers: bool, mode: str) -> tuple[int, str]:
    invocation = directory if mode == "smoke" else directory / "tests" / "nested"
    invocation.mkdir(parents=True, exist_ok=True)
    (directory / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    source = """import pytest

@pytest.fixture
def cleanup(pin):
    yield
    if MODE == "teardown":
        raise RuntimeError("cleanup failed")

@pytest.mark.parametrize("pin", ["KAN763_CANARY_739184"], ids=lambda value: "case-" + value)
def test_auth(pin, cleanup, request):
    assert request.node.nodeid == "tests/nested/test_case.py::test_auth[case-" + pin + "]"
    if MODE == "call":
        raise RuntimeError("authentication failed")

def test_public():
    assert True
""".replace("MODE", repr(mode))
    (invocation / "test_case.py").write_text(
        "def test_smoke(): assert True\n" if mode == "smoke" else source,
        encoding="utf-8",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTEST_") and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:dolphin-desktop",
            "-p",
            "dolphin_desktop.pytest_plugin",
            "--dolphin-trace=off",
            "--dolphin-video=off",
            "-vv",
            "--tb=short",
            *(["-p", "xdist.plugin", "-n", "2"] if workers else []),
            *(["--collect-only"] if mode == "collection" else []),
            "test_case.py",
        ],
        cwd=invocation,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        timeout=45,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr
