"""Fixture: launch the compiled Lazarus/LCL sample and wrap it in DelphiApp."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop
from tests.delphi._lcl_env import SAMPLE_EXE  # type: ignore[import-not-found]


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
