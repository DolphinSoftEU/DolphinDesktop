"""Small Win32 AUT used by the component locator tests."""

from __future__ import annotations

import win32api
import win32con
import win32gui

WINDOW_TITLE = "Dolphin Component Window"
WINDOW_CLASS = "DolphinComponentWindow"

ID_OK = 1001
ID_CANCEL = 1002
ID_EDIT = 1003
ID_STATUS = 1004

STATUS_IDLE = "Status: idle"
STATUS_CLICKED = "Status: clicked"
EDIT_INITIAL_TEXT = "initial text"

_controls: dict[int, int] = {}


def _create_control(
    parent: int, class_name: str, text: str, style: int, rect, ctrl_id: int
) -> None:
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
        ctrl_id,
        win32api.GetModuleHandle(None),
        None,
    )
    win32gui.SendMessage(hwnd, win32con.WM_SETFONT, win32gui.GetStockObject(17), 1)
    _controls[ctrl_id] = hwnd


def _set_status(text: str) -> None:
    win32gui.SetWindowText(_controls[ID_STATUS], text)


def _wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    if msg == win32con.WM_COMMAND:
        ctrl_id = wparam & 0xFFFF
        if ctrl_id == ID_OK:
            _set_status(STATUS_CLICKED)
        elif ctrl_id == ID_CANCEL:
            _set_status(STATUS_IDLE)
        return 0
    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def main() -> None:
    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = _wnd_proc
    wc.lpszClassName = WINDOW_CLASS
    wc.hInstance = win32api.GetModuleHandle(None)
    wc.hCursor = win32gui.LoadCursor(0, win32con.IDC_ARROW)
    wc.hbrBackground = win32con.COLOR_BTNFACE + 1
    win32gui.RegisterClass(wc)

    hwnd = win32gui.CreateWindowEx(
        0,
        WINDOW_CLASS,
        WINDOW_TITLE,
        win32con.WS_OVERLAPPEDWINDOW,
        80,
        80,
        420,
        220,
        0,
        0,
        win32api.GetModuleHandle(None),
        None,
    )
    _create_control(hwnd, "BUTTON", "OK", win32con.BS_PUSHBUTTON, (20, 20, 100, 30), ID_OK)
    _create_control(hwnd, "BUTTON", "Cancel", win32con.BS_PUSHBUTTON, (140, 20, 100, 30), ID_CANCEL)
    _create_control(
        hwnd,
        "EDIT",
        EDIT_INITIAL_TEXT,
        win32con.WS_BORDER | win32con.ES_AUTOHSCROLL,
        (20, 70, 300, 26),
        ID_EDIT,
    )
    _create_control(hwnd, "STATIC", STATUS_IDLE, 0, (20, 115, 300, 24), ID_STATUS)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
    win32gui.UpdateWindow(hwnd)
    win32gui.PumpMessages()


if __name__ == "__main__":
    main()
