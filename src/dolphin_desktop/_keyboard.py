"""Global keyboard utilities operating outside any specific window."""

from pywinauto.keyboard import send_keys as _send_keys


class Keyboard:
    """Send keystrokes to the currently focused window.

    Key syntax follows pywinauto conventions::

        Keyboard.press("^a")          # Ctrl+A — select all
        Keyboard.press("^s")          # Ctrl+S — save
        Keyboard.press("{ENTER}")     # Enter key
        Keyboard.press("+{F10}")      # Shift+F10 — context menu
        Keyboard.type("Hello World")  # type plain text with spaces
        Keyboard.hotkey("ctrl", "c")  # Ctrl+C — copy

    Special key tokens: ``{ENTER}``, ``{TAB}``, ``{ESC}``, ``{BACK}``,
    ``{DELETE}``, ``{F1}``…``{F12}``, ``{UP}``, ``{DOWN}``, ``{HOME}``, ``{END}``.

    Modifiers: ``^`` ctrl, ``+`` shift, ``%`` alt.
    """

    @staticmethod
    def press(keys: str) -> None:
        """Send keys using pywinauto key syntax."""
        _send_keys(keys)

    @staticmethod
    def type(text: str) -> None:
        """Type plain text including spaces and special characters."""
        _send_keys(text, with_spaces=True, with_tabs=True, with_newlines=True)

    @staticmethod
    def hotkey(*keys: str) -> None:
        """Press a key combination, e.g. hotkey('ctrl', 'c')."""
        modifiers = {"ctrl": "^", "shift": "+", "alt": "%"}
        combo = ""
        for k in keys:
            combo += modifiers.get(k.lower(), f"{{{k.upper()}}}")
        _send_keys(combo)
