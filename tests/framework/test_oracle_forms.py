"""Tests for the Oracle Forms facade."""

from __future__ import annotations

import ctypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import dolphin_desktop._java as java_module
import dolphin_desktop._oracle_forms as forms
from dolphin_desktop._exceptions import ElementNotFoundError, WaitTimeoutError
from dolphin_desktop._oracle_forms import (
    OracleFormsApp,
    OracleFormsBlock,
    OracleFormsItem,
    OracleFormsLov,
    OracleFormsMenu,
)


def test_item_delegates_actions_and_returns_itself() -> None:
    jab = Mock()
    jab.value.return_value = "7369"
    jab.text.return_value = "SMITH"
    jab.is_visible.return_value = True
    jab.is_enabled.return_value = False
    jab.bounding_box.return_value = {"left": 1, "top": 2}
    app = Mock()
    item = OracleFormsItem(jab, name="EMP.ENAME", app=app)

    assert item.name == "EMP.ENAME"
    assert item.value() == "7369"
    assert item.text() == "SMITH"
    assert item.type_text("JONES") is item
    assert item.type_text("JR", clear=False) is item
    assert item.set_text("KING") is item
    assert item.clear() is item
    assert item.click() is item
    assert item.focus() is item
    assert item.press_key("F8") is item
    assert item.is_visible() is True
    assert item.is_enabled() is False
    assert item.bounding_box() == {"left": 1, "top": 2}
    assert repr(item) == "OracleFormsItem(name='EMP.ENAME')"
    jab.set_text.assert_any_call("JONES")
    jab.set_text.assert_called_with("KING")
    app.bring_to_foreground.assert_called_once()
    assert jab.focus.call_count == 2
    jab.type_text.assert_called_once_with("JR")


def test_block_prefers_qualified_item_name_and_falls_back_to_plain_name() -> None:
    qualified = Mock()
    qualified.exists.return_value = False
    plain = Mock()
    plain.exists.return_value = True
    app = Mock()
    app._locator.side_effect = [qualified, plain]

    item = OracleFormsBlock(app, name="EMP").item("ENAME")

    assert item.name == "ENAME"
    assert app._locator.call_args_list[0].kwargs == {"name": "EMP.ENAME"}
    assert app._locator.call_args_list[1].kwargs == {"name": "ENAME"}


def test_block_raises_a_useful_error_when_no_item_matches() -> None:
    app = Mock()
    app._locator.side_effect = ElementNotFoundError("missing")

    with pytest.raises(ElementNotFoundError, match="item 'ENAME' not found in block 'EMP'"):
        OracleFormsBlock(app, name="EMP").item("ENAME")


@pytest.mark.parametrize(
    ("status", "expected"),
    [("Record 3 of 12", 3), ("Ready.", 0), ("record 4 of ?", 4)],
)
def test_block_extracts_current_record_from_status(status: str, expected: int) -> None:
    app = Mock()
    app.status_line.return_value = status

    assert OracleFormsBlock(app, name="EMP").current_record() == expected


def test_menu_uses_fallback_locator_when_menu_item_role_is_not_found(monkeypatch) -> None:
    first = Mock()
    first.exists.return_value = False
    fallback = Mock()
    app = Mock()
    app._locator.side_effect = [first, fallback]
    pressed: list[str] = []
    monkeypatch.setattr("dolphin_desktop._oracle_forms.Keyboard.press", pressed.append)
    monkeypatch.setattr("dolphin_desktop._oracle_forms.time.sleep", lambda _: None)

    OracleFormsMenu(app, name="Action").select(["Save"])

    assert pressed == ["%A"]
    assert app._locator.call_args_list[0].kwargs == {"name": "Save", "role": "menu item"}
    assert app._locator.call_args_list[1].kwargs == {"name": "Save"}
    fallback.click.assert_called_once()


def test_lov_reports_visibility_and_selects_or_rejects_an_entry(monkeypatch) -> None:
    locator = Mock()
    locator.exists.return_value = True
    app = Mock()
    app._locator.return_value = locator
    pressed: list[str] = []
    monkeypatch.setattr("dolphin_desktop._oracle_forms.Keyboard.press", pressed.append)
    lov = OracleFormsLov(app)

    assert lov.is_open() is True
    lov.select("SALES")
    lov.cancel()
    locator.click.assert_called_once()
    assert pressed == ["{ENTER}", "{ESC}"]

    locator.exists.return_value = False
    with pytest.raises(ElementNotFoundError, match="LOV entry 'SALES' not visible"):
        lov.select("SALES")


def test_status_line_uses_label_fallback_and_closes_all_locators() -> None:
    named = Mock()
    named.exists.return_value = False
    role = Mock()
    role.exists.return_value = False
    ignored = Mock()
    ignored.text.return_value = "Label"
    ignored.description.return_value = ""
    matching = Mock()
    matching.text.return_value = "Record 2 of 8"
    app = OracleFormsApp.__new__(OracleFormsApp)
    app._locator = Mock(side_effect=[named, role])
    app._all_locators = Mock(return_value=[ignored, matching])

    assert app.status_line() == "Record 2 of 8"
    ignored.close.assert_called_once()
    matching.close.assert_called_once()


def test_wait_for_status_rejects_empty_needle_and_times_out() -> None:
    app = OracleFormsApp.__new__(OracleFormsApp)
    with pytest.raises(ValueError, match="always matches immediately"):
        app.wait_for_status("")

    app.status_line = Mock(return_value="Waiting")
    with pytest.raises(WaitTimeoutError, match="last value: 'Waiting'"):
        app.wait_for_status("Ready", timeout=0.01, poll_interval=0)


def _empty_app() -> forms.OracleFormsApp:
    return forms.OracleFormsApp(Mock())


def test_block_and_app_accessors_expose_their_wrapped_objects() -> None:
    application = Mock()
    app = forms.OracleFormsApp(application, title_re="Forms")

    assert app.application is application
    assert app.block("EMP").name == "EMP"
    assert repr(app.block("EMP")) == "OracleFormsBlock(name='EMP')"


@pytest.mark.parametrize(
    "error",
    [ElementNotFoundError("missing"), forms.OracleFormsError("lookup failed")],
)
def test_lov_is_open_returns_false_when_lookup_fails(error: Exception) -> None:
    app = Mock()
    app._locator.side_effect = error

    assert forms.OracleFormsLov(app).is_open() is False


def test_form_window_title_returns_empty_when_the_native_window_fails() -> None:
    app = SimpleNamespace(
        _application=SimpleNamespace(window=Mock(side_effect=RuntimeError("gone")))
    )

    assert forms.OracleFormsWindow(app).title() == ""


def test_form_window_wait_ready_pumps_jab_and_releases_the_root_context(monkeypatch) -> None:
    session = Mock()
    session.get_root_context.return_value = (7, 11)
    factory = Mock(return_value=session)
    monkeypatch.setattr(java_module, "_JABSession", SimpleNamespace(get_or_create=factory))
    monkeypatch.setattr(forms.time, "monotonic", Mock(return_value=0.0))

    app = _empty_app()
    app._primary_hwnd = Mock(return_value=123)

    forms.OracleFormsWindow(app).wait_ready(timeout=1)

    factory.assert_called_once_with()
    session.pump.assert_called_once_with(10, 0.05)
    session.get_root_context.assert_called_once_with(123)
    session.release.assert_called_once_with(7, 11)


def test_form_window_wait_ready_swallows_probe_errors_then_times_out(monkeypatch) -> None:
    app = _empty_app()
    app._primary_hwnd = Mock(side_effect=RuntimeError("JAB unavailable"))
    monotonic = Mock(side_effect=[0.0, 0.0, 2.0])
    sleep = Mock()
    monkeypatch.setattr(forms.time, "monotonic", monotonic)
    monkeypatch.setattr(forms.time, "sleep", sleep)

    with pytest.raises(WaitTimeoutError, match="did not become ready within 1s"):
        forms.OracleFormsWindow(app).wait_ready(timeout=1)

    sleep.assert_called_once_with(0.25)


def test_backend_class_helpers_resolve_and_delegate_capabilities(monkeypatch) -> None:
    backend = Mock()
    backend.supports.return_value = True
    resolver = Mock(return_value=backend)
    monkeypatch.setattr("dolphin_desktop._backend.resolve", resolver)

    assert forms.OracleFormsApp.backend() is backend
    assert forms.OracleFormsApp.backend_supports("jab") is True
    forms.OracleFormsApp.require_capability("jab")

    assert resolver.call_count == 3
    backend.supports.assert_called_once_with("jab")
    backend.require_capability.assert_called_once_with("jab")


def test_function_key_handles_missing_window_by_using_foreground_fallback(monkeypatch) -> None:
    app = _empty_app()
    app._primary_hwnd = Mock(side_effect=forms.OracleFormsError("no window"))
    app.bring_to_foreground = Mock()
    press = Mock()
    monkeypatch.setattr(forms.Keyboard, "press", press)

    app.function_key("Ctrl+Q")

    app.bring_to_foreground.assert_called_once_with()
    press.assert_called_once_with("^Q")


def test_request_java_focus_requests_and_releases_the_root_context(monkeypatch) -> None:
    session = Mock()
    session.get_root_context.return_value = (4, 9)
    monkeypatch.setattr(
        java_module,
        "_JABSession",
        SimpleNamespace(get_or_create=Mock(return_value=session)),
    )

    _empty_app()._request_java_focus(88)

    session.pump.assert_called_once_with(5, 0.01)
    session.get_root_context.assert_called_once_with(88)
    session.request_focus.assert_called_once_with(4, 9)
    session.release.assert_called_once_with(4, 9)


def test_request_java_focus_ignores_missing_context_and_session_errors(monkeypatch) -> None:
    session = Mock()
    session.get_root_context.return_value = None
    monkeypatch.setattr(
        java_module,
        "_JABSession",
        SimpleNamespace(get_or_create=Mock(return_value=session)),
    )
    _empty_app()._request_java_focus(88)
    session.request_focus.assert_not_called()
    session.release.assert_not_called()

    monkeypatch.setattr(
        java_module,
        "_JABSession",
        SimpleNamespace(get_or_create=Mock(side_effect=RuntimeError("not loaded"))),
    )
    _empty_app()._request_java_focus(88)


def test_request_java_focus_releases_context_when_focus_request_raises(monkeypatch) -> None:
    session = Mock()
    session.get_root_context.return_value = (4, 9)
    session.request_focus.side_effect = RuntimeError("focus failed")
    monkeypatch.setattr(
        java_module,
        "_JABSession",
        SimpleNamespace(get_or_create=Mock(return_value=session)),
    )

    _empty_app()._request_java_focus(88)

    session.release.assert_called_once_with(4, 9)


def test_all_command_helpers_forward_their_forms_key(monkeypatch) -> None:
    app = _empty_app()
    function_key = Mock()
    app.function_key = function_key
    expected = [
        ("enter_query", forms.OracleFormsKey.ENTER_QUERY),
        ("execute_query", forms.OracleFormsKey.EXECUTE_QUERY),
        ("cancel_query", forms.OracleFormsKey.CANCEL_QUERY),
        ("save", forms.OracleFormsKey.SAVE),
        ("next_record", forms.OracleFormsKey.NEXT_RECORD),
        ("previous_record", forms.OracleFormsKey.PREVIOUS_RECORD),
        ("next_block", forms.OracleFormsKey.NEXT_BLOCK),
        ("previous_block", forms.OracleFormsKey.PREVIOUS_BLOCK),
        ("list_of_values", forms.OracleFormsKey.LIST_OF_VALUES),
    ]

    for method_name, _key in expected:
        getattr(app, method_name)()

    assert function_key.call_args_list == [call(key) for _, key in expected]


def test_status_line_uses_the_role_fallback_and_description() -> None:
    named = Mock()
    named.exists.return_value = False
    role = Mock()
    role.exists.return_value = True
    role.text.return_value = ""
    role.description.return_value = "Ready."
    app = _empty_app()
    app._locator = Mock(side_effect=[named, role])

    assert app.status_line() == "Ready."


def test_status_line_handles_missing_locators_and_reader_errors() -> None:
    named = Mock()
    named.exists.side_effect = ElementNotFoundError("gone")
    role = Mock()
    role.exists.side_effect = ElementNotFoundError("gone")
    app = _empty_app()
    app._locator = Mock(side_effect=[named, role])
    app._all_locators = Mock(side_effect=ElementNotFoundError("none"))

    assert app.status_line() == ""

    named.exists.side_effect = None
    named.exists.return_value = False
    role.exists.side_effect = None
    role.exists.return_value = False
    bad = Mock()
    bad.text.side_effect = RuntimeError("text unavailable")
    bad.description.side_effect = RuntimeError("description unavailable")
    app._locator = Mock(side_effect=[named, role])
    app._all_locators = Mock(return_value=[bad])

    assert app.status_line() == ""
    bad.close.assert_called_once_with()


def test_app_title_close_and_context_manager_delegate_to_application() -> None:
    application = Mock()
    application.window.return_value.title.return_value = "Forms"
    app = forms.OracleFormsApp(application)
    assert app.title() == "Forms"

    app.close()
    application.kill.assert_called_once_with()
    application.kill.side_effect = RuntimeError("already closed")
    app.close()

    with app as entered:
        assert entered is app
    assert application.kill.call_count == 3


def test_locator_and_all_locators_are_scoped_to_the_primary_hwnd(monkeypatch) -> None:
    locator = Mock()
    locator.all.return_value = ["first", "second"]
    locator_factory = Mock(return_value=locator)
    monkeypatch.setattr(forms, "JABLocator", locator_factory)
    app = _empty_app()
    app._primary_hwnd = Mock(return_value=42)

    assert app._locator(name="Save", role="push button", title_re="^Save$") is locator
    assert list(app._all_locators(role="label")) == ["first", "second"]
    assert locator_factory.call_args_list == [
        call(42, control_type="push button", title="Save", title_re="^Save$"),
        call(42, control_type="label", title=None, title_re=None),
    ]


def test_bring_to_foreground_attaches_threads_and_detaches_them(monkeypatch) -> None:
    user32 = Mock()
    user32.GetForegroundWindow.side_effect = [111, 42]
    user32.GetWindowThreadProcessId.return_value = 20
    user32.AttachThreadInput.return_value = 1
    user32.SetForegroundWindow.return_value = 1
    kernel32 = Mock()
    kernel32.GetCurrentThreadId.return_value = 10
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32, kernel32=kernel32))
    monkeypatch.setattr(forms, "_log", Mock())

    app = _empty_app()
    app._primary_hwnd = Mock(return_value=42)
    app.bring_to_foreground()

    user32.ShowWindow.assert_called_once_with(42, 9)
    user32.BringWindowToTop.assert_called_once_with(42)
    user32.SetFocus.assert_called_once_with(42)
    assert user32.AttachThreadInput.call_args_list == [call(10, 20, True), call(10, 20, False)]


def test_bring_to_foreground_logs_when_windows_refuses_activation(monkeypatch) -> None:
    user32 = Mock()
    user32.GetForegroundWindow.return_value = 99
    user32.GetWindowThreadProcessId.return_value = 10
    user32.SetForegroundWindow.return_value = 0
    kernel32 = Mock()
    kernel32.GetCurrentThreadId.return_value = 10
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32, kernel32=kernel32))
    logger = Mock()
    monkeypatch.setattr(forms, "_log", logger)

    app = _empty_app()
    app._primary_hwnd = Mock(return_value=42)
    app.bring_to_foreground()

    logger.warning.assert_called_once()
    user32.AttachThreadInput.assert_not_called()


def test_bring_to_foreground_logs_native_exceptions(monkeypatch) -> None:
    user32 = Mock()
    user32.GetForegroundWindow.side_effect = RuntimeError("user32 unavailable")
    kernel32 = Mock()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32, kernel32=kernel32))
    logger = Mock()
    monkeypatch.setattr(forms, "_log", logger)

    app = _empty_app()
    app._primary_hwnd = Mock(return_value=42)
    app.bring_to_foreground()

    logger.warning.assert_called_once()


def test_bring_to_foreground_logs_when_the_window_cannot_be_found(monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr(forms, "_log", logger)
    app = _empty_app()
    app._primary_hwnd = Mock(side_effect=forms.OracleFormsError("missing"))

    app.bring_to_foreground()

    logger.warning.assert_called_once()


class _FakeWindow:
    def __init__(self, class_name: str, title: str, handle: int) -> None:
        self._class_name = class_name
        self._title = title
        self.handle = handle

    def class_name(self) -> str:
        return self._class_name

    def window_text(self) -> str:
        return self._title


def test_primary_hwnd_prefers_matching_java_window() -> None:
    wins = [
        _FakeWindow("Notepad", "Other", 1),
        _FakeWindow("SunAwtFrame", "Oracle Forms", 2),
        _FakeWindow("SunAwtDialog", "Dialog", 3),
    ]
    application = SimpleNamespace(_app=SimpleNamespace(windows=Mock(return_value=wins)))
    app = forms.OracleFormsApp(application, title_re="Forms")

    assert app._primary_hwnd() == 2


def test_primary_hwnd_falls_back_to_the_first_candidate_when_title_does_not_match() -> None:
    wins = [_FakeWindow("SunAwtFrame", "Oracle Forms", 2), _FakeWindow("SunAwtDialog", "Dialog", 3)]
    application = SimpleNamespace(_app=SimpleNamespace(windows=Mock(return_value=wins)))
    app = forms.OracleFormsApp(application, title_re="Missing")

    assert app._primary_hwnd() == 2


def test_primary_hwnd_uses_non_java_windows_when_no_java_window_exists() -> None:
    wins = [
        _FakeWindow("Chrome_WidgetWin", "Other", 5),
        _FakeWindow("Chrome_WidgetWin", "Forms", 6),
    ]
    application = SimpleNamespace(_app=SimpleNamespace(windows=Mock(return_value=wins)))
    app = forms.OracleFormsApp(application, title_re="Forms")

    assert app._primary_hwnd() == 6


def test_primary_hwnd_raises_a_forms_error_when_window_enumeration_fails() -> None:
    application = SimpleNamespace(
        _app=SimpleNamespace(windows=Mock(side_effect=RuntimeError("closed")))
    )

    with pytest.raises(forms.OracleFormsError, match="no top-level window"):
        _empty_app_with_application(application)._primary_hwnd()


def _empty_app_with_application(application) -> forms.OracleFormsApp:
    return forms.OracleFormsApp(application)


def _launch_kwargs(**overrides):
    values = {
        "jnlp": None,
        "jar": None,
        "main_class": None,
        "classpath": None,
        "java_args": None,
        "title_re": "Forms",
        "timeout": 4.0,
        "startup_delay": 0.5,
    }
    values.update(overrides)
    return values


def test_launch_oracle_forms_requires_exactly_one_client_source(monkeypatch) -> None:
    ensure = Mock()
    monkeypatch.setattr(forms.JavaAccessBridge, "ensure_enabled", ensure)

    with pytest.raises(forms.OracleFormsError, match="exactly one"):
        forms._launch_oracle_forms(Mock(), **_launch_kwargs())
    with pytest.raises(forms.OracleFormsError, match="exactly one"):
        forms._launch_oracle_forms(Mock(), **_launch_kwargs(jnlp="a", jar="b"))
    ensure.assert_not_called()


def test_launch_oracle_forms_uses_javaws_for_jnlp(monkeypatch) -> None:
    desktop = Mock()
    underlying = Mock()
    desktop.launch_java.return_value = underlying
    monkeypatch.setattr(forms.JavaAccessBridge, "ensure_enabled", Mock())
    monkeypatch.setattr(forms, "_find_java", Mock(return_value="java"))
    monkeypatch.setattr(forms, "_find_javaws", Mock(return_value="javaws"))

    app = forms._launch_oracle_forms(
        desktop,
        **_launch_kwargs(jnlp="http://example/forms.jnlp"),
    )

    assert isinstance(app, forms.OracleFormsApp)
    desktop.launch_java.assert_called_once_with(
        "javaws http://example/forms.jnlp", timeout=4.0, startup_delay=0.5
    )


def test_launch_oracle_forms_falls_back_to_java_for_jnlp(monkeypatch) -> None:
    desktop = Mock()
    desktop.launch_java.return_value = Mock()
    monkeypatch.setattr(forms.JavaAccessBridge, "ensure_enabled", Mock())
    monkeypatch.setattr(forms, "_find_java", Mock(return_value="java"))
    monkeypatch.setattr(forms, "_find_javaws", Mock(return_value=None))

    forms._launch_oracle_forms(desktop, **_launch_kwargs(jnlp="forms.jnlp"))

    desktop.launch_java.assert_called_once_with("java forms.jnlp", timeout=4.0, startup_delay=0.5)


def test_launch_oracle_forms_builds_a_jar_command_with_java_args(monkeypatch) -> None:
    desktop = Mock()
    desktop.launch_java.return_value = Mock()
    monkeypatch.setattr(forms.JavaAccessBridge, "ensure_enabled", Mock())
    monkeypatch.setattr(forms, "_find_java", Mock(return_value="java"))

    forms._launch_oracle_forms(
        desktop,
        **_launch_kwargs(jar=r"C:\Forms App\client.jar", java_args=["-Xmx256m"]),
    )

    desktop.launch_java.assert_called_once_with(
        r"java -Djava.accessibility=true -Doracle.forms.accessible=true -Xmx256m "
        r'-jar "C:\Forms App\client.jar"',
        timeout=4.0,
        startup_delay=0.5,
    )


def test_launch_oracle_forms_builds_a_main_class_command_with_classpath(monkeypatch) -> None:
    desktop = Mock()
    desktop.launch_java.return_value = Mock()
    monkeypatch.setattr(forms.JavaAccessBridge, "ensure_enabled", Mock())
    monkeypatch.setattr(forms, "_find_java", Mock(return_value="java"))

    forms._launch_oracle_forms(
        desktop,
        **_launch_kwargs(
            main_class="com.example.Forms",
            classpath=r"C:\Forms Lib\forms.jar",
            java_args=None,
        ),
    )

    desktop.launch_java.assert_called_once_with(
        r"java -Djava.accessibility=true -Doracle.forms.accessible=true "
        r'-cp "C:\Forms Lib\forms.jar" com.example.Forms',
        timeout=4.0,
        startup_delay=0.5,
    )


def test_attach_oracle_forms_enables_jab_builds_criteria_and_connects(monkeypatch) -> None:
    desktop = Mock()
    desktop._build_attach_criteria.return_value = {"title": "Forms", "process": 77}
    underlying = Mock()
    desktop.connect.return_value = underlying
    ensure = Mock()
    monkeypatch.setattr(forms.JavaAccessBridge, "ensure_enabled", ensure)

    app = forms._attach_oracle_forms(
        desktop,
        title="Forms",
        title_re=None,
        process=77,
        timeout=3.0,
    )

    assert isinstance(app, forms.OracleFormsApp)
    ensure.assert_called_once_with()
    desktop._build_attach_criteria.assert_called_once_with(
        title="Forms",
        title_re=None,
        process=77,
        method_name="attach_oracle_forms",
        error_class=forms.OracleFormsError,
        accepts=("title", "title_re", "process"),
    )
    desktop.connect.assert_called_once_with(timeout=3.0, title="Forms", process=77)


def test_find_java_and_javaws_use_java_home_when_binaries_exist(
    monkeypatch, tmp_path: Path
) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "java.exe").touch()
    (bindir / "javaws.exe").touch()
    monkeypatch.setattr(forms.JavaAccessBridge, "java_home", Mock(return_value=str(tmp_path)))

    assert forms._find_java() == str(bindir / "java.exe")
    assert forms._find_javaws() == str(bindir / "javaws.exe")


def test_find_java_and_javaws_fall_back_without_java_home(monkeypatch) -> None:
    monkeypatch.setattr(forms.JavaAccessBridge, "java_home", Mock(return_value=None))

    assert forms._find_java() == "java"
    assert forms._find_javaws() is None


def _patch_post_message(monkeypatch, returns):
    user32 = Mock()
    user32.PostMessageW.side_effect = returns
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=user32))
    return user32


def test_post_key_to_window_posts_normal_and_extended_key_pairs(monkeypatch) -> None:
    user32 = _patch_post_message(monkeypatch, [1, 1])
    assert forms._post_key_to_window(55, " F7 ") is True
    assert user32.PostMessageW.call_args_list == [
        call(55, 0x0100, 0x76, 0x00000001),
        call(55, 0x0101, 0x76, 0xC0000001),
    ]

    user32 = _patch_post_message(monkeypatch, [1, 1])
    assert forms._post_key_to_window(55, "UP") is True
    assert user32.PostMessageW.call_args_list == [
        call(55, 0x0100, 0x26, 0x01000001),
        call(55, 0x0101, 0x26, 0xC1000001),
    ]


@pytest.mark.parametrize(
    ("returns", "expected"),
    [([0, 1], False), ([1, 1], True), ([1, 0, 0], False)],
)
def test_post_key_to_window_reports_postmessage_failures_and_retries_release(
    monkeypatch, returns, expected
) -> None:
    _patch_post_message(monkeypatch, returns)

    assert forms._post_key_to_window(55, "F10") is expected


def test_post_key_to_window_declines_modifiers_and_unknown_keys(monkeypatch) -> None:
    _patch_post_message(monkeypatch, [1, 1])

    assert forms._post_key_to_window(55, "Shift+F7") is False
    assert forms._post_key_to_window(55, "Mystery") is False


def test_oracle_forms_status_line_prefers_description_then_text() -> None:
    from dolphin_desktop._oracle_forms import OracleFormsApp

    locator = Mock()
    locator.exists.return_value = True
    locator.description.return_value = "Ready."
    app = OracleFormsApp.__new__(OracleFormsApp)
    app._locator = Mock(return_value=locator)
    assert app.status_line() == "Ready."
    locator.description.return_value = ""
    locator.text.return_value = "Record 1 of 1"
    assert app.status_line() == "Record 1 of 1"


def test_oracle_forms_items_blocks_menus_and_lov(monkeypatch) -> None:
    import dolphin_desktop._oracle_forms as forms

    jab = Mock()
    jab.value.return_value = "old"
    jab.text.return_value = "caption"
    jab.is_visible.return_value = True
    jab.is_enabled.return_value = True
    item = forms.OracleFormsItem(jab, name="EMP.NAME", app=Mock())
    assert item.value() == "old"
    assert item.text() == "caption"
    assert item.type_text("Alice") is item
    assert item.set_text("Bob").clear().click().focus().press_key("{TAB}") is item
    assert item.is_visible() and item.is_enabled()
    assert repr(item) == "OracleFormsItem(name='EMP.NAME')"

    app = Mock()
    app.status_line.return_value = "No record selected"
    missing = Mock()
    missing.exists.return_value = False
    found = Mock()
    found.exists.return_value = True
    app._locator.side_effect = [missing, found]
    block = forms.OracleFormsBlock(app, name="EMP")
    assert block.item("NAME").name == "NAME"
    assert app._locator.call_args_list[0].kwargs == {"name": "EMP.NAME"}
    assert block.current_record() == 0
    app.status_line.return_value = "Record 4 of ?"
    assert block.current_record() == 4

    menu_locator = Mock()
    menu_locator.exists.return_value = True
    menu = forms.OracleFormsMenu(app, name="Action")
    app._locator.side_effect = None
    app._locator.return_value = menu_locator
    monkeypatch.setattr(forms.Keyboard, "press", Mock())
    monkeypatch.setattr(forms.time, "sleep", Mock())
    menu.select(["Query", "Enter"])
    assert forms.Keyboard.press.call_args_list[0].args == ("%A",)
    assert menu_locator.click.call_count == 2

    lov = forms.OracleFormsLov(app)
    assert lov.is_open() is True
    app._locator.return_value = menu_locator
    lov.select("SALES")
    lov.cancel()
    assert forms.Keyboard.press.call_args_list[-2].args == ("{ENTER}",)
    assert forms.Keyboard.press.call_args_list[-1].args == ("{ESC}",)


def test_oracle_forms_key_translation_function_keys_and_status(monkeypatch) -> None:
    import dolphin_desktop._oracle_forms as forms

    assert forms._translate_key("F7") == "{F7}"
    assert forms._translate_key("Ctrl+Page_Down") == "^{PGDN}"
    assert forms._translate_key("Alt+Shift+F5") == "%+{F5}"

    app = forms.OracleFormsApp(Mock())
    app._primary_hwnd = Mock(return_value=100)
    app._request_java_focus = Mock()
    monkeypatch.setattr(forms, "_post_key_to_window", Mock(return_value=True))
    app.function_key("F7")
    forms._post_key_to_window.assert_called_once_with(100, "F7")
    monkeypatch.setattr(forms, "_post_key_to_window", Mock(return_value=False))
    monkeypatch.setattr(forms.Keyboard, "press", Mock())
    app.function_key("Shift+F5")
    forms.Keyboard.press.assert_called_once_with("+{F5}")

    status = Mock()
    status.exists.return_value = True
    status.description.return_value = "Record 1 of 2"
    app._locator = Mock(return_value=status)
    assert app.status_line() == "Record 1 of 2"
    assert app.wait_for_status("Record", timeout=0.1, poll_interval=0) == "Record 1 of 2"
    with pytest.raises(ValueError, match="always matches"):
        app.wait_for_status("", timeout=0.1)
    assert app.item("X")._name == "X"
    assert isinstance(app.form(), forms.OracleFormsWindow)
    assert isinstance(app.menu("File"), forms.OracleFormsMenu)
    assert isinstance(app.lov(), forms.OracleFormsLov)


def test_oracle_forms_key_and_command_helpers_cover_modifiers_and_quoting() -> None:
    from dolphin_desktop._oracle_forms import _quote, _translate_key

    assert _translate_key("F7") == "{F7}"
    assert _translate_key("Shift+F5") == "+{F5}"
    assert _translate_key("Ctrl+Page_Down") == "^{PGDN}"
    assert _translate_key("Alt+Enter") == "%{ENTER}"
    assert _quote("") == '""'
    assert _quote("plain") == "plain"
    assert _quote('two words "quoted"') == '"two words \\"quoted\\""'
