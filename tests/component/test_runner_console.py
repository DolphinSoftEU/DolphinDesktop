"""Real Windows console events, installed entry point and venv redirectors."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

import pytest

from dolphin_desktop import _runner

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(55)]


@pytest.mark.parametrize("launcher", ["exe", "python", "nested"])
@pytest.mark.parametrize(
    "mode", ["exit0", "exit7", "exit-with-grandchild", "interrupt", "interrupt-c", "ignore"]
)
def test_runner_preserves_result_and_controls_its_tree(tmp_path, launcher, mode):
    _assert_runner_case(tmp_path, launcher, mode)


@pytest.mark.parametrize("mode", ["exit0", "exit7", "exit-with-grandchild", "interrupt", "ignore"])
def test_missing_notifications_preserve_controlled_exit_code(tmp_path, mode):
    _assert_runner_case(tmp_path, "python-no-notifications", mode)


def _assert_runner_case(tmp_path, launcher, mode):
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


def test_process_creation_is_blocked_before_final_snapshot(monkeypatch, tmp_path):
    """A real actor cannot create a new member during final cleanup."""
    trigger = tmp_path / "spawn"
    report = tmp_path / "late.json"
    original_terminate = _runner._kernel32.TerminateJobObject
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
    witness = []
    executed = tmp_path / "unexpected-child"

    def spawn_then_terminate(job, code):
        if not trigger.exists():
            # The source has already read the snapshot and drained the port.
            trigger.touch()
            deadline = time.monotonic() + 10
            while not report.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("late child did not start")
                time.sleep(0.01)
            child = json.loads(report.read_text())
            assert child["error"] == 1816  # ERROR_NOT_ENOUGH_QUOTA
            assert not executed.exists()
            witness.append(child["error"])
        return original_terminate(job, code)

    interrupted = threading.Event()
    interrupted.set()
    monkeypatch.setattr(_runner, "_console_interrupts", lambda: nullcontext(interrupted))
    monkeypatch.setattr(_runner, "_INTERRUPT_GRACE_SECONDS", 0)
    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", spawn_then_terminate)
    child_code = (
        "import json, subprocess, sys, time; from pathlib import Path; "
        "from tests._runner_console_probe import duplicate_process_handle\n"
        "while not Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        "try:\n"
        " p = subprocess.Popen([sys.executable, '-c', "
        "'import sys,time; from pathlib import Path; Path(sys.argv[1]).touch(); time.sleep(120)', "
        "sys.argv[4]])\n"
        "except OSError as exc: data = {'error': exc.winerror}\n"
        "else: data = {'error': None, 'handle': duplicate_process_handle(p, int(sys.argv[3]))}\n"
        "out = Path(sys.argv[2]); tmp = out.with_suffix('.tmp'); "
        "tmp.write_text(json.dumps(data)); tmp.replace(out); "
        "time.sleep(120)"
    )
    try:
        assert (
            _runner.run_hidden(
                [
                    sys.executable,
                    "-c",
                    child_code,
                    str(trigger),
                    str(report),
                    str(os.getpid()),
                    str(executed),
                ]
            )
            == 130
        )
        child = json.loads(report.read_text())
        assert child["error"] == 1816
        assert witness == [1816]
    finally:
        if report.exists():
            handle = json.loads(report.read_text()).get("handle")
            if handle is not None:
                if kernel32.WaitForSingleObject(handle, 0) == 258:
                    kernel32.TerminateProcess(handle, 1)
                    kernel32.WaitForSingleObject(handle, 5000)
                kernel32.CloseHandle(handle)


def test_finished_helpers_release_handles_while_command_is_still_running(monkeypatch, tmp_path):
    finished = tmp_path / "helpers-finished"
    release = tmp_path / "release-command"
    original_drain = _runner._JobNotifications.drain
    observed = []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.GetProcessHandleCount.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
    kernel32.GetProcessHandleCount.restype = wt.BOOL

    def handle_count():
        count = wt.DWORD()
        assert kernel32.GetProcessHandleCount(kernel32.GetCurrentProcess(), ctypes.byref(count))
        return count.value

    before = handle_count()

    def inspect_running_command(tracker):
        original_drain(tracker)
        if finished.exists() and not observed:
            # Only the command's venv redirector and Python may still be live.
            assert len(tracker.processes) <= 2
            assert handle_count() <= before + 12
            observed.append(len(tracker.processes))
            release.touch()

    monkeypatch.setattr(_runner._JobNotifications, "drain", inspect_running_command)
    code = (
        "import subprocess,sys,time; from pathlib import Path\n"
        "for _ in range(40): subprocess.run([sys.executable, '-c', 'pass'], check=True)\n"
        "Path(sys.argv[1]).touch()\n"
        "deadline = time.monotonic() + 10\n"
        "while not Path(sys.argv[2]).exists():\n"
        " if time.monotonic() >= deadline: raise TimeoutError('observer did not release command')\n"
        " time.sleep(.01)\n"
    )
    try:
        assert _runner.run_hidden([sys.executable, "-c", code, str(finished), str(release)]) == 0
        assert len(observed) == 1
    finally:
        release.touch()


def test_stale_notification_cannot_add_a_real_foreign_process_to_cleanup():
    """Inject the PID of a live process in another job as a stale birth packet."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.PostQueuedCompletionStatus.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    kernel32.PostQueuedCompletionStatus.restype = wt.BOOL
    kernel32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    kernel32.WaitForSingleObject.restype = wt.DWORD
    with _runner._owned_job() as foreign_job:
        pid, handle = _runner.launch_on_desktop(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            _runner.get_current_desktop_name(),
            job_handle=foreign_job,
        )
        try:
            with _runner._owned_job() as target_job:
                tracker = _runner._job_notifications[target_job]
                assert kernel32.PostQueuedCompletionStatus(tracker.port, 6, target_job, pid)
                tracker.drain()
                assert tracker.processes == {}
                assert not tracker.pending()
            # The unrelated process remains alive after the empty job's cleanup.
            assert kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            _runner.close_process_handle(handle)
