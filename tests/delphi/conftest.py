"""Fixture: launch the compiled Lazarus/LCL sample and wrap it in DelphiApp."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dolphin_desktop import Desktop, sleep
from tests.delphi._lcl_env import SAMPLE_EXE  # type: ignore[import-not-found]


@pytest.fixture
def real_vcl_app(tmp_path):
    """Launch the unmodified official VCL runtime, when installed."""
    if sys.platform != "win32" or importlib.util.find_spec("delphivcl") is None:
        pytest.skip("requires Windows and optional delphivcl==1.0.7")
    from importlib.metadata import version

    assert version("delphivcl") == "1.0.7", "reference requires delphivcl==1.0.7"
    script = Path(__file__).with_name("real_vcl_app.py")
    pid_file = tmp_path / "vcl.pid"
    command = subprocess.list2cmdline([sys.executable, str(script), str(pid_file)])
    with Desktop().launch_delphi(command, startup_delay=1):
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            sleep(0.05)
        assert pid_file.exists(), "VCL fixture failed to start"
        # Attach to the actual interpreter owning the form, not the venv
        # redirector PID. This does not modify any UIA properties.
        with Desktop().attach_delphi(process=int(pid_file.read_text(encoding="ascii"))) as app:
            yield app


@pytest.fixture(scope="module")
def lcl_app():
    if SAMPLE_EXE is None:
        pytest.skip(
            "sample_lcl.exe not built. Install Lazarus and run "
            "`lazbuild sample_lcl.lpi` inside tests/delphi/sample_lcl/. "
            "See that directory's README.md for details."
        )
    app = Desktop().launch_delphi(SAMPLE_EXE, timeout=15, startup_delay=1.5)
    # Opt out of dolphin's per-test PID reaping — module scope requires it.
    try:
        app.application.detach()
    except Exception:
        pass
    form = app.form(title_re=".*Dolphin.*Sample.*")
    form.wait_ready(timeout=15)
    try:
        yield app, form
    finally:
        try:
            app.close()
        except Exception:
            pass
