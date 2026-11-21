"""Fixtures for the explicitly built WebView2 sample."""

from pathlib import Path

import pytest

from dolphin_desktop import Desktop

_EXE = (
    Path(__file__).parent
    / "WebView2LoginSample"
    / "bin"
    / "Debug"
    / "net10.0-windows"
    / "WebView2LoginSample.exe"
)


@pytest.fixture
def wv2_window(desktop: Desktop):
    if not _EXE.is_file():
        pytest.skip("Build the sample first: ./examples/webview2_sample/build.ps1 (.NET 10 SDK)")
    with desktop.launch_webview2(f'"{_EXE}"', timeout=15) as app:
        window = app.window(title="Dolphin WebView2 Login Sample", timeout=10)
        window.locator(control_type="Edit", title_re=".*Email.*").wait_for(timeout=15)
        yield app, window
