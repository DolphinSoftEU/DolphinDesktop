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
import pywintypes
import win32con

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

    def test_find_win32_elements_runs_the_search_child_window_runs(self):
        parent = object()
        with patch("pywinauto.findwindows.find_elements", return_value=["match"]) as find:
            assert w32.find_win32_elements(
                parent, {"control_id": 7}, visible_only=False, depth=2
            ) == ["match"]
        find.assert_called_once_with(
            parent=parent,
            backend="win32",
            depth=2,
            top_level_only=False,
            visible_only=False,
            control_id=7,
        )

    def test_find_win32_elements_keeps_a_visibility_the_selector_asked_for(self):
        with patch("pywinauto.findwindows.find_elements", return_value=[]) as find:
            w32.find_win32_elements(object(), {"visible_only": True}, visible_only=False)
        assert find.call_args.kwargs["visible_only"] is True

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

    def _all_parent(self, **attrs) -> SimpleNamespace:
        wrap = Mock(side_effect=lambda info: ("wrapped", info))
        return SimpleNamespace(
            backend=SimpleNamespace(name="win32", generic_wrapper_class=wrap),
            children=Mock(side_effect=AssertionError("children() ignores criteria")),
            descendants=Mock(side_effect=AssertionError("descendants() ignores criteria")),
            **attrs,
        )

    def test_all_sends_every_criterion_to_the_search_the_lookup_uses(self, friendly_names):
        parent = self._all_parent()
        infos = [_info("Edit"), _info("Edit")]
        locator = Locator(_window(parent), control_type="Edit", title="x", control_id=7)
        with patch("pywinauto.findwindows.find_elements", return_value=infos) as find:
            found = locator.all()
        assert [item._element for item in found] == [("wrapped", info) for info in infos]
        kwargs = find.call_args.kwargs
        assert kwargs["parent"] is parent
        assert kwargs["title"] == "x"
        assert kwargs["control_id"] == 7
        assert kwargs["predicate_func"](_info("Edit")) is True
        assert kwargs["predicate_func"](_info("Static")) is False
        assert "control_type" not in kwargs
        # all() has always listed hidden controls, and children() its whole subtree.
        assert kwargs["visible_only"] is False
        assert kwargs["depth"] is None

    def test_all_without_a_control_type_still_applies_the_criteria(self):
        parent = self._all_parent()
        with patch("pywinauto.findwindows.find_elements", return_value=[]) as find:
            assert Locator(_window(parent), control_id=7).count() == 0
        assert find.call_args.kwargs["control_id"] == 7
        assert "predicate_func" not in find.call_args.kwargs

    def test_all_passes_depth_on(self):
        parent = self._all_parent()
        with patch("pywinauto.findwindows.find_elements", return_value=[]) as find:
            Locator(_window(parent), control_id=7).all(depth=2)
        assert find.call_args.kwargs["depth"] == 2

    @pytest.mark.parametrize(
        ("index", "expected"), [(0, ["a"]), (2, ["c"]), (-1, ["c"]), (-3, ["a"]), (3, []), (-4, [])]
    )
    def test_all_narrows_to_the_nth_match(self, index, expected):
        parent = self._all_parent()
        with patch("pywinauto.findwindows.find_elements", return_value=["a", "b", "c"]) as find:
            found = Locator(_window(parent), class_name="Edit").nth(index).all()
        assert [item._element for item in found] == [("wrapped", info) for info in expected]
        assert "found_index" not in find.call_args.kwargs

    def test_all_resolves_a_specification_parent_to_its_wrapper(self):
        wrapper = self._all_parent()

        class Spec:
            backend = WIN32

            def wrapper_object(self):
                return wrapper

        with patch("pywinauto.findwindows.find_elements", return_value=[]) as find:
            Locator(_window(Spec()), control_id=7).all()
        assert find.call_args.kwargs["parent"] is wrapper

    def test_all_is_empty_when_the_parent_is_gone(self):
        class Spec:
            backend = WIN32

            def wrapper_object(self):
                raise RuntimeError("window closed")

        with patch("pywinauto.findwindows.find_elements") as find:
            assert Locator(_window(Spec()), control_id=7).all() == []
        find.assert_not_called()

    def test_all_reports_a_selector_the_search_rejects(self):
        parent = self._all_parent()
        with patch("pywinauto.findwindows.find_elements", side_effect=TypeError("bad keyword")):
            with pytest.raises(TypeError, match="bad keyword"):
                Locator(_window(parent), bogus=1).all()

    def test_all_fails_at_once_for_a_type_without_a_win32_class(self):
        with pytest.raises(UnsupportedCapabilityError):
            Locator(_window(self._all_parent()), control_type="TreeItem").all()

    def test_uia_all_keeps_children_and_descendants(self):
        parent = SimpleNamespace(
            backend=UIA,
            children=Mock(return_value=[]),
            descendants=Mock(return_value=[]),
        )
        Locator(_window(parent), control_type="Edit").all()
        parent.children.assert_called_once_with(control_type="Edit")
        Locator(_window(parent), control_type="Edit").all(depth=2)
        parent.descendants.assert_called_once_with(depth=2, control_type="Edit")

    @pytest.mark.parametrize("visible_only", [True, False])
    def test_negative_index_comes_from_the_final_match_set(self, friendly_names, visible_only):
        wrapper = SimpleNamespace(descendants=Mock(side_effect=AssertionError("ignores criteria")))
        criteria = w32.criteria_for_backend(
            SimpleNamespace(backend=WIN32),
            {"control_type": "Edit", "control_id": 7, "found_index": -1},
        )
        with (
            patch.object(locator_module, "_wrapper_object_until", return_value=wrapper),
            patch("pywinauto.findwindows.find_elements", return_value=["a", "b"]) as find,
        ):
            resolved = locator_module._resolve_negative_found_index(
                SimpleNamespace(backend=WIN32), criteria, timeout=0, visible_only=visible_only
            )
        assert resolved["found_index"] == 1
        kwargs = find.call_args.kwargs
        assert kwargs["parent"] is wrapper
        assert kwargs["control_id"] == 7
        assert kwargs["visible_only"] is visible_only
        assert kwargs["predicate_func"] is criteria["predicate_func"]
        assert "found_index" not in kwargs

    def test_negative_index_past_the_final_matches_does_not_tree_walk(self):
        wrapper = SimpleNamespace()
        with (
            patch.object(locator_module, "_wrapper_object_until", return_value=wrapper),
            patch("pywinauto.findwindows.find_elements", return_value=["only"]),
            patch.object(locator_module, "_tree_walk_find") as tree_walk,
            pytest.raises(Exception, match="negative index"),
        ):
            locator_module._resolve_negative_found_index(
                SimpleNamespace(backend=WIN32), {"title": "x", "found_index": -2}, timeout=0
            )
        tree_walk.assert_not_called()

    @pytest.mark.parametrize(
        ("method", "wait", "visible_only"),
        [
            ("_resolve", "_wait_until_visible", True),
            ("_resolve_presence", "_wait_until_present", False),
        ],
    )
    def test_lookup_tells_the_negative_index_which_matches_count(
        self, friendly_names, method, wait, visible_only
    ):
        child = Mock(name="child-spec")
        parent = self._parent(child_window=Mock(return_value=child))
        locator = Locator(_window(parent), control_type="Edit").nth(-1)
        with (
            patch.object(
                locator_module,
                "_resolve_negative_found_index",
                return_value={"found_index": 0},
            ) as negative,
            patch.object(locator_module, wait),
        ):
            assert getattr(locator, method)() is child
        assert negative.call_args.kwargs["visible_only"] is visible_only

    def test_predicate_stays_a_local_filter_on_other_backends(self):
        keep = SimpleNamespace(element_info=_info("keep"))
        drop = SimpleNamespace(element_info=_info("drop"))
        wrapper = SimpleNamespace(descendants=Mock(return_value=[drop, keep]))
        criteria = {"predicate_func": lambda info: info.friendly == "keep", "found_index": -1}
        with patch.object(locator_module, "_wrapper_object_until", return_value=wrapper):
            resolved = locator_module._resolve_negative_found_index(Mock(), criteria, timeout=0)
        assert resolved["found_index"] == 0
        wrapper.descendants.assert_called_once_with()

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
    def __init__(
        self,
        text: str,
        sub_menu: object = None,
        *,
        enabled: bool = True,
        checked: bool = False,
    ) -> None:
        self._text = text
        self._sub_menu = sub_menu
        self._enabled = enabled
        self._checked = checked
        self.select = Mock()
        self.click_input = Mock()
        # GetMenuItemRect answers an empty rectangle while the popup is closed.
        self.rectangle = Mock(return_value=CLOSED_RECT)
        self.ctrl = SimpleNamespace(
            is_visible=Mock(return_value=True), verify_actionable=Mock(), handle=123
        )
        self.on_main_menu = True
        self.menu = SimpleNamespace(owner_item=None, handle=456)
        self.index = Mock(return_value=0)
        self.command_id = 1000
        self.submenu_handle = 0

    def _read_item(self):
        return SimpleNamespace(wID=self.command_id, hSubMenu=self.submenu_handle, text=self._text)

    def text(self) -> str:
        return self._text

    def sub_menu(self) -> object:
        return self._sub_menu

    def is_enabled(self) -> bool:
        return self._enabled

    def is_checked(self) -> bool:
        return self._checked


class _NativeMenu:
    def __init__(self, *items: _NativeItem) -> None:
        self._items = list(items)

    def items(self) -> list[_NativeItem]:
        return list(self._items)


def _menu_window(menu_bar: object, backend: SimpleNamespace = WIN32) -> SimpleNamespace:
    owner = SimpleNamespace(menu=Mock(return_value=menu_bar))
    return _window(SimpleNamespace(backend=backend, wrapper_object=Mock(return_value=owner)))


@pytest.fixture
def native_menu(monkeypatch):
    monkeypatch.setattr(element_module, "_native_popup_is_open", Mock(return_value=False))
    monkeypatch.setattr(element_module, "_native_menu_owns_point", Mock(return_value=True))
    show = _NativeItem("Show &dialog\tCtrl+D")
    disabled = _NativeItem("Export", enabled=False)
    file_menu = _NativeItem("&File", _NativeMenu(show, _NativeItem(""), disabled))
    file_menu.rectangle.return_value = OPEN_RECT  # the menu bar is always laid out
    window = _menu_window(_NativeMenu(file_menu, _NativeItem("&Help", _NativeMenu())))
    return SimpleNamespace(window=window, file=file_menu, show=show, disabled=disabled)


@pytest.mark.parametrize("popup_menu, expected", [(456, True), (789, False)])
def test_popup_open_state_matches_hmenu_on_owner_thread(popup_menu, expected):
    item = _NativeItem("Mark")

    def enumerate_windows(thread_id, callback, param):
        assert thread_id == 42
        for hwnd in [100, 101, 102]:
            callback(hwnd, param)

    def menu_info(hwnd, object_id, item_id, target):
        assert (hwnd, object_id, item_id) == (102, -4, 0)
        assert target._obj.cbSize > 0
        target._obj.hMenu = popup_menu
        return 1

    with (
        patch("win32process.GetWindowThreadProcessId", return_value=(42, 77)),
        patch("win32gui.EnumThreadWindows", side_effect=enumerate_windows),
        patch("win32gui.GetClassName", side_effect=["Other", "#32768", "#32768"]),
        patch("win32gui.IsWindowVisible", side_effect=[False, True]),
        patch("pywinauto.win32functions.GetMenuBarInfo", side_effect=menu_info) as read_menu,
    ):
        assert element_module._native_popup_is_open(item) is expected
        read_menu.assert_called_once()


def test_popup_query_failure_is_unknown_rather_than_closed():
    with (
        patch("win32process.GetWindowThreadProcessId", return_value=(42, 77)),
        patch(
            "win32gui.EnumThreadWindows",
            side_effect=lambda thread, callback, param: callback(102, param),
        ),
        patch("win32gui.GetClassName", return_value="#32768"),
        patch("win32gui.IsWindowVisible", return_value=True),
        patch("pywinauto.win32functions.GetMenuBarInfo", return_value=0),
    ):
        with pytest.raises(OSError):
            element_module._native_popup_is_open(_NativeItem("Mark"))


@pytest.mark.parametrize("matching_popup", [False, True])
@pytest.mark.parametrize("failed_api", ["GetClassName", "IsWindowVisible"])
def test_unreadable_popup_does_not_hide_a_later_matching_popup(failed_api, matching_popup):
    native_error = pywintypes.error(1400, failed_api, "Invalid window handle")

    def enumerate_windows(thread_id, callback, param):
        callback(101, param)
        if matching_popup:
            callback(102, param)

    def menu_info(hwnd, object_id, item_id, target):
        assert hwnd == 102
        target._obj.hMenu = 456
        return 1

    with (
        patch("win32process.GetWindowThreadProcessId", return_value=(42, 77)),
        patch("win32gui.EnumThreadWindows", side_effect=enumerate_windows),
        patch("win32gui.GetClassName", return_value="#32768"),
        patch("win32gui.IsWindowVisible", return_value=True),
        patch("pywinauto.win32functions.GetMenuBarInfo", side_effect=menu_info),
        patch(
            f"win32gui.{failed_api}",
            side_effect=[native_error, "#32768" if failed_api == "GetClassName" else True],
        ),
    ):
        if matching_popup:
            assert element_module._native_popup_is_open(_NativeItem("Mark"))
        else:
            with pytest.raises(OSError, match="Invalid window handle"):
                element_module._native_popup_is_open(_NativeItem("Mark"))


@pytest.mark.parametrize(
    "failed_api", ["win32process.GetWindowThreadProcessId", "win32gui.EnumThreadWindows"]
)
def test_popup_enumeration_error_preserves_native_cause(failed_api):
    native_error = pywintypes.error(5, failed_api, "Access is denied")
    with (
        patch("win32process.GetWindowThreadProcessId", return_value=(42, 77)),
        patch("win32gui.EnumThreadWindows"),
        patch(failed_api, side_effect=native_error),
    ):
        with pytest.raises(OSError, match="Access is denied") as caught:
            element_module._native_popup_is_open(_NativeItem("Mark"))
        assert caught.value.__cause__ is native_error


@pytest.fixture
def menu_hit(monkeypatch):
    item = _NativeItem("Mark")
    item.on_main_menu = False
    state = SimpleNamespace(hit=101, identity=(42, 77), menu=456, kind="#32768", readable=True)

    def menu_info(hwnd, object_id, item_id, target):
        assert (object_id, item_id) == (-4, 0)
        target._obj.hMenu = state.menu
        return int(state.readable)

    monkeypatch.setattr("win32gui.WindowFromPoint", lambda point: state.hit)
    monkeypatch.setattr(
        "win32process.GetWindowThreadProcessId",
        lambda hwnd: (42, 77) if hwnd == item.ctrl.handle else state.identity,
    )
    monkeypatch.setattr("win32gui.GetClassName", lambda hwnd: state.kind)
    monkeypatch.setattr("win32gui.GetMenu", lambda hwnd: state.menu)
    monkeypatch.setattr("pywinauto.win32functions.GetMenuBarInfo", menu_info)
    monkeypatch.setattr(element_module, "_native_menu_index_at_point", Mock(return_value=0))
    monkeypatch.setattr(element_module, "_native_menu_rectangle", Mock(return_value=OPEN_RECT))
    return SimpleNamespace(item=item, state=state)


@pytest.mark.parametrize(
    "change,value",
    [("hit", 0), ("identity", (42, 88)), ("identity", (43, 77)), ("kind", "Edit"), ("menu", 789)],
)
def test_menu_point_rejects_missing_foreign_or_different_menu(menu_hit, change, value):
    setattr(menu_hit.state, change, value)
    assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30))


@pytest.mark.parametrize("main_menu", [False, True])
def test_menu_point_accepts_only_the_matching_owner_or_popup(menu_hit, main_menu):
    menu_hit.item.on_main_menu = main_menu
    if main_menu:
        menu_hit.state.hit = menu_hit.item.ctrl.handle
    assert element_module._native_menu_owns_point(menu_hit.item, (60, 30))


@pytest.mark.parametrize("wrong_owner", [False, True])
def test_menu_bar_point_rejects_other_owner_or_replaced_menu(menu_hit, wrong_owner):
    menu_hit.item.on_main_menu = True
    if not wrong_owner:
        menu_hit.state.hit = menu_hit.item.ctrl.handle
        menu_hit.state.menu = 789
    assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30))


def test_menu_point_rechecks_obstruction_after_identity_queries(menu_hit):
    with patch("win32gui.WindowFromPoint", side_effect=[101, 202]):
        assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30))


def test_menu_point_read_failure_does_not_authorize_input(menu_hit):
    menu_hit.state.readable = False
    with pytest.raises(OSError):
        element_module._native_menu_owns_point(menu_hit.item, (60, 30))


def test_menu_point_native_failure_retains_cause(menu_hit):
    error = pywintypes.error(1400, "WindowFromPoint", "Invalid window handle")
    with patch("win32gui.WindowFromPoint", side_effect=error):
        with pytest.raises(OSError) as caught:
            element_module._native_menu_owns_point(menu_hit.item, (60, 30))
        assert caught.value.__cause__ is error


@pytest.mark.parametrize("index", [-1, 1])
def test_menu_point_rejects_another_item_or_no_item_at_point(menu_hit, index):
    with patch.object(element_module, "_native_menu_index_at_point", return_value=index):
        assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30))


@pytest.mark.parametrize(
    "field,value", [("command_id", 2000), ("submenu_handle", 789), ("_text", "Other command")]
)
def test_menu_point_rejects_changed_identity_at_the_same_index(menu_hit, field, value):
    identity = element_module._native_menu_identity(menu_hit.item)
    assert element_module._native_menu_owns_point(menu_hit.item, (60, 30), identity)
    setattr(menu_hit.item, field, value)
    assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30), identity)


def test_menu_point_rejects_geometry_that_moved_after_positive_hit(menu_hit):
    identity = element_module._native_menu_identity(menu_hit.item)
    assert element_module._native_menu_owns_point(menu_hit.item, (60, 30), identity)
    with patch.object(element_module, "_native_menu_rectangle", return_value=CLOSED_RECT):
        assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30), identity)


@pytest.mark.parametrize("change", ["identity", "index"])
def test_menu_point_rechecks_item_after_reading_fresh_geometry(menu_hit, change):
    identity = element_module._native_menu_identity(menu_hit.item)

    def geometry(_):
        if change == "identity":
            menu_hit.item.command_id = 2000
        return OPEN_RECT

    with (
        patch.object(element_module, "_native_menu_rectangle", side_effect=geometry),
        patch.object(element_module, "_native_menu_index_at_point", side_effect=[0, 1]),
    ):
        assert not element_module._native_menu_owns_point(menu_hit.item, (60, 30), identity)


def test_menu_entry_retains_resolved_command_identity(menu_hit):
    entry = element_module._Win32MenuEntry(menu_hit.item, timeout=0)
    menu_hit.item.command_id = 2000
    with patch.object(element_module, "_native_menu_click") as click:
        with pytest.raises(ElementNotFoundError):
            entry.click_input()
        click.assert_not_called()


def test_menu_item_from_point_uses_owner_and_point_by_value():
    hit_test = Mock(return_value=2)
    with patch("ctypes.WinDLL", return_value=SimpleNamespace(MenuItemFromPoint=hit_test)):
        item = _NativeItem("Mark")
        assert element_module._native_menu_index_at_point(item, (60, 30)) == 2
        owner, menu, point = hit_test.call_args.args
        assert (owner, menu, point.x, point.y) == (123, 456, 60, 30)
        assert hit_test.argtypes[2] is type(point)


@pytest.fixture
def menu_dispatch(monkeypatch):
    metrics = {
        win32con.SM_XVIRTUALSCREEN: -1920,
        win32con.SM_YVIRTUALSCREEN: -1080,
        win32con.SM_CXVIRTUALSCREEN: 3840,
        win32con.SM_CYVIRTUALSCREEN: 2160,
        win32con.SM_SWAPBUTTON: 0,
    }
    guard = Mock(return_value=True)
    send = Mock(return_value=3)
    release = Mock()
    monkeypatch.setattr("win32api.GetSystemMetrics", lambda key: metrics[key])
    monkeypatch.setattr("pywinauto.win32functions.SendInput", send)
    monkeypatch.setattr("win32api.mouse_event", release)
    monkeypatch.setattr(element_module, "_native_menu_owns_point", guard)
    return SimpleNamespace(metrics=metrics, guard=guard, send=send, release=release)


@pytest.mark.parametrize("swapped", [False, True])
def test_menu_dispatch_guards_then_sends_physical_batch_on_virtual_desktop(menu_dispatch, swapped):
    menu_dispatch.metrics[win32con.SM_SWAPBUTTON] = int(swapped)
    order = []
    menu_dispatch.guard.side_effect = lambda *_: order.append("guard") or True
    menu_dispatch.send.side_effect = lambda *_: order.append("input") or 3
    item = _NativeItem("Mark")
    element_module._native_menu_click(item, coords=(-960, -540))
    assert order == ["guard", "input"]
    menu_dispatch.guard.assert_called_once_with(item, (-960, -540), None)
    count, pointer, size = menu_dispatch.send.call_args.args
    assert count == 3 and size > 0
    events = pointer._obj
    assert all(event.type == 0 for event in events)
    assert events[0].mi.dwFlags == (
        win32con.MOUSEEVENTF_MOVE | win32con.MOUSEEVENTF_ABSOLUTE | win32con.MOUSEEVENTF_VIRTUALDESK
    )
    # Decode the injected absolute coordinates back to physical screen pixels.
    assert events[0].mi.dx * 3840 // 65536 - 1920 == -960
    assert events[0].mi.dy * 2160 // 65536 - 1080 == -540
    assert events[1].mi.dwFlags == (
        win32con.MOUSEEVENTF_RIGHTDOWN if swapped else win32con.MOUSEEVENTF_LEFTDOWN
    )
    assert events[2].mi.dwFlags == (
        win32con.MOUSEEVENTF_RIGHTUP if swapped else win32con.MOUSEEVENTF_LEFTUP
    )
    menu_dispatch.release.assert_not_called()


def test_menu_dispatch_rechecks_ownership_before_any_mouse_event(menu_dispatch):
    menu_dispatch.guard.return_value = False
    with pytest.raises(ElementNotFoundError, match="obscured"):
        element_module._native_menu_click(_NativeItem("Mark"), coords=(60, 30))
    menu_dispatch.send.assert_not_called()
    menu_dispatch.release.assert_not_called()


def test_menu_dispatch_guard_exhausting_deadline_prevents_input(menu_dispatch, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)

    def slow_guard(*_):
        clock.now = 0.3
        return True

    menu_dispatch.guard.side_effect = slow_guard
    with pytest.raises(ElementNotFoundError, match="deadline expired"):
        element_module._native_menu_click(_NativeItem("Mark"), coords=(60, 30), deadline=0.2)
    menu_dispatch.send.assert_not_called()


def test_menu_dispatch_accepts_remaining_deadline(menu_dispatch, monkeypatch):
    monkeypatch.setattr(element_module.time, "monotonic", lambda: 0.1)
    element_module._native_menu_click(_NativeItem("Mark"), coords=(60, 30), deadline=0.2)
    menu_dispatch.send.assert_called_once()


def test_menu_dispatch_rechecks_enabled_state_after_final_hit_test(menu_dispatch):
    from pywinauto.controls.menuwrapper import MenuItemNotEnabled

    item = _NativeItem("Mark")

    def disable(*_):
        item._enabled = False
        return True

    menu_dispatch.guard.side_effect = disable
    with pytest.raises(MenuItemNotEnabled):
        element_module._native_menu_click(item, coords=(60, 30))
    menu_dispatch.send.assert_not_called()


@pytest.mark.parametrize("dimension", [win32con.SM_CXVIRTUALSCREEN, win32con.SM_CYVIRTUALSCREEN])
def test_menu_dispatch_refuses_unknown_desktop_size(menu_dispatch, dimension):
    menu_dispatch.metrics[dimension] = 0
    with pytest.raises(RuntimeError, match="desktop size"):
        element_module._native_menu_click(_NativeItem("Mark"), coords=(60, 30))
    menu_dispatch.send.assert_not_called()


@pytest.mark.parametrize("inserted", [0, 1, 2])
def test_menu_dispatch_failure_is_not_retried_and_releases_partial_button(menu_dispatch, inserted):
    menu_dispatch.send.return_value = inserted
    with pytest.raises(RuntimeError, match=f"inserted {inserted} of 3"):
        element_module._native_menu_click(_NativeItem("Mark"), coords=(60, 30))
    assert menu_dispatch.send.call_count == 1
    assert menu_dispatch.release.call_count == int(inserted == 2)


class TestNativeWin32Menu:
    def test_invoke_selects_the_item_without_mouse_input(self, native_menu):
        item = Menu(native_menu.window, title="File", control_type="MenuItem").item("Show dialog")
        item.invoke()
        native_menu.show.select.assert_called_once_with()
        native_menu.show.click_input.assert_not_called()

    def test_click_is_a_physical_click_on_the_native_item(self, native_menu):
        native_menu.show.on_main_menu = False
        native_menu.show.menu.owner_item = native_menu.file

        def physical_click(_item, *, coords, deadline=None, identity=None):
            if coords == (60, 30):
                native_menu.show.rectangle.return_value = OPEN_RECT

        def screen_rectangle(owner, menu, index, target):
            assert owner == 123
            assert menu.value == 456
            rect = target._obj
            rect.left, rect.top, rect.right, rect.bottom = 200, 300, 400, 340
            return 1

        with (
            patch.object(
                element_module, "_native_menu_click", side_effect=physical_click
            ) as mouse_click,
            patch("pywinauto.win32functions.GetMenuItemRect", side_effect=screen_rectangle),
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
            patch.object(element_module.time, "sleep"),
        ):
            Menu(native_menu.window, title="File").item("Show dialog").click()
            assert [call.kwargs["coords"] for call in mouse_click.call_args_list] == [
                (60, 30),
                (300, 320),
            ]
            # An already open parent must not be clicked again and toggled shut.
            mouse_click.reset_mock()
            Menu(native_menu.window, title="File").item("Show dialog").click()
            mouse_click.assert_called_once()
            assert mouse_click.call_args.kwargs["coords"] == (300, 320)
        native_menu.show.select.assert_not_called()
        native_menu.file.select.assert_not_called()

    def test_click_does_not_send_input_when_parent_fails_to_open(self, native_menu):
        native_menu.show.menu.owner_item = native_menu.file
        with (
            patch.object(element_module, "_native_menu_click") as mouse_click,
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
            patch.object(element_module.time, "sleep"),
        ):
            with pytest.raises(ElementNotFoundError, match="not on screen"):
                Menu(native_menu.window, title="File").item("Show dialog").timeout(0).click()
            mouse_click.assert_called_once()
            assert mouse_click.call_args.kwargs["coords"] == (60, 30)

    @pytest.mark.parametrize("already_open", [False, True])
    def test_click_waits_for_popup_layout_without_clicking_parent_twice(
        self, native_menu, already_open
    ):
        native_menu.show.on_main_menu = False
        native_menu.show.menu.owner_item = native_menu.file
        native_menu.show.rectangle.side_effect = [CLOSED_RECT, OPEN_RECT, OPEN_RECT]
        element_module._native_popup_is_open.return_value = already_open
        reads = []

        def read_popup(owner, menu, index, target):
            assert owner == native_menu.show.ctrl.handle
            assert menu.value == native_menu.show.menu.handle
            reads.append(owner)
            if len(reads) == 1:
                return 0
            rect = target._obj
            rect.left, rect.top, rect.right, rect.bottom = 200, 300, 400, 340
            return 1

        with (
            patch.object(element_module, "_native_menu_click") as mouse_click,
            patch("pywinauto.win32functions.GetMenuItemRect", side_effect=read_popup),
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
            patch.object(element_module.time, "sleep"),
        ):
            Menu(native_menu.window, title="File").item("Show dialog").click()
            expected = [(300, 320)] if already_open else [(60, 30), (300, 320)]
            assert [call.kwargs["coords"] for call in mouse_click.call_args_list] == expected
            assert len(reads) == 2
            native_menu.show.select.assert_not_called()

    def test_visible_disabled_item_does_not_send_input(self, native_menu):
        from pywinauto.controls.menuwrapper import MenuItemNotEnabled

        native_menu.disabled.menu.owner_item = native_menu.file
        native_menu.disabled.rectangle.return_value = OPEN_RECT
        with patch.object(element_module, "_native_menu_click") as mouse_click:
            with pytest.raises(MenuItemNotEnabled, match="is disabled"):
                Menu(native_menu.window, title="File").item("Export").click()
            mouse_click.assert_not_called()

    @pytest.mark.parametrize("enabled_after_open", [False, True])
    def test_popup_state_is_evaluated_after_opening_parent(self, native_menu, enabled_after_open):
        from pywinauto.controls.menuwrapper import MenuItemNotEnabled

        native_menu.show.menu.owner_item = native_menu.file
        native_menu.show.on_main_menu = False
        native_menu.show._enabled = not enabled_after_open

        def click(item, **kwargs):
            if item is native_menu.file:
                native_menu.show.rectangle.return_value = OPEN_RECT
                native_menu.show._enabled = enabled_after_open

        with (
            patch.object(element_module, "_native_menu_click", side_effect=click) as send,
            patch.object(
                element_module, "_native_menu_rectangle", side_effect=lambda item: item.rectangle()
            ),
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
            patch.object(element_module.time, "sleep"),
        ):
            item = Menu(native_menu.window, title="File").item("Show dialog")
            if enabled_after_open:
                item.click()
                assert [call.args[0] for call in send.call_args_list] == [
                    native_menu.file,
                    native_menu.show,
                ]
            else:
                with pytest.raises(MenuItemNotEnabled):
                    item.click()
                assert [call.args[0] for call in send.call_args_list] == [native_menu.file]

    @pytest.mark.parametrize("query_error", [False, True])
    def test_obscured_popup_or_unknown_hit_never_sends_input(self, native_menu, query_error):
        native_menu.show.menu.owner_item = native_menu.file
        native_menu.show.on_main_menu = False
        native_menu.show.rectangle.return_value = OPEN_RECT
        guard = element_module._native_menu_owns_point
        guard.return_value = False
        if query_error:
            guard.side_effect = OSError("cannot identify window at click point")
        with (
            patch.object(element_module._Win32MenuEntry, "rectangle", return_value=OPEN_RECT),
            patch.object(element_module, "_native_menu_click") as mouse_click,
        ):
            with pytest.raises(ElementNotFoundError) as caught:
                Menu(native_menu.window, title="File").item("Show dialog").timeout(0).click()
            if query_error:
                assert isinstance(caught.value.__cause__, OSError)
            else:
                assert "obscured" in str(caught.value)
            mouse_click.assert_not_called()
            native_menu.file.select.assert_not_called()
            element_module._native_popup_is_open.assert_not_called()

    def test_transient_obstruction_retries_geometry_without_reopening_parent(self, native_menu):
        native_menu.show.menu.owner_item = native_menu.file
        native_menu.show.on_main_menu = False
        guard = element_module._native_menu_owns_point
        guard.side_effect = [OSError("window is changing"), False, True]

        def click(_item, **kwargs):
            assert guard.call_count == 3, "input must wait for positive hit testing"

        with (
            patch.object(
                element_module._Win32MenuEntry, "rectangle", return_value=OPEN_RECT
            ) as rectangle,
            patch.object(element_module, "_native_menu_click", side_effect=click) as mouse_click,
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
            patch.object(element_module.time, "sleep"),
        ):
            Menu(native_menu.window, title="File").item("Show dialog").click()
            mouse_click.assert_called_once()
            assert mouse_click.call_args.kwargs["coords"] == (60, 30)
            assert rectangle.call_count == 3
            element_module._native_popup_is_open.assert_not_called()

    def test_closed_popup_does_not_use_stale_owner_rectangle(self, native_menu):
        native_menu.show.on_main_menu = False
        with patch("pywinauto.win32functions.GetMenuItemRect") as get_rect:
            entry = element_module._Win32MenuEntry(native_menu.show)
            assert entry.rectangle() is CLOSED_RECT
            assert not entry.is_visible()
            get_rect.assert_not_called()

    def test_failed_screen_rectangle_query_prevents_input(self, native_menu, monkeypatch):
        native_menu.show.on_main_menu = False
        native_menu.show.rectangle.return_value = OPEN_RECT
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)
        monkeypatch.setattr(
            element_module.time, "sleep", lambda delay: setattr(clock, "now", clock.now + delay)
        )
        with (
            patch("pywinauto.win32functions.GetMenuItemRect", return_value=0) as get_rect,
            patch.object(element_module, "_native_menu_click") as mouse_click,
        ):
            entry = element_module._Win32MenuEntry(native_menu.show, timeout=0.1)
            with pytest.raises(ElementNotFoundError, match="not on screen") as caught:
                entry.click_input()
            assert isinstance(caught.value.__cause__, OSError)
            assert get_rect.call_count == 2
            assert clock.now == pytest.approx(0.1)
            mouse_click.assert_not_called()

    def test_owner_rectangle_failure_does_not_report_open_popup_hidden(self, native_menu):
        native_menu.show.on_main_menu = False
        native_menu.show.rectangle.return_value = OPEN_RECT
        item = Menu(native_menu.window, title="File").item("Show dialog")
        with patch("pywinauto.win32functions.GetMenuItemRect", return_value=0) as get_rect:
            assert item.is_visible()
            with pytest.raises(WaitTimeoutError):
                item.wait_until_hidden(timeout=0)
            get_rect.assert_not_called()

    def test_unknown_popup_state_never_clicks_parent(self, native_menu):
        native_menu.show.menu.owner_item = native_menu.file
        element_module._native_popup_is_open.side_effect = OSError("popup state unavailable")
        with patch.object(element_module, "_native_menu_click") as mouse_click:
            with pytest.raises(ElementNotFoundError) as caught:
                Menu(native_menu.window, title="File").item("Show dialog").timeout(0).click()
            assert isinstance(caught.value.__cause__, OSError)
            mouse_click.assert_not_called()

    def test_lookup_and_layout_share_one_timeout(self, native_menu, monkeypatch):
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)
        monkeypatch.setattr(
            element_module.time, "sleep", lambda delay: setattr(clock, "now", clock.now + delay)
        )

        def find_after_delay(*_):
            clock.now += 0.18
            return native_menu.show

        monkeypatch.setattr(element_module, "_find_native_menu_item", find_after_delay)
        with patch.object(element_module, "_native_menu_click") as mouse_click:
            with pytest.raises(ElementNotFoundError):
                Menu(native_menu.window, title="File").item("Show dialog").timeout(0.2).click()
            assert clock.now == pytest.approx(0.2)
            mouse_click.assert_not_called()

    @pytest.mark.parametrize("timeout", [0.05, 0.2])
    def test_transient_lookup_uses_only_remaining_wait_budget(
        self, native_menu, monkeypatch, timeout
    ):
        clock = SimpleNamespace(now=0.0)
        waits = []
        monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)

        def sleep(delay):
            waits.append(delay)
            clock.now += delay

        def first_lookup(*_):
            clock.now += 0.02
            raise LookupError("menu is being rebuilt")

        monkeypatch.setattr(element_module.time, "sleep", sleep)
        monkeypatch.setattr(
            element_module,
            "_find_native_menu_item",
            Mock(side_effect=lambda *_: first_lookup() if clock.now == 0 else native_menu.file),
        )
        with (
            patch.object(element_module, "_native_menu_click") as mouse_click,
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
        ):
            menu = Menu(native_menu.window, title="File").timeout(timeout)
            if timeout == 0.05:
                with pytest.raises(ElementNotFoundError, match="before timeout"):
                    menu.click()
                mouse_click.assert_not_called()
            else:
                menu.click()
                mouse_click.assert_called_once()
                assert mouse_click.call_args.kwargs["coords"] == (60, 30)
                assert mouse_click.call_args.kwargs["deadline"] == pytest.approx(timeout)
            assert waits[0] == pytest.approx(min(0.1, timeout - 0.02))
            assert clock.now <= timeout

    def test_lookup_exhausting_timeout_prevents_input_even_with_valid_layout(
        self, native_menu, monkeypatch
    ):
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)

        def slow_lookup(*_):
            clock.now = 0.3
            return native_menu.file

        monkeypatch.setattr(element_module, "_find_native_menu_item", slow_lookup)
        with patch.object(element_module, "_native_menu_click") as mouse_click:
            with pytest.raises(ElementNotFoundError):
                Menu(native_menu.window, title="File").timeout(0.2).click()
            mouse_click.assert_not_called()

    def test_parent_opening_uses_the_same_deadline(self, native_menu, monkeypatch):
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)
        monkeypatch.setattr(
            element_module.time, "sleep", lambda delay: setattr(clock, "now", clock.now + delay)
        )
        native_menu.show.menu.owner_item = native_menu.file

        def open_parent(_item, **kwargs):
            clock.now += 0.19

        with (
            patch.object(
                element_module, "_native_menu_click", side_effect=open_parent
            ) as mouse_click,
            patch("pywinauto.win32functions.WaitGuiThreadIdle"),
        ):
            with pytest.raises(ElementNotFoundError):
                Menu(native_menu.window, title="File").item("Show dialog").timeout(0.2).click()
            assert clock.now == pytest.approx(0.2)
            mouse_click.assert_called_once()
            assert mouse_click.call_args.kwargs["coords"] == (60, 30)

    def test_layout_read_exhausting_deadline_does_not_send_input(self, native_menu, monkeypatch):
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(element_module.time, "monotonic", lambda: clock.now)

        def slow_rectangle():
            clock.now = 0.21
            return OPEN_RECT

        native_menu.file.rectangle.side_effect = slow_rectangle
        with patch.object(element_module, "_native_menu_click") as mouse_click:
            with pytest.raises(ElementNotFoundError, match="before timeout"):
                Menu(native_menu.window, title="File").timeout(0.2).click()
            mouse_click.assert_not_called()
            native_menu.file.select.assert_not_called()

    def test_state_reads_come_from_the_menu_handle(self, native_menu):
        menu = Menu(native_menu.window, title="File")
        assert menu.item("Show dialog").is_enabled() is True
        assert menu.item("Export").is_enabled() is False
        assert menu.item("Show dialog").exists() is True
        native_menu.show.select.assert_not_called()
        native_menu.show.click_input.assert_not_called()

    @pytest.mark.parametrize(("checked", "state"), [(False, 0), (True, 1)])
    def test_check_state_comes_from_the_menu_handle(self, checked, state):
        item = _NativeItem("Grid", checked=checked)
        window = _menu_window(_NativeMenu(_NativeItem("&View", _NativeMenu(item))))
        grid = Menu(window, title="View").item("Grid")
        assert element_module._Win32MenuEntry(item).get_check_state() == state
        assert grid.is_checked() is checked
        item.select.assert_not_called()
        item.click_input.assert_not_called()

    def test_check_and_uncheck_send_the_command_only_when_it_changes_something(self):
        item = _NativeItem("Grid", checked=False)
        window = _menu_window(_NativeMenu(_NativeItem("&View", _NativeMenu(item))))
        grid = Menu(window, title="View").item("Grid")

        grid.uncheck()
        item.select.assert_not_called()
        grid.check()
        item.select.assert_called_once_with()
        item.click_input.assert_not_called()

        item._checked = True
        item.select.reset_mock()
        grid.check()
        item.select.assert_not_called()
        grid.uncheck()
        item.select.assert_called_once_with()
        item.click_input.assert_not_called()

    def test_check_does_not_fall_back_to_a_click_when_the_item_is_disabled(self):
        class NotEnabled(Exception):
            pass

        item = _NativeItem("Grid", enabled=False)
        item.select.side_effect = NotEnabled("MenuItem Grid is disabled")
        window = _menu_window(_NativeMenu(_NativeItem("&View", _NativeMenu(item))))
        with pytest.raises(NotEnabled):
            Menu(window, title="View").item("Grid").check()
        item.click_input.assert_not_called()

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
