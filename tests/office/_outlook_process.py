"""Quit an exclusive Outlook and *prove* its process is gone.

Shared by the destructive live-Outlook suites. The helper returns only once the
pid has disappeared from a strict process snapshot; a process that survives
both Quit and ``taskkill /F`` is a hard, diagnostic error -- never a silent
success -- because the next exclusive start (or the persisted-draft check) would
otherwise run against a still-living Outlook.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any

_IMAGE = "OUTLOOK.EXE"

# How long a requested Quit may take before the process is force-killed, and
# how long a killed process may take to disappear from the snapshot.
GRACE_SECONDS = 15.0
REAP_SECONDS = 15.0
_POLL_SECONDS = 0.5


def outlook_pids() -> list[int]:
    from dolphin_desktop._office_session import _office_pids

    return _office_pids(_IMAGE)


def _taskkill(pid: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["taskkill", "/F", "/PID", str(pid)], capture_output=True, text=True, check=False
    )


def _describe(result: subprocess.CompletedProcess[str]) -> str:
    parts = (result.stdout, result.stderr)
    output = " ".join(part.strip() for part in parts if part and part.strip())
    return f"taskkill exit code {result.returncode}" + (f": {output}" if output else "")


def _wait_gone(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while pid in outlook_pids():
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_SECONDS)
    return True


def confirm_process_gone(
    pid: int, *, grace: float = GRACE_SECONDS, reap: float = REAP_SECONDS
) -> None:
    """Wait for *pid* to exit; force-kill it after *grace*; raise if it survives.

    A failing ``taskkill`` is only acceptable when the process vanished on its
    own meanwhile (e.g. "process not found" because it just exited). Otherwise
    the failure is reported with taskkill's exit code and output. After a
    successful kill the pid must disappear within *reap* seconds.
    """
    if _wait_gone(pid, grace):
        return
    result = _taskkill(pid)
    if result.returncode != 0 and pid in outlook_pids():
        raise RuntimeError(
            f"{_IMAGE} pid {pid} ignored Quit for {grace:g}s and could not be "
            f"force-killed ({_describe(result)})"
        )
    if not _wait_gone(pid, reap):
        raise RuntimeError(
            f"{_IMAGE} pid {pid} is still running {reap:g}s after a force-kill "
            f"({_describe(result)})"
        )


def quit_and_confirm_gone(
    app: Any,
    *,
    save_changes: bool = False,
    grace: float = GRACE_SECONDS,
    reap: float = REAP_SECONDS,
) -> None:
    """Quit an exclusive Outlook and return only once its process is really gone.

    ``OutlookApp.quit()`` raises "did not exit after Quit; retry cleanup" when
    the process has not died within the session's short wait. An exclusive
    Outlook driven through many create/delete cycles can ignore that Quit for a
    long time, so after *grace* seconds the process is force-killed: it is our
    own disposable UAT process on a throwaway PST, so nothing is lost. Any other
    quit error propagates; a process that cannot be removed raises RuntimeError.
    """
    pid = app.process_id
    try:
        app.quit(save_changes=save_changes)
    except RuntimeError as exc:
        if "retry cleanup" not in str(exc):
            raise
    if pid is None:
        return
    confirm_process_gone(pid, grace=grace, reap=reap)
