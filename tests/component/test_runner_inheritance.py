"""Real process isolation for concurrent stdio and pre-existing Windows jobs."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import msvcrt
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from textwrap import dedent

import pytest

from dolphin_desktop import _runner

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(40)]


@pytest.mark.parametrize("missing_handle", [0, -1])
def test_runner_without_standard_handles_launches_real_command(tmp_path, missing_handle):
    # Change the Win32 standard handles only in a separate host process, so
    # pytest's console and other concurrent callers are unaffected.
    host = tmp_path / "no_stdio_host.py"
    report = tmp_path / "report.json"
    executed = tmp_path / "executed"
    host.write_text(
        dedent("""\
        import ctypes
        import json
        import sys
        from pathlib import Path
        from dolphin_desktop import _runner as r

        missing, report, executed = sys.argv[1:]
        k = r._kernel32
        k.SetStdHandle.argtypes = [r._wt.DWORD, r._wt.HANDLE]
        k.SetStdHandle.restype = r._wt.BOOL
        for kind in (-10, -11, -12):
            assert k.SetStdHandle(r._wt.DWORD(kind), r._wt.HANDLE(int(missing)))
            assert k.GetStdHandle(r._wt.DWORD(kind)) in (None, r._wt.HANDLE(-1).value)

        create_process = k.CreateProcessW
        observed = {}

        def record_creation(*args):
            startup = args[8]._obj
            observed.update(
                inherit_handles=bool(args[4]), creation_flags=args[5],
                startup_flags=startup.dwFlags, startup_size=startup.cb,
                standard_handles=[startup.hStdInput, startup.hStdOutput, startup.hStdError],
            )
            return create_process(*args)

        k.CreateProcessW = record_creation
        code = f"from pathlib import Path; Path({executed!r}).touch(); raise SystemExit(7)"
        observed['exit_code'] = r.run_hidden([sys.executable, '-c', code])
        Path(report).write_text(json.dumps(observed))
    """)
    )
    result = subprocess.run(
        [sys.executable, str(host), str(missing_handle), str(report), str(executed)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        capture_output=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    observed = json.loads(report.read_text())
    assert observed["exit_code"] == 7
    assert executed.exists()
    assert observed["inherit_handles"] is False
    assert observed["startup_flags"] & _runner._STARTF_USESTDHANDLES == 0
    assert observed["creation_flags"] & _runner._EXTENDED_STARTUPINFO_PRESENT == 0
    assert observed["startup_size"] == ctypes.sizeof(_runner._StartupInfoW)
    assert observed["standard_handles"] == [None, None, None]


def test_parallel_runners_keep_stdio_pipes_private(monkeypatch, tmp_path):
    local = threading.local()
    barrier = threading.Barrier(2)
    create_process = _runner._kernel32.CreateProcessW
    release = tmp_path / "release"
    ready = tmp_path / "ready"
    streams = []
    for _ in range(2):
        stdin_r, stdin_w = os.pipe()
        stdout_r, stdout_w = os.pipe()
        stderr_r, stderr_w = os.pipe()
        for fd in (stdin_r, stdout_w, stderr_w):
            os.set_handle_inheritable(msvcrt.get_osfhandle(fd), True)
        streams.append((stdin_r, stdin_w, stdout_r, stdout_w, stderr_r, stderr_w))

    def create_together(*args):
        # Both callers have prepared their inheritable handles before either launch.
        barrier.wait(timeout=10)
        return create_process(*args)

    monkeypatch.setattr(_runner._kernel32, "CreateProcessW", create_together)
    monkeypatch.setattr(_runner._kernel32, "GetStdHandle", lambda kind: local.stdio[kind.value])

    def run(index):
        stdin_r, _, _, stdout_w, _, stderr_w = streams[index]
        local.stdio = dict(
            zip(
                (0xFFFFFFF6, 0xFFFFFFF5, 0xFFFFFFF4),
                map(msvcrt.get_osfhandle, (stdin_r, stdout_w, stderr_w)),
                strict=True,
            )
        )
        code = (
            "import sys,time; from pathlib import Path; "
            "data=sys.stdin.readline().strip(); "
            "print('out:'+data, flush=True); print('err:'+data, file=sys.stderr, flush=True)\n"
        )
        if index == 1:
            code += (
                f"Path({str(ready)!r}).touch()\n"
                "deadline=time.monotonic()+20\n"
                f"while not Path({str(release)!r}).exists():\n"
                " if time.monotonic()>deadline: raise TimeoutError('release missing')\n"
                " time.sleep(.01)\n"
            )
        try:
            return _runner.run_hidden([sys.executable, "-c", code])
        finally:
            for fd in (stdin_r, stdout_w, stderr_w):
                os.close(fd)

    def read_pipe(fd):
        with os.fdopen(fd, "rb") as stream:
            return stream.read()

    with ThreadPoolExecutor(max_workers=6) as pool:
        runners = [pool.submit(run, index) for index in range(2)]
        readers = [
            (pool.submit(read_pipe, fds[2]), pool.submit(read_pipe, fds[4])) for fds in streams
        ]
        try:
            for index, fds in enumerate(streams):
                os.write(fds[1], f"{index}\n".encode())
                os.close(fds[1])
            deadline = time.monotonic() + 15
            while not ready.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("second command did not become ready")
                time.sleep(0.01)
            assert runners[0].result(timeout=10) == 0
            assert not runners[1].done()
            # EOF must arrive while the second command is still alive. A leaked
            # writer in that process would keep both reads blocked.
            assert readers[0][0].result(timeout=3) == b"out:0\r\n"
            assert readers[0][1].result(timeout=3) == b"err:0\r\n"
        finally:
            release.touch()
        assert runners[1].result(timeout=10) == 0
        assert readers[1][0].result(timeout=3) == b"out:1\r\n"
        assert readers[1][1].result(timeout=3) == b"err:1\r\n"


@pytest.mark.parametrize("ui_restrictions", [False, True])
def test_cli_inside_existing_job(tmp_path, ui_restrictions):
    # The outer process owns the CI-like job. It must be separate from pytest,
    # since a process cannot remove itself from a job after this test.
    executed = tmp_path / "executed"
    job_name = f"DolphinRunnerHost_{uuid.uuid4().hex}"
    script = tmp_path / "job_host.py"
    script.write_text(
        "import ctypes, subprocess, sys\n"
        "from dolphin_desktop import _runner as r\n"
        "k=r._kernel32\n"
        f"job=k.CreateJobObjectW(None,{job_name!r})\n"
        "assert job\n"
        "try:\n"
        + (
            " ui=r._wt.DWORD(2) # JOB_OBJECT_UILIMIT_READCLIPBOARD\n"
            " assert k.SetInformationJobObject(job,4,ctypes.byref(ui),ctypes.sizeof(ui))\n"
            if ui_restrictions
            else ""
        )
        + " assert k.AssignProcessToJobObject(job,k.GetCurrentProcess())\n"
        " member=r._wt.BOOL()\n"
        " assert k.IsProcessInJob(k.GetCurrentProcess(),job,ctypes.byref(member))\n"
        " assert member.value\n"
        " result=subprocess.run(sys.argv[1:],capture_output=True,timeout=20)\n"
        " sys.stdout.buffer.write(result.stdout)\n"
        " sys.stderr.buffer.write(result.stderr)\n"
        " sys.exit(result.returncode)\n"
        "finally: k.CloseHandle(job)\n"
    )
    child_code = (
        "import ctypes; from pathlib import Path; "
        "from dolphin_desktop import _runner as r; k=r._kernel32; "
        "k.OpenJobObjectW.argtypes=[r._wt.DWORD,r._wt.BOOL,r._wt.LPCWSTR]; "
        "k.OpenJobObjectW.restype=r._wt.HANDLE; "
        f"job=k.OpenJobObjectW(4,False,{job_name!r}); assert job; "
        "member=r._wt.BOOL(); "
        "assert k.IsProcessInJob(k.GetCurrentProcess(),job,ctypes.byref(member)); "
        "assert member.value, 'command escaped CI job'; k.CloseHandle(job); "
        f"Path({str(executed)!r}).touch(); print('nested-ok')"
    )
    entrypoint = os.path.join(os.path.dirname(sys.executable), "dolphin-run.exe")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            entrypoint,
            sys.executable,
            "-c",
            child_code,
        ],
        capture_output=True,
        timeout=30,
    )
    # Ancestor UI restrictions do not universally reject nesting. If Windows
    # refuses it, require a diagnostic and proof that command code never ran.
    if ui_restrictions and result.returncode != 0:
        assert b"AssignProcessToJobObject failed" in result.stderr
        assert b"existing Windows Job Object" in result.stderr
        assert not executed.exists()
        return
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert result.stdout.strip() == b"nested-ok"
    assert executed.exists()


def test_no_stdio_does_not_inherit_unrelated_pipe(tmp_path):
    read_fd, write_fd = os.pipe()
    os.set_handle_inheritable(msvcrt.get_osfhandle(write_fd), True)
    release = tmp_path / "release"
    # An inherited writer would prevent EOF until this command is released.
    code = (
        "import time; from pathlib import Path\n"
        "deadline=time.monotonic()+15\n"
        f"while not Path({str(release)!r}).exists():\n"
        " if time.monotonic()>deadline: break\n"
        " time.sleep(.01)\n"
    )
    try:
        _, process = _runner.launch_on_desktop(
            [sys.executable, "-c", code],
            _runner.get_current_desktop_name(),
            inherit_stdio=False,
        )
    except BaseException:
        os.close(read_fd)
        os.close(write_fd)
        raise
    os.close(write_fd)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:

            def read():
                with os.fdopen(read_fd, "rb") as pipe:
                    return pipe.read()

            result = pool.submit(read)
            try:
                assert result.result(timeout=3) == b""
                assert _runner._kernel32.WaitForSingleObject(process, 0) == _runner._WAIT_TIMEOUT
            finally:
                release.touch()
        assert _runner._kernel32.WaitForSingleObject(process, 10000) == 0
        status = wt.DWORD()
        assert _runner._kernel32.GetExitCodeProcess(process, ctypes.byref(status))
        assert status.value == 0
    finally:
        if _runner._kernel32.WaitForSingleObject(process, 0) == _runner._WAIT_TIMEOUT:
            _runner.terminate_process_handle(process)
            _runner._kernel32.WaitForSingleObject(process, 5000)
        _runner.close_process_handle(process)
