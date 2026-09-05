"""Tests for specialized UI elements."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import dolphin_desktop._element as element_module
from dolphin_desktop._element import (
    Button,
    CheckBox,
    ComboBox,
    Edit,
    ListBox,
    Menu,
    MenuItem,
    RadioButton,
    Tab,
    Toolbar,
    Tree,
)
from dolphin_desktop._exceptions import ElementNotFoundError, UnsupportedPatternError
from dolphin_desktop._locator import Locator


def _root() -> Mock:
    root = Mock()
    root._get_spec.return_value = Mock()
    return root


def test_simple_element_types_and_combo_selection_fallback() -> None:
    root = _root()
    assert isinstance(Button(root), Button)
    assert isinstance(Edit(root), Edit)
    assert isinstance(CheckBox(root), CheckBox)
    assert isinstance(Toolbar(root), Toolbar)

    element = Mock()
    element.selected_item.return_value = "English"
    combo = ComboBox(root)
    combo._resolve = Mock(return_value=element)
    assert combo.selected_item() == "English"

    element.selected_item.side_effect = RuntimeError("unsupported wrapper")
    element.window_text.return_value = "Deutsch"
    assert combo.selected_item() == "Deutsch"


def test_radio_select_uses_selection_pattern_then_check_fallback(monkeypatch) -> None:
    radio = RadioButton(_root(), title="Choice")
    monkeypatch.setattr(Locator, "select", lambda self: self)
    assert radio.select() is radio

    monkeypatch.setattr(
        Locator,
        "select",
        Mock(side_effect=UnsupportedPatternError("no selection pattern")),
    )
    check = Mock()
    monkeypatch.setattr(radio, "check", check)
    assert radio.select() is radio
    check.assert_called_once_with()


def test_radio_checked_reads_selection_then_check_state(monkeypatch) -> None:
    radio = RadioButton(_root())
    selected = Mock(is_selected=Mock(return_value=1))
    radio._resolve = Mock(return_value=selected)
    assert radio.is_checked() is True

    selected.is_selected.side_effect = RuntimeError("no selection pattern")
    monkeypatch.setattr(element_module, "_require_check_state", lambda element, action: 1)
    assert radio.is_checked() is True
    monkeypatch.setattr(element_module, "_require_check_state", lambda element, action: 0)
    assert radio.is_checked() is False


def test_menu_and_toolbar_factories_create_typed_children() -> None:
    menu = Menu(_root(), title="File")
    item = menu.item("Open")
    assert isinstance(item, MenuItem)
    assert item._criteria == {"title": "Open", "control_type": "MenuItem"}
    nested = item.item("Recent")
    assert isinstance(nested, MenuItem)
    assert nested._parent is item

    toolbar = Toolbar(_root())
    button = toolbar.button("Save")
    assert isinstance(button, Button)
    assert button._criteria == {"title": "Save", "control_type": "Button"}


def _menu_item_with_parent(parent_element: Mock) -> MenuItem:
    parent = Menu(_root(), title="File")
    parent._resolve = Mock(return_value=parent_element)
    return parent.item("Open")


def test_menu_item_resolve_uses_parent_scope_first(monkeypatch) -> None:
    parent_element = Mock()
    child_spec = Mock()
    parent_element.child_window.return_value = child_spec
    item = _menu_item_with_parent(parent_element)
    monkeypatch.setattr(element_module.time, "sleep", Mock())

    assert item._resolve() is child_spec
    parent_element.click_input.assert_called_once_with()
    child_spec.wait.assert_called_once_with("exists visible", timeout=4.0)


def test_menu_item_resolve_falls_back_to_invoke_root_popup_and_error(monkeypatch) -> None:
    monkeypatch.setattr(element_module.time, "sleep", Mock())

    parent_element = Mock()
    parent_element.click_input.side_effect = RuntimeError("locked")
    parent_element.child_window.side_effect = RuntimeError("no child")
    item = _menu_item_with_parent(parent_element)
    root_spec = Mock()
    root_child = Mock()
    root_spec.child_window.return_value = root_child
    item._root_window_spec = Mock(return_value=root_spec)
    assert item._resolve() is root_child
    parent_element.invoke.assert_called_once_with()
    root_child.wait.assert_called_once_with("exists visible", timeout=3.0)

    parent_element = Mock()
    parent_element.click_input.side_effect = RuntimeError("locked")
    parent_element.invoke.side_effect = RuntimeError("no invoke")
    parent_element.child_window.side_effect = RuntimeError("no child")
    item = _menu_item_with_parent(parent_element)
    root_spec = Mock()
    root_spec.child_window.side_effect = RuntimeError("no root")
    item._root_window_spec = Mock(return_value=root_spec)
    item._find_in_popup_windows = Mock(return_value=Mock())
    assert item._resolve() is item._find_in_popup_windows.return_value

    item._find_in_popup_windows = Mock(return_value=None)
    with pytest.raises(ElementNotFoundError, match="MenuItem"):
        item._resolve()
    item._find_in_popup_windows = Mock(side_effect=RuntimeError("popup probe failed"))
    with pytest.raises(ElementNotFoundError, match="MenuItem"):
        item._resolve()


def test_menu_item_resolve_and_readonly_delegate_for_non_menu_parent(monkeypatch) -> None:
    parent = Locator(_root(), title="container")
    item = MenuItem(parent, title="Open", control_type="MenuItem")
    resolved = object()
    monkeypatch.setattr(Locator, "_resolve", Mock(return_value=resolved))
    monkeypatch.setattr(Locator, "_resolve_readonly", Mock(return_value=resolved))
    assert item._resolve() is resolved
    assert item._resolve_readonly() is resolved


def test_menu_item_readonly_finds_open_item_and_polls_until_timeout(monkeypatch) -> None:
    monkeypatch.setattr(element_module.time, "sleep", Mock())
    parent = Menu(_root(), title="File")
    item = parent.item("Open")
    found = object()
    item._find_open_item = Mock(return_value=found)
    assert item._resolve_readonly() is found

    item._timeout = 0.5
    item._find_open_item = Mock(side_effect=[None, None])
    monkeypatch.setattr(element_module.time, "monotonic", Mock(side_effect=[0.0, 0.0, 1.0]))
    with pytest.raises(ElementNotFoundError, match="not currently in the UI tree"):
        item._resolve_readonly()
    element_module.time.sleep.assert_called_once_with(0.1)


def test_menu_item_find_open_item_checks_parent_root_and_popup(monkeypatch) -> None:
    parent = Menu(_root(), title="File")
    item = parent.item("Open")
    parent_probe = Mock()
    parent_probe._resolve_readonly.return_value = Mock()
    parent.timeout = Mock(return_value=parent_probe)
    parent_scope = parent_probe._resolve_readonly.return_value
    parent_match = Mock()
    parent_scope.child_window.return_value = parent_match
    item._root_window_spec = Mock(return_value=Mock())
    assert item._find_open_item() is parent_match
    parent_match.wait.assert_called_once_with("exists visible", timeout=0)

    parent_scope.child_window.side_effect = RuntimeError("parent miss")
    root_scope = item._root_window_spec.return_value
    root_scope.child_window.side_effect = RuntimeError("root miss")
    popup = object()
    item._find_in_popup_windows = Mock(return_value=popup)
    assert item._find_open_item() is popup
    parent.timeout.side_effect = RuntimeError("parent menu disappeared")
    assert item._find_open_item() is popup


def test_menu_item_find_open_item_handles_non_locator_parent_and_popup_none() -> None:
    parent = SimpleNamespace()
    item = MenuItem(parent, title="Open", control_type="MenuItem")
    root_scope = Mock()
    root_scope.child_window.side_effect = RuntimeError("root miss")
    item._root_window_spec = Mock(return_value=root_scope)
    item._find_in_popup_windows = Mock(return_value=None)
    assert item._find_open_item() is None


def test_menu_item_root_window_spec_walks_nested_locators() -> None:
    root = SimpleNamespace(_get_spec=Mock(return_value="root-spec"))
    outer = Locator(root, title="Window")
    inner = Menu(outer, title="File")
    item = inner.item("Open")
    assert item._root_window_spec() == "root-spec"
    root._get_spec.assert_called_once_with()


def test_menu_item_popup_search_succeeds_and_handles_all_failure_modes(monkeypatch) -> None:
    import pywinauto

    root_spec = Mock()
    root_spec.wrapper_object.return_value.process_id.return_value = 77
    item = MenuItem(SimpleNamespace(_get_spec=Mock(return_value=root_spec)), title="Open")
    top = Mock()
    match = Mock()
    top.child_window.return_value = match
    monkeypatch.setattr(
        pywinauto,
        "Desktop",
        Mock(return_value=SimpleNamespace(windows=Mock(return_value=[top]))),
    )
    monkeypatch.setattr(element_module.time, "monotonic", Mock(side_effect=[0.0]))
    assert item._find_in_popup_windows(timeout=0) is match
    top.child_window.assert_called_once_with(title="Open")

    root_spec.wrapper_object.side_effect = RuntimeError("closed")
    assert item._find_in_popup_windows(timeout=0) is None

    root_spec.wrapper_object.side_effect = None
    top.child_window.side_effect = RuntimeError("not this popup")
    desktop = Mock()
    desktop.windows.return_value = [top]
    monkeypatch.setattr(pywinauto, "Desktop", Mock(return_value=desktop))
    monkeypatch.setattr(element_module.time, "monotonic", Mock(side_effect=[0.0, 1.0]))
    assert item._find_in_popup_windows(timeout=0) is None
    desktop.windows.side_effect = RuntimeError("desktop unavailable")
    monkeypatch.setattr(element_module.time, "monotonic", Mock(side_effect=[0.0, 1.0]))
    assert item._find_in_popup_windows(timeout=0) is None


def test_menu_item_popup_search_polls_when_popups_are_empty(monkeypatch) -> None:
    import pywinauto

    root_spec = Mock()
    root_spec.wrapper_object.return_value.process_id.return_value = 88
    item = MenuItem(SimpleNamespace(_get_spec=Mock(return_value=root_spec)), title="Open")
    desktop = Mock()
    desktop.windows.return_value = []
    monkeypatch.setattr(pywinauto, "Desktop", Mock(return_value=desktop))
    monkeypatch.setattr(element_module.time, "monotonic", Mock(side_effect=[0.0, 0.0, 2.0]))
    sleep = Mock()
    monkeypatch.setattr(element_module.time, "sleep", sleep)
    assert item._find_in_popup_windows(timeout=1) is None
    sleep.assert_called_once_with(0.1)


def test_tree_expansion_and_selection_cover_pattern_and_click_fallbacks(monkeypatch) -> None:
    monkeypatch.setattr(element_module.time, "sleep", Mock())
    tree = Tree(_root())

    first = Mock()
    second = Mock()
    second.expand.side_effect = RuntimeError("no expand pattern")
    container = Mock()
    container.child_window.return_value = first
    first.child_window.return_value = second
    tree._resolve = Mock(return_value=container)
    assert tree.expand_item("Root", "Branch") is tree
    first.expand.assert_called_once_with()
    second.click_input.assert_called_once_with()

    tree._resolve = Mock(return_value=container)
    assert tree.select_item_by_path() is tree

    parent = Mock()
    parent.expand.side_effect = RuntimeError("no expand")
    final = Mock()
    final.select.side_effect = RuntimeError("no selection")
    container = Mock()
    container.child_window.return_value = parent
    parent.child_window.return_value = final
    tree._resolve = Mock(return_value=container)
    assert tree.select_item_by_path("Root", "Leaf") is tree
    parent.click_input.assert_called_once_with()
    final.click_input.assert_called_once_with()


def test_listbox_items_and_selection_use_toggle_fallbacks() -> None:
    listbox = ListBox(_root())
    one = Mock()
    one.window_text.return_value = "One"
    one.is_selected.return_value = True
    two = Mock()
    two.window_text.return_value = "Two"
    two.is_selected.return_value = False
    three = Mock()
    three.window_text.return_value = "Three"
    three.is_selected.side_effect = RuntimeError("not supported")
    three.get_toggle_state.return_value = True
    four = Mock()
    four.window_text.return_value = "Four"
    four.is_selected.side_effect = RuntimeError("not supported")
    four.get_toggle_state.return_value = False
    five = Mock()
    five.window_text.return_value = "Five"
    five.is_selected.side_effect = RuntimeError("not supported")
    five.get_toggle_state.side_effect = RuntimeError("not toggleable")
    element = Mock()
    element.children.return_value = [one, two, three, four, five]
    listbox._resolve = Mock(return_value=element)

    assert listbox.items() == ["One", "Two", "Three", "Four", "Five"]
    assert listbox.selected_items() == ["One", "Three"]
    element.children.assert_called_with(control_type="ListItem")


def test_tab_selects_directly_then_searches_children_and_raises() -> None:
    tab = Tab(_root(), title="Settings")
    direct = Mock()
    element = Mock()
    element.child_window.return_value = direct
    tab._resolve = Mock(return_value=element)
    assert tab.select_tab("General") is tab
    direct.click_input.assert_called_once_with()

    direct.click_input.side_effect = RuntimeError("not direct")
    child = Mock()
    child.window_text.return_value = "General"
    tab._resolve = Mock(
        return_value=SimpleNamespace(
            child_window=lambda **_: direct,
            children=lambda: [child],
        )
    )
    assert tab.select_tab("General") is tab
    child.click_input.assert_called_once_with()

    bad_child = Mock()
    bad_child.window_text.side_effect = RuntimeError("disappeared")
    missing = SimpleNamespace(
        child_window=lambda **_: Mock(click_input=Mock(side_effect=RuntimeError("no direct"))),
        children=lambda: [SimpleNamespace(window_text=lambda: "Other"), bad_child],
    )
    tab._resolve = Mock(return_value=missing)
    with pytest.raises(ElementNotFoundError, match="Tab page"):
        tab.select_tab("Missing")


def test_element_listbox_and_tab_use_wrapper_fallbacks() -> None:
    from dolphin_desktop._element import ListBox, Tab

    selected = SimpleNamespace(is_selected=lambda: True, window_text=lambda: "One")
    toggled = SimpleNamespace(
        is_selected=lambda: (_ for _ in ()).throw(AttributeError()),
        get_toggle_state=lambda: True,
        window_text=lambda: "Two",
    )
    listbox = ListBox.__new__(ListBox)
    listbox._resolve = Mock(return_value=SimpleNamespace(children=lambda **_: [selected, toggled]))
    assert listbox.selected_items() == ["One", "Two"]

    tab = Tab.__new__(Tab)
    tab._criteria = {"title": "Settings"}
    child = Mock(window_text=lambda: "General")
    missing_tab = Mock(click_input=Mock(side_effect=RuntimeError()))
    tab._resolve = Mock(
        return_value=SimpleNamespace(child_window=lambda **_: missing_tab, children=lambda: [child])
    )
    assert tab.select_tab("General") is tab
    child.click_input.assert_called_once()


def test_specialized_elements_cover_selection_tree_list_tab_and_toolbar(monkeypatch) -> None:
    import dolphin_desktop._element as element

    combo = element.ComboBox.__new__(element.ComboBox)
    combo._resolve = Mock(return_value=SimpleNamespace(window_text=lambda: "Fallback"))
    assert combo.selected_item() == "Fallback"

    radio_target = SimpleNamespace(get_toggle_state=lambda: 0, toggle=Mock())
    radio = element.RadioButton.__new__(element.RadioButton)
    radio._criteria = {}
    radio._resolve = Mock(return_value=radio_target)
    assert radio.select() is radio
    radio_target.toggle.assert_called_once_with()
    radio_target.get_toggle_state = lambda: 1
    assert radio.is_checked() is True

    root = Mock()
    first = Mock()
    first.expand.side_effect = RuntimeError("no expand pattern")
    root.child_window.return_value = first
    tree = element.Tree.__new__(element.Tree)
    tree._resolve = Mock(return_value=root)
    tree._timeout = 0.1
    tree._criteria = {}
    monkeypatch.setattr(element.time, "sleep", Mock())
    assert tree.expand_item("Root", "Child") is tree
    assert first.click_input.call_count == 1
    assert tree.select_item_by_path() is tree
    assert tree.select_item_by_path("Leaf") is tree

    selected = SimpleNamespace(is_selected=lambda: True, window_text=lambda: "Selected")
    toggled = Mock()
    toggled.is_selected.side_effect = RuntimeError("no selection pattern")
    toggled.get_toggle_state.return_value = 1
    toggled.window_text.return_value = "Toggled"
    list_root = Mock()
    list_root.children.return_value = [selected, toggled]
    list_box = element.ListBox.__new__(element.ListBox)
    list_box._resolve = Mock(return_value=list_root)
    assert list_box.items() == ["Selected", "Toggled"]
    assert list_box.selected_items() == ["Selected", "Toggled"]

    tab_root = Mock()
    tab_item = Mock()
    tab_item.click_input.side_effect = RuntimeError("not direct")
    tab_child = Mock()
    tab_child.window_text.return_value = "Advanced"
    tab_root.child_window.return_value = tab_item
    tab_root.children.return_value = [tab_child]
    tab = element.Tab.__new__(element.Tab)
    tab._resolve = Mock(return_value=tab_root)
    tab._criteria = {"control_type": "Tab"}
    assert tab.select_tab("Advanced") is tab
    tab_root.child_window.assert_called_once_with(title="Advanced", control_type="TabItem")

    toolbar = element.Toolbar.__new__(element.Toolbar)
    assert toolbar.button("Save")._criteria == {"title": "Save", "control_type": "Button"}


def test_menu_item_opens_parent_and_resolves_direct_child(monkeypatch) -> None:
    import dolphin_desktop._element as element

    root = Mock()
    parent = Mock()
    child_spec = Mock()
    parent.child_window.return_value = child_spec
    child_spec.wait.return_value = None
    menu = element.Menu(root, title="File", control_type="MenuItem")
    menu._resolve = Mock(return_value=parent)
    item = menu.item("Open")
    monkeypatch.setattr(element.time, "sleep", Mock())
    assert item._resolve() is child_spec
    parent.click_input.assert_called_once_with()
    parent.child_window.assert_called_once_with(title="Open", control_type="MenuItem")


def test_element_specializations_use_control_specific_methods() -> None:
    from dolphin_desktop._element import ComboBox, RadioButton

    combo = ComboBox.__new__(ComboBox)
    combo._resolve = Mock(return_value=SimpleNamespace(selected_item=lambda: "English"))
    assert combo.selected_item() == "English"
    radio = RadioButton.__new__(RadioButton)
    radio._criteria = {}
    radio._resolve = Mock(return_value=SimpleNamespace(select=lambda: None))
    assert radio.select() is radio
