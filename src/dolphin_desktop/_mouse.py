"""Global mouse utilities for screen-coordinate operations."""

from pywinauto import mouse as _mouse

# pywinauto builds an empty event list for anything else and the call then only
# moves the cursor, so an unrecognised name clicks nothing at all.
_BUTTONS = frozenset({"left", "right", "middle", "x"})


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
        _mouse.click(button=_check_button(button), coords=(x, y))

    @staticmethod
    def double_click(x: int, y: int, button: str = "left") -> None:
        _mouse.double_click(button=_check_button(button), coords=(x, y))

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
        _mouse.press(button=_check_button(button), coords=(x, y))

    @staticmethod
    def release(x: int, y: int, button: str = "left") -> None:
        _mouse.release(button=_check_button(button), coords=(x, y))
