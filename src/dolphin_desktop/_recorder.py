"""Action recorder — captures global mouse/keyboard events and emits dolphin Python code.

Usage (headless)::

    from dolphin_desktop._recorder import Recorder

    rec = Recorder(app="Notepad")
    rec.start()
    print("Recording... press Ctrl+F12 to stop")
    rec.wait()
    rec.generate_code("test_recorded.py")

CLI::

    dolphin record --output test_recorded.py --app "Notepad"
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as _wt
import datetime
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Windows constants
# ---------------------------------------------------------------------------

_WH_MOUSE_LL = 14
_WH_KEYBOARD_LL = 13

_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONDBLCLK = 0x0203
_WM_RBUTTONDOWN = 0x0204
_WM_KEYDOWN = 0x0100
_WM_SYSKEYDOWN = 0x0104
_WM_QUIT = 0x0012

_HC_ACTION = 0

_VK_SHIFT = 0x10
_VK_LSHIFT = 0xA0
_VK_RSHIFT = 0xA1
_VK_CONTROL = 0x11
_VK_LCONTROL = 0xA2
_VK_RCONTROL = 0xA3
_VK_MENU = 0x12
_VK_LMENU = 0xA4
_VK_RMENU = 0xA5

_MODIFIER_VKS = {
    _VK_SHIFT,
    _VK_LSHIFT,
    _VK_RSHIFT,
    _VK_CONTROL,
    _VK_LCONTROL,
    _VK_RCONTROL,
    _VK_MENU,
    _VK_LMENU,
    _VK_RMENU,
    0x5B,
    0x5C,  # LWIN, RWIN
    0x14,
    0x90,
    0x91,  # CAPSLOCK, NUMLOCK, SCROLLLOCK
}

# VK code → pywinauto send_keys format
_SPECIAL_KEY_MAP: dict[int, str] = {
    0x08: "{BACKSPACE}",
    0x09: "{TAB}",
    0x0D: "{ENTER}",
    0x1B: "{ESC}",
    0x21: "{PGUP}",
    0x22: "{PGDN}",
    0x23: "{END}",
    0x24: "{HOME}",
    0x25: "{LEFT}",
    0x26: "{UP}",
    0x27: "{RIGHT}",
    0x28: "{DOWN}",
    0x2D: "{INSERT}",
    0x2E: "{DELETE}",
    0x70: "{F1}",
    0x71: "{F2}",
    0x72: "{F3}",
    0x73: "{F4}",
    0x74: "{F5}",
    0x75: "{F6}",
    0x76: "{F7}",
    0x77: "{F8}",
    0x78: "{F9}",
    0x79: "{F10}",
    0x7A: "{F11}",
    0x7B: "{F12}",
}

# ---------------------------------------------------------------------------
# ctypes hook structures
# ---------------------------------------------------------------------------


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _wt.POINT),
        ("mouseData", _wt.DWORD),
        ("flags", _wt.DWORD),
        ("time", _wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(_wt.ULONG)),
    ]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", _wt.DWORD),
        ("scanCode", _wt.DWORD),
        ("flags", _wt.DWORD),
        ("time", _wt.DWORD),
        ("dwExtraInfo", ctypes.POINTER(_wt.ULONG)),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, _wt.WPARAM, _wt.LPARAM)

_user32 = ctypes.windll.user32

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RecordedAction:
    """A single recorded user action."""

    kind: str  # "click" | "double_click" | "right_click" | "type_text" | "press_key"
    window_title: str
    window_class: str
    selector: dict[str, str]  # suggested_selector-style dict
    data: str = ""  # text for type_text; key spec for press_key
    x: int = 0
    y: int = 0
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Keyboard translation
# ---------------------------------------------------------------------------


def _to_unicode(vk: int, scan: int, shift: bool) -> str:
    """Return the Unicode character for a VK code using the current keyboard layout."""
    try:
        state = (ctypes.c_ubyte * 256)()
        if shift:
            state[_VK_SHIFT] = 0x80
        buf = ctypes.create_unicode_buffer(8)
        n = _user32.ToUnicode(vk, scan, state, buf, 8, 0)
        if n == 1:
            return buf.value
    except Exception:
        pass
    return ""


def _vk_to_sendkeys(vk: int, scan: int, shift: bool, ctrl: bool, alt: bool) -> str | None:
    """Convert a virtual key + modifiers to pywinauto send_keys format.

    Returns ``None`` for modifier-only keys (they should be ignored).
    """
    if vk in _MODIFIER_VKS:
        return None

    prefix = ""
    if ctrl:
        prefix += "^"
    if alt:
        prefix += "%"

    if vk in _SPECIAL_KEY_MAP:
        suffix = _SPECIAL_KEY_MAP[vk]
        if shift and not ctrl and not alt:
            return f"+{suffix}"
        return f"{prefix}{suffix}"

    # Plain printable character (no Ctrl/Alt)
    if not ctrl and not alt:
        char = _to_unicode(vk, scan, shift)
        if char and char.isprintable() and char != "\t":
            return char

    # Ctrl+letter shortcut (^a … ^z)
    if ctrl and 0x41 <= vk <= 0x5A:
        return f"^{chr(vk + 0x20)}"

    # Ctrl+digit
    if ctrl and 0x30 <= vk <= 0x39:
        return f"^{chr(vk)}"

    return None


# ---------------------------------------------------------------------------
# Selector → dolphin API call
# ---------------------------------------------------------------------------


def _selector_to_call(sel: dict[str, str]) -> str:
    """Convert a selector dict to a Window method chain fragment."""
    if not sel:
        return "locator()  # no selector found"
    keys = list(sel.keys())
    if keys == ["auto_id"]:
        return f'get_by_automation_id("{sel["auto_id"]}")'
    if set(keys) == {"title", "control_type"}:
        return f'get_by_role("{sel["control_type"]}", name="{sel["title"]}")'
    if keys == ["title"]:
        return f'get_by_title("{sel["title"]}")'
    if keys == ["class_name"]:
        return f'get_by_class("{sel["class_name"]}")'
    parts = [f'{k}="{v}"' for k, v in sel.items()]
    return f"locator({', '.join(parts)})"


def _action_to_line(action: RecordedAction, win_var: str) -> str:
    """Convert a RecordedAction to a single Python source line."""
    call = _selector_to_call(action.selector)
    text = action.data.replace("\\", "\\\\").replace('"', '\\"')
    if action.kind == "click":
        return f"    {win_var}.{call}.click()"
    if action.kind == "double_click":
        return f"    {win_var}.{call}.double_click()"
    if action.kind == "right_click":
        return f"    {win_var}.{call}.right_click()"
    if action.kind == "type_text":
        return f'    {win_var}.{call}.type_text("{text}")'
    if action.kind == "press_key":
        return f'    {win_var}.{call}.press_key("{text}")'
    return f"    # {action.kind}: {text}"


# ---------------------------------------------------------------------------
# Element resolution helpers
# ---------------------------------------------------------------------------


def _get_root_hwnd(hwnd: int) -> int:
    try:
        import win32con
        import win32gui

        root = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
        return root if root else hwnd
    except Exception:
        return hwnd


def _get_window_info(hwnd: int) -> tuple[str, str]:
    try:
        import win32gui

        return win32gui.GetWindowText(hwnd) or "", win32gui.GetClassName(hwnd) or ""
    except Exception:
        return "", ""


def _element_at_point(x: int, y: int) -> dict[str, str]:
    try:
        from dolphin_desktop._spy import _element_info_from_point, _suggest_selector

        info = _element_info_from_point(x, y)
        if info is None:
            return {}
        return _suggest_selector(
            info.automation_id or "",
            info.name or "",
            info.class_name or "",
            info.control_type or "",
        )
    except Exception:
        return {}


def _focused_element_selector() -> dict[str, str]:
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        from dolphin_desktop._spy import _suggest_selector

        elem = IUIA().iuia.GetFocusedElement()
        if not elem:
            return {}
        info = UIAElementInfo(elem)
        return _suggest_selector(
            info.automation_id or "",
            info.name or "",
            info.class_name or "",
            info.control_type or "",
        )
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Recorder
# ---------------------------------------------------------------------------


class Recorder:
    """Records mouse/keyboard interactions and generates dolphin Python test code.

    Parameters
    ----------
    app:
        Optional title fragment — only events in windows whose title contains
        this string are recorded.  If ``None``, all windows are captured.
    backend:
        pywinauto backend used in the generated code (default ``"uia"``).
    stop_key:
        Virtual key code of the stop key (default ``0x7B`` = F12).
        Must be combined with Ctrl.
    """

    def __init__(
        self,
        app: str | None = None,
        *,
        backend: str = "uia",
        stop_key: int = 0x7B,
    ) -> None:
        self.app = app
        self.backend = backend
        self._stop_vk = stop_key

        self._actions: list[RecordedAction] = []
        self._pending_text: list[str] = []
        self._pending_selector: dict[str, str] = {}
        self._pending_win_title: str = ""
        self._pending_win_class: str = ""

        self._evt_queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue()
        self._stopped = threading.Event()
        self._hook_thread_id: int = 0

        # Strong references — prevent GC while hooks are active
        self._mouse_cb: Any = None
        self._kbd_cb: Any = None

        self._hook_thread: threading.Thread | None = None
        self._proc_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Install global low-level hooks and start recording."""
        self._stopped.clear()
        self._hook_thread = threading.Thread(
            target=self._hook_loop, daemon=True, name="dolphin-rec-hook"
        )
        self._hook_thread.start()
        self._proc_thread = threading.Thread(
            target=self._process_loop, daemon=True, name="dolphin-rec-proc"
        )
        self._proc_thread.start()

    def stop(self) -> None:
        """Stop recording and unhook."""
        if not self._stopped.is_set():
            if self._hook_thread_id:
                _user32.PostThreadMessageW(self._hook_thread_id, _WM_QUIT, 0, 0)
            self._evt_queue.put(None)
            self._stopped.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until ``stop()`` is called or the stop key is pressed."""
        return self._stopped.wait(timeout)

    def actions(self) -> list[RecordedAction]:
        """Return a copy of recorded actions (flushes any pending text first)."""
        self._flush_text()
        return list(self._actions)

    def generate_code(
        self,
        output: str | Path | None = None,
        *,
        func_name: str = "test_recorded",
    ) -> str:
        """Generate Python test code from recorded actions.

        Parameters
        ----------
        output:
            If given, the generated code is written to this path.
        func_name:
            Name of the generated test function.

        Returns
        -------
        str
            The generated Python source (also written to *output* if provided).
        """
        self._flush_text()
        code = "\n".join(self._build_code(self._actions, func_name)) + "\n"
        if output is not None:
            Path(output).write_text(code, encoding="utf-8")
        return code

    # ------------------------------------------------------------------
    # Hook thread
    # ------------------------------------------------------------------

    def _hook_loop(self) -> None:
        self._hook_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        evt_queue = self._evt_queue
        stop_vk = self._stop_vk
        stopped = self._stopped

        def _mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == _HC_ACTION and w_param in (
                _WM_LBUTTONDOWN,
                _WM_LBUTTONDBLCLK,
                _WM_RBUTTONDOWN,
            ):
                ms = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                kind = {
                    _WM_LBUTTONDOWN: "click",
                    _WM_LBUTTONDBLCLK: "double_click",
                    _WM_RBUTTONDOWN: "right_click",
                }[w_param]
                try:
                    evt_queue.put_nowait(("mouse", kind, ms.pt.x, ms.pt.y, time.time()))
                except queue.Full:
                    pass
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        def _kbd_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == _HC_ACTION and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                kb = ctypes.cast(l_param, ctypes.POINTER(_KBDLLHOOKSTRUCT)).contents
                vk = int(kb.vkCode)
                if vk == stop_vk and bool(_user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000):
                    stopped.set()
                    _user32.PostQuitMessage(0)
                    return _user32.CallNextHookEx(None, n_code, w_param, l_param)
                shift = bool(_user32.GetAsyncKeyState(_VK_SHIFT) & 0x8000)
                ctrl = bool(_user32.GetAsyncKeyState(_VK_CONTROL) & 0x8000)
                alt = bool(_user32.GetAsyncKeyState(_VK_MENU) & 0x8000)
                try:
                    evt_queue.put_nowait(
                        ("key", vk, int(kb.scanCode), shift, ctrl, alt, time.time())
                    )
                except queue.Full:
                    pass
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        # Keep strong references on self so GC won't collect while hook is alive
        self._mouse_cb = _HOOKPROC(_mouse_proc)
        self._kbd_cb = _HOOKPROC(_kbd_proc)

        mouse_hook = _user32.SetWindowsHookExW(_WH_MOUSE_LL, self._mouse_cb, None, 0)
        kbd_hook = _user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._kbd_cb, None, 0)

        msg = _wt.MSG()
        while not stopped.is_set():
            ret = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret <= 0:
                break
            _user32.TranslateMessage(ctypes.byref(msg))
            _user32.DispatchMessageW(ctypes.byref(msg))

        if mouse_hook:
            _user32.UnhookWindowsHookEx(mouse_hook)
        if kbd_hook:
            _user32.UnhookWindowsHookEx(kbd_hook)

        stopped.set()
        evt_queue.put(None)

    # ------------------------------------------------------------------
    # Processor thread
    # ------------------------------------------------------------------

    def _process_loop(self) -> None:
        while True:
            try:
                evt = self._evt_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if evt is None:
                break
            if evt[0] == "mouse":
                _, kind, x, y, ts = evt
                self._flush_text()
                self._handle_mouse(kind, x, y, ts)
            elif evt[0] == "key":
                _, vk, scan, shift, ctrl, alt, ts = evt
                self._handle_key(vk, scan, shift, ctrl, alt, ts)

    def _handle_mouse(self, kind: str, x: int, y: int, ts: float) -> None:
        try:
            import win32gui

            hwnd = win32gui.WindowFromPoint((x, y))
            root = _get_root_hwnd(hwnd)
            win_title, win_class = _get_window_info(root)
        except Exception:
            win_title, win_class = "", ""

        if self.app and self.app.lower() not in win_title.lower():
            return

        selector = _element_at_point(x, y)

        # Upgrade preceding single-click to double_click if it happened at the same spot
        if kind == "double_click" and self._actions:
            prev = self._actions[-1]
            if prev.kind == "click" and prev.x == x and prev.y == y and ts - prev.timestamp < 0.7:
                self._actions[-1] = RecordedAction(
                    kind="double_click",
                    window_title=win_title,
                    window_class=win_class,
                    selector=selector,
                    x=x,
                    y=y,
                    timestamp=ts,
                )
                self._pending_selector = selector
                self._pending_win_title = win_title
                self._pending_win_class = win_class
                return

        self._actions.append(
            RecordedAction(
                kind=kind,
                window_title=win_title,
                window_class=win_class,
                selector=selector,
                x=x,
                y=y,
                timestamp=ts,
            )
        )
        # Update pending context so subsequent keystrokes attach to the clicked element
        self._pending_selector = selector
        self._pending_win_title = win_title
        self._pending_win_class = win_class

    def _handle_key(
        self, vk: int, scan: int, shift: bool, ctrl: bool, alt: bool, ts: float
    ) -> None:
        char = _vk_to_sendkeys(vk, scan, shift, ctrl, alt)
        if char is None:
            return  # modifier-only key

        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            root = _get_root_hwnd(hwnd)
            win_title, win_class = _get_window_info(root)
        except Exception:
            win_title, win_class = "", ""

        if self.app and self.app.lower() not in win_title.lower():
            self._flush_text()
            return

        if ctrl or alt:
            # Shortcut → flush accumulated text, emit press_key
            self._flush_text()
            sel = self._pending_selector or _focused_element_selector()
            self._actions.append(
                RecordedAction(
                    kind="press_key",
                    window_title=win_title,
                    window_class=win_class,
                    selector=sel,
                    data=char,
                    timestamp=ts,
                )
            )
            return

        # Printable character → accumulate for type_text
        if not self._pending_win_title:
            self._pending_win_title = win_title
            self._pending_win_class = win_class
            if not self._pending_selector:
                self._pending_selector = _focused_element_selector()
        elif win_title != self._pending_win_title:
            # Window changed mid-typing → flush and start fresh
            self._flush_text()
            self._pending_win_title = win_title
            self._pending_win_class = win_class
            self._pending_selector = _focused_element_selector()

        self._pending_text.append(char)

    def _flush_text(self) -> None:
        if not self._pending_text:
            return
        self._actions.append(
            RecordedAction(
                kind="type_text",
                window_title=self._pending_win_title,
                window_class=self._pending_win_class,
                selector=self._pending_selector,
                data="".join(self._pending_text),
            )
        )
        self._pending_text.clear()
        self._pending_win_title = ""
        self._pending_win_class = ""
        self._pending_selector = {}

    # ------------------------------------------------------------------
    # Code generation
    # ------------------------------------------------------------------

    def _build_code(self, actions: list[RecordedAction], func_name: str) -> list[str]:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"# Generated by dolphin recorder on {now}",
            "from dolphin_desktop import Desktop",
            "",
            "",
            f"def {func_name}() -> None:",
            f'    """Recorded test — generated by dolphin on {now}."""',
        ]

        # Collect unique (title, class) pairs in recording order
        seen: set[tuple[str, str]] = set()
        windows: list[tuple[str, str]] = []
        for a in actions:
            key = (a.window_title, a.window_class)
            if key not in seen:
                seen.add(key)
                windows.append(key)

        # Connection boilerplate
        lines.append(f'    desktop = Desktop(backend="{self.backend}")')
        if self.app:
            lines.append(f'    app = desktop.connect(title_re=".*{self.app}.*")')
        elif windows:
            title, cls = windows[0]
            if cls:
                lines.append(f'    app = desktop.connect(class_name="{cls}")')
            elif title:
                safe = title.replace('"', '\\"')
                lines.append(f'    app = desktop.connect(title_re=".*{safe}.*")')
            else:
                lines.append('    app = desktop.connect(title_re=".*")  # TODO: specify app')
        else:
            lines.append('    app = desktop.connect(title_re=".*")  # TODO: specify app')

        # Window variable declarations
        win_vars: dict[tuple[str, str], str] = {}
        for i, (title, cls) in enumerate(windows):
            var = "win" if i == 0 else f"win{i + 1}"
            win_vars[(title, cls)] = var
            if cls:
                lines.append(f'    {var} = app.window(class_name="{cls}")')
            elif title:
                safe = title.replace('"', '\\"')
                lines.append(f'    {var} = app.window(title_re=".*{safe}.*")')
            else:
                lines.append(f"    {var} = app.top_window()")

        if not actions:
            lines.append("    pass  # no actions recorded")
            return lines

        lines.append("")

        for action in actions:
            key = (action.window_title, action.window_class)
            win_var = win_vars.get(key, "win")
            lines.append(_action_to_line(action, win_var))

        return lines
