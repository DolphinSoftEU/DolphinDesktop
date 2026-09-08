"""Global mouse utilities for screen-coordinate operations."""

import sys

if sys.platform == "win32":
    import win32api as _win32api  # type: ignore[import-untyped]
    import win32con as _win32con  # type: ignore[import-untyped]
    from pywinauto import mouse as _mouse
else:
    from ._platform_compat import _UnavailableObject

    _mouse = _UnavailableObject("pywinauto.mouse")
    _win32api = None
    _win32con = None

# pywinauto builds an empty event list for anything else and the call then only
# moves the cursor, so an unrecognised name clicks nothing at all.
_BUTTONS = frozenset({"left", "right", "middle", "x"})
_XBUTTON1 = 0x0001


def _x_button_input(
    x: int,
    y: int,
    *,
    button_down: bool = True,
    button_up: bool = True,
    double: bool = False,
) -> None:
    """Send an XBUTTON1 event when pywinauto lacks its X-button constants."""
    if _win32api is None or _win32con is None:
        raise RuntimeError("X-button input is only available on Windows")

    _win32api.SetCursorPos((x, y))
    repeats = 2 if double and button_down and button_up else 1
    for _ in range(repeats):
        if button_down:
            _win32api.mouse_event(
                _win32con.MOUSEEVENTF_XDOWN,
                0,
                0,
                _XBUTTON1,
                0,
            )
        if button_up:
            _win32api.mouse_event(
                _win32con.MOUSEEVENTF_XUP,
                0,
                0,
                _XBUTTON1,
                0,
            )


def _check_button(button: str) -> str:
    if button not in _BUTTONS:
        raise ValueError(
            f"Unknown mouse button {button!r}. Supported buttons: " + ", ".join(sorted(_BUTTONS))
        )
    return button


class Mouse:
    """Control the mouse at absolute screen coordinates.

    Usage::

        Mouse.click(100, 200)
        Mouse.right_click(500, 300)
        Mouse.double_click(400, 150)
        Mouse.move(x=640, y=480)
        Mouse.scroll(x=400, y=300, wheel_dist=3)
    """

    @staticmethod
    def click(x: int, y: int, button: str = "left") -> None:
        button = _check_button(button)
        if button == "x":
            _x_button_input(x, y)
            return
        _mouse.click(button=button, coords=(x, y))

    @staticmethod
    def double_click(x: int, y: int, button: str = "left") -> None:
        button = _check_button(button)
        if button == "x":
            _x_button_input(x, y, double=True)
            return
        _mouse.double_click(button=button, coords=(x, y))

    @staticmethod
    def right_click(x: int, y: int) -> None:
        _mouse.right_click(coords=(x, y))

    @staticmethod
    def move(x: int, y: int) -> None:
        _mouse.move(coords=(x, y))

    @staticmethod
    def scroll(x: int, y: int, wheel_dist: int) -> None:
        """Scroll at (x, y). Positive wheel_dist scrolls up."""
        _mouse.scroll(coords=(x, y), wheel_dist=wheel_dist)

    @staticmethod
    def press(x: int, y: int, button: str = "left") -> None:
        button = _check_button(button)
        if button == "x":
            _x_button_input(x, y, button_up=False)
            return
        _mouse.press(button=button, coords=(x, y))

    @staticmethod
    def release(x: int, y: int, button: str = "left") -> None:
        button = _check_button(button)
        if button == "x":
            _x_button_input(x, y, button_down=False)
            return
        _mouse.release(button=button, coords=(x, y))
