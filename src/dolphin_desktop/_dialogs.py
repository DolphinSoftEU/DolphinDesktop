"""Helpers for interacting with common Windows standard dialogs."""

from __future__ import annotations

import re
import time

from pywinauto import Desktop as _PWDesktop  # type: ignore[import-untyped]

_DIALOG_TITLE_RE = re.compile(
    r"(Open|Save|Browse|Otwórz|Otwieranie|Zapisz|Zapis|Choose|Select|Pick)",
    re.IGNORECASE,
)


def _enum_visible_windows() -> list[tuple[int, str, str]]:
    """Return (hwnd, class_name, title) for all visible top-level windows.

    Uses win32gui.EnumWindows rather than pywinauto's Desktop().windows()
    because the latter misses owned windows of packaged (MSIX) apps such as
    the Windows 11 file picker (#32770 owned by Notepad).
    """
    import win32gui  # type: ignore[import-untyped]

    results: list[tuple[int, str, str]] = []

    def _cb(hwnd: int, _: None) -> None:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            cls = win32gui.GetClassName(hwnd)
            title = win32gui.GetWindowText(hwnd)
            results.append((hwnd, cls, title))
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return results


def _hwnd_to_spec(hwnd: int):
    """Return a pywinauto WindowSpecification for the given HWND."""
    return _PWDesktop(backend="uia").window(handle=hwnd)


def _find_window(class_name: str | None, title_re: str | None, timeout: float):
    """Poll until a matching window appears, then return a pywinauto spec.

    When several windows match (e.g. a stale ``#32770`` left open by an earlier
    test plus a freshly shown dialog), prefer the foreground window — a modal
    dialog grabs the foreground when shown — so we target the active one rather
    than the first one enumerated.
    """
    import win32gui  # type: ignore[import-untyped]

    deadline = time.monotonic() + timeout
    while True:
        matches = [
            hwnd
            for hwnd, cls, title in _enum_visible_windows()
            if (class_name is None or cls == class_name)
            and (title_re is None or re.search(title_re, title, re.IGNORECASE))
        ]
        if matches:
            fg = win32gui.GetForegroundWindow()
            chosen = fg if fg in matches else matches[0]
            return _hwnd_to_spec(chosen)
        if time.monotonic() >= deadline:
            desc = class_name or title_re or "dialog"
            raise TimeoutError(f"Could not find {desc!r} window within {timeout}s")
        time.sleep(0.2)


def _find_dialog_window(timeout: float):
    """Find any open/save dialog (#32770 or IFileDialog).

    Prefers the foreground window among the matches so a freshly opened dialog
    wins over a stale one left behind by an earlier interaction.
    """
    import win32gui  # type: ignore[import-untyped]

    deadline = time.monotonic() + timeout
    while True:
        matches = [
            hwnd
            for hwnd, cls, title in _enum_visible_windows()
            if cls == "#32770" or (title and _DIALOG_TITLE_RE.search(title))
        ]
        if matches:
            fg = win32gui.GetForegroundWindow()
            return _hwnd_to_spec(fg if fg in matches else matches[0])
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Could not find file dialog within {timeout}s")
        time.sleep(0.2)


class FileDialog:
    """Helper for interacting with Windows Open/Save file dialogs."""

    def __init__(self, _window) -> None:
        self._win = _window

    @staticmethod
    def wait_for(timeout: float = 10.0) -> FileDialog:
        """Wait for any open/save dialog to appear and return a FileDialog."""
        return FileDialog(_find_dialog_window(timeout))

    def set_path(self, path: str) -> FileDialog:
        """Type a file path into the filename field and press Enter."""
        from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]

        # Try to find an Edit control named 'File name' or similar
        edit = None
        for ctrl in self._win.children():
            try:
                if ctrl.class_name() in ("Edit", "RichEdit20W", "RICHEDIT50W"):
                    edit = ctrl
                    break
            except Exception:
                continue

        if edit is not None:
            try:
                edit.set_focus()
                edit.set_edit_text(path)
                # Do NOT press Enter here — that would already submit/close the
                # dialog, leaving confirm() with nothing to click (and the closed
                # dialog could be picked up as a stale #32770 by later waits).
                # Submission is the caller's job via confirm().
                return self
            except Exception:
                pass

        # Fallback: Ctrl+L to open address bar then type the path. The address
        # bar requires Enter to commit the typed location.
        self._win.set_focus()
        send_keys("^l")
        time.sleep(0.1)
        send_keys(path, with_spaces=True)
        send_keys("{ENTER}")
        return self

    def _dialog_gone(self) -> bool:
        """True if the underlying dialog window no longer exists/visible."""
        try:
            return not self._win.exists() or not self._win.is_visible()
        except Exception:
            return True

    def _click_by_auto_id(self, auto_id: str) -> bool:
        """Click the dialog control with the given AutomationId; True if clicked.

        Standard #32770 dialogs expose their default buttons by Win32 control id
        (IDOK="1", IDCANCEL="2"). Targeting the id avoids the ambiguity of the
        title-based lookup, where the Win11 file picker has several Buttons named
        "Otwórz" (the DropDown arrows) and the real accept control is a
        SplitButton — so control_type="Button" + title matched the wrong ones.

        The id alone can still be ambiguous (the picker exposes two elements with
        auto_id "1"/"2"), so we pin it to the control type of the actual button —
        a SplitButton (Open with its dropdown) or a plain Button (Save/Cancel).
        """
        for control_type in ("SplitButton", "Button"):
            try:
                self._win.child_window(auto_id=auto_id, control_type=control_type).click_input()
                return True
            except Exception:
                continue
        return False

    def confirm(self) -> None:
        """Click the Open / Save button."""
        # IDOK == "1": the dialog's default accept button (Open/Save/Otwórz/…).
        if self._click_by_auto_id("1"):
            return
        confirm_titles = ("Open", "Save", "Otwórz", "Zapisz", "OK")
        for title in confirm_titles:
            try:
                btn = self._win.child_window(title=title, control_type="Button")
                btn.click_input()
                return
            except Exception:
                continue
        # If the dialog already closed (e.g. submitted via Enter in the address
        # bar fallback), there is nothing left to confirm — treat as success.
        if self._dialog_gone():
            return
        raise RuntimeError("Could not find a confirm button in the file dialog")

    def cancel(self) -> None:
        """Click the Cancel button."""
        # IDCANCEL == "2".
        if self._click_by_auto_id("2"):
            return
        for title in ("Cancel", "Anuluj"):
            try:
                btn = self._win.child_window(title=title, control_type="Button")
                btn.click_input()
                return
            except Exception:
                continue
        if self._dialog_gone():
            return
        raise RuntimeError("Could not find a Cancel button in the file dialog")


class MessageBox:
    """Helper for interacting with Windows MessageBox dialogs."""

    def __init__(self, _window) -> None:
        self._win = _window

    @staticmethod
    def wait_for(timeout: float = 10.0) -> MessageBox:
        """Wait for a MessageBox (#32770) dialog and return a MessageBox."""
        return MessageBox(_find_window("#32770", None, timeout))

    def text(self) -> str:
        """Return the message text from the Static control."""
        for ctrl in self._win.children():
            try:
                if ctrl.class_name() == "Static":
                    t = ctrl.window_text()
                    if t:
                        return t
            except Exception:
                continue
        return ""

    def click_ok(self) -> None:
        """Click the OK button."""
        self.click("OK")

    def click_cancel(self) -> None:
        """Click the Cancel button."""
        self.click("Cancel", "Anuluj")

    def click_yes(self) -> None:
        """Click the Yes button."""
        self.click("Yes", "Tak")

    def click_no(self) -> None:
        """Click the No button."""
        self.click("No", "Nie")

    def click(self, *button_texts: str) -> None:
        """Click a button by its text label.

        Accepts several candidate labels and clicks the first one present, so
        the same call works regardless of the Windows display language
        (e.g. ``"Yes"`` / ``"Tak"``).
        """
        for title in button_texts:
            try:
                btn = self._win.child_window(title=title, control_type="Button")
                btn.click_input()
                return
            except Exception:
                continue
        labels = ", ".join(repr(t) for t in button_texts)
        raise RuntimeError(f"No button matching {labels} found in MessageBox")
