"""Controlled public Desktop.launch owner and Tk AUT for KAN-768."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def creation_time(handle: int) -> int:
    """Read kernel process identity through a retained handle, without PID lookup."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wt.BOOL
    times = [wt.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def publish(path: Path, **report) -> None:
    report["created_at"] = creation_time(-1)  # current-process pseudo handle
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report), encoding="utf-8")
    temporary.replace(path)


def wait_file(path: Path) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(0.02)


def main() -> None:
    mode, folder = sys.argv[1], Path(sys.argv[2])
    if mode == "grandchild":
        publish(folder / "grandchild.json", pid=os.getpid(), parent=os.getppid())
        time.sleep(120)
        return
    if mode == "fixture":
        import tkinter as tk

        root = tk.Tk()
        root.title("KAN-768 " + folder.name)
        root.update()
        # Use the base interpreter to avoid confusing a venv launcher with the
        # actual process. The owner itself still exercises the venv redirector.
        child = subprocess.Popen(
            [sys._base_executable, __file__, "grandchild", str(folder)],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        wait_file(folder / "grandchild.json")
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user.GetThreadDesktop.argtypes = [wt.DWORD]
        user.GetThreadDesktop.restype = wt.HANDLE
        user.GetUserObjectInformationW.argtypes = [
            wt.HANDLE,
            ctypes.c_int,
            wt.LPVOID,
            wt.DWORD,
            ctypes.POINTER(wt.DWORD),
        ]
        name, needed = ctypes.create_unicode_buffer(256), wt.DWORD()
        assert user.GetUserObjectInformationW(
            user.GetThreadDesktop(kernel.GetCurrentThreadId()),
            2,
            name,
            ctypes.sizeof(name),
            ctypes.byref(needed),
        )
        publish(
            folder / "fixture.json",
            pid=os.getpid(),
            parent=os.getppid(),
            desktop=name.value,
            grandchild_pid=child.pid,
        )
        root.mainloop()
        return

    publish(folder / "owner-start.json", pid=os.getpid(), parent=os.getppid())

    from dolphin_desktop import Desktop

    desktop = Desktop(hidden=True)
    interpreter = sys.executable if mode == "owner-hard-kill-venv" else sys._base_executable
    app = desktop.launch(
        subprocess.list2cmdline([interpreter, __file__, "fixture", str(folder)]),
        startup_delay=0,
    )
    wait_file(folder / "fixture.json")
    publish(
        folder / "owner.json",
        pid=os.getpid(),
        parent=os.getppid(),
        launched_pid=app.process_id,
    )
    wait_file(folder / "stop")
    if mode == "normal-close":
        app.close()
        publish(folder / "closed.json", pid=os.getpid())
        wait_file(folder / "exit")
    # normal-exit intentionally omits Application.close(): atexit owns the job.


if __name__ == "__main__":
    main()
