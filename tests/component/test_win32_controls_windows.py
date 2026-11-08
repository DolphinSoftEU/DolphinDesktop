"""KAN-747: Window convenience controls against a real classic Win32 form.

``Desktop.for_legacy_apps()`` drives ``tests/component/_win32_controls_window.py``
(plain EDIT / COMBOBOX / SysTreeView32 / BUTTON / STATIC windows and a native
HMENU) through the same ``Window.edit()`` / ``combo_box()`` / ``tree()`` /
``button()`` / ``check_box()`` / ``menu()`` calls a UIA test would use.
Every action is message-based (no mouse or keyboard input), and each one is
verified through the status label the AUT updates from its own event
handlers. The WinForms flavour of the same contract is DESKTOP-145 in the
UAT repository.
"""

from __future__ import annotations

import time

import pytest
from tests.component._win32_controls_window import (
    COMBO_ITEMS,
    ID_STATUS,
    STATUS_READY,
    WINDOW_TITLE,
)

from dolphin_desktop import (
    Desktop,
    UnsupportedCapabilityError,
    dirname,
    path_join,
    python_executable,
)

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(120)]

_HELPER = path_join(dirname(__file__), "_win32_controls_window.py")


@pytest.fixture
def window():
    app = Desktop.for_legacy_apps(hidden=False).launch(
        f'"{python_executable()}" "{_HELPER}"', startup_delay=0.5
    )
    try:
        win = app.window(title=WINDOW_TITLE, timeout=20)
        win.wait_until_ready(timeout=20)
        yield win
    finally:
        app.kill()


def _status(window) -> str:
    # value() falls back to the window text at once on win32; text() first
    # probes a UIA TextPattern, which a win32 spec answers only after its
    # default 5 s child-window wait.
    return window.locator(control_id=ID_STATUS).value()


def _wait_status(window, expected: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while _status(window) != expected:
        assert time.monotonic() < deadline, f"status {_status(window)!r} != {expected!r}"
        time.sleep(0.05)


def test_edit_combo_and_tree_are_found_and_driven_on_win32(window) -> None:
    assert _status(window) == STATUS_READY

    edit = window.edit()
    edit.wait_for(state="visible", timeout=5)
    assert edit.get_attribute("class_name") == "Edit"
    edit.set_text("Dolphin")
    assert edit.value() == "Dolphin"

    combo = window.combo_box()
    combo.wait_for(state="visible", timeout=5)
    assert combo.get_attribute("class_name") == "ComboBox"
    combo.select_item(COMBO_ITEMS[1])
    assert combo.selected_item() == COMBO_ITEMS[1]
    _wait_status(window, f"combo={COMBO_ITEMS[1]}")

    tree = window.tree()
    tree.wait_for(state="visible", timeout=5)
    assert tree.get_attribute("class_name") == "SysTreeView32"
    tree.select_item_by_path("Root", "Child")
    _wait_status(window, "tree=Child")
    window.locator(title="tree=Child", control_type="Static").wait_for(timeout=5)
    window.locator(title="tree=Child", control_type="Text").wait_for(timeout=5)


def test_button_and_check_box_are_told_apart_by_style(window) -> None:
    assert window.button(name="Save").exists(timeout=5)
    assert window.check_box(name="Remember").exists(timeout=5)
    # Both are class "Button"; only the BS_* style tells them apart.
    assert not window.button(name="Remember").exists(timeout=0)
    assert not window.check_box(name="Save").exists(timeout=0)


def test_native_menu_items_are_found_read_and_invoked(window) -> None:
    file_menu = window.menu("File")
    mark = file_menu.item("Mark")
    assert mark.exists(timeout=5)
    assert mark.is_enabled()
    assert not file_menu.item("Disabled item").is_enabled()
    assert not file_menu.item("Missing").exists(timeout=0)

    mark.invoke()
    _wait_status(window, "menu=mark")


def test_type_without_a_win32_class_fails_at_once(window) -> None:
    started = time.monotonic()
    with pytest.raises(UnsupportedCapabilityError, match="'TreeItem'"):
        window.locator(control_type="TreeItem", title="Child").wait_for(timeout=10)
    with pytest.raises(UnsupportedCapabilityError):
        window.locator(control_type="ListItem").exists(timeout=10)
    assert time.monotonic() - started < 5
