"""Tests for standard-dialog discovery and control lookup budgets."""


# Dialog discovery — class beats caption

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from dolphin_desktop._dialogs import _CONTROL_TIMEOUT, FileDialog, MessageBox, _find_dialog_window


def _windows(*entries: tuple[int, str, str]) -> list[tuple[int, str, str]]:
    return list(entries)


class TestFindDialogWindowRanking:
    """A caption is weak evidence; ``#32770`` is strong evidence."""

    def _find(self, windows, foreground: int = 0):
        with (
            patch("dolphin_desktop._dialogs._enum_visible_windows", return_value=windows),
            patch("win32gui.GetForegroundWindow", return_value=foreground),
            patch("dolphin_desktop._dialogs._hwnd_to_spec", side_effect=lambda h: h),
        ):
            return _find_dialog_window(timeout=0.0)

    def test_a_real_dialog_beats_a_browser_tab(self):
        """'Select your plan' must not outrank the picker when it is not foreground."""
        windows = _windows(
            (100, "Chrome_WidgetWin_1", "Select your plan - Acme - Google Chrome"),
            (200, "#32770", "Otwórz"),
        )
        assert self._find(windows) == 200

    def test_the_foreground_dialog_still_wins_among_dialogs(self):
        windows = _windows(
            (200, "#32770", "Open"),
            (300, "#32770", "Save As"),
        )
        assert self._find(windows, foreground=300) == 300

    def test_a_foreground_browser_tab_does_not_win(self):
        windows = _windows(
            (100, "Chrome_WidgetWin_1", "Select your plan"),
            (200, "#32770", "Open"),
        )
        assert self._find(windows, foreground=100) == 200

    def test_a_caption_containing_a_verb_mid_string_is_ignored(self):
        windows = _windows(
            (100, "Chrome_WidgetWin_1", "Acme — Choose a plan"),
            (200, "#32770", "Save As"),
        )
        assert self._find(windows) == 200

    def test_the_caption_fallback_still_works_without_a_32770(self):
        windows = _windows((400, "Windows.UI.Core.CoreWindow", "Open File"))
        assert self._find(windows) == 400

    def test_nothing_matching_raises(self):
        with pytest.raises(TimeoutError, match="Could not find file dialog"):
            self._find(_windows((100, "Notepad", "Untitled - Notepad")))


# Control lookup budget


_PYWINAUTO_FIND_TIMEOUT = 5.0


class _Spec:
    """A pywinauto WindowSpecification stand-in with a resolve cost."""

    def __init__(self, dialog: _Dialog, present: bool) -> None:
        self._dialog = dialog
        self._present = present

    def exists(self, timeout: float = 0.0, *args, **kwargs) -> bool:
        self._dialog.exists_timeouts.append(timeout)
        if not self._present:
            time.sleep(timeout)
        return self._present

    def click_input(self) -> None:
        if not self._present:
            # What pywinauto really does: resolve the spec, waiting out the
            # global window_find_timeout before raising.
            time.sleep(_PYWINAUTO_FIND_TIMEOUT)
            raise LookupError("no such control")
        self._dialog.clicked.append(self)


class _Dialog:
    """Fake dialog window exposing a fixed set of controls."""

    def __init__(self, present: set[str] | None = None) -> None:
        self._present = present or set()
        self.exists_timeouts: list[float] = []
        self.clicked: list[_Spec] = []

    def child_window(self, **criteria) -> _Spec:
        key = criteria.get("auto_id") or criteria.get("title") or ""
        return _Spec(self, key in self._present)

    def exists(self) -> bool:
        return True

    def is_visible(self) -> bool:
        return True


class TestControlLookupIsBudgeted:
    """Every miss otherwise costs pywinauto's 5 s window_find_timeout."""

    @pytest.fixture(autouse=True)
    def _fast_probe(self):
        with patch("dolphin_desktop._dialogs._CONTROL_TIMEOUT", 0.05):
            yield

    def test_a_missing_control_is_probed_not_clicked(self):
        dialog = _Dialog()
        with pytest.raises(RuntimeError, match="confirm button"):
            FileDialog(dialog).confirm()

        assert dialog.clicked == []
        assert dialog.exists_timeouts
        assert all(t <= 1.0 for t in dialog.exists_timeouts)

    def test_confirm_gives_up_quickly(self):
        dialog = _Dialog()
        started = time.perf_counter()
        with pytest.raises(RuntimeError):
            FileDialog(dialog).confirm()
        elapsed = time.perf_counter() - started

        assert elapsed < 2.0, f"confirm() spent {elapsed:.1f}s on missing controls"

    def test_message_box_click_gives_up_quickly(self):
        dialog = _Dialog()
        started = time.perf_counter()
        with pytest.raises(RuntimeError, match="No button matching"):
            MessageBox(dialog).click("Cancel", "Anuluj")
        elapsed = time.perf_counter() - started

        assert elapsed < 2.0, f"click() spent {elapsed:.1f}s on missing buttons"

    def test_the_present_control_is_still_clicked(self):
        dialog = _Dialog(present={"1"})
        FileDialog(dialog).confirm()
        assert len(dialog.clicked) == 1

    def test_the_title_fallback_is_still_reached(self):
        dialog = _Dialog(present={"Zapisz"})
        FileDialog(dialog).confirm()
        assert len(dialog.clicked) == 1

    def test_cancel_falls_back_to_the_localised_label(self):
        dialog = _Dialog(present={"Anuluj"})
        FileDialog(dialog).cancel()
        assert len(dialog.clicked) == 1

    def test_message_box_clicks_the_second_candidate(self):
        dialog = _Dialog(present={"Anuluj"})
        MessageBox(dialog).click("Cancel", "Anuluj")
        assert len(dialog.clicked) == 1


def test_default_control_timeout_is_well_under_pywinautos():
    assert _CONTROL_TIMEOUT < _PYWINAUTO_FIND_TIMEOUT


class TestKnownDialogRankingLimitation:
    """Pins the trade-off in `_find_dialog_window` so it is visible, not surprising.

    A Win11 XAML file picker is not a `#32770`. When an unrelated `#32770`
    happens to be open at the same time, the class match wins and the picker
    is missed. That is deliberate: the alternative — letting any foreground
    window with a dialog-ish caption outrank a real `#32770` — hands the
    caller a browser tab, which is worse. Narrowing it needs a verified class
    allowlist for the XAML pickers.
    """

    def _find(self, windows, foreground: int = 0):
        return TestFindDialogWindowRanking._find(self, windows, foreground)

    def test_a_stray_32770_outranks_a_foreground_xaml_picker(self):
        windows = _windows(
            (400, "Windows.UI.Core.CoreWindow", "Open"),
            (200, "#32770", "Save As"),
        )
        assert self._find(windows, foreground=400) == 200


def test_file_dialog_confirm_uses_auto_id_before_localized_titles(monkeypatch) -> None:
    from dolphin_desktop._dialogs import FileDialog

    window = SimpleNamespace()
    dialog = FileDialog(window)
    auto_id = Mock(return_value=True)
    monkeypatch.setattr(dialog, "_click_by_auto_id", auto_id)
    dialog.confirm()
    auto_id.assert_called_once_with("1")


def test_dialog_file_path_and_message_box_fallbacks(monkeypatch) -> None:
    import dolphin_desktop._dialogs as dialogs

    edit = Mock()
    edit.class_name.return_value = "Edit"
    window = Mock()
    window.children.return_value = [edit]
    assert dialogs.FileDialog(window).set_path("C:\\reports\\April report.txt")._win is window
    edit.set_focus.assert_called_once_with()
    edit.set_edit_text.assert_called_once_with("C:\\reports\\April report.txt")

    fallback_window = Mock()
    broken = Mock()
    broken.class_name.side_effect = RuntimeError("gone")
    fallback_window.children.return_value = [broken]
    send_keys = Mock()
    monkeypatch.setattr("pywinauto.keyboard.send_keys", send_keys)
    monkeypatch.setattr(dialogs.time, "sleep", Mock())
    dialogs.FileDialog(fallback_window).set_path("C:\\a b{c}")
    fallback_window.set_focus.assert_called_once_with()
    assert send_keys.call_args_list[0].args == ("^l",)
    assert send_keys.call_args_list[-1].args == ("{ENTER}",)

    gone = Mock()
    gone.exists.return_value = False
    gone.is_visible.return_value = False
    dialogs.FileDialog(gone).confirm()
    dialogs.FileDialog(gone).cancel()

    static_empty = Mock()
    static_empty.class_name.return_value = "Static"
    static_empty.window_text.return_value = ""
    static_text = Mock()
    static_text.class_name.return_value = "Static"
    static_text.window_text.return_value = "Operation failed"
    box_window = Mock()
    box_window.children.return_value = [static_empty, static_text]
    box = dialogs.MessageBox(box_window)
    assert box.text() == "Operation failed"
    box.click_ok()
    box.click_cancel()
    box.click_yes()
    box.click_no()
    assert box_window.child_window.call_count == 4


def test_dialog_discovery_filters_class_title_and_foreground(monkeypatch) -> None:
    import dolphin_desktop._dialogs as dialogs

    windows = [(10, "Other", "Open browser"), (20, "#32770", "Open")]
    monkeypatch.setattr(dialogs, "_enum_visible_windows", lambda: windows)
    monkeypatch.setattr("win32gui.GetForegroundWindow", lambda: 20)
    monkeypatch.setattr(dialogs, "_hwnd_to_spec", lambda hwnd: ("spec", hwnd))
    assert dialogs._find_window("#32770", r"open", 0) == ("spec", 20)
    assert dialogs.MessageBox.wait_for(0)._win == ("spec", 20)


def test_dialog_message_box_tries_buttons_in_order() -> None:
    from dolphin_desktop._dialogs import MessageBox

    window = Mock()
    button = Mock()
    window.child_window.side_effect = [Mock(click_input=Mock(side_effect=RuntimeError())), button]
    MessageBox(window).click("No", "Cancel")
    button.click_input.assert_called_once()
