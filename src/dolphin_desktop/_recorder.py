"""Action recorder — captures global mouse/keyboard events and emits dolphin Python code.

.. warning::

    **The recorder is a system-wide input capture.** While it is running it installs
    global low-level mouse and keyboard hooks, so it sees every keystroke typed
    anywhere on the desktop, not only in the application under test — including in
    your mail client, terminal, or password manager. Captured keystrokes are
    translated to real characters and written **in plaintext** into the generated
    ``.py`` file.

    * Always pass ``app=`` (CLI: ``--app``) so keystrokes outside the matching window
      are discarded before they are ever turned into text.
    * Password fields are redacted via the UI Automation "is password" flag, and a
      field the platform cannot classify at all is redacted too. A password typed
      into a control the platform reports as ordinary text is still recorded verbatim.
    * Review the generated file before committing or sharing it.

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
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._logging import get_logger
from ._platform_compat import _UnavailableObject

# Windows constants

_WH_MOUSE_LL = 14
_WH_KEYBOARD_LL = 13

_WM_LBUTTONDOWN = 0x0201
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

"""Every key in ``_SPECIAL_KEY_MAP`` ends a run of typed text.

Two reasons, and both are load-bearing. Tab and Enter hand focus to another
control — user → {TAB} → password is one uninterrupted keystroke run at the
hook level, and a run that spans the boundary attributes the password to the
username field. The rest are not characters at all: their send_keys spelling
is a braced token, and ``type_text`` escapes braces, so folding ``{BACKSPACE}``
into a text run replays it as the literal eight characters ``{BACKSPACE}``.
"""

# The subset that also hands focus to another control. Ending a text run and
# forgetting which control it belonged to are different decisions: every
# special key does the first, only these do the second. Backspace and the
# arrows leave the caret where it is, so the next run still belongs to the
# control the user clicked.
_FOCUS_CHANGING_VKS = {0x09, 0x0D}  # TAB, ENTER

# ctypes hook structures


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


_HOOKPROC = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
    ctypes.c_longlong, ctypes.c_int, _wt.WPARAM, _wt.LPARAM
)

_user32 = (
    ctypes.windll.user32 if sys.platform == "win32" else _UnavailableObject("recorder")
)

_log = get_logger("recorder")

# Text substituted for keystrokes typed into a field the platform flags as a password
_PASSWORD_PLACEHOLDER = "<REDACTED-PASSWORD>"

# Seconds stop()/wait() give the processor thread to drain the event queue
_PROC_JOIN_TIMEOUT = 2.0

# Data model


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


# Double-click synthesis

# A double-click's second press may land a pixel or two off the first
_DOUBLE_CLICK_SLOP_PX = 4


def _double_click_interval() -> float:
    """Return the system double-click interval in seconds."""
    try:
        return max(int(_user32.GetDoubleClickTime()), 1) / 1000.0
    except Exception:
        return 0.5


def _is_double_click(
    prev: RecordedAction | None,
    kind: str,
    x: int,
    y: int,
    ts: float,
    interval: float,
) -> bool:
    """True when this left press continues *prev* into a double-click.

    A WH_MOUSE_LL hook only ever receives raw button DOWN/UP messages —
    WM_LBUTTONDBLCLK is synthesized higher up, at window-proc level, and never
    reaches us — so a double-click has to be reconstructed from two consecutive
    presses within the system interval and a small movement threshold.
    """
    if kind != "click" or prev is None or prev.kind != "click":
        return False
    if not 0 <= ts - prev.timestamp <= interval:
        return False
    return abs(x - prev.x) <= _DOUBLE_CLICK_SLOP_PX and abs(y - prev.y) <= _DOUBLE_CLICK_SLOP_PX


# Keyboard translation


def _to_unicode(vk: int, scan: int, shift: bool, ctrl: bool = False, alt: bool = False) -> str:
    """Return the Unicode character for a VK code using the current keyboard layout."""
    try:
        state = (ctypes.c_ubyte * 256)()
        if shift:
            state[_VK_SHIFT] = 0x80
        if ctrl:
            state[_VK_CONTROL] = 0x80
        if alt:
            state[_VK_MENU] = 0x80
        buf = ctypes.create_unicode_buffer(8)
        n = _user32.ToUnicode(vk, scan, state, buf, 8, 0)
        if n == 1:
            return buf.value
    except Exception:
        pass
    return ""


def _altgr_char(
    vk: int, scan: int, shift: bool, ctrl: bool, alt: bool, *, ralt: bool = True
) -> str:
    """Return the character AltGr produces for *vk*, or ``""`` when it produces none.

    Windows reports AltGr as Ctrl+Alt, so on a layout with a third level (Polish ą,
    German € and {, French @) a plain typed character is indistinguishable from a
    Ctrl shortcut by the modifier flags alone — only the layout can tell them apart.

    *ralt* narrows that further: AltGr is physically the **right** Alt, so a
    Ctrl+Alt combination held with the left Alt is always a shortcut, whatever
    the layout would produce for it. Without this a Polish or German user
    recording Ctrl+Alt+S (Settings in several IDEs) got ``type_text("ś")`` —
    the shortcut lost and a stray character typed into the document.
    """
    if not ralt or not (ctrl and alt) or vk in _MODIFIER_VKS or vk in _SPECIAL_KEY_MAP:
        return ""
    char = _to_unicode(vk, scan, shift, ctrl=True, alt=True)
    if char and char.isprintable() and char != "\t":
        return char
    return ""


def _vk_to_sendkeys(
    vk: int, scan: int, shift: bool, ctrl: bool, alt: bool, *, ralt: bool = True
) -> str | None:
    """Convert a virtual key + modifiers to pywinauto send_keys format.

    Returns ``None`` for modifier-only keys (they should be ignored).

    *ralt* says whether the **right** Alt was the one held — see
    :func:`_altgr_char`. It defaults to ``True`` so a caller that only knows
    "Alt was down" keeps the layout-first reading, but the recorder always
    passes the real side.
    """
    if vk in _MODIFIER_VKS:
        return None

    prefix = ""
    if ctrl:
        prefix += "^"
    if alt:
        prefix += "%"
    if shift:
        # Shift belongs in the prefix for every *shortcut*, not just the
        # unmodified case: send_keys has no other way to spell Ctrl+Shift+Home,
        # and the character path below never gets here because Shift is already
        # baked into the character the layout produced.
        prefix += "+"

    if vk in _SPECIAL_KEY_MAP:
        return f"{prefix}{_SPECIAL_KEY_MAP[vk]}"

    # Plain printable character (no Ctrl/Alt)
    if not ctrl and not alt:
        char = _to_unicode(vk, scan, shift)
        if char and char.isprintable() and char != "\t":
            return char

    # AltGr character — must be tried before the Ctrl branches below.
    # ``ralt`` has to be threaded in: this call is reached even when the caller
    # already asked _altgr_char with ralt=False and got "" back, so taking the
    # default here silently reinstated the character and left Ctrl+left-Alt+S
    # recorded as "ś" after all.
    altgr = _altgr_char(vk, scan, shift, ctrl, alt, ralt=ralt)
    if altgr:
        return altgr

    # Modifier shortcut on a letter or digit. The prefix is used verbatim
    # rather than a hardcoded "^": hardcoding it recorded Ctrl+Alt+Q as plain
    # Ctrl+Q, which in most editors is Quit — a wrong action, not a failure.
    # Alt alone lands here too, so the Alt+F, O menu path survives instead of
    # being dropped as a modifier-only key and leaving a stray "o" behind.
    if ctrl or alt:
        if 0x41 <= vk <= 0x5A:
            return f"{prefix}{chr(vk + 0x20)}"
        if 0x30 <= vk <= 0x39:
            return f"{prefix}{chr(vk)}"

    return None


# Selector → dolphin API call


def _selector_to_call(sel: dict[str, str]) -> str:
    """Convert a selector dict to a Window method chain fragment.

    Every interpolated value goes through ``repr`` — element names routinely contain
    backslashes (``C:\\Users\\...``) and quotes, which a hand-quoted f-string turns
    into source that does not parse.
    """
    if not sel:
        return "locator()"
    keys = list(sel.keys())
    if keys == ["auto_id"]:
        return f"get_by_automation_id({sel['auto_id']!r})"
    if set(keys) == {"title", "control_type"}:
        return f"get_by_role({sel['control_type']!r}, name={sel['title']!r})"
    if keys == ["title"]:
        return f"get_by_title({sel['title']!r})"
    if keys == ["class_name"]:
        return f"get_by_class({sel['class_name']!r})"
    parts = [f"{k}={v!r}" for k, v in sel.items()]
    return f"locator({', '.join(parts)})"


_NO_SELECTOR_NOTE = "  # TODO: no selector was recorded — fill one in"


def _title_re_literal(title: str) -> str:
    """Return a Python literal for a ``title_re=`` matching *title* as plain text."""
    return repr(f".*{re.escape(title)}.*")


def _action_to_line(action: RecordedAction, win_var: str) -> str:
    """Convert a RecordedAction to a single Python source line.

    An unresolved selector is flagged by a comment *after* the whole call: a note
    placed anywhere earlier would comment the action out, and the generated test
    would then run green while doing nothing.
    """
    call = _selector_to_call(action.selector)
    text = repr(action.data)
    note = "" if action.selector else _NO_SELECTOR_NOTE
    if action.kind == "click":
        return f"    {win_var}.{call}.click(){note}"
    if action.kind == "double_click":
        return f"    {win_var}.{call}.double_click(){note}"
    if action.kind == "right_click":
        return f"    {win_var}.{call}.right_click(){note}"
    if action.kind == "type_text":
        return f"    {win_var}.{call}.type_text({text}){note}"
    if action.kind == "press_key":
        return f"    {win_var}.{call}.press_key({text}){note}"
    return f"    # {action.kind}: {text}"


# Element resolution helpers


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


def _focused_password_flag() -> bool | None:
    """Return the focused element's UIA "is password" flag, ``None`` when unknown."""
    try:
        from pywinauto.uia_defines import IUIA

        elem = IUIA().iuia.GetFocusedElement()
        if not elem:
            return None
        return bool(elem.CurrentIsPassword)
    except Exception:
        return None


def _focused_is_password() -> bool:
    """True when the focused element is a password field *or* cannot be classified.

    Fails closed: an unreadable focus (no UIA provider, COM hiccup, pywin32 missing)
    is treated as a secret, because the alternative is writing the keystrokes into
    the generated script in plaintext.  A control that reports itself as ordinary
    text — a plain edit used for a secret, a custom-drawn field — is still recorded
    verbatim; that is the limit of the platform flag.
    """
    flag = _focused_password_flag()
    if flag is None:
        _log.warning(
            "focused control could not be classified — treating its input as a "
            "password and redacting it"
        )
        return True
    return flag


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


# Recorder


class Recorder:
    """Records mouse/keyboard interactions and generates dolphin Python test code.

    Captures input globally — read the privacy warning at the top of this module
    before using it.  :meth:`start` restates it on the recorder's own output.

    Parameters
    ----------
    app:
        Optional title fragment — only events in windows whose title contains
        this string are recorded.  If ``None``, **all** windows are captured,
        including every keystroke typed outside the application under test.
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
        self._pending_is_password: bool = False
        self._dbl_click_interval: float = _double_click_interval()

        self._evt_queue: queue.Queue[tuple[Any, ...] | None] = queue.Queue()
        # Bumped once per fully handled event — the drain's progress signal, which
        # qsize() cannot give: an event leaves the queue before it is interpreted.
        self._processed: int = 0
        # Set while the processor is parked in get() — the only state in which it
        # holds no half-interpreted event, and so the only one a drain may end on.
        self._proc_idle = threading.Event()
        self._stopped = threading.Event()
        self._hook_thread_id: int = 0

        # Strong references — prevent GC while hooks are active
        self._mouse_cb: Any = None
        self._kbd_cb: Any = None

        self._hook_thread: threading.Thread | None = None
        self._proc_thread: threading.Thread | None = None

    # Public API

    def start(self) -> None:
        """Install global low-level hooks and start recording.

        Emits the privacy warning first — the hooks are system-wide, so this is the
        one point at which the user is told what is about to be captured.
        """
        scope = (
            f"windows whose title contains {self.app!r}"
            if self.app
            else "EVERY window on this desktop — no app filter was given"
        )
        _log.warning(
            "RECORDING GLOBAL INPUT. Mouse actions and keystrokes are captured from %s "
            "and written as plaintext into the generated .py file. Password fields are "
            "redacted best-effort only. Stop recording before typing anything secret, "
            "and review the generated file before sharing it.",
            scope,
        )
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
        """Stop recording, unhook, and wait for the queued events to be processed."""
        if not self._stopped.is_set():
            if self._hook_thread_id:
                _user32.PostThreadMessageW(self._hook_thread_id, _WM_QUIT, 0, 0)
            self._evt_queue.put(None)
            self._stopped.set()
        self._join_processor()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until ``stop()`` is called or the stop key is pressed."""
        if not self._stopped.wait(timeout):
            return False
        self._join_processor()
        return True

    def _join_processor(self, timeout: float = _PROC_JOIN_TIMEOUT) -> None:
        """Block until the processor thread has drained the event queue.

        The stop key sets ``_stopped`` from the hook thread while the processor thread
        may still hold unprocessed events, and every reader below mutates the same
        lists ``_handle_key`` appends to — so they must not run alongside it.

        *timeout* bounds a stalled drain, not the whole drain: a processor that is
        still consuming events keeps the wait alive, so a long recording is never
        truncated, while a wedged one is abandoned — with a warning, because the
        recording it produced is then incomplete.

        An empty queue is not the idle signal: an event leaves the queue before it is
        interpreted, so a processor wedged inside the UIA focus probe of the keystroke
        it just dequeued also leaves it empty.  Only ``_proc_idle`` says the processor
        is parked in ``get()`` holding nothing.
        """
        thread = self._proc_thread
        if thread is None or not self._stopped.is_set():
            return
        if thread is threading.current_thread():
            return
        processed = self._processed
        grace = True
        while True:
            thread.join(timeout)
            if not thread.is_alive():
                self._proc_thread = None
                return
            if self._processed != processed:
                processed = self._processed
                grace = True
                continue
            if self._proc_idle.is_set():
                if self._evt_queue.empty():
                    # Parked with nothing left to hand over — the hook thread has not
                    # posted its sentinel yet, which is not a stalled drain.
                    return
                if grace:
                    # An event landed as the wait expired; the get() about to return it
                    # gets one more pass before the drain is called stalled.
                    grace = False
                    continue
            break
        _log.warning(
            "recorder event drain made no progress for %.1fs (%d event(s) still queued) "
            "— the recording is incomplete",
            timeout,
            self._evt_queue.qsize(),
        )

    def actions(self) -> list[RecordedAction]:
        """Return a copy of recorded actions (flushes any pending text first)."""
        self._join_processor()
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
        self._join_processor()
        self._flush_text()
        code = "\n".join(self._build_code(self._actions, func_name)) + "\n"
        if output is not None:
            Path(output).write_text(code, encoding="utf-8")
        return code

    # Hook thread

    def _hook_loop(self) -> None:
        self._hook_thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        evt_queue = self._evt_queue
        stop_vk = self._stop_vk
        stopped = self._stopped

        def _mouse_proc(n_code: int, w_param: int, l_param: int) -> int:
            if n_code == _HC_ACTION and w_param in (_WM_LBUTTONDOWN, _WM_RBUTTONDOWN):
                ms = ctypes.cast(l_param, ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                kind = {
                    _WM_LBUTTONDOWN: "click",
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
                # Sampled separately from _VK_MENU because only the right Alt
                # can be AltGr. Windows reports AltGr as Ctrl+Alt, so without
                # the side the recorder cannot tell a third-level character
                # from a genuine Ctrl+Alt shortcut pressed with the left Alt.
                ralt = bool(_user32.GetAsyncKeyState(_VK_RMENU) & 0x8000)
                try:
                    evt_queue.put_nowait(
                        ("key", vk, int(kb.scanCode), shift, ctrl, alt, ralt, time.time())
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

    # Processor thread

    def _process_loop(self) -> None:
        while True:
            self._proc_idle.set()
            try:
                evt = self._evt_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            finally:
                self._proc_idle.clear()
            if evt is None:
                break
            if evt[0] == "mouse":
                _, kind, x, y, ts = evt
                self._flush_text()
                self._handle_mouse(kind, x, y, ts)
            elif evt[0] == "key":
                _, vk, scan, shift, ctrl, alt, ralt, ts = evt
                self._handle_key(vk, scan, shift, ctrl, alt, ts, ralt=ralt)
            self._processed += 1

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

        # Upgrade the preceding single-click to double_click when this press
        # completes a double-click the low-level hook cannot report as one.
        prev = self._actions[-1] if self._actions else None
        if prev is not None and _is_double_click(prev, kind, x, y, ts, self._dbl_click_interval):
            self._actions[-1] = RecordedAction(
                kind="double_click",
                window_title=win_title,
                window_class=win_class,
                selector=selector or prev.selector,
                x=x,
                y=y,
                timestamp=ts,
            )
            self._pending_selector = self._actions[-1].selector
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
        self,
        vk: int,
        scan: int,
        shift: bool,
        ctrl: bool,
        alt: bool,
        ts: float,
        *,
        ralt: bool = True,
    ) -> None:
        try:
            import win32gui

            hwnd = win32gui.GetForegroundWindow()
            root = _get_root_hwnd(hwnd)
            win_title, win_class = _get_window_info(root)
        except Exception:
            win_title, win_class = "", ""

        # Resolve scope before translating: a keystroke in a window outside the app
        # filter must never be turned into a character in the first place.
        if self.app and self.app.lower() not in win_title.lower():
            self._flush_text()
            return

        # AltGr is reported as Ctrl+Alt, so the layout — not the modifier flags —
        # decides whether this keystroke is typed text or a shortcut.
        altgr = _altgr_char(vk, scan, shift, ctrl, alt, ralt=ralt)
        char = altgr or _vk_to_sendkeys(vk, scan, shift, ctrl, alt, ralt=ralt)
        if char is None:
            return  # modifier-only key

        if not altgr and (ctrl or alt or vk in _SPECIAL_KEY_MAP):
            # Shortcut or focus-changing key → flush accumulated text, emit press_key.
            # The selector is read before the flush, which clears the pending context.
            sel = self._pending_selector or _focused_element_selector()
            self._flush_text()
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
            # _flush_text() clears nothing when no text had accumulated, so the pending
            # context is dropped here as well: this key moved focus, and the next run
            # must re-resolve it instead of inheriting the previous control's selector.
            #
            # Only for keys that actually move focus. This branch also handles
            # Backspace, the arrows and F-keys now, and those leave the caret
            # where it is — dropping the selector for them made the run after a
            # corrected typo re-resolve through _focused_element_selector(),
            # which answers {} on any failure, so a Qt or JAB control with weak
            # UIA focus reporting lost the known-good clicked-element selector
            # and the generated script got a bare locator().
            if ctrl or alt or vk in _FOCUS_CHANGING_VKS:
                self._pending_selector = {}
                self._pending_win_title = ""
                self._pending_win_class = ""
            return

        # Printable character → accumulate for type_text. The password flag is sampled
        # on every keystroke, not once per run: a run that starts in a plain field can
        # end in a password field, and that is the flow redaction exists for.
        is_password = _focused_is_password()
        if self._pending_text:
            if is_password and not self._pending_is_password:
                # Focus is sampled on this thread, one queue hop behind the keystroke,
                # so a run that turns out to reach a password field is redacted whole
                # rather than split at a boundary that may already be too late.
                self._pending_is_password = True
                self._flush_text()
            elif win_title != self._pending_win_title or is_password != self._pending_is_password:
                self._flush_text()

        if not self._pending_text:
            # A fresh run inherits the clicked element's selector only when the
            # keystroke landed in the window that click belonged to.
            if win_title != self._pending_win_title or not self._pending_selector:
                self._pending_selector = _focused_element_selector()
            self._pending_win_title = win_title
            self._pending_win_class = win_class
            self._pending_is_password = is_password

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
                data=(
                    _PASSWORD_PLACEHOLDER
                    if self._pending_is_password
                    else "".join(self._pending_text)
                ),
            )
        )
        self._pending_text.clear()
        self._pending_is_password = False
        self._pending_win_title = ""
        self._pending_win_class = ""
        self._pending_selector = {}

    # Code generation

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
        lines.append(f"    desktop = Desktop(backend={self.backend!r})")
        if self.app:
            lines.append(f"    app = desktop.connect(title_re={_title_re_literal(self.app)})")
        elif windows:
            title, cls = windows[0]
            if cls:
                lines.append(f"    app = desktop.connect(class_name={cls!r})")
            elif title:
                lines.append(f"    app = desktop.connect(title_re={_title_re_literal(title)})")
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
                lines.append(f"    {var} = app.window(class_name={cls!r})")
            elif title:
                lines.append(f"    {var} = app.window(title_re={_title_re_literal(title)})")
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
