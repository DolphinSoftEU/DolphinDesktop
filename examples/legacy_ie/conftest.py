"""Shared fixtures for IE/Trident (WPF WebBrowser) example tests."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from dolphin_desktop import Desktop

_SAMPLE_DIR = Path(__file__).parent / "IESampleApp"
_EXE = _SAMPLE_DIR / "bin" / "Debug" / "net48" / "IESampleApp.exe"


def _ensure_built() -> Path | None:
    """Build the sample WPF app with ``dotnet build -f net48``."""
    if _EXE.exists():
        return _EXE
    result = subprocess.run(
        ["dotnet", "build", str(_SAMPLE_DIR), "-f", "net48", "-c", "Debug"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return _EXE if _EXE.exists() else None


@pytest.fixture(scope="module")
def ie_window(desktop: Desktop):
    """Build (if needed) and launch the IE/Trident WPF sample; yield (app, win)."""
    exe = _ensure_built()
    if exe is None:
        pytest.skip(
            "IESampleApp could not be built. "
            "Install .NET SDK and run: .\\examples\\legacy_ie\\build.ps1"
        )

    app = desktop.launch(str(exe), timeout=15)
    win = app.window(title_re=".*Dolphin IE/Trident Sample.*", timeout=10)
    time.sleep(1.0)  # wait for MSHTML to load the inline HTML form

    yield app, win

    app.kill()
