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
    import dolphin_desktop._runner as runner
    from dolphin_desktop import _application, _desktop

    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    pywinauto_app = MagicMock()
    pywinauto_app.process = process.pid
    owned_handle = _application._open_owned_process_handle(process.pid)
    assert owned_handle is not None
    monkeypatch.setattr(
        runner,
        "launch_cmd_on_desktop",
        MagicMock(return_value=(process.pid, owned_handle)),
    )
    monkeypatch.setattr(
        _desktop,
        "_PyWinApp",
        MagicMock(return_value=pywinauto_app),
    )
    app = None
    try:
        app = _desktop.Desktop().launch("stub.exe", startup_delay=0)
        assert app._image_path is not None
    finally:
        if app is not None:
            app.detach(session=True)
        else:
            _application._close_owned_process_handle(owned_handle)
        process.kill()
        process.wait()
