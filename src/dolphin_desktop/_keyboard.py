"""Global keyboard utilities operating outside any specific window."""

import sys

if sys.platform == "win32":
    from pywinauto.keyboard import CODES as _PYWINAUTO_CODES  # type: ignore[import-untyped]
    from pywinauto.keyboard import send_keys as _send_keys

    _CODES = _PYWINAUTO_CODES
else:
    from ._platform_compat import _unsupported_callable

    _CODES = {}
    _send_keys = _unsupported_callable("pywinauto.keyboard.send_keys")

from ._exceptions import DolphinError
from ._helpers import _escape_keys

_MODIFIERS = {"ctrl": "^", "control": "^", "shift": "+", "alt": "%"}
_WIN_MODIFIERS = frozenset({"win", "windows", "super", "meta"})

# pywinauto's CODES table only carries the abbreviated spelling of these keys.
_KEY_ALIASES = {
    "ESCAPE": "ESC",
    "RETURN": "ENTER",
    "PAGEUP": "PGUP",
    "PAGEDOWN": "PGDN",
    "PRINTSCREEN": "PRTSC",
    "SPACEBAR": "SPACE",
    "APPS": "VK_APPS",
    "MENU": "VK_APPS",
    "CONTEXTMENU": "VK_APPS",
}

# Only these are pywinauto metacharacters; brace-wrapping anything else turns it
# into a Unicode VK_PACKET that keyboard accelerators do not match.
_METACHARS = "+^%~(){}"

# Whitespace pywinauto drops unless with_spaces/with_tabs/with_newlines are set,
# which a hotkey sequence never sets — hotkey("ctrl", "\n") would send a bare
# Ctrl press.
_WHITESPACE_KEYS = {" ": "{SPACE}", "\t": "{TAB}", "\n": "{ENTER}", "\r": "{ENTER}"}
_WINDOW_TARGETED_MODIFIER_VKS = frozenset(
    {
        0x10,
        0x11,
        0x12,
        0x5B,
        0x5C,
        0xA0,
        0xA1,
        0xA2,
        0xA3,
        0xA4,
        0xA5,
    }
)


def _hotkey_char(char: str) -> str:
    if char in _WHITESPACE_KEYS:
        return _WHITESPACE_KEYS[char]
    if char in _METACHARS:
        return "{" + char + "}"
    return char.lower()


def _hotkey_sequence(keys: tuple[str, ...]) -> tuple[str, bool]:
    """Build a pywinauto key sequence for a modifier + key combination.

    Returns ``(sequence, win)`` where *sequence* carries the ``^ + %`` modifier
    prefixes and *win* reports whether the Windows key has to be held around it
    — the caller emits that hold separately so it can be released in a
    ``finally``.

    Single characters are emitted bare (``"^c"``, ``"^-"``) so pywinauto
    produces a key action carrying a real virtual-key code.
    """
    if not keys:
        raise ValueError("hotkey() requires at least one key, e.g. hotkey('ctrl', 'c').")

    prefix = ""
    combo: list[str] = []
    win = False
    for key in keys:
        low = key.lower()
        if low in _MODIFIERS:
            prefix += _MODIFIERS[low]
        elif low in _WIN_MODIFIERS:
            win = True
        elif len(key) == 1:
            combo.append(_hotkey_char(key))
        elif key.startswith("{") and key.endswith("}"):
            combo.append(key)
        elif _KEY_ALIASES.get(key.upper(), key.upper()) in _CODES:
            combo.append("{" + _KEY_ALIASES.get(key.upper(), key.upper()) + "}")
        else:
            raise ValueError(
                f"Unknown hotkey component {key!r}. Supported modifiers: "
                "ctrl, shift, alt, win. Other components must be a single "
                "character or a pywinauto key name (enter, tab, f4, delete, ...)."
            )

    if len(combo) > 1:
        raise ValueError(
            f"hotkey() accepts at most one non-modifier key, got {keys!r}. "
            "pywinauto releases the modifier after the first key, so the "
            "remaining ones would be sent unmodified — call hotkey() once "
            "per combination."
        )
    if not combo and not win:
        raise ValueError(f"hotkey() needs a non-modifier key, got only modifiers {keys!r}.")
    return prefix + "".join(combo), win


def _send_keys_on_hidden_desktop(keys: str) -> bool:
    """Use the normal keyboard API while DolphinHidden is input-active.

    A hidden desktop normally cannot receive ``SendInput``. Windows does not
    offer a fully equivalent per-window keyboard injection API, especially for
    modifier state consumed by WPF. When the desktop is switchable, briefly
    make DolphinHidden the input desktop, send the sequence, and restore the
    desktop that was active before the call.

    Returns ``False`` when the desktop handles cannot be opened or switched;
    callers can then use a window-message fallback or report a clear error.
    """
    if sys.platform != "win32":
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    open_input_desktop = user32.OpenInputDesktop
    open_input_desktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_input_desktop.restype = wintypes.HANDLE
    open_desktop = user32.OpenDesktopW
    open_desktop.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    open_desktop.restype = wintypes.HANDLE
    switch_desktop = user32.SwitchDesktop
    switch_desktop.argtypes = [wintypes.HANDLE]
    switch_desktop.restype = wintypes.BOOL
    close_desktop = user32.CloseDesktop
    close_desktop.argtypes = [wintypes.HANDLE]
    close_desktop.restype = wintypes.BOOL

    desktop_switch = 0x0100
    original = open_input_desktop(0, False, desktop_switch)
    hidden = open_desktop("DolphinHidden", 0, False, desktop_switch)
    if not original or not hidden:
        if original:
            close_desktop(original)
        if hidden:
            close_desktop(hidden)
        return False

    switched = bool(switch_desktop(hidden))
    if not switched:
        close_desktop(hidden)
        close_desktop(original)
        return False
    try:
        _send_keys(keys)
    finally:
        # Restore first, then close both handles. If restoring fails Windows
        # still owns the active desktop; do not hide that failure from callers.
        restored = bool(switch_desktop(original))
        close_desktop(hidden)
        close_desktop(original)
        if not restored:
            error = ctypes.get_last_error()
            raise OSError(error, "SwitchDesktop restore failed", error)
    return True


def _post_keys_to_hwnd(hwnd: int, keys: str, *, pause: float = 0.05) -> None:
    """Post a pywinauto key sequence to a specific window handle.

    ``pywinauto.send_keys`` uses ``SendInput``, which targets the interactive
    input desktop. A window running on DolphinHidden cannot receive those
    events even after UIA has given it keyboard focus. Posting the equivalent
    ``WM_KEYDOWN``/``WM_KEYUP`` messages to that window keeps modifier-free
    ``Locator.press_key`` usable on a hidden desktop. Modifier shortcuts are
    rejected because ``keybd_event``/``AttachThreadInput`` cannot safely make
    their state window-targeted.

    This is intentionally a targeted helper. Global :class:`Keyboard` calls
    still use ``SendInput`` because they have no window handle to target.
    """
    if sys.platform != "win32":
        raise OSError("window-targeted keyboard input is only available on Windows")

    import ctypes
    from ctypes import wintypes

    from pywinauto.keyboard import parse_keys

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_window_thread = user32.GetWindowThreadProcessId
    get_window_thread.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    get_window_thread.restype = wintypes.DWORD
    send_message = user32.SendMessageW
    send_message.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    send_message.restype = ctypes.c_ssize_t

    wm_keydown = 0x0100
    wm_keyup = 0x0101
    wm_char = 0x0102
    keyeventf_extendedkey = 0x0001
    keyeventf_unicode = 0x0004

    # Use virtual-key actions for ordinary ASCII characters. The default
    # pywinauto parser uses Unicode VK_PACKET actions for those, which have no
    # virtual-key code that can be represented by WM_KEYDOWN.
    actions = parse_keys(
        keys,
        with_spaces=True,
        with_tabs=True,
        with_newlines=True,
        vk_packet=False,
    )
    process_id = wintypes.DWORD()
    if not get_window_thread(hwnd, ctypes.byref(process_id)):
        error = ctypes.get_last_error()
        raise OSError(error, "GetWindowThreadProcessId failed", error)
    if any(int(action.get_key_info()[0]) in _WINDOW_TARGETED_MODIFIER_VKS for action in actions):
        raise DolphinError(
            "press_key() cannot deliver modifier shortcuts through the hidden-window fallback",
            hint=(
                "the DolphinHidden desktop could not be activated; use a visible "
                "desktop or retry when SwitchDesktop is available"
            ),
        )

    import time

    for action in actions:
        vk, scan, flags = action.get_key_info()
        if not vk:
            # Unicode input has no WM_KEYDOWN virtual key. WM_CHAR preserves
            # useful text-entry behaviour for non-ASCII keys.
            value = getattr(action, "key", None)
            if (
                flags & keyeventf_unicode
                and action.down
                and action.up
                and isinstance(value, str)
                and len(value) == 1
            ):
                send_message(hwnd, wm_char, ord(value), 0)
                if pause:
                    time.sleep(pause)
                continue
            raise ValueError(f"key sequence contains an unsupported action: {action!r}")

        lparam = (int(scan) & 0xFF) << 16
        if flags & keyeventf_extendedkey:
            lparam |= 1 << 24

        if action.down:
            send_message(hwnd, wm_keydown, int(vk), lparam)
            if pause:
                time.sleep(pause)
        if action.up:
            send_message(hwnd, wm_keyup, int(vk), lparam | (1 << 30) | (1 << 31))
            if pause:
                time.sleep(pause)


class Keyboard:
    """Send keystrokes to the currently focused window.

    Key syntax follows pywinauto conventions::

        Keyboard.press("^a")          # Ctrl+A — select all
        Keyboard.press("^s")          # Ctrl+S — save
        Keyboard.press("{ENTER}")     # Enter key
        Keyboard.press("+{F10}")      # Shift+F10 — context menu
        Keyboard.type("Hello World")  # type plain text with spaces
        Keyboard.hotkey("ctrl", "c")  # Ctrl+C — copy
        Keyboard.hotkey("win", "e")   # Win+E — file explorer

    Special key tokens: ``{ENTER}``, ``{TAB}``, ``{ESC}``, ``{BACK}``,
    ``{DELETE}``, ``{F1}``…``{F12}``, ``{UP}``, ``{DOWN}``, ``{HOME}``, ``{END}``.

    Modifiers: ``^`` ctrl, ``+`` shift, ``%`` alt.
    """

    @staticmethod
    def press(keys: str) -> None:
        """Send keys using pywinauto key syntax."""
        _send_keys(keys)

    @staticmethod
    def type(text: str, *, escape: bool = True) -> None:
        """Type *text* literally, including spaces, tabs and newlines.

        The pywinauto metacharacters ``+ ^ % ~ ( ) { }`` are escaped so that
        ``type("100% done")`` types a percent sign instead of pressing Alt+Space.
        Pass ``escape=False`` to send *text* as a key *sequence* instead
        (``type("%F", escape=False)`` for Alt+F), matching
        :meth:`Locator.type_text`.
        """
        _send_keys(
            _escape_keys(text) if escape else text,
            with_spaces=True,
            with_tabs=True,
            with_newlines=True,
        )

    @staticmethod
    def hotkey(*keys: str) -> None:
        """Press a key combination, e.g. hotkey('ctrl', 'c').

        Supported modifiers are ``ctrl``, ``shift``, ``alt`` and ``win``; the
        one remaining component is a single character or a pywinauto key name
        (``enter``, ``f4``, ``delete``).  Raises :class:`ValueError` when no
        key is given, when a component is unrecognised, or when more than one
        non-modifier key is passed.
        """
        sequence, win = _hotkey_sequence(keys)
        if not win:
            _send_keys(sequence)
            return
        # The Win-key hold must be three separate send_keys calls: pywinauto
        # runs a parsed sequence in an unguarded loop, so a KeyboardInterrupt
        # or a failing SendInput in the middle of a single combined string
        # would leave the Windows key held down for the whole session.
        try:
            _send_keys("{VK_LWIN down}")
            if sequence:
                # vk_packet=False is mandatory here: with the modifier split
                # into its own call the payload is a bare character, which
                # pywinauto would otherwise inject as a Unicode VK_PACKET that
                # the shell's hotkey table cannot match — Win+E would open the
                # Start menu instead of Explorer.
                _send_keys(sequence, vk_packet=False)
        finally:
            _send_keys("{VK_LWIN up}")
