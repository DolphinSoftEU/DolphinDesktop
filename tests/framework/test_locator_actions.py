"""Tests for Locator/Element action semantics that need no live application."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest
from pywinauto.uia_defines import NoPatternInterfaceError

from dolphin_desktop import DolphinError, ElementNotFoundError, UnsupportedPatternError, _locator
from dolphin_desktop._locator import (
    _QtObjectNameLocator,
    _read_text_via_clipboard,
    _ResolvedLocator,
    _toggle_element,
    _tree_walk_find,
)
from dolphin_desktop._window import Window


def _window(spec: MagicMock, *, application: object | None = None) -> Window:
    """Return a Window backed by *spec*."""
    return Window(spec, application=application)


def _spec_with(child: MagicMock) -> MagicMock:
    """Return a window spec whose child_window() resolves to *child*."""
    spec = MagicMock()
    spec.child_window.return_value = child
    spec.children.return_value = []
    return spec


class _BestMatchStub:
    """What ``WindowSpecification`` hands back for an unknown attribute.

    ``child_window(best_match=name)`` is another WindowSpecification, so the
    failure only surfaces when the caller *calls* it.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise AttributeError(
            f"Neither GUI element (wrapper) nor wrapper method '{self._name}' were found (typo?)"
        )


class _FakeSpec:
    """Stand-in for ``pywinauto.application.WindowSpecification``.

    Reproduces the behaviour ``MagicMock(spec=[...])`` cannot represent and
    that the whole unsupported-pattern family turns on: with two or more
    criteria, ``getattr`` on an unknown name never raises — it returns another
    WindowSpecification. Probing with ``getattr(spec, name, None)`` therefore
    always succeeds, whatever the underlying wrapper implements.
    """

    def __init__(self, wrapper: object) -> None:
        self._wrapper = wrapper

    def wrapper_object(self) -> object:
        return self._wrapper

    def wait(self, *args: object, **kwargs: object) -> _FakeSpec:
        return self

    def child_window(self, **criteria: object) -> _FakeSpec:
        return self

    def set_focus(self) -> None:
        pass

    def children(self, **criteria: object) -> list:
        return []

    def __getattr__(self, name: str) -> object:
        try:
            return getattr(object.__getattribute__(self, "_wrapper"), name)
        except AttributeError:
            return _BestMatchStub(name)


class _FakeWrapper:
    """A pywinauto wrapper: only the attributes it was built with exist.

    ``is_visible`` is the exception, and is defined unconditionally because
    every real ``BaseWrapper`` defines it — the resolve path probes
    visibility through it before handing the element to an action. A test
    that needs it to answer False can still pass ``is_visible=...``.
    """

    def __init__(self, **attrs: object) -> None:
        self.__dict__.update(attrs)

    def is_visible(self) -> bool:
        return True


# Literal text must not be interpreted as pywinauto key syntax


class TestKeySyntaxEscaping:
    def test_type_text_escapes_metacharacters(self):
        child = MagicMock()
        _window(_spec_with(child)).edit().type_text("50% off (x86)")
        typed = child.type_keys.call_args[0][0]
        assert typed == "50{%} off {(}x86{)}"

    def test_type_text_escape_false_passes_key_syntax_through(self):
        child = MagicMock()
        _window(_spec_with(child)).edit().type_text("{ENTER}", escape=False)
        assert child.type_keys.call_args[0][0] == "{ENTER}"

    def test_type_text_leaves_plain_text_untouched(self):
        child = MagicMock()
        _window(_spec_with(child)).edit().type_text("hello world")
        assert child.type_keys.call_args[0][0] == "hello world"

    def test_set_text_keyboard_fallback_escapes(self, monkeypatch):
        monkeypatch.setattr("dolphin_desktop._locator._send_keys", MagicMock())
        child = MagicMock()
        child.set_edit_text.side_effect = Exception("no IValueProvider")
        _window(_spec_with(child)).edit().set_text(r"C:\Program Files (x86)\app")
        typed = child.type_keys.call_args[0][0]
        assert typed == r"C:\Program Files {(}x86{)}\app"

    def test_fill_escapes_via_set_text(self, monkeypatch):
        monkeypatch.setattr("dolphin_desktop._locator._send_keys", MagicMock())
        child = MagicMock()
        child.set_edit_text.side_effect = Exception("no IValueProvider")
        _window(_spec_with(child)).edit().fill("100% ^ done")
        assert child.type_keys.call_args[0][0] == "100{%} {^} done"


# TreeWalker fallback must never match on a subset of the criteria


class TestTreeWalkFallbackCriteria:
    def test_refuses_criteria_it_cannot_evaluate(self):
        parent = MagicMock()
        result = _tree_walk_find(parent, {"control_type": "Button", "class_name": "Toolbar"})
        assert result is None
        parent.wrapper_object.assert_not_called()

    def test_matches_auto_id_for_virtualized_tree_items(self):
        wanted = MagicMock()
        wanted.name = "Item 450"
        wanted.control_type = "TreeItem"
        wanted.automation_id = "item_450"
        wanted.children.return_value = []
        other = MagicMock()
        other.name = "Item 449"
        other.control_type = "TreeItem"
        other.automation_id = "item_449"
        other.children.return_value = []
        parent = MagicMock()
        parent.wrapper_object.return_value.element_info.children.return_value = [other, wanted]
        wrapper_cls = Mock(side_effect=lambda info: info)
        fake_pw = sys.modules["pywinauto"]
        with patch.object(
            fake_pw,
            "Application",
            return_value=SimpleNamespace(
                backend=SimpleNamespace(generic_wrapper_class=wrapper_cls)
            ),
        ):
            assert (
                _tree_walk_find(
                    parent,
                    {"control_type": "TreeItem", "auto_id": "item_450"},
                )
                is wanted
            )
        assert (
            _tree_walk_find(
                parent,
                {"control_type": "TreeItem", "auto_id": "missing"},
            )
            is None
        )

    def test_refuses_title_re(self):
        parent = MagicMock()
        assert _tree_walk_find(parent, {"title_re": ".*Save.*"}) is None
        parent.wrapper_object.assert_not_called()

    def test_found_index_skips_earlier_matches_instead_of_disqualifying_the_walk(self):
        """``.nth(N)`` must not have fewer fallbacks than the bare locator."""
        matches = []
        for _ in range(3):
            node = MagicMock()
            node.name = "OK"
            node.control_type = "Button"
            node.children.return_value = []
            matches.append(node)
        parent = MagicMock()
        parent.wrapper_object.return_value.element_info.children.return_value = matches

        result = _tree_walk_find(parent, {"title": "OK", "found_index": 2})
        assert result is not None
        assert result.element_info is matches[2]
        assert _tree_walk_find(parent, {"title": "OK", "found_index": 9}) is None

    def test_still_walks_for_title_and_control_type(self):
        child = MagicMock()
        child.name = "Bold"
        child.control_type = "Button"
        child.children.return_value = []
        parent = MagicMock()
        parent.wrapper_object.return_value.element_info.children.return_value = [child]
        _tree_walk_find(parent, {"title": "Bold", "control_type": "Button"})
        parent.wrapper_object.assert_called()

    def test_stale_auto_id_raises_instead_of_matching_first_button(self):
        child = MagicMock()
        child.wrapper_object.side_effect = Exception("element not visible")
        spec = _spec_with(child)
        loc = _window(spec).button(auto_id="btnDelete").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.click()


# Radio buttons: SelectionItemPattern, not TogglePattern


class TestRadioButtonSelection:
    def test_select_uses_selection_item_pattern(self):
        child = MagicMock()
        _window(_spec_with(child)).radio_button(name="Option A").select()
        child.select.assert_called_once_with()
        child.toggle.assert_not_called()
        child.click_input.assert_not_called()

    def test_select_falls_back_to_check_without_selection_item(self):
        child = MagicMock(spec=["wrapper_object", "toggle", "click_input", "get_check_state"])
        child.get_check_state.return_value = 0
        _window(_spec_with(child)).radio_button(name="Standard").select()
        child.toggle.assert_called_once_with()

    def test_select_returns_self(self):
        child = MagicMock()
        radio = _window(_spec_with(child)).radio_button(name="Option A")
        assert radio.select() is radio

    def test_is_checked_reads_is_selected(self):
        child = MagicMock()
        child.is_selected.return_value = True
        assert _window(_spec_with(child)).radio_button(name="Option A").is_checked() is True

    def test_is_checked_false_when_not_selected(self):
        child = MagicMock()
        child.is_selected.return_value = False
        assert _window(_spec_with(child)).radio_button(name="Option A").is_checked() is False

    def test_is_checked_falls_back_to_check_state(self):
        child = MagicMock(spec=["wrapper_object", "get_check_state"])
        child.get_check_state.return_value = 1
        assert _window(_spec_with(child)).radio_button(name="Standard").is_checked() is True


# Tab.select_tab must fail loudly like every other locator API


class TestSelectTab:
    def test_raises_when_no_tab_matches(self):
        child = MagicMock()
        child.child_window.side_effect = Exception("no TabItem")
        child.children.return_value = []
        with pytest.raises(ElementNotFoundError):
            _window(_spec_with(child)).tab().select_tab("Typo")

    def test_error_message_names_the_tab(self):
        child = MagicMock()
        child.child_window.side_effect = Exception("no TabItem")
        child.children.return_value = []
        with pytest.raises(ElementNotFoundError) as exc_info:
            _window(_spec_with(child)).tab().select_tab("Advanced")
        assert "Advanced" in str(exc_info.value)

    def test_returns_self_on_success(self):
        child = MagicMock()
        tab = _window(_spec_with(child)).tab()
        assert tab.select_tab("General") is tab

    def test_falls_back_to_child_text_match(self):
        page = MagicMock()
        page.window_text.return_value = "Advanced"
        child = MagicMock()
        child.child_window.side_effect = Exception("no TabItem")
        child.children.return_value = [page]
        _window(_spec_with(child)).tab().select_tab("Advanced")
        page.click_input.assert_called_once_with()


# nth() must honour the index or refuse it — never drop it


class TestNthIndex:
    def test_resolved_locator_refuses_a_real_index(self):
        with pytest.raises(ValueError):
            _ResolvedLocator(MagicMock()).nth(1)

    def test_resolved_locator_accepts_index_zero(self):
        element = MagicMock()
        assert _ResolvedLocator(element).nth(0)._resolve() is element

    def _qt_parent(self, *automation_ids: str) -> MagicMock:
        spec = MagicMock()
        descendants = []
        for aid in automation_ids:
            desc = MagicMock()
            desc.element_info.automation_id = aid
            descendants.append(desc)
        spec.descendants.return_value = descendants
        return spec

    def test_qt_object_name_locator_honours_the_index(self):
        spec = self._qt_parent("App.win.btn", "App.dlg.btn")
        loc = _QtObjectNameLocator(_window(spec), "btn")
        first, second = spec.descendants.return_value
        assert loc._resolve() is first
        assert loc.nth(1)._resolve() is second

    def test_qt_object_name_index_survives_a_timeout_clone(self):
        spec = self._qt_parent("App.win.btn", "App.dlg.btn")
        loc = _QtObjectNameLocator(_window(spec), "btn").nth(1).timeout(0)
        assert loc._resolve() is spec.descendants.return_value[1]

    def test_qt_object_name_locator_reports_a_missing_index(self):
        spec = self._qt_parent("App.win.btn")
        loc = _QtObjectNameLocator(_window(spec), "btn").nth(3).timeout(0)
        with pytest.raises(ElementNotFoundError):
            loc._resolve()


# A missing TogglePattern is NoPatternInterfaceError, not AttributeError


class TestTogglePatternFallback:
    def test_missing_toggle_pattern_falls_back_to_click(self):
        element = MagicMock()
        element.toggle.side_effect = NoPatternInterfaceError("no TogglePattern")
        _toggle_element(element)
        element.click_input.assert_called_once_with()

    def test_missing_toggle_method_falls_back_to_click(self):
        element = MagicMock(spec=["click_input"])
        _toggle_element(element)
        element.click_input.assert_called_once_with()

    def test_genuine_failure_is_not_turned_into_a_click(self):
        element = MagicMock()
        element.toggle.side_effect = RuntimeError("COM call failed")
        with pytest.raises(RuntimeError):
            _toggle_element(element)
        element.click_input.assert_not_called()

    def test_radio_without_selection_item_reaches_a_physical_click(self):
        """The documented check() fallback must actually flip a Win32 radio."""
        child = MagicMock()
        child.select.side_effect = NoPatternInterfaceError("no SelectionItemPattern")
        child.get_check_state.return_value = 0
        child.toggle.side_effect = NoPatternInterfaceError("no TogglePattern")
        _window(_spec_with(child)).radio_button(name="Standard").select()
        child.click_input.assert_called_once_with()

    def test_radio_without_any_pattern_raises_the_documented_error(self):
        child = MagicMock()
        child.select.side_effect = NoPatternInterfaceError("no SelectionItemPattern")
        child.get_check_state.side_effect = AttributeError
        child.get_toggle_state.side_effect = NoPatternInterfaceError("no TogglePattern")
        child.iface_toggle = MagicMock(spec=[])  # no TogglePattern interface either
        with pytest.raises(UnsupportedPatternError):
            _window(_spec_with(child)).radio_button(name="Standard").select()


# An unreadable check state is not an unchecked one


class TestCheckStateReadability:
    def _unreadable_child(self) -> MagicMock:
        child = MagicMock()
        child.get_check_state.side_effect = AttributeError
        child.get_toggle_state.side_effect = NoPatternInterfaceError("no TogglePattern")
        child.iface_toggle = MagicMock(spec=[])  # no TogglePattern interface either
        return child

    def test_uncheck_raises_instead_of_reporting_a_silent_noop(self):
        child = self._unreadable_child()
        with pytest.raises(UnsupportedPatternError):
            _window(_spec_with(child)).check_box(name="A").uncheck()
        child.toggle.assert_not_called()
        child.click_input.assert_not_called()

    def test_check_raises_instead_of_inverting_an_unknown_state(self):
        child = self._unreadable_child()
        with pytest.raises(UnsupportedPatternError):
            _window(_spec_with(child)).check_box(name="A").check()
        child.toggle.assert_not_called()

    def test_is_checked_raises_instead_of_answering_false(self):
        child = self._unreadable_child()
        with pytest.raises(UnsupportedPatternError):
            _window(_spec_with(child)).check_box(name="A").is_checked()

    def test_readable_state_still_drives_check_and_uncheck(self):
        child = MagicMock()
        child.get_check_state.return_value = 1
        win = _window(_spec_with(child))
        assert win.check_box(name="A").is_checked() is True
        win.check_box(name="A").check()
        child.toggle.assert_not_called()  # already checked
        win.check_box(name="A").uncheck()
        child.toggle.assert_called_once_with()


# The clipboard is a machine-wide lock — never leave it open


class TestClipboardIsAlwaysClosed:
    def test_open_and_close_stay_balanced_when_empty_fails(self, monkeypatch):
        import sys

        clipboard = MagicMock()
        clipboard.EmptyClipboard.side_effect = RuntimeError("access denied")
        clipboard.GetClipboardData.return_value = "text"
        monkeypatch.setitem(sys.modules, "win32clipboard", clipboard)
        monkeypatch.setitem(sys.modules, "win32con", MagicMock())
        monkeypatch.setattr("dolphin_desktop._locator._send_keys", MagicMock())

        _read_text_via_clipboard(MagicMock())

        assert clipboard.OpenClipboard.call_count == clipboard.CloseClipboard.call_count


# A drag must never leave the physical mouse button latched down


class TestDragReleasesTheButton:
    def _mouse(self, monkeypatch) -> MagicMock:
        import pywinauto

        mouse = MagicMock()
        monkeypatch.setattr(pywinauto, "mouse", mouse)
        return mouse

    def _draggable(self) -> MagicMock:
        child = MagicMock()
        child.rectangle.return_value = MagicMock(left=0, top=0, right=10, bottom=10)
        return child

    def test_button_is_released_when_a_move_fails(self, monkeypatch):
        mouse = self._mouse(monkeypatch)
        mouse.move.side_effect = RuntimeError("SendInput failed")
        child = self._draggable()
        with pytest.raises(RuntimeError):
            _window(_spec_with(child)).button(name="Src").drag_to((100, 100), duration=0.01)
        mouse.press.assert_called_once()
        mouse.release.assert_called_once()

    def test_button_is_released_on_keyboard_interrupt(self, monkeypatch):
        mouse = self._mouse(monkeypatch)
        mouse.move.side_effect = KeyboardInterrupt
        child = self._draggable()
        with pytest.raises(KeyboardInterrupt):
            _window(_spec_with(child)).button(name="Src").drag_to((100, 100), duration=0.01)
        mouse.release.assert_called_once()

    def test_successful_drag_releases_at_the_destination(self, monkeypatch):
        mouse = self._mouse(monkeypatch)
        child = self._draggable()
        _window(_spec_with(child)).button(name="Src").drag_to((100, 60), duration=0.01)
        assert mouse.release.call_args.kwargs["coords"] == (100, 60)


class TestHiddenPhysicalInputErrors:
    @staticmethod
    def _application() -> SimpleNamespace:
        return SimpleNamespace(_desktop=SimpleNamespace(_is_hidden=True))

    @staticmethod
    def _draggable() -> MagicMock:
        child = MagicMock()
        child.rectangle.return_value = SimpleNamespace(left=0, top=0, right=10, bottom=10)
        return child

    def test_click_wraps_set_cursor_pos_error_and_preserves_cause(self):
        import pywintypes

        low_level = pywintypes.error(2, "SetCursorPos", "No error message is available")
        child = MagicMock()
        child.click_input.side_effect = low_level
        locator = _window(_spec_with(child), application=self._application()).button(name="OK")

        with patch.object(locator, "_focus_for_input"), pytest.raises(DolphinError) as raised:
            locator.click()

        assert raised.value.__cause__ is low_level
        assert "click()" in str(raised.value)
        assert "hidden desktop" in str(raised.value)

    def test_hover_wraps_no_active_desktop_error_and_preserves_cause(self):
        import pywinauto.mouse as py_mouse

        low_level = RuntimeError("There is no active desktop")
        child = self._draggable()
        locator = _window(_spec_with(child), application=self._application()).button(name="OK")

        with (
            patch.object(py_mouse, "move", side_effect=low_level),
            pytest.raises(DolphinError) as raised,
        ):
            locator.hover()

        assert raised.value.__cause__ is low_level
        assert "hover()" in str(raised.value)

    def test_drag_wraps_no_active_desktop_error_and_releases_button(self):
        import pywinauto.mouse as py_mouse

        low_level = RuntimeError("There is no active desktop")
        child = self._draggable()
        locator = _window(_spec_with(child), application=self._application()).button(name="Src")

        with (
            patch.object(locator, "_focus_for_input"),
            patch.object(_locator.time, "sleep"),
            patch.object(py_mouse, "press"),
            patch.object(py_mouse, "move", side_effect=low_level),
            patch.object(py_mouse, "release") as release,
            pytest.raises(DolphinError) as raised,
        ):
            locator.drag_to((100, 100), duration=0.01)

        assert raised.value.__cause__ is low_level
        release.assert_called_once()
        assert "drag_to()" in str(raised.value)

    def test_unrelated_hidden_mouse_error_is_not_rewritten(self):
        low_level = RuntimeError("control provider failed")
        child = MagicMock()
        child.click_input.side_effect = low_level
        locator = _window(_spec_with(child), application=self._application()).button(name="OK")

        with patch.object(locator, "_focus_for_input"), pytest.raises(RuntimeError) as raised:
            locator.click()

        assert raised.value is low_level


# Physical-input actions treat focus the same way


class TestPhysicalInputFocus:
    def test_click_survives_a_refused_set_focus(self):
        child = MagicMock()
        spec = _spec_with(child)
        spec.set_focus.side_effect = RuntimeError("cannot set foreground window")
        _window(spec).button(name="OK").click()
        child.click_input.assert_called_once_with()

    @pytest.mark.parametrize("action", ["click", "double_click", "right_click"])
    def test_pointer_actions_focus_the_root(self, action):
        child = MagicMock()
        spec = _spec_with(child)
        getattr(_window(spec).button(name="OK"), action)()
        spec.set_focus.assert_called_once_with()

    def test_hover_does_not_activate_the_window(self):
        """Hover is a non-activating gesture — raising the window changes the
        very state a hover test is checking."""
        import pywinauto.mouse

        moved: list = []
        original = pywinauto.mouse.move
        pywinauto.mouse.move = lambda **kw: moved.append(kw)
        try:
            child = MagicMock()
            child.rectangle.return_value = MagicMock(left=0, top=0, right=10, bottom=10)
            spec = _spec_with(child)
            _window(spec).button(name="OK").hover()
        finally:
            pywinauto.mouse.move = original
        assert moved  # the pointer still moved
        spec.set_focus.assert_not_called()

    def test_drag_focuses_the_root(self, monkeypatch):
        import pywinauto

        monkeypatch.setattr(pywinauto, "mouse", MagicMock())
        child = MagicMock()
        child.rectangle.return_value = MagicMock(left=0, top=0, right=10, bottom=10)
        spec = _spec_with(child)
        _window(spec).button(name="OK").drag_to((5, 5), duration=0.01)
        spec.set_focus.assert_called_once_with()


# A temporary timeout_ms must not leak into the rest of the chain


class TestTimeoutMsDoesNotLeak:
    def _loc(self, child: MagicMock):
        return _window(_spec_with(child)).button(name="OK").timeout(4.0)

    @pytest.mark.parametrize(
        ("action", "args"),
        [
            ("click", ()),
            ("double_click", ()),
            ("press_key", ("{ENTER}",)),
            ("fill", ("text",)),
        ],
    )
    def test_action_returns_the_original_locator(self, action, args):
        loc = self._loc(MagicMock())
        assert getattr(loc, action)(*args, timeout_ms=500) is loc

    @pytest.mark.parametrize("action", ["hover", "scroll"])
    def test_pointer_action_returns_the_original_locator(self, action, monkeypatch):
        import pywinauto

        monkeypatch.setattr(pywinauto, "mouse", MagicMock())
        child = MagicMock()
        child.rectangle.return_value = MagicMock(left=0, top=0, right=10, bottom=10)
        loc = self._loc(child)
        assert getattr(loc, action)(timeout_ms=500) is loc

    def test_next_action_keeps_the_locator_timeout(self, monkeypatch):
        """A per-action timeout_ms applies to that action only.

        Each candidate is probed once; the locator timeout belongs to the
        shared resolution loop and must not leak into the next action.
        """
        from dolphin_desktop import _locator

        seen: list[float] = []
        monkeypatch.setattr(
            _locator, "_wait_until_visible", lambda spec, timeout: seen.append(timeout)
        )
        loc = self._loc(MagicMock())
        loc.click(timeout_ms=500).type_text("x")
        assert seen == [0, 0]


# select_item must not discard the error that made it fall through


class TestSelectItemErrorChaining:
    def test_popup_failure_chains_the_original_error(self, monkeypatch):
        import pywinauto

        empty_desktop = MagicMock()
        empty_desktop.windows.return_value = []
        monkeypatch.setattr(pywinauto, "Desktop", MagicMock(return_value=empty_desktop))
        boom = RuntimeError("COM call failed")
        child = MagicMock()
        child.select.side_effect = boom

        loc = _window(_spec_with(child)).combo_box().timeout(0)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.select_item("Missing")
        assert exc_info.value.__cause__ is boom


# Java windows: refuse criteria the Access Bridge cannot express


class TestJavaWindowCriteria:
    def _java_window(self, monkeypatch) -> Window:
        monkeypatch.setattr(Window, "_is_java_window", lambda self: True)
        monkeypatch.setattr(Window, "_java_hwnd", lambda self: 4321)
        return Window(MagicMock())

    def _capture_jab(self, monkeypatch) -> dict:
        captured: dict = {}

        class _FakeJABLocator:
            def __init__(self, hwnd, **criteria):
                captured["hwnd"] = hwnd
                captured["criteria"] = criteria

        monkeypatch.setattr("dolphin_desktop._java.JABLocator", _FakeJABLocator)
        return captured

    def test_locator_refuses_criteria_jab_cannot_express(self, monkeypatch):
        win = self._java_window(monkeypatch)
        with pytest.raises(ValueError, match="auto_id"):
            win.locator(auto_id="txtName")

    def test_locator_forwards_every_expressible_criterion(self, monkeypatch):
        win = self._java_window(monkeypatch)
        captured = self._capture_jab(monkeypatch)
        win.locator(control_type="Button", title="OK")
        assert captured["hwnd"] == 4321
        assert captured["criteria"] == {"control_type": "Button", "title": "OK"}

    @pytest.mark.parametrize(
        ("method", "arg"),
        [
            ("get_by_automation_id", "txtName"),
            ("get_by_class", "Edit"),
            ("get_by_object_name", "qt_btn_ok"),
        ],
    )
    def test_uia_only_lookups_are_refused(self, monkeypatch, method, arg):
        win = self._java_window(monkeypatch)
        with pytest.raises(ValueError):
            getattr(win, method)(arg)

    def test_non_java_window_is_unaffected(self):
        from dolphin_desktop._locator import Locator

        win = _window(_spec_with(MagicMock()))
        assert isinstance(win.get_by_automation_id("txtName"), Locator)
        assert isinstance(win.get_by_class("Edit"), Locator)


# Backends type literal text, not pywinauto key syntax


class TestBackendTypeTextEscaping:
    @pytest.mark.parametrize("backend_name", ["UIABackend", "Win32Backend"])
    def test_metacharacters_are_escaped(self, backend_name):
        import dolphin_desktop._backend as backend_module

        element = MagicMock()
        getattr(backend_module, backend_name)().type_text(element, "50% off (x86)")
        assert element.type_keys.call_args[0][0] == "50{%} off {(}x86{)}"

    @pytest.mark.parametrize("backend_name", ["UIABackend", "Win32Backend"])
    def test_plain_text_is_untouched(self, backend_name):
        import dolphin_desktop._backend as backend_module

        element = MagicMock()
        getattr(backend_module, backend_name)().type_text(element, "hello world")
        assert element.type_keys.call_args[0][0] == "hello world"


# A full-desktop capture must include every monitor, not just the primary one


class TestBackendScreenshotSpansAllScreens:
    @pytest.mark.parametrize("backend_name", ["UIABackend", "Win32Backend"])
    def test_full_desktop_grab_requests_all_screens(self, monkeypatch, backend_name):
        import PIL.ImageGrab

        import dolphin_desktop._backend as backend_module

        grab = MagicMock()
        monkeypatch.setattr(PIL.ImageGrab, "grab", grab)
        getattr(backend_module, backend_name)().screenshot()
        assert grab.call_args.kwargs.get("all_screens") is True

    @pytest.mark.parametrize("backend_name", ["UIABackend", "Win32Backend"])
    def test_element_capture_does_not_go_through_imagegrab(self, backend_name):
        import dolphin_desktop._backend as backend_module

        element = MagicMock()
        result = getattr(backend_module, backend_name)().screenshot(element)
        assert result is element.capture_as_image.return_value


# WindowSpecification.__getattribute__ never raises — the wrapper must be probed


class TestPatternProbingGoesThroughTheWrapper:
    def test_the_fake_reproduces_the_windowspecification_contract(self):
        """Guard for the fake itself: this is why probing the spec cannot work."""
        spec = _FakeSpec(_FakeWrapper())
        assert getattr(spec, "invoke", None) is not None
        with pytest.raises(AttributeError):
            spec.invoke()

    @pytest.mark.parametrize(
        "action", ["invoke", "toggle", "expand", "collapse", "select", "set_value"]
    )
    def test_missing_pattern_raises_unsupported_not_attribute_error(self, action):
        loc = _window(_FakeSpec(_FakeWrapper())).button(name="OK")
        args = ("x",) if action == "set_value" else ()
        with pytest.raises(UnsupportedPatternError):
            getattr(loc, action)(*args)

    def test_the_error_names_the_real_wrapper(self):
        with pytest.raises(UnsupportedPatternError) as exc_info:
            _window(_FakeSpec(_FakeWrapper())).button(name="OK").invoke()
        assert "_FakeWrapper" in str(exc_info.value)

    def test_a_present_wrapper_method_is_still_dispatched(self):
        calls = []
        wrapper = _FakeWrapper(invoke=lambda: calls.append("invoke"))
        _window(_FakeSpec(wrapper)).button(name="OK").invoke()
        assert calls == ["invoke"]


# Win32 radio buttons have no select() — the documented fallback must fire


class TestWin32RadioFallback:
    def test_select_falls_back_to_the_check_state_path(self):
        clicked = []
        wrapper = _FakeWrapper(
            get_check_state=lambda: 0,
            click_input=lambda: clicked.append(True),
        )
        _window(_FakeSpec(wrapper)).radio_button(name="Standard").select()
        assert clicked == [True]

    def test_already_selected_radio_is_left_alone(self):
        clicked = []
        wrapper = _FakeWrapper(
            get_check_state=lambda: 1,
            click_input=lambda: clicked.append(True),
        )
        _window(_FakeSpec(wrapper)).radio_button(name="Standard").select()
        assert clicked == []


# set_value: UIAWrapper / Edit / ComboBox reach ValuePattern only via iface_value


class TestSetValueThroughValuePattern:
    def test_value_pattern_is_used_when_the_wrapper_has_no_set_value(self):
        iface = MagicMock()
        wrapper = _FakeWrapper(iface_value=iface)
        _window(_FakeSpec(wrapper)).edit(name="Name").set_value("Alice")
        iface.SetValue.assert_called_once_with("Alice")

    def test_a_real_set_value_method_still_wins(self):
        calls = []
        iface = MagicMock()
        wrapper = _FakeWrapper(set_value=calls.append, iface_value=iface)
        _window(_FakeSpec(wrapper)).edit(name="Volume").set_value("7")
        assert calls == ["7"]
        iface.SetValue.assert_not_called()

    def test_no_value_pattern_raises_unsupported_pattern(self):
        with pytest.raises(UnsupportedPatternError):
            _window(_FakeSpec(_FakeWrapper())).edit(name="Name").set_value("Alice")


# TogglePattern without a wrapper method (MenuItem / ListItem / TreeItem)


class TestToggleStateViaIface:
    def _wrapper(self, state: int, toggled: list) -> _FakeWrapper:
        iface = MagicMock()
        iface.CurrentToggleState = state
        iface.Toggle.side_effect = lambda: toggled.append(True)
        return _FakeWrapper(iface_toggle=iface)

    def test_is_checked_reads_the_toggle_interface(self):
        loc = _window(_FakeSpec(self._wrapper(1, []))).check_box(name="Word wrap")
        assert loc.is_checked() is True

    def test_check_toggles_an_unchecked_item(self):
        toggled: list = []
        _window(_FakeSpec(self._wrapper(0, toggled))).check_box(name="Word wrap").check()
        assert toggled == [True]

    def test_check_leaves_an_already_checked_item_alone(self):
        toggled: list = []
        _window(_FakeSpec(self._wrapper(1, toggled))).check_box(name="Word wrap").check()
        assert toggled == []

    def test_toggle_action_uses_the_interface(self):
        toggled: list = []
        _window(_FakeSpec(self._wrapper(0, toggled))).check_box(name="Word wrap").toggle()
        assert toggled == [True]

    def test_unreadable_state_names_the_real_wrapper_not_the_specification(self):
        with pytest.raises(UnsupportedPatternError) as exc_info:
            _window(_FakeSpec(_FakeWrapper())).check_box(name="A").is_checked()
        message = str(exc_info.value)
        assert "_FakeWrapper" in message
        assert "WindowSpecification" not in message


# Read-only queries on a MenuItem must not drive input into the application


class TestMenuItemReadOnlyQueries:
    @staticmethod
    def _menu_child() -> MagicMock:
        """A child reachable from the parent menu's subtree as well as the root.

        A read-only MenuItem query searches the parent menu before falling back
        to the root window, so a child that does not descend to itself would
        hand back a fresh auto-attribute on that first scope — an element that
        reports itself visible and records none of the calls asserted here.
        """
        child = MagicMock()
        child.child_window.return_value = child
        child.children.return_value = []
        return child

    def test_is_visible_does_not_open_the_menu(self):
        child = self._menu_child()
        _window(_spec_with(child)).menu("File").item("Save").is_visible()
        child.click_input.assert_not_called()
        child.invoke.assert_not_called()

    def test_exists_does_not_open_the_menu(self):
        child = self._menu_child()
        _window(_spec_with(child)).menu("File").item("Save").exists()
        child.click_input.assert_not_called()

    def test_wait_until_hidden_never_clicks_the_parent_menu(self):
        child = self._menu_child()
        child.is_visible.return_value = False
        _window(_spec_with(child)).menu("File").item("Save").wait_until_hidden(timeout=0.3)
        child.click_input.assert_not_called()

    def test_click_still_opens_the_parent_menu(self):
        child = MagicMock()
        _window(_spec_with(child)).menu("File").item("Save").click()
        child.click_input.assert_called_once_with()

    @pytest.mark.parametrize(
        "query",
        [
            lambda mi: mi.is_checked(),
            lambda mi: mi.text(),
            lambda mi: mi.value(),
            lambda mi: mi.bounding_box(),
            lambda mi: mi.get_attribute("name"),
        ],
    )
    def test_no_query_opens_the_menu(self, query):
        """Every pure query, not just the three predicates.

        is_checked() went through _resolve(), and wait_for_checked() polls it
        every 150 ms — so waiting on a checkable View menu item physically
        clicked "View" several times a second, toggling the menu open and shut
        under the very poll that was reading it.
        """
        child = self._menu_child()
        try:
            query(_window(_spec_with(child)).menu("File").item("Save"))
        except Exception:
            pass
        child.click_input.assert_not_called()
        child.invoke.assert_not_called()


# Pointer input must not dismiss the popup it is aiming into


class TestFocusForInput:
    def test_an_already_active_window_is_not_refocused(self, monkeypatch):
        """``_locator._is_foreground`` is a patchable module-level hook.

        The real function is swapped for a handle-comparing stand-in and the
        substitute's True answer is read back through the module attribute,
        which is the seam the pointer-input path uses to skip a redundant
        ``set_focus()``. The genuine win32gui lookup needs a live foreground
        window and is not driven here; its unknown-state branch is covered by
        ``test_an_unknown_foreground_state_still_focuses``.
        """
        spec = MagicMock()
        spec.wrapper_object.return_value.handle = 4242
        monkeypatch.setattr(_locator, "_is_foreground", lambda s: s.wrapper_object().handle == 4242)
        assert _locator._is_foreground(spec) is True

    def test_an_unknown_foreground_state_still_focuses(self):
        """A background AUT that never comes forward sends every click to
        whatever window is in front of it, so an unknown answer must be False."""
        broken = MagicMock()
        broken.wrapper_object.side_effect = RuntimeError("no handle")
        assert _locator._is_foreground(broken) is False


# Predicates must agree with the resolver they are predicting


class TestPredicatesSeeWhatClickSees:
    """``is_visible()`` must not disagree with ``click()`` about existence.

    Some elements are reachable only through the TreeWalker last resort —
    ToolbarWindow32 button children are the standard case. Skipping that
    sweep to save a COM walk per poll would report them as invisible while
    ``click()`` resolves them fine, and ``wait_until_hidden()`` would return
    immediately for an element still on screen.
    """

    def _failing_window(self):
        child = MagicMock()
        child.wrapper_object.side_effect = Exception("never visible")
        return _window(_spec_with(child))

    def _record_tree_walk(self, monkeypatch, result=None):
        import dolphin_desktop._locator as locator_module

        calls: list = []

        def _spy(*args):
            calls.append(args)
            return result

        monkeypatch.setattr(locator_module, "_tree_walk_find", _spy)
        return calls

    def test_is_visible_uses_the_tree_walk(self, monkeypatch):
        calls = self._record_tree_walk(monkeypatch)
        self._failing_window().button(name="Bold").is_visible()
        assert calls

    def test_is_enabled_uses_the_tree_walk(self, monkeypatch):
        calls = self._record_tree_walk(monkeypatch)
        self._failing_window().button(name="Bold").is_enabled()
        assert calls

    def test_exists_still_uses_the_tree_walk(self, monkeypatch):
        calls = self._record_tree_walk(monkeypatch)
        self._failing_window().button(name="Bold").exists()
        assert calls

    def test_a_tree_walk_only_element_reports_visible(self, monkeypatch):
        found = MagicMock()
        found.is_visible.return_value = True
        self._record_tree_walk(monkeypatch, result=found)
        assert self._failing_window().button(name="Bold").is_visible() is True


# wait_for(state=...) on results that are wrappers, not WindowSpecifications


class TestWaitForOnRawWrappers:
    def test_enabled_is_polled_when_the_result_has_no_wait_method(self):
        loc = _ResolvedLocator(_FakeWrapper(is_enabled=lambda: True))
        assert loc.wait_for(state="enabled", timeout=0.2) is loc

    def test_a_state_that_never_arrives_still_raises_wait_timeout(self):
        from dolphin_desktop import WaitTimeoutError

        loc = _ResolvedLocator(_FakeWrapper(is_enabled=lambda: False))
        with pytest.raises(WaitTimeoutError):
            loc.wait_for(state="enabled", timeout=0.1)

    def test_an_unreportable_state_fails_fast_with_a_clear_message(self):
        from dolphin_desktop import WaitTimeoutError

        loc = _ResolvedLocator(_FakeWrapper())
        with pytest.raises(WaitTimeoutError) as exc_info:
            loc.wait_for(state="enabled", timeout=5.0)
        assert "_FakeWrapper" in str(exc_info.value)


# Chaining .locator() off an already-resolved element


class TestChainingOffAResolvedElement:
    def _wrapper_parent(self):
        backend = MagicMock()
        backend.name = "uia"
        backend.generic_wrapper_class = lambda el: ("wrapped", el)
        return _FakeWrapper(backend=backend)

    def test_child_search_goes_through_find_elements(self, monkeypatch):
        import pywinauto.findwindows as findwindows

        element = MagicMock()
        monkeypatch.setattr(findwindows, "find_elements", lambda **kw: [element])
        result = _ResolvedLocator(self._wrapper_parent()).locator(title="OK")._resolve()
        assert result == ("wrapped", element)

    def test_no_match_raises_element_not_found(self, monkeypatch):
        import pywinauto.findwindows as findwindows

        monkeypatch.setattr(findwindows, "find_elements", lambda **kw: [])
        loc = _ResolvedLocator(self._wrapper_parent()).locator(title="OK").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc._resolve()

    def test_a_non_wrapper_parent_says_so(self):
        loc = _ResolvedLocator(object()).locator(title="OK").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc._resolve()
        assert "not a pywinauto wrapper" in str(exc_info.value)


# A literal newline or tab in typed text must survive to the widget


class TestTypedWhitespaceSurvives:
    """``parse_keys`` drops a literal ``\n`` / ``\t`` unless told otherwise.

    Escaping also turns pywinauto's own ``~`` newline alias into a literal
    ``{~}``, so with the flags off there is no surviving path at all and
    ``type_text("line1\nline2")`` wrote ``line1line2`` while reporting success.
    """

    @pytest.mark.parametrize("method", ["type_text", "set_text", "fill"])
    def test_every_typing_entry_point_passes_the_flags(self, method):
        child = MagicMock()
        child.set_edit_text.side_effect = Exception("no IValueProvider")
        getattr(_window(_spec_with(child)).edit(), method)("line1\nline2")
        kwargs = child.type_keys.call_args.kwargs
        assert kwargs["with_tabs"] is True
        assert kwargs["with_newlines"] is True

    def test_pywinauto_then_emits_a_real_return(self):
        from pywinauto.keyboard import parse_keys

        from dolphin_desktop._helpers import _escape_keys

        actions = [
            str(a)
            for a in parse_keys(
                _escape_keys("line1\nline2"),
                with_spaces=True,
                with_tabs=True,
                with_newlines=True,
            )
        ]
        assert "<VK_RETURN>" in actions

    def test_without_the_flags_the_newline_would_vanish(self):
        """Pins why the flags are not optional, so they are not 'simplified' away."""
        from pywinauto.keyboard import parse_keys

        from dolphin_desktop._helpers import _escape_keys

        actions = [str(a) for a in parse_keys(_escape_keys("line1\nline2"), with_spaces=True)]
        assert "<VK_RETURN>" not in actions
