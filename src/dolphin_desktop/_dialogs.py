"""Helpers for interacting with common Windows standard dialogs."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path, PureWindowsPath

if sys.platform == "win32":
    from pywinauto import Desktop as _PWDesktop  # type: ignore[import-untyped]
    from pywinauto.findwindows import (
        ElementNotFoundError as _PywinautoElementNotFoundError,
    )
else:
    from ._platform_compat import _unavailable_class

    _PWDesktop = _unavailable_class("Desktop", "pywinauto.Desktop")
    _PywinautoElementNotFoundError = None

from ._helpers import _escape_keys

# Anchored: a caption merely *containing* one of these verbs ("Select your
# plan" in a browser tab) is not a file dialog.
_DIALOG_TITLE_RE = re.compile(
    r"^(Open|Save|Browse|Otwórz|Otwieranie|Zapisz|Zapis|Choose|Select|Pick)\b",
    re.IGNORECASE,
)

_DIALOG_CLASS = "#32770"

# Seconds a single control lookup inside an already-located dialog may take.
# pywinauto's global window_find_timeout is 5 s, so every miss in the candidate
# loops below would otherwise cost five seconds.
_CONTROL_TIMEOUT = 0.5
_DIALOG_CLOSE_TIMEOUT = 1.0


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
    wins over a stale one left behind by an earlier interaction.  A title-only
    match is a last resort: an ordinary application window whose caption starts
    with one of the dialog verbs must never outrank a real ``#32770``.
    """
    import win32gui  # type: ignore[import-untyped]

    deadline = time.monotonic() + timeout
    while True:
        by_class: list[int] = []
        by_title: list[int] = []
        for hwnd, cls, title in _enum_visible_windows():
            if cls == _DIALOG_CLASS:
                by_class.append(hwnd)
            elif title and _DIALOG_TITLE_RE.match(title):
                by_title.append(hwnd)
        # A caption is weak evidence and #32770 is strong, so the class match
        # wins outright. Known limitation: a Win11 XAML picker is not a
        # #32770, so if any unrelated #32770 happens to be open at the same
        # time it is preferred over the picker. Narrowing this needs a class
        # allowlist for the XAML pickers, which is not established here —
        # ranking a browser tab above a real dialog is the worse trade.
        matches = by_class or by_title
        if matches:
            fg = win32gui.GetForegroundWindow()
            return _hwnd_to_spec(fg if fg in matches else matches[0])
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Could not find file dialog within {timeout}s")
        time.sleep(0.2)


def _click_child(window, **criteria) -> bool:
    """Click the dialog control matching *criteria*; False when it is absent.

    ``WindowSpecification.click_input()`` resolves the specification with
    pywinauto's global ``window_find_timeout``, so probing with a short
    ``exists()`` first is what keeps a list of candidate labels from costing
    five seconds per miss.
    """
    try:
        spec = window.child_window(**criteria)
        if not spec.exists(timeout=_CONTROL_TIMEOUT):
            return False
        spec.click_input()
        return True
    except Exception:
        return False


def _split_button_main_area_coords(rect) -> tuple[int, int] | None:
    """Return a point in the main (non-arrow) area of a split button.

    ``Wrapper.click_input(coords=...)`` expects coordinates relative to the
    wrapper.  The right-most part of a Win32/UIA ``SplitButton`` is reserved
    for its drop-down arrow, so a point in the left quarter avoids that hit
    target while remaining independent of the button's screen position.
    """
    try:
        width = int(rect.width())
        height = int(rect.height())
    except Exception:
        return None
    if width < 3 or height < 3:
        return None
    return (max(1, min(width - 1, width // 4)), max(1, min(height - 1, height // 2)))


def _send_enter() -> None:
    """Send Enter through pywinauto's public keyboard API."""
    from pywinauto.keyboard import send_keys  # type: ignore[import-untyped]

    send_keys("{ENTER}")


def _window_handle(window) -> int | None:
    """Return a native HWND without resolving a lazy window specification.

    ``WindowSpecification`` implements magic attribute lookup by resolving its
    wrapper.  That lookup uses pywinauto's global ``window_find_timeout`` and
    must not be part of a bounded dialog-close probe.  Dialogs discovered in
    this module are rebuilt around an HWND, so reading the search criteria is
    sufficient for the normal path; direct attributes support lightweight
    wrappers supplied by callers and tests.
    """
    try:
        criteria = object.__getattribute__(window, "criteria")
    except Exception:
        criteria = ()

    if isinstance(criteria, (list, tuple)):
        for criterion in reversed(criteria):
            if not isinstance(criterion, dict):
                continue
            handle = criterion.get("handle")
            if handle:
                try:
                    return int(handle)
                except (TypeError, ValueError):
                    break

    # Keep this fallback useful for wrappers supplied by callers and tests that
    # already expose the native handle directly.  object.__getattribute__ is
    # intentional: getattr(WindowSpecification, "handle") is magic lookup.
    try:
        handle = object.__getattribute__(window, "handle")
        if handle:
            return int(handle)
    except Exception:
        pass
    return None


def _native_window_state(hwnd: int | None) -> tuple[bool, bool] | None:
    """Return ``(exists, visible)`` for *hwnd* when Win32 can verify it.

    ``UIAWrapper.is_visible()`` can raise ``COMError`` after a native dialog
    has been destroyed while its wrapper still exists.  Win32's HWND state is
    independent of that stale UIA object and is therefore the authority for
    deciding whether the dialog is gone.  ``None`` means that the native state
    could not be queried and must never be interpreted as a successful close.
    """
    if sys.platform != "win32" or hwnd is None:
        return None

    try:
        import win32gui  # type: ignore[import-untyped]

        if not win32gui.IsWindow(hwnd):
            return (False, False)
        return (True, bool(win32gui.IsWindowVisible(hwnd)))
    except Exception:
        return None


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

        # Native Windows file dialogs do not resolve relative paths against the
        # caller's working directory consistently.  Keep absolute paths as
        # supplied, while making relative paths (including ``~``) deterministic
        # before handing them to the dialog.
        path_object = Path(path).expanduser()
        is_absolute = path_object.is_absolute() or PureWindowsPath(path).is_absolute()
        dialog_path = path if is_absolute else str(path_object.resolve())

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
                edit.set_edit_text(dialog_path)
                # Do NOT press Enter here — that would already submit/close the
                # dialog, leaving confirm() with nothing to click (and the closed
                # dialog could be picked up as a stale #32770 by later waits).
                # Submission is the caller's job via confirm().
                return self
            except Exception:
                pass

        # Windows 11 exposes the filename Edit as a descendant of the
        # ``Nazwa pliku:`` ComboBox rather than as a direct dialog child.
        # Prefer its stable Win32 control id before falling back to the
        # address-bar route below.
        try:
            filename_edit = self._win.child_window(
                auto_id="1148",
                control_type="Edit",
            )
            if filename_edit.exists(timeout=_CONTROL_TIMEOUT) is True:
                edit = filename_edit.wrapper_object()
        except Exception:
            edit = None

        if edit is not None:
            try:
                edit.set_focus()
                edit.set_edit_text(dialog_path)
                return self
            except Exception:
                pass

        # Fallback: Ctrl+L to open address bar then type the path. The address
        # bar requires Enter to commit the typed location.
        self._win.set_focus()
        send_keys("^l")
        time.sleep(0.1)
        send_keys(_escape_keys(dialog_path), with_spaces=True)
        send_keys("{ENTER}")
        return self

    def _dialog_gone(self) -> bool:
        """True if the underlying dialog window no longer exists/visible."""
        try:
            wrapper_exists = self._win.exists(timeout=0.0)
        except Exception as exc:
            wrapper_exists = None

            # A pywinauto ElementNotFoundError is a reliable gone signal only
            # when Win32 cannot contradict it.  For COM/runtime failures the
            # HWND check below is mandatory; an unknown exception with a valid,
            # visible HWND means the dialog is still present.
            known_missing = (
                _PywinautoElementNotFoundError is not None
                and isinstance(exc, _PywinautoElementNotFoundError)
            )
        else:
            known_missing = False

        # Do not resolve a lazy WindowSpecification here.  _window_handle()
        # only reads already-bound data, so this probe remains bounded even
        # when ``exists`` reported a missing or unavailable window.
        hwnd = _window_handle(self._win)
        native_state = _native_window_state(hwnd)
        if native_state is not None:
            native_exists, native_visible = native_state
            return not native_exists or not native_visible

        if known_missing:
            return True
        if wrapper_exists is None:
            # This includes COMError and all other unexpected wrapper errors.
            # Without a native confirmation, report failure rather than a
            # false successful close.
            return False
        if not wrapper_exists:
            return True

        # WindowSpecification.is_visible is also a magic lookup.  Use only a
        # directly available method for wrapper doubles; production dialog
        # specifications use the native HWND path above.
        try:
            is_visible = object.__getattribute__(self._win, "is_visible")
        except AttributeError:
            return False
        try:
            return not is_visible()
        except Exception as exc:
            if (
                _PywinautoElementNotFoundError is not None
                and isinstance(exc, _PywinautoElementNotFoundError)
            ):
                return True
            return False

    def _wait_for_dialog_gone(self, timeout: float | None = None) -> bool:
        """Wait briefly until a click has actually closed the dialog."""
        if timeout is None:
            timeout = _DIALOG_CLOSE_TIMEOUT
        deadline = time.monotonic() + timeout
        while True:
            if self._dialog_gone():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(0.05, remaining))

    def _click_by_auto_id(self, auto_id: str) -> bool:
        """Click the dialog control with the given AutomationId; True if clicked.

        Standard #32770 dialogs expose their default buttons by Win32 control id
        (IDOK="1", IDCANCEL="2"). Targeting the id avoids the ambiguity of the
        title-based lookup, where the Win11 file picker has several Buttons named
        "Otwórz" (the DropDown arrows) and the real accept control may be a
        SplitButton — so a localized title alone can match the wrong control.

        The id alone can still be ambiguous (the picker exposes two elements with
        auto_id "1"/"2"), so we pin it to the control type of the actual button.
        A plain Button is tried first because the SplitButton exposed by some
        Windows 11 pickers can accept the input without submitting the dialog.
        Every candidate is required to close the dialog before it is reported as
        successful; this lets us try the next control when a click was consumed
        by the wrong part of the picker.
        """
        for control_type in ("Button", "SplitButton"):
            if not _click_child(
                self._win,
                auto_id=auto_id,
                control_type=control_type,
            ):
                continue
            if self._wait_for_dialog_gone():
                return True
        return False

    def _click_split_button_main_area(self, auto_id: str) -> bool:
        """Physically click the main area of a ``SplitButton`` and verify close.

        On Windows 11 the Open control in the native ``#32770`` picker can be
        exposed as a UIA ``SplitButton`` whose default click targets the arrow
        or otherwise leaves the picker open.  Resolve the uniquely identified
        SplitButton, inspect its wrapper rectangle, and click a relative point
        in the left quarter.  No native child handle is needed for this path.
        """
        try:
            spec = self._win.child_window(
                auto_id=auto_id,
                control_type="SplitButton",
            )
            if not spec.exists(timeout=_CONTROL_TIMEOUT):
                return False
            wrapper = spec.wrapper_object()
            coords = _split_button_main_area_coords(wrapper.rectangle())
            if coords is None:
                return False
            wrapper.click_input(coords=coords)
        except Exception:
            return False
        return self._wait_for_dialog_gone()

    def _press_enter_on_auto_id(self, auto_id: str) -> bool:
        """Submit a focused default button and require the dialog to close.

        Some Windows 11 pickers expose the Open control only as a
        ``SplitButton``.  Its ``click_input`` can be consumed by the split
        button without submitting the picker, while the focused control still
        responds correctly to Enter.  Only send the key after resolving the
        expected default-button candidate, and never treat sending it as a
        success without observing the dialog disappear.
        """
        for control_type in ("SplitButton", "Button"):
            try:
                spec = self._win.child_window(
                    auto_id=auto_id,
                    control_type=control_type,
                )
                if not spec.exists(timeout=_CONTROL_TIMEOUT):
                    continue
                spec.set_focus()
                _send_enter()
            except Exception:
                continue
            if self._wait_for_dialog_gone():
                return True
        return False

    def _click_and_wait_for_close(self, **criteria) -> bool:
        """Click a fallback control and require the dialog to close."""
        return _click_child(self._win, **criteria) and self._wait_for_dialog_gone()

    def _native_click_by_id(self, control_id: int) -> bool:
        """Submit a Win32 dialog control and require the dialog to disappear.

        Some Windows 11 file pickers expose IDOK as a UIA ``SplitButton`` whose
        UIA and physical click paths do not submit the dialog.  A standard
        ``#32770`` dialog still owns the control natively, so first post
        ``BM_CLICK`` to the child returned by ``GetDlgItem`` and then post the
        equivalent ``WM_COMMAND`` (including the standard ``BN_CLICKED``
        notification) to the dialog itself.  ``SendMessage`` is
        deliberately avoided: a synchronous message to a control in a modal
        picker can wait on the picker message loop and deadlock the caller.
        Neither message is considered successful until the original dialog is
        gone.
        """
        if sys.platform != "win32":
            return False

        hwnd = _window_handle(self._win)
        if hwnd is None:
            return False

        try:
            import win32con  # type: ignore[import-untyped]
            import win32gui  # type: ignore[import-untyped]
        except Exception:
            return False

        try:
            button_hwnd = win32gui.GetDlgItem(hwnd, control_id)
        except Exception:
            button_hwnd = 0

        if button_hwnd:
            try:
                posted = win32gui.PostMessage(
                    button_hwnd,
                    win32con.BM_CLICK,
                    0,
                    0,
                )
            except Exception:
                posted = False
            else:
                # PyWin32 versions differ on whether PostMessage returns the
                # native BOOL or None.  A call without an exception is enough
                # to wait for the asynchronously queued message in the latter
                # case; an explicit false result means posting failed.
                posted = posted is None or bool(posted)
                if posted and self._wait_for_dialog_gone():
                    return True

        try:
            notification = getattr(win32con, "BN_CLICKED", 0)
            posted = win32gui.PostMessage(
                hwnd,
                win32con.WM_COMMAND,
                control_id | (notification << 16),
                button_hwnd or 0,
            )
        except Exception:
            return False
        posted = posted is None or bool(posted)
        return posted and self._wait_for_dialog_gone()

    def confirm(self) -> None:
        """Click the Open / Save button."""
        # IDOK == "1": the dialog's default accept button (Open/Save/Otwórz/…).
        if self._click_by_auto_id("1"):
            return
        if self._click_split_button_main_area("1"):
            return
        if self._press_enter_on_auto_id("1"):
            return
        if self._native_click_by_id(1):
            return
        confirm_titles = ("Open", "Save", "Otwórz", "Zapisz", "OK")
        for title in confirm_titles:
            if self._click_and_wait_for_close(title=title, control_type="Button"):
                return
        # If the dialog already closed (e.g. submitted via Enter in the address
        # bar fallback), there is nothing left to confirm — treat as success.
        if self._dialog_gone():
            return
        raise RuntimeError("Could not find a confirm button in the file dialog")

    def cancel(self) -> None:
        # IDCANCEL == "2".
        if self._click_by_auto_id("2"):
            return
        for title in ("Cancel", "Anuluj"):
            if self._click_and_wait_for_close(title=title, control_type="Button"):
                return
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
        self.click("OK")

    def click_cancel(self) -> None:
        self.click("Cancel", "Anuluj")

    def click_yes(self) -> None:
        self.click("Yes", "Tak")

    def click_no(self) -> None:
        self.click("No", "Nie")

    def click(self, *button_texts: str) -> None:
        """Click a button by its text label.

        Accepts several candidate labels and clicks the first one present, so
        the same call works regardless of the Windows display language
        (e.g. ``"Yes"`` / ``"Tak"``).
        """
        for title in button_texts:
            if _click_child(self._win, title=title, control_type="Button"):
                return
        labels = ", ".join(repr(t) for t in button_texts)
        raise RuntimeError(f"No button matching {labels} found in MessageBox")
