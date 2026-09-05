"""Tests for keyboard key-sequence construction and clipboard DIB decoding."""


# Hotkey mnemonic construction

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from dolphin_desktop import Keyboard, WaitTimeoutError
from dolphin_desktop._clipboard import _dib_pixel_offset, _opened
from dolphin_desktop._keyboard import _hotkey_sequence


def _is_packet(action) -> bool:
    """True when pywinauto would send *action* as a Unicode VK_PACKET."""
    from pywinauto.keyboard import KeyAction

    return type(action) is KeyAction


class TestHotkeySequence:
    def test_letter_is_bare_not_braced(self):
        """A braced single char becomes a VK_PACKET that accelerators ignore."""
        assert _hotkey_sequence(("ctrl", "c")) == ("^c", False)

    def test_digit_is_bare(self):
        assert _hotkey_sequence(("ctrl", "1")) == ("^1", False)

    def test_multiple_modifiers_precede_key(self):
        assert _hotkey_sequence(("ctrl", "shift", "n")) == ("^+n", False)

    def test_control_alias(self):
        assert _hotkey_sequence(("control", "s")) == ("^s", False)

    def test_named_key_is_braced(self):
        assert _hotkey_sequence(("alt", "f4")) == ("%{F4}", False)
        assert _hotkey_sequence(("shift", "tab")) == ("+{TAB}", False)

    def test_already_braced_token_passes_through(self):
        assert _hotkey_sequence(("ctrl", "{F4}")) == ("^{F4}", False)

    def test_win_modifier_is_reported_separately(self):
        assert _hotkey_sequence(("win", "e")) == ("e", True)

    def test_win_combined_with_other_modifiers(self):
        assert _hotkey_sequence(("win", "shift", "s")) == ("+s", True)

    def test_unknown_component_raises_value_error(self):
        with pytest.raises(ValueError, match="ctrl, shift, alt, win"):
            _hotkey_sequence(("cmd", "c"))

    def test_sequence_parses_under_pywinauto(self):
        """Every emitted sequence must survive pywinauto's own parser."""
        from pywinauto.keyboard import parse_keys

        for combo in (("ctrl", "c"), ("win", "e"), ("alt", "f4"), ("ctrl", "shift", "n")):
            assert parse_keys(_hotkey_sequence(combo)[0])

    def test_hotkey_sends_built_sequence(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.hotkey("ctrl", "c")
        send.assert_called_once_with("^c")


class TestHotkeyPunctuation:
    """Punctuation must reach the app as a real VK, not a Unicode packet."""

    @pytest.mark.parametrize("char", ["-", "=", ",", ".", "/", ";", "'", "[", "]", "\\"])
    def test_punctuation_is_not_brace_wrapped(self, char):
        assert _hotkey_sequence(("ctrl", char)) == ("^" + char, False)

    @pytest.mark.parametrize("char", ["-", "=", ",", "."])
    def test_punctuation_parses_to_a_real_virtual_key(self, char):
        from pywinauto.keyboard import parse_keys

        actions = parse_keys(_hotkey_sequence(("ctrl", char))[0])
        assert not any(_is_packet(a) for a in actions), actions

    def test_space_maps_to_the_space_key(self):
        from pywinauto.keyboard import parse_keys

        sequence, win = _hotkey_sequence(("ctrl", " "))
        assert sequence == "^{SPACE}"
        assert not win
        assert not any(_is_packet(a) for a in parse_keys(sequence))

    @pytest.mark.parametrize(
        ("char", "expected"),
        [("\t", "^{TAB}"), ("\n", "^{ENTER}"), ("\r", "^{ENTER}")],
    )
    def test_whitespace_maps_to_a_named_key(self, char, expected):
        """pywinauto drops bare \\n / \\t without with_newlines / with_tabs."""
        assert _hotkey_sequence(("ctrl", char)) == (expected, False)

    @pytest.mark.parametrize("char", ["\t", "\n", "\r"])
    def test_whitespace_still_carries_a_key(self, char):
        """Without the mapping the sequence is a bare Ctrl press."""
        from pywinauto.keyboard import parse_keys

        actions = parse_keys(_hotkey_sequence(("ctrl", char))[0])
        assert len(actions) == 3, actions
        assert not any(_is_packet(a) for a in actions)

    @pytest.mark.parametrize("char", list("+^%~(){}"))
    def test_metacharacters_are_still_escaped(self, char):
        """Bare '+' would be Shift, bare '(' would open a group."""
        assert _hotkey_sequence(("ctrl", char)) == ("^{" + char + "}", False)


class TestHotkeyValidation:
    def test_no_arguments_raises(self):
        with pytest.raises(ValueError, match="at least one key"):
            Keyboard.hotkey()

    def test_no_arguments_never_sends(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            with pytest.raises(ValueError):
                Keyboard.hotkey()
        send.assert_not_called()

    def test_two_non_modifiers_raise(self):
        """pywinauto releases the modifier after the first key — 'b' would go bare."""
        with pytest.raises(ValueError, match="at most one non-modifier"):
            _hotkey_sequence(("ctrl", "a", "b"))

    def test_modifier_only_raises(self):
        with pytest.raises(ValueError, match="non-modifier"):
            _hotkey_sequence(("ctrl",))

    def test_win_alone_is_allowed(self):
        assert _hotkey_sequence(("win",)) == ("", True)


class TestHotkeyKeyNameAliases:
    @pytest.mark.parametrize(
        ("spelling", "expected"),
        [
            ("escape", "{ESC}"),
            ("pagedown", "{PGDN}"),
            ("pageup", "{PGUP}"),
            ("return", "{ENTER}"),
            ("printscreen", "{PRTSC}"),
            ("spacebar", "{SPACE}"),
            ("apps", "{VK_APPS}"),
            ("menu", "{VK_APPS}"),
        ],
    )
    def test_natural_spelling_is_accepted(self, spelling, expected):
        assert _hotkey_sequence(("ctrl", spelling)) == ("^" + expected, False)

    def test_abbreviations_still_work(self):
        assert _hotkey_sequence(("ctrl", "esc")) == ("^{ESC}", False)
        assert _hotkey_sequence(("ctrl", "pgdn")) == ("^{PGDN}", False)


class TestWinKeyIsExceptionSafe:
    """The Win-key hold must be released even when the payload blows up.

    A single combined ``"{VK_LWIN down}e{VK_LWIN up}"`` string is executed by
    pywinauto in an unguarded loop, so an interrupt or a failing SendInput
    between the down and the up leaves the Windows key latched system-wide.
    """

    def test_down_payload_and_up_are_three_calls(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.hotkey("win", "e")
        assert [c.args[0] for c in send.call_args_list] == [
            "{VK_LWIN down}",
            "e",
            "{VK_LWIN up}",
        ]

    def test_up_is_emitted_when_the_payload_raises(self):
        sent: list[str] = []

        def _fake(keys, **kwargs):
            sent.append(keys)
            if keys == "e":
                raise RuntimeError("SendInput inserted 0 events")

        with patch("dolphin_desktop._keyboard._send_keys", _fake):
            with pytest.raises(RuntimeError):
                Keyboard.hotkey("win", "e")
        assert sent[-1] == "{VK_LWIN up}"

    def test_up_is_emitted_on_keyboard_interrupt(self):
        sent: list[str] = []

        def _fake(keys, **kwargs):
            sent.append(keys)
            if keys == "+s":
                raise KeyboardInterrupt

        with patch("dolphin_desktop._keyboard._send_keys", _fake):
            with pytest.raises(KeyboardInterrupt):
                Keyboard.hotkey("win", "shift", "s")
        assert sent == ["{VK_LWIN down}", "+s", "{VK_LWIN up}"]

    def test_non_win_hotkey_stays_a_single_call(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.hotkey("alt", "f4")
        send.assert_called_once_with("%{F4}")


# Literal text escaping


class TestKeyboardType:
    def test_percent_is_escaped(self):
        """Unescaped '%' is Alt and opens the window system menu."""
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.type("100% done")
        assert send.call_args.args[0] == "100{%} done"

    def test_parens_and_braces_escaped(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.type("(note){x")
        assert send.call_args.args[0] == "{(}note{)}{{}x"

    def test_modifiers_and_tilde_escaped(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.type("a+b^c~d")
        assert send.call_args.args[0] == "a{+}b{^}c{~}d"

    def test_plain_text_unchanged(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.type("Hello World")
        assert send.call_args.args[0] == "Hello World"

    def test_escape_false_opt_out_keeps_key_syntax(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.type("%F", escape=False)
        assert send.call_args.args[0] == "%F"

    def test_whitespace_flags_preserved(self):
        with patch("dolphin_desktop._keyboard._send_keys") as send:
            Keyboard.type("a b")
        assert send.call_args.kwargs == {
            "with_spaces": True,
            "with_tabs": True,
            "with_newlines": True,
        }


# Mouse button validation


class TestMouseButtonValidation:
    """pywinauto builds an empty event list for an unknown button.

    ``Mouse.click(x, y, "primary")`` then moves the cursor and clicks nothing,
    with no error anywhere.
    """

    @pytest.mark.parametrize(
        ("method", "pywinauto_name"),
        [
            ("click", "click"),
            ("double_click", "double_click"),
            ("press", "press"),
            ("release", "release"),
        ],
    )
    def test_unknown_button_raises_instead_of_clicking_nothing(self, method, pywinauto_name):
        from dolphin_desktop import Mouse

        with patch(f"pywinauto.mouse.{pywinauto_name}") as sent:
            with pytest.raises(ValueError, match="primary"):
                getattr(Mouse, method)(10, 20, "primary")
        sent.assert_not_called()

    @pytest.mark.parametrize("button", ["left", "right", "middle", "x"])
    def test_supported_buttons_pass_through(self, button):
        from dolphin_desktop import Mouse

        with patch("pywinauto.mouse.click") as click:
            Mouse.click(10, 20, button)
        click.assert_called_once_with(button=button, coords=(10, 20))

    def test_move_is_not_a_click_button(self):
        """'move' is accepted by pywinauto but presses no button."""
        from dolphin_desktop import Mouse

        with pytest.raises(ValueError):
            Mouse.click(1, 2, "move")


# Clipboard open retry


def _fake_win32clipboard(open_failures: int) -> MagicMock:
    """A win32clipboard stub whose OpenClipboard fails *open_failures* times."""
    mod = MagicMock()
    state = {"calls": 0}

    def _open():
        state["calls"] += 1
        if state["calls"] <= open_failures:
            raise OSError("(5, 'OpenClipboard', 'Access is denied.')")

    mod.OpenClipboard.side_effect = _open
    mod._calls = state
    return mod


class TestClipboardOpenRetry:
    def test_opens_first_try(self):
        fake = _fake_win32clipboard(0)
        with patch.dict(sys.modules, {"win32clipboard": fake}), _opened() as clip:
            assert clip is fake
        fake.CloseClipboard.assert_called_once()

    def test_retries_while_another_process_holds_it(self):
        fake = _fake_win32clipboard(3)
        with (
            patch.dict(sys.modules, {"win32clipboard": fake}),
            patch("dolphin_desktop._clipboard.time.sleep"),
        ):
            with _opened():
                pass
        assert fake._calls["calls"] == 4
        fake.CloseClipboard.assert_called_once()

    def test_raises_clear_error_when_never_available(self):
        fake = _fake_win32clipboard(10**6)
        with (
            patch.dict(sys.modules, {"win32clipboard": fake}),
            patch("dolphin_desktop._clipboard.time.sleep"),
        ):
            with pytest.raises(WaitTimeoutError, match="Could not open the Windows clipboard"):
                with _opened(timeout=0.05):
                    pass
        fake.CloseClipboard.assert_not_called()

    def test_closes_when_body_raises(self):
        fake = _fake_win32clipboard(0)
        with patch.dict(sys.modules, {"win32clipboard": fake}):
            with pytest.raises(RuntimeError):
                with _opened():
                    raise RuntimeError("boom")
        fake.CloseClipboard.assert_called_once()


class TestClipboardSetTextIsAtomic:
    """EmptyClipboard() takes ownership, so a failed write must not be the end.

    Losing whatever the user had copied because SetClipboardData raised is a
    side effect of a failed call, not of a successful one.
    """

    def _fake(self, previous: str | None) -> MagicMock:
        fake = _fake_win32clipboard(0)
        if previous is None:
            fake.GetClipboardData.side_effect = OSError("no text on the clipboard")
        else:
            fake.GetClipboardData.return_value = previous
        return fake

    def test_normal_write_empties_then_sets(self):
        import win32con

        from dolphin_desktop import Clipboard

        fake = self._fake("old text")
        calls: list[str] = []
        fake.EmptyClipboard.side_effect = lambda: calls.append("empty")
        fake.SetClipboardData.side_effect = lambda fmt, val: calls.append(f"set:{val}")

        with patch.dict(sys.modules, {"win32clipboard": fake}):
            Clipboard.set_text("new text")

        assert calls == ["empty", "set:new text"]
        assert fake.SetClipboardData.call_args.args[0] == win32con.CF_UNICODETEXT

    def test_failed_write_restores_the_previous_text(self):
        from dolphin_desktop import Clipboard

        fake = self._fake("old text")
        restored: list[str] = []

        def _set(_fmt, value):
            if value == "new text":
                raise OSError("(1418, 'SetClipboardData', 'not the owner')")
            restored.append(value)

        fake.SetClipboardData.side_effect = _set

        with patch.dict(sys.modules, {"win32clipboard": fake}):
            with pytest.raises(OSError):
                Clipboard.set_text("new text")

        assert restored == ["old text"]
        fake.CloseClipboard.assert_called_once()

    def test_non_string_is_rejected_before_the_clipboard_is_emptied(self):
        from dolphin_desktop import Clipboard

        fake = self._fake("old text")
        with patch.dict(sys.modules, {"win32clipboard": fake}):
            with pytest.raises(TypeError, match="expects str"):
                Clipboard.set_text(b"bytes")  # type: ignore[arg-type]
        fake.EmptyClipboard.assert_not_called()

    def test_an_empty_clipboard_needs_nothing_restored(self):
        from dolphin_desktop import Clipboard

        fake = self._fake(None)
        fake.SetClipboardData.side_effect = OSError("boom")

        with patch.dict(sys.modules, {"win32clipboard": fake}):
            with pytest.raises(OSError):
                Clipboard.set_text("new text")

        assert fake.SetClipboardData.call_count == 1


class TestClipboardImageNesting:
    """Pillow opens and closes the clipboard itself — it must not run nested."""

    def _fake_with_format(self, available: int) -> MagicMock:
        fake = _fake_win32clipboard(0)
        fake.IsClipboardFormatAvailable.side_effect = lambda fmt: fmt == available
        return fake

    def test_grabclipboard_runs_after_our_handle_is_released(self):
        import win32con
        from PIL import ImageGrab

        from dolphin_desktop import Clipboard

        fake = self._fake_with_format(win32con.CF_BITMAP)
        order: list[str] = []
        fake.CloseClipboard.side_effect = lambda: order.append("close")

        def _grabclipboard():
            order.append("grabclipboard")
            return "image"

        with (
            patch.dict(sys.modules, {"win32clipboard": fake}),
            patch.object(ImageGrab, "grabclipboard", _grabclipboard),
        ):
            assert Clipboard.get_image() == "image"

        assert order == ["close", "grabclipboard"]
        assert fake.CloseClipboard.call_count == 1

    def test_dib_path_never_calls_grabclipboard(self):
        import win32con
        from PIL import ImageGrab

        from dolphin_desktop import Clipboard

        fake = self._fake_with_format(win32con.CF_DIB)
        fake.GetClipboardData.return_value = _bih() + b"\x00" * (16 * 16 * 3)

        with (
            patch.dict(sys.modules, {"win32clipboard": fake}),
            patch.object(ImageGrab, "grabclipboard") as grab,
        ):
            assert Clipboard.get_image() is not None
        grab.assert_not_called()

    def test_no_image_format_returns_none_without_grabbing(self):
        from PIL import ImageGrab

        from dolphin_desktop import Clipboard

        fake = self._fake_with_format(-1)

        with (
            patch.dict(sys.modules, {"win32clipboard": fake}),
            patch.object(ImageGrab, "grabclipboard") as grab,
        ):
            assert Clipboard.get_image() is None
        grab.assert_not_called()


# CF_DIB pixel-array offset


def _bih(
    *,
    size: int = 40,
    bit_count: int = 24,
    compression: int = 0,
    clr_used: int = 0,
) -> bytes:
    header = bytearray(size)
    header[0:4] = size.to_bytes(4, "little")
    header[4:8] = (16).to_bytes(4, "little")
    header[8:12] = (16).to_bytes(4, "little")
    header[12:14] = (1).to_bytes(2, "little")
    header[14:16] = bit_count.to_bytes(2, "little")
    header[16:20] = compression.to_bytes(4, "little")
    if size >= 36:
        header[32:36] = clr_used.to_bytes(4, "little")
    return bytes(header)


class TestDibPixelOffset:
    def test_plain_bi_rgb_24bpp(self):
        assert _dib_pixel_offset(_bih()) == 40

    def test_bitfields_32bpp_adds_masks(self):
        """Browsers put 32bpp BI_BITFIELDS on the clipboard — pixels start +12."""
        assert _dib_pixel_offset(_bih(bit_count=32, compression=3)) == 52

    def test_alphabitfields_adds_four_masks(self):
        assert _dib_pixel_offset(_bih(bit_count=32, compression=6)) == 56

    def test_8bpp_implicit_palette(self):
        assert _dib_pixel_offset(_bih(bit_count=8)) == 40 + 256 * 4

    def test_4bpp_implicit_palette(self):
        assert _dib_pixel_offset(_bih(bit_count=4)) == 40 + 16 * 4

    def test_clr_used_overrides_implicit_palette(self):
        assert _dib_pixel_offset(_bih(bit_count=8, clr_used=17)) == 40 + 17 * 4

    def test_optimal_palette_on_truecolor_dib(self):
        assert _dib_pixel_offset(_bih(bit_count=24, clr_used=8)) == 40 + 8 * 4

    def test_v5_header_carries_masks_inline(self):
        assert _dib_pixel_offset(_bih(size=124, bit_count=32, compression=3)) == 124

    def test_bitmapcoreheader_uses_rgbtriple_palette(self):
        header = bytearray(12)
        header[0:4] = (12).to_bytes(4, "little")
        header[10:12] = (8).to_bytes(2, "little")
        assert _dib_pixel_offset(bytes(header)) == 12 + 256 * 3

    def test_zero_bit_count_has_no_palette(self):
        assert _dib_pixel_offset(_bih(bit_count=0, compression=4)) == 40


def test_clipboard_dib_offsets_cover_core_and_bitfield_headers() -> None:
    from dolphin_desktop._clipboard import _dib_pixel_offset

    core = (12).to_bytes(4, "little") + b"\0" * 6 + (8).to_bytes(2, "little")
    bitfields = (
        (40).to_bytes(4, "little")
        + b"\0" * 10
        + (32).to_bytes(2, "little")
        + (3).to_bytes(4, "little")
        + b"\0" * 16
    )
    assert _dib_pixel_offset(core) == 780
    assert _dib_pixel_offset(bitfields) == 52


def test_keyboard_hotkey_sequences_validate_and_escape() -> None:
    from dolphin_desktop._keyboard import _hotkey_sequence

    assert _hotkey_sequence(("ctrl", "c")) == ("^c", False)
    assert _hotkey_sequence(("win", "enter")) == ("{ENTER}", True)
    with pytest.raises(ValueError, match="at most one"):
        _hotkey_sequence(("ctrl", "a", "b"))
