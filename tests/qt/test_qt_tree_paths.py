"""KAN-756: ``Tree`` paths on real Qt 5 and Qt 6 trees through UIA.

These use the mouse and wheel, because nothing else drives a Qt tree through
UIA, so they need a visible desktop nobody is using. The logic is covered without
a window in ``tests/framework/test_flat_tree.py``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from dolphin_desktop import (
    Desktop,
    ElementNotFoundError,
    UnsupportedCapabilityError,
    is_windows,
)

from . import _qt_helpers as h

pytestmark = [
    pytest.mark.skipif(not is_windows(), reason="Qt UIA backend tests are Windows-only"),
    pytest.mark.timeout(180),
]

_AUT = Path(__file__).with_name("_qt_tree_window.py")
_TITLE = "Dolphin Qt Tree Paths"
_BINDINGS = [
    pytest.param(("pyside6", "PySide6", h.QT6_SCRIPT, h.QT6_WINDOW_TITLE), id="qt6"),
    pytest.param(("pyqt5", "PyQt5", h.QT5_SCRIPT, h.QT5_WINDOW_TITLE), id="qt5"),
]


@pytest.fixture(params=_BINDINGS)
def binding(request):
    if importlib.util.find_spec(request.param[1]) is None:
        pytest.skip(f"requires {request.param[1]}")
    return request.param


@pytest.fixture
def demo(binding):
    desktop = Desktop(backend="uia", hidden=False)
    app, window = h.launch_demo(desktop, binding[2], binding[3])
    try:
        h.select_tab(window, "Containers")
        yield window
    finally:
        app.kill()


def _launch(binding, *args: str):
    desktop = Desktop(backend="uia", hidden=False)
    app = desktop.launch_qt_python_script(str(_AUT), args=[binding[0], *args])
    window = app.window(title=_TITLE, timeout=20)
    window.wait_until_ready(timeout=20)
    return app, window


@pytest.fixture
def window(binding):
    app, window = _launch(binding)
    try:
        yield window
    finally:
        app.kill()


def _status(window) -> str:
    return h.read_status(window)


def test_ticket_path_selects_the_node_in_the_demo(demo) -> None:
    tree = demo.tree(name="Files")
    tree.select_item_by_path("Project", "src", "main.py")
    assert h.wait_for_status(demo, "tree=main.py")
    tree.select_item_by_path("Project", "README.md")
    assert h.wait_for_status(demo, "tree=README.md")
    tree.select_item_by_path("Project")
    assert h.wait_for_status(demo, "tree=Project")


def test_second_column_text_is_not_a_node(demo) -> None:
    tree = demo.tree(name="Files").timeout(1)
    with pytest.raises(ElementNotFoundError, match="'Project' was reached"):
        tree.select_item_by_path("Project", "folder")


def test_closed_branches_open_without_selecting_them(window) -> None:
    tree = window.tree(name="Files")
    tree.expand_item("Alpha", "src")
    tree.expand_item("Alpha", "src")
    assert _status(window) == "ready"
    tree.select_item_by_path("Alpha", "src", "util.py")
    assert _status(window) == "Alpha/src/util.py"


def test_duplicate_names_resolve_by_their_ancestors(window) -> None:
    tree = window.tree(name="Files")
    tree.select_item_by_path("Alpha", "docs", "main.py")
    assert _status(window) == "Alpha/docs/main.py"
    tree.select_item_by_path("Beta", "src", "main.py", "deep", "deeper", "deepest")
    assert _status(window) == "Beta/src/main.py/deep/deeper/deepest"


def test_a_grandchild_is_not_taken_for_a_child(window) -> None:
    tree = window.tree(name="Files").timeout(1)
    with pytest.raises(ElementNotFoundError, match="'Alpha' was reached"):
        tree.select_item_by_path("Alpha", "main.py")
    assert _status(window) == "ready"


def test_a_name_shown_in_the_second_column_still_matches_its_own_row(window) -> None:
    window.tree(name="Files").select_item_by_path("node", "leaf")
    assert _status(window) == "node/leaf"


def test_rows_outside_the_viewport_are_scrolled_to(window) -> None:
    tree = window.tree(name="Files").timeout(30)
    tree.select_item_by_path("Omega", "Tail")
    assert _status(window) == "Omega/Tail"
    tree.select_item_by_path("Alpha", "src", "util.py")
    assert _status(window) == "Alpha/src/util.py"
    tree.select_item_by_path("Row 30", "Child 30")
    assert _status(window) == "Row 30/Child 30"


def test_too_narrow_column_is_an_explicit_error(binding) -> None:
    app, window = _launch(binding, "60")
    try:
        with pytest.raises(UnsupportedCapabilityError, match="first column"):
            window.tree(name="Files").timeout(5).select_item_by_path(
                "Beta", "src", "main.py", "deep", "deeper", "deepest"
            )
    finally:
        app.kill()
