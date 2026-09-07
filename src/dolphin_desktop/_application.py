"""Application — wraps a running process managed via pywinauto."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path
from typing import Any, Literal

if sys.platform == "win32":
    from pywinauto import Application as _PyWinApp
    from pywinauto import Desktop as _PwDesktop
    from pywinauto.findwindows import ElementNotFoundError as _PyWinElementNotFoundError
    from pywinauto.findwindows import find_elements as _find_elements
    from pywinauto.keyboard import send_keys as _send_keys
else:
    from ._platform_compat import _unavailable_class, _unsupported_callable

    _PyWinApp = _unavailable_class("Application", "pywinauto.Application")
    _PwDesktop = _unavailable_class("Desktop", "pywinauto.Desktop")
    _PyWinElementNotFoundError = RuntimeError
    _find_elements = _unsupported_callable("pywinauto.findwindows.find_elements")
    _send_keys = _unsupported_callable("pywinauto.keyboard.send_keys")

from ._exceptions import WindowNotFoundError
from ._locator import _ResolvedLocator
from ._logging import get_logger
from ._window import Window

_log = get_logger("application")

# Module-level set of PIDs currently tracked as live dolphin-launched processes.
# The pytest plugin drains this between tests to kill any zombies.
_live_pids: set[int] = set()

# Module-level set of PIDs that should be cleaned up at SESSION END regardless of
# whether the test runner ever called Application.kill(). Used as a safety net
# when pytest hard-kills a test (e.g. ``--timeout`` hit) before the fixture's
# ``finally:`` block runs. ``Application.detach()`` moves a PID out of
# ``_live_pids`` (to survive per-test teardown) but keeps it here, so the
# session finalizer can still tear it down.
_session_pids: set[int] = set()

# PIDs dolphin attached to but does not own. Never drained by the pytest
# plugin's kill loops — that is the point of owns_process=False — but needed so
# crash dumps can scope their UIA capture to the application under test, which
# for SAP / mainframe / Oracle Forms is normally an attached process.
_attached_pids: set[int] = set()

# DLL name fragments shipped by every Electron build (case-insensitive).
_ELECTRON_MODULE_SIGS = ("vk_swiftshader", "libglesv2", "electron")
# Window class used by all Chromium / Electron top-level windows.
_ELECTRON_WINDOW_CLASS = "Chrome_WidgetWin_1"
# DLL loaded by every WebView2 host app.
_WEBVIEW2_MODULE_SIGS = ("webview2loader",)
# DLL present in every standalone CEF (Chromium Embedded Framework) build.
_CEF_MODULE_SIGS = ("libcef",)
# MSHTML/Trident engine DLL — loaded by WPF WebBrowser control and legacy IE hosts.
_LEGACY_IE_MODULE_SIGS = ("mshtml",)
# Qt runtime DLLs shipped by every Qt 5 / Qt 6 app — used for runtime detection.
_QT_MODULE_SIGS = ("qt5core", "qt6core", "qt5widgets", "qt6widgets")
# Qt Quick / QML runtime DLLs — present only when the app uses QML.
# Qt Quick widgets are opaque to UIA (single Pane node, no children),
# so detecting them lets us warn users early.
_QT_QUICK_MODULE_SIGS = ("qt5quick", "qt6quick", "qt5qml", "qt6qml")
# Win32 process-open access rights for module enumeration.
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010
# Enough for QueryFullProcessImageNameW, and granted where the wider
# _PROCESS_QUERY_INFORMATION is refused.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_STILL_ACTIVE = 259
_PROCESS_NOT_FOUND_ERRORS = frozenset({87, 1168})

# ``EnumProcessModulesEx`` filter: 0x03 = LIST_MODULES_ALL — needed for
# 64-bit hosts loading Qt DLLs that a plain EnumProcessModules misses.
_ENUM_MODULES_ALL = 0x03


def _list_modules(pid: int, *, use_extended: bool = False) -> list[str]:
    """Return lowercased filenames of every module loaded by *pid*.

    Returns ``[]`` on any failure (invalid PID, access denied, PID gone).
    Centralises the ``OpenProcess → Enum → GetModuleFileNameEx →
    CloseHandle`` dance that every detection function used to repeat.

    Args:
        pid: The process to scan.
        use_extended: When True, prefer ``EnumProcessModulesEx`` with the
            ``LIST_MODULES_ALL`` filter. Required for 64-bit hosts that
            load Qt via a bit-mismatched launcher — the plain enumerator
            silently drops those modules. Detection functions targeting
            Qt pass ``use_extended=True``; simpler probes (Electron,
            WebView2, CEF) can use the plain enumerator.
    """
    try:
        import win32api
        import win32process
    except Exception:
        return []

    try:
        hproc = win32api.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
    except Exception:
        return []

    names: list[str] = []
    try:
        if use_extended:
            try:
                mods = win32process.EnumProcessModulesEx(hproc, _ENUM_MODULES_ALL)
            except Exception:
                mods = win32process.EnumProcessModules(hproc)
        else:
            mods = win32process.EnumProcessModules(hproc)
        for hmod in mods:
            try:
                names.append(win32process.GetModuleFileNameEx(hproc, hmod).lower())
            except Exception:
                continue
    except Exception:
        pass
    finally:
        try:
            win32api.CloseHandle(hproc)
        except Exception:
            pass
    return names


def _process_image_path(pid: int) -> str | None:
    """Return the lowercased path of *pid*'s own executable, or None on failure.

    Uses ``QueryFullProcessImageNameW``, which reads the image path the kernel
    recorded at ``CreateProcess`` time. The module-list route
    (``EnumProcessModules``) cannot be used here: it fails until the target's
    loader has populated the PEB, so it returns ``None`` for every process
    read immediately after spawn — precisely the case
    :func:`~dolphin_desktop._desktop._image_path_now` exists for.
    ``PROCESS_QUERY_LIMITED_INFORMATION`` also succeeds against processes at a
    higher integrity level, where ``PROCESS_QUERY_INFORMATION`` is denied.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [
            wt.HANDLE,
            wt.DWORD,
            wt.LPWSTR,
            ctypes.POINTER(wt.DWORD),
        ]
        k32.CloseHandle.argtypes = [wt.HANDLE]
    except Exception:
        return None

    try:
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    except Exception:
        return None
    if not handle:
        return None
    try:
        size = wt.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value.lower() or None
    except Exception:
        return None
    finally:
        try:
            k32.CloseHandle(handle)
        except Exception:
            pass


def _process_state(pid: int) -> Literal["running", "stopped", "unknown"]:
    """Return the process state without collapsing query failures into stopped."""
    try:
        import ctypes
        import ctypes.wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k32.GetExitCodeProcess.restype = wt.BOOL
        k32.CloseHandle.argtypes = [wt.HANDLE]
    except Exception:
        return "unknown"

    try:
        handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    except Exception:
        return "unknown"
    if not handle:
        return "stopped" if ctypes.get_last_error() in _PROCESS_NOT_FOUND_ERRORS else "unknown"
    try:
        exit_code = wt.DWORD()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return "unknown"
        return "running" if exit_code.value == _PROCESS_STILL_ACTIVE else "stopped"
    except Exception:
        return "unknown"
    finally:
        try:
            k32.CloseHandle(handle)
        except Exception:
            pass


def _has_any_signature(names: list[str], signatures: tuple[str, ...]) -> bool:
    """Return True if any signature substring appears in any module name."""
    return any(sig in name for name in names for sig in signatures)


def _electron_by_window_class(pid: int) -> bool:
    """True if *pid* owns any visible window with class Chrome_WidgetWin_1."""
    try:
        import win32gui
        import win32process

        found: list[bool] = []

        def _cb(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            try:
                _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return
            if wpid == pid and win32gui.GetClassName(hwnd) == _ELECTRON_WINDOW_CLASS:
                found.append(True)

        win32gui.EnumWindows(_cb, None)
        return bool(found)
    except Exception:
        return False


def _visible_window_handles(pid: int) -> list[int]:
    """Return visible top-level window handles owned by *pid*.

    ``pywinauto.Application.windows()`` builds its result from the selected
    accessibility backend.  UIA can omit owned/transient top-level windows,
    including the active modal dialog of an application.  Win32's window list
    is the authoritative source for that relationship, so use it to fill in
    anything the backend did not expose.
    """
    try:
        import win32gui
        import win32process
    except Exception:
        return []

    handles: list[int] = []

    def _cb(hwnd: int, _: object) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            _, window_pid = win32process.GetWindowThreadProcessId(hwnd)
            if window_pid == pid:
                handles.append(int(hwnd))
        except Exception:
            # Windows can destroy a dialog between EnumWindows and the
            # callback.  Ignore that window and keep the enumeration useful.
            pass

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return []
    return handles


def _get_last_active_popup(hwnd: int) -> int | None:
    """Return the last active popup for *hwnd*, or ``None`` on failure."""
    try:
        import win32gui

        get_last_active_popup = getattr(win32gui, "GetLastActivePopup", None)
        if get_last_active_popup is not None:
            return int(get_last_active_popup(hwnd))
    except Exception:
        pass

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_last_active_popup = user32.GetLastActivePopup
        get_last_active_popup.argtypes = (wintypes.HWND,)
        get_last_active_popup.restype = wintypes.HWND
        return int(get_last_active_popup(hwnd))
    except Exception:
        return None


def _last_active_popup_handle(pid: int) -> int | None:
    """Return the visible active popup handle for *pid*, if one exists."""
    handles = _visible_window_handles(pid)
    visible_handles = set(handles)
    for hwnd in handles:
        popup = _get_last_active_popup(hwnd)
        if popup is None:
            continue
        if popup != hwnd and popup in visible_handles:
            return popup
    return None


def _webview2_by_modules(pid: int) -> bool:
    """True if *pid*'s loaded modules include WebView2Loader.dll."""
    return _has_any_signature(_list_modules(pid), _WEBVIEW2_MODULE_SIGS)


def _electron_by_modules(pid: int) -> bool:
    """True if *pid*'s loaded modules include Electron-specific DLLs."""
    return _has_any_signature(_list_modules(pid), _ELECTRON_MODULE_SIGS)


def _cef_by_modules(pid: int) -> bool:
    """True if *pid* has libcef.dll but no Electron-specific DLLs.

    Electron is built on CEF, so Electron processes also carry libcef.dll —
    the distinguishing factor is the absence of Electron-only DLLs
    (vk_swiftshader, libglesv2, electron).
    """
    names = _list_modules(pid)
    if not names:
        return False
    has_cef = _has_any_signature(names, _CEF_MODULE_SIGS)
    has_electron = _has_any_signature(names, _ELECTRON_MODULE_SIGS)
    return has_cef and not has_electron


def _qt_quick_one(pid: int) -> bool:
    """Scan one PID for Qt Quick / QML DLLs (no child-process follow)."""
    return _has_any_signature(_list_modules(pid, use_extended=True), _QT_QUICK_MODULE_SIGS)


def _qt_quick_in_modules(pid: int) -> bool:
    """Return True if *pid* (or any child PID) has Qt Quick / QML loaded.

    Descends into child processes for venv-launcher compatibility — see
    docstring of :func:`_qt_module_info`.
    """
    if _qt_quick_one(pid):
        return True
    for child in _enumerate_child_pids(pid):
        if _qt_quick_one(child):
            return True
    return False


def _process_parent_map() -> dict[int, list[int]]:
    """Return ``{parent_pid: [child_pid, ...]}`` from one process snapshot.

    ``CreateToolhelp32Snapshot`` walks the whole process table, so callers that
    need more than one parent's children must share a single snapshot rather
    than take one per node.
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class _PE32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD),
                ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wt.DWORD),
                ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_wchar * 260),
            ]

        k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
        k32.Process32FirstW.argtypes = [wt.HANDLE, ctypes.POINTER(_PE32)]
        k32.Process32NextW.argtypes = [wt.HANDLE, ctypes.POINTER(_PE32)]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        k32.CloseHandle.restype = wt.BOOL
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
        if snap == ctypes.c_void_p(-1).value:
            return {}
        pe = _PE32()
        pe.dwSize = ctypes.sizeof(pe)
        by_parent: dict[int, list[int]] = {}
        try:
            if k32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    by_parent.setdefault(int(pe.th32ParentProcessID), []).append(
                        int(pe.th32ProcessID)
                    )
                    if not k32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            k32.CloseHandle(snap)
        return by_parent
    except Exception:
        return {}


def _enumerate_child_pids(parent_pid: int) -> list[int]:
    """Return PIDs whose ParentProcessID equals *parent_pid*.

    Used to follow venv launcher stubs (`.venv/Scripts/python.exe` is a thin
    launcher that spawns the real `python.exe` from the base install; the
    launcher's own module list contains only kernel32 + ntdll, never Qt).
    """
    return _process_parent_map().get(parent_pid, [])


def _enumerate_descendant_pids(root_pid: int) -> set[int]:
    """Return every PID below *root_pid*, not just its direct children.

    Hand-off launchers are frequently two levels deep (venv stub → python →
    app, ``cmd.exe`` wrapper → exe), so the one-level
    :func:`_enumerate_child_pids` misses the process that actually owns the
    window. One snapshot serves the whole descent — a Chromium root with 20+
    helpers would otherwise cost 20+ full process-table walks. The visited set
    also guards against a PID-reuse cycle in the snapshot.
    """
    by_parent = _process_parent_map()
    seen: set[int] = set()
    pending = [root_pid]
    while pending:
        for child in by_parent.get(pending.pop(), ()):
            if child not in seen and child != root_pid:
                seen.add(child)
                pending.append(child)
    return seen


def _qt_module_info_one(pid: int) -> tuple[bool, str | None]:
    """Scan a single PID's module list for Qt DLLs (no child-process follow)."""
    for name in _list_modules(pid, use_extended=True):
        for sig in _QT_MODULE_SIGS:
            if sig in name:
                return True, ("6" if sig.startswith("qt6") else "5")
    return False, None


def _qt_module_info(pid: int) -> tuple[bool, str | None]:
    """Return (is_qt, version_string) by scanning *pid* AND its child processes.

    The child-pid descent handles **venv launcher stubs**: Python virtual
    environments on Windows ship a thin redirector ``.venv/Scripts/python.exe``
    that proxies stdio + arguments to the base install's ``python.exe``. The
    launcher's own module list contains only ``ntdll/kernel32/sechost`` —
    Qt only ever lives in the spawned child. Without descent, every venv-spawned
    Qt app would be classified as non-Qt.

    Returns ``(False, None)`` on any failure to keep callers exception-free.
    """
    info = _qt_module_info_one(pid)
    if info[0]:
        return info
    # Follow venv-launcher pattern: descend into child processes.
    for child in _enumerate_child_pids(pid):
        info = _qt_module_info_one(child)
        if info[0]:
            return info
    return False, None


def _legacy_ie_by_modules(pid: int) -> bool:
    """True if *pid* has loaded mshtml.dll (MSHTML/Trident engine)."""
    return _has_any_signature(_list_modules(pid), _LEGACY_IE_MODULE_SIGS)


class Application:
    """Represents a running desktop application.

    Do not instantiate directly — use :meth:`Desktop.launch` or
    :meth:`Desktop.connect`.

    Usage::

        with desktop.launch("notepad.exe") as app:
            win = app.window(title_re=".*Notepad")
            win.get_by_role("Edit").type_text("hello")
    """

    def __init__(
        self,
        app: _PyWinApp,
        backend: str,
        *,
        default_timeout_ms: int = 10_000,
        desktop: Any | None = None,
        owns_process: bool = True,
        image_path: str | None = None,
    ) -> None:
        if default_timeout_ms < 0:
            raise ValueError("default_timeout_ms must be non-negative")
        self._app = app
        self._backend = backend
        self.default_timeout_ms = default_timeout_ms
        self._desktop = desktop
        self._owns_process = owns_process
        self._launched_apps: list[tuple[str, Application]] = []
        # Cache for Qt runtime info — module enumeration right after process
        # spawn is non-deterministic on Windows (EnumProcessModulesEx can
        # return a partial list during DLL load), so we memoise once.
        self._qt_info: tuple[bool, str | None] | None = None
        # Lazy-attached Qt agent. None until first use; remains
        # None for non-Qt apps. Attempted only via .qt_agent property.
        self._qt_agent: Any = None
        # Single-instance apps hand off to another PID and exit, and a dead
        # launcher can no longer be asked for its image path when _find_window
        # needs to decide whether that other PID is the same program. Desktop's
        # launch factories therefore read the path at spawn time and pass it in
        # via *image_path*; by the time __init__ runs the launcher stub has
        # often already exited and the lookup below returns None.
        if image_path is not None:
            self._image_path: str | None = image_path
        else:
            try:
                self._image_path = _process_image_path(self.process_id)
            except Exception:
                self._image_path = None
        if owns_process:
            try:
                _live_pids.add(self.process_id)
                _session_pids.add(self.process_id)
            except Exception:
                pass
        else:
            try:
                _attached_pids.add(self.process_id)
            except Exception:
                pass

    # Window accessors

    def window(
        self,
        alias: str | None = None,
        *,
        title: str | None = None,
        title_re: str | None = None,
        class_name: str | None = None,
        auto_id: str | None = None,
        found_index: int = 0,
        timeout: float = 10.0,
    ) -> Window:
        """Return a :class:`Window` matching the given criteria or Object Repository alias.

        Waits up to *timeout* seconds for the window to become visible.

        Args:
            alias: Optional Object Repository alias. When given, the alias is
                resolved to pywinauto criteria and the returned :class:`Window`
                stores the alias name so that :meth:`Window.element` can look up
                child elements from the same YAML entry.
        """
        if alias is not None:
            from .objects import _repository

            entry = _repository.resolve(alias)
            criteria: dict[str, Any] = dict(entry.selector)
        else:
            criteria = {"found_index": found_index}
            if title is not None:
                criteria["title"] = title
            if title_re is not None:
                criteria["title_re"] = title_re
            if class_name is not None:
                criteria["class_name"] = class_name
            if auto_id is not None:
                criteria["auto_id"] = auto_id

        win = self._find_window(criteria, timeout)
        if alias is not None:
            win._alias = alias  # type: ignore[attr-defined]
        return win

    def _find_window(self, criteria: dict[str, Any], timeout: float) -> Window:
        # First pass: search within this application's own process.
        try:
            spec = self._app.window(**criteria)
            spec.wait("visible", timeout=timeout * 0.6)
            spec = self._bind_window_handle(self._app, spec)
            return Window(spec, application=self)
        except Exception:
            pass

        # Fallback: single-instance apps (e.g. Windows 11 Notepad) hand off to
        # an existing process, so the window's PID differs from the launched one.
        # Search the entire desktop with the same criteria.
        try:
            desktop = _PwDesktop(backend=self._backend)
            spec = desktop.window(**criteria)
            spec.wait("visible", timeout=timeout * 0.4)
            spec = self._bind_window_handle(desktop, spec)
        except Exception as exc:
            raise WindowNotFoundError(
                f"Window {criteria!r} not found after {timeout}s "
                f"(searched app process and entire desktop)"
            ) from exc
        self._adopt_hand_off(spec, criteria)
        return Window(spec, application=self)

    @staticmethod
    def _bind_window_handle(owner: Any, spec: Any) -> Any:
        """Rebuild a discovered window specification around its native handle."""
        handle = spec.wrapper_object().handle
        return owner.window(handle=handle)

    def _adopt_hand_off(self, spec: Any, criteria: dict[str, Any]) -> None:
        """Repoint ``self._app`` at the process that really owns *spec*'s window.

        Reconnecting makes ``kill()`` target the live process instead of the
        dead launcher. Refuses — by raising — when that PID cannot be shown to
        be this application: returning the window anyway would bind a
        stranger's process to this :class:`Application`, so every subsequent
        click, keystroke and ``qml()`` / ``qt_widget()`` / ``graphics_view()``
        call would be aimed at the wrong program.
        """
        try:
            actual_pid = spec.wrapper_object().process_id()
        except Exception as exc:
            # An unreadable owner is the *looser* case, not the safer one:
            # letting it through binds an unidentified process to this
            # Application exactly as a mismatched pid would.
            raise WindowNotFoundError(
                f"Window {criteria!r} was found outside this application's "
                "process but its owning pid could not be read, so it cannot be "
                "shown to be a hand-off of this application",
                hint=(
                    "tighten the criteria, or drive that process explicitly via "
                    "Desktop().connect(process=...)"
                ),
            ) from exc
        if not self._is_hand_off_pid(actual_pid):
            raise WindowNotFoundError(
                f"Window {criteria!r} was found on the desktop but belongs to "
                f"pid {actual_pid}, which is neither this application "
                f"(pid {self.process_id}) nor a hand-off of it",
                hint=(
                    "tighten the criteria, or drive that process explicitly via "
                    "Desktop().connect(process=...)"
                ),
            )
        try:
            old_pid = self.process_id
            # Build + connect the replacement pywinauto App on a local
            # variable before swapping ``self._app``. If ``connect``
            # raises (dead pid, elevated process, access denied) we
            # keep the original — otherwise we'd leave ``self._app``
            # pointing at an unconnected App and every subsequent
            # ``self._app.window(...)`` call would fail with a
            # confusing pywinauto AppNotConnected.
            new_app = _PyWinApp(backend=self._backend)
            new_app.connect(process=actual_pid)
            self._app = new_app
            if self._owns_process:
                _live_pids.discard(old_pid)
                _live_pids.add(actual_pid)
                _session_pids.discard(old_pid)
                _session_pids.add(actual_pid)
            else:
                _attached_pids.discard(old_pid)
                _attached_pids.add(actual_pid)
        except Exception:
            pass

    def _is_hand_off_pid(self, pid: int) -> bool:
        """Return True when *pid* is plausibly this application's own process.

        The desktop-wide fallback in :meth:`_find_window` searches every
        window on the machine, so loose criteria (``title_re=".*Report.*"``)
        can match a program the caller never launched. Adopting it would
        repoint this Application at a stranger and — when we own the process —
        hand that PID to the per-test teardown reaper, killing a user's app.
        Only a genuine hand-off qualifies: the same PID, a descendant of it, or
        another instance of the same executable image after the launched
        process has exited (the single-instance pattern this fallback exists
        for). A matching image while the original is still alive is not enough
        to distinguish an independent process, so anything unverifiable is
        refused.
        """
        try:
            if pid == self.process_id:
                return True
            if pid in _enumerate_descendant_pids(self.process_id):
                return True
        except Exception:
            return False
        if self._image_path is None:
            return False
        if _process_state(self.process_id) != "stopped":
            return False
        return _process_image_path(pid) == self._image_path

    def top_window(self) -> Window:
        # pywinauto selects the first element returned by the accessibility
        # backend, which can be the owner window even when a modal popup is
        # active.  Win32 tracks that owner/popup relationship directly.
        popup_handle = _last_active_popup_handle(self.process_id)
        if popup_handle is not None:
            try:
                spec = self._app.window(handle=popup_handle)
                return Window(spec, application=self)
            except Exception:
                pass
        spec = self._app.top_window()
        return Window(spec, application=self)

    def windows(self) -> list[Window]:
        raw_windows = self._app.windows()
        windows = [Window(w, application=self) for w in raw_windows]

        # The accessibility backend may omit an owned modal dialog.  Merge
        # the native top-level list without disturbing pywinauto's existing
        # ordering or its handling of hidden windows.
        known_handles: set[int] = set()
        for raw_window in raw_windows:
            try:
                handle = raw_window.handle
                if isinstance(handle, int):
                    known_handles.add(handle)
            except Exception:
                continue

        for hwnd in _visible_window_handles(self.process_id):
            if hwnd in known_handles:
                continue
            try:
                spec = self._app.window(handle=hwnd)
            except Exception:
                continue
            windows.append(Window(spec, application=self))
            known_handles.add(hwnd)
        return windows

    def set_default_timeout(self, timeout_ms: int) -> None:
        if timeout_ms < 0:
            raise ValueError("timeout_ms must be non-negative")
        self.default_timeout_ms = timeout_ms

    def _generated_root(self, timeout_seconds: float) -> Window:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while True:
            try:
                return self.top_window()
            except Exception as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                raise WindowNotFoundError(
                    f"No top-level window for application pid {self.process_id}"
                ) from last_error
            time.sleep(0.1)

    @staticmethod
    def _generated_criteria(
        strategy: str, value: str, exact: bool | None, nth: int | None
    ) -> dict[str, Any]:
        if not value:
            raise ValueError("locator value must be a non-empty string")
        if nth is not None and nth < 0:
            raise ValueError("nth must be non-negative")
        normalized = strategy.lower()
        if normalized in {"name", "text"}:
            criteria: dict[str, Any] = (
                {"title_re": f".*{re.escape(value)}.*"} if exact is False else {"title": value}
            )
        elif normalized == "test_id":
            criteria = {"auto_id": value}
        elif normalized == "control_type":
            criteria = {"control_type": value}
        elif normalized == "class_name":
            criteria = {"class_name": value}
        else:
            raise ValueError(f"Unsupported locator strategy {strategy!r}")
        if nth is not None:
            criteria["found_index"] = nth
        return criteria

    def find(
        self,
        strategy: str,
        value: str,
        *,
        timeout_ms: int | None = None,
        exact: bool | None = None,
        nth: int | None = None,
    ) -> Any:
        timeout = self.default_timeout_ms if timeout_ms is None else timeout_ms
        if timeout < 0:
            raise ValueError("timeout_ms must be non-negative")
        root = self._generated_root(timeout / 1000.0)
        if strategy.lower() == "xpath":
            locator = root.find_by_xpath(value)
            if nth is not None:
                locator = locator.nth(nth)
        else:
            locator = root.locator(**self._generated_criteria(strategy, value, exact, nth))
        locator = locator.timeout(timeout / 1000.0)
        locator._resolve()
        return locator

    def find_all(
        self,
        strategy: str,
        value: str,
        *,
        timeout_ms: int | None = None,
        exact: bool | None = None,
        nth: int | None = None,
    ) -> list[Any]:
        timeout = self.default_timeout_ms if timeout_ms is None else timeout_ms
        if timeout < 0:
            raise ValueError("timeout_ms must be non-negative")
        if strategy.lower() == "xpath":
            raise ValueError("xpath collections are not supported by the UIA backend")
        root = self._generated_root(timeout / 1000.0)
        parent = root._get_spec().wrapper_object()
        criteria = self._generated_criteria(strategy, value, exact, nth)
        try:
            elements = _find_elements(
                parent=parent,
                top_level_only=False,
                backend=self._backend,
                depth=None,
                **criteria,
            )
        except _PyWinElementNotFoundError:
            return []
        wrapper_cls = self._app.backend.generic_wrapper_class
        return [_ResolvedLocator(wrapper_cls(element)) for element in elements]

    def press_key(self, keys: str) -> None:
        self.top_window()._get_spec().set_focus()
        _send_keys(keys)

    def screenshot(self, path: str | Path | None = None) -> Any:
        return self.top_window().screenshot(path)

    def launch(self, cmd: str) -> Application:
        """Launch a secondary process related to this primary application."""
        if self._desktop is None:
            raise RuntimeError("Application was not created by a Desktop factory")
        app = self._desktop.launch(cmd)
        self._launched_apps.append((cmd, app))
        return app

    def terminate(self, cmd: str) -> None:
        for index, (tracked_cmd, app) in enumerate(self._launched_apps):
            if tracked_cmd == cmd:
                app.kill()
                self._launched_apps.pop(index)
                return
        raise RuntimeError(f"No application launched for {cmd!r}")

    # Process info

    @property
    def process_id(self) -> int:
        return self._app.process  # type: ignore[return-value]

    # Lifecycle

    def _close_qt_agent(self) -> None:
        if self._qt_agent is not None:
            try:
                self._qt_agent.close()
            except Exception:
                pass
            self._qt_agent = None

    def close(self, timeout: float = 5.0) -> None:
        """Shut the application down, asking first.

        NOT a graceful close: pywinauto's ``kill(soft=True)`` posts
        ``WM_CLOSE`` to every top-level window and then calls
        ``TerminateProcess`` unconditionally, so a window holding an unsaved
        -changes prompt is killed anyway. Drive the prompt yourself first when
        the test needs the application's own shutdown path.

        Only processes dolphin launched are closed. For an attached
        application (``Desktop.connect`` / any ``attach_*`` factory) this
        releases the Qt agent and logs a warning, but leaves the process
        running — the same ownership rule :meth:`kill` follows. Secondary
        applications started through :meth:`launch` are closed too, as
        :meth:`kill` terminates them.

        Args:
            timeout: Forwarded to the secondary applications closed above;
                ``kill(soft=True)`` takes no timeout of its own.
        """
        self._close_qt_agent()
        children, self._launched_apps = self._launched_apps, []
        for _, child in children:
            try:
                child.close(timeout=timeout)
            except Exception:
                pass
        # This Application is done with the process either way; a crash dump
        # taken afterwards should no longer scope its capture to it.
        _attached_pids.discard(self.process_id)
        if not self._owns_process:
            self._warn_not_owned("close")
            return
        # ORDER MATTERS: kill FIRST, then remove PID from the session
        # reaper's set. Removing before kill would leave the process
        # as a zombie if kill() fails (e.g. permissions, transient COM
        # error) — the reaper would no longer know to clean it up.
        try:
            self._app.kill(soft=True)
        except Exception:
            pass
        try:
            _live_pids.discard(self.process_id)
            _session_pids.discard(self.process_id)
        except Exception:
            pass

    def _warn_not_owned(self, action: str) -> None:
        """Log that *action* left an attached process alone."""
        _log.warning(
            "%s() left pid %s running: dolphin did not launch this process and "
            "never terminates one it does not own. Terminate it yourself, or "
            "use Desktop.launch(...) if the test should own its lifetime.",
            action,
            self.process_id,
        )

    def kill(self) -> None:
        """Forcefully terminate the application process.

        Applications dolphin launched are terminated together with every
        secondary application started through :meth:`launch`. An attached
        application (``Desktop.connect`` / any ``attach_*`` factory) is NOT
        terminated — dolphin never kills a process it did not start — but its
        launched children still are, and a warning is logged.
        """
        self._close_qt_agent()
        first_error: Exception | None = None
        children, self._launched_apps = self._launched_apps, []
        for _, child in children:
            try:
                child.kill()
            except Exception as exc:
                first_error = first_error or exc
        if not self._owns_process:
            self._warn_not_owned("kill")
            if first_error is not None:
                raise first_error
            return
        # Same order as close(): kill first, forget the PID second, so
        # a failed kill still leaves the session-end reaper with a
        # PID to retry against.
        try:
            if self._app.is_process_running():
                self._app.kill(soft=False)
        except Exception as exc:
            first_error = first_error or exc
        finally:
            try:
                _live_pids.discard(self.process_id)
                _session_pids.discard(self.process_id)
            except Exception:
                pass
        if first_error is not None:
            raise first_error

    def wait_for_idle(self, timeout: float = 10.0) -> None:
        self._app.wait_cpu_usage_lower(threshold=0.5, timeout=timeout)

    # Context manager — ensures cleanup even on test failure

    def __enter__(self) -> Application:
        return self

    def __exit__(self, *_: object) -> None:
        self.kill()

    # Electron / Chromium / WebView2 detection

    def is_webview2(self) -> bool:
        """Return ``True`` if this application hosts an Edge WebView2 control.

        Detection checks whether the process has loaded ``WebView2Loader.dll``,
        which is present in every app built with the Microsoft WebView2 SDK.

        Returns ``False`` on any OS error, never raises.
        """
        return _webview2_by_modules(self.process_id)

    def is_electron(self) -> bool:
        """Return ``True`` if this application is built on the Electron/Chromium runtime.

        Detection is done in two passes:

        1. **Window class** — Electron/Chromium main windows have class
           ``Chrome_WidgetWin_1``.  This is fast and works without elevated
           privileges.
        2. **Module signatures** — if window enumeration finds nothing (e.g.
           called before the first window appears), the process module list is
           checked for ``vk_swiftshader``, ``libglesv2`` or ``electron``
           DLLs that ship with every Electron build.

        Returns ``False`` on any error so callers never need to guard against
        exceptions.
        """
        pid = self.process_id
        return _electron_by_window_class(pid) or _electron_by_modules(pid)

    def is_cef(self) -> bool:
        """Return ``True`` if this application uses the Chromium Embedded Framework (CEF).

        Detection checks for ``libcef.dll`` in the process module list while
        excluding Electron-specific DLLs (``vk_swiftshader``, ``libglesv2``,
        ``electron``).  Because Electron is itself built on CEF, both would
        carry ``libcef.dll`` — the absence of Electron modules distinguishes a
        standalone CEF app (Spotify, Steam, Battle.net, etc.) from Electron.

        Call this method after the app's main window has loaded — CEF DLLs are
        loaded lazily during UI initialization.

        .. note::
            UIA accessibility coverage for CEF apps varies widely.  Apps that
            expose ``--force-renderer-accessibility`` (or enable it internally)
            provide a richer UIA tree.  Spotify and Steam use bitmap rendering
            for most UI, leaving only the window frame and a few controls in
            the UIA tree.  See ``docs/guides/cef-legacy.md`` for workarounds.

        Returns ``False`` on any OS error, never raises.
        """
        return _cef_by_modules(self.process_id)

    def _get_qt_info(self) -> tuple[bool, str | None]:
        """Return cached ``(is_qt, version)``; populates the cache on first call.

        The first call retries the module scan a few times because the Qt
        runtime DLLs may not yet be visible to ``EnumProcessModulesEx``
        immediately after process spawn — Windows can return a partial module
        list during the loader's first few hundred milliseconds.
        """
        if self._qt_info is not None:
            return self._qt_info
        result: tuple[bool, str | None] = (False, None)
        # Up to ~0.5 s of warm-up — costs nothing if Qt DLLs are already loaded.
        for _ in range(5):
            result = _qt_module_info(self.process_id)
            if result[0]:
                break
            import time as _t

            _t.sleep(0.1)
        self._qt_info = result
        return result

    def is_qt(self) -> bool:
        """Return ``True`` if this application is built on the Qt 5 or Qt 6 runtime.

        Detection scans the process module list for ``Qt5Core.dll``,
        ``Qt6Core.dll``, ``Qt5Widgets.dll``, or ``Qt6Widgets.dll`` — present
        in every Qt desktop app. The result is cached per :class:`Application`
        instance.  Returns ``False`` on any error.

        Use :meth:`qt_version` to distinguish Qt 5 from Qt 6.
        """
        return self._get_qt_info()[0]

    def qt_version(self) -> str | None:
        """Return ``"5"``, ``"6"``, or ``None`` if the app is not a Qt app.

        Determined by which Qt runtime DLL is loaded. The result is cached
        per :class:`Application` instance.
        """
        return self._get_qt_info()[1]

    def uses_qt_quick(self) -> bool:
        """Return ``True`` if this Qt app loads the Qt Quick / QML runtime.

        Detected by scanning the process module list for ``Qt5/6Quick.dll`` or
        ``Qt5/6Qml.dll`` — present only when the app actually uses QML.
        Returns ``False`` for pure ``QWidget`` Qt apps and for non-Qt apps.

        .. warning::
            Qt Quick / QML scene graphs are **opaque to UIA** — the entire
            QML hierarchy is exposed as a single ``Pane`` node with no
            children, regardless of how many controls are visible.  QML
            controls are therefore not addressable through UIA widget
            locators — drive them via the injected Qt agent instead::

                app = desktop.launch_qt("trading_app.exe")
                if app.uses_qt_quick():
                    app.qml("loginButton").click()

            See ``docs/guides/qt.md`` for details.
        """
        return _qt_quick_in_modules(self.process_id)

    def _find_qt_pid(self) -> int | None:
        """Return the PID that actually has Qt loaded (self or a child).

        Venv launcher stubs spawn the real Python as a child process — Qt
        only lives in the child, so we must inject the agent there.

        Retries a few times with a short warm-up delay: on big Qt apps
        (Qt Creator, KDE tools) ``EnumProcessModulesEx`` sometimes returns a
        partial module list on the first call — a single miss would then
        fail injection even though the runtime is loaded.
        """
        import time as _t

        for _ in range(5):
            if _qt_module_info_one(self.process_id)[0]:
                return self.process_id
            for child in _enumerate_child_pids(self.process_id):
                if _qt_module_info_one(child)[0]:
                    return child
            _t.sleep(0.1)
        return None

    @property
    def qt_agent(self) -> Any:
        """Return a :class:`QtAgentClient` for this Qt application.

        Lazily injects ``dolphin_qt5_agent.dll`` or ``dolphin_qt6_agent.dll``
        into the Qt-hosting process on first access, then opens a named-pipe
        connection. Subsequent accesses return the cached client.

        If the recorded process is a venv launcher stub, the agent is
        injected into the spawned child where Qt actually lives.

        Raises:
            RuntimeError: if the application is not a Qt app, or if injection
                fails (Qt runtime missing from target, insufficient privileges,
                etc.).
        """
        from ._qt_inject import QtAgentClient, QtAgentInjectError

        if self._qt_agent is not None:
            if not self._qt_agent.is_broken:
                return self._qt_agent
            # A broken client refuses every call forever, so handing the cached
            # one back would strand the caller with no way to recover short of
            # killing the application.
            try:
                return self._qt_agent.reattach()
            except (QtAgentInjectError, OSError):
                self._qt_agent = None

        is_qt, ver = self._get_qt_info()
        if not is_qt or ver is None:
            raise RuntimeError(
                f"Application(pid={self.process_id}) is not a Qt app — "
                "qt_agent is only available for Qt 5/6 processes"
            )
        qt_pid = self._find_qt_pid()
        if qt_pid is None:
            raise RuntimeError(
                f"Application(pid={self.process_id}) detected as Qt but no "
                "Qt-loading PID found in self or children"
            )
        try:
            self._qt_agent = QtAgentClient.attach(qt_pid, ver)
        except QtAgentInjectError as exc:
            raise RuntimeError(f"Qt agent injection failed for pid={qt_pid}: {exc}") from exc
        return self._qt_agent

    def has_qt_agent(self) -> bool:
        """Return True if a usable Qt agent is attached (does not trigger injection)."""
        return self._qt_agent is not None and not self._qt_agent.is_broken

    def reset_qt_agent(self) -> None:
        """Drop the cached Qt agent so the next ``qt_agent`` access rebuilds it.

        The agent DLL stays loaded in the target either way — this only discards
        dolphin's client-side connection. Use it when the agent is wedged and
        :meth:`QtAgentClient.reattach` is not enough.
        """
        agent, self._qt_agent = self._qt_agent, None
        if agent is not None:
            try:
                agent.close()
            except Exception:
                pass

    def detach(self, *, session: bool = False) -> None:
        """Remove this Application from dolphin's PER-TEST auto-kill list.

        dolphin's pytest plugin kills every dolphin-tracked PID at the end
        of each test. Module-scoped fixtures need the AUT to survive across
        tests — call ``detach()`` after the first ``window()`` resolution to
        skip per-test cleanup.

        By default the PID stays on the SESSION-end cleanup list, so if a
        timeout or crash prevents the fixture's ``app.kill()`` from running,
        the session finalizer still tears it down. Pair with a ``finally:
        app.kill()`` in your fixture to be safe in the happy path too.

        Pass ``session=True`` when the Application wraps a pre-existing
        process that dolphin did NOT launch (e.g. ``Desktop.connect(pid=…)``
        against the user's own editor). This removes the PID from BOTH the
        per-test and session-end kill lists — dolphin will not touch that
        process on cleanup.

        Idempotent — safe to call multiple times or on a fresh Application
        that was never tracked.
        """
        try:
            _live_pids.discard(self.process_id)
            if session:
                _session_pids.discard(self.process_id)
                # "dolphin will not touch that process" extends to the crash
                # dump's capture scope.
                _attached_pids.discard(self.process_id)
        except Exception:
            pass

    def qml(self, object_name: str) -> Any:
        """Find a QML item by ``objectName`` and return a :class:`QmlElement`.

        Triggers Qt agent injection on first call. Raises
        :class:`ElementNotFoundError` when no QML item with that name exists.
        """
        from ._exceptions import ElementNotFoundError
        from ._qt_elements import QmlElement

        hits = self.qt_agent.qml_find(object_name)
        if not hits:
            raise ElementNotFoundError(f"no QML item with objectName={object_name!r}")
        meta = hits[0]
        return QmlElement(self.qt_agent, meta["handle"], meta)

    def qt_widget(
        self,
        *,
        object_name: str | None = None,
        class_name: str | None = None,
        text: str | None = None,
    ) -> Any:
        """Find a QObject / QWidget via the Qt agent and wrap it.

        Convenience over ``app.qt_agent.find(**criteria)[0]`` — performs the
        same filter and returns the first match as a :class:`WidgetElement`.
        """
        from ._exceptions import ElementNotFoundError
        from ._qt_elements import WidgetElement

        criteria: dict[str, Any] = {}
        if object_name is not None:
            criteria["objectName"] = object_name
        if class_name is not None:
            criteria["className"] = class_name
        if text is not None:
            criteria["text"] = text
        hits = self.qt_agent.find(**criteria)
        if not hits:
            raise ElementNotFoundError(f"no QObject matched {criteria}")
        meta = hits[0]
        return WidgetElement(self.qt_agent, meta["handle"], meta)

    def graphics_view(
        self,
        *,
        object_name: str | None = None,
    ) -> Any:
        """Return a :class:`GraphicsViewElement` wrapping the first matching QGraphicsView."""
        from ._exceptions import ElementNotFoundError
        from ._qt_elements import GraphicsViewElement

        criteria: dict[str, Any] = {"className": "QGraphicsView"}
        if object_name is not None:
            criteria["objectName"] = object_name
        hits = self.qt_agent.find(**criteria)
        if not hits:
            raise ElementNotFoundError(f"no QGraphicsView matched {criteria}")
        meta = hits[0]
        return GraphicsViewElement(self.qt_agent, meta["handle"], meta)

    def is_legacy_ie(self) -> bool:
        """Return ``True`` if this application hosts an IE/Trident (MSHTML) browser.

        Detection checks whether the process has loaded ``mshtml.dll``, the
        MSHTML rendering engine used by the WPF ``WebBrowser`` control and
        older IE-based components.  This DLL is present in every WPF app that
        instantiates a ``WebBrowser`` control, regardless of the URL loaded.

        .. note::
            IE/Trident is End-of-Life since June 2022.  The WPF ``WebBrowser``
            control still works in .NET 4.8 and partially in .NET 8 (Windows),
            but Microsoft no longer ships security patches for MSHTML.
            For new projects use Edge WebView2 (see ``docs/guides/webview2.md``).

        Returns ``False`` on any OS error, never raises.
        """
        return _legacy_ie_by_modules(self.process_id)

    def __repr__(self) -> str:
        return f"Application(pid={self.process_id}, backend={self._backend!r})"
