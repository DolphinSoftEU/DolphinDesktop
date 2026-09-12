"""Tests for :mod:`dolphin_desktop._application`.

The application wrapper is normally exercised through real Windows programs.
These tests keep the same public behaviour testable on CI by modelling the
small pywinauto/Win32 surfaces the wrapper consumes.
"""

from __future__ import annotations

import ctypes
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest


def _bare_application(application_module, *, pid: int = 100, owns: bool = False):
    """Build an Application without touching the process registries."""
    raw = Mock(process=pid)
    app = application_module.Application.__new__(application_module.Application)
    app._app = raw
    app._backend = "uia"
    app.default_timeout_ms = 1000
    app._desktop = None
    app._owns_process = owns
    app._launched_apps = []
    app._qt_info = None
    app._qt_agent = None
    app._image_path = None
    return app, raw


def test_list_modules_covers_open_failure_plain_scan_and_cleanup_failures(monkeypatch) -> None:
    import dolphin_desktop._application as application

    api = SimpleNamespace(
        OpenProcess=Mock(side_effect=OSError("denied")),
        CloseHandle=Mock(),
    )
    process = SimpleNamespace(EnumProcessModules=Mock(), GetModuleFileNameEx=Mock())
    monkeypatch.setitem(sys.modules, "win32api", api)
    monkeypatch.setitem(sys.modules, "win32process", process)
    assert application._list_modules(7) == []
    process.EnumProcessModules.assert_not_called()

    api.OpenProcess = Mock(return_value=42)
    api.CloseHandle = Mock(side_effect=OSError("already gone"))
    process.EnumProcessModules = Mock(return_value=("module",))
    process.GetModuleFileNameEx = Mock(return_value=r"C:\Qt5Core.DLL")
    assert application._list_modules(7) == [r"c:\qt5core.dll"]
    process.EnumProcessModules.assert_called_once_with(42)

    process.EnumProcessModulesEx = Mock(return_value=("extended",))
    process.GetModuleFileNameEx = Mock(return_value=r"C:\Qt6Core.DLL")
    assert application._list_modules(7, use_extended=True) == [r"c:\qt6core.dll"]
    process.EnumProcessModulesEx.assert_called_once_with(42, application._ENUM_MODULES_ALL)

    process.EnumProcessModulesEx = Mock(side_effect=RuntimeError("unsupported"))
    process.EnumProcessModules = Mock(return_value=("fallback",))
    process.GetModuleFileNameEx = Mock(return_value=r"C:\fallback.dll")
    assert application._list_modules(7, use_extended=True) == [r"c:\fallback.dll"]

    process.GetModuleFileNameEx = Mock(side_effect=RuntimeError("module unloaded"))
    assert application._list_modules(7) == []

    process.EnumProcessModules.side_effect = RuntimeError("snapshot failed")
    assert application._list_modules(7) == []


def test_list_modules_returns_empty_when_win32_imports_are_unavailable(monkeypatch) -> None:
    import dolphin_desktop._application as application

    monkeypatch.setitem(sys.modules, "win32api", None)
    monkeypatch.setitem(sys.modules, "win32process", None)
    assert application._list_modules(1) == []


@pytest.mark.parametrize("result", ["C:\\Program Files\\Demo.EXE", ""])
def test_process_image_path_reads_and_normalizes_kernel_result(monkeypatch, result: str) -> None:
    import dolphin_desktop._application as application

    k32 = SimpleNamespace(
        OpenProcess=Mock(return_value=77),
        QueryFullProcessImageNameW=Mock(),
        CloseHandle=Mock(),
    )

    def query(_handle, _flags, buffer, _size):
        buffer.value = result
        return True

    k32.QueryFullProcessImageNameW.side_effect = query
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: k32)
    expected = result.lower() or None
    assert application._process_image_path(12) == expected
    k32.OpenProcess.assert_called_once_with(
        application._PROCESS_QUERY_LIMITED_INFORMATION, False, 12
    )
    k32.CloseHandle.assert_called_once_with(77)


def test_process_image_path_covers_all_failure_points(monkeypatch) -> None:
    import dolphin_desktop._application as application

    k32 = SimpleNamespace(
        OpenProcess=Mock(return_value=77),
        QueryFullProcessImageNameW=Mock(return_value=False),
        CloseHandle=Mock(side_effect=OSError("close failed")),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: k32)
    assert application._process_image_path(12) is None

    k32.OpenProcess = Mock(return_value=0)
    assert application._process_image_path(12) is None

    k32.OpenProcess = Mock(return_value=77)
    k32.QueryFullProcessImageNameW = Mock(side_effect=OSError("query failed"))
    assert application._process_image_path(12) is None

    k32.OpenProcess = Mock(side_effect=OSError("open failed"))
    assert application._process_image_path(12) is None

    monkeypatch.setattr(ctypes, "WinDLL", Mock(side_effect=OSError("kernel32 missing")))
    assert application._process_image_path(12) is None


def test_window_class_probe_handles_callback_and_outer_errors(monkeypatch) -> None:
    import dolphin_desktop._application as application

    gui = SimpleNamespace(
        IsWindowVisible=lambda _hwnd: True,
        GetClassName=lambda hwnd: "Chrome_WidgetWin_1" if hwnd == 2 else "Other",
    )
    process = SimpleNamespace(
        GetWindowThreadProcessId=Mock(side_effect=[OSError("gone"), (0, 42), (0, 7)])
    )

    def enumerate_windows(callback, extra):
        for hwnd in (1, 2, 3):
            callback(hwnd, extra)

    gui.EnumWindows = enumerate_windows
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32process", process)
    assert application._electron_by_window_class(42) is True
    assert application._electron_by_window_class(9) is False

    gui.IsWindowVisible = lambda hwnd: hwnd != 9
    gui.EnumWindows = lambda callback, extra: callback(9, extra)
    assert application._electron_by_window_class(42) is False

    gui.EnumWindows = Mock(side_effect=RuntimeError("enumeration failed"))
    assert application._electron_by_window_class(42) is False


def test_windows_includes_visible_owned_dialogs_missing_from_backend(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=42)
    main = Mock(handle=101)
    extra = Mock(handle=102)
    dialog = Mock(handle=103)
    raw.windows.return_value = [main, extra]
    raw.window.return_value = dialog

    gui = SimpleNamespace(IsWindowVisible=lambda hwnd: hwnd != 104)
    process = SimpleNamespace(
        GetWindowThreadProcessId=Mock(side_effect=[(0, 42), (0, 42), (0, 42), (0, 999)])
    )

    def enumerate_windows(callback, extra_data):
        for hwnd in (101, 102, 103, 104):
            callback(hwnd, extra_data)

    gui.EnumWindows = enumerate_windows
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32process", process)

    windows = app.windows()

    assert [window._spec for window in windows] == [main, extra, dialog]
    raw.window.assert_called_once_with(handle=103)


def test_visible_window_handles_tolerates_missing_apis_callback_errors_and_enumeration_errors(
    monkeypatch,
) -> None:
    import dolphin_desktop._application as application

    monkeypatch.setitem(sys.modules, "win32gui", None)
    monkeypatch.setitem(sys.modules, "win32process", None)
    assert application._visible_window_handles(42) == []

    gui = SimpleNamespace(IsWindowVisible=lambda hwnd: hwnd != 1)
    process = SimpleNamespace(
        GetWindowThreadProcessId=Mock(side_effect=[RuntimeError("window disappeared"), (0, 42)])
    )

    def enumerate_windows(callback, extra_data):
        for hwnd in (1, 2, 3):
            callback(hwnd, extra_data)

    gui.EnumWindows = enumerate_windows
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    monkeypatch.setitem(sys.modules, "win32process", process)
    assert application._visible_window_handles(42) == [3]

    gui.EnumWindows = Mock(side_effect=RuntimeError("enumeration failed"))
    assert application._visible_window_handles(42) == []


def test_last_active_popup_handles_missing_api_and_popup_probe_errors(monkeypatch) -> None:
    import dolphin_desktop._application as application

    monkeypatch.setattr(application, "_visible_window_handles", lambda _pid: [101])
    monkeypatch.setitem(sys.modules, "win32gui", None)
    monkeypatch.setattr(
        ctypes,
        "WinDLL",
        Mock(side_effect=OSError("user32 unavailable")),
        raising=False,
    )
    assert application._last_active_popup_handle(42) is None

    gui = SimpleNamespace(GetLastActivePopup=Mock(side_effect=RuntimeError("window disappeared")))
    monkeypatch.setitem(sys.modules, "win32gui", gui)
    assert application._last_active_popup_handle(42) is None


def test_last_active_popup_uses_user32_when_pywin32_api_is_missing(monkeypatch) -> None:
    import dolphin_desktop._application as application

    get_last_active_popup = Mock(return_value=103)
    user32 = SimpleNamespace(GetLastActivePopup=get_last_active_popup)
    monkeypatch.setattr(application, "_visible_window_handles", lambda _pid: [101, 103])
    monkeypatch.setitem(sys.modules, "win32gui", SimpleNamespace())
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=user32), raising=False)

    assert application._last_active_popup_handle(42) == 103
    get_last_active_popup.assert_called_once_with(101)


def test_top_window_prefers_visible_active_popup(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=42)
    main = Mock(handle=101)
    dialog = Mock(handle=103)
    raw.top_window.return_value = main
    raw.window.return_value = dialog

    monkeypatch.setattr(application, "_visible_window_handles", lambda _pid: [101, 103])
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(GetLastActivePopup=lambda hwnd: 103 if hwnd == 101 else hwnd),
    )

    assert app.top_window()._spec is dialog
    raw.window.assert_called_once_with(handle=103)
    raw.top_window.assert_not_called()


def test_top_window_falls_back_when_popup_spec_cannot_be_created(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=42)
    top = Mock(handle=101)
    raw.top_window.return_value = top
    raw.window.side_effect = RuntimeError("dialog disappeared")

    monkeypatch.setattr(application, "_visible_window_handles", lambda _pid: [101, 103])
    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(GetLastActivePopup=lambda hwnd: 103 if hwnd == 101 else hwnd),
    )

    assert app.top_window()._spec is top
    raw.top_window.assert_called_once_with()


def test_windows_tolerates_stale_backend_handle_and_popup_wrap_errors(monkeypatch) -> None:
    import dolphin_desktop._application as application

    class StaleWindow:
        @property
        def handle(self):
            raise RuntimeError("window disappeared")

    app, raw = _bare_application(application, pid=42)
    raw.windows.return_value = [StaleWindow()]
    raw.window.side_effect = RuntimeError("dialog disappeared")
    monkeypatch.setattr(application, "_visible_window_handles", lambda _pid: [103])

    assert len(app.windows()) == 1
    raw.window.assert_called_once_with(handle=103)


def test_module_detection_empty_and_negative_cases(monkeypatch) -> None:
    import dolphin_desktop._application as application

    monkeypatch.setattr(application, "_list_modules", lambda *_args, **_kwargs: [])
    assert application._cef_by_modules(1) is False
    assert application._qt_quick_one(1) is False
    assert application._legacy_ie_by_modules(1) is False

    monkeypatch.setattr(application, "_enumerate_child_pids", lambda _pid: [2])
    monkeypatch.setattr(application, "_qt_quick_one", lambda _pid: False)
    assert application._qt_quick_in_modules(1) is False
    monkeypatch.setattr(application, "_qt_quick_one", lambda pid: pid == 2)
    assert application._qt_quick_in_modules(1) is True
    monkeypatch.setattr(application, "_qt_quick_one", lambda pid: pid == 1)
    assert application._qt_quick_in_modules(1) is True

    modules = {1: ["webview2loader.dll"], 2: ["electron.exe"], 3: ["libcef.dll"]}
    monkeypatch.setattr(application, "_list_modules", lambda pid, **_kwargs: modules[pid])
    assert application._webview2_by_modules(1) is True
    assert application._electron_by_modules(2) is True
    assert application._cef_by_modules(3) is True
    assert application._cef_by_modules(2) is False


def test_process_parent_map_builds_snapshot_and_closes_it(monkeypatch) -> None:
    import dolphin_desktop._application as application

    rows = [(101, 10), (102, 10), (201, 20)]
    k32 = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=55),
        Process32FirstW=Mock(),
        Process32NextW=Mock(),
        CloseHandle=Mock(),
    )

    def fill(pointer, row):
        pointer._obj.th32ProcessID = row[0]
        pointer._obj.th32ParentProcessID = row[1]

    k32.Process32FirstW.side_effect = lambda _snap, pointer: fill(pointer, rows[0]) or True
    next_rows = iter(rows[1:])

    def next_row(_snap, pointer):
        try:
            row = next(next_rows)
        except StopIteration:
            return False
        fill(pointer, row)
        return True

    k32.Process32NextW.side_effect = next_row
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: k32)

    assert application._process_parent_map() == {10: [101, 102], 20: [201]}
    k32.CreateToolhelp32Snapshot.assert_called_once_with(0x2, 0)
    k32.CloseHandle.assert_called_once_with(55)


def test_process_parent_map_handles_empty_invalid_and_exceptional_snapshots(monkeypatch) -> None:
    import dolphin_desktop._application as application

    invalid = ctypes.c_void_p(-1).value
    k32 = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=invalid),
        Process32FirstW=Mock(),
        Process32NextW=Mock(),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: k32)
    assert application._process_parent_map() == {}
    k32.CreateToolhelp32Snapshot = Mock(return_value=55)
    k32.Process32FirstW = Mock(return_value=False)
    assert application._process_parent_map() == {}
    k32.CloseHandle.assert_called_once_with(55)

    k32.CreateToolhelp32Snapshot = Mock(side_effect=OSError("kernel32 unavailable"))
    assert application._process_parent_map() == {}


def test_child_and_descendant_helpers_use_parent_snapshot(monkeypatch) -> None:
    import dolphin_desktop._application as application

    monkeypatch.setattr(application, "_process_parent_map", lambda: {1: [2, 3]})
    assert application._enumerate_child_pids(1) == [2, 3]
    assert application._enumerate_child_pids(99) == []

    monkeypatch.setattr(application, "_process_parent_map", lambda: {})
    assert application._enumerate_descendant_pids(1) == set()
    monkeypatch.setattr(application, "_process_parent_map", lambda: {1: [2], 2: [3]})
    assert application._enumerate_descendant_pids(1) == {2, 3}


def test_qt_module_info_scans_versions_children_and_misses(monkeypatch) -> None:
    import dolphin_desktop._application as application

    modules = {
        1: ["qt5core.dll"],
        2: ["qt6widgets.dll"],
        3: ["user32.dll"],
    }
    monkeypatch.setattr(application, "_list_modules", lambda pid, **_kwargs: modules[pid])
    assert application._qt_module_info_one(1) == (True, "5")
    assert application._qt_module_info_one(2) == (True, "6")
    assert application._qt_module_info_one(3) == (False, None)

    monkeypatch.setattr(application, "_enumerate_child_pids", lambda _pid: [2])
    assert application._qt_module_info(3) == (True, "6")
    assert application._qt_module_info(2) == (True, "6")
    monkeypatch.setattr(application, "_enumerate_child_pids", lambda _pid: [])
    assert application._qt_module_info(3) == (False, None)


def test_application_init_validates_and_tracks_ownership(monkeypatch) -> None:
    import dolphin_desktop._application as application

    with pytest.raises(ValueError, match="default_timeout_ms"):
        application.Application(Mock(process=1), "uia", default_timeout_ms=-1)

    monkeypatch.setattr(application, "_process_image_path", lambda _pid: r"C:\Demo.EXE")
    raw = Mock(process=1234)
    owned = application.Application(raw, "uia", default_timeout_ms=250)
    _attached = application.Application(Mock(process=1235), "uia", owns_process=False)
    try:
        assert owned._image_path == r"C:\Demo.EXE"
        assert owned.default_timeout_ms == 250
        assert {1234} <= application._live_pids
        assert {1234} <= application._session_pids
        assert 1235 in application._attached_pids
    finally:
        application._live_pids.discard(1234)
        application._session_pids.discard(1234)
        application._attached_pids.discard(1235)

    explicit = application.Application(Mock(process=1236), "uia", image_path="explicit.exe")
    try:
        assert explicit._image_path == "explicit.exe"
    finally:
        application._live_pids.discard(1236)
        application._session_pids.discard(1236)


def test_application_init_survives_process_lookup_and_registry_failures(monkeypatch) -> None:
    import dolphin_desktop._application as application

    monkeypatch.setattr(application, "_process_image_path", Mock(side_effect=RuntimeError()))

    class BrokenProcess:
        @property
        def process(self):
            raise RuntimeError("process unavailable")

    owned = application.Application(BrokenProcess(), "uia")
    attached = application.Application(BrokenProcess(), "uia", owns_process=False)
    assert owned._image_path is None
    assert attached._image_path is None


def test_window_accessors_build_criteria_and_apply_repository_alias(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._window import Window
    from dolphin_desktop.objects import ObjectEntry

    app, _raw = _bare_application(application)
    finder = Mock(side_effect=lambda criteria, timeout: Window(Mock(), application=app))
    monkeypatch.setattr(app, "_find_window", finder)
    result = app.window(
        title="Main",
        title_re=".*Main.*",
        class_name="Dialog",
        auto_id="main",
        found_index=2,
        timeout=1.5,
    )
    assert isinstance(result, Window)
    assert finder.call_args.args == (
        {
            "found_index": 2,
            "title": "Main",
            "title_re": ".*Main.*",
            "class_name": "Dialog",
            "auto_id": "main",
        },
        1.5,
    )

    entry = ObjectEntry({"title": "From repository", "control_type": "Window"})
    monkeypatch.setattr("dolphin_desktop.objects._repository.resolve", Mock(return_value=entry))
    alias_result = app.window("main_window", timeout=2)
    assert alias_result._alias == "main_window"
    assert finder.call_args.args == (entry.selector, 2)


def test_window_accessor_uses_repository_window_fallback_and_records_self_healing(
    monkeypatch,
) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError
    from dolphin_desktop._window import Window
    from dolphin_desktop.objects import ObjectEntry

    app, _raw = _bare_application(application)
    monkeypatch.setattr(application.time, "monotonic", Mock(return_value=0.0))
    fallback_window = Window(Mock(), application=app)
    finder = Mock(
        side_effect=[
            WindowNotFoundError("primary window missing"),
            fallback_window,
        ]
    )
    monkeypatch.setattr(app, "_find_window", finder)
    entry = ObjectEntry(
        {"title": "Missing window"},
        fallback=[{"title": "Fallback window"}],
    )
    monkeypatch.setattr("dolphin_desktop.objects._repository.resolve", Mock(return_value=entry))

    with monkeypatch.context() as context:
        record = Mock()
        context.setattr("dolphin_desktop._selfheal.record_fallback", record)
        result = app.window("window_alias", timeout=0.25)

    assert result is fallback_window
    assert result._alias == "window_alias"
    assert finder.call_args_list == [
        call(entry.selector, 0.25),
        call(entry.fallback[0], 0.25),
    ]
    record.assert_called_once_with(entry.selector, entry.fallback[0])


def test_window_accessor_shares_timeout_budget_across_repository_fallbacks(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError
    from dolphin_desktop._window import Window
    from dolphin_desktop.objects import ObjectEntry

    app, _raw = _bare_application(application)
    fallback_window = Window(Mock(), application=app)
    finder = Mock(
        side_effect=[
            WindowNotFoundError("primary window missing"),
            WindowNotFoundError("first fallback missing"),
            fallback_window,
        ]
    )
    monkeypatch.setattr(app, "_find_window", finder)
    monkeypatch.setattr(
        application.time,
        "monotonic",
        Mock(side_effect=[0.0, 3.0, 6.0]),
    )
    entry = ObjectEntry(
        {"title": "Missing window"},
        fallback=[{"title": "First fallback"}, {"title": "Second fallback"}],
    )
    monkeypatch.setattr("dolphin_desktop.objects._repository.resolve", Mock(return_value=entry))

    with monkeypatch.context() as context:
        context.setattr("dolphin_desktop._selfheal.record_fallback", Mock())
        result = app.window("window_alias", timeout=10)

    assert result is fallback_window
    assert finder.call_args_list == [
        call(entry.selector, 10),
        call(entry.fallback[0], 7.0),
        call(entry.fallback[1], 4.0),
    ]


def test_window_accessor_reraises_primary_when_fallback_budget_is_exhausted(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError
    from dolphin_desktop.objects import ObjectEntry

    app, _raw = _bare_application(application)
    primary_error = WindowNotFoundError("primary window missing")
    finder = Mock(side_effect=primary_error)
    monkeypatch.setattr(app, "_find_window", finder)
    monkeypatch.setattr(application.time, "monotonic", Mock(side_effect=[0.0, 10.0]))
    entry = ObjectEntry(
        {"title": "Missing window"},
        fallback=[{"title": "Fallback window"}],
    )
    monkeypatch.setattr("dolphin_desktop.objects._repository.resolve", Mock(return_value=entry))

    with pytest.raises(WindowNotFoundError) as exc_info:
        app.window("window_alias", timeout=10)

    assert exc_info.value is primary_error
    finder.assert_called_once_with(entry.selector, 10)


def test_window_accessor_reraises_primary_without_alias(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError

    app, _raw = _bare_application(application)
    primary_error = WindowNotFoundError("window missing")
    finder = Mock(side_effect=primary_error)
    monkeypatch.setattr(app, "_find_window", finder)

    with pytest.raises(WindowNotFoundError) as exc_info:
        app.window(title="Main", timeout=0)

    assert exc_info.value is primary_error
    finder.assert_called_once_with({"found_index": 0, "title": "Main"}, 0)


def test_window_accessor_reraises_primary_when_all_fallbacks_miss(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError
    from dolphin_desktop.objects import ObjectEntry

    app, _raw = _bare_application(application)
    primary_error = WindowNotFoundError("primary missing")
    finder = Mock(side_effect=primary_error)
    monkeypatch.setattr(app, "_find_window", finder)
    entry = ObjectEntry(
        {"title": "Primary"},
        fallback=[{"title": "Fallback 1"}, {"title": "Fallback 2"}],
    )
    monkeypatch.setattr("dolphin_desktop.objects._repository.resolve", Mock(return_value=entry))

    with pytest.raises(WindowNotFoundError) as exc_info:
        app.window("window_alias", timeout=0)

    assert exc_info.value is primary_error
    assert finder.call_args_list == [
        call(entry.selector, 0),
        call(entry.fallback[0], 0),
        call(entry.fallback[1], 0),
    ]


def test_find_window_uses_own_process_before_desktop_fallback(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application)
    criteria = {
        "title": "Main",
        "title_re": ".*Main.*",
        "class_name": "Dialog",
        "found_index": 2,
    }
    discovered_spec = Mock()
    discovered_spec.wrapper_object.return_value.handle = 101
    bound_spec = Mock()
    raw.window.side_effect = [discovered_spec, bound_spec]
    result = app._find_window(criteria, 10)
    assert result._spec is bound_spec
    discovered_spec.wait.assert_called_once_with("visible", timeout=6.0)
    assert raw.window.call_args_list == [call(**criteria), call(handle=101)]

    raw.window.side_effect = RuntimeError("not in own process")
    desktop_discovered_spec = Mock()
    desktop_discovered_spec.wrapper_object.return_value.handle = 202
    desktop_bound_spec = Mock()
    desktop_bound_spec.wait = Mock()
    desktop = Mock()
    desktop.window.side_effect = [desktop_discovered_spec, desktop_bound_spec]
    monkeypatch.setattr(application, "_PwDesktop", Mock(return_value=desktop))
    monkeypatch.setattr(app, "_adopt_hand_off", Mock())
    fallback = app._find_window(criteria, 10)
    assert fallback._spec is desktop_bound_spec
    desktop.window.assert_has_calls([call(**criteria), call(handle=202)])
    desktop_discovered_spec.wait.assert_called_once_with("visible", timeout=4.0)
    app._adopt_hand_off.assert_called_once_with(desktop_bound_spec, criteria)


def test_find_window_reports_desktop_failure(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError

    app, raw = _bare_application(application)
    raw.window.side_effect = RuntimeError("own miss")
    desktop = Mock()
    desktop.window.side_effect = RuntimeError("desktop miss")
    monkeypatch.setattr(application, "_PwDesktop", Mock(return_value=desktop))
    with pytest.raises(WindowNotFoundError, match="searched app process") as exc_info:
        app._find_window({"title": "Missing"}, 0.25)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_adopt_hand_off_rejects_unreadable_or_unrelated_windows(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError

    app, _raw = _bare_application(application, pid=10)
    unreadable = Mock()
    unreadable.wrapper_object.side_effect = RuntimeError("owner unavailable")
    with pytest.raises(WindowNotFoundError, match="owning pid could not be read"):
        app._adopt_hand_off(unreadable, {"title": "X"})

    unrelated = Mock()
    unrelated.wrapper_object.return_value.process_id.return_value = 99
    monkeypatch.setattr(app, "_is_hand_off_pid", Mock(return_value=False))
    with pytest.raises(WindowNotFoundError, match="neither this application"):
        app._adopt_hand_off(unrelated, {"title": "X"})


def test_adopt_hand_off_reconnects_and_updates_owned_registry(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application, pid=10, owns=True)
    application._live_pids.add(10)
    application._session_pids.add(10)
    spec = Mock()
    spec.wrapper_object.return_value.process_id.return_value = 20
    new_raw = Mock()
    monkeypatch.setattr(app, "_is_hand_off_pid", Mock(return_value=True))
    monkeypatch.setattr(application, "_PyWinApp", Mock(return_value=new_raw))
    try:
        app._adopt_hand_off(spec, {"title": "X"})
        assert app._app is new_raw
        new_raw.connect.assert_called_once_with(process=20)
        assert 10 not in application._live_pids
        assert 20 in application._live_pids
        assert 10 not in application._session_pids
        assert 20 in application._session_pids
    finally:
        application._live_pids.difference_update({10, 20})
        application._session_pids.difference_update({10, 20})


def test_adopt_hand_off_reconnects_attached_and_preserves_original_on_failure(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=10, owns=False)
    application._attached_pids.add(10)
    spec = Mock()
    spec.wrapper_object.return_value.process_id.return_value = 20
    new_raw = Mock()
    new_raw.connect.side_effect = RuntimeError("access denied")
    monkeypatch.setattr(app, "_is_hand_off_pid", Mock(return_value=True))
    monkeypatch.setattr(application, "_PyWinApp", Mock(return_value=new_raw))
    try:
        app._adopt_hand_off(spec, {"title": "X"})
        assert app._app is raw
        assert application._attached_pids == {10}

        new_raw.connect.side_effect = None
        app._adopt_hand_off(spec, {"title": "X"})
        assert app._app is new_raw
        assert application._attached_pids == {20}
    finally:
        application._attached_pids.difference_update({10, 20})


def test_is_hand_off_pid_checks_same_pid_descendants_and_executable(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application, pid=10, owns=True)
    assert app._is_hand_off_pid(10) is True
    monkeypatch.setattr(application, "_enumerate_descendant_pids", lambda _pid: {20})
    assert app._is_hand_off_pid(20) is True
    assert app._is_hand_off_pid(30) is False

    app._image_path = r"c:\demo.exe"
    monkeypatch.setattr(application, "_process_image_path", lambda _pid: r"C:\DEMO.EXE")
    assert app._is_hand_off_pid(30) is False

    monkeypatch.setattr(application, "_process_image_path", lambda _pid: r"c:\demo.exe")
    monkeypatch.setattr(application, "_process_state", lambda _pid: "stopped")
    assert app._is_hand_off_pid(30) is True

    monkeypatch.setattr(application, "_process_state", lambda _pid: "running")
    assert app._is_hand_off_pid(30) is False

    monkeypatch.setattr(application, "_process_state", lambda _pid: "unknown")
    assert app._is_hand_off_pid(30) is False

    attached, _raw = _bare_application(application, pid=10, owns=False)
    attached._image_path = r"c:\demo.exe"
    monkeypatch.setattr(application, "_process_state", lambda _pid: "stopped")
    monkeypatch.setattr(application, "_process_image_path", lambda _pid: r"c:\demo.exe")
    assert attached._is_hand_off_pid(30) is False

    monkeypatch.setattr(
        application, "_enumerate_descendant_pids", Mock(side_effect=RuntimeError("snapshot"))
    )
    assert app._is_hand_off_pid(30) is False


def test_same_image_hand_off_rejects_swallowed_process_query_error(monkeypatch) -> None:
    """A pywinauto-style false on query failure must not authorize adoption."""
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=10)
    app._image_path = r"c:\demo.exe"
    monkeypatch.setattr(application, "_enumerate_descendant_pids", lambda _pid: set())
    monkeypatch.setattr(application, "_process_image_path", lambda _pid: r"c:\demo.exe")
    raw.is_process_running.return_value = False

    kernel32 = SimpleNamespace(OpenProcess=Mock(side_effect=OSError("access denied")))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)

    assert app._is_hand_off_pid(30) is False
    raw.is_process_running.assert_not_called()


def test_basic_accessors_timeouts_and_window_delegates(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=5)
    top_spec = Mock()
    raw.top_window.return_value = top_spec
    raw.windows.return_value = [Mock(), Mock()]
    assert app.top_window()._spec is top_spec
    assert len(app.windows()) == 2
    assert app.process_id == 5
    app.set_default_timeout(0)
    assert app.default_timeout_ms == 0
    with pytest.raises(ValueError, match="timeout_ms"):
        app.set_default_timeout(-1)

    app.top_window = Mock(return_value=app.top_window())
    target = app.top_window()
    monkeypatch.setattr(app, "top_window", Mock(return_value=target))
    assert app._generated_root(0) is target

    screenshot = Mock(return_value="image")
    target.screenshot = screenshot
    assert app.screenshot("shot.png") == "image"
    screenshot.assert_called_once_with("shot.png")


def test_generated_root_retries_then_raises_with_last_error(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import WindowNotFoundError

    app, _raw = _bare_application(application, pid=9)
    target = Mock()
    monkeypatch.setattr(app, "top_window", Mock(side_effect=[RuntimeError("starting"), target]))
    monkeypatch.setattr(application.time, "monotonic", Mock(side_effect=[0.0, 0.0]))
    sleeper = Mock()
    monkeypatch.setattr(application.time, "sleep", sleeper)
    assert app._generated_root(1.0) is target
    sleeper.assert_called_once_with(0.1)

    monkeypatch.setattr(app, "top_window", Mock(side_effect=RuntimeError("still missing")))
    monkeypatch.setattr(application.time, "monotonic", Mock(side_effect=[0.0, 1.0]))
    with pytest.raises(WindowNotFoundError, match="No top-level window") as exc_info:
        app._generated_root(1.0)
    assert isinstance(exc_info.value.__cause__, RuntimeError)


def test_generated_criteria_rejects_invalid_values_and_supports_all_strategies() -> None:
    import dolphin_desktop._application as application

    assert application.Application._generated_criteria("control_type", "Button", None, None) == {
        "control_type": "Button"
    }
    assert application.Application._generated_criteria("class_name", "TButton", None, None) == {
        "class_name": "TButton"
    }
    with pytest.raises(ValueError, match="non-empty"):
        application.Application._generated_criteria("name", "", None, None)
    with pytest.raises(ValueError, match="non-negative"):
        application.Application._generated_criteria("name", "Save", None, -1)
    with pytest.raises(ValueError, match="Unsupported"):
        application.Application._generated_criteria("css", "Save", None, None)


def test_find_supports_xpath_and_generated_locators(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application)
    root = Mock()
    monkeypatch.setattr(app, "_generated_root", Mock(return_value=root))

    xpath_locator = Mock()
    xpath_nth = Mock()
    xpath_locator.nth.return_value = xpath_nth
    xpath_nth.timeout.return_value = xpath_nth
    root.find_by_xpath.return_value = xpath_locator
    assert app.find("XPath", "//Button", timeout_ms=250, nth=2) is xpath_nth
    root.find_by_xpath.assert_called_once_with("//Button")
    xpath_locator.nth.assert_called_once_with(2)
    xpath_nth.timeout.assert_called_once_with(0.25)
    xpath_nth._resolve.assert_called_once_with()

    normal_locator = Mock()
    normal_locator.timeout.return_value = normal_locator
    root.locator.return_value = normal_locator
    assert app.find("name", "Save", timeout_ms=500, exact=False, nth=1) is normal_locator
    root.locator.assert_called_once_with(title_re=r".*Save.*", found_index=1)
    normal_locator.timeout.assert_called_once_with(0.5)
    normal_locator._resolve.assert_called_once_with()

    with pytest.raises(ValueError, match="timeout_ms"):
        app.find("name", "Save", timeout_ms=-1)


def test_find_all_returns_resolved_locators_and_handles_errors(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application)
    root = Mock()
    parent = Mock()
    root._get_spec.return_value.wrapper_object.return_value = parent
    monkeypatch.setattr(app, "_generated_root", Mock(return_value=root))

    def wrapper_class(element):
        return ("wrapped", element)

    raw.backend = SimpleNamespace(generic_wrapper_class=wrapper_class)
    finder = Mock(return_value=["a", "b"])
    monkeypatch.setattr(application, "_find_elements", finder)

    results = app.find_all("test_id", "save", timeout_ms=250)
    assert len(results) == 2
    assert [item._element for item in results] == [("wrapped", "a"), ("wrapped", "b")]
    assert finder.call_args.kwargs == {
        "parent": parent,
        "top_level_only": False,
        "backend": "uia",
        "depth": None,
        "auto_id": "save",
    }

    from pywinauto.findwindows import ElementNotFoundError

    finder.side_effect = ElementNotFoundError()
    assert app.find_all("name", "missing") == []
    with pytest.raises(ValueError, match="xpath collections"):
        app.find_all("xpath", "//Button")
    with pytest.raises(ValueError, match="timeout_ms"):
        app.find_all("name", "Save", timeout_ms=-1)


def test_press_key_secondary_launch_and_termination(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application)
    window = Mock()
    spec = Mock()
    window._get_spec.return_value = spec
    monkeypatch.setattr(app, "top_window", Mock(return_value=window))
    monkeypatch.setattr(application, "_send_keys", Mock())
    app.press_key("^s")
    spec.set_focus.assert_called_once_with()
    application._send_keys.assert_called_once_with("^s")

    desktop = Mock()
    child = Mock()
    desktop.launch.return_value = child
    app._desktop = desktop
    assert app.launch("helper.exe") is child
    desktop.launch.assert_called_once_with("helper.exe")
    assert app._launched_apps == [("helper.exe", child)]
    app.terminate("helper.exe")
    child.kill.assert_called_once_with()
    assert app._launched_apps == []
    with pytest.raises(RuntimeError, match="No application launched"):
        app.terminate("missing.exe")

    app._desktop = None
    with pytest.raises(RuntimeError, match="not created by a Desktop"):
        app.launch("helper.exe")


def test_close_qt_agent_and_context_manager_paths(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=30, owns=True)
    healthy = Mock()
    app._qt_agent = healthy
    app._close_qt_agent()
    healthy.close.assert_called_once_with()
    assert app._qt_agent is None

    broken = Mock()
    broken.close.side_effect = RuntimeError("pipe already closed")
    app._qt_agent = broken
    app._close_qt_agent()
    assert app._qt_agent is None

    app._qt_agent = Mock()
    app._app.is_process_running.return_value = False
    entered = app.__enter__()
    assert entered is app
    monkeypatch.setattr(app, "kill", Mock())
    assert app.__exit__(None, None, None) is None
    app.kill.assert_called_once_with()
    assert raw.process == 30


def test_close_handles_children_attached_processes_and_kill_errors(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=31, owns=False)
    child = Mock()
    child.close.side_effect = RuntimeError("child close")
    app._launched_apps = [("child", child)]
    warning = Mock()
    monkeypatch.setattr(app, "_warn_not_owned", warning)
    application._attached_pids.add(31)
    try:
        app.close(timeout=2.5)
        child.close.assert_called_once_with(timeout=2.5)
        warning.assert_called_once_with("close")
        raw.kill.assert_not_called()
        assert not app._launched_apps
        assert 31 not in application._attached_pids
    finally:
        application._attached_pids.discard(31)

    app, raw = _bare_application(application, pid=32, owns=True)
    child = Mock()
    child.close.side_effect = RuntimeError("child close")
    app._launched_apps = [("child", child)]
    application._live_pids.add(32)
    application._session_pids.add(32)
    raw.kill.side_effect = RuntimeError("kill failed")
    try:
        app.close()
        assert 32 not in application._live_pids
        assert 32 not in application._session_pids
    finally:
        application._live_pids.difference_update({32})
        application._session_pids.difference_update({32})


def test_warn_not_owned_wait_idle_and_kill_lifecycle(monkeypatch, caplog) -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=40, owns=False)
    app._warn_not_owned("kill")
    assert "left pid 40 running" in caplog.text
    app.wait_for_idle(3.5)
    raw.wait_cpu_usage_lower.assert_called_once_with(threshold=0.5, timeout=3.5)

    app, raw = _bare_application(application, pid=41, owns=True)
    child = Mock()
    child.kill.side_effect = RuntimeError("child failed")
    app._launched_apps = [("child", child)]
    raw.is_process_running.return_value = False
    with pytest.raises(RuntimeError, match="child failed"):
        app.kill()
    raw.kill.assert_not_called()
    assert not app._launched_apps

    app, raw = _bare_application(application, pid=42, owns=False)
    child = Mock()
    child.kill.side_effect = RuntimeError("child failed")
    app._launched_apps = [("child", child)]
    monkeypatch.setattr(app, "_warn_not_owned", Mock())
    with pytest.raises(RuntimeError, match="child failed"):
        app.kill()
    raw.kill.assert_not_called()

    app, raw = _bare_application(application, pid=42, owns=False)
    warning = Mock()
    monkeypatch.setattr(app, "_warn_not_owned", warning)
    app.kill()
    warning.assert_called_once_with("kill")
    raw.kill.assert_not_called()


def test_close_kill_and_detach_swallow_registry_pid_lookup_failures() -> None:
    import dolphin_desktop._application as application

    class ProcessOnce:
        def __init__(self, pid: int):
            self._pid = pid
            self._reads = 0

        @property
        def process(self):
            self._reads += 1
            if self._reads == 1:
                return self._pid
            raise RuntimeError("pid is no longer readable")

    # close() has already killed the process when the second process_id read
    # reaches the registry cleanup try/except.
    raw = ProcessOnce(44)
    app = application.Application.__new__(application.Application)
    app._app = raw
    app._backend = "uia"
    app._owns_process = True
    app._launched_apps = []
    app._qt_agent = None
    app._attached_pids = set()
    app.close()

    # kill() reaches its finally block without needing process_id until the
    # registry is updated; the second discard then raises and is swallowed.
    raw = ProcessOnce(45)
    app = application.Application.__new__(application.Application)
    app._app = raw
    app._backend = "uia"
    app._owns_process = True
    app._launched_apps = []
    app._qt_agent = None
    raw.is_process_running = Mock(return_value=False)
    app.kill()

    # detach() protects the same operation when an attached process vanishes
    # while the fixture is being torn down.
    class BrokenProcess:
        @property
        def process(self):
            raise RuntimeError("gone")

    raw = BrokenProcess()
    app = _bare_application(application, pid=46)[0]
    app._app = raw
    app.detach(session=True)
    assert not app._launched_apps


def test_kill_only_kills_running_owned_process_and_cleans_registry_on_error() -> None:
    import dolphin_desktop._application as application

    app, raw = _bare_application(application, pid=43, owns=True)
    raw.is_process_running.return_value = True
    raw.kill.side_effect = RuntimeError("process disappeared")
    application._live_pids.add(43)
    application._session_pids.add(43)
    try:
        with pytest.raises(RuntimeError, match="process disappeared"):
            app.kill()
        raw.kill.assert_called_once_with(soft=False)
        assert 43 not in application._live_pids
        assert 43 not in application._session_pids
    finally:
        application._live_pids.difference_update({43})
        application._session_pids.difference_update({43})


def test_detection_delegates_and_qt_cache_retries(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application, pid=50)
    monkeypatch.setattr(application, "_webview2_by_modules", lambda pid: pid == 50)
    monkeypatch.setattr(application, "_electron_by_window_class", lambda _pid: True)
    electron_modules = Mock(return_value=False)
    monkeypatch.setattr(application, "_electron_by_modules", electron_modules)
    monkeypatch.setattr(application, "_cef_by_modules", lambda _pid: False)
    monkeypatch.setattr(application, "_legacy_ie_by_modules", lambda _pid: True)
    assert app.is_webview2() is True
    assert app.is_electron() is True
    electron_modules.assert_not_called()
    assert app.is_cef() is False
    assert app.is_legacy_ie() is True
    monkeypatch.setattr(application, "_qt_quick_in_modules", lambda pid: pid == 50)
    assert app.uses_qt_quick() is True

    info = Mock(side_effect=[(False, None), (True, "6")])
    monkeypatch.setattr(application, "_qt_module_info", info)
    monkeypatch.setattr(application.time, "sleep", Mock())
    assert app._get_qt_info() == (True, "6")
    assert info.call_count == 2
    assert app.is_qt() is True
    assert app.qt_version() == "6"
    assert info.call_count == 2

    cached = _bare_application(application, pid=51)[0]
    cached._qt_info = (False, None)
    info.reset_mock()
    assert cached._get_qt_info() == (False, None)
    info.assert_not_called()


def test_qt_info_exhausts_warmup_and_find_qt_pid_paths(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application, pid=60)
    info = Mock(return_value=(False, None))
    monkeypatch.setattr(application, "_qt_module_info", info)
    monkeypatch.setattr(application.time, "sleep", Mock())
    assert app._get_qt_info() == (False, None)
    assert info.call_count == 5

    one = Mock(side_effect=[(True, "5")])
    monkeypatch.setattr(application, "_qt_module_info_one", one)
    assert app._find_qt_pid() == 60

    one.reset_mock()
    one.side_effect = lambda pid: (pid == 61, "6" if pid == 61 else None)
    monkeypatch.setattr(application, "_enumerate_child_pids", lambda _pid: [61])
    assert app._find_qt_pid() == 61

    one.side_effect = lambda _pid: (False, None)
    monkeypatch.setattr(application.time, "sleep", Mock())
    assert app._find_qt_pid() is None
    # The successful child lookup above used two calls before the final
    # five unsuccessful two-PID rounds.
    assert one.call_count == 12


def test_qt_agent_property_covers_cache_recovery_detection_and_attach(monkeypatch) -> None:
    import dolphin_desktop._application as application
    import dolphin_desktop._qt_inject as qt_inject

    app, _raw = _bare_application(application, pid=70)
    healthy = Mock(is_broken=False)
    app._qt_agent = healthy
    assert app.qt_agent is healthy
    assert app.has_qt_agent() is True

    broken = Mock(is_broken=True)

    def recover():
        # QtAgentClient.reattach() repairs and returns the same client.
        broken.is_broken = False
        return broken

    broken.reattach.side_effect = recover
    app._qt_agent = broken
    assert app.qt_agent is broken
    assert app.has_qt_agent() is True

    app._qt_agent = Mock(is_broken=True)
    app._qt_agent.reattach.side_effect = OSError("pipe unavailable")
    app._qt_info = (False, None)
    with pytest.raises(RuntimeError, match="not a Qt app"):
        _ = app.qt_agent
    assert app._qt_agent is None

    app._qt_info = (True, "6")
    monkeypatch.setattr(app, "_find_qt_pid", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="no Qt-loading PID"):
        _ = app.qt_agent

    client = Mock()

    class FakeClient:
        @classmethod
        def attach(cls, pid, version):
            assert (pid, version) == (71, "5")
            return client

    monkeypatch.setattr(qt_inject, "QtAgentClient", FakeClient)
    app, _raw = _bare_application(application, pid=71)
    app._qt_info = (True, "5")
    monkeypatch.setattr(app, "_find_qt_pid", Mock(return_value=71))
    assert app.qt_agent is client


def test_qt_agent_attach_errors_and_reattach_failures_fall_back(monkeypatch) -> None:
    import dolphin_desktop._application as application
    import dolphin_desktop._qt_inject as qt_inject

    class FakeClient:
        @classmethod
        def attach(cls, _pid, _version):
            raise qt_inject.QtAgentInjectError("architecture mismatch")

    monkeypatch.setattr(qt_inject, "QtAgentClient", FakeClient)
    app, _raw = _bare_application(application, pid=80)
    app._qt_info = (True, "6")
    monkeypatch.setattr(app, "_find_qt_pid", Mock(return_value=80))
    with pytest.raises(RuntimeError, match="injection failed"):
        _ = app.qt_agent

    broken = Mock(is_broken=True)
    broken.reattach.side_effect = qt_inject.QtAgentInjectError("reattach failed")
    app._qt_agent = broken
    app._qt_info = (False, None)
    with pytest.raises(RuntimeError, match="not a Qt app"):
        _ = app.qt_agent


def test_qt_helpers_wrap_first_match_and_report_missing(monkeypatch) -> None:
    import dolphin_desktop._application as application
    from dolphin_desktop._exceptions import ElementNotFoundError
    from dolphin_desktop._qt_elements import GraphicsViewElement, QmlElement, WidgetElement

    app, _raw = _bare_application(application, pid=90)
    agent = Mock(is_broken=False)
    app._qt_agent = agent
    qml_meta = {"handle": "qml-1", "objectName": "login", "type": "QQuickButton"}
    agent.qml_find.return_value = [qml_meta]
    qml = app.qml("login")
    assert isinstance(qml, QmlElement)
    assert qml.handle == "qml-1"
    with pytest.raises(ElementNotFoundError, match="objectName='missing'"):
        agent.qml_find.return_value = []
        app.qml("missing")

    widget_meta = {"handle": "widget-1", "class": "QLabel", "objectName": "status"}
    agent.find.return_value = [widget_meta]
    widget = app.qt_widget(object_name="status", class_name="QLabel", text="Ready")
    assert isinstance(widget, WidgetElement)
    agent.find.assert_called_with(objectName="status", className="QLabel", text="Ready")
    agent.find.return_value = []
    with pytest.raises(ElementNotFoundError, match="no QObject matched"):
        app.qt_widget()

    view_meta = {"handle": "view-1", "class": "QGraphicsView"}
    agent.find.return_value = [view_meta]
    view = app.graphics_view(object_name="canvas")
    assert isinstance(view, GraphicsViewElement)
    agent.find.assert_called_with(className="QGraphicsView", objectName="canvas")
    agent.find.return_value = []
    with pytest.raises(ElementNotFoundError, match="no QGraphicsView"):
        app.graphics_view()


def test_reset_qt_agent_detach_and_legacy_detection(monkeypatch) -> None:
    import dolphin_desktop._application as application

    app, _raw = _bare_application(application, pid=100)
    agent = Mock()
    app._qt_agent = agent
    app.reset_qt_agent()
    agent.close.assert_called_once_with()
    assert app._qt_agent is None

    broken = Mock()
    broken.close.side_effect = RuntimeError("closed")
    app._qt_agent = broken
    app.reset_qt_agent()
    assert app._qt_agent is None
    app.reset_qt_agent()

    application._live_pids.add(100)
    application._session_pids.add(100)
    application._attached_pids.add(100)
    try:
        app.detach()
        assert 100 not in application._live_pids
        assert 100 in application._session_pids
        app.detach(session=True)
        assert 100 not in application._session_pids
        assert 100 not in application._attached_pids
    finally:
        application._live_pids.discard(100)
        application._session_pids.discard(100)
        application._attached_pids.discard(100)

    monkeypatch.setattr(application, "_legacy_ie_by_modules", lambda pid: pid == 100)
    assert app.is_legacy_ie() is True
    assert repr(app) == "Application(pid=100, backend='uia')"
