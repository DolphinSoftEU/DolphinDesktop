"""Process helpers shared by the destructive live-Outlook suites.

``outlook_exe`` reads the registered OUTLOOK.EXE from its ``LocalServer32``
command line (``parse_local_server32`` handles quoted paths with spaces and
trailing arguments). ``quit_and_confirm_gone`` quits an exclusive Outlook and
*proves* its process is gone. The helper returns only once the
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


def parse_local_server32(command: str) -> str:
    """Extract the executable path from a ``LocalServer32`` command line.

    The value is a command line, not a path: the executable may be quoted
    (``"C:\\Program Files\\...\\OUTLOOK.EXE" /automation``) or bare, and may or
    may not carry arguments. A quoted executable ends at its closing quote; a
    bare one ends right after ``.exe`` (it may itself contain spaces), falling
    back to the first `` /`` switch when no ``.exe`` is present.
    """
    command = command.strip()
    if not command:
        raise ValueError("LocalServer32 is empty")
    if command.startswith('"'):
        end = command.find('"', 1)
        if end <= 1:
            raise ValueError(f"LocalServer32 has an unterminated or empty quoted path: {command!r}")
        return command[1:end]
    lowered = command.lower()
    exe_end = lowered.find(".exe")
    if exe_end != -1:
        return command[: exe_end + len(".exe")]
    return command.split(" /", 1)[0].strip()


def outlook_exe() -> str:
    """The OUTLOOK.EXE path registered as the Outlook.Application COM server."""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID") as key:
        clsid = winreg.QueryValue(key, None)
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as key:
        return parse_local_server32(winreg.QueryValue(key, None))


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
