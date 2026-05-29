"""Desktop — the main entry point for dolphin, analogous to Playwright's `playwright` object."""

from __future__ import annotations

import os
import time
import warnings
from typing import Any

from pywinauto import Application as _PyWinApp

from ._application import Application
from ._exceptions import ApplicationError


class Desktop:
    """Factory for :class:`Application` instances.

    Usage::

        desktop = Desktop()
        app = desktop.launch("notepad.exe")
        # or
        app = desktop.connect(title_re=".*Notepad")

    Parameters
    ----------
    backend:
        ``"uia"`` (default) uses Microsoft UI Automation — works with UWP,
        WPF, and modern Win32 apps.  ``"win32"`` uses the legacy Win32 API
        and is better for older applications.
    hidden:
        ``True``  — launch AUT processes on a hidden desktop (``DolphinHidden``)
        so windows are invisible on the physical display.  Full UIA support
        requires the test process itself to run on that desktop — use
        ``dolphin-run pytest tests/`` for that.

        ``False`` — always use the visible default desktop.

        ``None`` (default) — auto-detect: activate hidden mode when the
        ``DOLPHIN_HEADLESS=1`` environment variable is set (injected by
        ``dolphin-run``) or when ``has_interactive_station()`` returns
        ``False`` (non-interactive CI without an RDP virtual display).
    """

    def __init__(self, backend: str = "uia", hidden: bool | None = None) -> None:
        self._backend = backend
        self._hidden_mode = hidden
        self._hDesk: int | None = None
        self._hidden_initialized = False

        # resolve auto-detect once at construction time
        if hidden is None:
            try:
                from ._runner import has_interactive_station

                env_headless = os.environ.get("DOLPHIN_HEADLESS") == "1"
                self._resolved_hidden: bool = env_headless or not has_interactive_station()
            except Exception:
                self._resolved_hidden = False
        else:
            self._resolved_hidden = hidden

    @property
    def _is_hidden(self) -> bool:
        """Effective headless mode flag."""
        return self._resolved_hidden

    def _ensure_hidden_mode(self) -> None:
        """Lazy init: create hidden desktop and switch thread if not already there."""
        if self._hidden_initialized:
            return
        self._hidden_initialized = True

        from ._runner import (
            DESKTOP_NAME,
            close_desktop,
            create_hidden_desktop,
            get_current_desktop_name,
            switch_thread_to_desktop,
        )

        if get_current_desktop_name() == DESKTOP_NAME:
            # Already on DolphinHidden — launched via dolphin-run.  No setup needed.
            return

        self._hDesk = create_hidden_desktop()
        try:
            switch_thread_to_desktop(self._hDesk)
        except OSError as exc:
            # Thread may have already created windows (COM/UIA initialised).
            # Apps will still be launched on the hidden desktop, but IUIAutomation
            # on this thread may not see them.  Use dolphin-run for full support.
            warnings.warn(
                f"Headless mode: SetThreadDesktop failed ({exc}). "
                "IUIAutomation may not see hidden-desktop windows. "
                "Run tests via 'dolphin-run pytest tests/' for full headless support.",
                stacklevel=4,
            )
        import atexit

        atexit.register(close_desktop, self._hDesk)

    # ------------------------------------------------------------------
    # Launch / connect
    # ------------------------------------------------------------------

    def launch(
        self,
        cmd: str,
        *,
        timeout: float = 10.0,
        work_dir: str | None = None,
        startup_delay: float = 0.5,
    ) -> Application:
        """Start a new process and return an :class:`Application`.

        Parameters
        ----------
        cmd:
            Command line to execute (e.g. ``"notepad.exe"`` or
            ``r"C:\\Windows\\notepad.exe my_file.txt"``).
        timeout:
            Maximum seconds to wait for the process to start.
        work_dir:
            Optional working directory for the new process.
        startup_delay:
            Seconds to wait after the process starts before returning.
            Useful for single-instance apps (e.g. Windows 11 Notepad) that
            hand off to an existing process — a brief pause lets the target
            window appear before :meth:`Application.window` is called.
        """
        if self._is_hidden:
            return self._launch_hidden(
                cmd, timeout=timeout, work_dir=work_dir, startup_delay=startup_delay
            )

        try:
            app = _PyWinApp(backend=self._backend)
            # wait_for_idle=False: packaged/store apps do not support
            # WaitForInputIdle and would raise RuntimeWarning otherwise.
            app.start(cmd, timeout=timeout, wait_for_idle=False, work_dir=work_dir)
        except Exception as exc:
            raise ApplicationError(f"Failed to launch {cmd!r}: {exc}") from exc
        if startup_delay > 0:
            time.sleep(startup_delay)
        return Application(app, backend=self._backend)

    def _launch_hidden(
        self,
        cmd: str,
        *,
        timeout: float,
        work_dir: str | None,
        startup_delay: float,
    ) -> Application:
        """Launch *cmd* on the hidden desktop and connect pywinauto by PID."""
        from ._runner import close_process_handle, launch_cmd_on_desktop

        self._ensure_hidden_mode()
        try:
            pid, h_process = launch_cmd_on_desktop(cmd, work_dir=work_dir)
            close_process_handle(h_process)
        except OSError as exc:
            raise ApplicationError(f"Failed to launch {cmd!r} on hidden desktop: {exc}") from exc

        if startup_delay > 0:
            time.sleep(startup_delay)

        try:
            app = _PyWinApp(backend=self._backend)
            app.connect(process=pid, timeout=timeout)
        except Exception as exc:
            raise ApplicationError(
                f"Failed to connect to hidden-desktop process (pid={pid}): {exc}"
            ) from exc

        return Application(app, backend=self._backend)

    def connect(
        self,
        *,
        title: str | None = None,
        title_re: str | None = None,
        process: int | None = None,
        path: str | None = None,
        class_name: str | None = None,
        found_index: int = 0,
        timeout: float = 10.0,
    ) -> Application:
        """Connect to an already-running process.

        At least one search criterion must be provided.

        When the criteria match more than one window (common for single-instance
        apps such as Windows 11 Notepad, where several windows share a class
        name or title pattern), *found_index* selects which match to use — the
        first one (``0``) by default. Without this, pywinauto raises
        ``ElementAmbiguousError`` on multiple matches.
        """
        criteria: dict[str, Any] = {}
        if title is not None:
            criteria["title"] = title
        if title_re is not None:
            criteria["title_re"] = title_re
        if process is not None:
            criteria["process"] = process
        if path is not None:
            criteria["path"] = path
        if class_name is not None:
            criteria["class_name"] = class_name

        if not criteria:
            raise ValueError("At least one search criterion must be provided")

        # process/path connect by an unambiguous identifier; found_index only
        # applies to the title/class_name lookup path, where several windows can
        # match. Adding it there picks the Nth match instead of raising.
        if process is None and path is None:
            criteria["found_index"] = found_index

        try:
            app = _PyWinApp(backend=self._backend)
            app.connect(timeout=timeout, **criteria)
        except Exception as exc:
            raise ApplicationError(f"Failed to connect to application {criteria!r}: {exc}") from exc
        return Application(app, backend=self._backend)

    @classmethod
    def for_legacy_apps(cls, **kwargs: Any) -> Desktop:
        """Win32 backend — better for Delphi/VCL, MFC, older WinForms."""
        return cls(backend="win32", **kwargs)

    def launch_qt(self, cmd: str, **kwargs: Any) -> Application:
        """Launch a Qt app with QT_ACCESSIBILITY=1."""
        import os

        os.environ.setdefault("QT_ACCESSIBILITY", "1")
        return self.launch(cmd, **kwargs)

    def launch_java(self, cmd: str, **kwargs: Any) -> Application:
        """Enable Java Access Bridge then launch a Java app."""
        from ._java import JavaAccessBridge, _JABSession

        JavaAccessBridge.ensure_enabled()
        # Initialize JAB client session BEFORE launching so it can receive
        # the WM_COPYDATA handshake broadcast by javaaccessbridge.dll on startup.
        session = _JABSession.get_or_create()
        app = self.launch(cmd, **kwargs)
        # Pump Windows messages to process the JAB handshake from the new JVM.
        session.pump(60, 0.05)  # ~3 s
        return app

    def launch_electron(self, cmd: str, **kwargs: Any) -> Application:
        """Launch an Electron/CEF app with accessibility enabled."""
        if "--force-renderer-accessibility" not in cmd:
            cmd = cmd.rstrip() + " --force-renderer-accessibility"
        return self.launch(cmd, **kwargs)

    def launch_cef(self, cmd: str, **kwargs: Any) -> Application:
        """Launch a standalone CEF (Chromium Embedded Framework) app.

        Attempts to enable UIA accessibility by appending
        ``--force-renderer-accessibility`` to the command line.  This flag is
        respected by CEF-based apps that pass unknown CLI arguments through to
        the Chromium layer (e.g. some enterprise launchers, Battle.net Launcher).

        .. warning::
            Apps that wrap CEF in their own proprietary launcher (Spotify,
            Steam) typically **ignore** this flag.  For those apps, UIA
            coverage is limited to what the app's own accessibility layer
            exposes — see ``docs/cef.md`` for workarounds using
            :class:`~dolphin_desktop.ImageLocator`.

        Usage::

            app = desktop.launch_cef(r"C:\\path\\to\\cef_app.exe", timeout=20)
            win = app.window(title_re=".*My CEF App.*", timeout=15)
            assert app.is_cef()
        """
        if "--force-renderer-accessibility" not in cmd:
            cmd = cmd.rstrip() + " --force-renderer-accessibility"
        return self.launch(cmd, **kwargs)

    def launch_webview2(self, cmd: str, **kwargs: Any) -> Application:
        """Launch a WebView2-hosted WPF/WinForms app.

        Edge WebView2 exposes a full UIA accessibility tree by default — no
        special command-line flags are required.  This method is a semantic
        wrapper over :meth:`launch` that documents intent and mirrors the
        ``launch_electron`` / ``launch_java`` / ``launch_qt`` family.
        """
        return self.launch(cmd, **kwargs)

    def find_process(
        self,
        *,
        name: str | None = None,
        pid: int | None = None,
        title: str | None = None,
        title_re: str | None = None,
    ) -> Application | None:
        """Return an Application connected to a matching running process, or None."""
        criteria: dict[str, Any] = {}
        if name is not None:
            criteria["path"] = name
        if pid is not None:
            criteria["process"] = pid
        if title is not None:
            criteria["title"] = title
        if title_re is not None:
            criteria["title_re"] = title_re

        if not criteria:
            raise ValueError("At least one search criterion must be provided")

        try:
            return self.connect(**criteria)
        except ApplicationError:
            return None

    def __repr__(self) -> str:
        hidden_str = "" if self._hidden_mode is None else f", hidden={self._is_hidden}"
        return f"Desktop(backend={self._backend!r}{hidden_str})"
