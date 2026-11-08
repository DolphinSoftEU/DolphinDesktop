"""KAN-747: UIA-style selectors and convenience controls on the win32 backend.

``Window.edit()`` / ``combo_box()`` / ``tree()`` / ``menu()`` speak UI
Automation control types, which pywinauto's win32 backend reads as the .NET
type of a WinForms control (and as an empty string for classic HWNDs). These
tests pin the translation to pywinauto's friendly class names, the immediate
error for types with no Win32 window class, and the win32 paths of ``Tree``
and native ``HMENU`` menus. The real-window counterpart is
``tests/component/test_win32_controls_windows.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import dolphin_desktop._element as element_module
import dolphin_desktop._locator as locator_module
import dolphin_desktop._win32_criteria as w32
from dolphin_desktop._element import Menu, MenuItem, Tree
from dolphin_desktop._exceptions import ElementNotFoundError, UnsupportedCapabilityError
from dolphin_desktop._locator import Locator

WIN32 = SimpleNamespace(name="win32")
UIA = SimpleNamespace(name="uia")


def _info(friendly: str) -> SimpleNamespace:
    """A fake win32 element_info whose friendly class name is *friendly*."""
    return SimpleNamespace(friendly=friendly)


@pytest.fixture
def friendly_names(monkeypatch):
    """Answer friendly class names from the fake element_info, not real HWNDs."""
    monkeypatch.setattr(
        w32, "win32_friendly_class_name", lambda info: getattr(info, "friendly", None)
    )


def _window(spec: object) -> SimpleNamespace:
    return SimpleNamespace(_get_spec=Mock(return_value=spec))


# Criteria translation


class TestCriteriaForBackend:
    def test_uia_and_unknown_scopes_keep_the_criteria_object(self):
        criteria = {"control_type": "Edit", "title": "Name"}
        assert w32.criteria_for_backend(SimpleNamespace(backend=UIA), criteria) is criteria
        assert w32.criteria_for_backend(Mock(), criteria) is criteria
        assert w32.criteria_for_backend(object(), criteria) is criteria

    def test_win32_without_control_type_is_untouched(self):
        criteria = {"title": "Name", "class_name": "Edit"}
        assert w32.criteria_for_backend(SimpleNamespace(backend=WIN32), criteria) is criteria

    @pytest.mark.parametrize(
        ("control_type", "accepted", "rejected"),
        [
            ("Edit", "Edit", "Static"),
            ("ComboBox", "ComboBox", "ListBox"),
            ("ComboBox", "ComboBoxEx", "Edit"),
            ("Tree", "TreeView", "ListView"),
            ("List", "ListBox", "TreeView"),
            ("List", "ListView", "ComboBox"),
            ("Button", "Button", "CheckBox"),
            ("CheckBox", "CheckBox", "Button"),
            ("RadioButton", "RadioButton", "CheckBox"),
            ("Group", "GroupBox", "Button"),
            ("Text", "Static", "Edit"),
            ("Tab", "TabControl", "Toolbar"),
            ("ToolBar", "Toolbar", "StatusBar"),
            # Win32 friendly names are accepted verbatim (Spy++ / pywinauto spelling).
            ("Static", "Static", "Edit"),
            ("TreeView", "TreeView", "Edit"),
            ("ListView", "ListView", "ListBox"),
        ],
    )
    def test_control_type_becomes_a_friendly_class_predicate(
        self, friendly_names, control_type, accepted, rejected
    ):
        criteria = {"control_type": control_type, "title": "Name"}
        translated = w32.criteria_for_backend(SimpleNamespace(backend=WIN32), criteria)

        assert "control_type" not in translated
        assert translated["title"] == "Name"
        predicate = translated["predicate_func"]
        assert predicate(_info(accepted)) is True
        assert predicate(_info(rejected)) is False
        assert predicate(_info(None)) is False
        assert criteria == {"control_type": control_type, "title": "Name"}

    def test_callers_predicate_is_combined_not_replaced(self, friendly_names):
        own = Mock(side_effect=lambda info: info.friendly == "Edit" and info.ok)
        translated = w32.criteria_for_backend(
            SimpleNamespace(backend=WIN32), {"control_type": "Edit", "predicate_func": own}
        )
        predicate = translated["predicate_func"]
        assert predicate(SimpleNamespace(friendly="Edit", ok=True)) is True
        assert predicate(SimpleNamespace(friendly="Edit", ok=False)) is False
        # The class check runs first, so the caller's predicate never sees a non-match.
        own.reset_mock()
        assert predicate(SimpleNamespace(friendly="Static", ok=True)) is False
        own.assert_not_called()

    @pytest.mark.parametrize(
        "control_type", ["System.Windows.Forms.TextBox", "SomeVendorControl", ""]
    )
    def test_dotted_net_types_and_unknown_names_pass_through(self, control_type):
        criteria = {"control_type": control_type}
        assert w32.criteria_for_backend(SimpleNamespace(backend=WIN32), criteria) is criteria

    @pytest.mark.parametrize(
        "control_type", ["MenuItem", "TreeItem", "ListItem", "Hyperlink", "Pane", "Window"]
    )
    def test_uia_types_without_a_win32_class_fail_at_once(self, control_type):
        with pytest.raises(UnsupportedCapabilityError, match=repr(control_type)) as caught:
            w32.criteria_for_backend(SimpleNamespace(backend=WIN32), {"control_type": control_type})
        assert "class_name" in str(caught.value)
        assert "Edit" in str(caught.value)

    def test_enumeration_criteria_splits_the_predicate_off(self, friendly_names):
        criteria = {"control_type": "Edit", "title": "Name"}
        assert w32.enumeration_criteria(SimpleNamespace(backend=UIA), criteria) == (
            criteria,
            None,
        )
        search, predicate = w32.enumeration_criteria(SimpleNamespace(backend=WIN32), criteria)
        assert search == {"title": "Name"}
        assert predicate(_info("Edit")) is True

    def test_friendly_class_name_of_a_non_window_is_none(self):
        assert w32.win32_friendly_class_name(object()) is None

    def test_backend_name_requires_a_string_name(self):
        assert w32.backend_name(SimpleNamespace(backend=WIN32)) == "win32"
        assert w32.backend_name(SimpleNamespace(backend=SimpleNamespace(name=Mock()))) is None
        assert w32.backend_name(object()) is None


# Locator resolution paths


class TestLocatorOnWin32:
    def _parent(self, **attrs) -> SimpleNamespace:
        return SimpleNamespace(backend=WIN32, **attrs)

    def test_resolve_sends_the_class_predicate_instead_of_control_type(self, friendly_names):
        child = Mock(name="child-spec")
        parent = self._parent(child_window=Mock(return_value=child))
        locator = Locator(_window(parent), control_type="Edit", title="Name")
        with patch.object(locator_module, "_wait_until_visible"):
            assert locator._resolve() is child
        kwargs = parent.child_window.call_args.kwargs
        assert "control_type" not in kwargs
        assert kwargs["title"] == "Name"
        assert kwargs["predicate_func"](_info("Edit")) is True
        assert locator._criteria == {"control_type": "Edit", "title": "Name"}

    def test_presence_resolution_is_translated_too(self, friendly_names):
        child = Mock(name="child-spec")
        parent = self._parent(child_window=Mock(return_value=child))
        locator = Locator(_window(parent), control_type="ComboBox")
        with patch.object(locator_module, "_wait_until_present"):
            assert locator._resolve_presence() is child
        kwargs = parent.child_window.call_args.kwargs
        assert kwargs["visible_only"] is False
        assert "control_type" not in kwargs
        assert kwargs["predicate_func"](_info("ComboBox")) is True

    def test_unsupported_control_type_fails_before_any_search_or_wait(self):
        parent = self._parent(child_window=Mock())
        locator = Locator(_window(parent), control_type="MenuItem", title="File")
        with patch.object(locator_module.time, "sleep", side_effect=AssertionError("polled")):
            with pytest.raises(UnsupportedCapabilityError, match="'MenuItem'"):
                locator._resolve()
            with pytest.raises(UnsupportedCapabilityError):
                locator._resolve_presence()
            with pytest.raises(UnsupportedCapabilityError):
                locator.wait_for(state="visible", timeout=10)
            with pytest.raises(UnsupportedCapabilityError):
                locator.exists(timeout=10)
        parent.child_window.assert_not_called()

    def test_unsupported_fallback_is_dropped_and_a_valid_one_is_translated(self, friendly_names):
        fallback_spec = Mock(name="fallback-spec")
        parent = self._parent(
            child_window=Mock(side_effect=[RuntimeError("primary missing"), fallback_spec])
        )
        locator = Locator(
            _window(parent),
            auto_id="renamed",
            fallback=[{"control_type": "MenuItem"}, {"control_type": "Edit", "title": "x"}],
        )
        with (
            patch.object(locator_module, "_wait_until_visible"),
            patch("dolphin_desktop._selfheal.record_fallback") as record,
        ):
            assert locator._resolve() is fallback_spec
        assert parent.child_window.call_count == 2
        assert parent.child_window.call_args_list[0].kwargs == {"auto_id": "renamed"}
        fallback_kwargs = parent.child_window.call_args_list[1].kwargs
        assert "control_type" not in fallback_kwargs
        assert fallback_kwargs["predicate_func"](_info("Edit")) is True
        record.assert_called_once_with(
            {"auto_id": "renamed"}, {"control_type": "Edit", "title": "x"}
        )

    def test_all_filters_enumerated_children_by_class(self, friendly_names):
        edit = SimpleNamespace(element_info=_info("Edit"))
        label = SimpleNamespace(element_info=_info("Static"))
        second_edit = SimpleNamespace(element_info=_info("Edit"))
        parent = self._parent(children=Mock(return_value=[edit, label, second_edit]))
        found = Locator(_window(parent), control_type="Edit", title="x").all()
        assert [item._element for item in found] == [edit, second_edit]
        # HwndElementInfo.children() ignores predicate_func, so it is never passed.
        parent.children.assert_called_once_with(title="x")

    def test_negative_index_counts_only_class_matches(self, friendly_names):
        edits = [SimpleNamespace(element_info=_info("Edit")) for _ in range(2)]
        label = SimpleNamespace(element_info=_info("Static"))
        wrapper = SimpleNamespace(descendants=Mock(return_value=[edits[0], label, edits[1]]))
        criteria = w32.criteria_for_backend(
            SimpleNamespace(backend=WIN32), {"control_type": "Edit", "found_index": -1}
        )
        with patch.object(locator_module, "_wrapper_object_until", return_value=wrapper):
            resolved = locator_module._resolve_negative_found_index(Mock(), criteria, timeout=0)
        assert resolved["found_index"] == 1
        assert "predicate_func" not in wrapper.descendants.call_args.kwargs

    def test_search_under_a_resolved_win32_wrapper_is_translated(self, friendly_names):
        found = [_info("Edit")]
        wrap = Mock(side_effect=lambda info: ("wrapped", info))
        parent = SimpleNamespace(
            backend=SimpleNamespace(name="win32", generic_wrapper_class=wrap),
        )
        with patch("pywinauto.findwindows.find_elements", return_value=found) as find:
            assert locator_module._find_under_wrapper(parent, {"control_type": "Edit"}, 0) == (
                "wrapped",
                found[0],
            )
        kwargs = find.call_args.kwargs
        assert "control_type" not in kwargs
        assert kwargs["predicate_func"](_info("Edit")) is True

        with pytest.raises(UnsupportedCapabilityError):
            locator_module._find_under_wrapper(parent, {"control_type": "TreeItem"}, 5)


# Tree on win32


def _win32_tree(item: object = None, **attrs) -> SimpleNamespace:
    return SimpleNamespace(
        backend=WIN32, get_item=Mock(return_value=item if item is not None else Mock()), **attrs
    )


def _tree_resolving_to(wrapper: object, *, timeout: float = 0) -> Tree:
    tree = Tree(Mock()).timeout(timeout)
    tree._resolve = Mock(return_value=wrapper)  # type: ignore[method-assign]
    return tree


class TestTreeOnWin32:
    def test_select_item_by_path_selects_through_the_tree_view_messages(self):
        item = Mock(name="child-node")
        wrapper = _win32_tree(item)
        tree = _tree_resolving_to(wrapper)
        assert tree.select_item_by_path("Root", "Child") is tree
        wrapper.get_item.assert_called_once_with(["Root", "Child"], exact=True)
        item.select.assert_called_once_with()

    def test_expand_item_expands_the_node(self):
        item = Mock(name="root-node")
        wrapper = _win32_tree(item)
        tree = _tree_resolving_to(wrapper)
        assert tree.expand_item("Root") is tree
        wrapper.get_item.assert_called_once_with(["Root"], exact=True)
        item.expand.assert_called_once_with()

    def test_missing_node_reports_the_path_and_pywinauto_reason(self):
        wrapper = _win32_tree()
        wrapper.get_item.side_effect = IndexError("Item 'Root' does not have a child 'Nope'")
        tree = _tree_resolving_to(wrapper)
        with pytest.raises(ElementNotFoundError, match=r"'Root -> Nope'.*does not have a child"):
            tree.select_item_by_path("Root", "Nope")

    def test_empty_tree_is_reported_as_such(self):
        wrapper = SimpleNamespace(backend=WIN32, get_item=Mock(return_value=None))
        tree = _tree_resolving_to(wrapper)
        with pytest.raises(ElementNotFoundError, match="no items"):
            tree.expand_item("Root")

    def test_node_added_late_is_found_by_polling(self, monkeypatch):
        monkeypatch.setattr(element_module.time, "sleep", Mock())
        item = Mock(name="late-node")
        wrapper = _win32_tree()
        wrapper.get_item.side_effect = [IndexError("not yet"), item]
        tree = _tree_resolving_to(wrapper, timeout=5)
        tree.select_item_by_path("Root")
        item.select.assert_called_once_with()

    def test_uia_tree_keeps_the_tree_item_path(self):
        wrapper = SimpleNamespace(backend=UIA, get_item=Mock())
        tree = _tree_resolving_to(wrapper)
        assert tree._win32_tree(wrapper) is None


# Native Win32 menus


class _NativeItem:
    def __init__(self, text: str, sub_menu: object = None, *, enabled: bool = True) -> None:
        self._text = text
        self._sub_menu = sub_menu
        self._enabled = enabled
        self.select = Mock()
        self.click_input = Mock()
        self.rectangle = Mock(return_value="rect")

    def text(self) -> str:
        return self._text

    def sub_menu(self) -> object:
        return self._sub_menu

    def is_enabled(self) -> bool:
        return self._enabled


class _NativeMenu:
    def __init__(self, *items: _NativeItem) -> None:
        self._items = list(items)

    def items(self) -> list[_NativeItem]:
        return list(self._items)


def _menu_window(menu_bar: object, backend: SimpleNamespace = WIN32) -> SimpleNamespace:
    owner = SimpleNamespace(menu=Mock(return_value=menu_bar))
    return _window(SimpleNamespace(backend=backend, wrapper_object=Mock(return_value=owner)))


@pytest.fixture
def native_menu():
    show = _NativeItem("Show &dialog\tCtrl+D")
    disabled = _NativeItem("Export", enabled=False)
    file_menu = _NativeItem("&File", _NativeMenu(show, _NativeItem(""), disabled))
    window = _menu_window(_NativeMenu(file_menu, _NativeItem("&Help", _NativeMenu())))
    return SimpleNamespace(window=window, file=file_menu, show=show, disabled=disabled)


class TestNativeWin32Menu:
    def test_invoke_selects_the_item_without_mouse_input(self, native_menu):
        item = Menu(native_menu.window, title="File", control_type="MenuItem").item("Show dialog")
        item.invoke()
        native_menu.show.select.assert_called_once_with()
        native_menu.show.click_input.assert_not_called()

    def test_click_is_a_physical_click_on_the_native_item(self, native_menu):
        Menu(native_menu.window, title="File").item("Show dialog").click()
        native_menu.show.click_input.assert_called_once_with()
        Menu(native_menu.window, title="File").click()
        native_menu.file.click_input.assert_called_once_with()

    def test_state_reads_come_from_the_menu_handle(self, native_menu):
        menu = Menu(native_menu.window, title="File")
        assert menu.item("Show dialog").is_enabled() is True
        assert menu.item("Export").is_enabled() is False
        assert menu.item("Show dialog").is_visible() is True
        assert menu.item("Show dialog").exists() is True
        native_menu.show.select.assert_not_called()
        native_menu.show.click_input.assert_not_called()

    def test_adapter_reports_text_without_mnemonic_or_shortcut(self, native_menu):
        entry = element_module._Win32MenuEntry(native_menu.show)
        assert entry.window_text() == "Show dialog"
        assert entry.rectangle() == "rect"

    def test_missing_item_lists_what_the_level_offers(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Nope").timeout(0)
        with pytest.raises(ElementNotFoundError, match=r"'File -> Nope'.*Show dialog.*Export"):
            item.invoke()
        assert item.exists() is False

    def test_item_under_a_leaf_reports_the_missing_submenu(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Show dialog").item("Deeper")
        with pytest.raises(ElementNotFoundError, match="has no submenu"):
            item.timeout(0).invoke()

    def test_window_without_a_native_menu_fails_at_once_with_a_hint(self):
        window = _menu_window(None)
        item = Menu(window, title="File").item("Open")
        with patch.object(element_module.time, "sleep", side_effect=AssertionError("polled")):
            with pytest.raises(ElementNotFoundError, match="no native Win32 menu") as caught:
                item.invoke()
        assert "MenuStrip" in str(caught.value)

    def test_menu_chained_under_a_locator_still_uses_the_window_menu(self, native_menu):
        pane = Locator(native_menu.window, title="pane")
        Menu(pane, title="File").item("Show dialog").invoke()
        native_menu.show.select.assert_called_once_with()

    def test_uia_window_keeps_the_ui_automation_menu_path(self, native_menu):
        window = _menu_window(_NativeMenu(), backend=UIA)
        assert element_module._native_menu_root(Menu(window, title="File").item("Open")) is None

    def test_menu_without_title_keeps_the_ui_automation_path(self, native_menu):
        assert element_module._native_menu_root(Menu(native_menu.window)) is None

    def test_menu_item_class_is_unchanged_for_callers(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Show dialog")
        assert isinstance(item, MenuItem)
        assert item._criteria == {"title": "Show dialog", "control_type": "MenuItem"}
