"""Unit tests for hidden-desktop keyboard delivery."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import dolphin_desktop._keyboard as keyboard_module


@pytest.mark.skipif(sys.platform != "win32", reason="hidden desktop is Windows-only")
def test_hidden_desktop_focuses_before_sending_and_restores_desktop():
    events = []

    def switch_desktop(handle):
        events.append(("switch", handle))
        return True

    user32 = SimpleNamespace(
        OpenInputDesktop=Mock(return_value=101),
        OpenDesktopW=Mock(return_value=202),
        SwitchDesktop=Mock(side_effect=switch_desktop),
        CloseDesktop=Mock(),
    )
    focus = Mock(side_effect=lambda: events.append("focus"))
    send_keys = Mock(side_effect=lambda keys: events.append(("send", keys)))

    with (
        patch("ctypes.WinDLL", return_value=user32),
        patch.object(keyboard_module, "_send_keys", send_keys),
    ):
        assert keyboard_module._send_keys_on_hidden_desktop("{ENTER}", focus=focus)

    assert events == [
        ("switch", 202),
        "focus",
        ("send", "{ENTER}"),
        ("switch", 101),
    ]
    send_keys.assert_called_once_with("{ENTER}")
    focus.assert_called_once_with()
