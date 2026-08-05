"""JavaAccessBridge — helpers for testing Java Swing/AWT applications."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import threading
import time
from typing import Any

from ._helpers import _MISSING

# The fields AccessibleContextInfo carries that are meaningful to read back
# as attributes. Bounds are reachable via ``bounding_box()`` and the child
# count via ``all()``, so neither is duplicated here.
_JAB_ATTRIBUTES: tuple[str, ...] = (
    "name",
    "description",
    "role",
    "role_en_US",
    "states",
    "states_en_US",
)


class JavaAccessBridge:
    """Utilities for enabling and checking the Java Access Bridge."""

    @staticmethod
    def is_enabled() -> bool:
        """Return True if Java Access Bridge appears to be active."""
        import winreg  # type: ignore[import-untyped]

        # Check the Accessibility ATs registry key for jabswitch
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows NT\CurrentVersion\Accessibility\ATs",
            )
            with key:
                i = 0
                while True:
                    try:
                        name, _value, _type = winreg.EnumValue(key, i)
                        if "jabswitch" in name.lower() or "jab" in name.lower():
                            return True
                        i += 1
                    except OSError:
                        break
        except OSError:
            pass

        # Check the Configuration value (set by jabswitch /enable on modern JDK)
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows NT\CurrentVersion\Accessibility",
            )
            with key:
                try:
                    val, _ = winreg.QueryValueEx(key, "Configuration")
                    low = val.lower()
                    if "javaaccessbridge" in low or "oracle_javaaccessbridge" in low:
                        return True
                except OSError:
                    pass
        except OSError:
            pass

        # Fallback: check for windowsaccessbridge-64.dll in common locations
        java_home = JavaAccessBridge.java_home()
        if java_home:
            for name in ("WindowsAccessBridge-64.dll", "windowsaccessbridge-64.dll"):
                if os.path.isfile(os.path.join(java_home, "bin", name)):
                    return True

        windir = os.environ.get("WINDIR", r"C:\Windows")
        for sub in ("SysWOW64", "System32"):
            for name in ("WindowsAccessBridge-64.dll", "windowsaccessbridge-64.dll"):
                if os.path.isfile(os.path.join(windir, sub, name)):
                    return True

        return False

    @staticmethod
    def enable() -> None:
        """Run jabswitch.exe /enable; raises RuntimeError on failure."""
        java_home = JavaAccessBridge.java_home()
        jabswitch = "jabswitch.exe"
        if java_home:
            candidate = os.path.join(java_home, "bin", "jabswitch.exe")
            if os.path.isfile(candidate):
                jabswitch = candidate
        try:
            result = subprocess.run(
                [jabswitch, "/enable"],
                capture_output=True,
                timeout=15,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "jabswitch.exe not found — ensure a JRE/JDK with Java Access Bridge is "
                "installed and jabswitch.exe is on PATH or JAVA_HOME is set."
            ) from exc
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            raise RuntimeError(f"jabswitch.exe /enable failed (exit {result.returncode}): {stderr}")

    @staticmethod
    def ensure_enabled() -> None:
        """Enable Java Access Bridge only if it is not already enabled."""
        if not JavaAccessBridge.is_enabled():
            JavaAccessBridge.enable()

    @staticmethod
    def java_home() -> str | None:
        """Return the JRE/JDK home directory, or None if not found."""
        env_home = os.environ.get("JAVA_HOME")
        if env_home and os.path.isdir(env_home):
            return env_home

        import winreg  # type: ignore[import-untyped]

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (
                r"SOFTWARE\JavaSoft\Java Runtime Environment",
                r"SOFTWARE\JavaSoft\JRE",
                r"SOFTWARE\JavaSoft\JDK",
            ):
                try:
                    key = winreg.OpenKey(root, sub)
                    with key:
                        try:
                            current, _ = winreg.QueryValueEx(key, "CurrentVersion")
                            ver_key = winreg.OpenKey(key, current)
                            with ver_key:
                                home, _ = winreg.QueryValueEx(ver_key, "JavaHome")
                                if home and os.path.isdir(home):
                                    return home
                        except OSError:
                            pass
                except OSError:
                    pass

        try:
            result = subprocess.run(
                ["where.exe", "java"],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                java_exe = result.stdout.decode(errors="replace").splitlines()[0].strip()
                home = os.path.dirname(os.path.dirname(java_exe))
                if os.path.isdir(home):
                    return home
        except Exception:
            pass

        return None


# JAB ctypes structures


class _ACI(ctypes.Structure):
    """AccessibleContextInfo — mirrors the C struct from AccessBridgePackages.h."""

    _fields_ = [
        ("name", ctypes.c_wchar * 1024),
        ("description", ctypes.c_wchar * 1024),
        ("role", ctypes.c_wchar * 256),
        ("role_en_US", ctypes.c_wchar * 256),
        ("states", ctypes.c_wchar * 256),
        ("states_en_US", ctypes.c_wchar * 256),
        ("indexInParent", ctypes.c_int),
        ("childrenCount", ctypes.c_int),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("accessibleComponent", ctypes.c_int),  # BOOL = 4 bytes on Windows
        ("accessibleAction", ctypes.c_int),
        ("accessibleSelection", ctypes.c_int),
        ("accessibleText", ctypes.c_int),
        ("accessibleInterfaces", ctypes.c_int),
    ]


class _ATI(ctypes.Structure):
    _fields_ = [
        ("charCount", ctypes.c_int),
        ("caretIndex", ctypes.c_int),
        ("indexAtPoint", ctypes.c_int),
    ]


_MAX_ACTION_INFO = 256
_MAX_ACTIONS_TO_DO = 32
_MAX_ACTION_NAME = 256

# Characters read per getAccessibleTextRange call. The reply travels in
# GetAccessibleTextRangePackage.rText, a ``wchar_t[MAX_BUFFER_SIZE]`` with
# MAX_BUFFER_SIZE == 10240 (AccessBridgePackages.h), so the requested
# length — count plus the terminator — must not exceed that. A larger
# request comes back truncated with no error.
_TEXT_RANGE_CHUNK = 10239

# Longest string setTextContents accepts: SetTextContentsPackage.text is
# ``wchar_t[MAX_STRING_SIZE]`` and AccessBridgeCalls.h documents the maximum
# settable length as MAX_STRING_SIZE - 1.
_MAX_SET_TEXT = 1023

# GetCurrentAccessibleValueFromContextPackage.rValue is
# ``wchar_t[SHORT_STRING_SIZE]``.
_VALUE_BUFFER = 256


class _AAInfo(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar * _MAX_ACTION_NAME)]


class _AAI(ctypes.Structure):
    _fields_ = [
        ("actionsCount", ctypes.c_int),
        ("actionInfo", _AAInfo * _MAX_ACTION_INFO),
    ]


class _AA_TO_DO(ctypes.Structure):
    _fields_ = [
        ("actionsCount", ctypes.c_int),
        ("actions", _AAInfo * _MAX_ACTIONS_TO_DO),
    ]


# JAB session singleton


class _JABSession:
    """Manages the windowsaccessbridge-64.dll client session (singleton)."""

    _instance: _JABSession | None = None

    @classmethod
    def get_or_create(cls) -> _JABSession:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        java_home = JavaAccessBridge.java_home()
        candidates = ["windowsaccessbridge-64.dll"]
        if java_home:
            candidates.append(os.path.join(java_home, "bin", "windowsaccessbridge-64.dll"))

        self._wab: Any = None
        for path in candidates:
            try:
                self._wab = ctypes.WinDLL(path)
                break
            except OSError:
                continue

        if self._wab is None:
            raise RuntimeError(
                "Could not load windowsaccessbridge-64.dll. "
                "Copy it from $JAVA_HOME/bin to C:\\Windows\\System32\\ "
                "or ensure JAVA_HOME is set."
            )

        self._setup_prototypes()
        self._wab.Windows_run()
        # Windows_run starts the bridge's message pump on the calling
        # thread; every later call is dispatched through it, so the
        # session belongs to whichever thread built it.
        self._thread_ident = threading.get_ident()

    def _setup_prototypes(self) -> None:
        w = self._wab
        w.Windows_run.restype = None
        w.Windows_run.argtypes = []

        w.isJavaWindow.restype = ctypes.c_bool
        w.isJavaWindow.argtypes = [ctypes.wintypes.HWND]

        w.getAccessibleContextFromHWND.restype = ctypes.c_bool
        w.getAccessibleContextFromHWND.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.POINTER(ctypes.c_long),
            ctypes.POINTER(ctypes.c_int64),
        ]

        w.getAccessibleContextInfo.restype = ctypes.c_bool
        w.getAccessibleContextInfo.argtypes = [
            ctypes.c_long,
            ctypes.c_int64,
            ctypes.POINTER(_ACI),
        ]

        w.getAccessibleChildFromContext.restype = ctypes.c_int64
        w.getAccessibleChildFromContext.argtypes = [
            ctypes.c_long,
            ctypes.c_int64,
            ctypes.c_int,
        ]

        # releaseJavaObject — every AccessibleContext handed out by the
        # bridge is a JNI global reference held INSIDE the target JVM.
        # Without this call a tree walk leaks one reference per visited
        # node, and the polling waits re-walk the tree several times a
        # second. See AccessBridgeCalls.h.
        try:
            w.releaseJavaObject.restype = None
            w.releaseJavaObject.argtypes = [ctypes.c_long, ctypes.c_int64]
            self._has_release_api = True
        except AttributeError:
            self._has_release_api = False

        # AccessibleValue — the exported entry point is
        # getCurrentAccessibleValueFromContext, which writes into a
        # caller-supplied wchar_t buffer (AccessBridgeCalls.h:206).
        try:
            w.getCurrentAccessibleValueFromContext.restype = ctypes.c_bool
            w.getCurrentAccessibleValueFromContext.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.c_wchar_p,
                ctypes.c_short,
            ]
            self._has_value_api = True
        except AttributeError:
            self._has_value_api = False

        # setTextContents — writes text into the component via JNI,
        # bypassing the OS keyboard buffer. Essential when SendInput()
        # is blocked (RDP, background process, foreground-lock guard).
        try:
            w.setTextContents.restype = ctypes.c_bool
            w.setTextContents.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.c_wchar_p,
            ]
            self._has_set_text_api = True
        except AttributeError:
            self._has_set_text_api = False

        # Direct text-read APIs — no keyboard/mouse required.
        try:
            w.getAccessibleTextInfo.restype = ctypes.c_bool
            w.getAccessibleTextInfo.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.POINTER(_ATI),
                ctypes.c_int,
                ctypes.c_int,
            ]
            w.getAccessibleTextRange.restype = ctypes.c_bool
            w.getAccessibleTextRange.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_wchar_p,
                # AccessBridgeCalls.h declares the buffer length as a C
                # ``short``; widening it here would not change what the
                # bridge reads, so long text is chunked instead.
                ctypes.c_short,
            ]
            self._has_text_api = True
        except AttributeError:
            self._has_text_api = False

        # requestFocus — ask the JVM to focus a specific component. Used
        # before PostMessage(WM_KEYDOWN) so Swing's InputMap sees the
        # window as "focused" and dispatches the key event.
        try:
            w.requestFocus.restype = ctypes.c_bool
            w.requestFocus.argtypes = [ctypes.c_long, ctypes.c_int64]
            self._has_focus_api = True
        except AttributeError:
            self._has_focus_api = False

        # AccessibleAction — invoke button actions by name, no mouse.
        try:
            w.getAccessibleActions.restype = ctypes.c_bool
            w.getAccessibleActions.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.POINTER(_AAI),
            ]
            w.doAccessibleActions.restype = ctypes.c_bool
            w.doAccessibleActions.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.POINTER(_AA_TO_DO),
                ctypes.POINTER(ctypes.c_int),
            ]
            self._has_action_api = True
        except AttributeError:
            self._has_action_api = False

    def pump(self, count: int = 50, interval: float = 0.05) -> None:
        msg = ctypes.wintypes.MSG()
        for _ in range(count):
            while ctypes.windll.user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(interval)

    def is_java_window(self, hwnd: int) -> bool:
        return bool(self._wab.isJavaWindow(hwnd))

    def get_root_context(self, hwnd: int) -> tuple[int, int] | None:
        vm_id = ctypes.c_long()
        ac = ctypes.c_int64()
        if self._wab.getAccessibleContextFromHWND(hwnd, ctypes.byref(vm_id), ctypes.byref(ac)):
            return vm_id.value, ac.value
        return None

    def get_info(self, vm_id: int, ac: int) -> _ACI | None:
        if not ac:
            return None
        info = _ACI()
        if self._wab.getAccessibleContextInfo(vm_id, ac, ctypes.byref(info)):
            return info
        return None

    def get_child(self, vm_id: int, ac: int, index: int) -> int:
        return int(self._wab.getAccessibleChildFromContext(vm_id, ac, index))

    def release(self, vm_id: int, ac: int) -> None:
        """Free the JNI global reference the JVM holds for *ac*.

        Must be called for every context the caller obtained and does not
        keep. Releasing a parent does not invalidate contexts already
        obtained for its children — each is an independent reference.
        """
        if not ac or not self._has_release_api:
            return
        try:
            self._wab.releaseJavaObject(vm_id, ac)
        except Exception:
            pass

    def get_value(self, vm_id: int, ac: int) -> str | None:
        """Read the component's AccessibleValue as the bridge renders it.

        Returns None when the API is missing or the target does not
        implement AccessibleValue — sliders, progress bars and spinners
        do, plain labels and text fields do not.
        """
        if not self._has_value_api:
            return None
        buf = ctypes.create_unicode_buffer(_VALUE_BUFFER)
        if self._wab.getCurrentAccessibleValueFromContext(vm_id, ac, buf, _VALUE_BUFFER):
            return buf.value
        return None

    def set_text_contents(self, vm_id: int, ac: int, text: str) -> bool:
        """Set the component's text via JNI (no keyboard events fired).

        Returns True on success. False when the API is not exported, the
        target component does not implement AccessibleEditableText, or
        *text* is longer than the bridge's packet can carry — the call
        replaces the whole content, so a long string cannot be chunked
        and handing it over would write a truncated value.
        """
        if not self._has_set_text_api or len(text) > _MAX_SET_TEXT:
            return False
        return bool(self._wab.setTextContents(vm_id, ac, text))

    def get_text(self, vm_id: int, ac: int) -> str | None:
        """Read the component's text via JNI (no clipboard/keyboard).

        Returns None when the target does not implement AccessibleText
        or the API is missing. Works for text fields, text areas,
        labels — anything that publishes AccessibleText.
        """
        if not self._has_text_api:
            return None
        ati = _ATI()
        if not self._wab.getAccessibleTextInfo(vm_id, ac, ctypes.byref(ati), 0, 0):
            return None
        n = max(0, ati.charCount)
        if n == 0:
            return ""
        parts: list[str] = []
        start = 0
        while start < n:
            count = min(_TEXT_RANGE_CHUNK, n - start)
            buf = ctypes.create_unicode_buffer(count + 1)
            if not self._wab.getAccessibleTextRange(
                vm_id, ac, start, start + count - 1, buf, count + 1
            ):
                return None
            chunk = buf.value
            if not chunk:
                break
            parts.append(chunk)
            # Advance by what came back, never by what was asked for: a
            # short read would otherwise leave a hole in the middle of the
            # returned text with nothing to signal it.
            start += len(chunk)
        return "".join(parts)

    def request_focus(self, vm_id: int, ac: int) -> bool:
        """Ask the JVM to give input focus to *ac* — no mouse, no keyboard.

        Returns True on success. Essential before dispatching keyboard
        events via PostMessage: Swing's InputMap
        (WHEN_IN_FOCUSED_WINDOW) only fires when the JVM considers the
        JFrame focused. OS focus alone is not enough — the request has
        to reach the AWT event queue.
        """
        if not self._has_focus_api:
            return False
        return bool(self._wab.requestFocus(vm_id, ac))

    def do_action(self, vm_id: int, ac: int, action_name: str = "click") -> bool:
        """Invoke *action_name* on the component via AccessibleAction.

        Uses the JAB API directly — no mouse, no keyboard. Standard
        action names are ``"click"`` (buttons, menu items) and
        ``"toggle"`` (checkboxes). Returns True when the action was
        dispatched, False if the API is unavailable or the target does
        not expose the requested action.

        Never substitutes a different action when *action_name* is
        absent: dispatching the component's only action instead would
        make ``expand()`` click a button and report success.
        """
        if not self._has_action_api:
            return False
        actions = _AAI()
        if not self._wab.getAccessibleActions(vm_id, ac, ctypes.byref(actions)):
            return False
        names = [actions.actionInfo[i].name for i in range(actions.actionsCount)]
        target = action_name.lower()
        chosen: str | None = None
        for n in names:
            if n.lower() == target:
                chosen = n
                break
        if chosen is None:
            return False
        todo = _AA_TO_DO()
        todo.actionsCount = 1
        todo.actions[0].name = chosen
        failure_index = ctypes.c_int(-1)
        return bool(
            self._wab.doAccessibleActions(
                vm_id, ac, ctypes.byref(todo), ctypes.byref(failure_index)
            )
        )


# JABLocator

# JAB ``role_en_US`` strings (javax.accessibility.AccessibleRole), accepted
# verbatim and resolved BEFORE the UIA alias table below. Several spellings
# belong to both vocabularies with different meanings — "text" is the role
# of a JTextField and "window" the role of a JWindow — and an alias entry
# for the same spelling would leave those components unreachable.
_JAB_ROLES: frozenset[str] = frozenset(
    {
        "alert",
        "canvas",
        "check box",
        "column header",
        "combo box",
        "date editor",
        "desktop icon",
        "desktop pane",
        "dialog",
        "directory pane",
        "file chooser",
        "filler",
        "font chooser",
        "frame",
        "glass pane",
        "group box",
        "header",
        "hyperlink",
        "icon",
        "internal frame",
        "label",
        "layered pane",
        "list",
        "list item",
        "menu",
        "menu bar",
        "menu item",
        "option pane",
        "page tab",
        "page tab list",
        "panel",
        "paragraph",
        "password text",
        "popup menu",
        "progress bar",
        "push button",
        "radio button",
        "root pane",
        "row header",
        "ruler",
        "scroll bar",
        "scroll pane",
        "separator",
        "slider",
        "split pane",
        "spin box",
        "status bar",
        "swing component",
        "table",
        "table cell",
        "text",
        "toggle button",
        "tool bar",
        "tool tip",
        "tree",
        "unknown",
        "viewport",
        "window",
    }
)

# UIA control types that are not themselves JAB roles, mapped to the closest
# AccessibleRole so ``Window.get_by_role`` keeps working on a Swing window.
_CT_TO_JAB: dict[str, str] = {
    "button": "push button",
    "calendar": "date editor",
    "checkbox": "check box",
    "combobox": "combo box",
    "custom": "unknown",
    "datagrid": "table",
    "dataitem": "table cell",
    "document": "text",
    "edit": "text",
    "group": "group box",
    "headeritem": "column header",
    "image": "icon",
    "listitem": "list item",
    "menubar": "menu bar",
    "menuitem": "menu item",
    "pane": "panel",
    "progressbar": "progress bar",
    "radiobutton": "radio button",
    "scrollbar": "scroll bar",
    "splitbutton": "push button",
    "statusbar": "status bar",
    "tab": "page tab list",
    "tabitem": "page tab",
    "toolbar": "tool bar",
    "tooltip": "tool tip",
    # Swing surfaces JTree nodes through the cell renderer, so a node
    # carries the renderer's role — "label" for DefaultTreeCellRenderer.
    "treeitem": "label",
}


class JABLocator:
    """Lazy element locator backed directly by the Java Access Bridge API.

    Used automatically by :class:`~dolphin_desktop._window.Window` for Java Swing
    windows (class ``SunAwtFrame``) where UIA does not expose child controls.
    """

    def __init__(
        self,
        hwnd: int,
        *,
        control_type: str | None = None,
        title: str | None = None,
        title_re: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._hwnd = hwnd
        self._control_type = control_type
        self._title = title
        self._title_re = title_re
        self._timeout = timeout
        # Resolve eagerly so an unmappable role fails here instead of being
        # dropped from the match criteria, which would silently widen the
        # search to every node in the tree.
        self._resolved_role = self._resolve_role(control_type)

    # Internal helpers

    @staticmethod
    def _resolve_role(control_type: str | None) -> str | None:
        """Translate *control_type* into a JAB ``role_en_US`` string.

        Accepts both JAB role names (``"push button"``, ``"status bar"``,
        ``"text"``) and the dolphin/UIA vocabulary (``"button"``,
        ``"edit"``, ``"ToolBar"``). JAB names win, so ``role="text"``
        reaches a JTextField instead of being rewritten to the UIA
        meaning of the same word.
        """
        if control_type is None:
            return None
        key = control_type.strip().lower()
        if key in _JAB_ROLES:
            return key
        mapped = _CT_TO_JAB.get(key)
        if mapped is not None:
            return mapped
        from ._exceptions import ElementNotFoundError

        raise ElementNotFoundError(
            f"unknown Java role {control_type!r} — expected a JAB role_en_US "
            f"name ({', '.join(sorted(_JAB_ROLES))}) or a UIA control type "
            f"({', '.join(sorted(_CT_TO_JAB))})",
            hint="Java windows are matched on AccessibleRole, not on UIA control types",
        )

    def _session(self) -> _JABSession:
        return _JABSession.get_or_create()

    def _jab_role(self) -> str | None:
        return self._resolved_role

    def _matches(self, info: _ACI) -> bool:
        import re as _re

        jab_role = self._jab_role()
        if jab_role and info.role_en_US.lower() != jab_role:
            return False
        if self._title and info.name != self._title:
            return False
        if self._title_re and not _re.search(self._title_re, info.name):
            return False
        # Require at least one criterion
        return bool(jab_role or self._title or self._title_re)

    def _resolve_context(self) -> tuple[int, int]:
        """Return (vm_id, root_ac), pumping messages until JAB handshake is received."""
        session = self._session()
        deadline = time.monotonic() + self._timeout
        while True:
            ctx = session.get_root_context(self._hwnd)
            if ctx is not None:
                return ctx
            session.pump(10, 0.1)
            if time.monotonic() >= deadline:
                from ._exceptions import ElementNotFoundError

                raise ElementNotFoundError(
                    f"Java window (HWND {self._hwnd}) not accessible via JAB. "
                    "Ensure jabswitch /enable has been run."
                )

    def _search(self, vm_id: int, ac: int, depth: int = 20) -> tuple[int, _ACI] | None:
        """DFS search for a matching element.

        The caller owns *ac*. Every context obtained for a descendant that
        is not the returned match is released here, so a walk over a large
        Swing tree does not leak a JNI reference per visited node.
        """
        if not ac or depth < 0:
            return None
        session = self._session()
        info = session.get_info(vm_id, ac)
        if info is None:
            return None
        if self._matches(info):
            return ac, info
        for i in range(info.childrenCount):
            child_ac = session.get_child(vm_id, ac, i)
            if not child_ac:
                continue
            try:
                result = self._search(vm_id, child_ac, depth - 1)
            except BaseException:
                session.release(vm_id, child_ac)
                raise
            if result is not None:
                if result[0] != child_ac:
                    session.release(vm_id, child_ac)
                return result
            session.release(vm_id, child_ac)
        return None

    def _release(self, vm_id: int, ac: int) -> None:
        """Release a context obtained by :meth:`_find` / :meth:`_wait_find`.

        Both return an owned context, so every caller must release it once
        it is done reading or acting on it.
        """
        self._session().release(vm_id, ac)

    def _find(self) -> tuple[int, int, _ACI] | None:
        """Find the element; returns (vm_id, ac, info) or None.

        The returned context is owned by the caller — release it with
        :meth:`_release`.
        """
        try:
            vm_id, root_ac = self._resolve_context()
        except Exception:
            return None
        try:
            result = self._search(vm_id, root_ac)
        except Exception:
            # _search raises on its own — an invalid title_re makes
            # _matches raise re.error on every probe — and swallowing that
            # inside the blanket handler stranded root_ac, so a polling
            # exists() leaked one JNI global reference per probe.
            self._session().release(vm_id, root_ac)
            return None
        except BaseException:
            # _search frees the children it walked on any BaseException;
            # root_ac is this frame's to free.
            self._session().release(vm_id, root_ac)
            raise
        if result is not None:
            ac, info = result
            if ac != root_ac:
                self._session().release(vm_id, root_ac)
            return vm_id, ac, info
        self._session().release(vm_id, root_ac)
        return None

    def _wait_find(self) -> tuple[int, int, _ACI]:
        """Like _find but retries until timeout."""
        deadline = time.monotonic() + self._timeout
        last_exc: Exception | None = None
        while True:
            try:
                vm_id, root_ac = self._resolve_context()
            except Exception as exc:
                last_exc = exc
            else:
                try:
                    result = self._search(vm_id, root_ac)
                except Exception as exc:
                    last_exc = exc
                    result = None
                except BaseException:
                    self._session().release(vm_id, root_ac)
                    raise
                if result is not None:
                    ac, info = result
                    if ac != root_ac:
                        self._session().release(vm_id, root_ac)
                    return vm_id, ac, info
                self._session().release(vm_id, root_ac)
            if time.monotonic() >= deadline:
                from ._exceptions import ElementNotFoundError

                raise ElementNotFoundError(
                    f"Java element not found after {self._timeout}s: "
                    f"control_type={self._control_type!r} title={self._title!r}"
                ) from last_exc
            time.sleep(0.3)

    @staticmethod
    def _center(info: _ACI) -> tuple[int, int]:
        return (info.x + info.width // 2, info.y + info.height // 2)

    # Internal: window focus

    def _bring_to_front(self) -> None:
        """Bring the Java window to the foreground before keyboard/mouse operations."""
        try:
            import win32gui  # type: ignore[import-untyped]

            win32gui.SetForegroundWindow(self._hwnd)
            time.sleep(0.05)
        except Exception:
            pass

    # Query methods

    def exists(self, timeout: float = 0.0) -> bool:
        """Return True when the element resolves, retrying for *timeout* seconds.

        Same signature as :meth:`Locator.exists` and
        :meth:`SapLocator.exists`; ``timeout=0`` is a single probe.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            result = self._find()
            if result is not None:
                self._release(result[0], result[1])
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def is_visible(self) -> bool:
        return "visible" in self._states()

    def is_enabled(self) -> bool:
        return "enabled" in self._states()

    def is_checked(self) -> bool:
        return "checked" in self._states()

    def _states(self) -> set[str]:
        """Return the AccessibleStateSet as a set of lowercased words.

        Java publishes states as a comma-separated string
        (``"enabled,visible,focused"``), so splitting on the comma gives
        exact-token membership — whereas a plain substring match
        would classify ``"disabled"`` as ``"enabled"``, ``"prechecked"``
        as ``"checked"``, and so on if the JVM ever adds a new state
        containing one of our target substrings.
        """
        result = self._find()
        if result is None:
            return set()
        vm_id, ac, info = result
        self._release(vm_id, ac)
        raw = info.states_en_US.lower()
        return {word.strip() for word in raw.split(",") if word.strip()}

    def bounding_box(self) -> dict[str, int]:
        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        return {
            "left": info.x,
            "top": info.y,
            "right": info.x + info.width,
            "bottom": info.y + info.height,
            "width": info.width,
            "height": info.height,
        }

    def text(self) -> str:
        """Return the component's visible text.

        Prefers the direct JAB ``getAccessibleTextRange`` API — works
        for any component that publishes AccessibleText, regardless of
        focus, and does not fire any keyboard / mouse events.

        Falls back to the click + Ctrl-A + Ctrl-C + clipboard-read
        sequence only when the direct API is unavailable. That path
        needs the target window to be foregrounded and — on locked-
        down systems — may still fail with SetCursorPos errors.
        """
        vm_id, ac, info = self._wait_find()
        session = self._session()
        try:
            direct = session.get_text(vm_id, ac)
        finally:
            self._release(vm_id, ac)
        if direct is not None:
            return direct

        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        from ._clipboard import Clipboard

        self._bring_to_front()
        try:
            mouse.click(coords=self._center(info))
        except Exception:
            pass
        time.sleep(0.1)
        Clipboard.clear()
        try:
            keyboard.send_keys("^a^c", pause=0.05)
        except RuntimeError:
            return ""
        time.sleep(0.15)
        return Clipboard.get_text() or ""

    def value(self) -> str:
        """Return the component's AccessibleValue, falling back to its text.

        AccessibleValue is tried first, and the ordering is the whole point:
        a JSlider or JScrollBar publishes it, so those never reach ``text()``'s
        last-resort path — which clicks the component's centre and would drag
        the thumb to the midpoint, changing the value it was asked to read (and
        then returning the clipboard instead of the value).

        A JTextField publishes no AccessibleValue, so it still falls through to
        ``text()`` and keeps working.
        """
        vm_id, ac, _info = self._wait_find()
        session = self._session()
        try:
            direct = session.get_value(vm_id, ac)
        finally:
            self._release(vm_id, ac)
        # Truthiness, not ``is not None``: the bridge answers TRUE with an empty
        # buffer for a component that has no AccessibleValue at all — a
        # JTextField among them — so an empty reading means "ask somewhere else",
        # not "the value is blank". A component that really does publish a value
        # never renders it as an empty string.
        if direct:
            return direct
        return self.text()

    def description(self) -> str:
        """Return the component's accessible description string (info.description).

        Useful for Swing labels or status bars that publish their
        dynamic text through ``AccessibleContext.setAccessibleDescription``
        rather than through the standard AccessibleText interface.
        """
        result = self._find()
        if result is None:
            return ""
        vm_id, ac, info = result
        self._release(vm_id, ac)
        return info.description or ""

    def get_attribute(self, name: str, default: Any = _MISSING) -> Any:
        """Return one field of the JAB AccessibleContextInfo by *name*.

        Raises ``AttributeError`` for a name JAB does not publish — the
        set is fixed (see :data:`_JAB_ATTRIBUTES`), so anything outside it
        is a typo that would otherwise read back as ``None`` and let an
        assertion pass against a field never fetched.

        Still returns ``None`` when the element itself is not found, which
        is how every reader on this locator reports an absent element.
        Pass *default* to opt back into a non-raising lookup.
        """
        if name not in _JAB_ATTRIBUTES:
            if default is not _MISSING:
                return default
            raise AttributeError(
                f"Java Access Bridge publishes no attribute {name!r}. "
                f"Available: {', '.join(_JAB_ATTRIBUTES)}"
            )
        result = self._find()
        if result is None:
            return None
        vm_id, ac, info = result
        self._release(vm_id, ac)
        return getattr(info, name)

    # Action methods

    def click(self) -> JABLocator:
        """Click the component.

        Uses the direct JAB ``doAccessibleActions`` API when the target
        exposes an action (buttons, menu items, check boxes) — no
        mouse cursor movement required. Falls back to a physical mouse
        click otherwise.
        """
        vm_id, ac, info = self._wait_find()
        session = self._session()
        try:
            dispatched = session.do_action(vm_id, ac, "click")
        finally:
            self._release(vm_id, ac)
        if dispatched:
            return self
        from pywinauto import mouse  # type: ignore[import-untyped]

        self._bring_to_front()
        try:
            mouse.click(coords=self._center(info))
        except Exception:
            pass
        return self

    # ------------------------------------------------------------------
    # Programmatic (headless-safe) counterparts
    #
    # These parallel the UIA pattern methods on :class:`Locator`. Every
    # method dispatches directly through the JAB API, never falls back
    # to a physical mouse click, and raises
    # :class:`UnsupportedPatternError` when the target does not expose
    # the requested AccessibleAction. See
    # ``docs/tutorials/headless-mode.md`` for the "click vs invoke"
    # decision tree.
    # ------------------------------------------------------------------

    def _do_action_or_raise(
        self,
        *,
        action_name: str,
        jab_action: str,
        hint_extra: str,
    ) -> JABLocator:
        """Dispatch *jab_action* via JAB or raise ``UnsupportedPatternError``.

        Deliberately does NOT fall back to a physical mouse click —
        programmatic actions must fail cleanly so the caller either
        picks a different primitive or drops to ``.click()`` explicitly.
        """
        from ._exceptions import UnsupportedPatternError

        vm_id, ac, _info = self._wait_find()
        session = self._session()
        try:
            dispatched = session.do_action(vm_id, ac, jab_action)
        finally:
            self._release(vm_id, ac)
        if dispatched:
            return self
        raise UnsupportedPatternError(
            f"{action_name}() failed — the target Java component does not "
            f"expose AccessibleAction {jab_action!r}",
            hint=hint_extra,
        )

    def invoke(self) -> JABLocator:
        """Fire the component's default action via JAB (no mouse).

        JAB counterpart to :meth:`Locator.invoke` — dispatches the
        ``"click"`` ``AccessibleAction`` on the target Java component,
        which fires the same ``ActionEvent`` chain a physical click
        would fire without any cursor movement. Works headless / RDP /
        locked workstations.

        Raises :class:`UnsupportedPatternError` when the target does
        not expose an invokable action (typically labels, text areas —
        use :meth:`type_text` instead).
        """
        return self._do_action_or_raise(
            action_name="invoke",
            jab_action="click",
            hint_extra=(
                "for check boxes / toggle buttons use .toggle(); for list "
                "items / radio buttons use .select(); for headed E2E use "
                ".click()"
            ),
        )

    def toggle(self) -> JABLocator:
        """Flip a check box / toggle button via JAB (no mouse).

        Sends the ``"toggle"`` ``AccessibleAction`` — falling back to
        ``"click"`` when the JVM only exposes the click name (many
        Swing L&Fs collapse both into a single click action on
        checkboxes).

        Raises :class:`UnsupportedPatternError` when the target does
        not expose either action.
        """
        from ._exceptions import UnsupportedPatternError

        vm_id, ac, _info = self._wait_find()
        session = self._session()
        try:
            dispatched = any(session.do_action(vm_id, ac, action) for action in ("toggle", "click"))
        finally:
            self._release(vm_id, ac)
        if dispatched:
            return self
        raise UnsupportedPatternError(
            "toggle() failed — the target Java component does not expose "
            "a toggle or click AccessibleAction",
            hint=(
                "verify the target is a JCheckBox or a checkable JToggleButton; "
                "for radio buttons use .select(); for headed E2E use .click()"
            ),
        )

    def select(self) -> JABLocator:
        """Select the component via JAB's ``click`` AccessibleAction.

        Applicable to any Swing component that participates in a
        selection group — ``JRadioButton`` inside a ``ButtonGroup``,
        ``JList`` items, ``JTree`` nodes. All of them fire the same
        ``ActionEvent`` chain in response to the ``"click"`` action
        that a physical click would trigger, so :meth:`invoke` and
        :meth:`select` collapse into the same dispatch — but expose
        both names so tests read semantically.

        For selecting the N-th item of a ``JList`` / ``JComboBox``
        container by index or by text, use :meth:`select_item`
        instead.

        Raises :class:`UnsupportedPatternError` when the component
        does not expose a click action (labels, borders, decorative
        panels).
        """
        return self._do_action_or_raise(
            action_name="select",
            jab_action="click",
            hint_extra=(
                "for a JList / JComboBox item picked by index or text use "
                ".select_item(...); for buttons use .invoke(); for check "
                "boxes use .toggle(); for headed E2E use .click()"
            ),
        )

    def expand(self) -> JABLocator:
        """Expand a Tree node / expander via JAB.

        Sends the ``"expand"`` ``AccessibleAction``. Raises
        :class:`UnsupportedPatternError` when unsupported (most Swing
        widgets don't publish it — trees do).
        """
        return self._do_action_or_raise(
            action_name="expand",
            jab_action="expand",
            hint_extra=(
                "the target does not expose an expand action; "
                "trees / expanders publish this — regular buttons don't. "
                "For headed E2E use .click()"
            ),
        )

    def collapse(self) -> JABLocator:
        """Collapse a Tree node / expander via JAB.

        Inverse of :meth:`expand`. Raises
        :class:`UnsupportedPatternError` when unsupported.
        """
        return self._do_action_or_raise(
            action_name="collapse",
            jab_action="collapse",
            hint_extra=(
                "the target does not expose a collapse action; use .click() "
                "in headed mode if the widget only responds to mouse events"
            ),
        )

    def set_value(self, text: str) -> JABLocator:
        """Set the target's text via JAB ``setTextContents`` (no keystrokes).

        Programmatic counterpart to :meth:`type_text` — uses the direct
        JNI text-write API. Works even when the JVM window is in the
        background or the foreground-lock is active.

        Raises :class:`ValueError` when *text* is longer than the
        bridge's ``setTextContents`` packet can carry, and
        :class:`UnsupportedPatternError` when the JVM does not expose
        ``setTextContents`` (older JAB builds) or when the target does
        not implement ``AccessibleEditableText`` (labels, buttons,
        read-only fields).
        """
        from ._exceptions import UnsupportedPatternError

        if len(text) > _MAX_SET_TEXT:
            raise ValueError(
                f"set_value() accepts at most {_MAX_SET_TEXT} characters, got {len(text)} — "
                "the JAB setTextContents packet carries a wchar_t[1024] buffer and the "
                "call replaces the whole content, so longer text cannot be chunked; "
                "use type_text() or set_text() for it"
            )
        vm_id, ac, _info = self._wait_find()
        session = self._session()
        try:
            written = session.set_text_contents(vm_id, ac, text)
        finally:
            self._release(vm_id, ac)
        if written:
            return self
        raise UnsupportedPatternError(
            "set_value() failed — the target Java component does not "
            "expose setTextContents (either the JVM's JAB build is older "
            "than the added API, or the widget is not editable)",
            hint=(
                "verify the target is a JTextField / JTextArea / JEditorPane; "
                "for headed E2E use .type_text() with real keystrokes"
            ),
        )

    def double_click(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        mouse.double_click(coords=self._center(info))
        return self

    def right_click(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        mouse.right_click(coords=self._center(info))
        return self

    def hover(self) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        mouse.move(coords=self._center(info))
        return self

    def focus(self) -> JABLocator:
        """Give the component keyboard focus without activating it.

        Prefers the bridge's ``requestFocus`` over ``click()``: aliasing the
        two meant ``form.item("SUBMIT").focus()`` dispatched the button's
        AccessibleAction and committed the transaction, and
        ``OracleFormsItem.type_text(text, clear=False)`` — which focuses before
        replaying keystrokes — pressed every button it was asked to type into.

        Two different failures are kept apart. A bridge that **exports**
        ``requestFocus`` and still refuses raises, because something is wrong
        with that component. A bridge build that does **not** export it at all
        has no other way to move focus, so this falls back to the activating
        path with a warning rather than breaking the append flow outright.
        """
        from ._exceptions import UnsupportedPatternError
        from ._logging import get_logger

        session = self._session()
        vm_id, ac, _info = self._wait_find()
        try:
            if session.request_focus(vm_id, ac):
                return self
            has_api = bool(getattr(session, "_has_focus_api", False))
        finally:
            self._release(vm_id, ac)

        if has_api:
            raise UnsupportedPatternError(
                f"Java component {self!r} refused keyboard focus",
                hint=(
                    "the Access Bridge accepted the request but the component "
                    "did not take focus — check it is showing and enabled; "
                    "click() would activate it instead, but that also fires "
                    "its action"
                ),
            )
        get_logger("java").warning(
            "this Access Bridge build exports no requestFocus — focusing %r by "
            "activating it instead, which fires the action of a button or menu item",
            self,
        )
        return self.click()

    def type_text(self, text: str, pause: float = 0.05) -> JABLocator:
        from pywinauto import keyboard  # type: ignore[import-untyped]

        keyboard.send_keys(text, pause=pause, with_spaces=True, with_tabs=True, with_newlines=True)
        return self

    def set_text(self, text: str) -> JABLocator:
        """Set the target's text.

        Tries the direct JAB ``setTextContents`` API first (writes via
        JNI, no keyboard events, works even when the target window is
        in the background or the foreground-lock is active). Falls back
        to the click + Ctrl-A + type sequence when the API is not
        available, the target does not implement AccessibleEditableText,
        or *text* exceeds what the bridge's packet can carry.
        """
        vm_id, ac, info = self._wait_find()
        session = self._session()
        try:
            written = session.set_text_contents(vm_id, ac, text)
        finally:
            self._release(vm_id, ac)
        if written:
            return self
        # Fallback path — best-effort, requires foreground focus.
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        self._bring_to_front()
        try:
            mouse.click(coords=self._center(info))
        except Exception:
            pass
        time.sleep(0.1)
        keyboard.send_keys("^a", pause=0.05)
        if text:
            keyboard.send_keys(
                text, pause=0.05, with_spaces=True, with_tabs=True, with_newlines=True
            )
        else:
            keyboard.send_keys("{DELETE}", pause=0.05)
        return self

    def clear(self) -> JABLocator:
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        self._bring_to_front()
        mouse.click(coords=self._center(info))
        time.sleep(0.1)
        keyboard.send_keys("^a{DELETE}", pause=0.05)
        return self

    def press_key(self, key: str) -> JABLocator:
        from pywinauto import keyboard  # type: ignore[import-untyped]

        keyboard.send_keys(key)
        return self

    def select_item(self, item: int | str) -> JABLocator:
        """Select a ComboBox item by index or text."""
        vm_id, ac, _info = self._wait_find()

        if isinstance(item, int):
            index: int = item
        else:
            try:
                idx = self._combo_text_index(vm_id, ac, item)
            except BaseException:
                self._release(vm_id, ac)
                raise
            if idx is None:
                self._release(vm_id, ac)
                raise ValueError(f"ComboBox item {item!r} not found")
            index = idx

        session = self._session()
        wab = session._wab
        # addAccessibleSelectionFromContext calls JComboBox.setSelectedIndex(i)
        # which fires ActionEvent → updates all listeners
        if not hasattr(wab, "_add_sel_setup"):
            wab.addAccessibleSelectionFromContext.argtypes = [
                ctypes.c_long,
                ctypes.c_int64,
                ctypes.c_int,
            ]
            wab.addAccessibleSelectionFromContext.restype = None
            wab._add_sel_setup = True
        wab.addAccessibleSelectionFromContext(vm_id, ac, index)
        self._release(vm_id, ac)
        time.sleep(0.1)
        return self

    def _combo_text_index(self, vm_id: int, ac: int, text: str) -> int | None:
        """Return the index of *text* in a ComboBox's item list.

        Returns an index, never a context, so every context obtained while
        walking is released before returning.
        """
        session = self._session()

        def find_list(vm_id: int, ac: int, depth: int = 0) -> int | None:
            if depth > 8:
                return None
            info = session.get_info(vm_id, ac)
            if info is None:
                return None
            if info.role_en_US == "list":
                for i in range(info.childrenCount):
                    item_ac = session.get_child(vm_id, ac, i)
                    if not item_ac:
                        continue
                    item_info = session.get_info(vm_id, item_ac)
                    session.release(vm_id, item_ac)
                    if item_info and item_info.name == text:
                        return i
                return None
            for i in range(min(info.childrenCount, 20)):
                child_ac = session.get_child(vm_id, ac, i)
                if not child_ac:
                    continue
                result = find_list(vm_id, child_ac, depth + 1)
                session.release(vm_id, child_ac)
                if result is not None:
                    return result
            return None

        return find_list(vm_id, ac)

    def check(self) -> JABLocator:
        if not self.is_checked():
            self.click()
        return self

    def uncheck(self) -> JABLocator:
        if self.is_checked():
            self.click()
        return self

    def scroll(self, direction: str, amount: int = 3) -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        cx, cy = self._center(info)
        wheel = amount if direction in ("up", "right") else -amount
        mouse.scroll(coords=(cx, cy), wheel_dist=wheel)
        return self

    def drag_to(self, target: Any, *, duration: float = 0.5, button: str = "left") -> JABLocator:
        from pywinauto import mouse  # type: ignore[import-untyped]

        vm_id, ac, src_info = self._wait_find()
        self._release(vm_id, ac)
        src = self._center(src_info)
        if hasattr(target, "_wait_find"):
            dst_vm_id, dst_ac, dst_info = target._wait_find()
            target._release(dst_vm_id, dst_ac)
            dst = self._center(dst_info)
        else:
            dst = target
        mouse.press(coords=src, button=button)
        time.sleep(duration)
        mouse.release(coords=dst, button=button)
        return self

    def select_text(self) -> JABLocator:
        from pywinauto import keyboard, mouse  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        mouse.click(coords=self._center(info))
        keyboard.send_keys("^a")
        return self

    def scroll_into_view(self) -> JABLocator:
        return self

    def screenshot(self) -> Any:
        """Grab the component's on-screen rectangle as a PIL image.

        ``all_screens=True`` is mandatory: without it Pillow captures the
        primary monitor only, so a Swing window on a secondary display comes
        back black and a bbox outside the primary monitor is cropped against
        the wrong framebuffer instead of against absolute screen coordinates.
        """
        from PIL import ImageGrab  # type: ignore[import-untyped]

        vm_id, ac, info = self._wait_find()
        self._release(vm_id, ac)
        return ImageGrab.grab(
            bbox=(info.x, info.y, info.x + info.width, info.y + info.height),
            all_screens=True,
        )

    # Waiting

    def wait_for(self, state: str, timeout: float | None = None) -> JABLocator:
        t = timeout if timeout is not None else self._timeout
        deadline = time.monotonic() + t
        while True:
            if state in ("visible", "exists") and self.exists():
                return self
            if state == "enabled" and self.is_enabled():
                return self
            if state in ("hidden", "invisible") and not self.is_visible():
                return self
            if time.monotonic() >= deadline:
                from ._exceptions import WaitTimeoutError

                raise WaitTimeoutError(f"Timeout waiting for Java element state {state!r}")
            time.sleep(0.3)

    def wait_until_hidden(self, timeout: float | None = None) -> JABLocator:
        return self.wait_for("hidden", timeout)

    def wait_until_enabled(self, timeout: float | None = None) -> JABLocator:
        return self.wait_for("enabled", timeout)

    def wait_for_checked(
        self,
        *,
        checked: bool = True,
        timeout: float | None = None,
        poll_interval: float = 0.15,
    ) -> JABLocator:
        """Poll :meth:`is_checked` until it equals ``checked``.

        JAB counterpart to :meth:`Locator.wait_for_checked`. Auto-
        waiting replacement for ``sleep(N); assert loc.is_checked() ==
        expected`` after toggling a JCheckBox / JRadioButton via
        :meth:`toggle` or :meth:`select`.
        """
        from ._exceptions import WaitTimeoutError as _WaitTimeoutError

        t = timeout if timeout is not None else self._timeout
        deadline = time.monotonic() + t
        while time.monotonic() < deadline:
            try:
                if bool(self.is_checked()) == checked:
                    return self
            except Exception:
                pass
            time.sleep(poll_interval)
        raise _WaitTimeoutError(
            f"Java element {self._control_type!r}/{self._title!r} did not "
            f"become {'checked' if checked else 'unchecked'} within {t}s"
        )

    def wait_for_text(
        self,
        text: str | None = None,
        *,
        text_re: str | None = None,
        contains: bool = True,
        source: str = "text",
        timeout: float | None = None,
        poll_interval: float = 0.15,
    ) -> JABLocator:
        """Poll the Java component until its text / description matches.

        JAB counterpart to :meth:`Locator.wait_for_text` — replaces the
        ``sleep(N); assert ... in locator.text()`` antipattern with a
        deterministic wait. Poll stops the moment the string matches.

        Args:
            text: Substring to look for (or exact match if
                ``contains=False``).
            text_re: Regex to match. Mutually exclusive with ``text``.
            contains: True for substring, False for equality.
            source: Which JAB read to poll — ``"text"`` (default —
                reads via ``getAccessibleTextRange``, works for text
                fields / memos), or ``"description"`` (reads
                ``AccessibleContext.description``, the canonical path
                for JLabel status text updated via
                ``setAccessibleDescription``). Swing labels do not
                implement ``AccessibleText``, so status assertions
                should use ``source="description"``.
            timeout: Seconds to wait. Defaults to the locator's own
                timeout.
            poll_interval: Seconds between reads (default 0.15).

        Raises :class:`WaitTimeoutError` on miss, including the last
        seen value in the message.
        """
        from ._exceptions import WaitTimeoutError as _WaitTimeoutError

        if (text is None) == (text_re is None):
            raise ValueError("wait_for_text requires exactly one of text= or text_re=")
        if text == "" and contains:
            raise ValueError(
                "wait_for_text(text='', contains=True) always matches immediately — "
                "use contains=False to wait for an exactly-empty field"
            )
        if source not in ("text", "description"):
            raise ValueError(f"source must be 'text' or 'description', got {source!r}")

        import re as _re

        pat = _re.compile(text_re) if text_re else None
        reader = self.description if source == "description" else self.text
        t = timeout if timeout is not None else self._timeout
        deadline = time.monotonic() + t
        current = ""
        while time.monotonic() < deadline:
            try:
                current = reader() or ""
            except Exception:
                current = ""
            if text is not None:
                if contains:
                    if text in current:
                        return self
                else:
                    if current == text:
                        return self
            elif pat is not None:
                if pat.search(current):
                    return self
            time.sleep(poll_interval)
        target = text if text is not None else f"regex {text_re!r}"
        raise _WaitTimeoutError(
            f"Java element {self._control_type!r}/{self._title!r} "
            f"{source} did not match {target!r} within {t}s "
            f"(last seen: {current!r})"
        )

    # Collections

    def close(self) -> None:
        """Release any AccessibleContext this locator retains.

        No-op for a lazily-resolving locator, which owns none; overridden
        by :class:`_JABResolvedLocator`. Always safe to call twice.
        """

    def count(self) -> int:
        items = self.all()
        try:
            return len(items)
        finally:
            for item in items:
                item.close()

    def all(self, depth: int | None = None) -> list[JABLocator]:
        results: list[JABLocator] = []
        try:
            vm_id, root_ac = self._resolve_context()
        except Exception:
            return results
        try:
            self._collect(vm_id, root_ac, results, 20 if depth is None else depth)
        except BaseException as exc:
            # _collect already released every context it did not hand out,
            # so only the locators appended before the walk died still own
            # one. Closing those here — rather than leaving them to __del__
            # — keeps the release on this thread even for a KeyboardInterrupt.
            for item in results:
                item.close()
            results.clear()
            if not isinstance(exc, Exception):
                raise
        return results

    def _collect(self, vm_id: int, ac: int, results: list, depth: int) -> bool:
        """Collect every match at or below *ac*; returns True when *ac* is retained.

        *ac* is consumed: either the ``_JABResolvedLocator`` appended to
        *results* takes ownership of it — which the return value reports —
        or it is released before this returns, on the raising path as much
        as on the normal one. Letting the caller release a context this
        walk had already handed to a locator gave the JVM a second
        ``releaseJavaObject`` for one JNI global reference, since ``all()``
        closes everything in *results*.
        """
        session = self._session()
        retained = False
        try:
            if not ac or depth < 0:
                return False
            info = session.get_info(vm_id, ac)
            if info is None:
                return False
            retained = self._matches(info)
            if retained:
                results.append(
                    _JABResolvedLocator(self._hwnd, vm_id, ac, info, self._timeout, session=session)
                )
            for i in range(info.childrenCount):
                child_ac = session.get_child(vm_id, ac, i)
                if child_ac:
                    self._collect(vm_id, child_ac, results, depth - 1)
            return retained
        finally:
            if ac and not retained:
                session.release(vm_id, ac)

    def nth(self, index: int) -> JABLocator:
        items = self.all()
        wanted = items[index] if 0 <= index < len(items) else None
        for item in items:
            if item is not wanted:
                item.close()
        if wanted is None:
            from ._exceptions import ElementNotFoundError

            raise ElementNotFoundError(f"No Java element at index {index}")
        return wanted

    # Chaining

    def timeout(self, secs: float) -> JABLocator:
        return JABLocator(
            self._hwnd,
            control_type=self._control_type,
            title=self._title,
            title_re=self._title_re,
            timeout=secs,
        )

    def locator(self, **criteria: Any) -> JABLocator:
        return JABLocator(
            self._hwnd,
            control_type=criteria.get("control_type", self._control_type),
            title=criteria.get("title", self._title),
            title_re=criteria.get("title_re", self._title_re),
            timeout=self._timeout,
        )

    def __repr__(self) -> str:
        return (
            f"JABLocator(hwnd={self._hwnd}, "
            f"control_type={self._control_type!r}, title={self._title!r})"
        )


class _JABResolvedLocator(JABLocator):
    """Already-found JABLocator; skips tree search.

    Owns the AccessibleContext it was built from — the JVM holds a JNI
    global reference for it until :meth:`close` runs — so a caller that
    enumerates a tree must close every locator it does not keep.
    """

    def __init__(
        self,
        hwnd: int,
        vm_id: int,
        ac: int,
        info: _ACI,
        timeout: float,
        *,
        owner: _JABResolvedLocator | None = None,
        session: Any = None,
    ) -> None:
        super().__init__(hwnd, timeout=timeout)
        self._vm_id = vm_id
        self._ac = ac
        self._info = info
        # A clone shares the original's single retained context. Holding
        # the owner keeps that context alive and leaves the one release
        # to the owner.
        self._owner = owner
        # The session that handed out the context, captured so the
        # finalizer never has to build one during interpreter teardown.
        self._retained_session = session

    def _live_ac(self) -> int:
        """The shared retained context, or 0 once its owner released it.

        A clone carries a copy of the owner's context but not the single
        release that goes with it, so reading its own copy after
        ``owner.close()`` would drive a JNI reference the JVM has already
        reclaimed.
        """
        owner = self._owner
        return owner._live_ac() if owner is not None else self._ac

    def _find(self) -> tuple[int, int, _ACI] | None:
        """Re-read the retained context so state queries see the live JVM.

        Serving the snapshot captured at collect time would freeze
        ``is_checked`` / ``is_visible`` / ``description`` and make every
        ``wait_for_*`` poll an unchanging value until it times out.
        """
        ac = self._live_ac()
        if not ac:
            return None
        info = self._session().get_info(self._vm_id, ac)
        if info is None:
            return None
        self._info = info
        return self._vm_id, ac, info

    def _wait_find(self) -> tuple[int, int, _ACI]:
        result = self._find()
        if result is None:
            from ._exceptions import ElementNotFoundError

            raise ElementNotFoundError(
                "the Java component this locator resolved to is no longer "
                "readable — it was disposed, or its context was closed",
            )
        return result

    def _release(self, vm_id: int, ac: int) -> None:
        # This locator retains its context for its whole lifetime, so a
        # caller finishing one operation must not free it.
        return None

    def close(self) -> None:
        ac, self._ac = self._ac, 0
        if not ac or self._owner is not None:
            return
        session = self._retained_session or self._session()
        session.release(self._vm_id, ac)

    def __del__(self) -> None:
        session = self._retained_session
        if session is None:
            return
        # The bridge dispatches its calls through the message loop of the
        # thread that called Windows_run, so releaseJavaObject belongs on
        # that thread. __del__ runs wherever the last reference happens to
        # be dropped; from a foreign thread the reference is left to the
        # JVM's own teardown rather than released across threads.
        owner_thread = getattr(session, "_thread_ident", None)
        if owner_thread is not None and owner_thread != threading.get_ident():
            return
        try:
            self.close()
        except Exception:
            pass

    def timeout(self, secs: float) -> _JABResolvedLocator:
        return _JABResolvedLocator(
            self._hwnd,
            self._vm_id,
            self._live_ac(),
            self._info,
            secs,
            owner=self._owner or self,
            session=self._retained_session,
        )
