"""Owned native controls and guarded input for recorder component tests."""

from __future__ import annotations

import ast
import queue
import sys
import threading
import time

import pytest

from dolphin_desktop import _recorder

win32api = pytest.importorskip("win32api")
win32con = pytest.importorskip("win32con")
win32gui = pytest.importorskip("win32gui")
win32process = pytest.importorskip("win32process")


def check_real_hook_lifecycle():
    """Install and remove production hooks without sending desktop input."""
    rec = _recorder.Recorder(pid=win32api.GetCurrentProcessId())
    try:
        rec.start()
        assert rec._hook_ready.is_set()
        assert rec._hook_thread.is_alive()
    finally:
        rec.stop()
        if rec._hook_thread is not None:
            rec._hook_thread.join(3)
            assert not rec._hook_thread.is_alive()
    assert rec._hook_error is None


def record_native_transitions():
    """Delay the worker while real hook callbacks capture changing native focus."""
    instance = win32api.GetModuleHandle(None)
    root = win32gui.CreateWindowEx(
        0,
        "Static",
        "Dolphin KAN-576 controlled window",
        win32con.WS_OVERLAPPEDWINDOW,
        100,
        100,
        400,
        220,
        0,
        0,
        instance,
        None,
    )
    controls = [
        win32gui.CreateWindowEx(
            0,
            cls,
            "",
            win32con.WS_CHILD | win32con.WS_VISIBLE | style,
            10,
            10 + index * 45,
            300,
            30,
            root,
            index + 1,
            instance,
            None,
        )
        for index, (cls, style) in enumerate(
            [("Edit", 0), ("Edit", win32con.ES_PASSWORD), ("Button", 0)]
        )
    ]
    pid = win32process.GetWindowThreadProcessId(root)[1]
    rec = _recorder.Recorder(pid=pid)
    hook = threading.Thread(target=rec._hook_loop, daemon=True)
    failures = []
    previous = sys.unraisablehook

    def pump_until(predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while not predicate():
            win32gui.PumpWaitingMessages()
            if time.monotonic() >= deadline:
                raise AssertionError("Timed out waiting for recorder hook events")
            time.sleep(0.005)

    def focus_control(control):
        assert win32gui.GetForegroundWindow() == root, "Owned window lost foreground"
        win32gui.SetFocus(control)
        assert _recorder._native_focus(win32api.GetCurrentThreadId()) == (root, control)
        assert win32process.GetWindowThreadProcessId(root)[1] == pid

    try:
        win32gui.ShowWindow(root, win32con.SW_SHOW)
        try:
            win32gui.SetForegroundWindow(root)
        except win32gui.error as exc:
            pytest.skip(f"Interactive desktop refused activation of owned AUT: {exc}")
        if win32gui.GetForegroundWindow() != root:
            pytest.skip("Interactive desktop did not activate owned AUT")
        focus_control(controls[0])
        assert _recorder._capture_context().password is False
        focus_control(controls[1])
        assert _recorder._capture_context().password is True
        focus_control(controls[2])
        assert _recorder._capture_context().password is None

        sys.unraisablehook = failures.append
        hook.start()
        assert rec._hook_ready.wait(3), "Hooks did not initialize"
        assert rec._hook_error is None
        # No worker exists until all four runs and the final focus change finish.
        for index, (control, letter) in enumerate(
            [(controls[1], "q"), (controls[0], "t"), (controls[1], "s"), (controls[2], "k")]
        ):
            focus_control(control)
            for _ in range(4):
                focus_control(control)  # Guard every injected key, including its PID.
                vk = ord(letter.upper())
                win32api.keybd_event(vk, 0, 0, 0)
                win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            pump_until(lambda count=(index + 1) * 4: rec._evt_queue.qsize() >= count)
        focus_control(controls[0])
        assert rec._processed == 0
        assert rec._evt_queue.qsize() == 16
        rec.stop()
        pump_until(lambda: not hook.is_alive())
        # stop() has posted a sentinel after the captured events.
        rec._process_loop()
        actions = rec.actions()
        code = rec.generate_code()
        ast.parse(code)
        assert not failures, "ctypes callback raised an unraisable exception"
        assert rec._hook_error is None
        return actions, code
    finally:
        if hook.is_alive():
            rec.stop()
            hook.join(3)
        sys.unraisablehook = previous
        win32gui.DestroyWindow(root)
        # A bounded check also prevents an unnoticed leaked global hook thread.
        assert not hook.is_alive()
        while True:
            try:
                rec._evt_queue.get_nowait()
            except queue.Empty:
                break
