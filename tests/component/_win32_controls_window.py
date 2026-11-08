"""Classic Win32 AUT for the KAN-747 convenience-control component tests.

Plain ``EDIT`` / ``COMBOBOX`` / ``SysTreeView32`` / ``LISTBOX`` /
``SysTabControl32`` / ``BUTTON`` / ``STATIC`` child windows and a native
``HMENU`` menu bar, i.e. what an MFC or VB6 form exposes to the win32
backend. Every application-visible event (combo, tree, list and tab
selection, menu command, button click) is echoed into the status label so a
test can prove the control really changed state.
"""

from __future__ import annotations

import argparse
import ctypes
import json
from ctypes import wintypes
from pathlib import Path

import win32api
import win32con
import win32gui

WINDOW_TITLE = "Dolphin Win32 Controls"
WINDOW_CLASS = "DolphinWin32ControlsWindow"

ID_EDIT = 2001
ID_COMBO = 2002
ID_TREE = 2003
ID_SAVE = 2004
ID_REMEMBER = 2005
ID_STATUS = 2006
ID_LIST = 2007
ID_TABS = 2008
ID_MENU_MARK = 2101
ID_MENU_DISABLED = 2102

STATUS_READY = "ready"
COMBO_ITEMS = ("Standard", "Premium")
LIST_ITEMS = ("Alpha", "Beta", "Gamma")
TAB_TEXTS = ("General", "&Advanced")

_TVM_INSERTITEMW = 0x1132
_TVM_GETNEXTITEM = 0x110A
_TVM_GETITEMW = 0x113E
_TVGN_CARET = 0x0009
_TVIF_TEXT = 0x0001
_TVI_ROOT = -0x10000
_TVI_LAST = -0xFFFF
_TVS_STYLE = 0x0001 | 0x0002 | 0x0004 | 0x0020  # HASBUTTONS|HASLINES|LINESATROOT|SHOWSELALWAYS
# TVN_SELCHANGEDA / TVN_SELCHANGEDW as the unsigned 32-bit NMHDR.code.
_TVN_SELCHANGED = {-402 & 0xFFFFFFFF, -451 & 0xFFFFFFFF}
_CBN_SELCHANGE = 1
_LBN_SELCHANGE = 1
_LB_GETCURSEL = 0x0188
_LB_GETTEXT = 0x0189
_TCM_GETCURSEL = 0x130B
_TCM_GETITEMW = 0x133C
_TCM_INSERTITEMW = 0x133E
_TCIF_TEXT = 0x0001
_TCN_SELCHANGE = -551 & 0xFFFFFFFF


class _TVITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", wintypes.UINT),
        ("hItem", ctypes.c_ssize_t),
        ("state", wintypes.UINT),
        ("stateMask", wintypes.UINT),
        ("pszText", wintypes.LPWSTR),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("iSelectedImage", ctypes.c_int),
        ("cChildren", ctypes.c_int),
        ("lParam", ctypes.c_ssize_t),
    ]


class _TVINSERTSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hParent", ctypes.c_ssize_t),
        ("hInsertAfter", ctypes.c_ssize_t),
        ("item", _TVITEMW),
    ]


class _TCITEMW(ctypes.Structure):
    _fields_ = [
        ("mask", wintypes.UINT),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("pszText", wintypes.LPWSTR),
        ("cchTextMax", ctypes.c_int),
        ("iImage", ctypes.c_int),
        ("lParam", ctypes.c_ssize_t),
    ]


class _NMHDR(ctypes.Structure):
    _fields_ = [
        ("hwndFrom", wintypes.HWND),
        ("idFrom", ctypes.c_size_t),
        ("code", wintypes.UINT),
    ]


_send_message_w = ctypes.windll.user32.SendMessageW
_send_message_w.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, ctypes.c_void_p]
_send_message_w.restype = ctypes.c_ssize_t

_controls: dict[int, int] = {}


def _create_control(parent: int, class_name: str, text: str, style: int, rect, ctrl_id: int) -> int:
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
    return hwnd


def _set_status(text: str) -> None:
    win32gui.SetWindowText(_controls[ID_STATUS], text)


def _insert_tree_item(tree: int, parent: int, text: str) -> int:
    buffer = ctypes.create_unicode_buffer(text)
    insert = _TVINSERTSTRUCTW()
    insert.hParent = parent
    insert.hInsertAfter = _TVI_LAST
    insert.item.mask = _TVIF_TEXT
    insert.item.pszText = ctypes.cast(buffer, wintypes.LPWSTR)
    insert.item.cchTextMax = len(text) + 1
    return int(_send_message_w(tree, _TVM_INSERTITEMW, 0, ctypes.addressof(insert)))


def _selected_tree_text(tree: int) -> str:
    item_handle = _send_message_w(tree, _TVM_GETNEXTITEM, _TVGN_CARET, None)
    if not item_handle:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    item = _TVITEMW()
    item.mask = _TVIF_TEXT
    item.hItem = item_handle
    item.pszText = ctypes.cast(buffer, wintypes.LPWSTR)
    item.cchTextMax = len(buffer)
    _send_message_w(tree, _TVM_GETITEMW, 0, ctypes.addressof(item))
    return buffer.value


def _insert_tab(tabs: int, index: int, text: str) -> None:
    buffer = ctypes.create_unicode_buffer(text)
    item = _TCITEMW()
    item.mask = _TCIF_TEXT
    item.pszText = ctypes.cast(buffer, wintypes.LPWSTR)
    item.cchTextMax = len(text) + 1
    _send_message_w(tabs, _TCM_INSERTITEMW, index, ctypes.addressof(item))


def _selected_tab_text(tabs: int) -> str:
    index = _send_message_w(tabs, _TCM_GETCURSEL, 0, None)
    if index < 0:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    item = _TCITEMW()
    item.mask = _TCIF_TEXT
    item.pszText = ctypes.cast(buffer, wintypes.LPWSTR)
    item.cchTextMax = len(buffer)
    _send_message_w(tabs, _TCM_GETITEMW, index, ctypes.addressof(item))
    return buffer.value.replace("&", "")


def _selected_list_text(list_box: int) -> str:
    index = _send_message_w(list_box, _LB_GETCURSEL, 0, None)
    if index < 0:
        return ""
    buffer = ctypes.create_unicode_buffer(256)
    _send_message_w(list_box, _LB_GETTEXT, index, ctypes.addressof(buffer))
    return buffer.value


def _wnd_proc(hwnd: int, msg: int, wparam: int, lparam: int) -> int:
    if msg == win32con.WM_COMMAND:
        ctrl_id = wparam & 0xFFFF
        notification = (wparam >> 16) & 0xFFFF
        if ctrl_id == ID_SAVE:
            _set_status("saved=1")
        elif ctrl_id == ID_COMBO and notification == _CBN_SELCHANGE:
            _set_status(f"combo={win32gui.GetWindowText(_controls[ID_COMBO])}")
        elif ctrl_id == ID_LIST and notification == _LBN_SELCHANGE:
            _set_status(f"list={_selected_list_text(_controls[ID_LIST])}")
        elif ctrl_id == ID_MENU_MARK:
            _set_status("menu=mark")
        return 0
    if msg == win32con.WM_NOTIFY:
        header = _NMHDR.from_address(lparam)
        if header.idFrom == ID_TREE and header.code in _TVN_SELCHANGED:
            _set_status(f"tree={_selected_tree_text(_controls[ID_TREE])}")
        elif header.idFrom == ID_TABS and header.code == _TCN_SELCHANGE:
            _set_status(f"tab={_selected_tab_text(_controls[ID_TABS])}")
        return 0
    if msg == win32con.WM_DESTROY:
        win32gui.PostQuitMessage(0)
        return 0
    return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)


def _build_menu(hwnd: int) -> None:
    file_menu = win32gui.CreatePopupMenu()
    win32gui.AppendMenu(file_menu, win32con.MF_STRING, ID_MENU_MARK, "&Mark\tCtrl+M")
    win32gui.AppendMenu(file_menu, win32con.MF_SEPARATOR, 0, "")
    win32gui.AppendMenu(
        file_menu, win32con.MF_STRING | win32con.MF_GRAYED, ID_MENU_DISABLED, "Disabled item"
    )
    menu_bar = win32gui.CreateMenu()
    win32gui.AppendMenu(menu_bar, win32con.MF_POPUP, file_menu, "&File")
    win32gui.SetMenu(hwnd, menu_bar)


def main(ready_file: Path | None = None) -> None:
    win32gui.InitCommonControls()
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
        560,
        420,
        0,
        0,
        win32api.GetModuleHandle(None),
        None,
    )
    _build_menu(hwnd)
    _create_control(
        hwnd, "EDIT", "", win32con.WS_BORDER | win32con.ES_AUTOHSCROLL, (20, 20, 220, 24), ID_EDIT
    )
    combo = _create_control(
        hwnd,
        "COMBOBOX",
        "",
        win32con.CBS_DROPDOWNLIST | win32con.WS_VSCROLL,
        (260, 20, 160, 200),
        ID_COMBO,
    )
    for text in COMBO_ITEMS:
        win32gui.SendMessage(combo, win32con.CB_ADDSTRING, 0, text)
    win32gui.SendMessage(combo, win32con.CB_SETCURSEL, 0, 0)
    tree = _create_control(
        hwnd, "SysTreeView32", "", win32con.WS_BORDER | _TVS_STYLE, (20, 60, 220, 160), ID_TREE
    )
    root = _insert_tree_item(tree, _TVI_ROOT, "Root")
    _insert_tree_item(tree, root, "Child")
    _create_control(hwnd, "BUTTON", "Save", win32con.BS_PUSHBUTTON, (260, 60, 100, 28), ID_SAVE)
    _create_control(
        hwnd, "BUTTON", "Remember", win32con.BS_AUTOCHECKBOX, (260, 100, 140, 24), ID_REMEMBER
    )
    list_box = _create_control(
        hwnd,
        "LISTBOX",
        "",
        win32con.WS_BORDER | win32con.LBS_NOTIFY | win32con.WS_VSCROLL,
        (260, 140, 160, 80),
        ID_LIST,
    )
    for text in LIST_ITEMS:
        win32gui.SendMessage(list_box, win32con.LB_ADDSTRING, 0, text)
    tabs = _create_control(hwnd, "SysTabControl32", "", 0, (20, 240, 300, 60), ID_TABS)
    for index, text in enumerate(TAB_TEXTS):
        _insert_tab(tabs, index, text)
    _create_control(hwnd, "STATIC", STATUS_READY, 0, (20, 320, 400, 24), ID_STATUS)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNORMAL)
    win32gui.UpdateWindow(hwnd)
    if ready_file is not None:
        ready_file.write_text(
            json.dumps({"hwnd": hwnd, "pid": win32api.GetCurrentProcessId()}), encoding="utf-8"
        )
    win32gui.PumpMessages()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", type=Path)
    main(parser.parse_args().ready_file)
