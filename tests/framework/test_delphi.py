"""Tests for the Delphi UIA facade.

These tests deliberately use small UIA doubles: the resolver needs to stay
portable across VCL and LCL and must therefore be testable without a Delphi
or Lazarus executable installed on the runner.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from dolphin_desktop._delphi import (
    DelphiApp,
    DelphiComponent,
    DelphiError,
    DelphiForm,
    _attach_delphi,
    _class_aliases,
    _first_action_that_works,
    _first_that_works,
    _launch_delphi,
    _safe,
    _uia_control_type,
)
from dolphin_desktop._exceptions import ElementNotFoundError, WaitTimeoutError


class _Element:
    def __init__(
        self,
        *,
        name: str = "",
        class_name: str = "",
        control_type: str = "",
        automation_id: str = "",
        text: str = "",
        rect: tuple[int, int, int, int] = (0, 0, 10, 10),
        children: list[object] | None = None,
    ) -> None:
        self.element_info = SimpleNamespace(
            name=name,
            class_name=class_name,
            control_type=control_type,
            automation_id=automation_id,
        )
        self._text = text
        self._rect = rect
        self._children = children or []

    def window_text(self) -> str:
        return self._text

    def rectangle(self) -> SimpleNamespace:
        left, top, right, bottom = self._rect
        return SimpleNamespace(
            left=left,
            top=top,
            right=right,
            bottom=bottom,
            width=lambda: right - left,
            height=lambda: bottom - top,
        )

    def children(self) -> list[object]:
        return self._children


def test_component_state_reads_and_simple_actions_cover_all_fallbacks() -> None:
    attrs = Mock()
    attrs.window_text.return_value = ""
    attrs.get_value.return_value = ""
    attrs.legacy_properties.return_value = []
    attrs.element_info = SimpleNamespace(rich_text="rich", value="value", name="Edit1")
    attrs.is_visible.side_effect = RuntimeError("stale")
    attrs.is_enabled.return_value = True
    attrs.rectangle.return_value = SimpleNamespace(
        left=1, top=2, right=11, bottom=22, width=lambda: 10, height=lambda: 20
    )
    component = DelphiComponent(attrs, name="Edit1")

    assert component.text() == "rich"
    attrs.element_info.rich_text = ""
    assert component.text() == "value"
    attrs.element_info.value = ""
    assert component.text() == ""
    assert component.value() == ""
    assert component.is_visible() is False
    assert component.is_enabled() is True
    assert component.bounding_box() == {
        "left": 1,
        "top": 2,
        "right": 11,
        "bottom": 22,
        "width": 10,
        "height": 20,
    }

    attrs.legacy_properties.side_effect = RuntimeError("no MSAA")
    attrs.get_toggle_state.side_effect = RuntimeError("no TogglePattern")
    assert component.is_checked() is False

    actions = Mock()
    actions.right_click_input.side_effect = RuntimeError("unsupported")
    action_component = DelphiComponent(actions, name="Menu")
    assert action_component.right_click() is action_component
    actions.click_input.assert_called_once_with(button="right")
    actions.toggle.side_effect = RuntimeError("unsupported")
    actions.invoke.side_effect = RuntimeError("unsupported")
    assert action_component.toggle() is action_component
    actions.click.assert_called_once_with()


def test_component_edit_select_and_collection_fallbacks() -> None:
    setter = Mock()
    component = DelphiComponent(setter, name="Edit")
    assert component.set_text("first") is component
    setter.set_edit_text.assert_called_once_with("first")
    setter.set_edit_text.side_effect = RuntimeError("unsupported")
    assert component.set_text("second") is component
    setter.set_text.assert_called_once_with("second")

    state = {"checked": False}
    checkbox = DelphiComponent(Mock(), name="Enabled")
    checkbox.is_checked = lambda: state["checked"]  # type: ignore[method-assign]
    checkbox.toggle = lambda: state.__setitem__("checked", not state["checked"]) or checkbox  # type: ignore[method-assign]
    assert checkbox.check() is checkbox and state["checked"]
    assert checkbox.check() is checkbox and state["checked"]
    assert checkbox.uncheck() is checkbox and not state["checked"]
    assert checkbox.uncheck() is checkbox and not state["checked"]

    native = Mock()
    native.element_info = SimpleNamespace(control_type="List")
    assert DelphiComponent(native, name="List").select("one") is not None
    native.select.assert_called_once_with("one")
    fallback_native = Mock()
    fallback_native.element_info = SimpleNamespace(control_type="List")
    fallback_native.select.side_effect = RuntimeError("unsupported")
    assert DelphiComponent(fallback_native, name="List").select("one") is not None
    fallback_native.select_item.assert_called_once_with("one")

    combo = Mock()
    combo.element_info = SimpleNamespace(control_type="ComboBox")
    combo.type_keys.side_effect = RuntimeError("lost focus")
    assert DelphiComponent(combo, name="Combo").select("A+B") is not None
    combo.select.assert_called_once_with("A+B")

    lines = Mock()
    lines.line_count.side_effect = RuntimeError("not an edit")
    lines.texts.return_value = ["only caption"]
    lines.window_text.return_value = "one\ntwo"
    assert DelphiComponent(lines, name="Memo").lines() == ["one", "two"]

    item = _Element(name="", control_type="ListItem")
    count = Mock()
    count.item_count.side_effect = RuntimeError("unsupported")
    count.item_texts.side_effect = RuntimeError("unsupported")
    count.children.return_value = [item]
    assert DelphiComponent(count, name="List").item_count() == 1

    direct_items = Mock()
    direct_items.item_texts.return_value = ["one", "two"]
    assert DelphiComponent(direct_items, name="List").items() == ["one", "two"]
    broken_items = Mock()
    broken_items.item_texts.return_value = []
    broken_items.descendants.side_effect = RuntimeError("gone")
    broken_items.line_count.return_value = 0
    broken_items.texts.return_value = ["caption", "fallback"]
    assert DelphiComponent(broken_items, name="List").items() == ["fallback"]

    grid = Mock()
    grid.get_item.return_value.text.return_value = None
    grid.grid_item.return_value.text.return_value = None
    grid.line_count.return_value = 0
    grid.texts.return_value = ["caption", "row one"]
    assert DelphiComponent(grid, name="Grid").cell(row=1, col=1) == "row one"
    assert DelphiComponent(grid, name="Grid").cell(row=3, col=1) == ""


def test_component_keystroke_and_list_view_fallbacks() -> None:
    keys = Mock()
    keys.set_edit_text.side_effect = RuntimeError("unsupported")
    keys.set_text.side_effect = RuntimeError("unsupported")
    component = DelphiComponent(keys, name="Edit")
    assert component.set_text("A+B") is component
    keys.type_keys.assert_has_calls(
        [
            call("^a{DELETE}", with_spaces=True),
            call("A{+}B", with_spaces=True, with_tabs=True, with_newlines=True),
        ]
    )
    keys.reset_mock()
    keys.set_edit_text.side_effect = None
    assert component.type_text("replace") is component
    keys.set_edit_text.assert_called_once_with("replace")
    keys.reset_mock()
    assert component.type_text("append+", clear=False) is component
    keys.type_keys.assert_called_once_with(
        "append{+}", with_spaces=True, with_tabs=True, with_newlines=True
    )

    row = _Element(name="Alice", control_type="DataItem")
    row._children = [_Element(name="Admin", control_type="Text")]
    list_view = Mock()
    list_view.item_texts.return_value = []
    list_view.descendants.return_value = [row]
    assert DelphiComponent(list_view, name="Users").items() == ["Alice Admin"]

    cell = Mock()
    cell.get_item.return_value.text.return_value = ""
    assert DelphiComponent(cell, name="Grid").cell(row=1, col=1) == ""
    failed_cell = DelphiComponent(Mock(), name="Grid")
    failed_cell._w.get_item.side_effect = RuntimeError("unsupported")
    failed_cell._w.grid_item.side_effect = RuntimeError("unsupported")
    failed_cell.lines = lambda: ["fallback"]  # type: ignore[method-assign]
    assert failed_cell.cell(row=1, col=1) == "fallback"


def test_component_waits_validate_match_and_timeout(monkeypatch) -> None:
    component = DelphiComponent(Mock(), name="Status")
    component.text = lambda: "READY"  # type: ignore[method-assign]
    assert component.wait_for_text("EAD", poll_interval=0) is component
    assert component.wait_for_text("READY", contains=False, poll_interval=0) is component
    assert component.wait_for_text(text_re="REA.+", poll_interval=0) is component
    with pytest.raises(ValueError, match="exactly one"):
        component.wait_for_text()
    with pytest.raises(ValueError, match="always matches"):
        component.wait_for_text("")
    with pytest.raises(WaitTimeoutError, match="last seen"):
        component.wait_for_text("missing", timeout=0)

    component.is_checked = lambda: False  # type: ignore[method-assign]
    assert component.wait_for_checked(checked=False, poll_interval=0) is component
    with pytest.raises(WaitTimeoutError, match="checked"):
        component.wait_for_checked(checked=True, timeout=0)


def test_component_remaining_actions_and_wait_retry_paths(monkeypatch) -> None:
    assert _uia_control_type(None) is None
    assert _class_aliases(None) == ()
    with pytest.raises(DelphiError, match="RuntimeError: no action"):
        _first_action_that_works(
            [lambda: (_ for _ in ()).throw(RuntimeError("no action"))],
            operation="invoke",
            target="button",
            hint="retry",
        )

    component = DelphiComponent(Mock(), name="Control", cls="TEdit")
    assert component.name == "Control"
    assert component.cls == "TEdit"
    assert repr(component) == "DelphiComponent(name='Control', cls='TEdit')"
    component._w.legacy_properties.return_value = {"State": 0x10}
    assert component.is_checked() is True
    component._w.legacy_properties.return_value = {"State": "focused, checked"}
    assert component.is_checked() is True
    assert component.double_click() is component
    component._w.toggle.reset_mock()
    assert component.toggle() is component
    component._w.toggle.assert_called_once_with()
    assert component.press_key("^s") is component
    component._w.type_keys.assert_called_once_with("^s", with_spaces=True)

    lines = Mock()
    lines.line_count.return_value = 2
    lines.get_line.side_effect = ["one", "two"]
    assert DelphiComponent(lines, name="Memo").lines() == ["one", "two"]

    combo = Mock()
    combo.element_info = SimpleNamespace(control_type="ComboBox")
    assert DelphiComponent(combo, name="Combo").select(2) is not None
    assert [entry.args for entry in combo.type_keys.call_args_list] == [
        ("{HOME}",),
        ("{DOWN}",),
        ("{DOWN}",),
        ("{ENTER}",),
    ]

    component.text = Mock(side_effect=RuntimeError("transient"))  # type: ignore[method-assign]
    monkeypatch.setattr("dolphin_desktop._delphi.time.monotonic", Mock(side_effect=[0, 0.1, 1]))
    monkeypatch.setattr("dolphin_desktop._delphi.time.sleep", Mock())
    with pytest.raises(WaitTimeoutError, match="last seen: ''"):
        component.wait_for_text("expected", timeout=0.5, poll_interval=0)

    component.is_checked = Mock(side_effect=RuntimeError("transient"))  # type: ignore[method-assign]
    monkeypatch.setattr("dolphin_desktop._delphi.time.monotonic", Mock(side_effect=[0, 0.1, 1]))
    with pytest.raises(WaitTimeoutError, match="unchecked"):
        component.wait_for_checked(checked=False, timeout=0.5, poll_interval=0)


def test_form_resolves_each_selector_and_reports_lookup_errors() -> None:
    label = _Element(name="Name:", class_name="TLabel", control_type="Text", rect=(0, 10, 50, 30))
    edit = _Element(
        class_name="TEdit", control_type="Edit", automation_id="EdtName", rect=(60, 10, 180, 30)
    )
    button = _Element(
        class_name="TButton", control_type="Button", text="Save", rect=(0, 50, 80, 75)
    )
    window = Mock()
    window.children.return_value = [label, edit, button]
    form = DelphiForm(Mock(), window, name="Main")

    assert form.component(near_label=" Name: ", cls="TEdit").pywinauto is edit
    assert form.component(index=0, cls="TButton").pywinauto is button
    assert form.component(title_re="^Sav", cls="TButton").pywinauto is button

    found = Mock()
    found.exists.return_value = True
    window.child_window.side_effect = [RuntimeError("bad provider"), found]
    assert form.component(name="Save", cls="TButton").pywinauto is found
    assert window.child_window.call_args_list[1].kwargs == {"auto_id": "Save"}

    with pytest.raises(DelphiError, match="requires at least one"):
        form.component()
    with pytest.raises(ElementNotFoundError, match=r"Missing.*class 'TEdit'"):
        form.component(name="Missing", cls="TEdit", timeout=0)


def test_form_helpers_cover_recursion_class_label_and_readiness() -> None:
    roleless = _Element(name="Field", class_name="", control_type="Pane")
    actual_label = _Element(name="Field", class_name="Static", control_type="Text")
    wrong_side = _Element(class_name="TEdit", control_type="Edit", rect=(-50, 10, -10, 30))
    right_side = _Element(class_name="TEdit", control_type="Edit", rect=(60, 10, 100, 30))
    no_rect = _Element(class_name="TEdit", control_type="Edit")
    no_rect.rectangle = Mock(side_effect=RuntimeError("gone"))  # type: ignore[method-assign]
    window = Mock()
    window.children.return_value = [roleless, actual_label, wrong_side, no_rect, right_side]
    window.window_text.side_effect = RuntimeError("gone")
    form = DelphiForm(Mock(), window)
    assert form.pywinauto is window
    assert form.title() == ""
    assert form._resolve_label("Field:", [roleless, actual_label]) is actual_label
    assert form._resolve_label("Missing", [roleless]) is None
    assert form._find_near_label(near_label="Field", control_type="Edit", aliases=()) is right_side
    assert form._find_near_label(near_label="Missing", control_type="Edit", aliases=()) is None
    actual_label.rectangle = Mock(side_effect=RuntimeError("gone"))  # type: ignore[method-assign]
    form._walk_cache = [actual_label]
    assert form._find_near_label(near_label="Field", control_type="Edit", aliases=()) is None
    assert form._matches_class(Mock(spec=[]), "Edit", ()) is False
    assert form._matches_class(_Element(control_type="Edit"), "Edit", ()) is True
    assert form._matches_class(_Element(), None, ()) is True
    assert form._find_by_index(index=-1, control_type="Edit", aliases=()) is None
    assert form._find_by_title_re(title_re="x", control_type=None, aliases=("TButton",)) is None
    assert form._build_criteria_variants(
        name=None, title="Save", control_type="Button", aliases=("TButton",)
    ) == [
        {"title": "Save", "control_type": "Button"},
        {"title": "Save"},
        {"title": "Save", "class_name": "TButton"},
    ]

    ready = Mock()
    ready.exists.return_value = True
    ready.is_visible.return_value = True
    assert DelphiForm(Mock(), ready, name="Ready").wait_ready() is not None
    with pytest.raises(WaitTimeoutError, match="Never"):
        DelphiForm(Mock(), Mock(), name="Never").wait_ready(timeout=0)


def test_form_wait_and_label_edge_cases(monkeypatch) -> None:
    class _UnstableInfo:
        def __init__(self) -> None:
            self._reads = 0

        @property
        def element_info(self):
            self._reads += 1
            if self._reads == 1:
                return SimpleNamespace(name="Name", control_type="Text", class_name="TLabel")
            raise RuntimeError("stale")

    broken_after_match = _UnstableInfo()
    label = _Element(name="Name", control_type="Text", class_name="TLabel", rect=(10, 10, 30, 30))
    above = _Element(class_name="TEdit", control_type="Edit", rect=(10, -30, 30, 0))
    below = _Element(class_name="TEdit", control_type="Edit", rect=(10, 40, 30, 60))
    window = Mock()
    window.children.return_value = [broken_after_match, label, above, below]
    form = DelphiForm(Mock(), window)
    assert form._resolve_label("Name", [broken_after_match]) is None
    assert form._find_near_label(near_label="Name", control_type="Edit", aliases=()) is below

    waiting = Mock()
    waiting.exists.return_value = False
    form = DelphiForm(Mock(), waiting, name="Slow")
    monkeypatch.setattr("dolphin_desktop._delphi.time.monotonic", Mock(side_effect=[0, 0.1, 1]))
    monkeypatch.setattr("dolphin_desktop._delphi.time.sleep", Mock())
    with pytest.raises(WaitTimeoutError, match="Slow"):
        form.wait_ready(timeout=0.5)


def test_form_retries_enumerates_components_and_obeys_walk_depth(monkeypatch) -> None:
    too_deep = _Element(class_name="TButton", control_type="Button", text="too deep")
    node: _Element = too_deep
    for _ in range(8):
        node = _Element(children=[node])
    broken = Mock(spec=[])
    edit = _Element(class_name="Edit", control_type="Edit", text="Caption")
    window = Mock()
    window.children.return_value = [broken, edit, node]
    form = DelphiForm(Mock(), window, name="Main")
    assert too_deep not in form._walk_children()
    assert [item.name for item in form.components(cls="TEdit")] == ["Caption"]
    assert "Caption" in [item.name for item in form.components()]
    form._walk_cache = None
    window.child_window.side_effect = RuntimeError("unstable tree")
    monkeypatch.setattr("dolphin_desktop._delphi.time.monotonic", Mock(side_effect=[0, 0.1, 1]))
    monkeypatch.setattr("dolphin_desktop._delphi.time.sleep", Mock())
    with pytest.raises(ElementNotFoundError, match="Retry") as exc_info:
        form.component(name="Retry", timeout=0.5)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_app_facade_backend_forms_context_and_factories(monkeypatch) -> None:
    backend = Mock()
    monkeypatch.setattr("dolphin_desktop._backend.resolve", lambda name: backend)
    application = Mock()
    application.process_id = 17
    window = Mock()
    application._app.window.return_value = window
    app = DelphiApp(application, title_re="Default")
    assert app.backend() is backend
    assert app.backend_supports("click") is backend.supports.return_value
    app.require_capability("click")
    backend.require_capability.assert_called_once_with("click")
    assert app.form(title_re="Other").name == ""
    assert application._app.window.call_args.kwargs == {"title_re": "Other"}
    app.form()
    assert application._app.window.call_args.kwargs == {"title_re": "Default"}
    window.wait.side_effect = RuntimeError("not visible")
    with pytest.raises(ElementNotFoundError, match="criteria"):
        app.form(title="Broken")
    window.wait.side_effect = None
    assert app.form(name="Main").name == "Main"
    assert repr(app) == "DelphiApp(pid=17)"

    application._app.windows.return_value = [
        Mock(spec=[]),
        _Element(class_name="", automation_id="Anonymous"),
        _Element(class_name="#32770", automation_id="Dialog"),
        _Element(class_name="Unrelated", automation_id="Skip"),
    ]
    assert [form.name for form in app.forms()] == ["Anonymous", "Dialog"]
    assert app.__enter__() is app
    app.__exit__(None, None, None)
    application.kill.assert_called_once_with()

    desktop = Mock()
    launched = _launch_delphi(
        desktop, "sample.exe", timeout=3, startup_delay=0.2, work_dir="work", title_re="Main"
    )
    assert isinstance(launched, DelphiApp)
    assert launched.application is desktop._launch_raw.return_value
    desktop._launch_raw.assert_called_once_with(
        "sample.exe", backend="uia", timeout=3, work_dir="work", startup_delay=0.2
    )
    desktop._build_attach_criteria.return_value = {"process": 42}
    attached = _attach_delphi(
        desktop, title="Main", title_re="M.*", process=42, path="sample.exe", timeout=4
    )
    assert attached.application is desktop._connect_raw.return_value
    desktop._connect_raw.assert_called_once_with(backend="uia", timeout=4, process=42)


def test_delphi_component_helpers_handle_safe_actions_and_unknown_classes() -> None:
    from dolphin_desktop._delphi import _class_aliases, _first_action_that_works

    assert _class_aliases(None) == ()
    calls: list[str] = []
    _first_action_that_works(
        [lambda: calls.append("clicked")], operation="click", target="Save", hint="retry"
    )
    assert calls == ["clicked"]


def test_delphi_class_mapping_and_fallback_helpers() -> None:
    assert _uia_control_type("TButton") == "Button"
    assert _uia_control_type("TUnknown") is None
    assert _class_aliases("TComboBox") == ("TComboBox", "LCLComboBox", "ComboBox")
    assert _class_aliases("TCustom") == ("TCustom",)
    assert _first_that_works([lambda: None, lambda: "value"]) == "value"
    assert (
        _first_that_works([lambda: (_ for _ in ()).throw(RuntimeError())], default="fallback")
        == "fallback"
    )
    assert _safe(lambda: 42) == 42
    assert _safe(lambda: (_ for _ in ()).throw(RuntimeError()), default="fallback") == "fallback"


def test_delphi_action_fallback_reports_all_failed_strategies() -> None:
    calls: list[str] = []

    def fail() -> None:
        calls.append("fail")
        raise RuntimeError("not available")

    def succeed() -> None:
        calls.append("succeed")

    _first_action_that_works([fail, succeed], operation="click", target="Save", hint="retry")
    assert calls == ["fail", "succeed"]

    with pytest.raises(DelphiError, match="every strategy raised"):
        _first_action_that_works([fail], operation="click", target="Save", hint="retry")


def test_delphi_component_uses_text_state_and_action_fallbacks() -> None:
    from dolphin_desktop._delphi import DelphiComponent

    wrapper = Mock()
    wrapper.window_text.return_value = ""
    wrapper.get_value.side_effect = RuntimeError("no value pattern")
    wrapper.legacy_properties.return_value = {"Value": "legacy", "State": "checked"}
    component = DelphiComponent(wrapper, name="Edit1", cls="TEdit")
    assert component.text() == "legacy"
    assert component.value() == "legacy"
    assert component.is_checked() is True
    assert component.name == "Edit1"
    assert component.cls == "TEdit"
    assert repr(component) == "DelphiComponent(name='Edit1', cls='TEdit')"

    wrapper.invoke.side_effect = RuntimeError("no invoke")
    wrapper.click.side_effect = RuntimeError("no click")
    assert component.click() is component
    wrapper.click_input.assert_called_once_with()

    fallback = Mock()
    fallback.double_click_input.side_effect = RuntimeError("no double")
    assert DelphiComponent(fallback, name="Button").double_click() is not None
    fallback.click_input.assert_called_once_with(double=True)


@pytest.mark.parametrize(
    ("state", "toggle_state", "expected"),
    [
        (0, None, False),
        (16, None, True),
        ("CHECKED,focused", None, True),
        (None, 1, True),
        (None, 2, False),
    ],
)
def test_delphi_component_checked_state_variants(state, toggle_state, expected) -> None:
    from dolphin_desktop._delphi import DelphiComponent

    wrapper = Mock()
    wrapper.legacy_properties.return_value = {} if state is None else {"State": state}
    if state is None:
        wrapper.get_toggle_state.return_value = toggle_state
    assert DelphiComponent(wrapper, name="CheckBox").is_checked() is expected


def test_delphi_component_set_text_and_collection_fallbacks() -> None:
    from dolphin_desktop._delphi import DelphiComponent

    wrapper = Mock()
    wrapper.set_edit_text.side_effect = RuntimeError("unsupported")
    wrapper.set_text.side_effect = RuntimeError("unsupported")
    component = DelphiComponent(wrapper, name="Edit")
    assert component.set_text("50%") is component
    assert wrapper.type_keys.call_args_list == [
        call("^a{DELETE}", with_spaces=True),
        call("50{%}", with_spaces=True, with_tabs=True, with_newlines=True),
    ]

    lines = Mock()
    lines.line_count.return_value = 2
    lines.get_line.side_effect = ["first", "second"]
    assert DelphiComponent(lines, name="Memo").lines() == ["first", "second"]

    list_wrapper = Mock()
    list_wrapper.item_count.side_effect = RuntimeError()
    list_wrapper.item_texts.return_value = ["one", "two"]
    list_wrapper.children.return_value = []
    assert DelphiComponent(list_wrapper, name="List").item_count() == 2


def test_delphi_component_fallbacks_cover_text_selection_and_collection_helpers() -> None:
    from dolphin_desktop._delphi import DelphiComponent

    wrapper = Mock()
    wrapper.window_text.side_effect = RuntimeError("no WM_GETTEXT")
    wrapper.get_value.side_effect = RuntimeError("no value pattern")
    wrapper.legacy_properties.return_value = {"Value": "legacy value", "State": "Checked"}
    wrapper.element_info = SimpleNamespace(control_type="Edit", rich_text="", value="")
    component = DelphiComponent(wrapper, name="Memo", cls="TMemo")

    assert component.text() == "legacy value"
    assert component.value() == "legacy value"
    assert component.is_checked() is True
    assert component.pywinauto is wrapper

    combo = Mock()
    combo.element_info = SimpleNamespace(control_type="ComboBox")
    DelphiComponent(combo, name="Choice").select(2)
    assert [call.args for call in combo.type_keys.call_args_list] == [
        ("{HOME}",),
        ("{DOWN}",),
        ("{DOWN}",),
        ("{ENTER}",),
    ]

    memo = Mock()
    memo.line_count.return_value = 0
    memo.texts.return_value = ["Memo", "first", "second"]
    assert DelphiComponent(memo, name="Memo").lines() == ["first", "second"]

    row = SimpleNamespace(
        element_info=SimpleNamespace(control_type="DataItem", name="Alice"),
        children=lambda: [
            SimpleNamespace(element_info=SimpleNamespace(control_type="Text", name="Admin"))
        ],
    )
    rows = Mock()
    rows.item_texts.return_value = []
    rows.descendants.return_value = [row]
    assert DelphiComponent(rows, name="Users").items() == ["Alice Admin"]

    grid = Mock()
    grid.get_item.side_effect = RuntimeError("not a list view")
    cell = Mock()
    cell.text.return_value = "B2"
    grid.grid_item.return_value = cell
    assert DelphiComponent(grid, name="Grid").cell(row=2, col=2) == "B2"


def test_delphi_form_helpers_and_app_facade_cover_lookup_and_enumeration() -> None:
    from dolphin_desktop._delphi import DelphiApp, DelphiForm

    label = SimpleNamespace(
        element_info=SimpleNamespace(
            name="User:", class_name="TLabel", control_type="Text", automation_id=""
        ),
        rectangle=lambda: SimpleNamespace(left=10, top=10, right=60, bottom=30),
        window_text=lambda: "User:",
    )
    edit = SimpleNamespace(
        element_info=SimpleNamespace(
            name="", class_name="TEdit", control_type="Edit", automation_id="EdtUser"
        ),
        rectangle=lambda: SimpleNamespace(left=70, top=10, right=200, bottom=30),
        window_text=lambda: "",
    )
    other = SimpleNamespace(
        element_info=SimpleNamespace(
            name="Save", class_name="TButton", control_type="Button", automation_id="BtnSave"
        ),
        rectangle=lambda: SimpleNamespace(left=10, top=60, right=80, bottom=85),
        window_text=lambda: "Save",
    )
    window = Mock()
    window.children.return_value = [label, edit, other]
    form = DelphiForm(Mock(), window, name="MainForm")
    assert form._walk_children() == [label, edit, other]
    assert form._walk_children() is form._walk_children()
    assert form._matches_class(edit, "Edit", ("TEdit", "Edit"))
    assert form._find_by_index(index=0, control_type="Edit", aliases=()) is edit
    assert form._find_by_title_re(title_re="Sav", control_type="Button", aliases=()) is other
    assert form._find_near_label(near_label="User:", control_type="Edit", aliases=()) is edit
    assert form._build_criteria_variants(
        name="EdtUser", title=None, control_type="Edit", aliases=("TEdit", "Edit")
    ) == [
        {"auto_id": "EdtUser", "control_type": "Edit"},
        {"auto_id": "EdtUser"},
        {"title": "EdtUser", "control_type": "Edit"},
        {"title": "EdtUser"},
        {"title": "EdtUser", "class_name": "TEdit"},
        {"title": "EdtUser", "class_name": "Edit"},
    ]

    form._walk_cache = [label, edit, other]
    assert [c.name for c in form.components(cls="TEdit")] == ["EdtUser"]
    assert [c.name for c in form.components()] == ["User:", "EdtUser", "BtnSave"]

    app = Mock()
    app.process_id = 321
    app._app.window.return_value = window
    facade = DelphiApp(app, title_re=".*Main.*")
    assert facade.application is app
    assert facade.form(name="MainForm").name == "MainForm"
    assert app._app.window.call_args.kwargs == {"auto_id": "MainForm"}
    assert facade.form(title="Main").name == ""
    assert app._app.window.call_args.kwargs == {"title": "Main"}

    app._app.windows.return_value = [
        SimpleNamespace(element_info=SimpleNamespace(class_name="TForm", automation_id="Main")),
        SimpleNamespace(element_info=SimpleNamespace(class_name="Window", automation_id="Dialog")),
        SimpleNamespace(element_info=SimpleNamespace(class_name="Unrelated", automation_id="No")),
    ]
    assert [f.name for f in facade.forms()] == ["Main", "Dialog"]
    facade.close()
    app.kill.assert_called_once_with()
    assert repr(facade) == "DelphiApp(pid=321)"
