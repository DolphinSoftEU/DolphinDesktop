"""Desktop — the main entry point for dolphin: launch, connect, and stack factories."""

from __future__ import annotations

import os
import sys
import time
import warnings
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if sys.platform == "win32":
    from pywinauto import Application as _PyWinApp
else:
    from ._platform_compat import _unavailable_class

    _PyWinApp = _unavailable_class("Application", "pywinauto.Application")

from ._application import Application, _process_image_path
from ._exceptions import ApplicationError, DolphinError


def _image_path_now(app: _PyWinApp) -> str | None:
    """Read *app*'s executable path immediately after spawn.

    Must be called before ``startup_delay``: single-instance launchers hand
    off and exit within milliseconds, and once the stub is gone its image path
    is unrecoverable — which is exactly when ``Application._is_hand_off_pid``
    needs it to tell a genuine hand-off from an unrelated program.
    """
    try:
        return _process_image_path(app.process)
    except Exception:
        return None


if TYPE_CHECKING:
    from ._capabilities import Capability
    from ._cdp import CDPSession
    from ._delphi import DelphiApp
    from ._mainframe import MainframeTerminal
    from ._oracle_forms import OracleFormsApp
    from ._sap import SapGui


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

    def __init__(
        self,
        backend: str = "uia",
        hidden: bool | None = None,
        default_timeout_ms: int = 10_000,
    ) -> None:
        if default_timeout_ms < 0:
            raise ValueError("default_timeout_ms must be non-negative")
        self._backend = backend
        self._default_timeout_ms = default_timeout_ms
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

    def _launch_with_environment(
        self,
        cmd: str,
        *,
        backend: str,
        timeout: float,
        work_dir: str | None,
        env: Mapping[str, str],
    ) -> _PyWinApp:
        """Create a visible child with a private environment block."""
        from ._runner import close_process_handle, launch_cmd_on_desktop

        pid, h_process = launch_cmd_on_desktop(
            cmd,
            None,
            work_dir=work_dir,
            env=env,
        )
        close_process_handle(h_process)
        app = _PyWinApp(backend=backend)
        app.connect(process=pid, timeout=timeout)
        return app

    # Launch / connect

    def launch(
        self,
        cmd: str,
        *,
        timeout: float = 10.0,
        work_dir: str | None = None,
        startup_delay: float = 0.5,
        env: Mapping[str, str] | None = None,
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
        env:
            Optional environment overlay for the child process. Values are
            merged into a private environment block for this spawn only;
            the caller's environment is never modified.
        """
        if self._is_hidden:
            return self._launch_hidden(
                cmd,
                timeout=timeout,
                work_dir=work_dir,
                startup_delay=startup_delay,
                env=env,
            )

        try:
            if env is None:
                app = _PyWinApp(backend=self._backend)
                # wait_for_idle=False: packaged/store apps do not support
                # WaitForInputIdle and would raise RuntimeWarning otherwise.
                app.start(cmd, timeout=timeout, wait_for_idle=False, work_dir=work_dir)
            else:
                app = self._launch_with_environment(
                    cmd,
                    backend=self._backend,
                    timeout=timeout,
                    work_dir=work_dir,
                    env=env,
                )
        except Exception as exc:
            raise ApplicationError(f"Failed to launch {cmd!r}: {exc}") from exc
        image_path = _image_path_now(app)
        if startup_delay > 0:
            time.sleep(startup_delay)
        return Application(
            app,
            backend=self._backend,
            default_timeout_ms=self._default_timeout_ms,
            desktop=self,
            image_path=image_path,
        )

    # ------------------------------------------------------------------
    # Internal helpers used by stack-specific launch/attach factories.
    # These are the seams that stack modules (`_delphi`, `_oracle_forms`,
    # …) hook into so they don't each re-import pywinauto or re-build
    # criteria dicts by hand.
    # ------------------------------------------------------------------

    def _launch_raw(
        self,
        cmd: str,
        *,
        backend: str,
        timeout: float = 10.0,
        work_dir: str | None = None,
        startup_delay: float = 0.5,
        env: Mapping[str, str] | None = None,
    ) -> Application:
        """Launch a process on an explicit backend, bypassing ``self._backend``.

        Used by stack-specific launchers that require a fixed backend
        regardless of what the caller configured Desktop with — e.g.
        the Delphi component resolver needs UIA even if the user
        instantiated ``Desktop(backend="win32")``. Keeping this in one
        place means hidden-mode and other future launch improvements
        propagate to every stack for free.
        """
        if self._is_hidden:
            # Hidden-desktop path always respects the configured
            # self._backend — hidden-mode is orthogonal to UIA/Win32.
            return self._launch_hidden(
                cmd,
                timeout=timeout,
                work_dir=work_dir,
                startup_delay=startup_delay,
                env=env,
            )
        try:
            if env is None:
                pw = _PyWinApp(backend=backend)
                pw.start(cmd, timeout=timeout, wait_for_idle=False, work_dir=work_dir)
            else:
                pw = self._launch_with_environment(
                    cmd,
                    backend=backend,
                    timeout=timeout,
                    work_dir=work_dir,
                    env=env,
                )
        except Exception as exc:
            raise ApplicationError(f"Failed to launch {cmd!r}: {exc}") from exc
        image_path = _image_path_now(pw)
        if startup_delay > 0:
            time.sleep(startup_delay)
        return Application(
            pw,
            backend=backend,
            default_timeout_ms=self._default_timeout_ms,
            desktop=self,
            image_path=image_path,
        )

    def _connect_raw(
        self,
        *,
        backend: str,
        timeout: float,
        **criteria: Any,
    ) -> Application:
        """Connect to a running process on an explicit backend.

        Companion to :meth:`_launch_raw` for stack-specific ``attach_*``
        factories. Takes the same criteria pywinauto's ``connect(...)``
        accepts.
        """
        try:
            pw = _PyWinApp(backend=backend)
            pw.connect(timeout=timeout, **criteria)
        except Exception as exc:
            raise ApplicationError(f"Failed to connect to application {criteria!r}: {exc}") from exc
        # An attached process was started by someone else: it must never enter
        # the owned-PID sets, which the pytest plugin terminates after each test.
        return Application(
            pw,
            backend=backend,
            default_timeout_ms=self._default_timeout_ms,
            desktop=self,
            owns_process=False,
        )

    @staticmethod
    def _build_attach_criteria(
        *,
        title: str | None = None,
        title_re: str | None = None,
        process: int | None = None,
        path: str | None = None,
        class_name: str | None = None,
        method_name: str,
        error_class: type[DolphinError] = ApplicationError,
        accepts: tuple[str, ...] = ("title", "title_re", "process", "path"),
    ) -> dict[str, Any]:
        """Build a pywinauto criteria dict for an ``attach_*`` factory.

        Centralises the identical "one of title/title_re/process/path
        must be supplied" validation every stack was open-coding.

        Args:
            method_name: The public API name to include in the error
                message (e.g. ``"attach_delphi"``).
            error_class: Exception class to raise when no criterion is
                supplied. Default ``ApplicationError``; stack modules
                pass their own error class for consistency.
            accepts: Which of the four selectors this factory accepts —
                Oracle Forms does not accept ``path=``, for example.
        """
        supplied = {
            "title": title,
            "title_re": title_re,
            "process": process,
            "path": path,
            "class_name": class_name,
        }
        criteria = {k: v for k, v in supplied.items() if v is not None and k in accepts}
        if not criteria:
            raise error_class(
                f"{method_name} requires at least one of {' / '.join(accepts)}",
                hint="pick the identifier from Task Manager or dolphin spy",
            )
        return criteria

    def _launch_hidden(
        self,
        cmd: str,
        *,
        timeout: float,
        work_dir: str | None,
        startup_delay: float,
        env: Mapping[str, str] | None = None,
    ) -> Application:
        """Launch *cmd* on the hidden desktop and connect pywinauto by PID."""
        from ._runner import close_process_handle, launch_cmd_on_desktop

        self._ensure_hidden_mode()
        try:
            pid, h_process = launch_cmd_on_desktop(cmd, work_dir=work_dir, env=env)
            close_process_handle(h_process)
        except OSError as exc:
            raise ApplicationError(f"Failed to launch {cmd!r} on hidden desktop: {exc}") from exc

        image_path = _process_image_path(pid)
        if startup_delay > 0:
            time.sleep(startup_delay)

        try:
            app = _PyWinApp(backend=self._backend)
            app.connect(process=pid, timeout=timeout)
        except Exception as exc:
            raise ApplicationError(
                f"Failed to connect to hidden-desktop process (pid={pid}): {exc}"
            ) from exc

        return Application(
            app,
            backend=self._backend,
            default_timeout_ms=self._default_timeout_ms,
            desktop=self,
            image_path=image_path,
        )

    def connect(
        self,
        *,
        title: str | None = None,
        title_re: str | None = None,
        process: int | None = None,
        handle: int | None = None,
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
        if handle is not None:
            criteria["handle"] = handle
        if path is not None:
            criteria["path"] = path
        if class_name is not None:
            criteria["class_name"] = class_name

        if not criteria:
            raise ValueError("At least one search criterion must be provided")

        # process/path connect by an unambiguous identifier; found_index only
        # applies to the title/class_name lookup path, where several windows can
        # match. Adding it there picks the Nth match instead of raising.
        if process is None and path is None and handle is None:
            criteria["found_index"] = found_index

        try:
            app = _PyWinApp(backend=self._backend)
            app.connect(timeout=timeout, **criteria)
        except Exception as exc:
            raise ApplicationError(f"Failed to connect to application {criteria!r}: {exc}") from exc
        return Application(
            app,
            backend=self._backend,
            default_timeout_ms=self._default_timeout_ms,
            desktop=self,
            owns_process=False,
        )

    @classmethod
    def for_legacy_apps(cls, **kwargs: Any) -> Desktop:
        """Win32 backend — better for Delphi/VCL, MFC, older WinForms."""
        return cls(backend="win32", **kwargs)

    def launch_qt(
        self,
        cmd: str,
        *,
        timeout: float = 10.0,
        work_dir: str | None = None,
        startup_delay: float = 0.5,
        qt_env: dict[str, str] | None = None,
    ) -> Application:
        """Launch a Qt 5 / Qt 6 app with accessibility enabled.

        Sets ``QT_ACCESSIBILITY=1`` (and any caller-supplied *qt_env* overrides)
        in the **child** process only — the parent's environment is restored
        after spawn so subsequent ``launch()`` calls aren't polluted.

        ``QT_ACCESSIBILITY=1`` is required for Qt to attach to the platform
        accessibility bridge (UIA on Windows). Without it, ``QWidget`` controls
        are largely invisible to UIA. Note that Qt Quick / QML and
        ``QGraphicsView`` custom-paint widgets remain opaque even with this
        flag — those need the injected Qt agent (``app.qt_agent``).

        Parameters
        ----------
        cmd:
            Command line to execute.
        timeout, work_dir, startup_delay:
            See :meth:`launch`.
        qt_env:
            Additional environment variables to set in the child process
            (e.g. ``{"QT_LOGGING_RULES": "*.debug=true"}``).  Always merged
            on top of ``QT_ACCESSIBILITY=1``.  The parent environment is
            never modified.
        """
        overrides = {"QT_ACCESSIBILITY": "1"}
        if qt_env:
            overrides.update(qt_env)

        return self.launch(
            cmd,
            timeout=timeout,
            work_dir=work_dir,
            startup_delay=startup_delay,
            env=overrides,
        )

    def launch_python_script(
        self,
        script: str,
        *,
        args: list[str] | None = None,
        **kwargs: Any,
    ) -> Application:
        """Launch ``script`` using the current Python interpreter.

        Saves callers from importing ``sys`` just to build the command line —
        a clean way to spawn helper / demo scripts from inside the test
        process. Equivalent to ``launch(f'"{python_executable()}" "{script}"')``.
        """
        from ._helpers import python_executable

        cmd = f'"{python_executable()}" "{script}"'
        if args:
            cmd += " " + " ".join(f'"{a}"' for a in args)
        return self.launch(cmd, **kwargs)

    def launch_qt_python_script(
        self,
        script: str,
        *,
        args: list[str] | None = None,
        **kwargs: Any,
    ) -> Application:
        """Launch a Python script as a Qt AUT (Python + Qt bindings).

        Wraps :meth:`launch_qt` with the current Python interpreter — for
        running PySide6 / PyQt5 demo scripts from a test process without
        importing ``sys``.
        """
        from ._helpers import python_executable

        cmd = f'"{python_executable()}" "{script}"'
        if args:
            cmd += " " + " ".join(f'"{a}"' for a in args)
        return self.launch_qt(cmd, **kwargs)

    def find_process_by_image(self, image_name: str) -> Application | None:
        """Connect to a running process by its executable name (e.g. ``"qtcreator.exe"``).

        Returns ``None`` if no matching process is running. Use as the entry
        point for tests that attach to an externally launched app.
        """
        from ._helpers import find_pid_by_image_name

        pid = find_pid_by_image_name(image_name)
        if pid is None:
            return None
        try:
            return self.connect(process=pid, timeout=5.0)
        except ApplicationError:
            return None

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

    def _launch_with_cdp_flag(
        self,
        cmd: str,
        *,
        port_flag: str,
        debug_port: int,
        timeout: float,
        work_dir: str | None,
        startup_delay: float,
        runtime_label: str,
    ) -> tuple[Application, Any]:
        """Shared launcher: append *port_flag*, spawn, poll ``/json/version``.

        Used by both :meth:`launch_electron_cdp` (Electron uses the standard
        ``--remote-debugging-port=N``) and :meth:`launch_cef_cdp` (Steam and
        other CEF-hosted apps use ``-cef-enable-debugging`` on a fixed port).
        """
        from ._cdp import CDPSession

        if port_flag not in cmd:
            cmd = cmd.rstrip() + f" {port_flag}"

        app = self.launch(cmd, timeout=timeout, work_dir=work_dir, startup_delay=startup_delay)

        endpoint = f"http://127.0.0.1:{debug_port}"
        deadline = time.monotonic() + timeout
        last_err: Exception | None = None
        import urllib.error
        import urllib.request

        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{endpoint}/json/version", timeout=1.0) as resp:
                    if resp.status == 200:
                        break
            except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
                last_err = exc
                time.sleep(0.25)
        else:
            try:
                app.kill()
            except Exception:
                pass
            raise RuntimeError(
                f"{runtime_label} CDP debug port {debug_port} did not become live "
                f"within {timeout}s: {last_err}"
            )

        session = CDPSession.connect(endpoint, timeout=max(1.0, deadline - time.monotonic()))
        return app, session

    def launch_electron_cdp(
        self,
        cmd: str,
        *,
        debug_port: int = 9222,
        timeout: float = 15.0,
        work_dir: str | None = None,
        startup_delay: float = 0.5,
    ) -> tuple[Application, CDPSession]:
        """Launch an Electron app with CDP enabled and attach a session.

        Adds ``--remote-debugging-port={debug_port}`` to *cmd*, launches the
        process, waits for the debugger port to accept connections, and
        returns ``(Application, CDPSession)``. Use CDP to reach UI that
        UIA cannot see: packaged Electron without
        ``--force-renderer-accessibility``, closed Shadow DOM roots, etc.

        Requires the ``[cdp]`` extra::

            pip install "dolphin-desktop[cdp]"
            playwright install chromium

        Args:
            cmd: Full command line to the Electron executable.
            debug_port: Port for CDP (default 9222). Pick a unique port per
                parallel test worker.
            timeout: Seconds to wait for the debugger port to become live.
            work_dir: Optional working directory for the child process.
            startup_delay: Seconds to sleep after ``launch`` before probing
                the debugger port — matches :meth:`launch`.

        Raises:
            RuntimeError: if the ``[cdp]`` extra is missing, or the port
                stays unresponsive past *timeout*.

        Example::

            app, cdp = desktop.launch_electron_cdp(
                r'"C:\\Program Files\\Microsoft VS Code\\Code.exe" --no-sandbox',
                debug_port=9223,
            )
            cdp.locator(".monaco-workbench").wait_for()
            app.kill(); cdp.close()
        """
        return self._launch_with_cdp_flag(
            cmd,
            port_flag=f"--remote-debugging-port={debug_port}",
            debug_port=debug_port,
            timeout=timeout,
            work_dir=work_dir,
            startup_delay=startup_delay,
            runtime_label="Electron",
        )

    def launch_cef_cdp(
        self,
        cmd: str,
        *,
        debug_port: int = 8080,
        debug_flag: str = "-cef-enable-debugging",
        timeout: float = 20.0,
        work_dir: str | None = None,
        startup_delay: float = 1.0,
    ) -> tuple[Application, Any]:
        """Launch a CEF-hosted app (Steam, Spotify, some launchers) with CDP.

        CEF applications speak the same Chrome DevTools Protocol as Electron
        — once the debug port is up, the returned :class:`CDPSession` behaves
        identically. Only the launch flag and default port differ:

        * Electron: ``--remote-debugging-port=9222`` (Chromium switch).
        * Steam / most CEF hosts: ``-cef-enable-debugging`` on a fixed port
          (Steam pins ``8080`` and ignores an explicit port value).

        For CEF hosts that follow Chromium's own switch (some enterprise
        launchers, Battle.net), pass ``debug_flag="--remote-debugging-port=..."``
        and use :meth:`launch_electron_cdp` semantics instead.

        Requires the ``[cdp]`` extra (see :meth:`launch_electron_cdp`).

        Args:
            cmd: Full command line to the CEF host executable.
            debug_port: Port for CDP. For Steam this MUST be 8080 (hardcoded
                by the client). For other CEF hosts, match what the app uses.
            debug_flag: Launch flag to append to *cmd*. Steam expects
                ``-cef-enable-debugging`` (single dash — Steam-specific
                argument parser). Pass a full switch like
                ``--remote-debugging-port=9500`` for hosts that use the
                Chromium form.
            timeout: Seconds to wait for the debugger port to become live.
                CEF apps often take longer than Electron to bootstrap; the
                default (20 s) is higher than :meth:`launch_electron_cdp`.
            work_dir: Optional working directory for the child process.
            startup_delay: Seconds to sleep after ``launch`` before probing —
                CEF hosts frequently daemonize and hand off to a second
                process; giving them a moment prevents a busy loop.

        Raises:
            RuntimeError: if the ``[cdp]`` extra is missing, or the port
                stays unresponsive past *timeout*. When Steam is already
                running the new process usually forwards to the existing
                instance without opening the port — kill the running Steam
                first, or use :meth:`~dolphin_desktop.CDPSession.connect`
                against an already-launched Steam.

        Example::

            app, cdp = desktop.launch_cef_cdp(
                r'"C:\\Program Files (x86)\\Steam\\steam.exe"',
            )
            # library / friends / overlay are separate CEF pages
            for i, p in enumerate(cdp.pages()):
                if "steamloopback.host" in p.url or "library" in p.url.lower():
                    cdp.switch_to_page(i)
                    break
            cdp.locator("text=Counter-Strike 2").exists(timeout=5)
        """
        return self._launch_with_cdp_flag(
            cmd,
            port_flag=debug_flag,
            debug_port=debug_port,
            timeout=timeout,
            work_dir=work_dir,
            startup_delay=startup_delay,
            runtime_label="CEF",
        )

    def launch_delphi(
        self,
        cmd: str,
        *,
        timeout: float = 10.0,
        work_dir: str | None = None,
        startup_delay: float = 0.5,
        title_re: str | None = None,
    ) -> DelphiApp:
        """Launch a Delphi / VCL (or Lazarus / LCL) executable and
        return a :class:`~dolphin_desktop.DelphiApp` facade.

        Delphi / VCL apps expose standard controls via Windows UIA
        automatically. On Delphi 10.4+ the developer-assigned
        ``TComponent.Name`` propagates to UIA ``AutomationId`` — the
        preferred selector. On older Delphi / Lazarus the fallback is
        (class_name, caption).

        Args:
            cmd: Full command line to the Delphi/Lazarus executable.
            timeout: Seconds to wait for the process to start.
            work_dir: Optional working directory for the child.
            startup_delay: Seconds to sleep after launch before the
                first UIA query — Delphi splash screens take a moment
                to yield the main form.
            title_re: Optional regex used as a default form selector
                by :meth:`DelphiApp.form` when ``name`` / ``title`` are
                not supplied.

        Example::

            with desktop.launch_delphi(r"C:\\path\\to\\vcl_app.exe") as app:
                form = app.form(name="MainForm")
                form.wait_ready(timeout=10)
                form.component(name="EdtName", cls="TEdit").set_text("Alice")
                form.component(name="BtnSave").click()
        """
        from ._delphi import _launch_delphi

        return _launch_delphi(
            self,
            cmd,
            timeout=timeout,
            startup_delay=startup_delay,
            work_dir=work_dir,
            title_re=title_re,
        )

    def attach_delphi(
        self,
        *,
        title: str | None = None,
        title_re: str | None = None,
        process: int | None = None,
        path: str | None = None,
        timeout: float = 10.0,
    ) -> DelphiApp:
        """Attach to an already-running Delphi / VCL process."""
        from ._delphi import _attach_delphi

        return _attach_delphi(
            self,
            title=title,
            title_re=title_re,
            process=process,
            path=path,
            timeout=timeout,
        )

    def launch_powerbuilder(self, cmd: str, **kwargs: Any) -> Application:
        """Launch a PowerBuilder desktop application.

        PowerBuilder 2019+ (Appeon runtime) exposes standard UIA
        properties for its standard controls (buttons, edits, window
        chrome). This method is a **semantic wrapper** over
        :meth:`launch`: it documents intent (so tests are
        self-explanatory) and reserves a hook for future PowerBuilder-
        specific launch prep (PBNI, PowerScript recorder, etc.).

        For **classic PowerBuilder** (pre-2019, pre-Appeon) the
        proprietary framework is opaque to UIA — you'll fall through
        to :class:`ImageLocator` for the DataWindow grids. See
        ``docs/guides/powerbuilder.md`` for the full status.
        """
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
            exposes — see ``docs/guides/cef-legacy.md`` for workarounds using
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

    def sap(self, *, timeout: float | None = None) -> SapGui:
        """Connect to the running SAP GUI Scripting engine."""
        from ._sap import SapGui

        return SapGui.connect(timeout=timeout)

    def launch_oracle_forms(
        self,
        *,
        jnlp: str | None = None,
        jar: str | None = None,
        main_class: str | None = None,
        classpath: str | None = None,
        java_args: list[str] | None = None,
        title_re: str | None = None,
        timeout: float = 30.0,
        startup_delay: float = 2.0,
    ) -> OracleFormsApp:
        """Launch an Oracle Forms Java client and return an :class:`OracleFormsApp`.

        Exactly one of *jnlp*, *jar* or *main_class* must be supplied.

        Args:
            jnlp: URL or file path to a Java Web Start descriptor.
                Passed to ``javaws.exe`` (Oracle JDK 8/11) or, if that
                is not available, to plain ``java`` — which will fail
                unless the local JRE registered a JNLP handler.
            jar: Absolute path to a Forms client JAR — appropriate for
                the Forms Standalone Launcher (FSAL) which bundles
                everything into ``frmsal.jar``.
            main_class: Fully qualified class to run instead of a JNLP or
                JAR — the route a Swing stand-in for Forms takes.
            classpath: Passed to ``java -cp`` alongside *main_class*.
            java_args: Extra flags to pass to the JVM before ``-jar``.
                Examples: ``["-Xmx1g", "-Dfoo=bar"]``.
            title_re: Optional title regex used later by
                :meth:`OracleFormsApp.form` to locate the main window
                when multiple Java windows are open.
            timeout: Seconds to wait for the Java process to start.
            startup_delay: Seconds to sleep after launch before attaching.
                Forms often takes 1–3 s to paint after ``java`` returns.

        Requires the Java Access Bridge (auto-enabled if installed).
        Example::

            app = desktop.launch_oracle_forms(jnlp="http://forms.example.com/frmservlet")
            form = app.form()
            form.wait_ready()
            app.block("EMPLOYEES").item("EMPNO").type_text("7369")
            app.execute_query()
        """
        from ._oracle_forms import _launch_oracle_forms

        return _launch_oracle_forms(
            self,
            jnlp=jnlp,
            jar=jar,
            main_class=main_class,
            classpath=classpath,
            java_args=java_args,
            title_re=title_re,
            timeout=timeout,
            startup_delay=startup_delay,
        )

    def attach_oracle_forms(
        self,
        *,
        title: str | None = None,
        title_re: str | None = None,
        process: int | None = None,
        timeout: float = 10.0,
    ) -> OracleFormsApp:
        """Attach to an already-running Oracle Forms client.

        Provide any of ``title``, ``title_re`` or ``process``. Useful
        when the Forms application is launched by the user (double-
        clicked JNLP) and the test process just needs to grab the
        session.
        """
        from ._oracle_forms import _attach_oracle_forms

        return _attach_oracle_forms(
            self,
            title=title,
            title_re=title_re,
            process=process,
            timeout=timeout,
        )

    def mainframe(
        self,
        *,
        host: str = "",
        port: int = 23,
        session_type: str = "3270",
        backend: str = "s3270",
        connect: bool = True,
        timeout: float = 15.0,
        ws3270_path: str | None = None,
        model: str = "3279-4",
        codepage: str | None = None,
        session_id: str = "A",
        hllapi_dll_path: str | None = None,
        extra_args: list[str] | None = None,
        trace: bool = False,
    ) -> MainframeTerminal:
        """Open a mainframe/midrange terminal session.

        Args:
            host: Hostname or IP of the TN3270/TN5250 gateway. Ignored
                when ``backend='hllapi'`` (the emulator manages the
                connection).
            port: TCP port. 23 (telnet) is standard for 3270/5250 hosts.
            session_type: ``"3270"`` for zSeries / z/OS, ``"5250"`` for
                iSeries / IBM i. s3270 negotiates the correct TN option
                automatically based on this hint.
            backend: ``"s3270"`` (spawns ws3270 subprocess, default) or
                ``"hllapi"`` (attaches to a running enterprise emulator
                through EHLLAPI).
            connect: When True (default), dial the host before returning.
                Set False to configure the backend and connect later —
                useful when the caller wants to catch connect errors
                separately.
            timeout: Seconds to wait for the initial screen after
                connecting.
            ws3270_path: Explicit path to ``ws3270`` / ``s3270`` binary.
                Only used by the s3270 backend. If omitted, PATH is
                searched, then common install directories.
            model: 3270 terminal model. ``3279-4`` = 43×80 color. Use
                ``3279-2`` for 24×80.
            codepage: EBCDIC character set for non-US hosts. Passed to
                s3270 via ``-charset``. Common values: ``"bracket"``
                (default US IBM-037), ``"german"`` (cp273),
                ``"french"`` (cp297), ``"italian"`` (cp280),
                ``"uk"`` (cp285), ``"spanish"`` (cp284), ``"japanese"``,
                ``"russian"`` (cp1025). Ignored by the HLLAPI backend
                (the emulator's own configuration wins).
            session_id: HLLAPI session letter (A, B, C, …). Ignored by
                the s3270 backend.
            hllapi_dll_path: Absolute path to an HLLAPI-compatible DLL
                (``PCSHLL32.DLL``, ``EHLAPI32.DLL``, …). Ignored by the
                s3270 backend.
            extra_args: Additional command-line arguments for the s3270
                subprocess (e.g. ``['-trace']``).
            trace: When True, every backend command + response is emitted
                via ``dolphin_desktop.get_logger("dolphin_desktop.mainframe")``
                at INFO level — invaluable when debugging why a test
                fails inside a locked keyboard or a stuck field.

        Returns:
            A :class:`~dolphin_desktop.MainframeTerminal`. Use as a
            context manager (``with desktop.mainframe(...) as term:``)
            for automatic disconnect.

        Example — connect to the pub400.com IBM i demo host::

            with desktop.mainframe(host="pub400.com", session_type="5250") as term:
                term.wait_ready()
                assert "Sign On" in term.text()
                term.field_after("User").type_text("MYUSER")
                term.press("Enter")
        """
        from ._mainframe import _build_terminal

        term = _build_terminal(
            backend=backend,
            ws3270_path=ws3270_path,
            model=model,
            codepage=codepage,
            session_id=session_id,
            hllapi_dll_path=hllapi_dll_path,
            extra_args=extra_args,
            trace=trace,
        )
        if connect:
            term.connect(host, port, session_type=session_type, timeout=timeout)
        return term

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

    # Capability introspection

    def backend_supports(self, capability: Capability) -> bool:
        """Return True iff the currently-configured backend declares *capability*.

        Uses the registered :class:`Backend` for ``self._backend``.
        Prefer this over calling operations blindly — the returned bool
        tells you whether the call would succeed BEFORE dispatch::

            desktop = Desktop(backend="image")
            if not desktop.backend_supports(Capability.INVOKE):
                pytest.skip("image backend does not implement invoke()")

        For stack-facade capabilities (SAP / CDP / Delphi / Mainframe /
        Java) query the facade directly:
        ``CDPSession.backend_supports(Capability.INVOKE)``.
        """
        from ._backend import resolve

        return resolve(self._backend).supports(capability)

    def require_capability(self, capability: Capability) -> None:
        """Raise :class:`UnsupportedCapabilityError` when the configured
        backend does not declare *capability*.

        Use in test setup or Page Object constructors to fail fast when
        a test suite is running against a backend that cannot fulfil
        its assumptions — the error message names both the backend and
        the missing capability, and its hint lists working alternatives::

            desktop.require_capability(Capability.SET_VALUE)  # raise here or continue
        """
        from ._backend import resolve

        resolve(self._backend).require_capability(capability)

    def __repr__(self) -> str:
        hidden_str = "" if self._hidden_mode is None else f", hidden={self._is_hidden}"
        return f"Desktop(backend={self._backend!r}{hidden_str})"
