"""Tests for the lazy desktop :mod:`_locator` module.

The locator is deliberately tested with small pywinauto-shaped fakes.  This
keeps the suite deterministic and exercises the error/fallback paths that are
not practical to reach with a real Windows application in CI.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

import dolphin_desktop._locator as locator_module
from dolphin_desktop._exceptions import (
    AmbiguousMatchError,
    ElementNotFoundError,
    UnsupportedPatternError,
    WaitTimeoutError,
)


class FakeInfo:
    def __init__(self, *, name="", control_type="", automation_id="", process_id=1, **extra):
        self.name = name
        self.control_type = control_type
        self.automation_id = automation_id
        self.process_id = process_id
        for key, value in extra.items():
            setattr(self, key, value)


class FakeElement:
    def __init__(self, *, text="", value=None, visible=True, enabled=True, **info):
        self.element_info = FakeInfo(**info)
        self._text = text
        self._value = value
        self._visible = visible
        self._enabled = enabled
        self.children_result = []
        self.descendants_result = []

    def window_text(self):
        return self._text

    def get_value(self):
        if isinstance(self._value, BaseException):
            raise self._value
        return self._value

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def rectangle(self):
        return SimpleNamespace(left=10, top=20, right=110, bottom=70)

    def children(self, **criteria):
        return list(self.children_result)

    def descendants(self, **criteria):
        return list(self.descendants_result)


class FakeSpec:
    def __init__(self, *, wrapper=None, children=(), descendants=()):
        self.wrapper = wrapper or FakeElement()
        self.children_result = list(children)
        self.descendants_result = list(descendants)
        self.child_window_calls = []
        self.wait_calls = []

    def child_window(self, **criteria):
        self.child_window_calls.append(criteria)
        return self

    def wrapper_object(self):
        return self.wrapper

    def wait(self, state, timeout=None):
        self.wait_calls.append((state, timeout))

    def children(self, **criteria):
        return list(self.children_result)

    def descendants(self, **criteria):
        return list(self.descendants_result)

    def set_focus(self):
        self.focused = True


def resolved(element=None, *, timeout=0):
    with patch.object(locator_module, "_get_timeout", return_value=timeout):
        result = locator_module._ResolvedLocator(element or FakeElement())
    return result


def monotonic_values(*values):
    return patch.object(locator_module.time, "monotonic", side_effect=values)


def test_wrapper_of_and_takes_exactly_cover_wrappers_and_signatures():
    class Wrapped:
        def wrapper_object(self):
            return "wrapped"

    class Broken:
        def wrapper_object(self):
            raise RuntimeError("broken")

    assert locator_module._wrapper_of(FakeElement()) is not None
    assert locator_module._wrapper_of(Wrapped()) == "wrapped"
    broken = Broken()
    assert locator_module._wrapper_of(broken) is broken

    def exact(a):
        return a

    def variadic(*args):
        return args

    def keywords(**kwargs):
        return kwargs

    assert locator_module._takes_exactly(exact, (1,)) is True
    assert locator_module._takes_exactly(exact, ()) is False
    assert locator_module._takes_exactly(variadic, (1, 2)) is True
    assert locator_module._takes_exactly(keywords, ()) is True
    assert locator_module._takes_exactly(1, ()) is True


def test_pattern_action_dispatches_direct_iface_and_reports_failures():
    direct = Mock()
    element = FakeElement()
    element.invoke = direct
    loc = resolved(element)
    assert (
        locator_module._pattern_action(
            loc, action_name="invoke", pattern_name="InvokePattern", method_name="invoke"
        )
        is loc
    )
    direct.assert_called_once_with()

    iface = Mock()
    element2 = FakeElement()
    element2.iface_value = SimpleNamespace(SetValue=iface)
    loc2 = resolved(element2)
    assert (
        locator_module._pattern_action(
            loc2,
            action_name="set_value",
            pattern_name="ValuePattern",
            method_name="set_value",
            method_args=("abc",),
            iface_fallback=locator_module._set_value_via_iface,
        )
        is loc2
    )
    iface.assert_called_once_with("abc")

    mismatch = FakeElement()
    mismatch.select = lambda: None
    with pytest.raises(UnsupportedPatternError, match="does not expose SelectionItemPattern"):
        locator_module._pattern_action(
            resolved(mismatch),
            action_name="select",
            pattern_name="SelectionItemPattern",
            method_name="select",
            method_args=("wrong",),
        )

    with pytest.raises(UnsupportedPatternError, match="does not expose"):
        locator_module._pattern_action(
            resolved(FakeElement()),
            action_name="invoke",
            pattern_name="InvokePattern",
            method_name="invoke",
        )


@pytest.mark.parametrize(
    "error",
    [
        type("NoPatternInterfaceError", (Exception,), {})(),
        AttributeError("missing"),
        type(
            "AccessDeniedError",
            (Exception,),
            {"hresult": locator_module._E_ACCESSDENIED},
        )(),
    ],
)
def test_pattern_action_converts_missing_pattern_errors(error):
    element = FakeElement()
    element.invoke = Mock(side_effect=error)
    with pytest.raises(UnsupportedPatternError, match="does not implement InvokePattern"):
        resolved(element).invoke()


def test_pattern_action_preserves_resolution_and_unrelated_errors_and_traces():
    loc = resolved(FakeElement())
    resolution_error = RuntimeError("resolve failed")
    loc._resolve = Mock(side_effect=resolution_error)
    with pytest.raises(RuntimeError, match="resolve failed"):
        locator_module._pattern_action(
            loc, action_name="invoke", pattern_name="InvokePattern", method_name="invoke"
        )

    element = FakeElement()
    element.invoke = Mock(side_effect=ValueError("bad action"))
    session = Mock()
    with patch("dolphin_desktop._trace.current_session", return_value=session):
        with pytest.raises(ValueError, match="bad action"):
            resolved(element).invoke()
    assert session.record_step.call_args.args[0] == "invoke"
    assert session.record_step.call_args.args[3] == "bad action"

    element2 = FakeElement()
    element2.iface_value = SimpleNamespace(SetValue=Mock(side_effect=RuntimeError("iface")))
    with pytest.raises(RuntimeError, match="iface"):
        locator_module._pattern_action(
            resolved(element2),
            action_name="set_value",
            pattern_name="ValuePattern",
            method_name="set_value",
            method_args=("x",),
            iface_fallback=locator_module._set_value_via_iface,
        )


def test_public_pattern_actions_reach_each_shared_dispatcher():
    element = FakeElement()
    element.toggle = Mock()
    element.expand = Mock()
    element.collapse = Mock()
    element.select = Mock()
    element.set_value = Mock()
    loc = resolved(element)
    assert loc.toggle() is loc
    assert loc.expand() is loc
    assert loc.collapse() is loc
    assert loc.select() is loc
    assert loc.set_value("value") is loc
    element.toggle.assert_called_once_with()
    element.expand.assert_called_once_with()
    element.collapse.assert_called_once_with()
    element.select.assert_called_once_with()
    element.set_value.assert_called_once_with("value")


def test_wait_until_visible_success_retry_timeout_and_ambiguity():
    from pywinauto.timings import TimeoutError as PyTimeoutError

    element = FakeElement(visible=True)
    with patch.object(locator_module.time, "monotonic", side_effect=[0, 0]):
        locator_module._wait_until_visible(FakeSpec(wrapper=element), 1)

    element._visible = False
    with (
        patch.object(locator_module.time, "monotonic", side_effect=[0, 0, 2]),
        patch.object(locator_module.time, "sleep") as sleep,
    ):
        with pytest.raises(PyTimeoutError):
            locator_module._wait_until_visible(FakeSpec(wrapper=element), 1)
        sleep.assert_called_once()

    from pywinauto.findwindows import ElementAmbiguousError

    ambiguous = FakeSpec(wrapper=FakeElement())
    ambiguous.wrapper_object = Mock(side_effect=ElementAmbiguousError("ambiguous"))
    with monotonic_values(0, 2):
        with pytest.raises(ElementAmbiguousError):
            locator_module._wait_until_visible(ambiguous, 1)

    missing = FakeSpec(wrapper=FakeElement())
    missing.wrapper_object = Mock(side_effect=RuntimeError("not ready"))
    with monotonic_values(0, 2):
        with pytest.raises(PyTimeoutError, match="did not become visible") as exc_info:
            locator_module._wait_until_visible(missing, 1)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_is_foreground_and_trace_step_cover_known_unknown_and_no_session():
    spec = FakeSpec(wrapper=SimpleNamespace(handle=42))
    fake_gui = types.SimpleNamespace(GetForegroundWindow=Mock(return_value=42))
    with patch.dict(sys.modules, {"win32gui": fake_gui}):
        assert locator_module._is_foreground(spec) is True
        fake_gui.GetForegroundWindow.return_value = 7
        assert locator_module._is_foreground(spec) is False
    bad = FakeSpec(wrapper=FakeElement())
    bad.wrapper_object = Mock(side_effect=RuntimeError("bad"))
    with patch.dict(sys.modules, {"win32gui": fake_gui}):
        assert locator_module._is_foreground(bad) is False

    session = Mock()
    with patch("dolphin_desktop._trace.current_session", return_value=None):
        locator_module._trace_step("noop", {"x": 1})
    with patch("dolphin_desktop._trace.current_session", return_value=session):
        locator_module._trace_step("do", {"x": 1}, "element", "error")
    session.record_step.assert_called_once_with("do", "{'x': 1}", "element", "error")


def test_normalize_criteria_aliases_and_conflicts():
    criteria = {"automation_id": "a", "role": "Button", "name": "Save", "other": 1}
    assert locator_module._normalize_criteria(criteria) == {
        "auto_id": "a",
        "control_type": "Button",
        "title": "Save",
        "other": 1,
    }
    with pytest.raises(ValueError, match="conflicting criteria"):
        locator_module._normalize_criteria({"name": "one", "title": "two"})


def test_locator_configuration_and_parent_resolution():
    spec = FakeSpec()
    parent = SimpleNamespace(_get_spec=Mock(return_value=spec))
    fallback = {"automation_id": "fallback"}
    loc = locator_module.Locator(
        parent,
        automation_id="primary",
        fallback=[fallback],
        image_fallback="image",
    )
    assert loc._criteria == {"auto_id": "primary"}
    assert loc._fallback == [{"auto_id": "fallback"}]
    assert fallback == {"automation_id": "fallback"}
    clone = loc._clone(name="Save")
    assert clone is not loc
    assert clone._criteria == {"auto_id": "primary", "title": "Save"}
    assert loc.timeout(2)._timeout == 2
    assert loc._timeout != 2
    assert isinstance(loc.locator(role="Edit"), locator_module.Locator)
    assert loc.nth(3)._criteria["found_index"] == 3
    assert loc._get_parent_spec() is spec

    parent_locator = resolved(FakeElement())
    child = locator_module.Locator(parent_locator, title="child")
    assert child._get_parent_spec() is parent_locator._element


def test_locator_resolve_direct_wrapper_and_window_spec_paths():
    raw = FakeElement()
    with patch.object(locator_module, "_find_under_wrapper", return_value="found") as finder:
        loc = locator_module.Locator(SimpleNamespace(_get_spec=Mock(return_value=raw)), title="x")
        assert loc._resolve() == "found"
        finder.assert_called_once_with(raw, {"title": "x"}, loc._timeout)

    spec = FakeSpec()
    with patch.object(locator_module, "_wait_until_visible") as waiter:
        loc = locator_module.Locator(SimpleNamespace(_get_spec=Mock(return_value=spec)), name="x")
        assert loc._resolve() is spec
    waiter.assert_called_once_with(spec, loc._timeout)
    assert spec.child_window_calls == [{"title": "x"}]


@pytest.mark.parametrize(
    ("index", "expected_index"),
    [(-1, 2), (-2, 1), (-3, 0)],
)
def test_locator_negative_nth_resolves_against_complete_match_set(index, expected_index):
    parent = FakeSpec(wrapper=FakeElement())
    parent.wrapper.descendants_result = [FakeElement(), FakeElement(), FakeElement()]
    loc = locator_module.Locator(
        SimpleNamespace(_get_spec=Mock(return_value=parent)),
        control_type="Button",
    ).nth(index)
    loc._timeout = 0

    with monotonic_values(0, 1), patch.object(locator_module, "_wait_until_visible") as waiter:
        assert loc._resolve() is parent

    assert parent.child_window_calls == [{"control_type": "Button", "found_index": expected_index}]
    waiter.assert_called_once_with(parent, loc._timeout)


def test_locator_negative_nth_prefers_complete_descendants_over_limited_tree_walk():
    parent = FakeSpec(wrapper=FakeElement())
    parent.wrapper.descendants_result = [FakeElement(), FakeElement()]
    shallow_info = SimpleNamespace(name="", control_type="Button", children=lambda: [])
    deep_info = SimpleNamespace(name="", control_type="Button", children=lambda: [])
    nested = deep_info
    for _ in range(9):
        nested = SimpleNamespace(
            name="",
            control_type="Pane",
            children=lambda child=nested: [child],
        )
    parent.wrapper.element_info = SimpleNamespace(
        children=lambda: [shallow_info, nested]
    )

    wrapper_cls = Mock(side_effect=lambda info: ("wrapped", info))
    fake_pw = sys.modules["pywinauto"]
    with patch.object(
        fake_pw,
        "Application",
        return_value=SimpleNamespace(backend=SimpleNamespace(generic_wrapper_class=wrapper_cls)),
    ):
        tree_result = locator_module._tree_walk_find(
            parent, {"control_type": "Button", "found_index": -1}
        )
    assert tree_result == ("wrapped", shallow_info)

    loc = locator_module.Locator(
        SimpleNamespace(_get_spec=Mock(return_value=parent)),
        control_type="Button",
    ).nth(-1)
    loc._timeout = 0

    # TreeWalker can find the shallow first match while its depth cap hides the
    # later match. The nth() index must still be resolved from descendants().
    with (
        monotonic_values(0, 1),
        patch.object(locator_module, "_tree_walk_find", return_value=tree_result),
        patch.object(locator_module, "_wait_until_visible"),
    ):
        assert loc._resolve() is parent

    assert parent.child_window_calls == [{"control_type": "Button", "found_index": 1}]


def test_locator_negative_nth_out_of_range_uses_not_found_behavior():
    parent = FakeSpec(wrapper=FakeElement())
    parent.wrapper.descendants_result = [FakeElement(), FakeElement(), FakeElement()]
    loc = locator_module.Locator(
        SimpleNamespace(_get_spec=Mock(return_value=parent)),
        control_type="Button",
    ).nth(-4)
    loc._timeout = 0

    with monotonic_values(0, 1):
        with pytest.raises(ElementNotFoundError):
            loc._resolve()

    assert parent.child_window_calls == []


def test_locator_negative_nth_polls_until_delayed_match_is_available():
    matches = [FakeElement(), FakeElement(), FakeElement()]
    parent = FakeSpec(wrapper=FakeElement())
    parent.wrapper.descendants = Mock(side_effect=[[], matches])
    loc = locator_module.Locator(
        SimpleNamespace(_get_spec=Mock(return_value=parent)),
        control_type="Button",
    ).nth(-2)
    loc._timeout = 1

    with monotonic_values(0, 0), patch.object(locator_module.time, "sleep"):
        with patch.object(locator_module, "_wait_until_visible"):
            assert loc._resolve() is parent

    assert parent.wrapper.descendants.call_count == 2
    assert parent.child_window_calls == [{"control_type": "Button", "found_index": 1}]


def test_locator_resolve_ambiguity_fallback_image_tree_and_not_found():
    from pywinauto.findwindows import ElementAmbiguousError

    spec = FakeSpec()
    spec.child_window = Mock(side_effect=ElementAmbiguousError("ambiguous"))
    loc = locator_module.Locator(SimpleNamespace(_get_spec=Mock(return_value=spec)), title="x")
    with pytest.raises(AmbiguousMatchError, match="matched more than one"):
        loc._resolve()

    fallback_parent = FakeSpec()
    fallback_result = FakeSpec()
    fallback_parent.child_window = Mock(side_effect=[RuntimeError("primary"), fallback_result])
    with (
        patch.object(locator_module, "_wait_until_visible"),
        patch("dolphin_desktop._selfheal.record_fallback") as record,
    ):
        fallback_loc = locator_module.Locator(
            SimpleNamespace(_get_spec=Mock(return_value=fallback_parent)),
            title="primary",
            fallback=[{"title": "fallback"}],
        )
        assert fallback_loc._resolve() is fallback_result
    record.assert_called_once_with({"title": "primary"}, {"title": "fallback"})

    primary = FakeSpec()
    fb_spec = FakeSpec()
    fb_spec.wait = Mock(side_effect=RuntimeError("fallback miss"))

    def child_window(**criteria):
        if criteria == {"title": "primary"}:
            raise RuntimeError("primary miss")
        return fb_spec

    primary.child_window = child_window
    image = SimpleNamespace(
        find_with_size=Mock(return_value=(20, 30, 40, 50)),
        _template_path="template.png",
    )
    with (
        patch("dolphin_desktop._selfheal.record_fallback") as record,
        patch.object(locator_module, "_tree_walk_find", return_value=None),
    ):
        loc = locator_module.Locator(
            SimpleNamespace(_get_spec=Mock(return_value=primary)),
            title="primary",
            fallback=[{"title": "fallback"}],
            image_fallback=image,
        )
        assert loc._resolve().__class__.__name__ == "_ImageElement"
        assert record.call_count == 1

    empty_image = SimpleNamespace(find_with_size=Mock(return_value=None), _template_path="x")
    with patch.object(locator_module, "_tree_walk_find", return_value="tree"):
        loc = locator_module.Locator(
            SimpleNamespace(_get_spec=Mock(return_value=primary)),
            title="primary",
            image_fallback=empty_image,
        )
        assert loc._resolve() == "tree"

    not_found_spec = FakeSpec()
    not_found_spec.child_window = Mock(side_effect=RuntimeError("missing"))
    broken_image = SimpleNamespace(
        find_with_size=Mock(side_effect=RuntimeError("image")), _template_path="x"
    )
    with patch.object(locator_module, "_tree_walk_find", return_value="tree"):
        loc = locator_module.Locator(
            SimpleNamespace(_get_spec=Mock(return_value=not_found_spec)),
            title="x",
            image_fallback=broken_image,
        )
        assert loc._resolve() == "tree"

    with patch.object(locator_module, "_tree_walk_find", return_value=None):
        loc = locator_module.Locator(
            SimpleNamespace(_get_spec=Mock(return_value=not_found_spec)), title="x"
        )
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc._resolve()
    assert "Last seen tree" in str(exc_info.value)


def test_focus_for_input_uses_root_or_resolved_element_and_swallows_errors():
    root_spec = FakeSpec(wrapper=SimpleNamespace(handle=11))
    root_spec.set_focus = Mock()
    root = SimpleNamespace(_get_spec=Mock(return_value=root_spec))
    child = locator_module.Locator(root, title="child")
    with patch.object(locator_module, "_is_foreground", return_value=False):
        child._focus_for_input()
    root_spec.set_focus.assert_called_once()

    with patch.object(locator_module, "_is_foreground", return_value=True):
        child._focus_for_input()
    assert root_spec.set_focus.call_count == 1

    element = FakeElement()
    element.set_focus = lambda: setattr(element, "focused", True)
    resolved_loc = resolved(element)
    resolved_loc._resolve = Mock(return_value=element)
    resolved_loc._focus_for_input()
    assert getattr(element, "focused", False) is True

    bad = locator_module.Locator(SimpleNamespace(_get_spec=Mock(side_effect=RuntimeError("bad"))))
    bad._focus_for_input()


def test_input_actions_success_timeout_and_errors():
    element = FakeElement()
    for method in ("click_input", "double_click_input", "right_click_input"):
        setattr(element, method, Mock())
    loc = resolved(element)
    with patch.object(loc, "_focus_for_input") as focus:
        assert loc.click() is loc
        assert loc.double_click() is loc
        assert loc.right_click() is loc
    assert element.click_input.called
    assert element.double_click_input.called
    assert element.right_click_input.called
    assert focus.call_count == 3

    timed_click = Mock()
    timed_double = Mock()
    click_clone = resolved(element)
    click_clone.click = timed_click
    double_clone = resolved(element)
    double_clone.double_click = timed_double
    with patch.object(loc, "timeout", side_effect=[click_clone, double_clone]) as timeout:
        assert locator_module.Locator.click(loc, 250) is loc
        assert locator_module.Locator.double_click(loc, 250) is loc
    timed_click.assert_called_once_with()
    timed_double.assert_called_once_with()
    assert timeout.call_args_list == [call(0.25), call(0.25)]

    failing = resolved(FakeElement())
    failing._resolve = Mock(side_effect=RuntimeError("click"))
    with patch.object(failing, "_focus_for_input"):
        with pytest.raises(RuntimeError):
            failing.click()

    failing._resolve = Mock(side_effect=RuntimeError("double"))
    with patch.object(failing, "_focus_for_input"):
        with pytest.raises(RuntimeError):
            failing.double_click()
    failing._resolve = Mock(side_effect=RuntimeError("right"))
    with patch.object(failing, "_focus_for_input"):
        with pytest.raises(RuntimeError):
            failing.right_click()


def test_type_set_clear_and_press_key_paths():
    element = FakeElement()
    element.type_keys = Mock()
    element.set_edit_text = Mock()
    loc = resolved(element)
    with patch.object(locator_module, "_escape_keys", return_value="escaped") as escape:
        assert loc.type_text("literal", with_spaces=False, pause=0.2) is loc
        escape.assert_called_once_with("literal")
    element.type_keys.assert_called_once_with(
        "escaped", with_spaces=False, with_tabs=True, with_newlines=True, pause=0.2
    )
    element.type_keys.reset_mock()
    assert loc.type_text("raw", escape=False) is loc
    element.type_keys.assert_called_once_with(
        "raw", with_spaces=True, with_tabs=True, with_newlines=True, pause=0.05
    )
    element.type_keys.side_effect = RuntimeError("type")
    with pytest.raises(RuntimeError, match="type"):
        loc.type_text("broken")
    element.type_keys.side_effect = None

    assert loc.set_text("direct") is loc
    element.set_edit_text.assert_called_with("direct")
    assert loc.clear() is loc
    element.set_edit_text.assert_called_with("")

    element.set_edit_text.side_effect = RuntimeError("unsupported")
    element.set_focus = Mock()
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module, "_send_keys") as send,
        patch.object(locator_module, "_escape_keys", return_value="typed"),
    ):
        assert loc.set_text("line\n2") is loc
        send.assert_called_once_with("^a")
        assert element.type_keys.call_args.kwargs["with_newlines"] is True
    element.set_edit_text.side_effect = RuntimeError("unsupported")
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module, "_send_keys") as send,
    ):
        assert loc.set_text("") is loc
        send.assert_has_calls([call("^a"), call("{DELETE}")])

    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module, "_send_keys") as send,
    ):
        assert loc.clear() is loc
        send.assert_called_with("^a{DELETE}")
    element.set_focus.side_effect = RuntimeError("clear focus")
    with pytest.raises(RuntimeError, match="clear focus"):
        loc.clear()
    element.set_focus.side_effect = None

    assert loc.press_key("{ENTER}") is loc
    assert element.type_keys.call_args.args == ("{ENTER}",)
    press_clone = resolved(element)
    press = Mock()
    press_clone.press_key = press
    with patch.object(loc, "timeout", return_value=press_clone) as timeout:
        assert locator_module.Locator.press_key(loc, "x", 100) is loc
    press.assert_called_once_with("x")
    timeout.assert_called_once_with(0.1)

    element.type_keys.side_effect = RuntimeError("key")
    with pytest.raises(RuntimeError):
        loc.press_key("x")

    element.set_edit_text.side_effect = RuntimeError("set")
    element.set_focus.side_effect = RuntimeError("focus")
    with pytest.raises(RuntimeError):
        loc.set_text("x")


def test_select_item_direct_typeerror_valueerror_and_win32_fallbacks():
    element = FakeElement()
    element.select = Mock()
    loc = resolved(element)
    assert loc.select_item("A") is loc
    element.select.assert_called_once_with("A")

    list_item = Mock()
    list_item.select = Mock()
    element.select.side_effect = TypeError("takes no args")
    element.children = Mock(return_value=[list_item])
    assert loc.select_item(0) is loc
    list_item.select.assert_called_once_with()

    named = Mock()
    named.wrapper_object.return_value.select = Mock()
    element.children = Mock(return_value=[])
    element.child_window = Mock(return_value=named)
    assert loc.select_item("Named") is loc
    named.wrapper_object.return_value.select.assert_called_once_with()

    invoke_item = Mock()
    invoke_item.invoke = Mock()
    element.select.side_effect = ValueError("no selected index")
    element.expand = Mock()
    element.children = Mock(side_effect=[[], [SimpleNamespace(children=lambda **_: [invoke_item])]])
    assert loc.select_item(0) is loc
    invoke_item.invoke.assert_called_once_with()

    element.expand.side_effect = RuntimeError("expand unavailable")
    element.children = Mock(return_value=[invoke_item])
    assert loc.select_item(0) is loc
    element.expand.side_effect = None

    element.children = Mock(return_value=[])
    descendant = Mock()
    element.descendants = Mock(return_value=[descendant])
    assert loc.select_item(0) is loc
    descendant.click_input.assert_called_once_with()

    element.children = Mock(return_value=[])
    element.child_window = Mock(return_value=named)
    assert loc.select_item("ValueError-name") is loc

    # A non-pywinauto wrapper with no select method reaches the Win32 message
    # fallback.  Cover exact, partial and indexed selection.
    no_select = FakeElement()
    no_select.handle = 101
    fake_gui = types.SimpleNamespace(
        SendMessage=Mock(side_effect=[-1, 2, 2, 2, 2, 2]),
        GetParent=Mock(return_value=202),
        GetDlgCtrlID=Mock(return_value=303),
    )
    with (
        patch.dict(sys.modules, {"win32gui": fake_gui}),
        patch.object(locator_module, "_select_via_popup") as popup,
    ):
        assert resolved(no_select).select_item("partial") is not None
    assert fake_gui.SendMessage.call_count == 4
    popup.assert_not_called()

    no_select2 = FakeElement()
    no_select2.handle = 102
    fake_gui2 = types.SimpleNamespace(
        SendMessage=Mock(return_value=4),
        GetParent=Mock(return_value=202),
        GetDlgCtrlID=Mock(return_value=303),
    )
    with (
        patch.dict(sys.modules, {"win32gui": fake_gui2}),
        patch.object(locator_module, "_select_via_popup") as popup,
    ):
        assert resolved(no_select2).select_item(4) is not None
    assert fake_gui2.SendMessage.call_count == 2
    popup.assert_not_called()

    exact_gui = types.SimpleNamespace(
        SendMessage=Mock(return_value=0),
        GetParent=Mock(return_value=202),
        GetDlgCtrlID=Mock(return_value=303),
    )
    exact_element = FakeElement()
    exact_element.handle = 103
    with (
        patch.dict(sys.modules, {"win32gui": exact_gui}),
        patch.object(locator_module, "_select_via_popup") as popup,
    ):
        assert resolved(exact_element).select_item("exact") is not None
    popup.assert_not_called()

    not_found_gui = types.SimpleNamespace(
        SendMessage=Mock(return_value=-1),
        GetParent=Mock(return_value=202),
        GetDlgCtrlID=Mock(return_value=303),
    )
    with (
        patch.dict(sys.modules, {"win32gui": not_found_gui}),
        patch.object(locator_module, "_select_via_popup") as popup,
    ):
        assert resolved(no_select).select_item("missing") is not None
    popup.assert_called_once()


def test_select_item_popup_fallback_and_resolution_failure():
    element = FakeElement()
    element.select = Mock(side_effect=RuntimeError("select unavailable"))
    with patch.object(locator_module, "_select_via_popup") as popup:
        assert resolved(element).select_item("x") is not None
    popup.assert_called_once()
    assert popup.call_args.kwargs["cause"].args == ("select unavailable",)

    loc = resolved(FakeElement())
    loc._resolve = Mock(side_effect=ElementNotFoundError("missing"))
    with pytest.raises(ElementNotFoundError):
        loc.select_item("x")


def test_select_item_keyboard_all_inputs_and_errors():
    element = FakeElement()
    element.set_focus = Mock()
    loc = resolved(element)
    with (
        patch.object(locator_module, "_send_keys") as send,
        patch.object(locator_module.time, "sleep"),
    ):
        assert loc.select_item_keyboard(0) is loc
        assert loc.select_item_keyboard(2) is loc
        assert loc.select_item_keyboard("A") is loc
    assert call("%{DOWN}") in send.call_args_list
    assert call("{DOWN 2}") in send.call_args_list
    assert call("A") in send.call_args_list
    with pytest.raises(ValueError, match="non-empty"):
        loc.select_item_keyboard("")
    with pytest.raises(ValueError, match="non-empty"):
        loc.select_item_keyboard(1.5)  # type: ignore[arg-type]
    element.set_focus.side_effect = RuntimeError("focus")
    with pytest.raises(RuntimeError):
        loc.select_item_keyboard("x")


def test_check_uncheck_is_checked_and_toggle_helpers():
    class ToggleWrapper:
        def __init__(self, state):
            self.state = state
            self.toggle = Mock()

        def get_check_state(self):
            return self.state

    unchecked = ToggleWrapper(0)
    loc = resolved(unchecked)
    assert loc.check() is loc
    unchecked.toggle.assert_called_once_with()
    assert loc.is_checked() is False

    checked = ToggleWrapper(1)
    loc_checked = resolved(checked)
    assert loc_checked.check() is loc_checked
    checked.toggle.assert_not_called()
    assert loc_checked.is_checked() is True

    indeterminate = ToggleWrapper(2)
    assert resolved(indeterminate).check() is not None
    indeterminate.toggle.assert_called_once_with()

    class ToggleOnly:
        def __init__(self):
            self.get_toggle_state = Mock(return_value=1)
            self.toggle = Mock()

    toggle_only = ToggleOnly()
    assert locator_module._get_check_state(toggle_only) == 1
    assert resolved(toggle_only).uncheck() is not None
    assert resolved(ToggleWrapper(0)).uncheck() is not None

    class InterfaceOnly:
        iface_toggle = SimpleNamespace(CurrentToggleState=2)

    assert locator_module._get_check_state(InterfaceOnly()) == 2

    class Unreadable:
        def get_check_state(self):
            raise AttributeError

        def get_toggle_state(self):
            raise RuntimeError

        iface_toggle = SimpleNamespace(CurrentToggleState=property(lambda _: 1))

    assert locator_module._get_check_state(Unreadable()) is None
    with pytest.raises(UnsupportedPatternError, match="no check state"):
        resolved(Unreadable()).is_checked()
    with pytest.raises(UnsupportedPatternError, match="no check state"):
        resolved(Unreadable()).uncheck()

    failing = resolved(ToggleWrapper(0))
    failing._resolve = Mock(side_effect=RuntimeError("check resolve"))
    with pytest.raises(RuntimeError, match="check resolve"):
        failing.check()

    assert locator_module._require_check_state(ToggleWrapper(1), "check") == 1
    with pytest.raises(UnsupportedPatternError, match="no check state"):
        locator_module._require_check_state(Unreadable(), "check")


def test_toggle_element_direct_iface_pattern_fallbacks():
    direct = SimpleNamespace(toggle=Mock())
    locator_module._toggle_element(direct)
    direct.toggle.assert_called_once_with()

    iface = SimpleNamespace(iface_toggle=SimpleNamespace(Toggle=Mock()))
    locator_module._toggle_element(iface)
    iface.iface_toggle.Toggle.assert_called_once_with()

    class NoPattern:
        def toggle(self):
            raise type("NoPatternInterfaceError", (Exception,), {})()

    fallback = NoPattern()
    fallback.click_input = Mock()
    locator_module._toggle_element(fallback)
    fallback.click_input.assert_called_once_with()

    generic = SimpleNamespace(toggle=Mock(side_effect=RuntimeError("toggle")))
    with pytest.raises(RuntimeError, match="toggle"):
        locator_module._toggle_element(generic)

    iface_bad = SimpleNamespace(
        iface_toggle=SimpleNamespace(Toggle=Mock(side_effect=RuntimeError("iface"))),
        click_input=Mock(),
    )
    locator_module._toggle_element(iface_bad)
    iface_bad.click_input.assert_called_once_with()


def test_focus_scroll_text_value_and_queries():
    element = FakeElement(text="hello", value="v")
    element.set_focus = Mock()
    element.iface_scroll_item = SimpleNamespace(ScrollIntoView=Mock())
    loc = resolved(element)
    assert loc.focus() is loc
    assert loc.scroll_into_view() is loc
    element.iface_scroll_item.ScrollIntoView.assert_called_once_with()

    no_scroll = FakeElement()
    no_scroll.set_focus = Mock()
    resolved(no_scroll).scroll_into_view()
    no_scroll.set_focus.assert_called_once_with()
    no_scroll.set_focus.side_effect = RuntimeError("focus")
    resolved(no_scroll).scroll_into_view()

    assert loc.text() == "hello"
    empty = FakeElement(text="", value="value")
    assert resolved(empty).text() == "value"
    no_value = FakeElement(text="", value=RuntimeError("no value"))
    with patch.object(locator_module, "_read_text_via_clipboard", return_value="clipboard"):
        assert resolved(no_value).text() == "clipboard"
    assert loc.value() == "v"

    class WindowTextOnly:
        element_info = FakeInfo()

        def window_text(self):
            return "window"

    fallback_value = WindowTextOnly()
    assert resolved(fallback_value).value() == "window"

    assert loc.is_visible() is True
    assert loc.is_enabled() is True
    hidden = FakeElement(visible=False, enabled=False)
    assert resolved(hidden).is_visible() is False
    assert resolved(hidden).is_enabled() is False
    broken = resolved(FakeElement())
    broken._resolve = Mock(side_effect=RuntimeError("broken"))
    assert broken.is_visible() is False
    assert broken.is_enabled() is False
    assert loc.bounding_box() == {
        "left": 10,
        "top": 20,
        "right": 110,
        "bottom": 70,
        "width": 100,
        "height": 50,
    }


def test_exists_waits_and_bounding_box_error_safe_queries():
    loc = resolved(FakeElement())
    assert loc.exists() is True
    ambiguous = resolved(FakeElement())
    ambiguous._resolve = Mock(side_effect=AmbiguousMatchError("ambiguous"))
    assert ambiguous.exists() is True
    missing = resolved(FakeElement())
    missing._resolve = Mock(side_effect=RuntimeError("missing"))
    assert missing.exists() is False

    spec = FakeSpec()
    loc = resolved(spec)
    assert loc.wait_for(state="visible") is loc
    assert loc.wait_for(state="exists") is loc
    spec.wait = Mock()
    assert loc.wait_for(state="enabled", timeout=2) is loc
    spec.wait.assert_called_once_with("enabled", timeout=2)
    spec.wait.side_effect = RuntimeError("wait")
    with pytest.raises(WaitTimeoutError, match="did not reach state"):
        loc.wait_for(state="enabled", timeout=0)

    raw = SimpleNamespace(is_enabled=Mock(return_value=True))
    raw_loc = resolved(raw)
    assert raw_loc.wait_for(state="enabled", timeout=1) is raw_loc
    retry_probe = SimpleNamespace(is_enabled=Mock(side_effect=[RuntimeError("not yet"), True]))
    with monotonic_values(0, 0), patch.object(locator_module.time, "sleep"):
        assert resolved(retry_probe).wait_for(state="enabled", timeout=1) is not None
    unknown = resolved(SimpleNamespace())
    with pytest.raises(WaitTimeoutError, match="cannot report state"):
        unknown.wait_for(state="unknown", timeout=0)
    probe_bad = SimpleNamespace(is_enabled=Mock(side_effect=RuntimeError("not yet")))
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="did not reach state"):
            resolved(probe_bad).wait_for(state="enabled", timeout=1)
    false_probe = SimpleNamespace(is_enabled=Mock(return_value=False))
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="did not reach state"):
            resolved(false_probe).wait_for(state="enabled", timeout=1)

    spec.wait.side_effect = None
    assert loc.wait_until_enabled(timeout=0) is loc


def test_wait_until_hidden_checked_and_text_matching():
    visible = FakeElement(visible=False)
    loc = resolved(visible)
    assert loc.wait_until_hidden(timeout=0) is loc

    visible._visible = True
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="still visible"):
            loc.wait_until_hidden(timeout=1)

    changing = FakeElement()
    changing.is_visible = Mock(side_effect=[True, False])
    with monotonic_values(0, 0), patch.object(locator_module.time, "sleep"):
        assert resolved(changing).wait_until_hidden(timeout=1) is not None

    state = ToggleStateSequence([0, 1])
    loc = resolved(state)
    with monotonic_values(0, 0, 2), patch.object(locator_module.time, "sleep"):
        assert loc.wait_for_checked(checked=True, timeout=1, poll_interval=0.01) is loc
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="unchecked"):
            loc.wait_for_checked(checked=False, timeout=1)

    text_element = TextSequence(["old", "ready now"])
    loc = resolved(text_element)
    with monotonic_values(0, 0, 2), patch.object(locator_module.time, "sleep"):
        assert loc.wait_for_text("ready", timeout=1, poll_interval=0.01) is loc
    exact = TextSequence(["not", "done"])
    with monotonic_values(0, 0, 2), patch.object(locator_module.time, "sleep"):
        assert resolved(exact).wait_for_text("done", contains=False, timeout=1) is not None
    regex = TextSequence(["loading", "status: 200"])
    with monotonic_values(0, 0, 2), patch.object(locator_module.time, "sleep"):
        assert resolved(regex).wait_for_text(text_re=r"status: \d+", timeout=1) is not None

    for kwargs in ({}, {"text": "x", "text_re": "x"}, {"text": ""}):
        with pytest.raises(ValueError):
            resolved(TextSequence(["x"])).wait_for_text(**kwargs)
    never = TextSequence(["current"])
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="last seen"):
            resolved(never).wait_for_text("missing", timeout=1)
    errors = TextSequence([RuntimeError("not ready"), None])
    with monotonic_values(0, 0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError):
            resolved(errors).wait_for_text("missing", timeout=1)
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="regex"):
            resolved(TextSequence(["no match"])).wait_for_text(text_re="ready", timeout=1)
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(WaitTimeoutError, match="regex ''"):
            resolved(TextSequence(["still text"])).wait_for_text(text_re="", timeout=1)


class ToggleStateSequence:
    def __init__(self, states):
        self.states = iter(states)

    def get_check_state(self):
        return next(self.states)


class TextSequence:
    def __init__(self, values):
        self.values = iter(values)

    def window_text(self):
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value


def test_screenshot_fill_hover_scroll_and_mouse_drag(tmp_path):
    import pywinauto.mouse as py_mouse
    from PIL import Image

    image = Image.new("RGB", (2, 3))
    element = FakeElement()
    element.capture_as_image = Mock(return_value=image)
    loc = resolved(element)
    assert loc.screenshot() is image
    output = tmp_path / "screenshot.png"
    with patch.object(image, "save") as save:
        assert loc.screenshot(output) is image
        save.assert_called_once()
    timeout_clone = resolved(element)
    timeout_clone.screenshot = Mock(return_value=image)
    with patch.object(loc, "timeout", return_value=timeout_clone) as timeout:
        assert locator_module.Locator.screenshot(loc, None, 100) is image
    timeout.assert_called_once_with(0.1)

    with patch.object(locator_module.Locator, "set_text") as set_text:
        assert loc.fill("value") is loc
        set_text.assert_called_once_with("value")
        set_text.reset_mock()
        assert loc.fill("value", timeout_ms=200) is loc
        assert set_text.call_args.args == ("value",)

    mouse = types.SimpleNamespace(move=Mock(), press=Mock(), release=Mock(), scroll=Mock())
    with (
        patch.object(py_mouse, "move", mouse.move),
        patch.object(
            loc, "bounding_box", return_value={"left": 10, "top": 20, "width": 4, "height": 6}
        ),
    ):
        assert loc.hover() is loc
        hover_clone = resolved(element)
        hover = Mock()
        hover_clone.hover = hover
        with patch.object(loc, "timeout", return_value=hover_clone):
            assert locator_module.Locator.hover(loc, 100) is loc
        hover.assert_called_once_with()
    mouse.move.assert_called_once_with(coords=(12, 23))

    with (
        patch.object(loc, "_focus_for_input"),
        patch.object(
            loc, "bounding_box", return_value={"left": 0, "top": 0, "width": 10, "height": 10}
        ),
        patch.object(locator_module.time, "sleep"),
        patch.object(py_mouse, "press", mouse.press),
        patch.object(py_mouse, "move", mouse.move),
        patch.object(py_mouse, "release", mouse.release),
    ):
        assert loc.drag_to((30, 40), duration=0) is loc
    assert mouse.press.call_args == call(button="left", coords=(5, 5))
    assert mouse.release.call_args == call(button="left", coords=(30, 40))

    target = resolved(FakeElement())
    with (
        patch.object(loc, "_focus_for_input"),
        patch.object(
            loc, "bounding_box", return_value={"left": 0, "top": 0, "width": 10, "height": 10}
        ),
        patch.object(
            target,
            "bounding_box",
            return_value={"left": 100, "top": 100, "width": 10, "height": 10},
        ),
        patch.object(locator_module.time, "sleep"),
        patch.object(py_mouse, "press", mouse.press),
        patch.object(py_mouse, "move", mouse.move),
        patch.object(py_mouse, "release", mouse.release),
    ):
        loc.drag_to(target, duration=0.01, button="right")
    assert mouse.press.call_args == call(button="right", coords=(5, 5))

    failing_mouse = types.SimpleNamespace(
        move=Mock(side_effect=[None, RuntimeError("move")]), press=Mock(), release=Mock()
    )
    with (
        patch.object(loc, "_focus_for_input"),
        patch.object(
            loc, "bounding_box", return_value={"left": 0, "top": 0, "width": 10, "height": 10}
        ),
        patch.object(locator_module.time, "sleep"),
        patch.object(py_mouse, "press", failing_mouse.press),
        patch.object(py_mouse, "move", failing_mouse.move),
        patch.object(py_mouse, "release", failing_mouse.release),
    ):
        with pytest.raises(RuntimeError):
            loc.drag_to((30, 40))
    failing_mouse.release.assert_called_once()

    with (
        patch.object(
            loc, "bounding_box", return_value={"left": 0, "top": 0, "width": 10, "height": 10}
        ),
        patch.object(py_mouse, "move", mouse.move),
        patch.object(py_mouse, "scroll", mouse.scroll),
    ):
        assert loc.scroll("up", 4) is loc
        assert loc.scroll("down", 5) is loc
        with pytest.warns(UserWarning):
            assert loc.scroll("sideways") is loc
    assert mouse.scroll.call_args_list[-2:] == [
        call(coords=(5, 5), wheel_dist=4),
        call(coords=(5, 5), wheel_dist=-5),
    ]
    scroll_clone = resolved(element)
    scroll = Mock()
    scroll_clone.scroll = scroll
    with patch.object(loc, "timeout", return_value=scroll_clone):
        assert locator_module.Locator.scroll(loc, "up", 1, timeout_ms=100) is loc
    scroll.assert_called_once_with("up", 1)


def test_attributes_selection_collections_repr_and_dump_tree():
    info = FakeInfo(name="name", custom="value")
    element = SimpleNamespace(element_info=info, set_focus=Mock())
    loc = resolved(element)
    assert loc.get_attribute("custom") == "value"
    assert loc.get_attribute("missing", "default") == "default"
    with pytest.raises(AttributeError, match="publishes no attribute 'missing'"):
        loc.get_attribute("missing")

    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module, "_send_keys") as send,
    ):
        assert loc.select_text() is loc
    send.assert_called_once_with("^a")

    parent = FakeSpec(children=[FakeElement(), FakeElement()])
    parent.descendants_result = [FakeElement(), FakeElement()]
    loc = locator_module.Locator(SimpleNamespace(_get_spec=Mock(return_value=parent)), title="x")
    assert len(loc.all()) == 2
    assert len(loc.all(depth=0)) == 2
    parent.children = Mock(side_effect=RuntimeError("children"))
    assert loc.all() == []
    assert loc.count() == 0
    assert "Locator(" in repr(loc)

    child_info = FakeInfo(
        name="Button" + chr(9) + "shortcut", control_type="Button", automation_id="ok"
    )
    child = SimpleNamespace(element_info=child_info, children=lambda: [])
    root = SimpleNamespace(children=lambda: [child])
    expected_tree_line = "Button name='Button" + chr(92) + "tshortcut' id='ok'"
    assert locator_module._dump_tree(root) == expected_tree_line
    assert locator_module._dump_tree(SimpleNamespace(children=lambda: [])) == "(empty)"
    no_name_id = SimpleNamespace(
        element_info=FakeInfo(name="", automation_id="auto"), children=lambda: []
    )
    no_name_no_id = SimpleNamespace(
        element_info=FakeInfo(name="", automation_id=""), children=lambda: []
    )
    compact = SimpleNamespace(children=lambda: [no_name_id, no_name_no_id])
    assert locator_module._dump_tree(compact) == "? id='auto'\n?"

    class BadInfoChild:
        def children(self):
            return []

        @property
        def element_info(self):
            raise RuntimeError("info")

    bad_child = BadInfoChild()
    assert "?" in locator_module._dump_tree(SimpleNamespace(children=lambda: [bad_child]))
    assert (
        locator_module._dump_tree(SimpleNamespace(children=Mock(side_effect=RuntimeError("tree"))))
        == "(empty)"
    )
    with patch.object(locator_module, "_collect_tree", side_effect=RuntimeError("capture")):
        assert locator_module._dump_tree(root) == "(could not capture tree)"

    lines = []
    locator_module._collect_tree(root, lines, 4, 3)
    many = SimpleNamespace(children=lambda: [child] * 25)
    lines = []
    locator_module._collect_tree(many, lines, 0, 0)
    assert lines == [expected_tree_line] * 20

    message = locator_module._NotFoundMessage({"title": "x"}, 0.5, root)
    assert "not found" in str(message)
    assert repr(message) == str(message)


def test_select_popup_helper_string_index_and_timeout():
    item = SimpleNamespace(window_text=Mock(return_value="Wanted"), click_input=Mock())
    wrong_process = SimpleNamespace(
        element_info=FakeInfo(process_id=999), descendants=Mock(return_value=[])
    )
    no_items = SimpleNamespace(
        element_info=FakeInfo(process_id=1), descendants=Mock(return_value=[])
    )
    win = SimpleNamespace(
        element_info=FakeInfo(process_id=1),
        descendants=Mock(return_value=[item]),
    )
    element = SimpleNamespace(
        expand=Mock(side_effect=RuntimeError("already expanded")),
        element_info=FakeInfo(process_id=1),
    )
    desktop = Mock(
        return_value=SimpleNamespace(windows=Mock(return_value=[wrong_process, no_items, win]))
    )
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module.time, "monotonic", return_value=0),
        patch.object(sys.modules["pywinauto"], "Desktop", desktop),
    ):
        locator_module._select_via_popup(element, "Wanted", 1)
    item.click_input.assert_called_once_with()

    indexed = SimpleNamespace(click_input=Mock())
    win.descendants.return_value = [indexed]
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module.time, "monotonic", return_value=0),
        patch.object(sys.modules["pywinauto"], "Desktop", desktop),
    ):
        locator_module._select_via_popup(element, 0, 1)
    indexed.click_input.assert_called_once_with()

    empty_win = SimpleNamespace(
        element_info=FakeInfo(process_id=1), descendants=Mock(side_effect=RuntimeError("gone"))
    )
    desktop.return_value.windows.return_value = [empty_win]
    cause = RuntimeError("original")
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module.time, "monotonic", side_effect=[0, 0, 2]),
        patch.object(sys.modules["pywinauto"], "Desktop", desktop),
    ):
        with pytest.raises(ElementNotFoundError) as exc_info:
            locator_module._select_via_popup(element, "missing", 1, cause=cause)
    assert exc_info.value.__cause__ is cause

    bad_label = SimpleNamespace(window_text=Mock(side_effect=RuntimeError("label")))
    good_label = SimpleNamespace(window_text=Mock(return_value="Wanted"), click_input=Mock())
    win.descendants.return_value = [bad_label, good_label]
    desktop.return_value.windows.return_value = [win]
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module.time, "monotonic", return_value=0),
        patch.object(sys.modules["pywinauto"], "Desktop", desktop),
    ):
        locator_module._select_via_popup(element, "Wanted", 1)
    good_label.click_input.assert_called_once_with()

    string_first = SimpleNamespace(
        element_info=FakeInfo(process_id=1),
        descendants=Mock(return_value=[SimpleNamespace(window_text=Mock(return_value="other"))]),
    )
    string_second_item = SimpleNamespace(
        window_text=Mock(return_value="Wanted"), click_input=Mock()
    )
    string_second = SimpleNamespace(
        element_info=FakeInfo(process_id=1), descendants=Mock(return_value=[string_second_item])
    )
    desktop.return_value.windows.return_value = [string_first, string_second]
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module.time, "monotonic", return_value=0),
        patch.object(sys.modules["pywinauto"], "Desktop", desktop),
    ):
        locator_module._select_via_popup(element, "Wanted", 1)
    string_second_item.click_input.assert_called_once_with()

    first_window = SimpleNamespace(
        element_info=FakeInfo(process_id=1), descendants=Mock(return_value=[SimpleNamespace()])
    )
    second_window_item = SimpleNamespace(click_input=Mock())
    second_window = SimpleNamespace(
        element_info=FakeInfo(process_id=1),
        descendants=Mock(return_value=[SimpleNamespace(), second_window_item]),
    )
    desktop.return_value.windows.return_value = [first_window, second_window]
    with (
        patch.object(locator_module.time, "sleep"),
        patch.object(locator_module.time, "monotonic", return_value=0),
        patch.object(sys.modules["pywinauto"], "Desktop", desktop),
    ):
        locator_module._select_via_popup(element, 1, 1)
    second_window_item.click_input.assert_called_once_with()


def test_qt_object_name_locator_resolves_suffix_index_and_times_out():
    parent = FakeSpec(
        descendants=[
            SimpleNamespace(element_info=FakeInfo(automation_id="root.first")),
            SimpleNamespace(element_info=FakeInfo(automation_id="target")),
            SimpleNamespace(element_info=FakeInfo(automation_id="root.target")),
        ]
    )
    window = SimpleNamespace(_get_spec=Mock(return_value=parent))
    qt = locator_module._QtObjectNameLocator(window, "target")
    qt._timeout = 1
    with monotonic_values(0):
        assert qt._resolve().element_info.automation_id == "target"
    qt._criteria["found_index"] = 1
    with monotonic_values(0):
        assert qt._resolve().element_info.automation_id == "root.target"

    class BadAutomationId:
        @property
        def element_info(self):
            raise RuntimeError("bad")

    bad_desc = BadAutomationId()
    parent.descendants_result = [bad_desc]
    with monotonic_values(0, 0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(ElementNotFoundError, match="objectName"):
            qt._resolve()

    parent.descendants = Mock(side_effect=RuntimeError("descendants"))
    with monotonic_values(0, 2), patch.object(locator_module.time, "sleep"):
        with pytest.raises(ElementNotFoundError) as exc_info:
            qt._resolve()
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_read_text_via_clipboard_success_and_all_best_effort_failures():
    clipboard = types.SimpleNamespace(
        OpenClipboard=Mock(),
        EmptyClipboard=Mock(),
        CloseClipboard=Mock(),
        GetClipboardData=Mock(return_value="copied"),
    )
    con = types.SimpleNamespace(CF_UNICODETEXT=13)
    element = SimpleNamespace(set_focus=Mock())
    with (
        patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}),
        patch.object(locator_module, "_send_keys") as send,
        patch.object(locator_module.time, "sleep"),
    ):
        assert locator_module._read_text_via_clipboard(element) == "copied"
    send.assert_called_once_with("^a^c")
    assert clipboard.CloseClipboard.call_count == 2

    no_focus = SimpleNamespace(set_focus=Mock(side_effect=RuntimeError("focus")))
    with patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}):
        assert locator_module._read_text_via_clipboard(no_focus) == ""

    clipboard.OpenClipboard.side_effect = RuntimeError("locked")
    with (
        patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}),
        patch.object(locator_module, "_send_keys", side_effect=RuntimeError("input")),
        patch.object(locator_module.time, "sleep"),
    ):
        assert locator_module._read_text_via_clipboard(element) == ""

    clipboard.OpenClipboard.side_effect = [None, RuntimeError("locked read")]
    with (
        patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}),
        patch.object(locator_module, "_send_keys"),
        patch.object(locator_module.time, "sleep"),
    ):
        assert locator_module._read_text_via_clipboard(element) == ""

    clipboard.OpenClipboard.side_effect = None
    clipboard.GetClipboardData.side_effect = RuntimeError("empty")
    with (
        patch.dict(sys.modules, {"win32clipboard": clipboard, "win32con": con}),
        patch.object(locator_module, "_send_keys"),
        patch.object(locator_module.time, "sleep"),
    ):
        assert locator_module._read_text_via_clipboard(element) == ""


def test_find_under_wrapper_success_invalid_and_timeout():
    wrapped = object()
    wrapper_cls = Mock(return_value="wrapped-result")
    parent = SimpleNamespace(backend=SimpleNamespace(name="uia", generic_wrapper_class=wrapper_cls))
    with patch("pywinauto.findwindows.find_elements", return_value=[wrapped]) as find:
        assert locator_module._find_under_wrapper(parent, {"title": "x"}, 1) == "wrapped-result"
    find.assert_called_once()
    wrapper_cls.assert_called_once_with(wrapped)

    with pytest.raises(ElementNotFoundError, match="Cannot search"):
        locator_module._find_under_wrapper(SimpleNamespace(), {"title": "x"}, 0)

    backend = SimpleNamespace(name="uia", generic_wrapper_class=wrapper_cls)
    parent = SimpleNamespace(backend=backend)
    with (
        patch("pywinauto.findwindows.find_elements", side_effect=[RuntimeError("search"), []]),
        patch.object(locator_module.time, "monotonic", side_effect=[0, 0, 2]),
        patch.object(locator_module.time, "sleep"),
    ):
        with pytest.raises(ElementNotFoundError, match="not found") as exc_info:
            locator_module._find_under_wrapper(parent, {"title": "x"}, 1)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_tree_walk_find_filters_walks_children_and_builds_wrapper():
    assert locator_module._tree_walk_find(SimpleNamespace(), {"auto_id": "x"}) is None
    assert locator_module._tree_walk_find(SimpleNamespace(), {}) is None

    grand = SimpleNamespace(name="Grand", control_type="Button", children=lambda: [])
    skipped = SimpleNamespace(name="Skip", control_type="Button", children=lambda: [])
    match = SimpleNamespace(
        name="Wanted\tshortcut", control_type="Button", children=lambda: [grand]
    )
    second_match = SimpleNamespace(name="Wanted", control_type="Button", children=lambda: [])
    root_info = SimpleNamespace(children=lambda: [skipped, match, second_match])
    root = SimpleNamespace(
        wrapper_object=Mock(return_value=SimpleNamespace(element_info=root_info))
    )
    wrapper_cls = Mock(side_effect=lambda info: ("wrapped", info))
    fake_pw = sys.modules["pywinauto"]
    application = patch.object(
        fake_pw,
        "Application",
        return_value=SimpleNamespace(backend=SimpleNamespace(generic_wrapper_class=wrapper_cls)),
    )
    with application:
        assert locator_module._tree_walk_find(
            root, {"title": "Wanted", "control_type": "Button"}
        ) == (
            "wrapped",
            match,
        )
    with application:
        assert locator_module._tree_walk_find(root, {"title": "Wanted", "found_index": 1}) == (
            "wrapped",
            second_match,
        )
    with application:
        assert locator_module._tree_walk_find(
            root, {"control_type": "Button", "found_index": -1}
        ) == ("wrapped", second_match)
        assert locator_module._tree_walk_find(root, {"title": "Wanted", "found_index": -3}) is None

    class BadTreeChild:
        control_type = "Button"

        def children(self):
            return []

        @property
        def name(self):
            raise RuntimeError("bad child")

    failing_match = SimpleNamespace(name="Wanted", control_type="Button", children=lambda: [])
    nested_match = SimpleNamespace(name="Wanted", control_type="Button", children=lambda: [])
    container = SimpleNamespace(
        name="Container", control_type="Pane", children=lambda: [nested_match]
    )
    complex_info = SimpleNamespace(children=lambda: [BadTreeChild(), failing_match, container])
    complex_root = SimpleNamespace(
        wrapper_object=Mock(return_value=SimpleNamespace(element_info=complex_info))
    )
    failing_wrapper = Mock(side_effect=[RuntimeError("wrap"), ("nested", nested_match)])
    with patch.object(
        fake_pw,
        "Application",
        return_value=SimpleNamespace(
            backend=SimpleNamespace(generic_wrapper_class=failing_wrapper)
        ),
    ):
        assert locator_module._tree_walk_find(
            complex_root, {"title": "Wanted", "control_type": "Button"}
        ) == ("nested", nested_match)

    deep = SimpleNamespace(name="", control_type="", children=lambda: [])
    for _ in range(9):
        deep = SimpleNamespace(name="", control_type="", children=lambda child=deep: [child])
    deep_root = SimpleNamespace(
        wrapper_object=Mock(return_value=SimpleNamespace(element_info=deep))
    )
    assert locator_module._tree_walk_find(deep_root, {"title": "never"}) is None

    no_root = SimpleNamespace(wrapper_object=Mock(side_effect=RuntimeError("root")))
    assert locator_module._tree_walk_find(no_root, {"title": "x"}) is None
    broken_children = SimpleNamespace(children=Mock(side_effect=RuntimeError("children")))
    broken_root = SimpleNamespace(
        wrapper_object=Mock(return_value=SimpleNamespace(element_info=broken_children))
    )
    assert locator_module._tree_walk_find(broken_root, {"title": "x"}) is None


def test_resolved_locator_nth_and_resolve():
    element = FakeElement()
    loc = locator_module._ResolvedLocator(element)
    assert loc._resolve() is element
    assert loc._get_parent_spec() is element
    assert loc.nth(0)._element is element
    with pytest.raises(ValueError, match="not supported"):
        loc.nth(1)


def test_locator_normalizes_friendly_selector_names() -> None:
    from dolphin_desktop._locator import _normalize_criteria

    assert _normalize_criteria({"name": "Save", "class_name": "Button"}) == {
        "title": "Save",
        "class_name": "Button",
    }
