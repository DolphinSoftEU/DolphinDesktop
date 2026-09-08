"""Tests for :mod:`dolphin_desktop._mouse`."""

from __future__ import annotations

import sys
from unittest.mock import Mock

import pytest


def test_mouse_delegates_every_coordinate_action(monkeypatch) -> None:
    from dolphin_desktop import _mouse

    wheel = Mock()
    monkeypatch.setattr(_mouse._mouse, "scroll", wheel)
    _mouse.Mouse.scroll(10, 20, -3)
    wheel.assert_called_once_with(coords=(10, 20), wheel_dist=-3)


def test_mouse_forwards_all_coordinate_operations_and_checks_buttons(monkeypatch) -> None:
    from dolphin_desktop import _mouse

    names = ("click", "double_click", "right_click", "move", "scroll", "press", "release")
    calls = {name: Mock() for name in names}
    for name, mock in calls.items():
        monkeypatch.setattr(_mouse._mouse, name, mock)

    _mouse.Mouse.click(1, 2)
    _mouse.Mouse.double_click(3, 4, button="middle")
    _mouse.Mouse.right_click(5, 6)
    _mouse.Mouse.move(7, 8)
    _mouse.Mouse.scroll(9, 10, -2)
    _mouse.Mouse.press(11, 12, button="middle")
    _mouse.Mouse.release(13, 14)

    calls["click"].assert_called_once_with(button="left", coords=(1, 2))
    calls["double_click"].assert_called_once_with(button="middle", coords=(3, 4))
    calls["right_click"].assert_called_once_with(coords=(5, 6))
    calls["move"].assert_called_once_with(coords=(7, 8))
    calls["scroll"].assert_called_once_with(coords=(9, 10), wheel_dist=-2)
    calls["press"].assert_called_once_with(button="middle", coords=(11, 12))
    calls["release"].assert_called_once_with(button="left", coords=(13, 14))

    with pytest.raises(ValueError, match="Unknown mouse button"):
        _mouse.Mouse.press(0, 0, button="sideways")
    with pytest.raises(ValueError, match="Unknown mouse button"):
        _mouse.Mouse.release(0, 0, button="sideways")


def test_mouse_validates_buttons_before_delegating(monkeypatch) -> None:
    from dolphin_desktop import _mouse

    click = Mock()
    monkeypatch.setattr(_mouse._mouse, "click", click)
    _mouse.Mouse.click(3, 4, "middle")
    click.assert_called_once_with(button="middle", coords=(3, 4))
    with pytest.raises(ValueError, match="Unknown mouse button"):
        _mouse.Mouse.click(0, 0, "trackball")


@pytest.mark.skipif(sys.platform != "win32", reason="XBUTTON1 is Windows-only")
def test_mouse_x_button_sends_xbutton1_data(monkeypatch) -> None:
    from dolphin_desktop import _mouse

    win32api = Mock()
    monkeypatch.setattr(_mouse, "_win32api", win32api)

    _mouse.Mouse.click(30, 40, button="x")

    win32api.SetCursorPos.assert_called_once_with((30, 40))
    assert win32api.mouse_event.call_args_list == [
        ((_mouse._win32con.MOUSEEVENTF_XDOWN, 0, 0, 1, 0),),
        ((_mouse._win32con.MOUSEEVENTF_XUP, 0, 0, 1, 0),),
    ]
