"""Tests for Window locator factories and platform seams."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image

import dolphin_desktop._window as window_module
from dolphin_desktop._element import (
    Button,
    CheckBox,
    ComboBox,
    Edit,
    ListBox,
    Menu,
    RadioButton,
    Tab,
    Toolbar,
    Tree,
)
from dolphin_desktop._exceptions import WaitTimeoutError
from dolphin_desktop._window import Window, _parse_xpath


def _window(monkeypatch: pytest.MonkeyPatch, spec: Mock | None = None) -> Window:
    """Return a UIA-style window without probing the host desktop."""
    monkeypatch.setattr(Window, "_is_java_window", lambda self: False)
    return Window(spec or Mock())


def test_parse_xpath_supports_wildcards_and_mapped_attributes() -> None:
    root = Mock()

    locator = _parse_xpath(
        root,
        "/Window[@Name=\"Main\"]/*[@ClassName='QPushButton']/*",
    )

    assert locator._criteria == {}
    assert locator._parent._criteria == {"class_name": "QPushButton"}
    assert locator._parent._parent._criteria == {"control_type": "Window", "title": "Main"}
    assert locator._parent._parent._parent is root


def test_xpath_without_a_segment_is_rejected() -> None:
    with pytest.raises(ValueError, match="No valid XPath segments"):
        _parse_xpath(Mock(), "Window[@Name='Main']")


def test_window_locator_factories_build_expected_criteria(monkeypatch: pytest.MonkeyPatch) -> None:
    window = _window(monkeypatch)

    assert window.locator(title="Main")._criteria == {"title": "Main"}
    assert window.get_by_title("Save", control_type="Button")._criteria == {
        "title": "Save",
        "control_type": "Button",
    }
    assert window.get_by_title("Save")._criteria == {"title": "Save"}
    assert window.get_by_role("Button", name="Save")._criteria == {
        "control_type": "Button",
        "title": "Save",
    }
    assert window.get_by_role("Button")._criteria == {"control_type": "Button"}
    assert window.get_by_text("Ready")._criteria == {"title": "Ready"}
    assert window.get_by_automation_id("save")._criteria == {"auto_id": "save"}
    assert window.get_by_class("QPushButton")._criteria == {"class_name": "QPushButton"}


@pytest.mark.parametrize(
    ("method", "expected_type", "expected_control_type"),
    [
        ("button", Button, "Button"),
        ("edit", Edit, "Edit"),
        ("combo_box", ComboBox, "ComboBox"),
        ("check_box", CheckBox, "CheckBox"),
        ("radio_button", RadioButton, "RadioButton"),
        ("menu", Menu, "MenuItem"),
        ("tree", Tree, "Tree"),
        ("list_box", ListBox, "List"),
        ("tab", Tab, "Tab"),
        ("toolbar", Toolbar, "ToolBar"),
    ],
)
def test_specialized_factories_use_their_control_types(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    expected_type: type,
    expected_control_type: str,
) -> None:
    window = _window(monkeypatch)
    locator = getattr(window, method)("Named", auto_id="stable")

    assert isinstance(locator, expected_type)
    assert locator._criteria == {
        "control_type": expected_control_type,
        "auto_id": "stable",
        "title": "Named",
    }


def test_specialized_factories_do_not_add_empty_names(monkeypatch: pytest.MonkeyPatch) -> None:
    window = _window(monkeypatch)

    assert window.button()._criteria == {"control_type": "Button"}
    assert window.edit()._criteria == {"control_type": "Edit"}
    assert window.menu()._criteria == {"control_type": "MenuItem"}
    assert window.list_box()._criteria == {"control_type": "List"}


def test_window_element_resolves_flat_and_child_repository_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dolphin_desktop import objects

    repository = Mock()
    repository.resolve.return_value = SimpleNamespace(selector={"title": "Save"}, fallback=[])
    repository.resolve_child.return_value = SimpleNamespace(
        selector={"auto_id": "save"}, fallback=[{"title": "Backup"}]
    )
    monkeypatch.setattr(objects, "_repository", repository)
    window = _window(monkeypatch)

    flat = window.element("save")
    window._alias = "main_window"
    child = window.element("save_button")

    assert flat._criteria == {"title": "Save"}
    assert flat._fallback == []
    assert child._criteria == {"auto_id": "save"}
    assert child._fallback == [{"title": "Backup"}]
    repository.resolve.assert_called_once_with("save")
    repository.resolve_child.assert_called_once_with("main_window", "save_button")


def test_window_image_scopes_match_and_handles_missing_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dolphin_desktop._image as image_module

    captured: list[tuple[object, dict[str, object]]] = []

    class FakeImageLocator:
        def __init__(self, template: object, **kwargs: object) -> None:
            captured.append((template, kwargs))

    monkeypatch.setattr(image_module, "ImageLocator", FakeImageLocator)
    spec = Mock()
    spec.rectangle.return_value = SimpleNamespace(left=1, top=2, right=11, bottom=22)
    window = _window(monkeypatch, spec)

    assert isinstance(
        window.image("save.png", confidence=0.91, scales=[0.8, 1.0]), FakeImageLocator
    )
    assert captured == [
        (
            "save.png",
            {"threshold": 0.91, "scales": [0.8, 1.0], "region": (1, 2, 11, 22)},
        )
    ]

    broken = Mock()
    broken.rectangle.side_effect = RuntimeError("window disappeared")
    _window(monkeypatch, broken).image("save.png")
    assert captured[-1][1]["region"] is None


def test_find_by_xpath_is_the_window_facing_xpath_entry_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(monkeypatch)

    locator = window.find_by_xpath("//Button[@Name='Save']")

    assert locator._criteria == {"control_type": "Button", "title": "Save"}
    assert locator._parent is window


def test_java_factories_forward_only_access_bridge_criteria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Window, "_is_java_window", lambda self: True)
    monkeypatch.setattr(Window, "_java_hwnd", lambda self: 4321)
    calls: list[tuple[int, dict[str, object]]] = []

    class FakeJABLocator:
        def __init__(self, hwnd: int, **criteria: object) -> None:
            calls.append((hwnd, criteria))

    monkeypatch.setattr("dolphin_desktop._java.JABLocator", FakeJABLocator)
    window = Window(Mock())

    window.locator(control_type="Button", title="Save", title_re="Save.*")
    window.get_by_title("Save", control_type="Button")
    window.get_by_role("Button", name="Save")
    window.get_by_text("Ready")

    assert calls == [
        (
            4321,
            {"control_type": "Button", "title": "Save", "title_re": "Save.*"},
        ),
        (4321, {"control_type": "Button", "title": "Save"}),
        (4321, {"control_type": "Button", "title": "Save"}),
        (4321, {"title": "Ready"}),
    ]


def test_java_locator_rejects_criteria_that_would_be_silently_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Window, "_is_java_window", lambda self: True)
    monkeypatch.setattr(Window, "_java_hwnd", lambda self: 4321)
    window = Window(Mock())

    with pytest.raises(ValueError, match="auto_id"):
        window.locator(auto_id="save")
    with pytest.raises(ValueError, match="class_name"):
        window.locator(class_name="QPushButton")

    for method in ("get_by_automation_id", "get_by_object_name", "get_by_class"):
        with pytest.raises(ValueError, match="Java Swing window"):
            getattr(window, method)("value")


def test_java_window_detection_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = Mock()
    spec.wrapper_object.return_value.handle = 99
    win32gui = SimpleNamespace(GetClassName=Mock(return_value="SunAwtFrame"))
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    window = Window(spec)

    assert window._is_java_window() is True
    assert window._is_java_window() is True
    win32gui.GetClassName.assert_called_once_with(99)


def test_failed_java_probe_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = Mock()
    spec.wrapper_object.return_value.handle = 99
    win32gui = SimpleNamespace(GetClassName=Mock(side_effect=RuntimeError("not ready")))
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    window = Window(spec)

    assert window._is_java_window() is False
    assert window._is_java is None
    assert win32gui.GetClassName.call_count == 1


def test_window_move_resize_and_basic_queries_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = Mock()
    spec.handle = 77
    spec.rectangle.return_value = SimpleNamespace(left=10, top=20, right=110, bottom=220)
    spec.window_text.return_value = "Main"
    spec.exists.return_value = 1
    spec.is_visible.return_value = 1
    spec.is_active.return_value = 0
    win32gui = SimpleNamespace(MoveWindow=Mock())
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    window = Window(spec)

    window.move(30, 40)
    window.resize(640, 480)

    assert win32gui.MoveWindow.call_args_list == [
        ((77, 30, 40, 100, 200, True), {}),
        ((77, 10, 20, 640, 480, True), {}),
    ]
    assert (window.title(), window.exists(), window.is_visible(), window.is_active()) == (
        "Main",
        True,
        True,
        False,
    )
    assert repr(window) == "Window()"


def test_window_actions_screenshot_and_waits_use_the_spec() -> None:
    spec = Mock()
    image = Mock()
    spec.capture_as_image.return_value = image
    window = Window(spec)

    window.close()
    window.maximize()
    window.minimize()
    window.restore()
    window.focus()
    assert window.screenshot() is image
    assert window.wait_until_ready(1.5) is window
    window.wait_for_close(2.5)

    spec.close.assert_called_once_with()
    spec.maximize.assert_called_once_with()
    spec.minimize.assert_called_once_with()
    spec.restore.assert_called_once_with()
    spec.set_focus.assert_called_once_with()
    spec.wait.assert_called_once_with("ready", timeout=1.5)
    spec.wait_not.assert_called_once_with("visible", timeout=2.5)

    spec.wait_not.side_effect = RuntimeError("still visible")
    with pytest.raises(WaitTimeoutError) as exc_info:
        window.wait_for_close(0)
    assert "did not close" in str(exc_info.value)


def test_window_screenshot_saves_to_the_requested_path(tmp_path) -> None:
    spec = Mock()
    image = Image.new("RGB", (3, 2), (10, 20, 30))
    spec.capture_as_image.return_value = image
    target = tmp_path / "nested" / "window.png"

    result = Window(spec).screenshot(target)

    assert result is image
    spec.capture_as_image.assert_called_once_with()
    assert target.exists()
    assert Image.open(target).getpixel((0, 0)) == (10, 20, 30)


def test_window_wait_until_ready_wraps_spec_failures() -> None:
    spec = Mock()
    spec.wait.side_effect = RuntimeError("busy")

    with pytest.raises(WaitTimeoutError) as exc_info:
        Window(spec).wait_until_ready(0)
    assert "not ready" in str(exc_info.value)


def test_window_qt_helpers_require_and_forward_the_application() -> None:
    app = Mock()
    window = Window(Mock(), application=app)

    assert window.qml("root") is app.qml.return_value
    assert window.qt_widget(object_name="editor", class_name="QLineEdit", text="Name") is (
        app.qt_widget.return_value
    )
    assert window.graphics_view(object_name="scene") is app.graphics_view.return_value
    app.qml.assert_called_once_with("root")
    app.qt_widget.assert_called_once_with(object_name="editor", class_name="QLineEdit", text="Name")
    app.graphics_view.assert_called_once_with(object_name="scene")

    with pytest.raises(RuntimeError, match="back-pointer"):
        Window(Mock()).qt_widget()


def test_window_xpath_builds_locator_chain_with_mapped_attributes() -> None:
    from dolphin_desktop._window import _parse_xpath

    root = Mock()
    locator = _parse_xpath(root, "//Window[@Name='Demo']/Button[@AutomationId='save']")
    assert locator._criteria == {"control_type": "Button", "auto_id": "save"}
    assert locator._parent._criteria == {"control_type": "Window", "title": "Demo"}


def test_window_xpath_rejects_an_empty_expression() -> None:
    from dolphin_desktop._window import _parse_xpath

    with pytest.raises(ValueError):
        _parse_xpath(Mock(), "")


@pytest.mark.parametrize(
    "xpath",
    [
        "//Button[contains(@Name, 'Save')]",
        "//Button[@HelpText='Save document']",
        "//Button[@RuntimeId='42']",
        "//Button[@Unknown='value']",
        "//Button[@name='Save']",
        "//Button[1]",
        "//Button[@Name!='Save']",
        "//Button[@Name='Save'",
        "//Button[@Name='Save\"]",
        "//Button[@Name='Save']]",
        "//Button[@Name='Save']trailing",
        "prefix//Button",
        "///Button",
        "//Button//",
    ],
)
def test_parse_xpath_rejects_unsupported_or_malformed_syntax_without_partial_locator(
    monkeypatch: pytest.MonkeyPatch,
    xpath: str,
) -> None:
    locator_constructor = Mock()
    monkeypatch.setattr(window_module, "Locator", locator_constructor)

    with pytest.raises(ValueError):
        _parse_xpath(Mock(), xpath)

    locator_constructor.assert_not_called()


def test_parse_xpath_preserves_empty_quoted_values_and_outer_whitespace() -> None:
    locator = _parse_xpath(Mock(), "  //Button[@Name='']  ")

    assert locator._criteria == {"control_type": "Button", "title": ""}
