"""KAN-747: Window convenience controls against a real classic Win32 form.

``Desktop.for_legacy_apps()`` drives ``tests/component/_win32_controls_window.py``
(plain EDIT / COMBOBOX / SysTreeView32 / LISTBOX / SysTabControl32 / BUTTON /
STATIC windows and a native HMENU) through the same ``Window.edit()`` /
``combo_box()`` / ``tree()`` / ``list_box()`` / ``tab()`` / ``button()`` /
``check_box()`` / ``menu()`` calls a UIA test would use.
Every action is message-based (no mouse or keyboard input), and each one is
verified through the status label the AUT updates from its own event
handlers. The WinForms flavour of the same contract is DESKTOP-145 in the
UAT repository.
"""

from __future__ import annotations

import time

import pytest
import win32con
import win32gui
from pywinauto.controls.menuwrapper import MenuItemNotEnabled
from tests.component._win32_controls_window import (
    COMBO_ITEMS,
    ID_BUTTON_TABS,
    ID_EDIT,
    ID_EMPTY_EDIT,
    ID_EMPTY_LABEL,
    ID_STATUS,
    ID_TABS,
    LIST_ITEMS,
    STATUS_READY,
    WINDOW_TITLE,
    WM_OPEN_FILE_MENU,
)

import dolphin_desktop._locator as locator_module
from dolphin_desktop import (
    Desktop,
    ElementNotFoundError,
    UnsupportedCapabilityError,
    WaitTimeoutError,
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
    return window.locator(control_id=ID_STATUS).text()


def _wait_status(window, expected: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while _status(window) != expected:
        assert time.monotonic() < deadline, f"status {_status(window)!r} != {expected!r}"
        time.sleep(0.05)


def test_edit_combo_and_tree_are_found_and_driven_on_win32(window) -> None:
    assert _status(window) == STATUS_READY

    edit = window.edit(control_id=ID_EDIT)
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


def test_list_box_items_and_selection_on_win32(window) -> None:
    list_box = window.list_box()
    list_box.wait_for(state="visible", timeout=5)
    assert list_box.items() == list(LIST_ITEMS)
    assert list_box.selected_items() == []

    list_box.select_item(LIST_ITEMS[1])
    assert list_box.selected_items() == [LIST_ITEMS[1]]
    _wait_status(window, f"list={LIST_ITEMS[1]}")


def test_tab_is_selected_by_exact_text_on_win32(window) -> None:
    tabs = window.tab(control_id=ID_TABS)
    tabs.wait_for(state="visible", timeout=5)
    # "&Advanced": the mnemonic does not have to be spelled out.
    tabs.select_tab("Advanced")
    _wait_status(window, "tab=Advanced")
    tabs.select_tab("General")
    _wait_status(window, "tab=General")
    with pytest.raises(ElementNotFoundError, match="General"):
        tabs.select_tab("Missing")


def test_text_reads_a_win32_label_without_a_pattern_timeout(window) -> None:
    status = window.locator(control_id=ID_STATUS)
    status.wait_for(timeout=5)
    started = time.monotonic()
    assert status.text() == STATUS_READY
    assert time.monotonic() - started < 2


def test_tab_with_the_buttons_style_really_changes_selection(window) -> None:
    # TCM_SETCURFOCUS alone only moves the focus on a TCS_BUTTONS control.
    tabs = window.tab(control_id=ID_BUTTON_TABS)
    tabs.select_tab("Two")
    _wait_status(window, "button-tab=Two")
    tabs.select_tab("One")
    _wait_status(window, "button-tab=One")


def test_native_menu_item_is_visible_only_while_its_popup_is_open(window) -> None:
    file_menu = window.menu("File")
    mark = file_menu.item("Mark")
    assert file_menu.is_visible()
    assert not mark.is_visible()
    assert mark.exists()
    assert mark.wait_until_hidden(timeout=0) is mark
    with pytest.raises(WaitTimeoutError, match="not on screen"):
        mark.wait_for(state="visible", timeout=0)

    hwnd = window._get_spec().wrapper_object().handle
    win32gui.PostMessage(hwnd, WM_OPEN_FILE_MENU, 0, 0)
    mark.wait_for(state="visible", timeout=10)
    assert mark.is_visible()
    win32gui.PostMessage(hwnd, win32con.WM_CANCELMODE, 0, 0)
    mark.wait_until_hidden(timeout=10)
    assert not mark.is_visible()


def test_all_and_count_apply_every_criterion(window) -> None:
    window.edit(control_id=ID_EDIT).set_text("Username")

    def count(**criteria) -> int:
        return window.locator(**criteria).count()

    assert count(control_type="Edit") == 2
    assert count(control_type="Edit", title="Missing") == 0
    assert count(control_type="Edit", title="Username") == 1
    assert count(control_type="Edit", control_id=ID_EDIT) == 1
    assert count(control_type="Edit", control_id=9999) == 0
    assert count(control_type="Edit", title_re="User.*") == 1
    # Not only a translated control_type: these used to return every control.
    assert count(control_id=ID_EDIT) == 1
    assert count(control_id=9999) == 0
    assert count(class_name_re="^ListBox$") == 1

    ids = [
        item._resolve().control_id()
        for item in window.locator(control_type="Edit", control_id=ID_EMPTY_EDIT).all()
    ]
    assert ids == [ID_EMPTY_EDIT]
    assert window.locator(control_type="Edit").nth(1).count() == 1
    assert window.locator(control_type="Edit").nth(9).count() == 0


def _control_id(locator) -> int:
    return locator.timeout(5)._resolve().wrapper_object().control_id()


def test_negative_index_counts_the_same_matches_as_the_final_lookup(window) -> None:
    window.edit(control_id=ID_EDIT).set_text("Username")
    edits = window.locator(control_type="Edit")
    assert _control_id(edits.nth(-1)) == ID_EMPTY_EDIT
    assert _control_id(edits.nth(-2)) == ID_EDIT
    assert _control_id(window.locator(control_type="Edit", title="Username").nth(-1)) == ID_EDIT
    assert _control_id(window.locator(control_type="Edit", control_id=ID_EDIT).nth(-1)) == ID_EDIT
    assert _control_id(window.locator(control_id=ID_EDIT).nth(-1)) == ID_EDIT
    assert not window.locator(control_type="Edit", control_id=9999).nth(-1).exists(timeout=0)


def test_negative_index_skips_a_hidden_control_only_when_visibility_counts(window) -> None:
    hidden = window.locator(control_id=ID_EMPTY_EDIT)._resolve().wrapper_object().handle
    win32gui.ShowWindow(hidden, win32con.SW_HIDE)
    edits = window.locator(control_type="Edit")
    # The last visible Edit, not an index past the end of the visible ones.
    assert _control_id(edits.nth(-1)) == ID_EDIT
    # Presence counts the hidden one: -2 is the first of two.
    assert edits.nth(-2).exists(timeout=0)
    assert not edits.nth(-3).exists(timeout=0)


def test_checkable_native_menu_item(window) -> None:
    file_menu = window.menu("File")
    grid = file_menu.item("Show grid")
    assert grid.is_checked() is False
    grid.check()
    assert grid.is_checked() is True
    _wait_status(window, "grid=1")

    # Already checked: the command must not be sent again, or it would flip back.
    file_menu.item("Mark").invoke()
    _wait_status(window, "menu=mark")
    grid.check()
    assert _status(window) == "menu=mark"
    assert grid.is_checked() is True

    grid.uncheck()
    assert grid.is_checked() is False
    _wait_status(window, "grid=0")

    assert file_menu.item("Mark").is_checked() is False
    with pytest.raises(MenuItemNotEnabled):
        file_menu.item("Disabled item").check()


def test_menu_caption_with_a_literal_ampersand(window) -> None:
    file_menu = window.menu("File")
    assert not file_menu.item("RD report").exists(timeout=0)
    file_menu.item("R&D report").invoke()
    _wait_status(window, "menu=rnd")


@pytest.mark.parametrize("control_id", [ID_EMPTY_LABEL, ID_EMPTY_EDIT], ids=["static", "edit"])
def test_empty_plain_control_reads_empty_without_the_clipboard(
    window, monkeypatch, control_id
) -> None:
    def must_not_run(_element):
        raise AssertionError("text() took focus and wiped the clipboard for an empty control")

    monkeypatch.setattr(locator_module, "_read_text_via_clipboard", must_not_run)
    control = window.locator(control_id=control_id)
    control.wait_for(state="exists", timeout=5)
    started = time.monotonic()
    assert control.text() == ""
    assert time.monotonic() - started < 2
