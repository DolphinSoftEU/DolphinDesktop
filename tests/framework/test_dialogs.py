"""Tests for standard-dialog discovery and control lookup budgets."""


# Dialog discovery — class beats caption

from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from dolphin_desktop._dialogs import (
    _CONTROL_TIMEOUT,
    FileDialog,
    MessageBox,
    _find_dialog_window,
)


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

    def __init__(
        self,
        dialog: _Dialog,
        present: bool,
        key: str,
        control_type: str | None,
    ) -> None:
        self._dialog = dialog
        self._present = present
        self.key = key
        self.control_type = control_type

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
        close_control = self._dialog.close_control
        if close_control is None or close_control == (self.key, self.control_type):
            self._dialog._alive = False

    def set_focus(self) -> None:
        if not self._present:
            raise LookupError("no such control")
        self._dialog.focused.append(self)


class _Dialog:
    """Fake dialog window exposing a fixed set of controls."""

    def __init__(
        self,
        present: set[str] | None = None,
        *,
        controls: set[tuple[str, str]] | None = None,
        close_control: tuple[str, str] | None = None,
    ) -> None:
        self._present = present or set()
        self._controls = controls
        self.close_control = close_control
        self._alive = True
        self.exists_timeouts: list[float] = []
        self.clicked: list[_Spec] = []
        self.focused: list[_Spec] = []

    def child_window(self, **criteria) -> _Spec:
        key = criteria.get("auto_id") or criteria.get("title") or ""
        control_type = criteria.get("control_type")
        present = (
            (key, control_type) in self._controls
            if self._controls is not None
            else key in self._present
        )
        return _Spec(self, present, key, control_type)

    def exists(self, timeout: float = 0.0) -> bool:
        return self._alive

    def is_visible(self) -> bool:
        return self._alive


class _NativeDialog(_Dialog):
    handle = 100

    def wrapper_object(self):
        return SimpleNamespace(handle=100)


class _UnresolvedWindow:
    """Window stand-in whose wrapper resolution must never be attempted."""

    def __init__(self, exists_error: Exception | None = None) -> None:
        self.exists_error = exists_error
        self.exists_timeouts: list[float] = []
        self.wrapper_calls = 0

    def exists(self, timeout: float = 0.0) -> bool:
        self.exists_timeouts.append(timeout)
        if self.exists_error is not None:
            raise self.exists_error
        return False

    def wrapper_object(self):
        self.wrapper_calls += 1
        raise AssertionError("dialog-close probe must not resolve a lazy wrapper")


class TestControlLookupIsBudgeted:
    """Every miss otherwise costs pywinauto's 5 s window_find_timeout."""

    @pytest.fixture(autouse=True)
    def _fast_probe(self):
        with (
            patch("dolphin_desktop._dialogs._CONTROL_TIMEOUT", 0.05),
            patch("dolphin_desktop._dialogs._DIALOG_CLOSE_TIMEOUT", 0.05),
        ):
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

    def test_unknown_dialog_probe_error_does_not_report_success(self):
        dialog = _Dialog(present={"1"})
        dialog.exists = Mock(side_effect=RuntimeError("COM failure"))

        assert FileDialog(dialog)._dialog_gone() is False
        with pytest.raises(RuntimeError, match="confirm button"):
            FileDialog(dialog).confirm()

    def test_pywinauto_missing_dialog_is_reported_as_gone(self):
        from pywinauto.findwindows import ElementNotFoundError

        dialog = _Dialog()
        dialog.exists = Mock(side_effect=ElementNotFoundError("dialog gone"))

        assert FileDialog(dialog)._dialog_gone() is True

    def test_missing_window_does_not_trigger_unbounded_wrapper_resolution(self):
        window = _UnresolvedWindow()
        started = time.perf_counter()

        assert FileDialog(window)._dialog_gone() is True

        elapsed = time.perf_counter() - started
        assert elapsed < 0.2
        assert window.exists_timeouts == [0.0]
        assert window.wrapper_calls == 0

    def test_unavailable_window_wait_is_bounded_and_not_reported_gone(self):
        window = _UnresolvedWindow(RuntimeError("UIA unavailable"))
        started = time.perf_counter()

        assert FileDialog(window)._wait_for_dialog_gone(timeout=0.05) is False

        elapsed = time.perf_counter() - started
        assert elapsed < 0.5
        assert window.exists_timeouts
        assert all(timeout == 0.0 for timeout in window.exists_timeouts)
        assert window.wrapper_calls == 0

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 HWND validation")
    def test_com_error_with_valid_visible_hwnd_is_not_reported_as_gone(self, monkeypatch):
        from _ctypes import COMError

        dialog = _NativeDialog()
        dialog.is_visible = Mock(side_effect=COMError(-1, "stale UIA", None))
        win32gui = SimpleNamespace(
            IsWindow=Mock(return_value=True),
            IsWindowVisible=Mock(return_value=True),
        )
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)

        assert FileDialog(dialog)._dialog_gone() is False
        win32gui.IsWindow.assert_called_once_with(100)
        win32gui.IsWindowVisible.assert_called_once_with(100)

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 HWND validation")
    def test_com_error_with_invalid_hwnd_is_reported_as_gone(self, monkeypatch):
        from _ctypes import COMError

        dialog = _NativeDialog()
        dialog.is_visible = Mock(side_effect=COMError(-1, "stale UIA", None))
        win32gui = SimpleNamespace(
            IsWindow=Mock(return_value=False),
            IsWindowVisible=Mock(),
        )
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)

        assert FileDialog(dialog)._dialog_gone() is True
        win32gui.IsWindow.assert_called_once_with(100)
        win32gui.IsWindowVisible.assert_not_called()

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 HWND validation")
    def test_unknown_wrapper_error_with_valid_visible_hwnd_is_not_reported_as_gone(
        self, monkeypatch
    ):
        dialog = _NativeDialog()
        dialog.is_visible = Mock(side_effect=RuntimeError("unexpected wrapper error"))
        win32gui = SimpleNamespace(
            IsWindow=Mock(return_value=True),
            IsWindowVisible=Mock(return_value=True),
        )
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)

        assert FileDialog(dialog)._dialog_gone() is False

    def test_confirm_verifies_close_and_tries_the_next_auto_id_candidate(self):
        dialog = _Dialog(
            controls={("1", "Button"), ("1", "SplitButton")},
            close_control=("1", "SplitButton"),
        )
        FileDialog(dialog).confirm()

        assert [(item.key, item.control_type) for item in dialog.clicked] == [
            ("1", "Button"),
            ("1", "SplitButton"),
        ]

    def test_confirm_uses_enter_after_splitbutton_click_has_no_effect(self):
        dialog = _Dialog(
            controls={("1", "SplitButton")},
            close_control=("keyboard", "Enter"),
        )

        def submit_with_enter() -> None:
            assert [(item.key, item.control_type) for item in dialog.focused] == [
                ("1", "SplitButton"),
            ]
            dialog._alive = False

        with patch(
            "dolphin_desktop._dialogs._send_enter",
            side_effect=submit_with_enter,
        ):
            FileDialog(dialog).confirm()

        assert [(item.key, item.control_type) for item in dialog.clicked] == [
            ("1", "SplitButton"),
        ]

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 message fallback")
    def test_confirm_uses_async_native_post_message_fallback(self, monkeypatch):
        dialog = _NativeDialog()
        events = []
        win32con = SimpleNamespace(BM_CLICK=0x00F5, WM_COMMAND=0x0111, BN_CLICKED=0)

        def post_message(hwnd, message, w_param, l_param):
            events.append((hwnd, message, w_param, l_param))
            if message == win32con.WM_COMMAND:
                dialog._alive = False

        win32gui = SimpleNamespace(
            GetDlgItem=Mock(return_value=200),
            PostMessage=post_message,
        )
        monkeypatch.setitem(sys.modules, "win32con", win32con)
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)

        FileDialog(dialog).confirm()

        assert events == [
            (200, win32con.BM_CLICK, 0, 0),
            (100, win32con.WM_COMMAND, 1, 200),
        ]
        win32gui.GetDlgItem.assert_called_once_with(100, 1)

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 message fallback")
    def test_native_confirm_does_not_report_success_when_dialog_remains(self, monkeypatch):
        dialog = _NativeDialog()
        win32con = SimpleNamespace(BM_CLICK=0x00F5, WM_COMMAND=0x0111, BN_CLICKED=0)
        win32gui = SimpleNamespace(
            GetDlgItem=Mock(return_value=200),
            PostMessage=Mock(return_value=True),
        )
        monkeypatch.setitem(sys.modules, "win32con", win32con)
        monkeypatch.setitem(sys.modules, "win32gui", win32gui)

        with pytest.raises(RuntimeError, match="confirm button"):
            FileDialog(dialog).confirm()

        assert win32gui.PostMessage.call_args_list == [
            ((200, win32con.BM_CLICK, 0, 0),),
            ((100, win32con.WM_COMMAND, 1, 200),),
        ]

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


def test_file_dialog_confirm_clicks_split_button_main_area(monkeypatch) -> None:
    import dolphin_desktop._dialogs as dialogs

    window = Mock()
    window._alive = True
    window.exists.side_effect = lambda timeout=0.0: window._alive
    window.is_visible.side_effect = lambda: window._alive

    button = Mock()
    button.exists.return_value = False

    split_button = Mock()
    split_button.exists.return_value = True
    split_button.click_input.return_value = None

    wrapper = Mock()
    wrapper.rectangle.return_value = SimpleNamespace(
        width=lambda: 100,
        height=lambda: 20,
    )

    def click_main_area(*, coords):
        assert coords == (25, 10)
        window._alive = False

    wrapper.click_input.side_effect = click_main_area
    split_button.wrapper_object.return_value = wrapper

    def child_window(**criteria):
        if criteria == {"auto_id": "1", "control_type": "Button"}:
            return button
        if criteria == {"auto_id": "1", "control_type": "SplitButton"}:
            return split_button
        raise AssertionError(f"unexpected lookup: {criteria}")

    window.child_window.side_effect = child_window

    with (
        monkeypatch.context() as patcher,
    ):
        patcher.setattr(dialogs, "_DIALOG_CLOSE_TIMEOUT", 0.01)
        patcher.setattr(dialogs.time, "sleep", Mock())
        dialogs.FileDialog(window).confirm()

    wrapper.rectangle.assert_called_once_with()
    wrapper.click_input.assert_called_once_with(coords=(25, 10))


def test_dialog_file_path_and_message_box_fallbacks(monkeypatch) -> None:
    import dolphin_desktop._dialogs as dialogs

    edit = Mock()
    edit.class_name.return_value = "Edit"
    window = Mock()
    window.children.return_value = [edit]
    assert dialogs.FileDialog(window).set_path("C:\\reports\\April report.txt")._win is window
    edit.set_focus.assert_called_once_with()
    edit.set_edit_text.assert_called_once_with("C:\\reports\\April report.txt")

    nested_window = Mock()
    nested_edit = Mock()
    nested_spec = Mock()
    nested_spec.exists.return_value = True
    nested_spec.wrapper_object.return_value = nested_edit
    nested_window.children.return_value = []
    nested_window.child_window.return_value = nested_spec
    assert (
        dialogs.FileDialog(nested_window).set_path("artifacts/DESKTOP-059/input.txt")._win
        is nested_window
    )
    nested_window.child_window.assert_called_once_with(
        auto_id="1148",
        control_type="Edit",
    )
    nested_edit.set_focus.assert_called_once_with()
    nested_edit.set_edit_text.assert_called_once_with(
        str(Path("artifacts/DESKTOP-059/input.txt").resolve())
    )

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


def test_file_dialog_resolves_relative_path_from_process_cwd(monkeypatch, tmp_path) -> None:
    import dolphin_desktop._dialogs as dialogs

    edit = Mock()
    edit.class_name.return_value = "Edit"
    window = Mock()
    window.children.return_value = [edit]
    monkeypatch.chdir(tmp_path)

    relative_path = "artifacts/DESKTOP-059/input.txt"
    dialogs.FileDialog(window).set_path(relative_path)

    edit.set_edit_text.assert_called_once_with(str((tmp_path / relative_path).resolve()))


def test_file_dialog_preserves_absolute_path(monkeypatch) -> None:
    import dolphin_desktop._dialogs as dialogs

    edit = Mock()
    edit.class_name.return_value = "Edit"
    window = Mock()
    window.children.return_value = [edit]
    absolute_path = r"C:\reports\April report.txt"

    dialogs.FileDialog(window).set_path(absolute_path)

    edit.set_edit_text.assert_called_once_with(absolute_path)


def test_file_dialog_normalizes_posix_spelling_of_absolute_windows_path() -> None:
    import dolphin_desktop._dialogs as dialogs

    edit = Mock()
    edit.class_name.return_value = "Edit"
    window = Mock()
    window.children.return_value = [edit]
    absolute_path = "C:/reports/April report.txt"

    dialogs.FileDialog(window).set_path(absolute_path)

    edit.set_edit_text.assert_called_once_with(str(Path(absolute_path)))


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
