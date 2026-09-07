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
