"""Real owner death, process-tree cleanup and shared-desktop isolation (KAN-768)."""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from tests._hidden_lifecycle_probe import creation_time

from dolphin_desktop import _runner as r

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(90)]


def _read_ready(path: Path, owner: subprocess.Popen) -> dict:
    deadline = time.monotonic() + 30
    while not path.exists():
        assert owner.poll() is None, f"owner exited; inspect {path.parent}"
        assert time.monotonic() < deadline, f"no handshake: {path}"
        time.sleep(0.02)
    return json.loads(path.read_text(encoding="utf-8"))


def _desktop_exists() -> bool:
    user = r._user32
    user.OpenDesktopW.argtypes = [r._wt.LPCWSTR, r._wt.DWORD, r._wt.BOOL, r._wt.DWORD]
    user.OpenDesktopW.restype = r._wt.HANDLE
    handle = user.OpenDesktopW("DolphinHidden", 0, False, 1)
    if handle:
        assert user.CloseDesktop(handle)
        return True
    assert ctypes.get_last_error() == 2, "OpenDesktop failed for a reason other than absence"
    return False


def _retain_process(cleanup: ExitStack, report: dict) -> int:
    pid = report["pid"]
    handle = r._kernel32.OpenProcess(0x101001, False, pid)  # synchronize, terminate, query
    assert handle, f"cannot retain owned process {pid}"
    try:
        if creation_time(handle) != report["created_at"]:
            pytest.fail(f"PID {pid} was reused; refusing to terminate it")
    except BaseException:
        r.close_process_handle(handle)
        raise

    def release():
        # Only known original handles are used, including on test failure. Never
        # reopen a PID during cleanup or enumerate/kill foreign desktop activity.
        try:
            if r._kernel32.WaitForSingleObject(handle, 0) == r._WAIT_TIMEOUT:
                assert r._kernel32.TerminateProcess(handle, 99)
                assert r._kernel32.WaitForSingleObject(handle, 5000) == 0
        finally:
            r.close_process_handle(handle)

    cleanup.callback(release)
    return handle


def _launch_owner(cleanup: ExitStack, folder: Path, mode: str) -> dict:
    folder.mkdir()
    stdout = cleanup.enter_context((folder / "stdout.log").open("wb"))
    stderr = cleanup.enter_context((folder / "stderr.log").open("wb"))
    owner = subprocess.Popen(
        [sys.executable, "-m", "tests._hidden_lifecycle_probe", mode, str(folder)],
        stdout=stdout,
        stderr=stderr,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    def reap_launcher():
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=10)

    cleanup.callback(reap_launcher)
    started = _read_ready(folder / "owner-start.json", owner)
    assert started["pid"] == owner.pid or started["parent"] == owner.pid
    owner_handle = _retain_process(cleanup, started)
    ready = _read_ready(folder / "owner.json", owner)
    # .venv/Scripts/python.exe may be only a launcher. Terminate the owner from
    # its handshake, after verifying it is the launcher or its direct child.
    assert ready["pid"] == owner.pid or ready["parent"] == owner.pid
    assert ready["pid"] == started["pid"] and ready["created_at"] == started["created_at"]
    fixture = _read_ready(folder / "fixture.json", owner)
    grandchild = _read_ready(folder / "grandchild.json", owner)
    assert fixture["desktop"] == "DolphinHidden"
    assert fixture["pid"] == ready["launched_pid"] or fixture["parent"] == ready["launched_pid"]
    assert grandchild["pid"] == fixture["grandchild_pid"]
    assert grandchild["parent"] == fixture["pid"]
    handles = {"owner": owner_handle} | {
        key: _retain_process(cleanup, report)
        for key, report in (
            ("fixture", fixture),
            ("grandchild", grandchild),
        )
    }
    assert all(
        r._kernel32.WaitForSingleObject(handle, 0) == r._WAIT_TIMEOUT for handle in handles.values()
    )
    return {"folder": folder, "launcher": owner, "handles": handles}


@pytest.mark.parametrize(
    "mode", ["normal-close", "owner-hard-kill", "owner-hard-kill-venv", "normal-exit", "two-owners"]
)
def test_hidden_owner_lifecycle(tmp_path, mode):
    # A pre-existing session must never be closed by the test. Record that fact
    # and only require desktop disappearance when this test is its sole owner.
    baseline = _desktop_exists()
    evidence = {"mode": mode, "desktop_preexisting": baseline, "observations": []}
    with ExitStack() as cleanup:
        first = _launch_owner(cleanup, tmp_path / "first", mode)
        second = _launch_owner(cleanup, tmp_path / "second", mode) if mode == "two-owners" else None
        handles = first["handles"]
        try:
            if mode.startswith("owner-hard-kill") or mode == "two-owners":
                assert r._kernel32.TerminateProcess(handles["owner"], 77)
            else:
                (first["folder"] / "stop").touch()
                if mode == "normal-close":
                    _read_ready(first["folder"] / "closed.json", first["launcher"])
                    assert r._kernel32.WaitForSingleObject(handles["fixture"], 15000) == 0
                    assert r._kernel32.WaitForSingleObject(handles["owner"], 0) == r._WAIT_TIMEOUT
                    (first["folder"] / "exit").touch()
            for name, handle in handles.items():
                result = r._kernel32.WaitForSingleObject(handle, 15000)
                evidence["observations"].append({"process": name, "wait": result})
                assert result == 0, f"{name} survived owner exit; {tmp_path}"
            first["launcher"].wait(timeout=10)
            if second:
                assert all(
                    r._kernel32.WaitForSingleObject(h, 0) == r._WAIT_TIMEOUT
                    for h in second["handles"].values()
                )
                assert _desktop_exists(), "second session must retain the shared desktop"
                assert r._kernel32.TerminateProcess(second["handles"]["owner"], 77)
                for handle in second["handles"].values():
                    assert r._kernel32.WaitForSingleObject(handle, 15000) == 0
                second["launcher"].wait(timeout=10)
            if not baseline:
                deadline = time.monotonic() + 15
                while _desktop_exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert not _desktop_exists(), "DolphinHidden survived after owned processes exited"
        finally:
            # Observe before ExitStack performs emergency cleanup, even on failure.
            evidence["before_cleanup"] = {
                name: r._kernel32.WaitForSingleObject(handle, 0) for name, handle in handles.items()
            }
            evidence["desktop_before_cleanup"] = _desktop_exists()
            (tmp_path / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
