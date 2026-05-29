"""Shared fixtures for WebView2 example tests."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from dolphin_desktop import Desktop

_SAMPLE_DIR = Path(__file__).parent / "WebView2LoginSample"
_EXE = _SAMPLE_DIR / "bin" / "Debug" / "net8.0-windows" / "WebView2LoginSample.exe"


def _ensure_built() -> Path | None:
    """Build the sample app with ``dotnet build`` if not already compiled."""
    if _EXE.exists():
        return _EXE
    result = subprocess.run(
        ["dotnet", "build", str(_SAMPLE_DIR), "-c", "Debug"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return _EXE if _EXE.exists() else None


@pytest.fixture(scope="module")
def wv2_window(desktop: Desktop):
    """Build (if needed) and launch the WebView2 login sample; yield the Window."""
    exe = _ensure_built()
    if exe is None:
        pytest.skip(
            "WebView2LoginSample could not be built. "
            "Install .NET 8 SDK and run: .\\examples\\webview2_sample\\build.ps1"
        )

    app = desktop.launch_webview2(str(exe), timeout=15)
    win = app.window(title_re=".*Dolphin WebView2 Login Sample.*", timeout=10)
    time.sleep(1.5)  # wait for WebView2 runtime to load the HTML

    yield app, win

    app.kill()
