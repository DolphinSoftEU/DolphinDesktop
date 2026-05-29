"""Application — wraps a running process managed via pywinauto."""

from __future__ import annotations

from typing import Any

from pywinauto import Application as _PyWinApp
from pywinauto import Desktop as _PwDesktop

from ._exceptions import WindowNotFoundError
from ._window import Window

# Module-level set of PIDs currently tracked as live dolphin-launched processes.
# The pytest plugin drains this between tests to kill any zombies.
_live_pids: set[int] = set()

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
# Win32 process-open access rights for module enumeration.
_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010


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


def _webview2_by_modules(pid: int) -> bool:
    """True if *pid*'s loaded modules include WebView2Loader.dll."""
    try:
        import win32api
        import win32process

        hproc = win32api.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
        try:
            for hmod in win32process.EnumProcessModules(hproc):
                try:
                    name = win32process.GetModuleFileNameEx(hproc, hmod).lower()
                except Exception:
                    continue
                if any(sig in name for sig in _WEBVIEW2_MODULE_SIGS):
                    return True
        finally:
            win32api.CloseHandle(hproc)
    except Exception:
        pass
    return False


def _electron_by_modules(pid: int) -> bool:
    """True if *pid*'s loaded modules include Electron-specific DLLs."""
    try:
        import win32api
        import win32process

        hproc = win32api.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
        try:
            for hmod in win32process.EnumProcessModules(hproc):
                try:
                    name = win32process.GetModuleFileNameEx(hproc, hmod).lower()
                except Exception:
                    continue
                if any(sig in name for sig in _ELECTRON_MODULE_SIGS):
                    return True
        finally:
            win32api.CloseHandle(hproc)
    except Exception:
        pass
    return False


def _cef_by_modules(pid: int) -> bool:
    """True if *pid* has libcef.dll but no Electron-specific DLLs.

    Electron is built on CEF, so Electron processes also carry libcef.dll —
    the distinguishing factor is the absence of Electron-only DLLs
    (vk_swiftshader, libglesv2, electron).
    """
    try:
        import win32api
        import win32process

        hproc = win32api.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
        try:
            names = []
            for hmod in win32process.EnumProcessModules(hproc):
                try:
                    names.append(win32process.GetModuleFileNameEx(hproc, hmod).lower())
                except Exception:
                    continue
            has_cef = any(sig in n for n in names for sig in _CEF_MODULE_SIGS)
            has_electron = any(sig in n for n in names for sig in _ELECTRON_MODULE_SIGS)
            return has_cef and not has_electron
        finally:
            win32api.CloseHandle(hproc)
    except Exception:
        pass
    return False


def _legacy_ie_by_modules(pid: int) -> bool:
    """True if *pid* has loaded mshtml.dll (MSHTML/Trident engine)."""
    try:
        import win32api
        import win32process

        hproc = win32api.OpenProcess(_PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ, False, pid)
        try:
            for hmod in win32process.EnumProcessModules(hproc):
                try:
                    name = win32process.GetModuleFileNameEx(hproc, hmod).lower()
                except Exception:
                    continue
                if any(sig in name for sig in _LEGACY_IE_MODULE_SIGS):
                    return True
        finally:
            win32api.CloseHandle(hproc)
    except Exception:
        pass
    return False


class Application:
    """Represents a running desktop application.

    Do not instantiate directly — use :meth:`Desktop.launch` or
    :meth:`Desktop.connect`.

    Usage::

        with desktop.launch("notepad.exe") as app:
            win = app.window(title_re=".*Notepad")
            win.get_by_role("Edit").type_text("hello")
    """

    def __init__(self, app: _PyWinApp, backend: str) -> None:
        self._app = app
        self._backend = backend
        try:
            _live_pids.add(self.process_id)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Window accessors
    # ------------------------------------------------------------------

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
            return Window(spec)
        except Exception:
            pass

        # Fallback: single-instance apps (e.g. Windows 11 Notepad) hand off to
        # an existing process, so the window's PID differs from the launched one.
        # Search the entire desktop with the same criteria.
        try:
            spec = _PwDesktop(backend=self._backend).window(**criteria)
            spec.wait("visible", timeout=timeout * 0.4)
            # Reconnect self._app to the actual hosting process so that kill()
            # targets the right PID (not the dead launcher).
            try:
                actual_pid = spec.wrapper_object().process_id()
                old_pid = self.process_id
                self._app = _PyWinApp(backend=self._backend)
                self._app.connect(process=actual_pid)
                _live_pids.discard(old_pid)
                _live_pids.add(actual_pid)
            except Exception:
                pass
            return Window(spec)
        except Exception as exc:
            raise WindowNotFoundError(
                f"Window {criteria!r} not found after {timeout}s "
                f"(searched app process and entire desktop)"
            ) from exc

    def top_window(self) -> Window:
        """Return the topmost window of this application."""
        spec = self._app.top_window()
        return Window(spec)

    def windows(self) -> list[Window]:
        """Return all top-level windows belonging to this application."""
        return [Window(w) for w in self._app.windows()]

    # ------------------------------------------------------------------
    # Process info
    # ------------------------------------------------------------------

    @property
    def process_id(self) -> int:
        return self._app.process  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self, timeout: float = 5.0) -> None:
        """Ask the application to close gracefully."""
        try:
            _live_pids.discard(self.process_id)
        except Exception:
            pass
        try:
            self._app.kill(soft=True)
        except Exception:
            pass

    def kill(self) -> None:
        """Forcefully terminate the application process."""
        try:
            _live_pids.discard(self.process_id)
        except Exception:
            pass
        self._app.kill(soft=False)

    def wait_for_idle(self, timeout: float = 10.0) -> None:
        self._app.wait_cpu_usage_lower(threshold=0.5, timeout=timeout)

    # ------------------------------------------------------------------
    # Context manager — ensures cleanup even on test failure
    # ------------------------------------------------------------------

    def __enter__(self) -> Application:
        return self

    def __exit__(self, *_: object) -> None:
        self.kill()

    # ------------------------------------------------------------------
    # Electron / Chromium / WebView2 detection
    # ------------------------------------------------------------------

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
            the UIA tree.  See ``docs/cef.md`` for workarounds.

        Returns ``False`` on any OS error, never raises.
        """
        return _cef_by_modules(self.process_id)

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
            For new projects use Edge WebView2 (see ``docs/webview2.md``).

        Returns ``False`` on any OS error, never raises.
        """
        return _legacy_ie_by_modules(self.process_id)

    def __repr__(self) -> str:
        return f"Application(pid={self.process_id}, backend={self._backend!r})"
