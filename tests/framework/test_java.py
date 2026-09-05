"""Tests for the Java Access Bridge adapter.

These tests deliberately model the small C API boundary instead of starting a
JVM.  Apart from making the suite runnable on CI, this lets us exercise the
failure paths which are difficult to trigger reliably with a real Swing app.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import pytest

import dolphin_desktop._java as java
from dolphin_desktop._exceptions import (
    ElementNotFoundError,
    UnsupportedPatternError,
    WaitTimeoutError,
)


def _info(
    role: str = "push button",
    name: str = "Save",
    *,
    description: str = "",
    states: str = "enabled, visible",
    children: int = 0,
    x: int = 10,
    y: int = 20,
    width: int = 30,
    height: int = 40,
) -> java._ACI:
    info = java._ACI()
    info.role_en_US = role
    info.name = name
    info.description = description
    info.states_en_US = states
    info.childrenCount = children
    info.x, info.y, info.width, info.height = x, y, width, height
    return info


def _locator(session: object, info: java._ACI | None = None) -> java.JABLocator:
    """Build a locator without invoking the real singleton session."""
    info = info or _info()
    locator = object.__new__(java.JABLocator)
    locator._hwnd = 100
    locator._control_type = "button"
    locator._title = "Save"
    locator._title_re = None
    locator._timeout = 1.0
    locator._resolved_role = "push button"
    locator._session = lambda: session  # type: ignore[method-assign]
    locator._find = Mock(return_value=(1, 2, info))
    locator._wait_find = Mock(return_value=(1, 2, info))
    locator._release = Mock()
    return locator


class _Export:
    """Callable object that behaves enough like a ctypes DLL export."""

    def __init__(self, result: object = False) -> None:
        self.result = result
        self.restype = None
        self.argtypes = None
        self.calls: list[tuple[object, ...]] = []

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        if callable(self.result):
            return self.result(*args)
        return self.result


class _MinimalWab:
    """Required bridge exports, with all optional exports absent."""

    def __init__(self) -> None:
        self.Windows_run = _Export()
        self.isJavaWindow = _Export()
        self.getAccessibleContextFromHWND = _Export()
        self.getAccessibleContextInfo = _Export()
        self.getAccessibleChildFromContext = _Export()

    def __getattr__(self, name: str) -> object:
        raise AttributeError(name)


def _session(wab: object, **flags: bool) -> java._JABSession:
    session = object.__new__(java._JABSession)
    session._wab = wab
    for name, value in flags.items():
        setattr(session, name, value)
    return session


# JavaAccessBridge discovery and enabling


def test_is_enabled_checks_configuration_and_returns_false_when_every_probe_misses(
    monkeypatch,
) -> None:
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_key(_root, subkey):
        if subkey.endswith("ATs"):
            return _Key()
        if subkey.endswith("Accessibility"):
            return _Key()
        raise OSError("missing")

    calls = {"enum": 0}

    def enum_value(_key, index):
        calls["enum"] += 1
        if index == 0:
            return ("other-tool", "", 1)
        raise OSError("end")

    def query_value(_key, name):
        assert name == "Configuration"
        return ("Narrator", 1)

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=open_key,
        EnumValue=enum_value,
        QueryValueEx=query_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: None))
    monkeypatch.setenv("WINDIR", r"C:\Windows")
    monkeypatch.setattr(java.os.path, "isfile", lambda _path: False)

    assert java.JavaAccessBridge.is_enabled() is False
    assert calls["enum"] == 2


def test_is_enabled_accepts_a_jabswitch_registry_value_and_ignores_bad_configuration(
    monkeypatch,
) -> None:
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_key(_root, subkey):
        if subkey.endswith("ATs"):
            return _Key()
        return _Key()

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=open_key,
        EnumValue=lambda _key, index: (
            "JABSwitch" if index == 0 else (_ for _ in ()).throw(OSError("end")),
            "",
            1,
        ),
        QueryValueEx=lambda *_args: (_ for _ in ()).throw(OSError("not present")),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    assert java.JavaAccessBridge.is_enabled() is True

    # The registry key exists, but Configuration itself may be absent.
    winreg.EnumValue = lambda *_args: (_ for _ in ()).throw(OSError("end"))
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: None))
    monkeypatch.setattr(java.os.path, "isfile", lambda _path: False)
    assert java.JavaAccessBridge.is_enabled() is False


@pytest.mark.parametrize(
    ("configuration", "expected"),
    [("JavaAccessBridge", True), ("oracle_javaaccessbridge", True), ("Narrator", False)],
)
def test_is_enabled_reads_modern_configuration_value(monkeypatch, configuration, expected) -> None:
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_key(_root, subkey):
        if subkey.endswith("ATs"):
            raise OSError("old key missing")
        return _Key()

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=open_key,
        QueryValueEx=lambda _key, _name: (configuration, 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: None))
    monkeypatch.setattr(java.os.path, "isfile", lambda _path: False)
    assert java.JavaAccessBridge.is_enabled() is expected


def test_is_enabled_finds_dll_in_java_home_and_windows_fallback(monkeypatch) -> None:
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda *_args: (_ for _ in ()).throw(OSError("missing")),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: r"C:\jdk"))
    seen: list[str] = []
    monkeypatch.setattr(
        java.os.path, "isfile", lambda path: seen.append(path) or path.endswith("64.dll")
    )
    assert java.JavaAccessBridge.is_enabled() is True
    assert seen[0].endswith(r"C:\jdk\bin\WindowsAccessBridge-64.dll")

    seen.clear()
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: None))
    monkeypatch.setenv("WINDIR", r"D:\Windows")
    assert java.JavaAccessBridge.is_enabled() is True
    assert any(r"D:\Windows\SysWOW64" in path for path in seen)


def test_is_enabled_continues_after_every_dll_candidate_is_missing(monkeypatch) -> None:
    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda *_args: (_ for _ in ()).throw(OSError("missing")),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: r"C:\jdk"))
    monkeypatch.setattr(java.os.path, "isfile", lambda _path: False)
    assert java.JavaAccessBridge.is_enabled() is False


def test_java_home_uses_a_valid_registry_installation(monkeypatch, tmp_path) -> None:
    invalid = tmp_path / "missing"
    monkeypatch.setenv("JAVA_HOME", str(invalid))

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    root_key = _Key()
    version_key = _Key()

    def open_key(parent, subkey):
        if isinstance(parent, _Key):
            assert subkey == "17"
            return version_key
        return root_key

    def query_value(key, name):
        if key is root_key:
            assert name == "CurrentVersion"
            return "17", 1
        assert key is version_key and name == "JavaHome"
        return str(tmp_path), 1

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=open_key,
        QueryValueEx=query_value,
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    assert java.JavaAccessBridge.java_home() == str(tmp_path)


def test_java_home_falls_back_to_where_and_swallows_discovery_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "not-a-directory"))
    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda *_args: (_ for _ in ()).throw(OSError("no registry")),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    java_exe = tmp_path / "jdk" / "bin" / "java.exe"
    java_exe.parent.mkdir(parents=True)
    with patch.object(
        java.subprocess,
        "run",
        return_value=SimpleNamespace(returncode=0, stdout=str(java_exe).encode()),
    ) as run:
        assert java.JavaAccessBridge.java_home() == str(tmp_path / "jdk")
    run.assert_called_once_with(["where.exe", "java"], capture_output=True, timeout=5)

    with patch.object(
        java.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"")
    ):
        assert java.JavaAccessBridge.java_home() is None
    with patch.object(java.subprocess, "run", side_effect=OSError("where unavailable")):
        assert java.JavaAccessBridge.java_home() is None


def test_java_home_ignores_registry_value_errors(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "not-a-directory"))

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda *_args: _Key(),
        QueryValueEx=lambda *_args: (_ for _ in ()).throw(OSError("bad value")),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    with patch.object(
        java.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout=b"")
    ):
        assert java.JavaAccessBridge.java_home() is None


def test_java_home_skips_an_invalid_registry_home_and_invalid_where_home(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "not-a-directory"))

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    root_key = _Key()
    version_key = _Key()
    winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda parent, _sub: version_key if parent is root_key else root_key,
        QueryValueEx=lambda key, name: ("17", 1) if key is root_key else (r"C:\missing-jdk", 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.setattr(java.os.path, "isdir", lambda _path: False)
    where = SimpleNamespace(returncode=0, stdout=b"C:\\jdk\\bin\\java.exe\n")
    with patch.object(java.subprocess, "run", return_value=where):
        assert java.JavaAccessBridge.java_home() is None


def test_enable_prefers_jdk_jabswitch_and_reports_failures(monkeypatch, tmp_path) -> None:
    home = tmp_path / "jdk"
    binary = home / "bin" / "jabswitch.exe"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: str(home)))
    completed = SimpleNamespace(returncode=0, stderr=b"")
    with patch.object(java.subprocess, "run", return_value=completed) as run:
        java.JavaAccessBridge.enable()
    run.assert_called_once_with([str(binary), "/enable"], capture_output=True, timeout=15)

    with patch.object(
        java.subprocess, "run", return_value=SimpleNamespace(returncode=2, stderr=b"bad\xff")
    ):
        with pytest.raises(RuntimeError, match=r"exit 2.*bad"):
            java.JavaAccessBridge.enable()
    with patch.object(java.subprocess, "run", side_effect=FileNotFoundError):
        with pytest.raises(RuntimeError, match=r"jabswitch\.exe not found"):
            java.JavaAccessBridge.enable()

    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: str(home)))
    with (
        patch.object(java.os.path, "isfile", return_value=False),
        patch.object(
            java.subprocess, "run", return_value=SimpleNamespace(returncode=0, stderr=b"")
        ) as run,
    ):
        java.JavaAccessBridge.enable()
    run.assert_called_once_with(["jabswitch.exe", "/enable"], capture_output=True, timeout=15)


def test_ensure_enabled_calls_enable_only_when_probe_is_false(monkeypatch) -> None:
    monkeypatch.setattr(java.JavaAccessBridge, "is_enabled", staticmethod(lambda: False))
    with patch.object(java.JavaAccessBridge, "enable") as enable:
        java.JavaAccessBridge.ensure_enabled()
    enable.assert_called_once_with()


# Session construction, ctypes prototypes, and low-level calls


def test_session_is_singleton_and_init_tries_both_dll_candidates(monkeypatch) -> None:
    wab = _MinimalWab()
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: r"C:\jdk"))
    attempts: list[str] = []

    def load(path):
        attempts.append(path)
        if path == "windowsaccessbridge-64.dll":
            raise OSError("not on PATH")
        return wab

    monkeypatch.setattr(java.ctypes, "WinDLL", load)
    java._JABSession._instance = None
    first = java._JABSession.get_or_create()
    assert java._JABSession.get_or_create() is first
    assert attempts == ["windowsaccessbridge-64.dll", r"C:\jdk\bin\windowsaccessbridge-64.dll"]
    assert wab.Windows_run.calls
    assert first._thread_ident == threading.get_ident()
    java._JABSession._instance = None


def test_session_init_raises_when_no_dll_can_be_loaded(monkeypatch) -> None:
    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: None))
    monkeypatch.setattr(java.ctypes, "WinDLL", Mock(side_effect=OSError("missing")))
    with pytest.raises(RuntimeError, match=r"Could not load windowsaccessbridge-64\.dll"):
        java._JABSession()


def test_setup_prototypes_marks_absent_optional_exports(monkeypatch) -> None:
    session = _session(_MinimalWab())
    session._setup_prototypes()
    assert session._has_release_api is False
    assert session._has_value_api is False
    assert session._has_set_text_api is False
    assert session._has_text_api is False
    assert session._has_focus_api is False
    assert session._has_action_api is False
    assert session._wab.Windows_run.restype is None
    assert session._wab.getAccessibleChildFromContext.argtypes == [
        ctypes.c_long,
        ctypes.c_int64,
        ctypes.c_int,
    ]


def test_pump_dispatches_pending_messages_and_sleeps(monkeypatch) -> None:
    user32 = SimpleNamespace(
        PeekMessageW=Mock(side_effect=[1, 0, 0]),
        TranslateMessage=Mock(),
        DispatchMessageW=Mock(),
    )
    monkeypatch.setattr(java.ctypes, "windll", SimpleNamespace(user32=user32))
    slept: list[float] = []
    monkeypatch.setattr(java.time, "sleep", lambda interval: slept.append(interval))
    session = _session(SimpleNamespace())
    session.pump(count=2, interval=0.25)
    assert user32.TranslateMessage.call_count == 1
    assert user32.DispatchMessageW.call_count == 1
    assert slept == [0.25, 0.25]


def test_session_context_and_info_wrappers_cover_success_and_failure() -> None:
    def get_root(_hwnd, vm_ref, ac_ref):
        vm_ref._obj.value = 7
        ac_ref._obj.value = 99
        return True

    wab = SimpleNamespace(
        isJavaWindow=lambda hwnd: hwnd == 5,
        getAccessibleContextFromHWND=get_root,
        getAccessibleContextInfo=lambda _vm, _ac, ref: setattr(ref._obj, "name", "root") or True,
        getAccessibleChildFromContext=lambda _vm, _ac, index: -index,
    )
    session = _session(wab)
    assert session.is_java_window(5) is True
    assert session.is_java_window(6) is False
    assert session.get_root_context(1) == (7, 99)
    wab.getAccessibleContextFromHWND = lambda *_args: False
    assert session.get_root_context(1) is None
    assert session.get_info(1, 0) is None
    assert session.get_info(1, 2).name == "root"
    wab.getAccessibleContextInfo = lambda *_args: False
    assert session.get_info(1, 2) is None
    assert session.get_child(1, 2, 3) == -3


def test_session_release_swallows_bridge_errors() -> None:
    release = Mock(side_effect=RuntimeError("bridge closed"))
    session = _session(SimpleNamespace(releaseJavaObject=release), _has_release_api=True)
    session.release(1, 2)
    session.release(1, 0)
    session._has_release_api = False
    session.release(1, 3)
    release.assert_called_once_with(1, 2)


def test_session_value_and_text_set_wrappers_cover_missing_and_negative_results() -> None:
    session = _session(SimpleNamespace(), _has_value_api=False, _has_set_text_api=False)
    assert session.get_value(1, 2) is None
    assert session.set_text_contents(1, 2, "hello") is False
    assert session.set_text_contents(1, 2, "x" * (java._MAX_SET_TEXT + 1)) is False

    value = Mock(return_value=False)
    set_text = Mock(return_value=False)
    session = _session(
        SimpleNamespace(
            getCurrentAccessibleValueFromContext=value,
            setTextContents=set_text,
        ),
        _has_value_api=True,
        _has_set_text_api=True,
    )
    assert session.get_value(1, 2) is None
    assert session.set_text_contents(1, 2, "hello") is False
    value.return_value = True
    set_text.return_value = True
    value_result = session.get_value(1, 2)
    assert value_result == ""
    assert session.set_text_contents(1, 2, "hello") is True


def test_get_text_handles_info_failure_range_failure_and_short_read() -> None:
    missing = _session(SimpleNamespace(), _has_text_api=False)
    assert missing.get_text(1, 2) is None

    info_failure = _session(
        SimpleNamespace(getAccessibleTextInfo=Mock(return_value=False)), _has_text_api=True
    )
    assert info_failure.get_text(1, 2) is None

    def get_info(_vm, _ac, ref, _x, _y):
        ref._obj.charCount = 4
        return True

    range_failure = _session(
        SimpleNamespace(
            getAccessibleTextInfo=get_info, getAccessibleTextRange=Mock(return_value=False)
        ),
        _has_text_api=True,
    )
    assert range_failure.get_text(1, 2) is None

    def short_range(_vm, _ac, _start, _end, buffer, _length):
        buffer.value = "x"
        return True

    short = _session(
        SimpleNamespace(getAccessibleTextInfo=get_info, getAccessibleTextRange=short_range),
        _has_text_api=True,
    )
    assert short.get_text(1, 2) == "xxxx"
    empty = _session(
        SimpleNamespace(
            getAccessibleTextInfo=lambda _vm, _ac, ref, _x, _y: (
                setattr(ref._obj, "charCount", -2) or True
            ),
            getAccessibleTextRange=Mock(),
        ),
        _has_text_api=True,
    )
    assert empty.get_text(1, 2) == ""

    def blank_range(_vm, _ac, _start, _end, buffer, _length):
        buffer.value = ""
        return True

    blank = _session(
        SimpleNamespace(getAccessibleTextInfo=get_info, getAccessibleTextRange=blank_range),
        _has_text_api=True,
    )
    assert blank.get_text(1, 2) == ""


def test_request_focus_and_accessible_action_dispatch() -> None:
    session = _session(SimpleNamespace(), _has_focus_api=False, _has_action_api=False)
    assert session.request_focus(1, 2) is False
    assert session.do_action(1, 2) is False

    def get_actions(_vm, _ac, ref):
        ref._obj.actionsCount = 2
        ref._obj.actionInfo[0].name = "CLICK"
        ref._obj.actionInfo[1].name = "toggle"
        return True

    def do_actions(_vm, _ac, ref, failure):
        assert ref._obj.actionsCount == 1
        assert ref._obj.actions[0].name.lower() in {"click", "toggle"}
        assert failure._obj.value == -1
        return True

    wab = SimpleNamespace(
        requestFocus=Mock(return_value=True),
        getAccessibleActions=get_actions,
        doAccessibleActions=do_actions,
    )
    session = _session(wab, _has_focus_api=True, _has_action_api=True)
    assert session.request_focus(1, 2) is True
    assert session.do_action(1, 2, "click") is True
    assert session.do_action(1, 2, "TOGGLE") is True
    assert session.do_action(1, 2, "expand") is False
    wab.getAccessibleActions = Mock(return_value=False)
    assert session.do_action(1, 2) is False
    wab.getAccessibleActions = get_actions
    wab.doAccessibleActions = Mock(return_value=False)
    assert session.do_action(1, 2) is False


# Locator resolution, reads, actions, and input fallbacks


@pytest.mark.parametrize(
    ("control_type", "expected"),
    [
        (None, None),
        ("  PUSH BUTTON ", "push button"),
        ("text", "text"),
        ("ToolBar", "tool bar"),
        ("treeitem", "label"),
    ],
)
def test_role_resolution_accepts_jab_names_and_uia_aliases(control_type, expected) -> None:
    assert java.JABLocator._resolve_role(control_type) == expected


def test_locator_gets_the_lazy_singleton_session(monkeypatch) -> None:
    session = object()
    monkeypatch.setattr(java._JABSession, "get_or_create", classmethod(lambda cls: session))
    locator = java.JABLocator(1, title="Save")
    assert locator._session() is session


def test_matches_requires_criteria_and_checks_role_title_and_regex() -> None:
    info = _info(role="push button", name="Save now")
    assert java.JABLocator(1, title="Save now")._matches(info) is True
    assert java.JABLocator(1, control_type="button")._matches(info) is True
    assert java.JABLocator(1, title_re="^Save")._matches(info) is True
    assert java.JABLocator(1, control_type="label")._matches(info) is False
    assert java.JABLocator(1, title="Other")._matches(info) is False
    assert java.JABLocator(1, title_re="^Other")._matches(info) is False
    assert java.JABLocator(1)._matches(info) is False


def test_resolve_context_pumps_until_root_is_available(monkeypatch) -> None:
    session = MagicMock()
    session.get_root_context.side_effect = [None, (8, 9)]
    session.pump = Mock()
    locator = java.JABLocator(123, title="Save", timeout=5)
    locator._session = lambda: session  # type: ignore[method-assign]
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    assert locator._resolve_context() == (8, 9)
    session.pump.assert_called_once_with(10, 0.1)


def test_resolve_context_times_out_with_a_helpful_error(monkeypatch) -> None:
    session = MagicMock()
    session.get_root_context.return_value = None
    session.pump = Mock()
    locator = java.JABLocator(123, title="Save", timeout=1)
    locator._session = lambda: session  # type: ignore[method-assign]
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    with pytest.raises(ElementNotFoundError, match=r"HWND 123.*jabswitch"):
        locator._resolve_context()
    session.pump.assert_called_once_with(10, 0.1)


def test_search_releases_only_intermediate_contexts(monkeypatch) -> None:
    session = MagicMock()
    session.get_info.side_effect = [
        _info(role="panel", name="root", children=1),
        _info(role="panel", name="child", children=1),
        _info(),
    ]
    session.get_child.side_effect = [20, 30]
    locator = _locator(session)
    # The matching grandchild is returned, while the root and intermediate
    # child contexts are released by their callers.
    root, child, match = (
        _info(role="panel", name="root", children=1),
        _info(role="panel", name="child", children=1),
        _info(),
    )
    session.get_info.side_effect = [root, child, match]
    session.get_child.side_effect = [20, 30]
    result = locator._search(1, 10)
    assert result == (30, match)
    assert session.release.call_args_list == [call(1, 20)]


def test_search_covers_null_contexts_and_base_exception_cleanup() -> None:
    session = MagicMock()
    locator = _locator(session)
    assert locator._search(1, 0) is None
    assert locator._search(1, 2, depth=-1) is None
    session.get_info.return_value = None
    assert locator._search(1, 2) is None

    session.get_info.return_value = _info(role="panel", children=2)
    session.get_info.side_effect = [_info(role="panel", children=2), None]
    session.get_child.side_effect = [0, 4]
    session.release.reset_mock()
    assert locator._search(1, 2) is None
    assert session.release.call_args_list == [call(1, 4)]

    session.get_info.side_effect = [_info(role="panel", children=1), KeyboardInterrupt()]
    session.get_child.side_effect = None
    session.get_child.return_value = 3
    with pytest.raises(KeyboardInterrupt):
        locator._search(1, 2)
    session.release.assert_called_with(1, 3)


def test_search_releases_a_nonmatching_child_and_release_delegates() -> None:
    session = MagicMock()
    locator = _locator(session)
    session.get_info.side_effect = [_info(role="panel", children=1), _info(role="label")]
    session.get_child.return_value = 3
    assert locator._search(1, 2) is None
    session.release.assert_called_once_with(1, 3)
    locator._release = java.JABLocator._release.__get__(locator)  # type: ignore[method-assign]
    locator._release(1, 99)
    session.release.assert_any_call(1, 99)


def test_find_releases_root_on_each_result_kind_and_exception() -> None:
    session = MagicMock()
    locator = _locator(session)
    locator._find = java.JABLocator._find.__get__(locator)  # type: ignore[method-assign]
    locator._resolve_context = Mock(return_value=(1, 10))  # type: ignore[method-assign]
    info = _info()
    locator._search = Mock(return_value=(10, info))  # type: ignore[method-assign]
    assert locator._find() == (1, 10, info)
    session.release.assert_not_called()

    locator._search = Mock(return_value=(20, info))  # type: ignore[method-assign]
    assert locator._find() == (1, 20, info)
    session.release.assert_called_with(1, 10)

    session.release.reset_mock()
    locator._search = Mock(return_value=None)  # type: ignore[method-assign]
    assert locator._find() is None
    session.release.assert_called_once_with(1, 10)

    session.release.reset_mock()
    locator._search = Mock(side_effect=RuntimeError("bad search"))  # type: ignore[method-assign]
    assert locator._find() is None
    session.release.assert_called_once_with(1, 10)

    session.release.reset_mock()
    locator._search = Mock(side_effect=KeyboardInterrupt)  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        locator._find()
    session.release.assert_called_once_with(1, 10)

    locator._resolve_context = Mock(side_effect=RuntimeError("no root"))  # type: ignore[method-assign]
    assert locator._find() is None


def test_wait_find_retries_errors_and_handles_root_or_descendant_match(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    locator._wait_find = java.JABLocator._wait_find.__get__(locator)  # type: ignore[method-assign]
    info = _info()
    locator._resolve_context = Mock(return_value=(1, 10))  # type: ignore[method-assign]
    locator._search = Mock(return_value=(10, info))  # type: ignore[method-assign]
    assert locator._wait_find() == (1, 10, info)
    session.release.assert_not_called()

    session.release.reset_mock()
    locator._search = Mock(return_value=(20, info))  # type: ignore[method-assign]
    assert locator._wait_find() == (1, 20, info)
    session.release.assert_called_once_with(1, 10)

    # A failed probe is followed by the deadline, preserving the last
    # exception as the cause of ElementNotFoundError.
    session.release.reset_mock()
    locator._timeout = 1.0
    locator._resolve_context = Mock(side_effect=RuntimeError("no root"))  # type: ignore[method-assign]
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    with pytest.raises(ElementNotFoundError) as exc_info:
        locator._wait_find()
    assert isinstance(exc_info.value.__cause__, RuntimeError)

    # A BaseException must release the root immediately and propagate.
    session.release.reset_mock()
    locator._resolve_context = Mock(return_value=(1, 10))  # type: ignore[method-assign]
    locator._search = Mock(side_effect=KeyboardInterrupt)  # type: ignore[method-assign]
    monkeypatch.setattr(java.time, "monotonic", lambda: 0.0)
    with pytest.raises(KeyboardInterrupt):
        locator._wait_find()
    session.release.assert_called_once_with(1, 10)


def test_wait_find_retries_a_search_exception_and_releases_failed_root(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    locator._wait_find = java.JABLocator._wait_find.__get__(locator)  # type: ignore[method-assign]
    locator._timeout = 1.0
    locator._resolve_context = Mock(return_value=(1, 10))  # type: ignore[method-assign]
    locator._search = Mock(side_effect=RuntimeError("stale"))  # type: ignore[method-assign]
    clock = iter((0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with pytest.raises(ElementNotFoundError) as exc_info:
        locator._wait_find()
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    session.release.assert_called_once_with(1, 10)


def test_wait_find_sleeps_between_a_failed_probe_and_a_later_match(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    locator._wait_find = java.JABLocator._wait_find.__get__(locator)  # type: ignore[method-assign]
    locator._timeout = 1.0
    locator._resolve_context = Mock(return_value=(1, 10))  # type: ignore[method-assign]
    info = _info()
    locator._search = Mock(side_effect=[None, (10, info)])  # type: ignore[method-assign]
    clock = iter((0.0, 0.0, 0.0, 0.5))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(java.time, "sleep", Mock())
    assert locator._wait_find() == (1, 10, info)
    java.time.sleep.assert_called_once_with(0.3)  # type: ignore[attr-defined]


def test_locator_queries_cover_missing_values_and_states(monkeypatch) -> None:
    info = _info(description="status", states="enabled, visible, checked, disabled-ish")
    session = MagicMock()
    locator = _locator(session, info)
    assert locator._center(info) == (25, 40)
    assert locator._states() == {"enabled", "visible", "checked", "disabled-ish"}
    assert locator.is_visible() is True
    assert locator.is_enabled() is True
    assert locator.is_checked() is True
    locator._find.return_value = None
    assert locator._states() == set()
    assert locator.is_visible() is False
    assert locator.description() == ""
    assert locator.get_attribute("name") is None

    locator._find.return_value = (1, 2, info)
    assert locator.bounding_box() == {
        "left": 10,
        "top": 20,
        "right": 40,
        "bottom": 60,
        "width": 30,
        "height": 40,
    }
    assert locator.description() == "status"
    info.states = "published-state"
    assert locator.get_attribute("states") == "published-state"
    assert locator.get_attribute("nope", "fallback") == "fallback"
    with pytest.raises(AttributeError):
        locator.get_attribute("nope")

    win32gui = SimpleNamespace(SetForegroundWindow=Mock())
    monkeypatch.setitem(sys.modules, "win32gui", win32gui)
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    locator._bring_to_front()
    win32gui.SetForegroundWindow.assert_called_once_with(100)
    win32gui.SetForegroundWindow.side_effect = RuntimeError("locked")
    locator._bring_to_front()


def test_value_prefers_accessible_value_and_falls_back_to_text() -> None:
    session = MagicMock()
    locator = _locator(session)
    locator.value = java.JABLocator.value.__get__(locator)  # type: ignore[method-assign]
    session.get_value.return_value = "42"
    assert locator.value() == "42"
    session.get_value.return_value = ""
    locator.text = Mock(return_value="field text")  # type: ignore[method-assign]
    assert locator.value() == "field text"
    locator.text = Mock(return_value="fallback")  # type: ignore[method-assign]
    session.get_value.return_value = None
    assert locator.value() == "fallback"


def test_exists_retries_and_releases_a_successful_probe(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    locator._find.side_effect = [None, (1, 2, _info())]
    clock = iter((0.0, 0.0, 0.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    assert locator.exists(timeout=1) is True
    locator._release.assert_called_once_with(1, 2)

    locator._find.return_value = None
    locator._find.side_effect = None
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    assert locator.exists(timeout=1) is False


def test_text_direct_and_clipboard_fallback_paths(monkeypatch) -> None:
    info = _info(x=1, y=2, width=3, height=4)
    session = MagicMock()
    locator = _locator(session, info)
    session.get_text.return_value = "direct text"
    assert locator.text() == "direct text"

    session.get_text.return_value = None
    clipboard = SimpleNamespace(clear=Mock(), get_text=Mock(return_value="copied"))
    monkeypatch.setattr("dolphin_desktop._clipboard.Clipboard", clipboard)
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with (
        patch("pywinauto.mouse.click") as mouse_click,
        patch("pywinauto.keyboard.send_keys") as send_keys,
    ):
        assert locator.text() == "copied"
    mouse_click.assert_called_once_with(coords=(2, 4))
    send_keys.assert_called_once_with("^a^c", pause=0.05)
    clipboard.clear.assert_called_once_with()

    clipboard.get_text.return_value = None
    with (
        patch("pywinauto.mouse.click", side_effect=RuntimeError("mouse")),
        patch("pywinauto.keyboard.send_keys", side_effect=RuntimeError("keyboard")),
    ):
        assert locator.text() == ""


def test_click_uses_jab_then_falls_back_to_mouse(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    session.do_action.return_value = True
    assert locator.click() is locator
    session.do_action.assert_called_once_with(1, 2, "click")

    session.do_action.return_value = False
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with patch("pywinauto.mouse.click") as mouse_click:
        assert locator.click() is locator
    mouse_click.assert_called_once_with(coords=(25, 40))

    with patch("pywinauto.mouse.click", side_effect=RuntimeError("locked")):
        assert locator.click() is locator


def test_programmatic_actions_and_set_value_failure(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    session.do_action.return_value = False
    with pytest.raises(UnsupportedPatternError, match="does not expose"):
        locator.expand()
    session.do_action.return_value = True
    for action in (locator.invoke, locator.select, locator.expand, locator.collapse):
        assert action() is locator

    session.set_text_contents.return_value = False
    with pytest.raises(UnsupportedPatternError, match="setTextContents"):
        locator.set_value("new")
    session.set_text_contents.return_value = True
    assert locator.set_value("new") is locator
    with pytest.raises(ValueError, match="at most"):
        locator.set_value("x" * (java._MAX_SET_TEXT + 1))

    session.do_action.return_value = False
    with pytest.raises(UnsupportedPatternError, match="toggle or click"):
        locator.toggle()


def test_mouse_keyboard_operations_are_forwarded(monkeypatch) -> None:
    session = MagicMock()
    info = _info(x=0, y=0, width=10, height=20)
    locator = _locator(session, info)
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with (
        patch("pywinauto.mouse.double_click") as double_click,
        patch("pywinauto.mouse.right_click") as right_click,
        patch("pywinauto.mouse.move") as move,
        patch("pywinauto.keyboard.send_keys") as send_keys,
    ):
        assert locator.double_click() is locator
        assert locator.right_click() is locator
        assert locator.hover() is locator
        assert locator.type_text("hello", pause=0.2) is locator
        assert locator.press_key("{ENTER}") is locator
        assert locator.select_text() is locator
    double_click.assert_called_once_with(coords=(5, 10))
    right_click.assert_called_once_with(coords=(5, 10))
    move.assert_called_once_with(coords=(5, 10))
    assert (
        call("hello", pause=0.2, with_spaces=True, with_tabs=True, with_newlines=True)
        in send_keys.call_args_list
    )
    assert call("{ENTER}") in send_keys.call_args_list
    assert call("^a") in send_keys.call_args_list

    with patch("pywinauto.mouse.click") as click, patch("pywinauto.keyboard.send_keys") as send:
        assert locator.clear() is locator
    click.assert_called_once_with(coords=(5, 10))
    send.assert_called_once_with("^a{DELETE}", pause=0.05)


def test_set_text_direct_and_keyboard_fallback_for_empty_and_nonempty(monkeypatch) -> None:
    session = MagicMock()
    info = _info(x=0, y=0, width=10, height=20)
    locator = _locator(session, info)
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    session.set_text_contents.return_value = True
    assert locator.set_text("direct") is locator

    session.set_text_contents.return_value = False
    with patch("pywinauto.mouse.click") as click, patch("pywinauto.keyboard.send_keys") as send:
        assert locator.set_text("hello") is locator
    click.assert_called_once_with(coords=(5, 10))
    assert send.call_args_list == [
        call("^a", pause=0.05),
        call("hello", pause=0.05, with_spaces=True, with_tabs=True, with_newlines=True),
    ]

    with (
        patch("pywinauto.mouse.click", side_effect=RuntimeError("mouse")),
        patch("pywinauto.keyboard.send_keys") as send,
    ):
        assert locator.set_text("") is locator
    assert send.call_args_list == [call("^a", pause=0.05), call("{DELETE}", pause=0.05)]


def test_screenshot_uses_the_component_rectangle_on_all_screens() -> None:
    info = _info(x=100, y=200, width=300, height=400)
    locator = _locator(MagicMock(), info)
    with patch("PIL.ImageGrab.grab", return_value="image") as grab:
        assert locator.screenshot() == "image"
    grab.assert_called_once_with(bbox=(100, 200, 400, 600), all_screens=True)


def test_focus_uses_request_focus_or_safe_fallback(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    session.request_focus.return_value = True
    assert locator.focus() is locator
    session.request_focus.return_value = False
    session._has_focus_api = False
    locator.click = Mock(return_value=locator)  # type: ignore[method-assign]
    with patch("dolphin_desktop._logging.get_logger") as get_logger:
        assert locator.focus() is locator
    locator.click.assert_called_once_with()
    get_logger.return_value.warning.assert_called_once()

    session._has_focus_api = True
    with pytest.raises(UnsupportedPatternError, match="refused keyboard focus"):
        locator.focus()


# Combo-box selection and pointer helpers


class _ComboSession:
    def __init__(self, nodes: dict[int, tuple[str, str, list[int]]]) -> None:
        self.nodes = nodes
        self.releases: list[int] = []

    def get_info(self, _vm_id: int, ac: int) -> java._ACI | None:
        node = self.nodes.get(ac)
        if node is None:
            return None
        role, name, children = node
        return _info(role, name, children=len(children))

    def get_child(self, _vm_id: int, ac: int, index: int) -> int:
        return self.nodes[ac][2][index]

    def release(self, _vm_id: int, ac: int) -> None:
        self.releases.append(ac)


def test_combo_text_index_handles_missing_info_null_children_and_depth_limit() -> None:
    session = _ComboSession(
        {
            1: ("panel", "root", [0, 2]),
            2: ("list", "items", [3, 4, 5]),
            3: ("list item", "wrong", []),
            4: ("list item", "target", []),
            5: ("list item", "last", []),
        }
    )
    locator = _locator(session)
    assert locator._combo_text_index(1, 1, "target") == 1
    assert session.releases == [3, 4, 2]

    session = _ComboSession({1: ("panel", "root", [2]), 2: ("panel", "child", [])})
    locator = _locator(session)
    assert locator._combo_text_index(1, 99, "missing") is None
    assert locator._combo_text_index(1, 1, "missing") is None

    # A deeply nested malformed tree must stop without recursing forever.
    nodes = {i: ("panel", str(i), [i + 1]) for i in range(1, 11)}
    nodes[11] = ("panel", "end", [])
    session = _ComboSession(nodes)
    locator = _locator(session)
    assert locator._combo_text_index(1, 1, "missing") is None
    assert session.releases == list(range(10, 1, -1))

    # A list can itself contain a null handle and an unreadable item; neither
    # should prevent the walk from returning "not found".
    session = _ComboSession(
        {
            1: ("list", "items", [0, 2, 3]),
            2: ("list item", "wrong", []),
            3: ("list item", "also wrong", []),
        }
    )
    original_get_info = session.get_info
    session.get_info = lambda vm, ac: None if ac == 3 else original_get_info(vm, ac)  # type: ignore[method-assign]
    locator = _locator(session)
    assert locator._combo_text_index(1, 1, "missing") is None
    assert session.releases == [2, 3]

    session = _ComboSession({1: ("list", "items", [2]), 2: ("list item", "wrong", [])})
    assert _locator(session)._combo_text_index(1, 1, "missing") is None


def test_select_item_supports_index_and_text_and_releases_on_errors(monkeypatch) -> None:
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    add_selection = _Export()
    wab = SimpleNamespace(addAccessibleSelectionFromContext=add_selection)
    session = MagicMock(_wab=wab)
    locator = _locator(session)
    assert locator.select_item(3) is locator
    assert add_selection.calls[-1] == (1, 2, 3)
    assert add_selection.argtypes == [ctypes.c_long, ctypes.c_int64, ctypes.c_int]
    assert add_selection.restype is None

    locator._combo_text_index = Mock(return_value=4)  # type: ignore[method-assign]
    assert locator.select_item("Fourth") is locator
    assert add_selection.calls[-1] == (1, 2, 4)

    locator._combo_text_index = Mock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="not found"):
        locator.select_item("Missing")
    locator._release.assert_called()

    locator._combo_text_index = Mock(side_effect=KeyboardInterrupt)  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        locator.select_item("Interrupted")
    assert locator._release.call_count >= 2


def test_check_uncheck_scroll_drag_and_scroll_into_view(monkeypatch) -> None:
    info = _info(x=0, y=0, width=10, height=20)
    session = MagicMock()
    locator = _locator(session, info)
    locator.is_checked = Mock(side_effect=[False, True])  # type: ignore[method-assign]
    locator.click = Mock(return_value=locator)  # type: ignore[method-assign]
    assert locator.check() is locator
    assert locator.uncheck() is locator
    assert locator.click.call_count == 2

    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with (
        patch("pywinauto.mouse.scroll") as scroll,
        patch("pywinauto.mouse.press") as press,
        patch("pywinauto.mouse.release") as release,
    ):
        assert locator.scroll("up", amount=4) is locator
        assert locator.scroll("down", amount=2) is locator
        assert locator.drag_to((50, 60), duration=0.3, button="right") is locator
    assert scroll.call_args_list == [
        call(coords=(5, 10), wheel_dist=4),
        call(coords=(5, 10), wheel_dist=-2),
    ]
    press.assert_called_once_with(coords=(5, 10), button="right")
    release.assert_called_once_with(coords=(50, 60), button="right")

    target = _locator(session, _info(x=100, y=200, width=20, height=20))
    with patch("pywinauto.mouse.press") as press, patch("pywinauto.mouse.release") as release:
        assert locator.drag_to(target) is locator
    press.assert_called_once_with(coords=(5, 10), button="left")
    release.assert_called_once_with(coords=(110, 210), button="left")
    assert locator.scroll_into_view() is locator

    # Already-correct checkbox states must not cause a click.
    locator.is_checked = Mock(side_effect=[True, False])  # type: ignore[method-assign]
    locator.click.reset_mock()
    assert locator.check() is locator
    assert locator.uncheck() is locator
    locator.click.assert_not_called()


def test_wait_for_state_helpers_cover_success_timeout_and_delegates(monkeypatch) -> None:
    locator = _locator(MagicMock())
    locator.exists = Mock(return_value=True)  # type: ignore[method-assign]
    locator.is_enabled = Mock(return_value=True)  # type: ignore[method-assign]
    locator.is_visible = Mock(return_value=False)  # type: ignore[method-assign]
    assert locator.wait_for("visible", timeout=0) is locator
    assert locator.wait_for("exists", timeout=0) is locator
    assert locator.wait_for("enabled", timeout=0) is locator
    assert locator.wait_for("hidden", timeout=0) is locator
    assert locator.wait_for("invisible", timeout=0) is locator
    assert locator.wait_until_hidden(0) is locator
    assert locator.wait_until_enabled(0) is locator

    locator.exists.return_value = False
    locator.is_enabled.return_value = False
    locator.is_visible.return_value = True
    clock = iter((0.0, 0.0, 2.0, 0.0, 0.0, 2.0, 0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with pytest.raises(WaitTimeoutError):
        locator.wait_for("visible", timeout=1)
    with pytest.raises(WaitTimeoutError):
        locator.wait_for("enabled", timeout=1)
    with pytest.raises(WaitTimeoutError):
        locator.wait_for("other", timeout=1)


def test_wait_for_checked_polls_exceptions_and_reports_timeout(monkeypatch) -> None:
    locator = _locator(MagicMock())
    locator.is_checked = Mock(return_value=True)  # type: ignore[method-assign]
    assert locator.wait_for_checked(timeout=1) is locator
    locator.is_checked = Mock(return_value=False)  # type: ignore[method-assign]
    assert locator.wait_for_checked(checked=False, timeout=1) is locator

    locator.is_checked = Mock(side_effect=[RuntimeError("stale"), False])  # type: ignore[method-assign]
    clock = iter((0.0, 0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with pytest.raises(WaitTimeoutError, match="checked"):
        locator.wait_for_checked(checked=True, timeout=1)


def test_wait_for_text_validates_arguments_and_matches_each_source(monkeypatch) -> None:
    locator = _locator(MagicMock())
    with pytest.raises(ValueError, match="exactly one"):
        locator.wait_for_text()
    with pytest.raises(ValueError, match="exactly one"):
        locator.wait_for_text("a", text_re="a")
    with pytest.raises(ValueError, match="always matches"):
        locator.wait_for_text("", timeout=0)
    with pytest.raises(ValueError, match="source"):
        locator.wait_for_text("a", source="value", timeout=0)

    locator.text = Mock(return_value="hello world")  # type: ignore[method-assign]
    assert locator.wait_for_text("world", timeout=1) is locator
    assert locator.wait_for_text("hello world", contains=False, timeout=1) is locator
    locator.description = Mock(return_value="Status: ready")  # type: ignore[method-assign]
    assert locator.wait_for_text("ready", source="description", timeout=1) is locator
    locator.text = Mock(return_value="value=42")  # type: ignore[method-assign]
    assert locator.wait_for_text(text_re=r"value=\d+", timeout=1) is locator

    locator.text = Mock(return_value="no match")  # type: ignore[method-assign]
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(java.time, "sleep", lambda _interval: None)
    with pytest.raises(WaitTimeoutError, match="last seen: 'no match'"):
        locator.wait_for_text("missing", timeout=1)

    locator.text = Mock(side_effect=RuntimeError("read failed"))  # type: ignore[method-assign]
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    with pytest.raises(WaitTimeoutError, match="last seen: ''"):
        locator.wait_for_text(text_re="anything", timeout=1)

    locator.text = Mock(return_value="different")  # type: ignore[method-assign]
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    with pytest.raises(WaitTimeoutError):
        locator.wait_for_text("expected", contains=False, timeout=1)

    locator.text = Mock(return_value="different")  # type: ignore[method-assign]
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    with pytest.raises(WaitTimeoutError):
        locator.wait_for_text(text_re=r"expected", timeout=1)

    # An empty regex is accepted by the argument validator but intentionally
    # produces no compiled pattern; exercise that defensive no-op branch.
    clock = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr(java.time, "monotonic", lambda: next(clock))
    with pytest.raises(WaitTimeoutError):
        locator.wait_for_text(text_re="", timeout=1)


# Collections, chaining, and retained contexts


def test_all_handles_resolution_failure_and_collect_exception(monkeypatch) -> None:
    session = MagicMock()
    locator = _locator(session)
    locator._resolve_context = Mock(side_effect=RuntimeError("not ready"))  # type: ignore[method-assign]
    assert locator.all() == []

    locator._resolve_context = Mock(return_value=(1, 2))  # type: ignore[method-assign]
    locator._collect = Mock(side_effect=ValueError("walk failed"))  # type: ignore[method-assign]
    # A mocked collector does not append a locator, so this mainly covers the
    # Exception swallowing contract.
    assert locator.all(depth=3) == []

    locator._collect = Mock(side_effect=KeyboardInterrupt)  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        locator.all()

    retained = MagicMock()
    retained.close = Mock()

    def collect_then_fail(_vm, _ac, results, _depth):
        results.append(retained)
        raise ValueError("partial walk")

    locator._collect = collect_then_fail  # type: ignore[method-assign]
    assert locator.all() == []
    retained.close.assert_called_once_with()


def test_collect_skips_null_or_too_deep_nodes_and_releases_nonmatches() -> None:
    session = MagicMock()
    locator = _locator(session)
    results: list[java.JABLocator] = []
    assert locator._collect(1, 0, results, 2) is False
    assert locator._collect(1, 2, results, -1) is False
    session.get_info.return_value = None
    assert locator._collect(1, 2, results, 2) is False
    session.get_info.return_value = _info(role="panel", children=1)
    session.get_child.return_value = 0
    assert locator._collect(1, 2, results, 2) is False
    session.release.assert_called_with(1, 2)

    session.get_info.return_value = _info(role="push button", children=0)
    session.get_child.return_value = 4
    session.get_info.side_effect = None
    assert locator._collect(1, 2, results, 2) is True
    assert len(results) == 1
    results[0].close()

    session.get_info.side_effect = [_info(role="panel", children=1), _info(role="push button")]
    session.get_child.return_value = 5
    results.clear()
    assert locator._collect(1, 2, results, 2) is False
    assert len(results) == 1
    results[0].close()


def test_count_nth_chaining_and_repr() -> None:
    locator = java.JABLocator(123, control_type="button", title="Save", title_re="^S", timeout=2)
    assert repr(locator) == "JABLocator(hwnd=123, control_type='button', title='Save')"
    assert locator.timeout(8)._timeout == 8
    assert locator.timeout(8)._control_type == "button"
    child = locator.locator(control_type="edit", title="Field")
    assert child._control_type == "edit"
    assert child._title == "Field"
    assert child._timeout == 2
    inherited = locator.locator()
    assert inherited._title_re == "^S"


def test_count_and_nth_close_unwanted_results() -> None:
    locator = java.JABLocator(1, title="anything")
    unwanted = MagicMock()
    unwanted.close = Mock()
    wanted = MagicMock()
    wanted.close = Mock()
    locator.all = Mock(return_value=[unwanted, wanted])  # type: ignore[method-assign]
    assert locator.count() == 2
    assert unwanted.close.call_count == 1
    assert wanted.close.call_count == 1

    another = MagicMock()
    another.close = Mock()
    locator.all.return_value = [another, wanted]
    assert locator.nth(1) is wanted
    another.close.assert_called_once_with()
    locator.all.return_value = [another]
    with pytest.raises(ElementNotFoundError, match="index 4"):
        locator.nth(4)
    another.close.assert_called()


def test_collect_retains_matching_context_and_recurses_into_children() -> None:
    session = MagicMock()
    locator = _locator(session)
    session.get_info.side_effect = [
        _info(role="push button", children=1),
        _info(role="push button"),
    ]
    session.get_child.return_value = 8
    results: list[java.JABLocator] = []
    assert locator._collect(1, 2, results, 2) is True
    assert len(results) == 2
    assert session.release.call_count == 0
    for item in results:
        item.close()


def test_resolved_locator_lifecycle_and_refresh(monkeypatch) -> None:
    info = _info()
    session = MagicMock()
    session._thread_ident = threading.get_ident()
    resolved = java._JABResolvedLocator(1, 2, 3, info, 4, session=session)
    resolved._session = lambda: session  # type: ignore[method-assign]
    assert resolved._live_ac() == 3
    session.get_info.return_value = _info(name="Changed")
    found = resolved._find()
    assert found[0:2] == (2, 3)
    assert found[2].name == "Changed"
    resolved._release(2, 3)
    session.release.assert_not_called()
    resolved.close()
    resolved.close()
    session.release.assert_called_once_with(2, 3)
    assert resolved._live_ac() == 0

    clone_owner = java._JABResolvedLocator(1, 2, 4, info, 4, session=session)
    clone = clone_owner.timeout(9)
    assert clone._owner is clone_owner
    assert clone._live_ac() == 4
    clone.close()
    assert clone._live_ac() == 4
    clone_owner.close()
    assert clone._live_ac() == 0


def test_resolved_locator_missing_context_and_finalizer_branches(monkeypatch) -> None:
    info = _info()
    session = MagicMock()
    session._thread_ident = threading.get_ident()
    session.get_info.return_value = None
    resolved = java._JABResolvedLocator(1, 2, 3, info, 1, session=session)
    resolved._session = lambda: session  # type: ignore[method-assign]
    assert resolved._find() is None
    with pytest.raises(ElementNotFoundError, match="no longer readable"):
        resolved._wait_find()

    session.get_info.return_value = _info(name="live")
    assert resolved._wait_find()[2].name == "live"

    no_session = java._JABResolvedLocator(1, 2, 3, info, 1)
    no_session.__del__()

    foreign = java._JABResolvedLocator(1, 2, 4, info, 1, session=session)
    session._thread_ident = threading.get_ident() + 1
    foreign.__del__()
    assert foreign._ac == 4

    session._thread_ident = threading.get_ident()
    failing = java._JABResolvedLocator(1, 2, 5, info, 1, session=session)
    session.release.side_effect = RuntimeError("teardown")
    failing.__del__()  # the finalizer intentionally swallows release failures


def test_resolved_close_uses_live_session_when_not_captured() -> None:
    info = _info()
    session = MagicMock()
    resolved = java._JABResolvedLocator(1, 2, 3, info, 1, session=None)
    resolved._session = lambda: session  # type: ignore[method-assign]
    resolved.close()
    session.release.assert_called_once_with(2, 3)


def test_java_locator_matches_role_title_and_regex_criteria() -> None:
    from dolphin_desktop._java import _ACI, JABLocator

    info = _ACI()
    info.role_en_US = "push button"
    info.name = "Save document"
    assert JABLocator(1, control_type="button", title="Save document")._matches(info) is True
    assert JABLocator(1, control_type="button", title_re="^Save")._matches(info) is True
    assert JABLocator(1, control_type="button", title="Cancel")._matches(info) is False


def test_jab_locator_queries_and_programmatic_actions() -> None:
    from dolphin_desktop._exceptions import UnsupportedPatternError
    from dolphin_desktop._java import _ACI

    info = _ACI()
    info.name = "Save"
    info.description = "Save the document"
    info.role_en_US = "push button"
    info.states_en_US = "enabled, visible, checked"
    info.x, info.y, info.width, info.height = 10, 20, 30, 40

    session = Mock()
    session.get_text.return_value = "visible label"
    session.get_value.return_value = "42"
    session.do_action.return_value = True
    session.set_text_contents.return_value = True
    locator = _locator(session, info)

    assert locator.exists() is True
    assert locator.is_visible() is True
    assert locator.is_enabled() is True
    assert locator.is_checked() is True
    assert locator.bounding_box() == {
        "left": 10,
        "top": 20,
        "right": 40,
        "bottom": 60,
        "width": 30,
        "height": 40,
    }
    assert locator.text() == "visible label"
    assert locator.value() == "42"
    assert locator.description() == "Save the document"
    assert locator.get_attribute("name") == "Save"
    assert locator.get_attribute("unknown", "fallback") == "fallback"
    with pytest.raises(AttributeError, match="publishes no attribute"):
        locator.get_attribute("unknown")

    actions = (locator.invoke, locator.toggle, locator.select, locator.expand, locator.collapse)
    for action in actions:
        assert action() is locator
    assert locator.set_value("new value") is locator
    assert session.do_action.call_count == 5
    session.set_text_contents.assert_called_once_with(1, 2, "new value")

    session.do_action.return_value = False
    with pytest.raises(UnsupportedPatternError, match="does not expose"):
        locator.invoke()
    with pytest.raises(ValueError, match="at most"):
        locator.set_value("x" * 1024)


def test_java_access_bridge_checks_environment_and_runs_enable(monkeypatch, tmp_path) -> None:
    import dolphin_desktop._java as java

    java_home = tmp_path / "jdk"
    java_home.mkdir()
    monkeypatch.setenv("JAVA_HOME", str(java_home))
    assert java.JavaAccessBridge.java_home() == str(java_home)

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=1,
        HKEY_LOCAL_MACHINE=2,
        OpenKey=lambda *_args: _Key(),
        EnumValue=lambda *_args: ("jabswitch", "", 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    assert java.JavaAccessBridge.is_enabled() is True

    monkeypatch.setattr(java.JavaAccessBridge, "is_enabled", staticmethod(lambda: True))
    with patch.object(java.JavaAccessBridge, "enable") as enable:
        java.JavaAccessBridge.ensure_enabled()
    enable.assert_not_called()

    monkeypatch.setattr(java.JavaAccessBridge, "java_home", staticmethod(lambda: None))
    completed = SimpleNamespace(returncode=0)
    with patch.object(java.subprocess, "run", return_value=completed) as run:
        java.JavaAccessBridge.enable()
    run.assert_called_once_with(["jabswitch.exe", "/enable"], capture_output=True, timeout=15)


def test_java_role_mapping_rejects_unknown_roles() -> None:
    from dolphin_desktop._exceptions import ElementNotFoundError
    from dolphin_desktop._java import JABLocator

    assert JABLocator(0, control_type="button")._jab_role() == "push button"
    with pytest.raises(ElementNotFoundError):
        JABLocator(0, control_type="invalid-role")
