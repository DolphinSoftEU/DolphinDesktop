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
from dolphin_desktop._element import ListBox, Menu, MenuItem, Tab, Tree
from dolphin_desktop._exceptions import (
    ElementNotFoundError,
    UnsupportedCapabilityError,
    WaitTimeoutError,
)
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


CLOSED_RECT = SimpleNamespace(left=0, top=0, right=0, bottom=0)
OPEN_RECT = SimpleNamespace(left=10, top=20, right=110, bottom=40)


class _NativeItem:
    def __init__(self, text: str, sub_menu: object = None, *, enabled: bool = True) -> None:
        self._text = text
        self._sub_menu = sub_menu
        self._enabled = enabled
        self.select = Mock()
        self.click_input = Mock()
        # GetMenuItemRect answers an empty rectangle while the popup is closed.
        self.rectangle = Mock(return_value=CLOSED_RECT)
        self.ctrl = SimpleNamespace(is_visible=Mock(return_value=True))

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
    file_menu.rectangle.return_value = OPEN_RECT  # the menu bar is always laid out
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
        assert menu.item("Show dialog").exists() is True
        native_menu.show.select.assert_not_called()
        native_menu.show.click_input.assert_not_called()

    def test_adapter_reports_text_without_mnemonic_or_shortcut(self, native_menu):
        entry = element_module._Win32MenuEntry(native_menu.show)
        assert entry.window_text() == "Show dialog"
        assert entry.rectangle() is CLOSED_RECT

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


# List boxes and list views on win32


def _element_resolving_to(cls: type, wrapper: object):
    element = cls(Mock()).timeout(0)
    element._resolve = Mock(return_value=wrapper)
    return element


def _list_row(text: str, *, selected: bool = False) -> SimpleNamespace:
    return SimpleNamespace(text=lambda: text, is_selected=lambda: selected)


class TestListOnWin32:
    def _list_box(self, indices: tuple[int, ...] = ()) -> SimpleNamespace:
        return SimpleNamespace(
            backend=WIN32,
            friendly_class_name=lambda: "ListBox",
            item_texts=Mock(return_value=["Alpha", "Beta"]),
            selected_indices=Mock(return_value=indices),
        )

    def _list_view(self, *rows: SimpleNamespace) -> SimpleNamespace:
        return SimpleNamespace(
            backend=WIN32,
            friendly_class_name=lambda: "ListView",
            item_count=lambda: len(rows),
            get_item=lambda index: rows[index],
        )

    def test_list_box_reads_items_and_selection_from_messages(self):
        element = _element_resolving_to(ListBox, self._list_box((1,)))
        assert element.items() == ["Alpha", "Beta"]
        assert element.selected_items() == ["Beta"]

    def test_single_selection_list_box_without_a_selection_is_empty(self):
        # LB_GETCURSEL answers LB_ERR (-1), which must not index the last item.
        element = _element_resolving_to(ListBox, self._list_box((-1,)))
        assert element.selected_items() == []

    def test_list_view_reports_the_first_column_of_each_row(self):
        wrapper = self._list_view(_list_row("One"), _list_row("Two", selected=True))
        element = _element_resolving_to(ListBox, wrapper)
        assert element.items() == ["One", "Two"]
        assert element.selected_items() == ["Two"]

    @pytest.mark.parametrize(
        "wrapper",
        [
            SimpleNamespace(backend=UIA, friendly_class_name=lambda: "ListBox"),
            SimpleNamespace(backend=WIN32, friendly_class_name=lambda: "ComboBox"),
            SimpleNamespace(backend=WIN32),
        ],
        ids=["uia", "other-win32-class", "no-friendly-name"],
    )
    def test_anything_else_keeps_the_ui_automation_path(self, wrapper):
        assert ListBox._win32_list(wrapper) is None


# Tab controls on win32


class TestTabOnWin32:
    def _tabs(self, *texts: str) -> SimpleNamespace:
        state = {"selected": 0}
        return SimpleNamespace(
            backend=WIN32,
            tab_count=lambda: len(texts),
            get_tab_text=lambda index: texts[index],
            select=Mock(side_effect=lambda index: state.update(selected=index)),
            get_selected_tab=lambda: state["selected"],
        )

    def test_refused_selection_is_reported_not_passed_off_as_success(self):
        # A missed TCS_BUTTONS click or a TCN_SELCHANGING veto leaves the old tab.
        wrapper = self._tabs("General", "Advanced")
        wrapper.select = Mock()
        tab = _element_resolving_to(Tab, wrapper)
        with pytest.raises(WaitTimeoutError, match=r"'Advanced' was not selected.*index 0"):
            tab.select_tab("Advanced")
        wrapper.select.assert_called_once_with(1)

    def test_selection_that_lands_late_is_accepted(self, monkeypatch):
        monkeypatch.setattr(element_module.time, "sleep", Mock())
        wrapper = self._tabs("General", "Advanced")
        wrapper.select = Mock()
        wrapper.get_selected_tab = Mock(side_effect=[0, 1])
        tab = Tab(Mock()).timeout(5)
        tab._resolve = Mock(return_value=wrapper)
        assert tab.select_tab("Advanced") is tab

    def test_refused_selection_does_not_wait_the_whole_locator_timeout(self):
        wrapper = self._tabs("General", "Advanced")
        wrapper.select = Mock()
        tab = Tab(Mock()).timeout(60)
        tab._resolve = Mock(return_value=wrapper)
        with (
            patch.object(element_module.time, "monotonic", side_effect=[0.0, 0.0, 2.5]),
            patch.object(element_module.time, "sleep") as sleep,
        ):
            with pytest.raises(WaitTimeoutError):
                tab.select_tab("Advanced")
        sleep.assert_called_once()

    def test_tab_is_selected_by_index_of_its_exact_text(self):
        wrapper = self._tabs("General", "Advanced")
        tab = _element_resolving_to(Tab, wrapper)
        assert tab.select_tab("Advanced") is tab
        wrapper.select.assert_called_once_with(1)

    def test_mnemonic_does_not_have_to_be_spelled_out(self):
        wrapper = self._tabs("General", "&Advanced")
        _element_resolving_to(Tab, wrapper).select_tab("Advanced")
        wrapper.select.assert_called_once_with(1)
        wrapper.select.reset_mock()
        _element_resolving_to(Tab, wrapper).select_tab("&Advanced")
        wrapper.select.assert_called_once_with(1)

    def test_near_match_is_refused_rather_than_best_matched(self):
        wrapper = self._tabs("General", "Advanced")
        tab = _element_resolving_to(Tab, wrapper)
        with pytest.raises(ElementNotFoundError, match=r"'Gener'.*General.*Advanced"):
            tab.select_tab("Gener")
        wrapper.select.assert_not_called()

    def test_uia_tab_keeps_the_tab_item_path(self):
        item = Mock(name="tab-item")
        wrapper = SimpleNamespace(
            backend=UIA, get_tab_text=Mock(), child_window=Mock(return_value=item)
        )
        _element_resolving_to(Tab, wrapper).select_tab("General")
        wrapper.child_window.assert_called_once_with(title="General", control_type="TabItem")
        item.click_input.assert_called_once_with()
        wrapper.get_tab_text.assert_not_called()


# UIA pattern probes must not stall on a win32 WindowSpecification


class _LazySpec:
    """A WindowSpecification stand-in that records patterns probed on itself.

    A real win32 spec answers ``iface_*`` with ``child_window(best_match=...)``
    and only fails after its default ~5 s wait, so probing it is the bug.
    """

    def __init__(self, wrapper: object, *, text: str = "ready") -> None:
        self._wrapper = wrapper
        self._text = text
        self.probed: list[str] = []

    def wrapper_object(self) -> object:
        return self._wrapper

    @property
    def iface_text(self) -> object:
        self.probed.append("iface_text")
        raise RuntimeError("waited for a child window named iface_text")

    @property
    def iface_scroll_item(self) -> object:
        self.probed.append("iface_scroll_item")
        raise RuntimeError("waited for a child window named iface_scroll_item")

    def window_text(self) -> str:
        return self._text

    def set_focus(self) -> None:
        self.focused = True


class TestPatternProbesUseTheWrapper:
    def _locator(self, spec: _LazySpec) -> Locator:
        locator = Locator(Mock())
        locator._resolve = Mock(return_value=spec)
        locator._resolve_readonly = Mock(return_value=spec)
        return locator

    def test_text_probes_the_wrapper_and_falls_back_to_the_window_text(self):
        spec = _LazySpec(SimpleNamespace())
        assert self._locator(spec).text() == "ready"
        assert spec.probed == []

    def test_text_still_reads_a_text_pattern_from_the_wrapper(self):
        pattern = SimpleNamespace(DocumentRange=SimpleNamespace(GetText=lambda _n: "from pattern"))
        spec = _LazySpec(SimpleNamespace(iface_text=pattern))
        assert self._locator(spec).text() == "from pattern"
        assert spec.probed == []

    def test_scroll_into_view_probes_the_wrapper(self):
        scroll = Mock()
        spec = _LazySpec(SimpleNamespace(iface_scroll_item=SimpleNamespace(ScrollIntoView=scroll)))
        self._locator(spec).scroll_into_view()
        scroll.assert_called_once_with()
        assert spec.probed == []

    def test_scroll_into_view_without_the_pattern_falls_back_to_focus(self):
        spec = _LazySpec(SimpleNamespace())
        self._locator(spec).scroll_into_view()
        assert spec.focused is True
        assert spec.probed == []


# PR #62 review: on-screen state of native menus


class TestNativeMenuVisibility:
    def test_item_is_visible_only_while_its_popup_is_open(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Show dialog")
        assert item.is_visible() is False
        assert item.exists() is True
        native_menu.show.rectangle.return_value = OPEN_RECT
        assert item.is_visible() is True
        native_menu.show.rectangle.return_value = CLOSED_RECT
        assert item.is_visible() is False

    def test_menu_bar_item_follows_its_owner_window(self, native_menu):
        menu = Menu(native_menu.window, title="File")
        assert menu.is_visible() is True
        native_menu.file.ctrl.is_visible.return_value = False
        assert menu.is_visible() is False

    def test_wait_until_hidden_returns_once_the_popup_closed(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Show dialog")
        assert item.wait_until_hidden(timeout=0) is item
        native_menu.show.rectangle.return_value = OPEN_RECT
        with pytest.raises(WaitTimeoutError, match="still visible"):
            item.wait_until_hidden(timeout=0)

    def test_wait_for_visible_waits_for_the_popup_without_opening_it(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Show dialog")
        with pytest.raises(WaitTimeoutError, match="not on screen") as caught:
            item.wait_for(state="visible", timeout=0)
        assert "menu.click()" in str(caught.value)
        native_menu.file.click_input.assert_not_called()

        native_menu.show.rectangle.side_effect = [CLOSED_RECT, OPEN_RECT]
        with patch.object(element_module.time, "sleep"):
            assert item.wait_for(state="visible", timeout=5) is item

    def test_wait_for_visible_on_a_missing_item_is_not_found(self, native_menu):
        with pytest.raises(ElementNotFoundError, match="File -> Nope"):
            Menu(native_menu.window, title="File").item("Nope").wait_for(timeout=0)

    def test_wait_for_visible_on_the_menu_bar_item(self, native_menu):
        menu = Menu(native_menu.window, title="File")
        assert menu.wait_for(state="visible", timeout=0) is menu

    def test_wait_for_exists_keeps_the_presence_meaning(self, native_menu):
        item = Menu(native_menu.window, title="File").item("Show dialog")
        assert item.wait_for(state="exists", timeout=0) is item


# PR #62 review: "&&" is a literal ampersand in a Win32 caption


class TestCaptionText:
    @pytest.mark.parametrize(
        ("caption", "displayed"),
        [
            ("&File", "File"),
            ("Save &as", "Save as"),
            ("R&&D", "R&D"),
            ("A&&&B", "A&B"),
            ("Save\tCtrl+S", "Save"),
            ("&Find && Replace\tCtrl+H", "Find & Replace"),
            ("trailing&", "trailing&"),
            ("", ""),
        ],
    )
    def test_caption_is_normalized_as_win32_displays_it(self, caption, displayed):
        assert element_module._menu_text_key(caption) == displayed

    @pytest.mark.parametrize(
        ("wanted", "matches"),
        [("R&D report", True), ("R&&D report", True), ("RD report", False)],
    )
    def test_menu_item_with_a_literal_ampersand(self, wanted, matches):
        item = _NativeItem("R&&D report")
        window = _menu_window(_NativeMenu(_NativeItem("&File", _NativeMenu(item))))
        assert Menu(window, title="File").item(wanted).timeout(0).exists() is matches

    def test_adapter_reports_the_literal_ampersand(self):
        entry = element_module._Win32MenuEntry(_NativeItem("R&&D report\tCtrl+R"))
        assert entry.window_text() == "R&D report"

    def test_tab_with_a_literal_ampersand(self):
        wrapper = TestTabOnWin32()._tabs("General", "R&&D")
        _element_resolving_to(Tab, wrapper).select_tab("R&D")
        wrapper.select.assert_called_once_with(1)


# PR #62 review: empty text of a plain win32 control


class TestEmptyTextOnWin32:
    def _text(self, wrapper: object, *, clipboard: str = "clipboard") -> tuple[str, Mock]:
        locator = Locator(Mock())
        locator._resolve_readonly = Mock(return_value=_LazySpec(wrapper, text=""))
        with patch.object(
            locator_module, "_read_text_via_clipboard", return_value=clipboard
        ) as read_clipboard:
            return locator.text(), read_clipboard

    @pytest.mark.parametrize(
        ("kind", "class_name"),
        [
            ("Edit", "Edit"),
            ("Static", "Static"),
            ("Edit", "WindowsForms10.EDIT.app.0.1"),
            ("Static", "WindowsForms10.STATIC.app.0.1"),
            ("Edit", "ThunderRT6TextBox"),
            ("Edit", "TMemo"),
        ],
    )
    def test_plain_control_never_touches_the_clipboard(self, kind, class_name):
        wrapper = SimpleNamespace(
            backend=WIN32, friendly_class_name=lambda: kind, class_name=lambda: class_name
        )
        text, read_clipboard = self._text(wrapper)
        assert text == ""
        read_clipboard.assert_not_called()

    @pytest.mark.parametrize(
        "wrapper",
        [
            SimpleNamespace(
                backend=WIN32,
                friendly_class_name=lambda: "Edit",
                class_name=lambda: "RichEditD2DPT",
            ),
            SimpleNamespace(
                backend=WIN32,
                friendly_class_name=lambda: "Edit",
                class_name=lambda: "RichEdit20W",
            ),
            SimpleNamespace(
                backend=WIN32, friendly_class_name=lambda: "Button", class_name=lambda: "Button"
            ),
            SimpleNamespace(
                backend=UIA, friendly_class_name=lambda: "Edit", class_name=lambda: "Edit"
            ),
            SimpleNamespace(backend=WIN32),
            SimpleNamespace(),
        ],
        ids=["win11-notepad", "richedit", "other-class", "uia", "no-class-info", "not-pywinauto"],
    )
    def test_everything_else_keeps_the_clipboard_fallback(self, wrapper):
        text, read_clipboard = self._text(wrapper)
        assert text == "clipboard"
        read_clipboard.assert_called_once()
