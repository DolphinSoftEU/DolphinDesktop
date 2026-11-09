"""Stdlib-only controlled console harness for dolphin-run regression tests."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def duplicate_process_handle(process: subprocess.Popen, parent_pid: int) -> int:
    """Pin the exact process object in the observing parent, before it can exit."""
    import _winapi

    parent = _winapi.OpenProcess(0x0040, False, parent_pid)  # PROCESS_DUP_HANDLE
    try:
        return _winapi.DuplicateHandle(
            _winapi.GetCurrentProcess(),
            int(process._handle),
            parent,
            0,
            False,
            2,  # DUPLICATE_SAME_ACCESS
        )
    finally:
        _winapi.CloseHandle(parent)


def child(folder: Path, mode: str) -> None:
    def interrupt(signum, frame):
        (folder / "interrupt.json").write_text(json.dumps({"signal": signum}))
        # The launcher must keep waiting while application cleanup runs.
        time.sleep(0.25)
        sys.exit(23)

    signal.signal(signal.SIGBREAK, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    if mode in ("ignore", "exit-with-grandchild"):
        if mode == "ignore":
            signal.signal(signal.SIGBREAK, signal.SIG_IGN)
        grandchild = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal, sys, time; from pathlib import Path; "
                "signal.signal(signal.SIGBREAK, signal.SIG_IGN); "
                "Path(sys.argv[1]).touch(); time.sleep(120)",
                str(folder / "grandchild-ready"),
            ]
        )
        (folder / "grandchild.json").write_text(json.dumps({"pid": grandchild.pid}))
        deadline = time.monotonic() + 10
        while not (folder / "grandchild-ready").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("grandchild did not become ready")
            time.sleep(0.05)
        assert grandchild.poll() is None

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentThreadId.restype = wt.DWORD
    user32.GetThreadDesktop.argtypes = [wt.DWORD]
    user32.GetThreadDesktop.restype = wt.HANDLE
    user32.GetUserObjectInformationW.argtypes = [
        wt.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
    ]
    name = ctypes.create_unicode_buffer(256)
    needed = wt.DWORD()
    assert user32.GetUserObjectInformationW(
        user32.GetThreadDesktop(kernel32.GetCurrentThreadId()),
        2,
        name,
        ctypes.sizeof(name),
        ctypes.byref(needed),
    )
    (folder / "ready.json").write_text(
        json.dumps(
            {"pid": os.getpid(), "desktop": name.value, "headless": os.getenv("DOLPHIN_HEADLESS")}
        )
    )
    if mode.startswith("exit"):
        os.write(1, b"O" * (128 * 1024))
        os.write(2, b"E" * (128 * 1024))
        sys.exit(0 if mode == "exit-with-grandchild" else int(mode[4:]))
    while True:
        time.sleep(0.05)


def harness(folder: Path, launcher: str, mode: str) -> None:
    """Run in a private console so group signals cannot affect the test runner."""
    # Ctrl+C must be broadcast to this private console (group ID zero).
    # A handler keeps the harness alive without setting the inheritable ignore
    # flag that SIG_IGN would pass to the command under test.
    signal.signal(signal.SIGINT, lambda signum, frame: None)
    entry = str(Path(sys.executable).with_name("dolphin-run.exe"))
    command = (
        [entry]
        if launcher == "exe"
        else [sys.executable, "-c", "from dolphin_desktop._runner import main; main()"]
    )
    if launcher == "python-no-notifications":
        command = [
            sys.executable,
            "-c",
            "from dolphin_desktop import _runner; "
            "_runner._JobNotifications.drain = lambda self: None; _runner.main()",
        ]
    if launcher == "nested":
        command += [entry]
    command += [sys.executable, __file__, "--child", str(folder), mode]
    with subprocess.Popen(
        command,
        # CREATE_NEW_PROCESS_GROUP disables Ctrl+C for the new group. Test its
        # enabled case using ordinary console inheritance instead.
        creationflags=0 if mode == "interrupt-c" else subprocess.CREATE_NEW_PROCESS_GROUP,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            if not mode.startswith("exit"):
                deadline = time.monotonic() + 15
                while not (folder / "ready.json").exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError("child did not become ready")
                    time.sleep(0.05)
                if mode == "interrupt-c":
                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    kernel32.GenerateConsoleCtrlEvent.argtypes = [wt.DWORD, wt.DWORD]
                    kernel32.GenerateConsoleCtrlEvent.restype = wt.BOOL
                    if not kernel32.GenerateConsoleCtrlEvent(signal.CTRL_C_EVENT, 0):
                        raise ctypes.WinError(ctypes.get_last_error())
                else:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
            stdout, stderr = process.communicate(timeout=20)
            (folder / "stdout.bin").write_bytes(stdout)
            (folder / "stderr.bin").write_bytes(stderr)
            (folder / "result.json").write_text(json.dumps({"returncode": process.returncode}))
        finally:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )


def parallel_controller(folder: Path) -> None:
    """Two threads must both escalate a real ignored console interrupt."""
    from concurrent.futures import ThreadPoolExecutor

    from dolphin_desktop import _runner

    os.environ["DOLPHIN_HEADLESS"] = "0"

    def run(index: int) -> int:
        return _runner.run_hidden(
            [
                sys.executable,
                __file__,
                "--child",
                str(folder / str(index)),
                "ignore",
            ]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (0, 1)))
    (folder / "parallel-result.json").write_text(
        json.dumps(
            {
                "returncodes": results,
                "parent_headless": os.environ.get("DOLPHIN_HEADLESS"),
            }
        )
    )


def parallel_harness(folder: Path) -> None:
    for index in (0, 1):
        (folder / str(index)).mkdir()
    with (
        (folder / "parallel-stdout.bin").open("wb") as stdout,
        (folder / "parallel-stderr.bin").open("wb") as stderr,
    ):
        process = subprocess.Popen(
            [sys.executable, __file__, "--parallel-controller", str(folder)],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            deadline = time.monotonic() + 15
            while not all((folder / str(index) / "ready.json").exists() for index in (0, 1)):
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("parallel commands did not become ready")
                time.sleep(0.05)
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=20)
            if process.returncode != 0:
                raise RuntimeError(f"parallel controller failed with code {process.returncode}")
        finally:
            if process.poll() is None:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                finally:
                    process.wait(timeout=3)


if __name__ == "__main__":
    if sys.argv[1] == "--child":
        child(Path(sys.argv[2]), sys.argv[3])
    elif sys.argv[1] == "--parallel-controller":
        parallel_controller(Path(sys.argv[2]))
    elif sys.argv[1] == "--parallel-harness":
        parallel_harness(Path(sys.argv[2]))
    else:
        harness(Path(sys.argv[1]), sys.argv[2], sys.argv[3])
