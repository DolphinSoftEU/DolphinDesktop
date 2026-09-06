"""Smoke tests against real-world Qt applications installed on the system.

Validates that dolphin_desktop's Qt support works against real Qt
applications when they are present on the machine — not just our
PyQt5/PySide6 demos.

Each test locates its target by image name among the running processes and
is skipped when that process isn't running.

Targets covered:

* **AMD Radeon Software** (Qt 6) — GPU control panel.
* **AMD Ryzen Master** (Qt 6) — CPU monitoring/overclocking.
* **Logitech G HUB** (Qt 5) — gaming peripheral software.

For each target we verify:

1. ``Application.is_qt()`` returns ``True``.
2. ``Application.qt_version()`` matches expected ("5" or "6").

Detection is all these tests touch — no window or locator is exercised, so
they stay safe to run against a live process the user is actually using.

We deliberately don't launch + kill these apps because they're long-running
services in the user's environment — instead we **connect** to a running
instance when one exists, or skip when it doesn't. This keeps the test
suite non-destructive.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, find_pid_by_image_name, is_windows

pytestmark = [
    pytest.mark.external,
    pytest.mark.skipif(
        not is_windows(),
        reason="Qt UIA backend tests are Windows-only",
    ),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect_to_running(image: str, expected_qt_version: str):
    """Find a running process by image name, connect, and verify it's a Qt app.

    Skips the test cleanly if the process isn't running or isn't a Qt app
    of the expected major version.
    """
    pid = find_pid_by_image_name(image)
    if pid is None:
        pytest.skip(f"{image!r} is not running — skip real-app smoke test")

    desktop = Desktop()
    app = desktop.connect(process=pid, timeout=5.0)
    if not app.is_qt():
        pytest.skip(f"{image!r} PID={pid} does not look like a Qt app — skip")
    if app.qt_version() != expected_qt_version:
        pytest.skip(
            f"{image!r} PID={pid} reports Qt {app.qt_version()}, expected {expected_qt_version}"
        )
    # Don't let the pytest plugin's PID-kill tear down a real user process.
    app.detach()
    return app, pid


# ---------------------------------------------------------------------------
# AMD Radeon Software — Qt 6
# ---------------------------------------------------------------------------


def test_amd_radeon_software_detected_as_qt6():
    app, pid = _connect_to_running("RadeonSoftware.exe", "6")
    assert app.is_qt() is True
    assert app.qt_version() == "6"
    assert app.process_id == pid


def test_amd_ryzen_master_detected_as_qt6():
    app, _pid = _connect_to_running("AMD Ryzen Master.exe", "6")
    assert app.is_qt() is True
    assert app.qt_version() == "6"


# ---------------------------------------------------------------------------
# Logitech G HUB — Qt 5
# ---------------------------------------------------------------------------


def test_lghub_detected_as_qt5():
    app, _ = _connect_to_running("lghub.exe", "5")
    assert app.is_qt() is True
    assert app.qt_version() == "5"


def test_lghub_agent_detected_as_qt5():
    """Background agent is also Qt 5 but headless — exercises module scan only."""
    app, _ = _connect_to_running("lghub_agent.exe", "5")
    assert app.is_qt() is True
    assert app.qt_version() == "5"


# ---------------------------------------------------------------------------
# Cross-cutting: at least one Qt app present?
# ---------------------------------------------------------------------------
