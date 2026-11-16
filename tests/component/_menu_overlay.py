"""A controlled, non-activating foreign-process window covering a menu item."""

import json

import win32api
import win32con
import win32gui


class MenuOverlay:
    def __init__(self, rect):
        self.button_events = []
        self.instance = win32api.GetModuleHandle(None)
        self.class_name = f"DolphinMenuOverlay{win32api.GetCurrentProcessId()}"
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = self.instance
        window_class.lpszClassName = self.class_name
        window_class.lpfnWndProc = self._window_proc
        window_class.hbrBackground = win32gui.GetStockObject(win32con.BLACK_BRUSH)
        win32gui.RegisterClass(window_class)
        self.hwnd = 0
        try:
            self.hwnd = win32gui.CreateWindowEx(
                win32con.WS_EX_TOPMOST | win32con.WS_EX_NOACTIVATE,
                self.class_name,
                "Controlled menu input obstruction",
                win32con.WS_POPUP | win32con.WS_VISIBLE,
                rect.left,
                rect.top,
                rect.right - rect.left,
                rect.bottom - rect.top,
                0,
                0,
                self.instance,
                None,
            )
            win32gui.SetWindowPos(
                self.hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )
        except Exception:
            if self.hwnd:
                win32gui.DestroyWindow(self.hwnd)
            win32gui.UnregisterClass(self.class_name, self.instance)
            raise

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message in (
            win32con.WM_LBUTTONDOWN,
            win32con.WM_LBUTTONUP,
            win32con.WM_RBUTTONDOWN,
            win32con.WM_RBUTTONUP,
        ):
            self.button_events.append(message)
        if message == win32con.WM_MOUSEACTIVATE:
            return win32con.MA_NOACTIVATE
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def __enter__(self):
        return self

    def evidence(self, output, **values):
        win32gui.PumpWaitingMessages()
        values.update(
            overlay_hwnd=self.hwnd,
            overlay_pid=win32api.GetCurrentProcessId(),
            button_messages=self.button_events,
        )
        output.write_text(json.dumps(values, indent=2), encoding="utf-8")

    def __exit__(self, *_):
        self.close()

    def close(self):
        win32gui.DestroyWindow(self.hwnd)
        win32gui.UnregisterClass(self.class_name, self.instance)
