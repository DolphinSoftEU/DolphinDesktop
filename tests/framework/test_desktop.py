"""Tests for :mod:`dolphin_desktop._desktop`.

The public ``Desktop`` facade mostly delegates to platform-specific helpers.
These tests keep those seams deterministic by replacing pywinauto, process
launchers, network probes, and stack factories with mocks.
"""


from __future__ import annotations

import atexit
import os
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

import dolphin_desktop._desktop as desktop_module
from dolphin_desktop._application import (
    _cef_by_modules,
    _enumerate_descendant_pids,
    _qt_module_info,
)
from dolphin_desktop._desktop import Desktop
from dolphin_desktop._exceptions import ApplicationError, DolphinError


def test_image_path_now_returns_path_and_swallows_probe_errors(monkeypatch) -> None:
    app = SimpleNamespace(process=123)
    monkeypatch.setattr(desktop_module, "_process_image_path", lambda pid: f"{pid}.exe")
    assert desktop_module._image_path_now(app) == "123.exe"

    monkeypatch.setattr(
        desktop_module,
        "_process_image_path",
        Mock(side_effect=RuntimeError("process vanished")),
    )
    assert desktop_module._image_path_now(app) is None


def test_constructor_validation_auto_detection_and_repr(monkeypatch) -> None:
    import dolphin_desktop._runner as runner

    with pytest.raises(ValueError, match="non-negative"):
        desktop_module.Desktop(default_timeout_ms=-1)

    explicit = desktop_module.Desktop(backend="win32", hidden=False, default_timeout_ms=0)
    assert explicit._is_hidden is False
    assert repr(explicit) == "Desktop(backend='win32', hidden=False)"

    monkeypatch.setenv("DOLPHIN_HEADLESS", "1")
    monkeypatch.setattr(runner, "has_interactive_station", lambda: True)
    assert desktop_module.Desktop()._is_hidden is True

    monkeypatch.delenv("DOLPHIN_HEADLESS")
    monkeypatch.setattr(runner, "has_interactive_station", lambda: False)
    auto_hidden = desktop_module.Desktop()
    assert auto_hidden._is_hidden is True
    assert repr(auto_hidden) == "Desktop(backend='uia')"

    monkeypatch.setattr(
        runner,
        "has_interactive_station",
        Mock(side_effect=OSError("probe unavailable")),
    )
    assert desktop_module.Desktop()._is_hidden is False


def test_ensure_hidden_mode_initializes_once_and_registers_cleanup(monkeypatch) -> None:
    import dolphin_desktop._runner as runner

    get_name = Mock(return_value="WinSta0\\Default")
    create = Mock(return_value=456)
    switch = Mock()
    close = Mock()
    register = Mock()
    monkeypatch.setattr(runner, "get_current_desktop_name", get_name)
    monkeypatch.setattr(runner, "create_hidden_desktop", create)
    monkeypatch.setattr(runner, "switch_thread_to_desktop", switch)
    monkeypatch.setattr(runner, "close_desktop", close)
    monkeypatch.setattr(atexit, "register", register)

    desktop = desktop_module.Desktop(hidden=True)
    desktop._ensure_hidden_mode()
    desktop._ensure_hidden_mode()

    assert desktop._hDesk == 456
    create.assert_called_once_with()
    switch.assert_called_once_with(456)
    register.assert_called_once_with(close, 456)
    get_name.assert_called_once_with()


def test_ensure_hidden_mode_skips_setup_when_already_on_hidden_desktop(monkeypatch) -> None:
    import dolphin_desktop._runner as runner

    monkeypatch.setattr(runner, "get_current_desktop_name", lambda: runner.DESKTOP_NAME)
    create = Mock()
    register = Mock()
    monkeypatch.setattr(runner, "create_hidden_desktop", create)
    monkeypatch.setattr(atexit, "register", register)

    desktop = desktop_module.Desktop(hidden=True)
    desktop._ensure_hidden_mode()
    assert desktop._hidden_initialized is True
    create.assert_not_called()
    register.assert_not_called()


def test_ensure_hidden_mode_warns_but_continues_after_switch_failure(monkeypatch) -> None:
    import dolphin_desktop._runner as runner

    monkeypatch.setattr(runner, "get_current_desktop_name", lambda: "Default")
    monkeypatch.setattr(runner, "create_hidden_desktop", lambda: 99)
    monkeypatch.setattr(
        runner,
        "switch_thread_to_desktop",
        Mock(side_effect=OSError("windows already exist")),
    )
    register = Mock()
    monkeypatch.setattr(atexit, "register", register)

    desktop = desktop_module.Desktop(hidden=True)
    with pytest.warns(UserWarning, match="SetThreadDesktop failed"):
        desktop._ensure_hidden_mode()
    register.assert_called_once()


def test_launch_visible_success_and_application_failure(monkeypatch) -> None:
    process_app = Mock(process=321)
    pywin_app = Mock(return_value=process_app)
    application = Mock(return_value="wrapped")
    sleep = Mock()
    monkeypatch.setattr(desktop_module, "_PyWinApp", pywin_app)
    monkeypatch.setattr(desktop_module, "Application", application)
    monkeypatch.setattr(desktop_module, "_image_path_now", lambda app: "demo.exe")
    monkeypatch.setattr(desktop_module.time, "sleep", sleep)

    desktop = desktop_module.Desktop(backend="win32", hidden=False, default_timeout_ms=777)
    assert desktop.launch("demo.exe", timeout=2, work_dir="C:\\tmp", startup_delay=0.25) == (
        "wrapped"
    )
    pywin_app.assert_called_once_with(backend="win32")
    process_app.start.assert_called_once_with(
        "demo.exe", timeout=2, wait_for_idle=False, work_dir="C:\\tmp"
    )
    sleep.assert_called_once_with(0.25)
    application.assert_called_once_with(
        process_app,
        backend="win32",
        default_timeout_ms=777,
        desktop=desktop,
        image_path="demo.exe",
    )

    failing = Mock()
    failing.start.side_effect = RuntimeError("bad command")
    monkeypatch.setattr(desktop_module, "_PyWinApp", Mock(return_value=failing))
    with pytest.raises(ApplicationError, match=r"Failed to launch 'broken\.exe'"):
        desktop.launch("broken.exe", startup_delay=0)


def test_launch_and_raw_launch_use_hidden_path_or_explicit_backend(monkeypatch) -> None:
    desktop = desktop_module.Desktop(backend="win32", hidden=False)
    hidden_result = object()
    hidden = Mock(return_value=hidden_result)
    monkeypatch.setattr(desktop, "_launch_hidden", hidden)
    desktop._resolved_hidden = True

    assert desktop.launch(
        "hidden.exe", timeout=3, work_dir="wd", startup_delay=0.1
    ) is hidden_result
    hidden.assert_called_once_with(
        "hidden.exe", timeout=3, work_dir="wd", startup_delay=0.1
    )
    hidden.reset_mock()
    assert desktop._launch_raw(
        "raw-hidden.exe", backend="uia", timeout=4, work_dir="raw", startup_delay=0
    ) is hidden_result
    hidden.assert_called_once_with(
        "raw-hidden.exe", timeout=4, work_dir="raw", startup_delay=0
    )


def test_raw_launch_and_connect_success_and_errors(monkeypatch) -> None:
    process_app = Mock(process=12)
    pywin_app = Mock(return_value=process_app)
    application = Mock(return_value="raw")
    monkeypatch.setattr(desktop_module, "_PyWinApp", pywin_app)
    monkeypatch.setattr(desktop_module, "Application", application)
    monkeypatch.setattr(desktop_module, "_image_path_now", lambda app: "raw.exe")
    monkeypatch.setattr(desktop_module.time, "sleep", Mock())
    desktop = desktop_module.Desktop(hidden=False, default_timeout_ms=55)

    assert desktop._launch_raw("raw.exe", backend="uia", startup_delay=0.1) == "raw"
    process_app.start.assert_called_once_with(
        "raw.exe", timeout=10.0, wait_for_idle=False, work_dir=None
    )
    application.assert_called_with(
        process_app,
        backend="uia",
        default_timeout_ms=55,
        desktop=desktop,
        image_path="raw.exe",
    )

    failing = Mock()
    failing.start.side_effect = RuntimeError("cannot start")
    monkeypatch.setattr(desktop_module, "_PyWinApp", Mock(return_value=failing))
    with pytest.raises(ApplicationError, match=r"Failed to launch 'raw-broken\.exe'"):
        desktop._launch_raw("raw-broken.exe", backend="uia", startup_delay=0)

    attached = Mock()
    monkeypatch.setattr(desktop_module, "_PyWinApp", Mock(return_value=attached))
    application.reset_mock()
    assert desktop._connect_raw(backend="win32", timeout=3, title="Demo") == "raw"
    attached.connect.assert_called_once_with(timeout=3, title="Demo")
    application.assert_called_once_with(
        attached,
        backend="win32",
        default_timeout_ms=55,
        desktop=desktop,
        owns_process=False,
    )

    attached.connect.side_effect = RuntimeError("not found")
    with pytest.raises(ApplicationError, match="Failed to connect"):
        desktop._connect_raw(backend="win32", timeout=3, process=8)


def test_build_attach_criteria_filters_selectors_and_uses_custom_error() -> None:
    criteria = desktop_module.Desktop._build_attach_criteria(
        title="Demo",
        title_re=".*Demo.*",
        process=8,
        path="demo.exe",
        class_name="Window",
        method_name="attach_demo",
        accepts=("title", "process", "class_name"),
    )
    assert criteria == {
        "title": "Demo",
        "process": 8,
        "class_name": "Window",
    }

    with pytest.raises(ApplicationError, match="attach_demo") as exc_info:
        desktop_module.Desktop._build_attach_criteria(
            class_name="ignored-by-default",
            method_name="attach_demo",
        )
    assert "pick the identifier" in str(exc_info.value)

    with pytest.raises(DolphinError, match="attach_custom"):
        desktop_module.Desktop._build_attach_criteria(
            method_name="attach_custom",
            error_class=DolphinError,
            accepts=("title",),
        )


def test_hidden_launch_success_and_both_failure_points(monkeypatch) -> None:
    import dolphin_desktop._runner as runner

    launcher = Mock(return_value=(333, 444))
    close_handle = Mock()
    py_app = Mock()
    py_app.connect = Mock()
    application = Mock(return_value="hidden-app")
    monkeypatch.setattr(runner, "launch_cmd_on_desktop", launcher)
    monkeypatch.setattr(runner, "close_process_handle", close_handle)
    monkeypatch.setattr(desktop_module, "_process_image_path", lambda pid: "hidden.exe")
    monkeypatch.setattr(desktop_module, "_PyWinApp", Mock(return_value=py_app))
    monkeypatch.setattr(desktop_module, "Application", application)
    monkeypatch.setattr(desktop_module.time, "sleep", Mock())

    desktop = desktop_module.Desktop(hidden=True, default_timeout_ms=42)
    ensure = Mock()
    monkeypatch.setattr(desktop, "_ensure_hidden_mode", ensure)
    assert desktop._launch_hidden(
        "hidden.exe", timeout=4, work_dir="wd", startup_delay=0.2
    ) == "hidden-app"
    ensure.assert_called_once_with()
    launcher.assert_called_once_with("hidden.exe", work_dir="wd")
    close_handle.assert_called_once_with(444)
    py_app.connect.assert_called_once_with(process=333, timeout=4)
    application.assert_called_once_with(
        py_app,
        backend="uia",
        default_timeout_ms=42,
        desktop=desktop,
        image_path="hidden.exe",
    )

    launcher.side_effect = OSError("CreateProcess failed")
    with pytest.raises(ApplicationError, match="hidden desktop"):
        desktop._launch_hidden("broken.exe", timeout=1, work_dir=None, startup_delay=0)

    launcher.side_effect = None
    launcher.return_value = (555, 666)
    py_app.connect.side_effect = RuntimeError("attach failed")
    with pytest.raises(ApplicationError, match="pid=555"):
        desktop._launch_hidden("unattachable.exe", timeout=1, work_dir=None, startup_delay=0)


def test_connect_builds_all_criteria_and_handles_failures(monkeypatch) -> None:
    py_app = Mock()
    pywin_app = Mock(return_value=py_app)
    application = Mock(return_value="connected")
    monkeypatch.setattr(desktop_module, "_PyWinApp", pywin_app)
    monkeypatch.setattr(desktop_module, "Application", application)
    desktop = desktop_module.Desktop(backend="uia", hidden=False, default_timeout_ms=11)

    assert desktop.connect(
        title="Demo",
        title_re=".*Demo.*",
        class_name="Window",
        found_index=2,
        timeout=3,
    ) == "connected"
    py_app.connect.assert_called_once_with(
        timeout=3,
        title="Demo",
        title_re=".*Demo.*",
        class_name="Window",
        found_index=2,
    )
    application.assert_called_once_with(
        py_app,
        backend="uia",
        default_timeout_ms=11,
        desktop=desktop,
        owns_process=False,
    )

    py_app.reset_mock()
    desktop.connect(process=3, path="demo.exe", found_index=9)
    assert py_app.connect.call_args.kwargs == {
        "timeout": 10.0,
        "process": 3,
        "path": "demo.exe",
    }
    py_app.reset_mock()
    desktop.connect(handle=4, title="ignored-for-index")
    assert py_app.connect.call_args.kwargs == {
        "timeout": 10.0,
        "handle": 4,
        "title": "ignored-for-index",
    }

    with pytest.raises(ValueError, match="At least one"):
        desktop.connect()
    pywin_app.side_effect = RuntimeError("connection failed")
    with pytest.raises(ApplicationError, match="Failed to connect"):
        desktop.connect(title="Broken")


def test_legacy_factory_qt_launch_and_script_builders(monkeypatch) -> None:
    legacy = desktop_module.Desktop.for_legacy_apps(default_timeout_ms=123, hidden=False)
    assert legacy._backend == "win32"
    assert legacy._default_timeout_ms == 123

    launch = Mock(return_value="qt-app")
    monkeypatch.setattr(legacy, "launch", launch)
    monkeypatch.setenv("QT_ACCESSIBILITY", "old")
    monkeypatch.delenv("QT_LOGGING_RULES", raising=False)
    assert legacy.launch_qt(
        "qt.exe",
        timeout=2,
        work_dir="qt-wd",
        startup_delay=0,
        qt_env={"QT_LOGGING_RULES": "*.debug=true"},
    ) == "qt-app"
    assert launch.call_args.args == ("qt.exe",)
    assert launch.call_args.kwargs == {
        "timeout": 2,
        "work_dir": "qt-wd",
        "startup_delay": 0,
    }
    assert os.environ["QT_ACCESSIBILITY"] == "old"
    assert "QT_LOGGING_RULES" not in os.environ

    monkeypatch.setattr(
        "dolphin_desktop._helpers.python_executable",
        lambda: r"C:\Python\python.exe",
    )
    launch.reset_mock()
    assert legacy.launch_python_script("my file.py") == "qt-app"
    assert launch.call_args.args[0] == '"C:\\Python\\python.exe" "my file.py"'
    launch.reset_mock()
    assert legacy.launch_python_script("my file.py", args=["A B"]) == "qt-app"
    assert launch.call_args.args[0] == '"C:\\Python\\python.exe" "my file.py" "A B"'

    launch_qt = Mock(return_value="qt-script")
    monkeypatch.setattr(legacy, "launch_qt", launch_qt)
    assert legacy.launch_qt_python_script("qt.py", args=["--flag", "two words"]) == "qt-script"
    assert launch_qt.call_args.args[0] == (
        '"C:\\Python\\python.exe" "qt.py" "--flag" "two words"'
    )


def test_launch_qt_restores_environment_when_launch_fails(monkeypatch) -> None:
    desktop = desktop_module.Desktop(hidden=False)
    launch = Mock(side_effect=RuntimeError("spawn failed"))
    monkeypatch.setattr(desktop, "launch", launch)
    monkeypatch.delenv("QT_ACCESSIBILITY", raising=False)
    with pytest.raises(RuntimeError, match="spawn failed"):
        desktop.launch_qt("qt.exe", qt_env={"QT_ACCESSIBILITY": "custom"})
    assert "QT_ACCESSIBILITY" not in os.environ


def test_process_lookup_and_semantic_launch_wrappers(monkeypatch) -> None:
    import dolphin_desktop._helpers as helpers

    desktop = desktop_module.Desktop(hidden=False)
    monkeypatch.setattr(helpers, "find_pid_by_image_name", Mock(return_value=None))
    assert desktop.find_process_by_image("missing.exe") is None

    find_pid = Mock(return_value=808)
    monkeypatch.setattr(helpers, "find_pid_by_image_name", find_pid)
    connect = Mock(return_value="attached")
    monkeypatch.setattr(desktop, "connect", connect)
    assert desktop.find_process_by_image("demo.exe") == "attached"
    connect.assert_called_once_with(process=808, timeout=5.0)

    connect.side_effect = ApplicationError("gone")
    assert desktop.find_process_by_image("gone.exe") is None

    launch = Mock(return_value="app")
    monkeypatch.setattr(desktop, "launch", launch)
    assert desktop.launch_electron("electron.exe") == "app"
    assert launch.call_args.args[0] == "electron.exe --force-renderer-accessibility"
    desktop.launch_electron("electron.exe --force-renderer-accessibility")
    assert launch.call_args.args[0] == "electron.exe --force-renderer-accessibility"

    desktop.launch_powerbuilder("pb.exe", timeout=2)
    desktop.launch_cef("cef.exe")
    desktop.launch_webview2("webview.exe")
    assert launch.call_args_list[-3:] == [
        call("pb.exe", timeout=2),
        call("cef.exe --force-renderer-accessibility"),
        call("webview.exe"),
    ]


def test_java_launch_initializes_bridge_pumps_messages_and_returns_app(monkeypatch) -> None:
    import dolphin_desktop._java as java

    desktop = desktop_module.Desktop(hidden=False)
    app = object()
    bridge = Mock()
    session = Mock()
    monkeypatch.setattr(java.JavaAccessBridge, "ensure_enabled", bridge.ensure_enabled)
    monkeypatch.setattr(java._JABSession, "get_or_create", Mock(return_value=session))
    monkeypatch.setattr(desktop, "launch", Mock(return_value=app))
    assert desktop.launch_java("java.exe", timeout=4) is app
    bridge.ensure_enabled.assert_called_once_with()
    session.pump.assert_called_once_with(60, 0.05)


def test_cdp_launch_polling_succeeds_and_timeout_kills_process(monkeypatch) -> None:
    import dolphin_desktop._cdp as cdp

    desktop = desktop_module.Desktop(hidden=False)
    app = Mock()
    monkeypatch.setattr(desktop, "launch", Mock(return_value=app))
    response = Mock(status=200)
    response_cm = MagicMock()
    response_cm.__enter__.return_value = response
    response_cm.__exit__.return_value = None
    urlopen = Mock(return_value=response_cm)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr(desktop_module.time, "monotonic", Mock(side_effect=[0.0, 0.0, 0.0]))
    session = object()
    connect = Mock(return_value=session)
    monkeypatch.setattr(cdp.CDPSession, "connect", connect)

    result = desktop._launch_with_cdp_flag(
        "app.exe",
        port_flag="--debug=1",
        debug_port=9222,
        timeout=5,
        work_dir="wd",
        startup_delay=0.2,
        runtime_label="TestRuntime",
    )
    assert result == (app, session)
    desktop.launch.assert_called_once_with(
        "app.exe --debug=1", timeout=5, work_dir="wd", startup_delay=0.2
    )
    urlopen.assert_called_once_with("http://127.0.0.1:9222/json/version", timeout=1.0)
    connect.assert_called_once_with("http://127.0.0.1:9222", timeout=5.0)

    urlopen.side_effect = urllib.error.URLError("not live")
    app.kill.reset_mock()
    monkeypatch.setattr(desktop_module.time, "monotonic", Mock(side_effect=[0.0, 0.0, 1.0]))
    monkeypatch.setattr(desktop_module.time, "sleep", Mock())
    with pytest.raises(RuntimeError, match="TestRuntime CDP debug port 9223"):
        desktop._launch_with_cdp_flag(
            "app.exe --debug=2",
            port_flag="--debug=2",
            debug_port=9223,
            timeout=0.5,
            work_dir=None,
            startup_delay=0,
            runtime_label="TestRuntime",
        )
    app.kill.assert_called_once_with()

    app.kill.side_effect = RuntimeError("already gone")
    monkeypatch.setattr(desktop_module.time, "monotonic", Mock(side_effect=[0.0, 0.0, 1.0]))
    with pytest.raises(RuntimeError, match="TestRuntime CDP debug port 9224"):
        desktop._launch_with_cdp_flag(
            "app.exe",
            port_flag="--debug=4",
            debug_port=9224,
            timeout=0.5,
            work_dir=None,
            startup_delay=0,
            runtime_label="TestRuntime",
        )


def test_cdp_public_launchers_forward_their_runtime_specific_options(monkeypatch) -> None:
    desktop = desktop_module.Desktop(hidden=False)
    helper = Mock(return_value=("app", "session"))
    monkeypatch.setattr(desktop, "_launch_with_cdp_flag", helper)

    assert desktop.launch_electron_cdp("electron.exe", debug_port=9333, timeout=4) == (
        "app",
        "session",
    )
    assert helper.call_args == call(
        "electron.exe",
        port_flag="--remote-debugging-port=9333",
        debug_port=9333,
        timeout=4,
        work_dir=None,
        startup_delay=0.5,
        runtime_label="Electron",
    )

    assert desktop.launch_cef_cdp(
        "steam.exe -cef-enable-debugging", debug_port=8080, debug_flag="-cef-enable-debugging"
    ) == ("app", "session")
    assert helper.call_args.kwargs == {
        "port_flag": "-cef-enable-debugging",
        "debug_port": 8080,
        "timeout": 20.0,
        "work_dir": None,
        "startup_delay": 1.0,
        "runtime_label": "CEF",
    }


def test_stack_factories_delegate_all_arguments(monkeypatch) -> None:
    import dolphin_desktop._delphi as delphi
    import dolphin_desktop._mainframe as mainframe
    import dolphin_desktop._oracle_forms as oracle
    import dolphin_desktop._sap as sap

    desktop = desktop_module.Desktop(hidden=False)
    monkeypatch.setattr(delphi, "_launch_delphi", Mock(return_value="delphi"))
    monkeypatch.setattr(delphi, "_attach_delphi", Mock(return_value="delphi-attached"))
    monkeypatch.setattr(oracle, "_launch_oracle_forms", Mock(return_value="forms"))
    monkeypatch.setattr(oracle, "_attach_oracle_forms", Mock(return_value="forms-attached"))
    monkeypatch.setattr(sap.SapGui, "connect", Mock(return_value="sap"))
    build_terminal = Mock(return_value=Mock())
    monkeypatch.setattr(mainframe, "_build_terminal", build_terminal)

    assert desktop.launch_delphi(
        "demo.exe", timeout=2, work_dir="wd", startup_delay=0.3, title_re="Demo"
    ) == "delphi"
    delphi._launch_delphi.assert_called_once_with(
        desktop, "demo.exe", timeout=2, startup_delay=0.3, work_dir="wd", title_re="Demo"
    )
    assert desktop.attach_delphi(
        title="Demo", title_re=".*", process=5, path="demo.exe", timeout=3
    ) == (
        "delphi-attached"
    )
    delphi._attach_delphi.assert_called_once_with(
        desktop, title="Demo", title_re=".*", process=5, path="demo.exe", timeout=3
    )

    assert desktop.sap(timeout=6) == "sap"
    sap.SapGui.connect.assert_called_once_with(timeout=6)

    assert desktop.launch_oracle_forms(
        jar="client.jar",
        classpath="libs",
        main_class="Main",
        java_args=["-Xmx1g"],
        title_re="Forms",
        timeout=8,
        startup_delay=1,
    ) == "forms"
    oracle._launch_oracle_forms.assert_called_once_with(
        desktop,
        jnlp=None,
        jar="client.jar",
        main_class="Main",
        classpath="libs",
        java_args=["-Xmx1g"],
        title_re="Forms",
        timeout=8,
        startup_delay=1,
    )
    assert desktop.attach_oracle_forms(title_re="Forms", process=9, timeout=4) == "forms-attached"
    oracle._attach_oracle_forms.assert_called_once_with(
        desktop, title=None, title_re="Forms", process=9, timeout=4
    )


def test_mainframe_factory_connects_only_when_requested(monkeypatch) -> None:
    import dolphin_desktop._mainframe as mainframe

    desktop = desktop_module.Desktop(hidden=False)
    term = Mock()
    build = Mock(return_value=term)
    monkeypatch.setattr(mainframe, "_build_terminal", build)
    result = desktop.mainframe(
        host="example.test",
        port=992,
        session_type="5250",
        backend="hllapi",
        connect=True,
        timeout=7,
        ws3270_path="ws3270.exe",
        model="3279-2",
        codepage="german",
        session_id="B",
        hllapi_dll_path="hllapi.dll",
        extra_args=["-trace"],
        trace=True,
    )
    assert result is term
    build.assert_called_once_with(
        backend="hllapi",
        ws3270_path="ws3270.exe",
        model="3279-2",
        codepage="german",
        session_id="B",
        hllapi_dll_path="hllapi.dll",
        extra_args=["-trace"],
        trace=True,
    )
    term.connect.assert_called_once_with("example.test", 992, session_type="5250", timeout=7)

    term.connect.reset_mock()
    assert desktop.mainframe(connect=False) is term
    term.connect.assert_not_called()


def test_find_process_builds_criteria_and_returns_none_on_attach_error(monkeypatch) -> None:
    desktop = desktop_module.Desktop(hidden=False)
    connect = Mock(return_value="found")
    monkeypatch.setattr(desktop, "connect", connect)
    assert desktop.find_process(name="demo.exe", pid=7, title="Demo", title_re=".*Demo.*") == (
        "found"
    )
    connect.assert_called_once_with(
        path="demo.exe", process=7, title="Demo", title_re=".*Demo.*"
    )

    with pytest.raises(ValueError, match="At least one"):
        desktop.find_process()
    connect.side_effect = ApplicationError("gone")
    assert desktop.find_process(pid=99) is None


def test_capability_methods_delegate_to_selected_backend(monkeypatch) -> None:
    capability = object()
    backend = Mock()
    backend.supports.return_value = True
    monkeypatch.setattr("dolphin_desktop._backend.resolve", Mock(return_value=backend))
    desktop = desktop_module.Desktop(hidden=False)
    assert desktop.backend_supports(capability) is True
    assert desktop.require_capability(capability) is None
    backend.supports.assert_called_once_with(capability)
    backend.require_capability.assert_called_once_with(capability)


def test_desktop_capability_queries_delegate_to_the_selected_backend(monkeypatch) -> None:
    from dolphin_desktop._capabilities import Capability
    from dolphin_desktop._desktop import Desktop

    desktop = Desktop.__new__(Desktop)
    desktop._backend = "fake"
    backend = Mock()
    backend.supports.return_value = True
    monkeypatch.setattr("dolphin_desktop._backend.resolve", lambda _: backend)
    assert desktop.backend_supports(Capability.CLICK) is True
    desktop.require_capability(Capability.CLICK)
    backend.require_capability.assert_called_once_with(Capability.CLICK)


def test_desktop_and_application_process_helpers_handle_selection_and_cycles(monkeypatch) -> None:
    assert Desktop._build_attach_criteria(title="Calculator", method_name="attach_delphi") == {
        "title": "Calculator"
    }
    with pytest.raises(ApplicationError, match="attach_delphi requires at least one"):
        Desktop._build_attach_criteria(method_name="attach_delphi")

    monkeypatch.setattr(
        "dolphin_desktop._application._process_parent_map",
        lambda: {1: [2, 3], 2: [4], 3: [1], 4: [2]},
    )
    assert _enumerate_descendant_pids(1) == {2, 3, 4}

    def modules(pid: int, *, use_extended: bool = False) -> list[str]:
        by_pid = {
            10: ["libcef.dll"],
            11: ["libcef.dll", "electron.exe"],
            20: [],
            21: ["qt6core.dll"],
        }
        return by_pid[pid]

    monkeypatch.setattr("dolphin_desktop._application._list_modules", modules)
    monkeypatch.setattr("dolphin_desktop._application._enumerate_child_pids", lambda pid: [21])
    assert _cef_by_modules(10) is True
    assert _cef_by_modules(11) is False
    assert _qt_module_info(20) == (True, "6")


def test_desktop_launch_connect_and_qt_environment_are_isolated(monkeypatch) -> None:
    import dolphin_desktop._desktop as desktop_module

    py_app = Mock(process=123)
    pywin_app = Mock(return_value=py_app)
    application = Mock(return_value="application")
    monkeypatch.setattr(desktop_module, "_PyWinApp", pywin_app)
    monkeypatch.setattr(desktop_module, "_image_path_now", lambda app: "c:/demo.exe")
    monkeypatch.setattr(desktop_module, "Application", application)
    monkeypatch.setattr(desktop_module.time, "sleep", Mock())
    desktop = desktop_module.Desktop(backend="win32", hidden=False, default_timeout_ms=250)

    assert desktop.launch("demo.exe", startup_delay=0.1) == "application"
    pywin_app.assert_called_once_with(backend="win32")
    py_app.start.assert_called_once_with(
        "demo.exe", timeout=10.0, wait_for_idle=False, work_dir=None
    )
    application.assert_called_once_with(
        py_app,
        backend="win32",
        default_timeout_ms=250,
        desktop=desktop,
        image_path="c:/demo.exe",
    )

    pywin_app.reset_mock()
    py_app.reset_mock()
    application.reset_mock()
    assert desktop.connect(title="Demo") == "application"
    py_app.connect.assert_called_once_with(timeout=10.0, title="Demo", found_index=0)
    assert application.call_args.kwargs["owns_process"] is False

    observed: dict[str, str | None] = {}

    def fake_launch(cmd, **kwargs):
        observed.update(
            {
                "accessibility": os.environ.get("QT_ACCESSIBILITY"),
                "custom": os.environ.get("QT_CUSTOM"),
            }
        )
        return "qt-app"

    monkeypatch.setattr(desktop, "launch", fake_launch)
    monkeypatch.delenv("QT_ACCESSIBILITY", raising=False)
    assert desktop.launch_qt("qt.exe", qt_env={"QT_CUSTOM": "yes"}) == "qt-app"
    assert observed == {"accessibility": "1", "custom": "yes"}
    assert os.environ.get("QT_ACCESSIBILITY") is None
    assert os.environ.get("QT_CUSTOM") is None


def test_desktop_hidden_mode_is_lazy_and_warns_when_thread_switch_fails(monkeypatch) -> None:
    import dolphin_desktop._desktop as desktop_module
    import dolphin_desktop._runner as runner

    register = Mock()
    monkeypatch.setattr(atexit, "register", register)
    monkeypatch.setattr(runner, "DESKTOP_NAME", "DolphinHidden")
    monkeypatch.setattr(runner, "get_current_desktop_name", lambda: "WinSta0")
    monkeypatch.setattr(runner, "create_hidden_desktop", lambda: 77)
    monkeypatch.setattr(runner, "switch_thread_to_desktop", Mock(side_effect=OSError("busy")))
    monkeypatch.setattr(runner, "close_desktop", Mock())

    desktop = desktop_module.Desktop(hidden=True)
    with pytest.warns(UserWarning, match="SetThreadDesktop failed"):
        desktop._ensure_hidden_mode()
    desktop._ensure_hidden_mode()
    assert desktop._hDesk == 77
    assert register.call_count == 1

    same_desktop = desktop_module.Desktop(hidden=True)
    monkeypatch.setattr(runner, "get_current_desktop_name", lambda: "DolphinHidden")
    same_desktop._ensure_hidden_mode()
    assert same_desktop._hDesk is None
    with pytest.raises(ValueError, match="default_timeout_ms"):
        desktop_module.Desktop(default_timeout_ms=-1)


def test_desktop_connect_supports_each_identifier_and_reports_failures(monkeypatch) -> None:
    import dolphin_desktop._desktop as desktop_module

    py_app = Mock()
    pywin_app = Mock(return_value=py_app)
    application = Mock(return_value="attached")
    monkeypatch.setattr(desktop_module, "_PyWinApp", pywin_app)
    monkeypatch.setattr(desktop_module, "Application", application)
    desktop = desktop_module.Desktop(hidden=False)

    assert desktop.connect(process=10) == "attached"
    assert py_app.connect.call_args.kwargs == {"timeout": 10.0, "process": 10}
    py_app.reset_mock()
    assert desktop.connect(path="demo.exe") == "attached"
    assert py_app.connect.call_args.kwargs == {"timeout": 10.0, "path": "demo.exe"}
    py_app.reset_mock()
    assert desktop.connect(handle=20, class_name="Window") == "attached"
    assert py_app.connect.call_args.kwargs == {
        "timeout": 10.0,
        "handle": 20,
        "class_name": "Window",
    }
    with pytest.raises(ValueError, match="At least one"):
        desktop.connect()

    pywin_app.side_effect = RuntimeError("connection refused")
    with pytest.raises(desktop_module.ApplicationError, match="Failed to connect"):
        desktop.connect(title="Demo")


def test_desktop_raw_launch_hidden_launch_and_script_builders(monkeypatch) -> None:
    import dolphin_desktop._desktop as desktop_module
    import dolphin_desktop._runner as runner

    py_app = Mock(process=123)
    pywin_app = Mock(return_value=py_app)
    application = Mock(return_value="raw-app")
    monkeypatch.setattr(desktop_module, "_PyWinApp", pywin_app)
    monkeypatch.setattr(desktop_module, "Application", application)
    monkeypatch.setattr(desktop_module, "_image_path_now", lambda app: "raw.exe")
    monkeypatch.setattr(desktop_module.time, "sleep", Mock())
    desktop = desktop_module.Desktop(hidden=False)
    assert desktop._launch_raw("demo.exe", backend="uia", startup_delay=0) == "raw-app"
    pywin_app.assert_called_with(backend="uia")
    py_app.start.assert_called_once_with(
        "demo.exe", timeout=10.0, wait_for_idle=False, work_dir=None
    )

    launch_hidden = Mock(return_value=(333, 444))
    close_handle = Mock()
    monkeypatch.setattr(runner, "launch_cmd_on_desktop", launch_hidden)
    monkeypatch.setattr(runner, "close_process_handle", close_handle)
    monkeypatch.setattr(desktop_module, "_process_image_path", lambda pid: "hidden.exe")
    monkeypatch.setattr(desktop, "_ensure_hidden_mode", Mock())
    desktop._resolved_hidden = True
    pywin_app.reset_mock()
    application.reset_mock()
    assert desktop.launch("hidden.exe", startup_delay=0) == "raw-app"
    launch_hidden.assert_called_once_with("hidden.exe", work_dir=None)
    close_handle.assert_called_once_with(444)
    py_app.connect.assert_called_once_with(process=333, timeout=10.0)


def test_desktop_script_builders_delegate_with_quoted_arguments(monkeypatch) -> None:
    import dolphin_desktop._desktop as desktop_module

    desktop = desktop_module.Desktop(hidden=False)
    launch = Mock(return_value="app")
    launch_qt = Mock(return_value="qt-app")
    monkeypatch.setattr(desktop, "launch", launch)
    monkeypatch.setattr(desktop, "launch_qt", launch_qt)
    monkeypatch.setattr(
        "dolphin_desktop._helpers.python_executable",
        lambda: r"C:\Python\python.exe",
    )

    assert desktop.launch_python_script("my script.py", args=["A B"]) == "app"
    assert launch.call_args.args[0] == '"C:\\Python\\python.exe" "my script.py" "A B"'
    assert desktop.launch_qt_python_script("qt.py", args=["--flag"]) == "qt-app"
    assert launch_qt.call_args.args[0] == '"C:\\Python\\python.exe" "qt.py" "--flag"'


def test_desktop_stack_factories_and_cdp_launcher_cover_success_and_failure(monkeypatch) -> None:
    import dolphin_desktop._desktop as desktop_module

    desktop = desktop_module.Desktop(hidden=False)
    assert desktop_module.Desktop.for_legacy_apps()._backend == "win32"
    assert desktop._build_attach_criteria(
        title="Demo", class_name="Window", method_name="attach"
    ) == {"title": "Demo"}
    with pytest.raises(desktop_module.ApplicationError, match="requires"):
        desktop._build_attach_criteria(method_name="attach")

    terminal = Mock()
    monkeypatch.setattr("dolphin_desktop._mainframe._build_terminal", Mock(return_value=terminal))
    assert desktop.mainframe(backend="s3270", connect=False) is terminal
    terminal.connect.assert_not_called()
    assert desktop.mainframe(backend="s3270", connect=True, host="host") is terminal
    terminal.connect.assert_called_once()

    app = Mock()
    desktop.launch = Mock(return_value=app)
    assert desktop.launch_electron("app.exe") is app
    assert "--force-renderer-accessibility" in desktop.launch.call_args.args[0]
    desktop.launch.reset_mock()
    assert desktop.launch_cef("cef.exe --force-renderer-accessibility") is app
    assert desktop.launch.call_args.args[0] == "cef.exe --force-renderer-accessibility"
    desktop.launch.reset_mock()
    assert desktop.launch_webview2("webview.exe") is app
    assert desktop.launch.call_args.args[0] == "webview.exe"

    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    response = _Response()
    session = object()
    monkeypatch.setattr("urllib.request.urlopen", Mock(return_value=response))
    monkeypatch.setattr("dolphin_desktop._cdp.CDPSession.connect", Mock(return_value=session))
    result_app, result_session = desktop.launch_electron_cdp("electron.exe", timeout=0.2)
    assert result_app is app
    assert result_session is session
    assert "--remote-debugging-port=9222" in desktop.launch.call_args.args[0]

    monkeypatch.setattr("urllib.request.urlopen", Mock(side_effect=OSError("closed")))
    with pytest.raises(RuntimeError, match="did not become live"):
        desktop.launch_cef_cdp("cef.exe", timeout=0)
    app.kill.assert_called()
