"""Qt agent — wait_for_property polling helper coverage."""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, WaitTimeoutError, monotonic, sleep
from tests.qt._qt_helpers import QML_SCRIPT

pytestmark = pytest.mark.qt_qml


@pytest.fixture
def qml_app_w():
    desktop = Desktop()
    app = desktop.launch_qt_python_script(str(QML_SCRIPT), timeout=20, startup_delay=2.5)
    sleep(1.5)
    app.detach()
    try:
        yield app
    finally:
        try:
            app.kill()
        except Exception:
            pass


def test_wait_for_property_converges_when_set(qml_app_w):
    field = qml_app_w.qml("qmlNameField")
    field.set_property("text", "ConvergedValue")
    assert field.wait_for_property("text", "ConvergedValue", timeout=2.0) == "ConvergedValue"


def test_wait_for_property_via_qml_binding(qml_app_w):
    btn = qml_app_w.qml("qmlClickButton")
    status = qml_app_w.qml("qmlStatusLabel")
    btn.click()
    assert status.wait_for_property("text", "clicked", timeout=2.0) == "clicked"


def test_wait_for_property_raises_on_timeout(qml_app_w):
    status = qml_app_w.qml("qmlStatusLabel")
    with pytest.raises(WaitTimeoutError) as exc_info:
        status.wait_for_property("text", "NEVER_HAPPENS", timeout=0.5)
    assert "NEVER_HAPPENS" in str(exc_info.value)


def test_wait_for_property_returns_immediately_on_match(qml_app_w):
    field = qml_app_w.qml("qmlNameField")
    field.set_property("text", "instant")
    start = monotonic()
    field.wait_for_property("text", "instant", timeout=10.0, poll_interval=0.05)
    elapsed = monotonic() - start
    assert elapsed < 0.5, f"wait took {elapsed:.2f}s — should be near-instant"
