"""Controlled Qt 5/Qt 6 UIA regressions for library development.

The two demo AUTs expose the same controls. A single parameterized suite
keeps their coverage aligned without maintaining two copies of every test.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from dolphin_desktop import Desktop, is_windows
from tests.qt import _qt_helpers as h

_windows_only = pytest.mark.skipif(not is_windows(), reason="Qt UIA tests are Windows-only")
pytestmark = pytest.mark.qt_desktop


@pytest.fixture(
    params=(
        pytest.param(("PySide6", h.QT6_SCRIPT, h.QT6_WINDOW_TITLE, "qt", "6"), id="qt6"),
        pytest.param(("PyQt5", h.QT5_SCRIPT, h.QT5_WINDOW_TITLE, "qt5", "5"), id="qt5"),
    ),
    scope="module",
)
def qt_variant(request):
    binding, script, title, prefix, version = request.param
    if importlib.util.find_spec(binding) is None:
        pytest.skip(f"{binding} is not installed")
    if not Path(script).is_file():
        pytest.skip(f"Qt demo AUT is missing: {script}")

    app, win = h.launch_demo(Desktop(backend="uia"), script, title)
    try:
        yield app, win, prefix, version
    finally:
        try:
            app.kill()
        except Exception:
            pass


@_windows_only
def test_qt_variant_detection_and_version(qt_variant):
    app, _win, _prefix, version = qt_variant
    assert app.is_qt() is True
    assert app.qt_version() == version


@_windows_only
def test_qt_variant_has_named_button_and_object_name(qt_variant):
    _app, win, prefix, _version = qt_variant
    h.select_tab(win, "Buttons")
    assert win.button(name="OK").exists(timeout=2)
    assert win.get_by_object_name(f"{prefix}_btn_ok").exists(timeout=2)


@_windows_only
def test_qt_variant_button_updates_status(qt_variant):
    _app, win, _prefix, _version = qt_variant
    h.select_tab(win, "Buttons")
    win.button(name="OK").invoke()
    assert h.wait_for_status(win, "clicked OK")


@_windows_only
def test_qt_variant_inputs_round_trip(qt_variant):
    _app, win, _prefix, _version = qt_variant
    h.select_tab(win, "Inputs")
    win.edit(name="Username").set_text("Dolphin")
    assert win.edit(name="Username").text() == "Dolphin"
