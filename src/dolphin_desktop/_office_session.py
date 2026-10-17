"""Opt-in ownership of an exclusive Office process, with no forced termination."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import PureWindowsPath
from typing import Any

from ._application import _process_creation_time_from_handle, _process_image_path


def _office_pids(image: str) -> list[int]:
    """Take a strict snapshot: enumeration errors must never imply no process."""
    return [pid for name, pid in _process_entries() if name.casefold() == image.casefold()]


def _process_entries() -> list[tuple[str, int]]:
    """Read executable names using Toolhelp, without tasklist/WMI/UI message waits."""
    import ctypes
    from ctypes import wintypes as wt

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD),
            ("cntUsage", wt.DWORD),
            ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD),
            ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", wt.LONG),
            ("dwFlags", wt.DWORD),
            ("szExeFile", wt.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
    for method in (kernel.Process32FirstW, kernel.Process32NextW):
        method.argtypes = [wt.HANDLE, ctypes.POINTER(ProcessEntry)]
        method.restype = wt.BOOL
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    entries = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        if not more:
            raise ctypes.WinError(ctypes.get_last_error())
        while more:
            entries.append((str(entry.szExeFile), int(entry.th32ProcessID)))
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
            raise ctypes.WinError(ctypes.get_last_error())
        return entries
    finally:
        kernel.CloseHandle(handle)


def _wait_for_exit(handle: Any, timeout: float) -> bool:
    """Keep the COM apartment responsive while Office tears down its server."""
    import pythoncom
    import win32event

    deadline = time.monotonic() + timeout
    while True:
        remaining = max(0, deadline - time.monotonic())
        result = win32event.WaitForSingleObject(handle, min(50, int(remaining * 1000)))
        if result == 0:
            return True
        if result != 258:
            raise RuntimeError("Cannot wait for the owned Office process handle")
        pythoncom.PumpWaitingMessages()
        if time.monotonic() >= deadline:
            return False


class _OfficeSession:
    """Retain a handle to the original process; PID reuse cannot redirect cleanup."""

    def __init__(self, pid: int, image: str, started: int) -> None:
        import win32api

        self.pid = pid
        self.closed = False
        self._quit_requested = False
        self._handle = win32api.OpenProcess(0x101000, False, pid)  # QUERY_LIMITED | SYNCHRONIZE
        try:
            creation = _process_creation_time_from_handle(self._handle)
            path = _process_image_path(pid)
            if creation is None or creation < started or path is None:
                raise RuntimeError("Cannot verify newly started Office process identity")
            if PureWindowsPath(path).name.casefold() != image.casefold():
                raise RuntimeError("Office process image does not match the expected application")
        except BaseException:
            self._handle.Close()
            raise

    def running(self) -> bool:
        """Query the anchored process; inspection failures never mean it exited."""
        import win32event

        if self.closed:
            return False
        result = win32event.WaitForSingleObject(self._handle, 0)
        if result == 0:
            self._handle.Close()
            self.closed = True
            return False
        if result != 258:  # WAIT_TIMEOUT: original process is still running
            raise RuntimeError("Cannot inspect the owned Office process handle")
        return True

    def quit(
        self,
        com: Any,
        *,
        timeout: float = 10,
        release: Callable[[], None] | None = None,
    ) -> None:
        if not self.running():
            if release is not None:
                release()
            return
        if not self._quit_requested:
            com.Quit()
            self._quit_requested = True
        com = None
        if release is not None:
            release()
        if not _wait_for_exit(self._handle, timeout):
            raise RuntimeError(f"Office process {self.pid} did not exit after Quit; retry cleanup")
        self._handle.Close()
        self.closed = True


def _start_office(
    client: Any,
    progid: str,
    image: str,
    *,
    exclusive: bool,
) -> tuple[Any, _OfficeSession | None]:
    """Start a shared facade or an explicitly isolated UAT session.

    Exclusive mode requires a dedicated test account with no concurrent Office
    launches. A pre-existing or ambiguous process is never claimed or quit.
    """
    if not isinstance(exclusive, bool):
        raise TypeError("exclusive must be a bool")
    if not exclusive:
        return client.Dispatch(progid), None
    if _office_pids(image):
        raise RuntimeError(f"Exclusive session requires no existing {image} process")
    started = time.time_ns() // 100 + 116444736000000000  # Windows FILETIME epoch
    com = client.Dispatch(progid)
    pids = _office_pids(image)
    if len(pids) != 1:
        raise RuntimeError(f"Cannot establish exclusive {image} process ownership")
    return com, _OfficeSession(pids[0], image, started)
