"""SAP GUI for Windows automation through SAP GUI Scripting COM."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterable
from typing import Any

from ._config import get_poll_interval, get_timeout
from ._exceptions import ApplicationError, ElementNotFoundError, WaitTimeoutError
from ._helpers import _MISSING
from ._logging import get_logger

_LOG = get_logger("sap")


class Stopwatch:
    """Monotonic wall-clock timer for asserting operation duration in tests.

    Removes the need to import the stdlib ``time`` module in test files just
    to measure how long a dolphin_desktop operation took.  Without this,
    every test that asserts timing (e.g. "transaction must complete in < 15 s")
    has to manage its own ``time.monotonic()`` bookkeeping, which leaks stdlib
    imports into test files that should only depend on dolphin_desktop.

    Usage::

        sw = Stopwatch()
        sess.transaction("SE16")
        sess.wait_until_ready(timeout=15.0)
        assert sw.elapsed < 15.0, f"SE16 took {sw.elapsed:.1f}s"
    """

    def __init__(self) -> None:
        self._start = time.monotonic()

    @property
    def elapsed(self) -> float:
        """Seconds elapsed since this Stopwatch was created (or last reset)."""
        return time.monotonic() - self._start

    def reset(self) -> None:
        self._start = time.monotonic()


# Texts of the "Allow" button in the SAP GUI Scripting security dialog (all locales).
_SCRIPTING_ALLOW_TEXTS: frozenset[str] = frozenset(
    {"zezwól", "allow", "erlauben", "zulassen", "ok"}
)

# The scripting-security notification is a plain Win32 dialog (class
# "#32770") owned by the SAP GUI front end. Its caption is NOT reliable:
# some locales keep the product name ("SAP GUI Security"), others caption
# it with the front end's own — a Polish SAP Logon 800 raises it as plain
# "SAP Logon", naming the script only in the body. Since the dialog is
# modal to the scripting call, missing it blocks every attach until a
# human clicks it, so caption OR body may identify it. Owning process and
# window class must line up either way, and only a button whose text is
# an explicit "allow" is ever pressed.
_SCRIPTING_DIALOG_CLASS = "#32770"
_SCRIPTING_TITLE_MARKERS: tuple[str, ...] = ("sap gui", "sapgui", "sap-gui")
_SCRIPTING_BODY_MARKERS: tuple[str, ...] = ("script", "skript", "skrypt", "sap gui")
_SAPGUI_PROCESS_PREFIX = "sap"


# QueryFullProcessImageNameW buffer sizes, in wide chars: MAX_PATH first,
# then the 32767 limit of the extended-length \\?\ form for the retry.
_IMAGE_NAME_CAPACITIES: tuple[int, ...] = (260, 32768)


def _window_process_name(hwnd: int) -> str | None:
    """Return the lowercased image name of the process owning *hwnd*.

    ``None`` means the owning process could not be identified at all —
    ``OpenProcess`` returns ACCESS_DENIED when SAP GUI runs elevated or
    as another user, and the window may also die mid-call. Callers must
    treat that as "unknown", not as "not SAP".
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    # PROCESS_QUERY_LIMITED_INFORMATION — granted for same-user processes
    # without elevation, unlike PROCESS_QUERY_INFORMATION.
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return None
    try:
        # A first call sized at MAX_PATH fails with ERROR_INSUFFICIENT_BUFFER
        # for any longer image path, so grow the buffer and retry.
        for capacity in _IMAGE_NAME_CAPACITIES:
            size = wintypes.DWORD(capacity)
            buf = ctypes.create_unicode_buffer(capacity)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        return None
    finally:
        kernel32.CloseHandle(handle)


def _dialog_static_texts(hwnd: int, win32gui: Any) -> list[str]:
    """Return the texts of a dialog's static (label) children."""
    texts: list[str] = []

    def _child(child: int, _: Any) -> None:
        try:
            if win32gui.GetClassName(child) != "Static":
                return
            text = win32gui.GetWindowText(child)
        except Exception:
            return
        if text:
            texts.append(text)

    try:
        win32gui.EnumChildWindows(hwnd, _child, None)
    except Exception:
        pass
    return texts


def _is_scripting_security_dialog(hwnd: int, win32gui: Any) -> bool:
    """Return True only for the SAP GUI Scripting security notification."""
    if not win32gui.IsWindowVisible(hwnd):
        return False
    try:
        if win32gui.GetClassName(hwnd) != _SCRIPTING_DIALOG_CLASS:
            return False
    except Exception:
        return False
    title = win32gui.GetWindowText(hwnd) or ""
    low = title.lower()
    if not any(marker in low for marker in _SCRIPTING_TITLE_MARKERS):
        # Localized front ends caption the notification with their own
        # product name; the body still names the script.
        body = " ".join(_dialog_static_texts(hwnd, win32gui)).lower()
        if not any(marker in body for marker in _SCRIPTING_BODY_MARKERS):
            return False
    name = _window_process_name(hwnd)
    if name is None:
        # Refusing here leaves the modal on screen and the run stalls
        # behind it with no diagnostic, so fall back to the caption and
        # window-class evidence already collected above.
        _LOG.debug(
            "SAP scripting dialog %r: owning process not queryable, "
            "accepting on caption + window class",
            title,
        )
        return True
    return name.startswith(_SAPGUI_PROCESS_PREFIX)


def _scripting_security_worker(stop_event: threading.Event) -> None:
    """Background thread: dismisses SAP GUI Scripting security dialogs via Win32."""
    try:
        import win32api  # type: ignore[import-untyped]
        import win32con  # type: ignore[import-untyped]
        import win32gui  # type: ignore[import-untyped]
    except ImportError:
        return  # pywin32 not available

    def _enum(hwnd: int, _: None) -> None:
        if not _is_scripting_security_dialog(hwnd, win32gui):
            return

        # Look for OK / Allow button children (strip & accelerator prefix).
        def _find_btn(child: int, found: list) -> None:
            txt = win32gui.GetWindowText(child).strip().lstrip("&")
            if txt.lower() in _SCRIPTING_ALLOW_TEXTS:
                found.append(child)

        found: list[int] = []
        try:
            win32gui.EnumChildWindows(hwnd, _find_btn, found)
        except Exception:
            pass
        for btn in found:
            try:
                win32api.SendMessage(btn, win32con.BM_CLICK, 0, 0)
            except Exception:
                pass

    while not stop_event.is_set():
        try:
            win32gui.EnumWindows(_enum, None)
        except Exception:
            pass
        time.sleep(0.3)


_SAPGUI_PROG_ID = "SAPGUI"

_VKEYS: dict[str, int] = {
    "ENTER": 0,
    "RETURN": 0,
    "F1": 1,
    "F2": 2,
    "BACK": 3,
    "F3": 3,
    "F4": 4,
    "F5": 5,
    "F6": 6,
    "F7": 7,
    "F8": 8,
    "EXECUTE": 8,
    "F9": 9,
    "F10": 10,
    "F11": 11,
    "SAVE": 11,
    "F12": 12,
    "CANCEL": 12,
    # Shift+F1 … Shift+F10 (SAP virtual keys 13-22)
    "F13": 13,
    "SHIFT_F1": 13,
    "F14": 14,
    "SHIFT_F2": 14,
    "F15": 15,
    "SHIFT_F3": 15,
    "F16": 16,
    "SHIFT_F4": 16,
    "F17": 17,
    "SHIFT_F5": 17,
    "F18": 18,
    "SHIFT_F6": 18,
    "F19": 19,
    "SHIFT_F7": 19,
    "F20": 20,
    "SHIFT_F8": 20,
    "F21": 21,
    "SHIFT_F9": 21,
    "F22": 22,
    "SHIFT_F10": 22,
}


def _require_win32com() -> Any:
    """Import win32com.client or raise a clear RuntimeError."""
    try:
        import win32com.client  # type: ignore[import-untyped]

        return win32com.client
    except ImportError as exc:
        raise RuntimeError(
            "win32com is not available. Install pywin32: pip install pywin32  or  uv add pywin32"
        ) from exc


def _deadline(timeout: float | None) -> tuple[float, float]:
    seconds = get_timeout() if timeout is None else float(timeout)
    return seconds, time.monotonic() + seconds


def _sleep() -> None:
    time.sleep(get_poll_interval())


def _collection_item(collection: Any, index: int) -> Any:
    """Return a COM collection item using the common SAP/pywin32 shapes."""
    try:
        return collection(index)
    except Exception:
        pass
    try:
        return collection.Item(index)
    except Exception:
        pass
    return collection[index]


def _collection_count(collection: Any) -> int | None:
    for name in ("Count", "count", "Length", "length"):
        try:
            return int(getattr(collection, name))
        except Exception:
            pass
    try:
        return len(collection)
    except Exception:
        return None


def _children(obj: Any) -> Any | None:
    try:
        return obj.Children
    except Exception:
        return None


def _iter_children(obj: Any) -> Iterable[Any]:
    children = _children(obj)
    if children is None:
        return []

    count = _collection_count(children)
    if count is not None:
        result = []
        for index in range(count):
            try:
                result.append(_collection_item(children, index))
            except Exception:
                continue
        return result

    try:
        return list(children)
    except Exception:
        return []


def _get_first(obj: Any, names: tuple[str, ...]) -> Any:
    for name in names:
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if value is not None:
            return value
    return None


def _set_first(obj: Any, names: tuple[str, ...], value: Any) -> bool:
    for name in names:
        try:
            setattr(obj, name, value)
            return True
        except Exception:
            continue
    return False


def _call_first(obj: Any, names: tuple[str, ...], *args: Any) -> bool:
    """Call the first matching member on *obj*.

    Only a missing member (the ``getattr`` lookup failing) is swallowed so the
    next candidate name can be tried. If a member is found and raises while
    being called, that execution error is allowed to propagate so failed SAP
    actions (for example pressing a disabled or rejected button) are not
    silently reported as success.
    """
    for name in names:
        try:
            member = getattr(obj, name)
        except Exception:
            continue
        member(*args)
        return True
    return False


# COM/RPC HRESULTs that mean the SAP GUI process or session no longer
# exists, as opposed to a call that merely failed. Transient results such
# as RPC_E_CALL_REJECTED (0x80010001, server busy) are deliberately absent,
# and so is RPC_E_SERVERFAULT (0x80010105) — that one reports an exception
# raised by a live out-of-process server while it handled the call.
_DEAD_SESSION_HRESULTS: frozenset[int] = frozenset(
    {
        -2147417848,  # 0x80010108 RPC_E_DISCONNECTED
        -2147418105,  # 0x80010007 RPC_E_SERVER_DIED
        -2147418094,  # 0x80010012 RPC_E_SERVER_DIED_DNE
        -2147418106,  # 0x80010006 RPC_E_CONNECTION_TERMINATED
        -2147220995,  # 0x800401FD CO_E_OBJNOTCONNECTED
        -2147221021,  # 0x800401E3 MK_E_UNAVAILABLE
        -2147023174,  # 0x800706BA RPC_S_SERVER_UNAVAILABLE
        -2147023170,  # 0x800706BE RPC_S_CALL_FAILED
        -2147023169,  # 0x800706BF RPC_S_CALL_FAILED_DNE
    }
)


def _is_session_gone(exc: BaseException) -> bool:
    """Return True when *exc* means the SAP session/process has died.

    ``pywintypes.com_error`` carries the HRESULT as its first argument.
    """
    args: tuple[Any, ...] = exc.args
    return bool(args) and isinstance(args[0], int) and args[0] in _DEAD_SESSION_HRESULTS


def _raise_if_session_gone(exc: BaseException, operation: str) -> None:
    """Convert a dead-session COM failure into a clear ApplicationError.

    Readers such as ``is_busy`` or ``status_message`` otherwise report the
    "everything is fine" value for a session that no longer exists, which
    turns ``wait_until_ready`` / ``assert_no_error`` into silent passes.
    """
    if _is_session_gone(exc):
        raise ApplicationError(
            f"SAP session is no longer available ({operation} failed: {exc})",
            hint=(
                "the SAP GUI process exited or the session was closed — "
                "reconnect with SapGui.connect() and re-resolve the session"
            ),
        ) from exc


def _get_scripting_engine(sap_gui: Any) -> Any:
    engine = sap_gui.GetScriptingEngine
    if callable(engine):
        try:
            _ = engine.Children
        except Exception:
            return engine()
    return engine


def _bool_attr(obj: Any, names: tuple[str, ...], default: bool) -> bool:
    value = _get_first(obj, names)
    if value is None:
        return default
    return bool(value)


_ROW_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


def _parse_selected_rows(raw: str) -> list[int]:
    """Expand a SAP ``SelectedRows`` string into individual row indices.

    ``GuiGridView.SelectedRows`` uses run-length range syntax — ``"3,5-8,14"``
    means rows 3, 5, 6, 7, 8 and 14 — so contiguous selections arrive as a
    range rather than as one entry per row.
    """
    result: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        match = _ROW_RANGE_RE.match(part)
        if match is not None:
            start, end = int(match.group(1)), int(match.group(2))
            if end < start:
                start, end = end, start
            result.extend(range(start, end + 1))
        elif part.isdigit():
            result.append(int(part))
    return result


def _component_text(component: Any) -> str:
    value = _get_first(component, ("Text", "text", "Value", "value", "Name", "name"))
    return "" if value is None else str(value)


class SapGui:
    """Entry point for SAP GUI for Windows automation.

    This wrapper attaches to the SAP GUI Scripting engine exposed by a running
    SAP GUI for Windows process. SAP GUI Scripting must be enabled in the SAP
    client and allowed by the target SAP system.
    """

    #: Fronts the SapBackend (SAP GUI Scripting COM stack).
    backend_id: str = "sap"

    @classmethod
    def backend(cls):
        from ._backend import resolve as _resolve

        return _resolve(cls.backend_id)

    @classmethod
    def backend_supports(cls, capability) -> bool:
        return cls.backend().supports(capability)

    @classmethod
    def require_capability(cls, capability) -> None:
        cls.backend().require_capability(capability)

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @classmethod
    def connect(cls, *, timeout: float | None = None) -> SapGui:
        seconds, end = _deadline(timeout)
        last_exc: Exception | None = None

        while True:
            try:
                client = _require_win32com()
                sap_gui = client.GetObject(_SAPGUI_PROG_ID)
                return cls(_get_scripting_engine(sap_gui))
            except Exception as exc:
                last_exc = exc
                if time.monotonic() >= end:
                    raise ApplicationError(
                        "Could not connect to SAP GUI Scripting engine after "
                        f"{seconds:g}s. Ensure SAP GUI for Windows is running and "
                        "SAP GUI Scripting is enabled on both the client and SAP system."
                    ) from last_exc
                _sleep()

    @property
    def raw(self) -> Any:
        return self._com

    @staticmethod
    def scripting_security_handler() -> threading.Event:
        """Start a background thread that auto-dismisses SAP GUI Scripting security dialogs.

        Only the SAP GUI security-notification dialog is touched: the
        window must belong to a SAP front-end process, carry the standard
        dialog class, and name SAP GUI in its caption. Dialogs from any
        other application on the desktop are left alone.

        Returns a stop event — call ``stop_event.set()`` when done.
        """
        stop_event = threading.Event()
        t = threading.Thread(
            target=_scripting_security_worker,
            args=(stop_event,),
            daemon=True,
        )
        t.start()
        return stop_event

    @staticmethod
    def is_server_reachable(
        host: str = "127.0.0.1",
        port: int = 3200,
        *,
        timeout: float = 3.0,
    ) -> bool:
        """Return True when the SAP application server is reachable on *host*:*port*.

        Uses a raw TCP connect so no SAP GUI process needs to be running yet.
        Typical usage in a test::

            if not SapGui.is_server_reachable():
                pytest.skip("SAP server unavailable")
        """
        import socket as _socket

        try:
            with _socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def wait_for_any_connection(self, timeout: float = 30.0) -> None:
        """Wait until at least one connection appears in the SAP GUI engine.

        ``keyboard_login()`` submits credentials but the SAP session registers
        in the scripting engine only after the server processes the login —
        an inherently asynchronous step.  Without this method every test that
        uses keyboard login must poll ``connections()`` with a raw
        ``time.monotonic()`` deadline, forcing a stdlib ``time`` import into
        test files that should only depend on dolphin_desktop.
        """
        seconds, end = _deadline(timeout)
        while True:
            if self.connections():
                return
            if time.monotonic() >= end:
                raise WaitTimeoutError(f"No SAP connection appeared after {seconds:g}s")
            _sleep()

    @staticmethod
    def keyboard_login(win_title: str, user: str, password: str) -> None:
        """Type SAP login credentials via keyboard — no mouse movement.

        SAP login auto-advances focus from the pre-filled Client field directly
        to the User (BNAME) field.  We type *user* into BNAME, Tab to BCODE
        (Password), type *password*, then Enter.

        Raises ApplicationError if the SAP login window cannot be found.
        """
        try:
            from pywinauto import Application as _PwApp
            from pywinauto.keyboard import send_keys as _send_keys
        except ImportError as exc:
            raise ApplicationError("pywinauto not available") from exc

        app = _PwApp(backend="win32")
        try:
            app.connect(title=win_title, timeout=15)
        except Exception as exc:
            raise ApplicationError(f"Cannot connect to '{win_title}': {exc}") from exc

        dlg = app.window(title=win_title)
        # Bring to foreground — pywinauto uses AttachThreadInput internally.
        dlg.set_focus()
        time.sleep(1.0)

        # Focus is on BNAME (User) — SAP auto-advances past pre-filled Client.
        # Type user → Tab → type password → Enter.
        _send_keys(user, with_spaces=True, pause=0.05)
        time.sleep(0.1)
        _send_keys("{TAB}")
        time.sleep(0.1)
        _send_keys(password, with_spaces=True, pause=0.05)
        time.sleep(0.1)
        _send_keys("{ENTER}")

    def connection(self, index: int = 0) -> SapConnection:
        """Return a SAP GUI connection by zero-based index."""
        return SapConnection(_collection_item(self._com.Children, index))

    def connections(self) -> list[SapConnection]:
        count = _collection_count(self._com.Children)
        if count is None:
            return []
        return [self.connection(index) for index in range(count)]

    def close_all_connections(self) -> None:
        try:
            n = self._com.Children.Count
        except Exception:
            return
        for i in range(n - 1, -1, -1):
            try:
                self._com.Children(i).CloseConnection()
            except Exception:
                pass

    def open_connection(self, name: str, *, timeout: float | None = None) -> SapSession | None:
        """Open a named SAP system connection.

        Calls ``GuiApplication.OpenConnection`` (sync) so the SAP login window
        appears before this method returns.  If the scripting session is
        accessible (server-side scripting enabled), returns the
        :class:`SapSession`; otherwise returns ``None`` so the caller can fall
        back to keyboard / UIA login.
        """
        com_conn = self._com.OpenConnection(name, True)
        _, end = _deadline(timeout or 10.0)
        while True:
            # Try the returned connection's children first.
            try:
                return SapSession(_collection_item(com_conn.Children, 0))
            except Exception:
                pass
            # Fallback: scan all engine connections.
            try:
                n = self._com.Children.Count
                for i in range(n):
                    try:
                        return SapSession(_collection_item(self._com.Children(i).Children, 0))
                    except Exception:
                        continue
            except Exception:
                pass
            if time.monotonic() >= end:
                # Scripting session not accessible — caller should use UIA/keyboard.
                return None
            _sleep()

    def session(self, *, connection: int = 0, session: int = 0) -> SapSession:
        """Return a SAP GUI session by zero-based connection and session index."""
        return self.connection(connection).session(session)

    @property
    def version(self) -> str:
        try:
            return str(self._com.Version)
        except Exception:
            return ""


class SapConnection:
    """Wrapper around a SAP GUI Scripting connection."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def raw(self) -> Any:
        return self._com

    @property
    def description(self) -> str:
        """Return the connection description (SAP system name / instance)."""
        try:
            return str(self._com.Description)
        except Exception:
            return ""

    def close(self) -> None:
        try:
            self._com.CloseConnection()
        except Exception:
            pass

    def session(self, index: int = 0) -> SapSession:
        """Return a session from this connection by zero-based index."""
        return SapSession(_collection_item(self._com.Children, index))

    def sessions(self) -> list[SapSession]:
        count = _collection_count(self._com.Children)
        if count is None:
            return []
        return [self.session(index) for index in range(count)]

    def wait_for_session_count(self, expected: int, timeout: float = 5.0) -> None:
        """Wait until this connection has exactly *expected* sessions.

        SAP GUI Scripting's CreateSession() and the /i command that closes a
        session are asynchronous — the Children collection does not update
        synchronously with the call.  Without this method every test that opens
        or closes a session is forced to sprinkle raw time.sleep() calls and
        import the stdlib time module just to paper over the race.  Keeping the
        poll here lets tests express intent ("wait until there are N sessions")
        instead of "sleep and hope", and ensures the wait uses the same
        configurable poll interval as every other dolphin_desktop wait helper.
        """
        seconds, end = _deadline(timeout)
        while True:
            if len(self.sessions()) == expected:
                return
            if time.monotonic() >= end:
                raise WaitTimeoutError(
                    f"Session count did not reach {expected} after {seconds:g}s; "
                    f"current={len(self.sessions())}"
                )
            _sleep()

    def create_session(self) -> None:
        """Request a new SAP session on this connection.

        The new session appears asynchronously in the connection's session
        list.  Pair with ``wait_for_session_count`` to block until it is
        accessible::

            n = len(conn.sessions())
            conn.create_session()
            conn.wait_for_session_count(n + 1)
            new_sess = conn.session(n)
        """
        try:
            sess_com = _collection_item(self._com.Children, 0)
            if not _call_first(sess_com, ("CreateSession", "createSession")):
                raise ApplicationError("SAP session does not expose CreateSession")
        except ApplicationError:
            raise
        except Exception as exc:
            raise ApplicationError(f"Cannot create new SAP session: {exc}") from exc


class SapSession:
    """Wrapper around a SAP GUI Scripting session."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def raw(self) -> Any:
        return self._com

    def find_by_id(self, component_id: str, *, timeout: float | None = None) -> SapLocator:
        """Return a lazy locator for a SAP component ID such as ``wnd[0]/usr/...``."""
        return SapLocator(self, id=component_id, timeout=timeout)

    def locator(
        self,
        *,
        id: str | None = None,
        name: str | None = None,
        type: str | None = None,
        timeout: float | None = None,
    ) -> SapLocator:
        """Return a lazy SAP locator by component ID, name, type, or a combination."""
        if id is None and name is None and type is None:
            raise ValueError("SAP locator requires at least one of: id, name, type")
        return SapLocator(self, id=id, name=name, type=type, timeout=timeout)

    def wait_until_ready(self, timeout: float | None = None) -> SapSession:
        seconds, end = _deadline(timeout)
        while True:
            if not self.is_busy():
                return self
            if time.monotonic() >= end:
                raise WaitTimeoutError(f"SAP session still busy after {seconds:g}s")
            _sleep()

    def wait_for_title(
        self, expected: str, *, exact: bool = False, timeout: float | None = None
    ) -> SapSession:
        """Wait until the main window title contains (or equals) *expected*.

        Set *exact=True* for an exact match instead of a substring check.

        Empty ``expected`` with ``exact=False`` is rejected — ``"" in any``
        is universally True, which would silent-succeed regardless of the
        actual SAP title. Empty exact match is a legit "wait until title
        is cleared" case and stays allowed.
        """
        if expected == "" and not exact:
            raise ValueError(
                "wait_for_title(expected='', exact=False) always matches "
                "immediately — pass a real substring or use exact=True to "
                "wait for an exactly-empty title"
            )
        seconds, end = _deadline(timeout)
        while True:
            current = self.title()
            if (exact and current == expected) or (not exact and expected in current):
                return self
            if time.monotonic() >= end:
                raise WaitTimeoutError(
                    f"SAP title did not {'equal' if exact else 'contain'} {expected!r} "
                    f"after {seconds:g}s; last title={current!r}"
                )
            _sleep()

    def is_busy(self) -> bool:
        try:
            return bool(self._com.Busy)
        except Exception as exc:
            _raise_if_session_gone(exc, "is_busy")
            return False

    def send_vkey(self, key: int | str, *, timeout: float | None = None) -> SapSession:
        """Send a SAP virtual key to the main window.

        Common string aliases include ``"ENTER"``, ``"F3"``, ``"F8"``,
        ``"SAVE"``, and ``"CANCEL"``. Integers are passed through directly.
        """
        vkey = _normalize_vkey(key)
        self.wait_until_ready(timeout=timeout)
        window = self._find_now("wnd[0]")
        if not _call_first(window, ("SendVKey", "sendVKey"), vkey):
            raise ApplicationError("SAP main window does not expose SendVKey")
        self.wait_until_ready(timeout=timeout)
        return self

    def current_transaction(self) -> str:
        """Return the currently active SAP transaction code (e.g. ``'SE16'``)."""
        try:
            return str(self._com.Info.Transaction)
        except Exception as exc:
            _raise_if_session_gone(exc, "current_transaction")
            return ""

    def title(self) -> str:
        """Return the title text of the active SAP screen (wnd[0].Text)."""
        try:
            return str(self._find_now("wnd[0]").Text)
        except Exception as exc:
            _raise_if_session_gone(exc, "title")
            return ""

    def status_message(self) -> tuple[str, str]:
        """Return ``(text, type)`` from the SAP status bar.

        *type* is one of ``'S'`` (success), ``'W'`` (warning), ``'E'`` (error),
        ``'A'`` (abend), or ``''`` (no message / info).

        Raises :class:`ApplicationError` when the session itself is gone —
        an empty result there would let ``assert_no_error`` pass against a
        dead SAP GUI.
        """
        try:
            sbar = self._find_now("wnd[0]/sbar")
            text = _component_text(sbar)
            msg_type = _get_first(sbar, ("MessageType", "messageType"))
            return text, "" if msg_type is None else str(msg_type)
        except Exception as exc:
            _raise_if_session_gone(exc, "status_message")
            return "", ""

    def confirm_popup(
        self, *, button_id: str | None = None, timeout: float | None = None
    ) -> SapSession:
        """Confirm the active SAP popup dialog.

        If *button_id* is given (e.g. ``"wnd[1]/usr/btnBUTTON_1"``), that
        button is clicked.  Otherwise the first button found in ``wnd[1]`` is
        pressed, which is the default confirm action in most SAP dialogs.
        """
        self.wait_until_ready(timeout=timeout)
        if button_id:
            self.find_by_id(button_id, timeout=timeout).click()
        else:
            try:
                popup = self._find_now("wnd[1]")
                children = _iter_children(popup)
                for child in children:
                    if _call_first(child, ("Press", "press", "Click", "click")):
                        break
                else:
                    # No pressable child — press Enter to confirm
                    self.send_vkey(0, timeout=timeout)
            except Exception:
                self.send_vkey(0, timeout=timeout)
        self.wait_until_ready(timeout=timeout)
        return self

    def close_popup(self, *, timeout: float | None = None) -> SapSession:
        """Close the active SAP popup dialog with Cancel / F12.

        Sends F12 to wnd[1] directly when it exists (required for modal windows);
        falls back to wnd[0] otherwise.
        """
        self.wait_until_ready(timeout=timeout)
        try:
            modal = self._find_now("wnd[1]")
            if not _call_first(modal, ("SendVKey", "sendVKey"), 12):
                raise ApplicationError("wnd[1] does not expose SendVKey")
            self.wait_until_ready(timeout=timeout)
            return self
        except Exception:
            pass
        self.send_vkey("F12", timeout=timeout)
        self.wait_until_ready(timeout=timeout)
        return self

    # Navigation shortcuts  (readable aliases for the most common vkeys)

    def enter(self, *, timeout: float | None = None) -> SapSession:
        """Send Enter (vkey 0) — confirm input or open a screen."""
        return self.send_vkey(0, timeout=timeout)

    def execute(self, *, timeout: float | None = None) -> SapSession:
        """Send F8 — execute / run a report or transaction."""
        return self.send_vkey("F8", timeout=timeout)

    def save(self, *, timeout: float | None = None) -> SapSession:
        """Send F11 — save the current document."""
        return self.send_vkey("F11", timeout=timeout)

    def navigate_back(self, *, timeout: float | None = None) -> SapSession:
        """Send F3 — navigate one step back."""
        return self.send_vkey("F3", timeout=timeout)

    def cancel(self, *, timeout: float | None = None) -> SapSession:
        """Send F12 — cancel / close a popup."""
        return self.send_vkey("F12", timeout=timeout)

    def logoff(self, *, confirm: bool = True) -> None:
        """Log off the current SAP session.

        Runs the ``/nex`` command, which ends the session without asking
        for unsaved data. If *confirm* is True the confirmation popup is
        dismissed automatically. After calling this the session is invalid.
        """
        try:
            self.transaction("/nex")
        except Exception:
            pass
        if confirm:
            try:
                self.dismiss_all_popups(vkey=0)
            except Exception:
                pass

    # Assertion helpers

    def assert_no_error(self, *, timeout: float | None = None) -> SapSession:
        """Assert the SAP status bar does not show an error or abend.

        Polls until the session is no longer busy, then checks that the
        status bar type is not ``'E'`` or ``'A'``::

            sess.save()
            sess.assert_no_error()
        """
        self.wait_until_ready(timeout=timeout)
        msg, msg_type = self.status_message()
        if msg_type in ("E", "A"):
            raise AssertionError(f"SAP reported an error: {msg!r} (type={msg_type!r})")
        return self

    def assert_field_value(
        self,
        field_id: str,
        expected: str,
        *,
        partial: bool = False,
        case_sensitive: bool = False,
        timeout: float | None = None,
    ) -> SapSession:
        """Assert that a field's value matches *expected*.

        By default an exact, case-insensitive comparison is performed.
        Set *partial=True* for a substring check::

            sess.assert_field_value("wnd[0]/usr/ctxtMATNR", "100-100")
            sess.assert_field_value("wnd[0]/usr/txtNAME1", "GmbH", partial=True)

        Empty ``expected`` with ``partial=True`` is rejected — `"" in x`
        is universally True, so the assertion would silently pass for
        any field content. Exact-empty match (assert field is cleared)
        stays supported via ``partial=False``.
        """
        if expected == "" and partial:
            raise ValueError(
                "assert_field_value(expected='', partial=True) always matches "
                "immediately — pass a real substring or use partial=False to "
                "assert the field is cleared"
            )
        actual = self.find_by_id(field_id, timeout=timeout).text()
        a = actual if case_sensitive else actual.upper()
        e = expected if case_sensitive else expected.upper()
        ok = (e in a) if partial else (e == a)
        if not ok:
            raise AssertionError(
                f"Field {field_id!r}: expected {expected!r} "
                f"({'contains' if partial else 'equals'}), got {actual!r}"
            )
        return self

    def wait_until_no_error(self, *, timeout: float | None = None) -> SapSession:
        """Wait until the SAP status bar is clear of errors.

        Useful after correcting invalid input — polls until the status bar
        type is no longer ``'E'`` or ``'A'``::

            sess.find_by_id(field).set_text(corrected_value)
            sess.enter()
            sess.wait_until_no_error(timeout=5.0)
        """
        seconds, end = _deadline(timeout)
        while True:
            self.wait_until_ready(timeout=timeout)
            _, msg_type = self.status_message()
            if msg_type not in ("E", "A"):
                return self
            if time.monotonic() >= end:
                msg, _ = self.status_message()
                raise WaitTimeoutError(
                    f"SAP status bar still shows error after {seconds:g}s: {msg!r}"
                )
            _sleep()

    def execute_report(
        self,
        program: str,
        *,
        variant: str | None = None,
        timeout: float | None = None,
    ) -> SapSession:
        """Open SA38, enter *program*, optionally load *variant*, then execute.

        Encapsulates the repetitive 3–4 step sequence needed to run any
        ABAP report from a test::

            sess.execute_report("RFUMSV00")
            sess.execute_report("RFUMSV00", variant="MONTHLY")
        """
        self.transaction("SA38", timeout=timeout)
        self.wait_until_ready(timeout=timeout)
        fld = self.find_by_id_or_variant(
            "wnd[0]/usr/ctxtRS38M-PROGRAMM",
            "wnd[0]/usr/txtRS38M-PROGRAMM",
            timeout=timeout,
        )
        fld.set_text(program)
        if variant:
            try:
                var_fld = self.find_by_id("wnd[0]/usr/ctxtRS38M-VARIANT", timeout=2.0)
                if var_fld.exists(timeout=1.0):
                    var_fld.set_text(variant)
            except Exception:
                pass
        self.execute(timeout=timeout)
        self.wait_until_ready(timeout=timeout)
        return self

    def transaction(self, code: str, *, timeout: float | None = None) -> SapSession:
        """Start a SAP transaction through the OK Code field."""
        tx = code.strip()
        if not tx:
            raise ValueError("SAP transaction code cannot be empty")
        if not tx.startswith(("/", "=")):
            tx = f"/n{tx}"
        self.find_by_id("wnd[0]/tbar[0]/okcd", timeout=timeout).set_text(tx)
        self.send_vkey(0, timeout=timeout)
        return self

    def find_first_in_tree(
        self,
        component_type: str,
        *,
        parent_id: str = "wnd[0]",
        timeout: float | None = None,
    ) -> SapLocator:
        """Return a locator for the first descendant with the given SAP component type.

        Tests repeatedly define local _find_tabstrip() / _find_all_by_type() helpers
        because the library had no public tree-search API.  This method centralises
        the cycle-safe recursive walk so tests can do::

            ts  = sess.find_first_in_tree("GuiTabStrip")
            grd = sess.find_first_in_tree("GuiGridView", parent_id="wnd[0]/usr")
        """
        seconds, end = _deadline(timeout)
        while True:
            try:
                if not self.is_busy():
                    root = self._find_now(parent_id)
                    comp = _find_in_tree(root, name=None, type=component_type, seen=set(), depth=30)
                    if comp is not None:
                        found_id = str(getattr(comp, "Id", ""))
                        return SapLocator(
                            self,
                            id=found_id or None,
                            type=component_type,
                            timeout=timeout,
                        )
            except Exception:
                pass
            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"SAP component type={component_type!r} not found under "
                    f"{parent_id!r} after {seconds:g}s"
                )
            _sleep()

    def find_all_in_tree(
        self,
        component_type: str,
        *,
        parent_id: str = "wnd[0]",
        timeout: float | None = None,
    ) -> list[SapLocator]:
        """Return locators for all descendants with the given SAP component type.

        Counterpart to find_first_in_tree for cases where multiple matching
        components exist (e.g. all GuiTextField fields on a screen).
        """
        seconds, end = _deadline(timeout)
        while True:
            try:
                if not self.is_busy():
                    root = self._find_now(parent_id)
                    raw: list[Any] = []
                    _find_all_in_tree(root, type=component_type, seen=set(), depth=30, result=raw)
                    return [
                        SapLocator(
                            self,
                            id=str(getattr(c, "Id", "")) or None,
                            type=component_type,
                            timeout=timeout,
                        )
                        for c in raw
                    ]
            except Exception:
                pass
            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"SAP tree search for type={component_type!r} failed after {seconds:g}s"
                )
            _sleep()

    def find_by_id_or_variant(self, *ids: str, timeout: float | None = None) -> SapLocator:
        """Return the first existing locator from a list of candidate component IDs.

        SAP component IDs differ between system versions and screen layouts.
        Without this method tests must write explicit for-loops over candidate IDs,
        cluttering test logic with version-compatibility code::

            # Before
            for fid in ("wnd[0]/usr/ctxtSUID_ST_BNAME-BNAME",
                        "wnd[0]/usr/ctxtQ_BNAME-LOW"):
                if sess.find_by_id(fid).exists(timeout=2.0):
                    field = sess.find_by_id(fid)
                    break

            # After
            field = sess.find_by_id_or_variant(
                "wnd[0]/usr/ctxtSUID_ST_BNAME-BNAME",
                "wnd[0]/usr/ctxtQ_BNAME-LOW",
            )
        """
        if not ids:
            raise ValueError("At least one component ID is required")
        seconds, end = _deadline(timeout)
        while True:
            for candidate_id in ids:
                try:
                    self._find_now(candidate_id)
                    return SapLocator(self, id=candidate_id, timeout=timeout)
                except Exception:
                    continue
            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"None of the candidate SAP IDs found after {seconds:g}s: {list(ids)}"
                )
            _sleep()

    def dismiss_all_popups(
        self,
        *,
        vkey: int | str = 0,
        max_attempts: int = 5,
        timeout: float | None = None,
    ) -> SapSession:
        """Dismiss all SAP popup dialogs (wnd[1] and above) up to *max_attempts* times.

        SAP operations often chain multiple confirmation dialogs (SE38 activate →
        package selection → workbench confirmation).  Without this method every
        test must decide how many iterations to allow and manage the dismiss loop
        manually.  The pattern::

            for _ in range(3):
                if sess.find_by_id("wnd[1]").exists(timeout=2.0):
                    sess._com.FindById("wnd[1]").SendVKey(0)
                    sess.wait_until_ready(...)
                else:
                    break

        appears 10+ times across the test suite.  Use instead::

            sess.dismiss_all_popups()
            sess.dismiss_all_popups(vkey="F12")   # Cancel instead of Enter
        """
        vkey_int = _normalize_vkey(vkey)
        for _ in range(max_attempts):
            self.wait_until_ready(timeout=timeout)
            try:
                popup = self._find_now("wnd[1]")
                if not _call_first(popup, ("SendVKey", "sendVKey"), vkey_int):
                    break
                self.wait_until_ready(timeout=timeout)
            except Exception:
                break
        return self

    def click_tab_by_label(
        self,
        label: str,
        *,
        partial: bool = True,
        case_sensitive: bool = False,
        parent_id: str = "wnd[0]",
        timeout: float | None = None,
    ) -> SapSession:
        """Find a GuiTabStrip and click the tab whose text matches *label*.

        Tests repeatedly reimplement _find_tabstrip() + Children iteration to
        click a tab by its label.  Example::

            sess.click_tab_by_label("Fields")       # partial match (default)
            sess.click_tab_by_label("Felder", partial=False)  # exact match
        """
        seconds, end = _deadline(timeout)
        label_cmp = label if case_sensitive else label.upper()
        while True:
            try:
                if not self.is_busy():
                    root = self._find_now(parent_id)
                    ts = _find_in_tree(root, name=None, type="GuiTabStrip", seen=set(), depth=30)
                    if ts is not None:
                        count = _collection_count(ts.Children)
                        for i in range(count or 0):
                            try:
                                tab = _collection_item(ts.Children, i)
                                txt = str(getattr(tab, "Text", ""))
                                txt_cmp = txt if case_sensitive else txt.upper()
                                hit = (label_cmp in txt_cmp) if partial else (label_cmp == txt_cmp)
                                if hit:
                                    try:
                                        tab.Select()
                                    except Exception:
                                        tab.Press()
                                    self.wait_until_ready(timeout=timeout)
                                    return self
                            except Exception:
                                continue
            except Exception:
                pass
            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"SAP tab {label!r} not found under {parent_id!r} after {seconds:g}s"
                )
            _sleep()

    def menu_click(
        self,
        path: str | list[str],
        *,
        partial: bool = True,
        case_sensitive: bool = False,
        timeout: float | None = None,
    ) -> SapSession:
        """Navigate the menu bar and click a menu item by label path.

        Tests that trigger SAP actions through the menu bar (e.g. Program → Delete)
        must currently iterate raw COM Children looking for text matches.
        This method encapsulates that pattern::

            sess.menu_click(["Program", "Delete"])
            sess.menu_click("Program > Delete")    # string shorthand
            sess.menu_click("Edit > Find")
        """
        if isinstance(path, str):
            labels = [p.strip() for p in path.split(">")]
        else:
            labels = list(path)
        if not labels:
            raise ValueError("menu_click requires at least one label")

        def _match_child(parent: Any, lbl: str) -> Any | None:
            lbl_cmp = lbl if case_sensitive else lbl.upper()
            count = _collection_count(parent.Children)
            for i in range(count or 0):
                try:
                    item = _collection_item(parent.Children, i)
                    txt = str(getattr(item, "Text", ""))
                    txt_cmp = txt if case_sensitive else txt.upper()
                    hit = (lbl_cmp in txt_cmp) if partial else (lbl_cmp == txt_cmp)
                    if hit:
                        return item
                except Exception:
                    continue
            return None

        seconds, end = _deadline(timeout)
        while True:
            try:
                if not self.is_busy():
                    node = self._find_now("wnd[0]/mbar")
                    for lbl in labels:
                        node = _match_child(node, lbl)
                        if node is None:
                            raise ElementNotFoundError(f"Menu item {lbl!r} not found")
                    if _call_first(node, ("Select", "select")):
                        self.wait_until_ready(timeout=timeout)
                        return self
                    raise ApplicationError(f"Menu item {labels[-1]!r} does not expose Select")
            except (ElementNotFoundError, ApplicationError):
                pass
            except Exception:
                pass
            if time.monotonic() >= end:
                raise ElementNotFoundError(f"SAP menu path {labels!r} not found after {seconds:g}s")
            _sleep()

    def maximize(self) -> SapSession:
        try:
            wnd = self._find_now("wnd[0]")
            if not _call_first(wnd, ("Maximize", "maximize")):
                raise ApplicationError("SAP main window does not expose Maximize")
            self.wait_until_ready()
        except ApplicationError:
            raise
        except Exception:
            pass
        return self

    def restore(self) -> SapSession:
        """Restore the SAP main window from maximized state."""
        try:
            wnd = self._find_now("wnd[0]")
            _call_first(wnd, ("Restore", "restore"))
            self.wait_until_ready()
        except Exception:
            pass
        return self

    def set_window_size(self, width: int, height: int) -> SapSession:
        """Resize the SAP main window to *width* × *height* pixels."""
        try:
            wnd = self._find_now("wnd[0]")
            wnd.Width = width
            wnd.Height = height
            self.wait_until_ready()
        except Exception as exc:
            raise ApplicationError(f"Cannot resize SAP window: {exc}") from exc
        return self

    def close(self) -> None:
        """Close this SAP session using the /i command.

        If this is the last session on the connection, SAP also closes the
        connection.  The SapSession object is invalid after calling this.
        """
        try:
            self._find_now("wnd[0]/tbar[0]/okcd").Text = "/i"
            self._find_now("wnd[0]").SendVKey(0)
        except Exception:
            pass

    def create_session(self) -> None:
        """Request a new SAP session on the same connection.

        The new session appears asynchronously.  Pair with
        ``connection.wait_for_session_count(n + 1)`` to block until it is
        accessible::

            n = len(conn.sessions())
            sess.create_session()
            conn.wait_for_session_count(n + 1)
            new_sess = conn.session(n)
        """
        if not _call_first(self._com, ("CreateSession", "createSession")):
            raise ApplicationError("SAP session does not expose CreateSession")

    def system_info(self) -> dict[str, str]:
        """Return session metadata as a plain dict.

        Useful for logging in CI, for asserting the correct client, and for
        debugging test failures::

            info = sess.system_info()
            assert info["client"] == "001"
            print(info["user"], info["system"])
        """
        try:
            info = self._com.Info
            return {
                "user": str(getattr(info, "User", "") or ""),
                "client": str(getattr(info, "Client", "") or ""),
                "system": str(getattr(info, "SystemName", "") or ""),
                "transaction": str(getattr(info, "Transaction", "") or ""),
                "language": str(getattr(info, "Language", "") or ""),
                "host": str(getattr(info, "ApplicationServer", "") or ""),
                "program": str(getattr(info, "Program", "") or ""),
            }
        except Exception as exc:
            _raise_if_session_gone(exc, "system_info")
            return {}

    def wait_for_popup(self, *, timeout: float | None = None) -> SapSession:
        """Wait until a SAP popup dialog (wnd[1]) appears.

        Useful after operations that asynchronously raise a confirmation or
        error dialog::

            sess.send_vkey("F8")
            sess.wait_for_popup(timeout=10.0)
            sess.popup_click_button("Yes")
        """
        seconds, end = _deadline(timeout)
        while True:
            try:
                self._find_now("wnd[1]")
                return self
            except Exception:
                pass
            if time.monotonic() >= end:
                raise WaitTimeoutError(f"SAP popup (wnd[1]) did not appear after {seconds:g}s")
            _sleep()

    def popup_click_button(
        self,
        title: str,
        *,
        partial: bool = True,
        case_sensitive: bool = False,
        timeout: float | None = None,
    ) -> SapSession:
        """Find and click a named button inside a SAP popup dialog.

        ``dismiss_all_popups`` can only send virtual keys (Enter / F12).
        When a dialog has explicit "Yes" / "No" / "Continue" buttons that
        must be chosen by label, use this method::

            sess.popup_click_button("Yes")
            sess.popup_click_button("Ja")            # German locale
            sess.popup_click_button("Cont", partial=True)
        """
        seconds, end = _deadline(timeout)
        while True:
            try:
                if not self.is_busy():
                    for popup_id in ("wnd[1]", "wnd[2]", "wnd[3]"):
                        try:
                            popup = self._find_now(popup_id)
                            btn = _find_by_text(
                                popup,
                                text=title,
                                comp_type="GuiButton",
                                partial=partial,
                                case_sensitive=case_sensitive,
                                seen=set(),
                                depth=10,
                            )
                            if btn is not None:
                                _call_first(btn, ("Press", "press", "Click", "click"))
                                self.wait_until_ready(timeout=timeout)
                                return self
                        except Exception:
                            continue
            except Exception:
                pass
            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"Button {title!r} not found in any popup after {seconds:g}s"
                )
            _sleep()

    def click_toolbar_button(
        self,
        title: str,
        *,
        parent_id: str = "wnd[0]",
        partial: bool = True,
        case_sensitive: bool = False,
        timeout: float | None = None,
    ) -> SapSession:
        """Find and click a toolbar button by its visible label.

        SAP toolbar button IDs (``tbar[1]/btn[8]``) differ between
        transactions and screen layouts.  This method searches the component
        tree for a button whose text matches *title*::

            sess.click_toolbar_button("Execute")
            sess.click_toolbar_button("Refresh")
            sess.click_toolbar_button("Export", parent_id="wnd[0]/usr")
        """
        seconds, end = _deadline(timeout)
        while True:
            try:
                if not self.is_busy():
                    root = self._find_now(parent_id)
                    btn = _find_by_text(
                        root,
                        text=title,
                        comp_type="GuiButton",
                        partial=partial,
                        case_sensitive=case_sensitive,
                        seen=set(),
                        depth=15,
                    )
                    if btn is not None:
                        _call_first(btn, ("Press", "press", "Click", "click"))
                        self.wait_until_ready(timeout=timeout)
                        return self
            except Exception:
                pass
            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"Toolbar button {title!r} not found under {parent_id!r} after {seconds:g}s"
                )
            _sleep()

    def set_selection_value(self, field_name: str, value: str) -> SapSession:
        """Set a parameter or select-option low value on a selection screen.

        Tries the standard SAP selection-screen ID variants
        (``ctxt<NAME>-LOW``, ``txt<NAME>-LOW``, ``ctxt<NAME>`` …) so tests
        do not need to hardcode exact field IDs::

            sess.set_selection_value("S_MATNR", "MAT-001")
            sess.set_selection_value("PA_DATE", "01.01.2024")
        """
        fid = self._selection_field_id(field_name, high=False)
        if fid is None:
            raise ElementNotFoundError(
                f"Selection screen field {field_name!r} not found on wnd[0]/usr"
            )
        self.find_by_id(fid).set_text(value)
        return self

    def set_selection_range(
        self,
        field_name: str,
        *,
        low: str = "",
        high: str = "",
    ) -> SapSession:
        """Set a Between (BT) range on a selection-screen select-option.

        Sets the Low and/or High value fields.  Omit *high* to set only
        the Low value (equivalent to ``set_selection_value``)::

            sess.set_selection_range("S_BUDAT", low="01.01.2024", high="31.12.2024")
        """
        if low:
            fid = self._selection_field_id(field_name, high=False)
            if fid is None:
                raise ElementNotFoundError(f"Selection screen low field {field_name!r} not found")
            self.find_by_id(fid).set_text(low)
        if high:
            fid = self._selection_field_id(field_name, high=True)
            if fid is None:
                raise ElementNotFoundError(f"Selection screen high field {field_name!r} not found")
            self.find_by_id(fid).set_text(high)
        return self

    def open_selection_options(
        self,
        field_name: str,
        *,
        timeout: float | None = None,
    ) -> SapSession:
        """Open the multi-value / ranges popup for a select-option field.

        Clicks the arrow button next to a select-option field — the button
        whose ID follows the pattern ``%_<FIELD>_%_APP_%-VALU_PUSH``::

            sess.open_selection_options("S_MATNR")
            # wnd[1] now shows the multi-value entry table
        """
        btn_id = f"wnd[0]/usr/btn%_{field_name}_%_APP_%-VALU_PUSH"
        try:
            self._find_now(btn_id)
            self.find_by_id(btn_id).click()
            self.wait_until_ready(timeout=timeout)
            return self
        except Exception:
            pass
        raise ElementNotFoundError(
            f"Selection options button for {field_name!r} not found (expected ID: {btn_id!r})"
        )

    def _selection_field_id(self, name: str, *, high: bool = False) -> str | None:
        suffix = "-HIGH" if high else "-LOW"
        candidates = [
            f"wnd[0]/usr/ctxt{name}{suffix}",
            f"wnd[0]/usr/txt{name}{suffix}",
            f"wnd[0]/usr/ctxt{name}",
            f"wnd[0]/usr/txt{name}",
            f"wnd[0]/usr/ctxtPA_{name}{suffix}",
            f"wnd[0]/usr/txtPA_{name}{suffix}",
        ]
        for c in candidates:
            try:
                self._find_now(c)
                return c
            except Exception:
                continue
        return None

    def screenshot(self, path: str | None = None):
        """Capture the SAP main window as a PIL Image, optionally saving to *path*.

        The window region is read from the COM object (ScreenLeft/ScreenTop/Width/Height)
        so only the SAP window is captured, not the full screen.  Falls back to
        full-screen capture when the position cannot be read.

        Returns the PIL Image so tests can inspect it without writing a file::

            sess.screenshot("failure.png")
            img = sess.screenshot()
        """
        from ._image import Screen

        region = None
        try:
            wnd = self._find_now("wnd[0]")
            left = int(_get_first(wnd, ("ScreenLeft", "Left", "left")) or 0)
            top = int(_get_first(wnd, ("ScreenTop", "Top", "top")) or 0)
            w = int(_get_first(wnd, ("Width", "width")) or 0)
            h = int(_get_first(wnd, ("Height", "height")) or 0)
            if w > 0 and h > 0:
                region = (left, top, left + w, top + h)
        except Exception:
            pass
        img = Screen.screenshot(region=region)
        if path is not None:
            img.save(str(path))
        return img

    def wait_for_status_message(
        self,
        text: str = "",
        *,
        type: str | None = None,
        timeout: float | None = None,
    ) -> tuple[str, str]:
        """Wait until the SAP status bar matches *text* and/or *type*.

        Returns ``(message, type_code)``.  *type_code* is ``'S'`` (success),
        ``'W'`` (warning), ``'E'`` (error), or ``'A'`` (abend).

        When neither *text* nor *type* is given, waits for any non-empty
        status message::

            sess.wait_for_status_message("saved", type="S", timeout=10.0)
            sess.wait_for_status_message(type="E")          # any error
            msg, _ = sess.wait_for_status_message()         # any message
        """
        seconds, end = _deadline(timeout)
        want_text = bool(text)
        want_type = type is not None
        while True:
            msg, msg_type = self.status_message()
            text_ok = (not want_text) or (text.lower() in msg.lower())
            type_ok = (not want_type) or (msg_type == type)
            any_ok = (want_text or want_type) or bool(msg.strip())
            if text_ok and type_ok and any_ok:
                return msg, msg_type
            if time.monotonic() >= end:
                raise WaitTimeoutError(
                    f"SAP status did not match text={text!r} type={type!r} after {seconds:g}s; "
                    f"last={msg!r} ({msg_type!r})"
                )
            _sleep()

    def assert_status(
        self,
        text: str = "",
        *,
        type: str = "S",
        timeout: float | None = None,
    ) -> SapSession:
        """Wait for a status message and raise AssertionError if the type is wrong.

        Combines ``wait_for_status_message`` with an assertion so tests can
        verify that an operation succeeded without manual status_message() checks::

            sess.find_by_id("wnd[0]/tbar[0]/btn[11]").click()   # Activate
            sess.assert_status(type="S")
            sess.assert_status("object saved", type="S", timeout=10.0)
        """
        msg, msg_type = self.wait_for_status_message(text, type=None, timeout=timeout)
        if msg_type != type:
            raise AssertionError(
                f"Expected SAP status type={type!r} but got type={msg_type!r}: {msg!r}"
            )
        if text and text.lower() not in msg.lower():
            raise AssertionError(f"Expected SAP status to contain {text!r} but got: {msg!r}")
        return self

    def select_f4_value(
        self,
        value: str,
        *,
        column: str | int | None = None,
        timeout: float | None = None,
    ) -> SapSession:
        """Select *value* from an open F4 (input help) popup dialog.

        Call after the F4 popup is already open (e.g. after
        ``locator.open_f4_help()``).  Searches the first grid inside
        ``wnd[1]`` for a cell containing *value*, selects the row, and
        confirms the dialog.  *column* narrows the search to a specific
        column key or zero-based index; when omitted the first column is
        used::

            sess.find_by_id("wnd[0]/usr/ctxtKUNNR").open_f4_help()
            sess.select_f4_value("CUST001")
        """
        secs, end = _deadline(timeout or 15.0)
        while True:
            try:
                self._find_now("wnd[1]")
                break
            except Exception:
                pass
            if time.monotonic() >= end:
                raise WaitTimeoutError(f"F4 popup (wnd[1]) did not appear after {secs:g}s")
            _sleep()

        self.wait_until_ready(timeout=timeout)
        popup = self._find_now("wnd[1]")

        grid = _find_in_tree(popup, name=None, type="GuiGridView", seen=set(), depth=10)
        if grid is None:
            grid = _find_in_tree(popup, name=None, type="GuiTableControl", seen=set(), depth=10)

        if grid is None:
            txt_fld = _find_in_tree(popup, name=None, type="GuiCTextField", seen=set(), depth=10)
            if txt_fld is None:
                txt_fld = _find_in_tree(popup, name=None, type="GuiTextField", seen=set(), depth=10)
            if txt_fld is not None:
                _set_first(txt_fld, ("Text", "text", "Value", "value"), value)
                _call_first(popup, ("SendVKey", "sendVKey"), 0)
                self.wait_until_ready(timeout=timeout)
                return self
            raise ElementNotFoundError("No grid or text field found in F4 popup (wnd[1])")

        row_count = _get_first(grid, ("RowCount", "rowCount"))
        if row_count is None:
            rows_col = _get_first(grid, ("Rows", "rows"))
            row_count = _collection_count(rows_col) if rows_col is not None else 0
        row_count = int(row_count or 0)

        col_key: str | int = 0
        if column is not None:
            col_key = column
        else:
            try:
                col_key = str(_collection_item(grid.Columns, 0).Name)
            except Exception:
                col_key = 0

        for row in range(row_count):
            try:
                cell = str(grid.GetCellValue(row, col_key))
                if value.lower() in cell.lower():
                    try:
                        grid.SetCurrentCell(row, col_key)
                    except Exception:
                        pass
                    if not _call_first(grid, ("ClickCurrentCell", "clickCurrentCell")):
                        _call_first(grid, ("DoubleClickCurrentCell", "doubleClickCurrentCell"))
                    self.wait_until_ready(timeout=timeout)
                    self.dismiss_all_popups(vkey=0)
                    return self
            except Exception:
                continue

        raise ElementNotFoundError(
            f"Value {value!r} not found in F4 popup grid (searched column={col_key!r})"
        )

    def enumerate_screen_types(
        self,
        parent_id: str = "wnd[0]",
    ) -> list[dict[str, str]]:
        """Walk the current screen and return every unique component type found.

        Use this against a live SAP session to discover component types that
        may not yet be handled by the library.  Each entry is a dict with
        ``type``, ``sub_type`` (for GuiShell), ``id``, and ``name``::

            for item in sess.enumerate_screen_types():
                print(item["type"], item.get("sub_type", ""), item["id"])

        Compare the output against ``_GUISHELL_SUBTYPE_MAP`` to find any
        SubType strings that are missing from the map.
        """
        result: list[dict[str, str]] = []
        seen_ids: set[str] = set()

        def _walk(component: Any, depth: int) -> None:
            if depth < 0:
                return
            key = _component_key(component)
            if _key_seen_before(key, seen_ids):
                return
            obj_id = key or ""

            comp_type = str(_get_first(component, ("Type", "type")) or "")
            comp_subtype = str(_get_first(component, ("SubType", "subType")) or "")
            comp_name = str(_get_first(component, ("Name", "name")) or "")
            entry: dict[str, str] = {"type": comp_type, "id": obj_id, "name": comp_name}
            if comp_subtype:
                entry["sub_type"] = comp_subtype
            result.append(entry)

            for child in _iter_children(component):
                _walk(child, depth - 1)

        try:
            root = self._find_now(parent_id)
            _walk(root, depth=30)
        except Exception:
            pass
        return result

    def _find_now(self, component_id: str) -> Any:
        for name in ("FindById", "findById"):
            try:
                return getattr(self._com, name)(component_id)
            except AttributeError:
                continue
        raise AttributeError("SAP session does not expose FindById")

    def _wait_component(
        self,
        *,
        id: str | None,
        name: str | None,
        type: str | None,
        timeout: float | None,
    ) -> Any:
        seconds, end = _deadline(timeout)
        last_exc: Exception | None = None

        while True:
            try:
                if not self.is_busy():
                    component = self._find_component(id=id, name=name, type=type)
                    if component is not None:
                        return component
            except ApplicationError:
                # A dead session cannot start answering later — retrying
                # until the timeout would only rewrite it as a much less
                # informative ElementNotFoundError.
                raise
            except Exception as exc:
                last_exc = exc

            if time.monotonic() >= end:
                raise ElementNotFoundError(
                    f"SAP component {self._selector(id=id, name=name, type=type)} "
                    f"not found after {seconds:g}s"
                ) from last_exc
            _sleep()

    def _find_component(self, *, id: str | None, name: str | None, type: str | None) -> Any | None:
        if id is not None:
            component = self._find_now(id)
            if _matches(component, name=name, type=type):
                return component
            return None

        roots: list[Any] = []
        try:
            roots.append(self._find_now("wnd[0]"))
        except Exception:
            pass
        roots.extend(_iter_children(self._com))

        seen: set[str] = set()
        for root in roots:
            result = _find_in_tree(root, name=name, type=type, seen=seen, depth=30)
            if result is not None:
                return result
        return None

    @staticmethod
    def _selector(*, id: str | None, name: str | None, type: str | None) -> str:
        parts = []
        if id is not None:
            parts.append(f"id={id!r}")
        if name is not None:
            parts.append(f"name={name!r}")
        if type is not None:
            parts.append(f"type={type!r}")
        return "{" + ", ".join(parts) + "}"


class SapLocator:
    """Lazy locator for SAP GUI Scripting components."""

    def __init__(
        self,
        session: SapSession,
        *,
        id: str | None = None,
        name: str | None = None,
        type: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self._session = session
        self._id = id
        self._name = name
        self._type = type
        self._timeout = get_timeout() if timeout is None else float(timeout)

    def timeout(self, seconds: float) -> SapLocator:
        """Return a copy of this locator with a different timeout."""
        return SapLocator(
            self._session,
            id=self._id,
            name=self._name,
            type=self._type,
            timeout=seconds,
        )

    @property
    def raw(self) -> Any:
        """Resolve and return the underlying SAP component COM object."""
        return self._resolve()

    def _resolve(self, *, timeout: float | None = None) -> Any:
        return self._session._wait_component(
            id=self._id,
            name=self._name,
            type=self._type,
            timeout=self._timeout if timeout is None else timeout,
        )

    def click(self) -> SapLocator:
        """Press the SAP component.

        Never falls back to ``SetFocus``: a GuiCheckBox exposes SetFocus
        but not Press, so focusing it and returning would report a click
        that never toggled anything as a pass.
        """
        component = self._resolve()
        if not _call_first(component, ("Press", "press", "Click", "click", "Select", "select")):
            raise ApplicationError(
                f"SAP component {self!r} cannot be clicked",
                hint=(
                    "it exposes none of Press/Click/Select — use set_checked() for a "
                    "checkbox or radio button, select_row() / tree_select_node() for "
                    "a grid or tree, or focus() to only move the cursor there"
                ),
            )
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def double_click(self) -> SapLocator:
        """Double-click the SAP component if the COM component supports it."""
        component = self._resolve()
        if not _call_first(component, ("DoubleClick", "doubleClick")):
            raise ApplicationError(f"SAP component {self!r} cannot be double-clicked")
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def set_text(self, text: str) -> SapLocator:
        component = self._resolve()
        if not _set_first(component, ("Text", "text", "Value", "value"), text):
            raise ApplicationError(f"SAP component {self!r} does not expose text/value")
        return self

    def type_text(self, text: str) -> SapLocator:
        """Alias for ``set_text`` for SAP text-capable components."""
        return self.set_text(text)

    def clear(self) -> SapLocator:
        return self.set_text("")

    def press_key(self, key: int | str) -> SapLocator:
        component = self._resolve()
        if not _call_first(component, ("SetFocus", "setFocus")):
            raise ApplicationError(f"SAP component {self!r} cannot receive focus")
        self._session.send_vkey(key, timeout=self._timeout)
        return self

    def focus(self) -> SapLocator:
        component = self._resolve()
        if not _call_first(component, ("SetFocus", "setFocus")):
            raise ApplicationError(f"SAP component {self!r} cannot receive focus")
        return self

    def text(self) -> str:
        """Return the SAP component text/value/name."""
        return _component_text(self._resolve())

    def value(self) -> str:
        return self.text()

    def exists(self, timeout: float = 0.0) -> bool:
        """Return True if the SAP component exists within *timeout* seconds."""
        try:
            self.timeout(timeout)._resolve()
            return True
        except Exception:
            return False

    def is_visible(self) -> bool:
        """Return True if the SAP component is visible, defaulting to True."""
        try:
            return _bool_attr(self._resolve(timeout=0), ("Visible", "visible"), True)
        except Exception:
            return False

    def is_enabled(self) -> bool:
        """Return True if the SAP component is enabled/changeable.

        A control is only interactable when every flag it exposes agrees: a
        non-read-only field that is also disabled or not changeable is not
        enabled. Each flag is checked independently and any one of them being
        unset makes the control not enabled. Missing flags are ignored so a
        control that exposes none defaults to enabled.
        """
        try:
            component = self._resolve(timeout=0)
        except Exception:
            return False
        read_only = _get_first(component, ("ReadOnly", "readOnly", "readonly"))
        if read_only is not None and bool(read_only):
            return False
        enabled = _get_first(component, ("Enabled", "enabled"))
        if enabled is not None and not bool(enabled):
            return False
        changeable = _get_first(component, ("Changeable", "changeable"))
        if changeable is not None and not bool(changeable):
            return False
        return True

    def get_attribute(self, name: str, default: Any = _MISSING) -> Any:
        """Return an attribute from the underlying SAP component.

        SAP's COM interface spells properties in PascalCase (``Text``,
        ``Changeable``), so the given name is tried first, then a
        capitalized and a lowercased variant.

        Raises ``AttributeError`` when no variant resolves, chaining the
        error COM raised for the last one. Returning ``None`` there would
        hide both a misspelled property and a component that failed to
        answer, leaving an assertion to pass against a value never read.

        Pass *default* to opt back into a non-raising lookup.
        """
        component = self._resolve()
        last_exc: Exception | None = None
        for candidate in (name, name[:1].upper() + name[1:], name.lower()):
            try:
                return getattr(component, candidate)
            except Exception as exc:
                last_exc = exc
                continue
        if default is not _MISSING:
            return default
        raise AttributeError(
            f"SAP component {self._id or self._name or self._type!r} answered "
            f"no attribute {name!r} (also tried PascalCase and lowercase)"
        ) from last_exc

    def wait_for(self, *, state: str = "visible", timeout: float | None = None) -> SapLocator:
        """Wait for state ``exists``, ``visible``, ``enabled``, or ``hidden``."""
        seconds, end = _deadline(timeout if timeout is not None else self._timeout)
        while True:
            ok = False
            if state == "exists":
                ok = self.exists(timeout=0)
            elif state == "visible":
                ok = self.is_visible()
            elif state == "enabled":
                ok = self.is_enabled()
            elif state in ("hidden", "invisible"):
                ok = False if self._session.is_busy() else not self.is_visible()
            else:
                raise ValueError(f"Unsupported SAP locator state: {state!r}")

            if ok:
                return self
            if time.monotonic() >= end:
                raise WaitTimeoutError(
                    f"SAP component {self!r} did not reach state {state!r} after {seconds:g}s"
                )
            _sleep()

    def wait_until_hidden(self, timeout: float = 10.0) -> SapLocator:
        """Wait until the SAP component is hidden or missing."""
        return self.wait_for(state="hidden", timeout=timeout)

    def wait_until_enabled(self, timeout: float = 10.0) -> SapLocator:
        return self.wait_for(state="enabled", timeout=timeout)

    def is_checked(self) -> bool:
        """Return True if a SAP checkbox or radio button is selected."""
        try:
            component = self._resolve(timeout=0)
        except Exception:
            return False
        val = _get_first(component, ("Selected", "selected"))
        if val is None:
            return False
        return bool(val)

    def set_checked(self, checked: bool) -> SapLocator:
        """Set the checked state of a SAP checkbox or radio button."""
        component = self._resolve()
        if not _set_first(component, ("Selected", "selected"), checked):
            raise ApplicationError(f"SAP component {self!r} does not support checked state")
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def row_count(self) -> int:
        """Return the number of rows in a SAP table or grid component."""
        component = self._resolve()
        # GuiGridView exposes RowCount directly
        val = _get_first(component, ("RowCount", "rowCount"))
        if val is not None:
            return int(val)
        # GuiTableControl exposes Rows collection
        rows = _get_first(component, ("Rows", "rows"))
        if rows is not None:
            cnt = _collection_count(rows)
            if cnt is not None:
                return cnt
        return 0

    def cell_value(self, row: int, column: str | int) -> str:
        """Return the text of a cell in a SAP grid or table.

        *column* is either the column key string (``GuiGridView``) or
        a zero-based integer column index (``GuiTableControl``).
        """
        component = self._resolve()
        # For GuiShell(GridView), GetCellValue requires a column name string.
        # Resolve integer index → column name: try Columns first, then ColumnOrder.
        col_key: str | int = column
        if isinstance(column, int):
            try:
                col_name = str(_collection_item(component.Columns, column).Name)
                col_key = col_name
            except Exception:
                pass
            if col_key == column:
                # Columns failed; fall back to ColumnOrder collection
                try:
                    col_key = str(_collection_item(component.ColumnOrder, column))
                except Exception:
                    pass
        # GuiGridView
        try:
            return str(component.GetCellValue(row, col_key))
        except Exception:
            pass
        # GuiTableControl — Rows(row).Item(col).Text
        try:
            cell = _collection_item(component.Rows, row).Item(column)
            return _component_text(cell)
        except Exception:
            pass
        raise ApplicationError(f"Cannot read cell ({row}, {column!r}) from SAP component {self!r}")

    def select_row(self, index: int) -> SapLocator:
        """Select a row in a SAP table or grid by zero-based *index*."""
        component = self._resolve()
        if _call_first(component, ("SelectRow", "selectRow"), index):
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        # GuiGridView: set SelectedRows
        try:
            component.SelectedRows = str(index)
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        # GuiTableControl: GetAbsoluteRow(index).Selected = True
        try:
            component.GetAbsoluteRow(index).Selected = True
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        raise ApplicationError(f"SAP component {self!r} cannot select row {index}")

    def click_or_send_vkey(self, fallback_vkey: int | str) -> SapLocator:
        """Click this component; fall back to SendVKey(*fallback_vkey*) if click fails.

        SAP toolbar buttons are sometimes accessible via Press() and sometimes
        only via virtual key — depending on the screen state.  Without this
        method tests need explicit try/except blocks::

            # Before
            try:
                sess._com.FindById("wnd[0]/tbar[1]/btn[8]").Press()
            except Exception:
                sess._com.FindById("wnd[0]").SendVKey(8)

            # After
            sess.find_by_id("wnd[0]/tbar[1]/btn[8]").click_or_send_vkey("F8")
        """
        try:
            self.click()
        except Exception:
            self._session.send_vkey(fallback_vkey)
        return self

    # Combined click + confirm

    def click_and_confirm(
        self,
        button_label: str | None = None,
        *,
        vkey: int | str = 0,
        timeout: float | None = None,
    ) -> SapLocator:
        """Click this component, then dismiss the confirmation popup.

        Nearly every destructive SAP operation (delete, post, reverse) raises
        a confirmation popup immediately after the button is pressed.  This
        method clicks the component and then:

        * clicks the popup button whose text matches *button_label* (when given), or
        * sends *vkey* (default Enter) to dismiss the popup.

        Example::

            sess.find_by_id("wnd[0]/tbar[0]/btn[3]").click_and_confirm("Yes")
            sess.find_by_id("wnd[0]/tbar[0]/btn[3]").click_and_confirm()   # Enter
        """
        self.click()
        self._session.wait_until_ready(timeout=timeout)
        try:
            self._session.wait_for_popup(timeout=3.0)
            if button_label:
                self._session.popup_click_button(button_label, timeout=timeout)
            else:
                self._session.dismiss_all_popups(vkey=vkey, timeout=timeout)
        except WaitTimeoutError:
            pass  # no popup appeared — that is fine
        return self

    def click_and_dismiss(self, *, timeout: float | None = None) -> SapLocator:
        """Click this component, then cancel any confirmation popup (F12).

        Convenience alias for ``click_and_confirm(vkey='F12')``::

            sess.find_by_id("btn_cancel").click_and_dismiss()
        """
        return self.click_and_confirm(vkey="F12", timeout=timeout)

    # Field introspection

    def is_mandatory(self) -> bool:
        """Return True when the SAP field is flagged as mandatory (required input).

        Distinct from ``is_enabled()``: a field can be enabled (changeable)
        but not mandatory, or both enabled and mandatory.
        """
        try:
            component = self._resolve(timeout=0)
            val = _get_first(component, ("Required", "required", "Mandatory", "mandatory"))
            return bool(val) if val is not None else False
        except Exception:
            return False

    def is_readonly(self) -> bool:
        """Return True when the SAP field is read-only (not changeable).

        More specific than ``is_enabled()``, which also checks the Enabled
        and Changeable flags.  Use this when you only care about the
        ReadOnly flag::

            assert not sess.find_by_id("wnd[0]/usr/ctxtMATNR").is_readonly()
        """
        try:
            component = self._resolve(timeout=0)
            val = _get_first(component, ("ReadOnly", "readOnly", "readonly"))
            return bool(val) if val is not None else False
        except Exception:
            return False

    def get_tooltip(self) -> str:
        """Return the tooltip text of the SAP component (empty string if none)."""
        try:
            component = self._resolve()
            val = _get_first(component, ("Tooltip", "tooltip", "DefaultTooltip", "defaultTooltip"))
            return "" if val is None else str(val)
        except Exception:
            return ""

    # Combo box

    def select_option(self, value: str, *, partial: bool = False) -> SapLocator:
        """Set the selected entry of a SAP combo box (GuiComboBox).

        Tries setting by key first, then by exact display text, then (when
        *partial=True*) by partial display-text match::

            sess.find_by_id("wnd[0]/usr/cmbDOKTY").select_option("RN")
            sess.find_by_id("wnd[0]/usr/cmbDOKTY").select_option("Invoice", partial=True)
        """
        component = self._resolve()
        # Direct key assignment (most reliable)
        try:
            component.Key = value
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        # Scan Entries collection for matching display text
        try:
            entries = component.Entries
            count = _collection_count(entries)
            for i in range(count or 0):
                try:
                    entry = _collection_item(entries, i)
                    entry_val = str(getattr(entry, "Value", "") or "")
                    entry_key = str(getattr(entry, "Key", "") or "")
                    hit = (
                        value == entry_val
                        or value == entry_key
                        or (partial and value.lower() in entry_val.lower())
                    )
                    if hit:
                        component.Key = entry_key
                        self._session.wait_until_ready(timeout=self._timeout)
                        return self
                except Exception:
                    continue
        except Exception:
            pass
        # Last resort: set Value property
        try:
            component.Value = value
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        raise ApplicationError(f"SAP combo box {self!r} has no entry matching {value!r}")

    # Grid / table cell operations

    def set_cell_value(self, row: int, column: str | int, value: str) -> SapLocator:
        """Write *value* into a cell of a SAP grid or table control.

        Works with GuiGridView (*column* = column key string) and
        GuiTableControl (*column* = zero-based integer index)::

            grid.set_cell_value(0, "KUNNR", "CUST001")
            table.set_cell_value(2, 1, "100.00")
        """
        component = self._resolve()
        try:
            component.ModifyCell(row, column, value)
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        try:
            cell = _collection_item(component.Rows, row).Item(column)
            _set_first(cell, ("Text", "text", "Value", "value"), value)
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        raise ApplicationError(f"Cannot write cell ({row}, {column!r}) on SAP component {self!r}")

    def click_cell(self, row: int, column: str | int) -> SapLocator:
        """Click a specific cell in a SAP grid view.

        Sets the current cell then clicks it, which triggers hotspot
        (drilldown) or cell-level navigation::

            grid.click_cell(0, "MATNR")   # drilldown on material number
        """
        component = self._resolve()
        try:
            component.SetCurrentCell(row, column)
            component.ClickCurrentCell()
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        try:
            component.SelectedRows = str(row)
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        raise ApplicationError(f"Cannot click cell ({row}, {column!r}) on {self!r}")

    def double_click_cell(self, row: int, column: str | int) -> SapLocator:
        component = self._resolve()
        try:
            component.SetCurrentCell(row, column)
            component.DoubleClickCurrentCell()
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        raise ApplicationError(f"Cannot double-click cell ({row}, {column!r}) on {self!r}")

    def column_count(self) -> int:
        """Return the number of columns in a SAP grid view."""
        component = self._resolve()
        val = _get_first(component, ("ColumnCount", "columnCount"))
        if val is not None:
            return int(val)
        try:
            return int(_collection_count(component.Columns) or 0)
        except Exception:
            return 0

    def column_titles(self) -> list[str]:
        """Return the visible column titles of a SAP grid view.

        Useful for discovering the column layout of an ALV grid without
        knowing the technical field names::

            cols = grid.column_titles()
            # ['Material', 'Plant', 'Storage Location', ...]
        """
        component = self._resolve()
        titles: list[str] = []
        try:
            cols = component.Columns
            count = _collection_count(cols)
            if not count:
                # GuiShell exposes ColumnCount as a direct property
                count = _get_first(component, ("ColumnCount", "columnCount"))
                count = int(count) if count is not None else 0
            for i in range(count or 0):
                try:
                    col = _collection_item(cols, i)
                    title = str(getattr(col, "Title", "") or getattr(col, "Name", "") or "")
                    titles.append(title)
                except Exception:
                    continue
        except Exception:
            pass
        return titles

    def select_all_rows(self) -> SapLocator:
        component = self._resolve()
        if not _call_first(component, ("SelectAll", "selectAll")):
            raise ApplicationError(f"SAP component {self!r} does not support SelectAll")
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def deselect_all_rows(self) -> SapLocator:
        component = self._resolve()
        if not _call_first(
            component, ("ClearSelection", "clearSelection", "DeselectAll", "deselectAll")
        ):
            try:
                component.SelectedRows = ""
            except Exception as exc:
                raise ApplicationError(
                    f"SAP component {self!r} does not support row deselection"
                ) from exc
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def get_selected_rows(self) -> list[int]:
        """Return a list of selected row indices from a SAP grid view.

        SAP returns selected rows as a comma-separated string that
        abbreviates contiguous runs as ranges (``'0,2,5-8'``); this method
        expands them into a plain Python list::

            rows = grid.get_selected_rows()   # [0, 2, 5, 6, 7, 8]
        """
        component = self._resolve()
        raw = _get_first(component, ("SelectedRows", "selectedRows"))
        if raw is None:
            return []
        return _parse_selected_rows(str(raw).strip())

    # F4 input help

    def open_f4_help(self) -> SapLocator:
        """Press F4 on this field to open the SAP input help popup.

        After calling this, use ``session.select_f4_value(value)`` to
        pick an entry from the popup::

            sess.find_by_id("wnd[0]/usr/ctxtKUNNR").open_f4_help()
            sess.select_f4_value("CUST001")
        """
        component = self._resolve()
        _call_first(component, ("SetFocus", "setFocus"))
        self._session.send_vkey("F4", timeout=self._timeout)
        return self

    # GuiTree operations

    def tree_expand(self, node_key: str) -> SapLocator:
        """Expand a node in a SAP GuiTree control by its node key."""
        component = self._resolve()
        if not _call_first(component, ("ExpandNode", "expandNode"), node_key):
            raise ApplicationError(f"GuiTree {self!r} cannot expand node {node_key!r}")
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def tree_collapse(self, node_key: str) -> SapLocator:
        """Collapse a node in a SAP GuiTree control by its node key."""
        component = self._resolve()
        if not _call_first(component, ("CollapseNode", "collapseNode"), node_key):
            raise ApplicationError(f"GuiTree {self!r} cannot collapse node {node_key!r}")
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def tree_select_node(self, node_key: str) -> SapLocator:
        """Select a node in a SAP GuiTree control by its node key."""
        component = self._resolve()
        if not _call_first(component, ("SelectNode", "selectNode"), node_key):
            raise ApplicationError(f"GuiTree {self!r} cannot select node {node_key!r}")
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def tree_get_selected(self) -> str | None:
        """Return the node key of the currently selected GuiTree node, or None."""
        component = self._resolve()
        val = _get_first(component, ("SelectedNode", "selectedNode", "GetSelectedNode"))
        return None if val is None else str(val)

    def tree_find_node(
        self,
        text: str,
        *,
        partial: bool = True,
        case_sensitive: bool = False,
    ) -> str:
        """Return the node key of the first GuiTree node whose label matches *text*.

        GuiTree node keys are opaque SAP identifiers (e.g. ``"    1"`` or
        ``"\\ITEM\\001"``).  Tests should never hardcode them — use the
        visible node label instead::

            key = tree.tree_find_node("General Settings")
            tree.tree_expand(key)
            tree.tree_select_node(key)

            # Chain directly:
            tree.tree_select_node(tree.tree_find_node("Customizing"))
        """
        component = self._resolve()
        t_cmp = text if case_sensitive else text.upper()

        def _check_key(key: str) -> bool:
            try:
                label = str(component.GetNodeTextByKey(key))
            except Exception:
                try:
                    label = str(component.GetItemText(key, ""))
                except Exception:
                    return False
            l_cmp = label if case_sensitive else label.upper()
            return (t_cmp in l_cmp) if partial else (t_cmp == l_cmp)

        def _get_children(key: str) -> list[str]:
            try:
                col = component.GetSubNodesCol(key)
                n = _collection_count(col)
                return [str(_collection_item(col, i)) for i in range(n or 0)]
            except Exception:
                return []

        def _walk(key: str) -> str | None:
            if _check_key(key):
                return key
            for child_key in _get_children(key):
                found = _walk(child_key)
                if found is not None:
                    return found
            return None

        # Traverse from top-level nodes
        try:
            top_col = component.GetSubNodesCol("")
            n = _collection_count(top_col)
            for i in range(n or 0):
                try:
                    found = _walk(str(_collection_item(top_col, i)))
                    if found is not None:
                        return found
                except Exception:
                    continue
        except Exception:
            try:
                nodes = component.Nodes
                n = _collection_count(nodes)
                for i in range(n or 0):
                    try:
                        found = _walk(str(_collection_item(nodes, i)))
                        if found is not None:
                            return found
                    except Exception:
                        continue
            except Exception:
                pass

        # Last resort: TopNode fallback (for trees where GetSubNodesCol("") returns None)
        try:
            top_key = str(component.TopNode)
            if top_key.strip():
                found = _walk(top_key)
                if found is not None:
                    return found
        except Exception:
            pass

        raise ElementNotFoundError(f"GuiTree node with label {text!r} not found")

    def tree_get_all_nodes(self, start_key: str | None = None) -> list[str]:
        """Return all node keys reachable from *start_key* (or from the root).

        Traverses the GuiTree depth-first.  Collapsed sub-trees may not be
        fully enumerable until their parent is expanded::

            tree = sess.find_first_in_tree("GuiTree")
            for key in tree.tree_get_all_nodes():
                print(key)
        """
        component = self._resolve()
        result: list[str] = []

        def _children_of(key: str) -> list[str]:
            try:
                col = component.GetSubNodesCol(key)
                n = _collection_count(col)
                return [str(_collection_item(col, i)) for i in range(n or 0)]
            except Exception:
                return []

        def _walk(key: str) -> None:
            result.append(key)
            for child_key in _children_of(key):
                _walk(child_key)

        if start_key is not None:
            _walk(start_key)
            return result

        # Collect top-level nodes
        try:
            top_col = component.GetSubNodesCol("")
            n = _collection_count(top_col)
            for i in range(n or 0):
                try:
                    _walk(str(_collection_item(top_col, i)))
                except Exception:
                    continue
        except Exception:
            pass

        if not result:
            # Fallback: use component.Nodes collection
            try:
                nodes = component.Nodes
                n = _collection_count(nodes)
                for i in range(n or 0):
                    try:
                        _walk(str(_collection_item(nodes, i)))
                    except Exception:
                        continue
            except Exception:
                pass

        if not result:
            # Last resort: collect just the top-visible node (no deep walk)
            # to satisfy callers that need at least one node key to work with
            try:
                top_key = str(component.TopNode)
                if top_key.strip():
                    result.append(top_key)
            except Exception:
                pass

        return result

    # Combo box entries

    def get_combo_entries(self) -> list[dict[str, str]]:
        """Return all available entries of a SAP combo box as a list of dicts.

        Each entry is ``{"key": "...", "value": "..."}`` where *key* is the
        technical key and *value* is the display text::

            entries = sess.find_by_id("wnd[0]/usr/cmbDOKTY").get_combo_entries()
            labels  = [e["value"] for e in entries]
        """
        component = self._resolve()
        result: list[dict[str, str]] = []
        try:
            entries = component.Entries
            count = _collection_count(entries)
            for i in range(count or 0):
                try:
                    entry = _collection_item(entries, i)
                    result.append(
                        {
                            "key": str(getattr(entry, "Key", "") or ""),
                            "value": str(getattr(entry, "Value", "") or ""),
                        }
                    )
                except Exception:
                    continue
        except Exception:
            pass
        return result

    # Grid data extraction and search

    def find_row_by_value(
        self,
        column: str | int,
        value: str,
        *,
        partial: bool = False,
        case_sensitive: bool = False,
        start_row: int = 0,
    ) -> int:
        """Return the first row index where *column* contains *value*.

        Scrolls the grid automatically so every row is checked, not only
        the ones currently in the viewport::

            row = grid.find_row_by_value("MATNR", "100-100")
            grid.click_cell(row, "MATNR")

            row = grid.find_row_by_value("NAME1", "GmbH", partial=True)
        """
        component = self._resolve()
        total = self.row_count()
        v_cmp = value if case_sensitive else value.upper()
        for row in range(start_row, total):
            try:
                # Ensure row is in viewport
                try:
                    component.FirstVisibleRow = row
                except Exception:
                    pass
                cell = str(component.GetCellValue(row, column))
                c_cmp = cell if case_sensitive else cell.upper()
                hit = (v_cmp in c_cmp) if partial else (v_cmp == c_cmp)
                if hit:
                    return row
            except Exception:
                continue
        raise ElementNotFoundError(
            f"Value {value!r} not found in column {column!r} ({total} rows searched)"
        )

    def get_all_rows(
        self,
        columns: list[str | int] | None = None,
    ) -> list[dict[str, str]]:
        """Extract all rows from a SAP grid as a list of dicts.

        *columns* is a list of column keys (strings) or zero-based indices.
        When omitted, all columns returned by ``column_titles()`` are used
        (GuiGridView only; for GuiTableControl pass explicit column indices).

        Scrolls the grid to reach every row, so lazy-loaded grids are fully
        extracted::

            rows = grid.get_all_rows(["MATNR", "WERKS", "LGORT"])
            # [{"MATNR": "100-100", "WERKS": "1000", "LGORT": "0001"}, ...]

            rows = grid.get_all_rows()   # all columns
        """
        component = self._resolve()
        total = self.row_count()

        if columns is None:
            # Derive keys from Columns collection
            col_keys: list[str | int] = []
            try:
                cols = component.Columns
                n = _collection_count(cols)
                for i in range(n or 0):
                    try:
                        col_keys.append(str(_collection_item(cols, i).Name))
                    except Exception:
                        col_keys.append(i)
            except Exception:
                col_keys = list(range(self.column_count() or 0))
        else:
            col_keys = list(columns)

        result: list[dict[str, str]] = []
        for row in range(total):
            try:
                # Bring row into viewport
                try:
                    component.FirstVisibleRow = row
                except Exception:
                    pass
                record: dict[str, str] = {}
                for key in col_keys:
                    try:
                        record[str(key)] = str(component.GetCellValue(row, key))
                    except Exception:
                        record[str(key)] = ""
                result.append(record)
            except Exception:
                continue
        return result

    def sort_by_column(
        self,
        column: str | int,
        *,
        descending: bool = False,
    ) -> SapLocator:
        """Sort a SAP grid by *column* (ascending by default).

        Useful when tests need a predictable row order before making
        positional assertions::

            grid.sort_by_column("BUDAT")
            grid.sort_by_column("WRBTR", descending=True)
        """
        component = self._resolve()
        method = "SortDescending" if descending else "SortAscending"
        if not _call_first(component, (method, method.lower()), column):
            # Fallback: double-click column header (toggles sort)
            try:
                component.SetCurrentCell(0, column)
                component.ClickCurrentCell()
            except Exception as exc:
                raise ApplicationError(
                    f"SAP grid {self!r} does not support sorting on column {column!r}"
                ) from exc
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    # Grid scrolling

    def scroll_to_row(self, row: int) -> SapLocator:
        """Scroll a SAP grid or table so that *row* is within the visible window.

        SAP ALV grids load rows lazily — ``cell_value(n, col)`` raises for
        rows beyond the current viewport.  Call this first to bring the row
        into view::

            grid.scroll_to_row(500)
            val = grid.cell_value(500, "MATNR")
        """
        component = self._resolve()
        # GuiGridView exposes FirstVisibleRow
        try:
            component.FirstVisibleRow = row
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        # GuiTableControl — use the vertical scrollbar
        try:
            component.VerticalScrollbar.Position = row
            self._session.wait_until_ready(timeout=self._timeout)
            return self
        except Exception:
            pass
        raise ApplicationError(f"SAP component {self!r} does not support row scrolling")

    # Column lookup by title

    def cell_value_by_column(
        self,
        row: int,
        column_title: str,
        *,
        partial: bool = False,
        case_sensitive: bool = False,
    ) -> str:
        """Return a cell value from a SAP grid by column header text.

        GuiTableControl column order changes when users rearrange columns.
        This method finds the correct index by matching the column title,
        making tests resilient to layout changes::

            val = table.cell_value_by_column(0, "Vendor")
            val = table.cell_value_by_column(0, "Vend", partial=True)
        """
        component = self._resolve()
        title_cmp = column_title if case_sensitive else column_title.upper()
        col_key: str | int | None = None
        try:
            cols = component.Columns
            count = _collection_count(cols)
            for i in range(count or 0):
                try:
                    col = _collection_item(cols, i)
                    t = str(getattr(col, "Title", "") or getattr(col, "Name", "") or "")
                    t_cmp = t if case_sensitive else t.upper()
                    hit = (title_cmp in t_cmp) if partial else (title_cmp == t_cmp)
                    if hit:
                        try:
                            col_key = str(col.Name)
                        except Exception:
                            col_key = i
                        break
                except Exception:
                    continue
        except Exception:
            pass
        if col_key is None:
            raise ElementNotFoundError(
                f"Column {column_title!r} not found in SAP component {self!r}"
            )
        return self.cell_value(row, col_key)

    # Context menu

    def right_click(self) -> SapLocator:
        """Open the context menu on this SAP component.

        After calling this, use ``context_menu_click(option)`` to select an
        entry::

            grid.click_cell(0, "MATNR")
            grid.right_click()
            grid.context_menu_click("Display Material")
        """
        component = self._resolve()
        if not _call_first(
            component, ("ShowContextMenu", "showContextMenu", "RightClick", "rightClick")
        ):
            raise ApplicationError(
                f"SAP component {self!r} does not support right-click / context menu"
            )
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    def context_menu_click(
        self,
        option: str,
        *,
        partial: bool = True,
        case_sensitive: bool = False,
    ) -> SapLocator:
        """Click an entry in the currently open SAP context menu.

        Must be called after ``right_click()`` has opened the context menu::

            grid.right_click().context_menu_click("Export to Spreadsheet")
        """
        try:
            root = self._session._find_now("wnd[0]")
            ctx = _find_in_tree(root, name=None, type="GuiContextMenu", seen=set(), depth=5)
            if ctx is None:
                ctx = self._session._find_now("wnd[1]")
            item = _find_by_text(
                ctx,
                text=option,
                comp_type=None,
                partial=partial,
                case_sensitive=case_sensitive,
                seen=set(),
                depth=8,
            )
            if item is not None:
                if not _call_first(item, ("Select", "select", "Press", "press", "Click", "click")):
                    raise ApplicationError(f"Context menu item {option!r} cannot be activated")
                self._session.wait_until_ready(timeout=self._timeout)
                return self
        except (ApplicationError, ElementNotFoundError):
            raise
        except Exception:
            pass
        raise ElementNotFoundError(f"Context menu option {option!r} not found")

    # GuiTextEdit  (multi-line long-text fields)

    def line_count(self) -> int:
        """Return the number of lines in a SAP multi-line text editor (GuiTextEdit)."""
        component = self._resolve()
        val = _get_first(component, ("LineCount", "lineCount", "NumberOfLines", "numberOfLines"))
        if val is not None:
            return int(val)
        # Fallback: count newlines in text
        try:
            return len(str(component.Text).splitlines())
        except Exception:
            return 0

    def append_text(self, text: str, *, newline: bool = True) -> SapLocator:
        """Append *text* to a SAP multi-line text editor (GuiTextEdit).

        When *newline* is True (default) a line break is inserted before the
        new text::

            notes = sess.find_first_in_tree("GuiTextEdit")
            notes.append_text("Approved by QA")
        """
        component = self._resolve()
        current = _get_first(component, ("Text", "text")) or ""
        separator = "\n" if newline and current else ""
        if not _set_first(component, ("Text", "text"), str(current) + separator + text):
            raise ApplicationError(
                f"SAP component {self!r} does not expose a writable Text property"
            )
        self._session.wait_until_ready(timeout=self._timeout)
        return self

    # GuiListbox  (selection list dialogs)

    def get_items(self) -> list[dict[str, str]]:
        """Return all entries of a SAP listbox as a list of dicts.

        Each dict contains ``{"key": "...", "value": "...", "selected": "True/False"}``::

            items = sess.find_first_in_tree("GuiListbox").get_items()
            labels = [i["value"] for i in items if i["selected"] == "True"]
        """
        component = self._resolve()
        result: list[dict[str, str]] = []
        for attr in ("Entries", "Items", "Children"):
            try:
                collection = getattr(component, attr)
                count = _collection_count(collection)
                if count is None:
                    continue
                for i in range(count):
                    try:
                        item = _collection_item(collection, i)
                        key = str(getattr(item, "Key", i) or i)
                        value = str(getattr(item, "Value", "") or "")
                        selected = bool(getattr(item, "Selected", False))
                        result.append(
                            {
                                "key": key,
                                "value": value,
                                "selected": str(selected),
                            }
                        )
                    except Exception:
                        continue
                return result
            except Exception:
                continue
        return result

    def select_items(
        self,
        values: list[str],
        *,
        by: str = "value",
        partial: bool = False,
        case_sensitive: bool = False,
    ) -> SapLocator:
        """Select entries in a SAP listbox by value or key.

        *by* is ``"value"`` (default, matches display text) or ``"key"``
        (matches the technical key).  Existing selection is cleared first::

            lb = sess.find_first_in_tree("GuiListbox")
            lb.select_items(["Plant 1000", "Plant 2000"])
            lb.select_items(["1000", "2000"], by="key")
        """
        component = self._resolve()

        try:
            component.ClearSelection()
        except Exception:
            pass

        for attr in ("Entries", "Items", "Children"):
            try:
                collection = getattr(component, attr)
                count = _collection_count(collection)
                if count is None:
                    continue
                for i in range(count):
                    try:
                        item = _collection_item(collection, i)
                        candidate = str(
                            getattr(item, "Value" if by == "value" else "Key", "") or ""
                        )
                        for target in values:
                            t = target if case_sensitive else target.upper()
                            c = candidate if case_sensitive else candidate.upper()
                            hit = (t in c) if partial else (t == c)
                            if hit:
                                _set_first(item, ("Selected", "selected"), True)
                                break
                    except Exception:
                        continue
                self._session.wait_until_ready(timeout=self._timeout)
                return self
            except Exception:
                continue

        raise ApplicationError(
            f"SAP component {self!r} does not expose a selectable items collection"
        )

    # GuiCalendar  (date picker)

    def select_date(self, date: str) -> SapLocator:
        """Select a date in a SAP calendar control (GuiCalendar / DateTimePicker).

        *date* should be in the SAP date format for the current locale
        (e.g. ``"01.01.2024"`` for German, ``"01/01/2024"`` for English).
        Falls back to setting the ``Text`` property directly when the
        component exposes it (works for GuiCTextField date fields)::

            sess.find_first_in_tree("GuiCalendar").select_date("01.01.2024")
        """
        component = self._resolve()

        # GuiCalendar / DateTimePicker: try focused date selection methods first
        for method in ("SetSelectionInterval", "setSelectionInterval"):
            try:
                getattr(component, method)(date, date)
                self._session.wait_until_ready(timeout=self._timeout)
                return self
            except Exception:
                pass

        # Some versions expose a single date property
        for prop in ("FocusedDate", "focusedDate", "SelectedDate", "selectedDate"):
            try:
                setattr(component, prop, date)
                self._session.wait_until_ready(timeout=self._timeout)
                return self
            except Exception:
                pass

        # Last resort: direct Text assignment (GuiCTextField date fields)
        if _set_first(component, ("Text", "text", "Value", "value"), date):
            self._session.wait_until_ready(timeout=self._timeout)
            return self

        raise ApplicationError(f"SAP calendar component {self!r} does not accept date {date!r}")

    def __repr__(self) -> str:
        return f"SapLocator(id={self._id!r}, name={self._name!r}, type={self._type!r})"


def _normalize_vkey(key: int | str) -> int:
    if isinstance(key, int):
        return key
    normalized = key.strip().upper()
    if normalized.isdigit():
        return int(normalized)
    if normalized in _VKEYS:
        return _VKEYS[normalized]
    raise ValueError(f"Unsupported SAP virtual key: {key!r}")


# GuiShell is a host container in the SAP GUI Scripting COM hierarchy.
# The actual widget type is identified by GuiShell.SubType.  This mapping
# lets callers use the logical type name (e.g. "GuiGridView") in
# find_first_in_tree / find_all_in_tree regardless of whether the running
# SAP GUI version exposes the widget as its own COM type or wraps it in
# a GuiShell.
#
# SubType strings are the exact strings returned by the COM SubType property
# (case-sensitive).  To extend this map for a new SubType discovered via
# SapSession.enumerate_screen_types(), just add the entry here.
_GUISHELL_SUBTYPE_MAP: dict[str, str] = {
    "GuiGridView": "GridView",
    "GuiTree": "Tree",
    "GuiCalendar": "DateTimePicker",
    "GuiChart": "Chart",
    "GuiMap": "Map",
    "GuiPicture": "Picture",
    "GuiHtmlViewer": "HTMLViewer",  # HTML viewer in some dynpros
    "GuiToolbarControl": "ToolbarControl",  # custom ALV toolbar
    "GuiOfficeIntegration": "OfficeIntegration",
    # SAP GUI 7.40+ wraps the ABAP source editor as GuiShell(AbapEditor)
    # rather than exposing a native GuiTextEdit type.
    "GuiTextEdit": "AbapEditor",
}

# Maps logical type names to actual SAP COM type names for non-GuiShell components.
# E.g. SAP uses "GuiSplitterShell" while callers ask for "GuiSplitter".
_TYPE_ALIASES: dict[str, str] = {
    "GuiSplitter": "GuiSplitterShell",
}


def _matches(component: Any, *, name: str | None, type: str | None) -> bool:
    if name is not None:
        comp_name = _get_first(component, ("Name", "name"))
        if comp_name != name:
            return False
    if type is not None:
        comp_type = _get_first(component, ("Type", "type"))
        if comp_type != type:
            # Check non-GuiShell type aliases (e.g. GuiSplitter → GuiSplitterShell)
            alias = _TYPE_ALIASES.get(type)
            if alias and comp_type == alias:
                return True
            if comp_type == "GuiShell":
                sub_type = _get_first(component, ("SubType", "subType"))
                if _GUISHELL_SUBTYPE_MAP.get(type) == sub_type:
                    return True
            return False
    return True


def _component_key(component: Any) -> str | None:
    """Return the stable SAP ``Id`` string used as the tree-walk cycle guard.

    ``None`` when the component exposes no Id. Python's ``id()`` is not a
    usable substitute: win32com hands back fresh wrapper objects whose
    memory addresses are reused once the previous wrapper is freed, so an
    id()-keyed visited set marks unrelated siblings as already-seen and
    silently drops whole subtrees. Callers must skip deduplication for a
    keyless component instead — revisiting a node is bounded by *depth*,
    losing one is not recoverable.
    """
    try:
        raw = component.Id
    except Exception:
        return None
    return str(raw) if raw else None


def _key_seen_before(key: str | None, seen: set[str]) -> bool:
    """Return True when *key* is already recorded in *seen*.

    Takes an already-read key so a caller that needs the Id anyway does
    not pay a second COM round-trip for it. A keyless component is never
    deduplicated — see :func:`_component_key`.
    """
    if key is None:
        return False
    if key in seen:
        return True
    seen.add(key)
    return False


def _seen_before(component: Any, seen: set[str]) -> bool:
    """Return True when *component* has a key already recorded in *seen*."""
    return _key_seen_before(_component_key(component), seen)


def _find_in_tree(
    component: Any,
    *,
    name: str | None,
    type: str | None,
    seen: set[str],
    depth: int,
) -> Any | None:
    if depth < 0:
        return None

    if _seen_before(component, seen):
        return None

    if _matches(component, name=name, type=type):
        return component

    for child in _iter_children(component):
        result = _find_in_tree(child, name=name, type=type, seen=seen, depth=depth - 1)
        if result is not None:
            return result
    return None


def _find_all_in_tree(
    component: Any,
    *,
    type: str,
    seen: set[str],
    depth: int,
    result: list[Any],
) -> None:
    """Collect all descendants matching *type* into *result* (in-place)."""
    if depth < 0:
        return
    if _seen_before(component, seen):
        return
    if _matches(component, name=None, type=type):
        result.append(component)
    for child in _iter_children(component):
        _find_all_in_tree(child, type=type, seen=seen, depth=depth - 1, result=result)


def _find_by_text(
    component: Any,
    *,
    text: str,
    comp_type: str | None,
    partial: bool,
    case_sensitive: bool,
    seen: set[str],
    depth: int,
) -> Any | None:
    """Find the first descendant whose display text matches *text*.

    Unlike _find_in_tree which matches on the SAP component type/name (the
    technical identifier), this searches the human-visible Text attribute —
    used for buttons, menu items, tabs, and toolbar entries where only the
    visible label is known.

    *comp_type* restricts the search to components of a specific SAP type
    (e.g. ``"GuiButton"``); pass ``None`` to search all component types.
    """
    if depth < 0:
        return None
    if _seen_before(component, seen):
        return None

    type_ok = True
    if comp_type is not None:
        ct = _get_first(component, ("Type", "type"))
        type_ok = ct == comp_type
    if type_ok:
        raw_text = _component_text(component)
        if raw_text:
            cmp_a = raw_text if case_sensitive else raw_text.upper()
            cmp_b = text if case_sensitive else text.upper()
            hit = (cmp_b in cmp_a) if partial else (cmp_b == cmp_a)
            if hit:
                return component

    for child in _iter_children(component):
        result = _find_by_text(
            child,
            text=text,
            comp_type=comp_type,
            partial=partial,
            case_sensitive=case_sensitive,
            seen=seen,
            depth=depth - 1,
        )
        if result is not None:
            return result
    return None
