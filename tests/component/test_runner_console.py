"""Real Windows console events, installed entry point and venv redirectors."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from dolphin_desktop import _runner

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(55)]


@pytest.mark.parametrize("launcher", ["exe", "python", "nested"])
@pytest.mark.parametrize(
    "mode", ["exit0", "exit7", "exit-with-grandchild", "interrupt", "interrupt-c", "ignore"]
)
def test_runner_preserves_result_and_controls_its_tree(tmp_path, launcher, mode):
    probe = Path(__file__).parents[1] / "_runner_console_probe.py"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0  # SW_HIDE: a private console, without a visible window
    result = subprocess.run(
        [sys.executable, str(probe), str(tmp_path), launcher, mode],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        startupinfo=startup,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    report = json.loads((tmp_path / "result.json").read_text())
    ready = json.loads((tmp_path / "ready.json").read_text())
    assert ready["desktop"] == "DolphinHidden"
    assert ready["headless"] == "1"
    if mode.startswith("exit"):
        assert report["returncode"] == (0 if mode == "exit-with-grandchild" else int(mode[4:]))
        assert (tmp_path / "stdout.bin").read_bytes() == b"O" * (128 * 1024)
        assert (tmp_path / "stderr.bin").read_bytes() == b"E" * (128 * 1024)
    elif mode in ("interrupt", "interrupt-c"):
        assert report["returncode"] == 23
        expected_signal = signal.SIGINT if mode == "interrupt-c" else signal.SIGBREAK
        assert json.loads((tmp_path / "interrupt.json").read_text()) == {"signal": expected_signal}
    else:
        assert report["returncode"] == 130
        assert not (tmp_path / "interrupt.json").exists()
    if mode in ("ignore", "exit-with-grandchild"):
        grandchild = json.loads((tmp_path / "grandchild.json").read_text())
        _assert_exited(grandchild["pid"])
    _assert_exited(ready["pid"])


def _assert_exited(pid: int, timeout_ms: int = 5000) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel32.OpenProcess.restype = wt.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel32.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
    if not handle:
        assert ctypes.get_last_error() == 87  # PID no longer exists
        return
    try:
        assert kernel32.WaitForSingleObject(handle, timeout_ms) == 0
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.parametrize("leave_grandchild", [False, True])
def test_runner_releases_process_job_and_desktop_handles(monkeypatch, tmp_path, leave_grandchild):
    name = f"DolphinRunnerTest_{uuid.uuid4().hex}"
    create_desktop = _runner.create_hidden_desktop
    monkeypatch.setattr(_runner, "DESKTOP_NAME", name)
    monkeypatch.setattr(_runner, "create_hidden_desktop", lambda: create_desktop(name))
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.GetProcessHandleCount.restype = wt.BOOL
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]
    user32.OpenDesktopW.restype = wt.HANDLE
    user32.CloseDesktop.argtypes = [wt.HANDLE]

    def handle_count():
        count = wt.DWORD()
        assert kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count))
        return count.value

    before = handle_count()
    for iteration in range(3):
        report = tmp_path / f"grandchild-{iteration}.json"
        code = (
            "import json, subprocess, sys; from pathlib import Path; "
            "from tests._runner_console_probe import duplicate_process_handle; "
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
            "handle = duplicate_process_handle(p, int(sys.argv[2])); "
            "Path(sys.argv[1]).write_text(json.dumps({'pid': p.pid, 'handle': handle}))"
            if leave_grandchild
            else "pass"
        )
        try:
            assert (
                _runner.run_hidden([sys.executable, "-c", code, str(report), str(os.getpid())]) == 0
            )
            if leave_grandchild:
                # The child duplicates its original handle before exiting.
                # Reopening its PID here could observe an unrelated new process.
                handle = json.loads(report.read_text())["handle"]
                assert kernel32.WaitForSingleObject(handle, 0) == 0
        finally:
            if leave_grandchild and report.exists():
                handle = json.loads(report.read_text())["handle"]
                if kernel32.WaitForSingleObject(handle, 0) == _runner._WAIT_TIMEOUT:
                    kernel32.TerminateProcess(handle, 1)
                    kernel32.WaitForSingleObject(handle, 5000)
                kernel32.CloseHandle(handle)
        desktop = user32.OpenDesktopW(name, 0, False, 1)  # DESKTOP_READOBJECTS
        if desktop:
            user32.CloseDesktop(desktop)
            pytest.fail("runner left its hidden desktop alive")
        assert ctypes.get_last_error() == 2  # ERROR_FILE_NOT_FOUND
    assert handle_count() == before
