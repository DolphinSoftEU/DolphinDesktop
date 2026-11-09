"""Owned native controls and guarded input for recorder component tests."""

from __future__ import annotations

import ast
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path

import pytest

from dolphin_desktop import _recorder

win32api = pytest.importorskip("win32api")
win32con = pytest.importorskip("win32con")
win32gui = pytest.importorskip("win32gui")
win32process = pytest.importorskip("win32process")


def _desktop_unavailable(reason):
    if os.environ.get("DOLPHIN_RECORDER_ACCEPTANCE") == "1":
        pytest.fail(f"Recorder acceptance requires an interactive desktop: {reason}")
    pytest.skip(reason)


def _activate_owned_window(root):
    try:
        win32gui.SetForegroundWindow(root)
    except win32gui.error as exc:
        _desktop_unavailable(f"Interactive desktop refused activation of owned AUT: {exc}")
    if win32gui.GetForegroundWindow() != root:
        _desktop_unavailable("Interactive desktop did not activate owned AUT")


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
    rec._started = True  # This probe starts only the producer to delay the worker.
    hook = threading.Thread(target=rec._hook_loop, daemon=True)
    rec._hook_thread = hook
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
        _activate_owned_window(root)
        # KAN-759: use the unmodified pywin32 result and query the real focus
        # both on its owning thread and on a worker, as the hook does.
        with ThreadPoolExecutor(max_workers=1) as worker:
            for control, password in zip(controls, (False, True, None), strict=True):
                focus_control(control)
                identity = win32process.GetWindowThreadProcessId(root)
                assert tuple(identity) == (win32api.GetCurrentThreadId(), pid)
                contexts = [
                    _recorder._capture_context(),
                    worker.submit(_recorder._capture_context).result(timeout=2),
                ]
                for context in contexts:
                    assert (context.hwnd, context.pid, context.focus) == (root, pid, control)
                    assert context.password is password

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


@contextmanager
def _owned_recorder_window(ready):
    """Keep a process identity handle and close the owned AUT even on test failure."""
    from dolphin_desktop._application import _enumerate_descendant_pids

    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("_real_window.py")),
            "--ready-file",
            str(ready),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    handle = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert process.poll() is None, "Owned AUT exited before becoming ready"
            assert time.monotonic() < deadline, "Owned AUT did not become ready"
            time.sleep(0.01)
        identity = json.loads(ready.read_text(encoding="utf-8"))
        root, pid = identity["hwnd"], identity["pid"]
        assert pid in {process.pid} | _enumerate_descendant_pids(process.pid)
        assert win32process.GetWindowThreadProcessId(root)[1] == pid
        handle = win32api.OpenProcess(0x101001, False, pid)
        yield root, pid
    finally:
        try:
            if handle is not None:
                try:
                    if win32process.GetExitCodeProcess(handle) == win32con.STILL_ACTIVE:
                        win32api.TerminateProcess(handle, 0)
                finally:
                    handle.Close()
        finally:
            try:
                if process.poll() is None:
                    process.terminate()
            finally:
                process.communicate(timeout=5)


def record_process_isolation(directory):
    """Queue real input from two processes and drain with the foreign window active."""
    from ._real_window import ID_EDIT

    with ExitStack() as resources:
        owned = resources.enter_context(_owned_recorder_window(directory / "owned.ready.json"))
        foreign = resources.enter_context(_owned_recorder_window(directory / "foreign.ready.json"))
        assert owned[0] != foreign[0]
        assert owned[1] != foreign[1]
        for (root, _pid), title in ((owned, "KAN-576 owned"), (foreign, "KAN-576 foreign")):
            win32gui.SetWindowText(root, title)
            win32gui.SendMessage(win32gui.GetDlgItem(root, ID_EDIT), win32con.WM_SETTEXT, 0, "")

        rec = _recorder.Recorder(pid=owned[1])
        rec._started = True  # Start only the hook so every event stays queued.
        hook = threading.Thread(target=rec._hook_loop, daemon=True)
        rec._hook_thread = hook
        failures = []
        previous = sys.unraisablehook
        sys.unraisablehook = failures.append
        resources.callback(setattr, sys, "unraisablehook", previous)
        resources.callback(rec.stop)
        hook.start()
        assert rec._hook_ready.wait(3), "Hooks did not initialize"
        assert rec._hook_error is None

        def wait_until(predicate):
            deadline = time.monotonic() + 2
            while not predicate():
                assert time.monotonic() < deadline, "Timed out waiting for focus or queued events"
                time.sleep(0.005)

        def key_events():
            with rec._evt_queue.mutex:
                return [event for event in rec._evt_queue.queue if event and event[0] == "key"]

        expected = []
        for (root, pid), letter in ((owned, "t"), (foreign, "q"), (owned, "s"), (foreign, "z")):
            edit = win32gui.GetDlgItem(root, ID_EDIT)
            _activate_owned_window(root)
            left, top, right, bottom = win32gui.GetWindowRect(edit)
            point = ((left + right) // 2, (top + bottom) // 2)
            win32api.SetCursorPos(point)
            assert win32gui.GetForegroundWindow() == root
            assert win32process.GetWindowThreadProcessId(root)[1] == pid
            assert win32gui.WindowFromPoint(point) == edit, "Owned Edit is obscured"
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            wait_until(lambda edit=edit: _recorder._capture_context().focus == edit)
            for _ in range(4):
                context = _recorder._capture_context()
                assert win32gui.GetForegroundWindow() == root, "Owned AUT lost foreground"
                assert (context.hwnd, context.pid, context.focus) == (root, pid, edit)
                assert context.password is False
                vk = ord(letter.upper())
                win32api.keybd_event(vk, 0, 0, 0)
                win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
                expected.append((vk, root, pid, edit))
            wait_until(lambda: len(key_events()) >= len(expected))

        # Prove that both processes were captured, in order, before any interpretation.
        assert rec._processed == 0
        assert [
            (event[1], event[-1].hwnd, event[-1].pid, event[-1].focus) for event in key_events()
        ] == expected
        assert _recorder._capture_context().pid == foreign[1]
        rec.stop()
        assert not hook.is_alive()
        rec._process_loop()
        actions, code = rec.actions(), rec.generate_code()
        ast.parse(code)
        assert rec._evt_queue.empty()
        assert not failures, "ctypes callback raised an unraisable exception"
        assert rec._hook_error is None
        return actions, code


def record_generate_replay(output):
    """Record real input and execute the unmodified generated script on an owned AUT."""
    from dolphin_desktop import Desktop

    from ._real_window import ID_EDIT, ID_STATUS, STATUS_IDLE, WINDOW_CLASS

    ready = Path(output).with_suffix(".ready.json")
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("_real_window.py")),
            "--ready-file",
            str(ready),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    rec = None
    process_handle = None
    app = None

    def wait_until(predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert process.poll() is None, "Owned AUT exited unexpectedly"
            assert time.monotonic() < deadline, "Timed out waiting for owned AUT/recorder"
            time.sleep(0.01)

    try:
        wait_until(ready.exists)
        identity = json.loads(ready.read_text(encoding="utf-8"))
        root, pid = identity["hwnd"], identity["pid"]
        # Windows venv redirectors may put the AUT in a child of Popen.pid.
        from dolphin_desktop._application import _enumerate_descendant_pids

        assert pid in {process.pid} | _enumerate_descendant_pids(process.pid)
        assert win32process.GetWindowThreadProcessId(root)[1] == pid
        # Keep an identity anchor with SYNCHRONIZE | QUERY_LIMITED_INFORMATION | TERMINATE.
        process_handle = win32api.OpenProcess(0x101001, False, pid)
        rec = _recorder.Recorder(pid=pid)
        edit = win32gui.GetDlgItem(root, ID_EDIT)
        status = win32gui.GetDlgItem(root, ID_STATUS)
        _activate_owned_window(root)
        app = Desktop().connect(process=pid)
        window = app.window(class_name=WINDOW_CLASS)
        assert window._get_spec().wrapper_object().handle == root
        field = window.get_by_automation_id(str(ID_EDIT))
        status_field = window.get_by_automation_id(str(ID_STATUS))
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, "")
        left, top, right, bottom = win32gui.GetWindowRect(edit)
        point = ((left + right) // 2, (top + bottom) // 2)

        def guard():
            assert win32gui.GetForegroundWindow() == root, "Owned AUT lost foreground"
            assert win32process.GetWindowThreadProcessId(root)[1] == pid
            assert win32gui.WindowFromPoint(point) == edit, "Owned Edit is obscured"

        guard()
        rec.start()
        win32api.SetCursorPos(point)
        guard()
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        wait_until(lambda: rec._processed >= 1)
        assert _recorder._capture_context().focus == edit
        assert _recorder._capture_context().password is False
        for _ in range(4):
            guard()
            assert _recorder._capture_context().focus == edit
            win32api.keybd_event(ord("T"), 0, 0, 0)
            win32api.keybd_event(ord("T"), 0, win32con.KEYEVENTF_KEYUP, 0)
        wait_until(lambda: rec._processed >= 5)
        wait_until(lambda: field.text() == "tttt")
        rec.stop()
        assert rec.wait(2)
        rec._hook_thread.join(2)
        assert not rec._hook_thread.is_alive()

        actions = rec.actions()
        assert [action.kind for action in actions] == ["click", "type_text"]
        typed = actions[-1]
        assert typed.data == "tttt"
        assert typed.selector, "Recording must produce a usable field selector"
        locator = window.locator(**typed.selector)
        assert locator._resolve().handle == edit
        code = rec.generate_code(output)
        assert "TODO" not in code
        namespace = {}
        exec(compile(Path(output).read_text(encoding="utf-8"), str(output), "exec"), namespace)
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, "")
        assert field.text() == ""
        guard()
        namespace["test_recorded"](process_id=pid)
        wait_until(lambda: field.text() == "tttt")
        assert status_field._resolve().handle == status
        assert status_field.text() == STATUS_IDLE
    finally:
        try:
            if rec is not None:
                try:
                    rec.stop()
                finally:
                    if rec._hook_thread is not None:
                        rec._hook_thread.join(3)
                        assert not rec._hook_thread.is_alive()
        finally:
            try:
                if app is not None:
                    app.detach()
            finally:
                try:
                    if process_handle is not None:
                        try:
                            if (
                                win32process.GetExitCodeProcess(process_handle)
                                == win32con.STILL_ACTIVE
                            ):
                                win32api.TerminateProcess(process_handle, 0)
                        finally:
                            process_handle.Close()
                finally:
                    try:
                        if process.poll() is None:
                            process.terminate()
                    finally:
                        process.communicate(timeout=5)
