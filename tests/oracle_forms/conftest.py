"""Shared fixture: launch the compiled Java Swing Oracle-Forms mock."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, dirname, path_exists, path_join, which

_MOCK_DIR = dirname(__file__)
_CLASS_FILE = path_join(_MOCK_DIR, "OracleFormsMock.class")


@pytest.fixture(scope="module")
def forms_app():
    if not path_exists(_CLASS_FILE):
        pytest.skip(
            "OracleFormsMock.class not built. Run "
            "`javac --release 21 OracleFormsMock.java` inside "
            "tests/oracle_forms/."
        )
    if which("java") is None:
        pytest.skip("java not on PATH")

    app = Desktop().launch_oracle_forms(
        main_class="OracleFormsMock",
        classpath=_MOCK_DIR,
        title_re=".*Oracle Forms Mock.*",
        timeout=25,
        startup_delay=3,
    )
    # Opt out of dolphin's per-test PID reaping — the fixture is module-scoped
    # and we do NOT want the JVM killed between tests.
    try:
        app.application.detach()
    except Exception:
        pass
    try:
        app.form().wait_ready(timeout=15)
        yield app
    finally:
        try:
            app.close()
        except Exception:
            pass
