"""Fixtures for the explicitly built legacy MSHTML host."""

from pathlib import Path

import pytest

from dolphin_desktop import Desktop

_EXE = Path(__file__).parent / "IESampleApp" / "bin" / "Debug" / "net48" / "IESampleApp.exe"


@pytest.fixture
def ie_window(desktop: Desktop):
    if not _EXE.is_file():
        pytest.skip("Build the sample first: ./examples/legacy_ie/build.ps1 (.NET Framework 4.8)")
    with desktop.launch(f'"{_EXE}"', timeout=15) as app:
        window = app.window(title="Dolphin IE/Trident Sample", timeout=10)
        window.get_by_automation_id("StatusBar").wait_for_text("loaded", timeout=15)
        yield app, window
