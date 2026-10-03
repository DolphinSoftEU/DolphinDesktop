"""Unit tests for hidden-desktop keyboard delivery."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

import dolphin_desktop._keyboard as keyboard_module


class _FakeKeyAction:
    """Stand-in for a pywinauto key action, as returned by ``parse_keys``."""

    def __init__(self, vk, scan=0, flags=0, *, down=True, up=True, key=None):
        self._info = (vk, scan, flags)
        self.down = down
        self.up = up
        self.key = key

    def get_key_info(self):
        return self._info

    def __repr__(self):  # pragma: no cover - only used in assertion failures
        return f"_FakeKeyAction(vk={self._info[0]!r})"


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


def test_hidden_desktop_returns_false_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert keyboard_module._send_keys_on_hidden_desktop("{ENTER}") is False


@pytest.mark.skipif(sys.platform != "win32", reason="hidden desktop is Windows-only")
@pytest.mark.parametrize(
    ("original", "hidden"),
    [
        pytest.param(0, 202, id="original-fails-to-open"),
        pytest.param(101, 0, id="hidden-desktop-fails-to-open"),
    ],
)
def test_hidden_desktop_closes_whichever_handle_opened_when_the_other_fails(original, hidden):
    user32 = SimpleNamespace(
        OpenInputDesktop=Mock(return_value=original),
        OpenDesktopW=Mock(return_value=hidden),
        SwitchDesktop=Mock(),
        CloseDesktop=Mock(),
    )

    with patch("ctypes.WinDLL", return_value=user32):
        assert keyboard_module._send_keys_on_hidden_desktop("{ENTER}") is False

    if original:
        user32.CloseDesktop.assert_any_call(original)
    if hidden:
        user32.CloseDesktop.assert_any_call(hidden)
    user32.SwitchDesktop.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="hidden desktop is Windows-only")
def test_hidden_desktop_closes_both_handles_when_switch_fails():
    user32 = SimpleNamespace(
        OpenInputDesktop=Mock(return_value=101),
        OpenDesktopW=Mock(return_value=202),
        SwitchDesktop=Mock(return_value=False),
        CloseDesktop=Mock(),
    )

    with patch("ctypes.WinDLL", return_value=user32):
        assert keyboard_module._send_keys_on_hidden_desktop("{ENTER}") is False

    assert user32.CloseDesktop.call_args_list == [call(202), call(101)]


@pytest.mark.skipif(sys.platform != "win32", reason="hidden desktop is Windows-only")
def test_hidden_desktop_raises_oserror_when_restore_fails():
    # First SwitchDesktop call (to the hidden desktop) succeeds; the restore
    # call in the finally block fails.
    switch_results = iter([True, False])
    user32 = SimpleNamespace(
        OpenInputDesktop=Mock(return_value=101),
        OpenDesktopW=Mock(return_value=202),
        SwitchDesktop=Mock(side_effect=lambda handle: next(switch_results)),
        CloseDesktop=Mock(),
    )

    with (
        patch("ctypes.WinDLL", return_value=user32),
        patch.object(keyboard_module, "_send_keys", Mock()),
        pytest.raises(OSError, match="SwitchDesktop restore failed"),
    ):
        keyboard_module._send_keys_on_hidden_desktop("{ENTER}")

    assert user32.CloseDesktop.call_args_list == [call(202), call(101)]


def test_post_keys_to_hwnd_raises_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(OSError, match="only available on Windows"):
        keyboard_module._post_keys_to_hwnd(123, "a")


@pytest.mark.skipif(sys.platform != "win32", reason="posts window messages via WinDLL")
def test_post_keys_to_hwnd_raises_when_window_thread_lookup_fails():
    user32 = SimpleNamespace(
        GetWindowThreadProcessId=Mock(return_value=0),
        SendMessageW=Mock(),
    )

    with (
        patch("ctypes.WinDLL", return_value=user32),
        patch("pywinauto.keyboard.parse_keys", return_value=[]),
        pytest.raises(OSError, match="GetWindowThreadProcessId failed"),
    ):
        keyboard_module._post_keys_to_hwnd(123, "a")


@pytest.mark.skipif(sys.platform != "win32", reason="posts window messages via WinDLL")
def test_post_keys_to_hwnd_sends_wm_char_for_unicode_only_actions():
    # vk == 0 with the unicode flag set is how pywinauto represents a
    # non-ASCII character that has no virtual-key code.
    action = _FakeKeyAction(vk=0, flags=0x0004, key="é")
    user32 = SimpleNamespace(
        GetWindowThreadProcessId=Mock(return_value=4242),
        SendMessageW=Mock(),
    )

    with (
        patch("ctypes.WinDLL", return_value=user32),
        patch("pywinauto.keyboard.parse_keys", return_value=[action]),
        patch("time.sleep") as sleep,
    ):
        keyboard_module._post_keys_to_hwnd(123, "é", pause=0.01)

    user32.SendMessageW.assert_called_once_with(123, 0x0102, ord("é"), 0)
    sleep.assert_called_once_with(0.01)


@pytest.mark.skipif(sys.platform != "win32", reason="posts window messages via WinDLL")
def test_post_keys_to_hwnd_raises_for_unsupported_action():
    # vk == 0 without the unicode flag matches nothing _post_keys_to_hwnd
    # knows how to deliver.
    action = _FakeKeyAction(vk=0, flags=0, key=None)
    user32 = SimpleNamespace(
        GetWindowThreadProcessId=Mock(return_value=4242),
        SendMessageW=Mock(),
    )

    with (
        patch("ctypes.WinDLL", return_value=user32),
        patch("pywinauto.keyboard.parse_keys", return_value=[action]),
        pytest.raises(ValueError, match="unsupported action"),
    ):
        keyboard_module._post_keys_to_hwnd(123, "￾")


@pytest.mark.skipif(sys.platform != "win32", reason="posts window messages via WinDLL")
def test_post_keys_to_hwnd_sends_keydown_and_keyup_with_extended_flag():
    # VK_HOME (0x24) as an extended key, e.g. produced for {HOME}.
    action = _FakeKeyAction(vk=0x24, scan=0x47, flags=0x0001)
    user32 = SimpleNamespace(
        GetWindowThreadProcessId=Mock(return_value=4242),
        SendMessageW=Mock(),
    )

    with (
        patch("ctypes.WinDLL", return_value=user32),
        patch("pywinauto.keyboard.parse_keys", return_value=[action]),
        patch("time.sleep") as sleep,
    ):
        keyboard_module._post_keys_to_hwnd(123, "{HOME}", pause=0.02)

    expected_lparam = (0x47 << 16) | (1 << 24)
    assert user32.SendMessageW.call_args_list == [
        call(123, 0x0100, 0x24, expected_lparam),
        call(123, 0x0101, 0x24, expected_lparam | (1 << 30) | (1 << 31)),
    ]
    assert sleep.call_count == 2
    sleep.assert_called_with(0.02)


def test_keyboard_press_sends_keys_via_pywinauto():
    with patch.object(keyboard_module, "_send_keys") as send:
        keyboard_module.Keyboard.press("^s")
    send.assert_called_once_with("^s")
