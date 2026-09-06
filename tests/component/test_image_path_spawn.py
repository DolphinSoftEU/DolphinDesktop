"""Windows component regression tests for process image-path capture."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.windows_component


def test_path_is_known_immediately_after_create_process() -> None:
    from dolphin_desktop._application import _process_image_path

    for _ in range(5):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            path = _process_image_path(process.pid)
        finally:
            process.kill()
            process.wait()

        assert path is not None
        assert path.endswith(".exe")


def test_launch_passes_a_real_path_to_the_application(monkeypatch) -> None:
    from dolphin_desktop import _application, _desktop

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pywinauto_app = MagicMock()
    pywinauto_app.process = process.pid
    monkeypatch.setattr(
        _desktop,
        "_PyWinApp",
        MagicMock(return_value=pywinauto_app),
    )
    try:
        app = _desktop.Desktop().launch("stub.exe", startup_delay=0)
        assert app._image_path is not None
    finally:
        _application._live_pids.discard(process.pid)
        _application._session_pids.discard(process.pid)
        process.kill()
        process.wait()
