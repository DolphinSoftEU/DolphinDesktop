"""A real Win32 window, built from user32 primitives — the AUT for the
real-resolution locator tests.

Run it as a script (``python tests/framework/_real_window.py``) and it puts a
single top-level window titled ``Dolphin Real Window`` on screen, then pumps
messages until the process is killed.

Only ``user32`` built-in control classes are used (BUTTON / EDIT / STATIC), so
nothing but the frame's own window class has to be registered and the AUT has
no third-party dependency. Every control gets a distinct control id through
``CreateWindowEx``'s ``hMenu`` parameter; the UIA HWND provider republishes
that id as the element's ``AutomationId``, which is what lets the tests
address controls the way a real application would be addressed.

Behaviour the tests rely on:

* clicking either **OK** button sets the STATIC to ``Status: clicked``
* clicking **Cancel** sets it back to ``Status: idle``
"""

from __future__ import annotations

import win32api
import win32con
import win32gui

WINDOW_TITLE = "Dolphin Real Window"
WINDOW_CLASS = "DolphinRealWindow"

# Fixed placement keeps the window out of the way and off any other AUT.
WINDOW_X = 80
WINDOW_Y = 80
WINDOW_WIDTH = 460
WINDOW_HEIGHT = 260

# Control ids — passed as hMenu, surfaced by UIA as AutomationId.
ID_OK = 1001
ID_CANCEL = 1002
ID_OK_DUPLICATE = 1003
ID_EDIT = 1004
ID_STATIC = 1005
ID_CHECKBOX = 1006

EDIT_INITIAL_TEXT = "initial text"
STATUS_IDLE = "Status: idle"
STATUS_CLICKED = "Status: clicked"

# Neither constant is exposed by win32con in every pywin32 build.
BS_AUTOCHECKBOX = 0x00000003
DEFAULT_GUI_FONT = 17

_controls: dict[int, int] = {}


def _create_control(parent: int, class_name: str, text: str, style: int, rect, ctrl_id: int) -> int:
    """Create one child control and remember its handle under *ctrl_id*."""
    x, y, width, height = rect
    hwnd = win32gui.CreateWindowEx(
        0,
        class_name,
        text,
        win32con.WS_CHILD | win32con.WS_VISIBLE | style,
        x,
        y,
        width,
        height,
        parent,
        ctrl_id,  # hMenu doubles as the control id for child windows
        win32api.GetModuleHandle(None),
        None,
    )
    # Without this the controls render in the ancient system font; the GUI
    # font is what a real dialog uses and what the tests see on screen.
    gui_font = win32gui.GetStockObject(DEFAULT_GUI_FONT)
    win32gui.SendMessage(hwnd, win32con.WM_SETFONT, gui_font, 1)
    _controls[ctrl_id] = hwnd
    return hwnd


def _build_controls(parent: int) -> None:
    """Populate *parent* with the fixed control set the tests address."""
    _create_control(parent, "BUTTON", "OK", win32con.BS_PUSHBUTTON, (20, 20, 110, 30), ID_OK)
    _create_control(
        parent, "BUTTON", "Cancel", win32con.BS_PUSHBUTTON, (150, 20, 110, 30), ID_CANCEL
    )
    # Deliberate duplicate caption — the ambiguity fixture.
    _create_control(
        parent, "BUTTON", "OK", win32con.BS_PUSHBUTTON, (280, 20, 110, 30), ID_OK_DUPLICATE
    )
    _create_control(
        parent,
        "EDIT",
        EDIT_INITIAL_TEXT,
        win32con.WS_BORDER | win32con.ES_AUTOHSCROLL,
        (20, 70, 370, 26),
        ID_EDIT,
    )
    _create_control(parent, "STATIC", STATUS_IDLE, 0, (20, 110, 370, 24), ID_STATIC)
    _create_control(
        parent, "BUTTON", "Remember me", BS_AUTOCHECKBOX, (20, 145, 200, 26), ID_CHECKBOX
    )


def _set_status(text: str) -> None:
    """Write *text* into the STATIC control."""
    win32gui.SetWindowText(_controls[ID_STATIC], text)


def _on_command(wparam: int) -> None:
    """Route a WM_COMMAND notification from a child control."""
    ctrl_id = wparam & 0xFFFF
    if ctrl_id in (ID_OK, ID_OK_DUPLICATE):
        _set_status(STATUS_CLICKED)
    elif ctrl_id == ID_CANCEL:
        _set_status(STATUS_IDLE)


def _wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    if msg == win32con.WM_COMMAND:
        _on_command(wparam)
        return 0
    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def _register_class() -> str:
    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = _wnd_proc
    wc.lpszClassName = WINDOW_CLASS
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wc.hbrBackground = win32con.COLOR_BTNFACE + 1
    win32gui.RegisterClass(wc)
    return WINDOW_CLASS


def main() -> None:
    class_name = _register_class()
    hwnd = win32gui.CreateWindowEx(
        0,
        class_name,
        WINDOW_TITLE,
        win32con.WS_OVERLAPPEDWINDOW,
        WINDOW_X,
        WINDOW_Y,
        WINDOW_WIDTH,
        WINDOW_HEIGHT,
        0,
        0,
        win32api.GetModuleHandle(None),
        None,
    )
    # Controls are created here rather than from a WM_CREATE branch in
    # _wnd_proc: pywin32 binds a Python window procedure to the handle only
    # once CreateWindowEx has returned, so WM_NCCREATE / WM_CREATE are handled
    # entirely by DefWindowProc and never reach _wnd_proc.
    _build_controls(hwnd)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
    win32gui.UpdateWindow(hwnd)
    win32gui.PumpMessages()


if __name__ == "__main__":
    main()
