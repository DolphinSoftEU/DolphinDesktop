"""dolphin-run headless CI launcher via CreateDesktopW.

Creates a hidden Windows desktop in the current user session and re-launches
the given command on that desktop.  Sets ``DOLPHIN_HEADLESS=1`` in the child
environment so the dolphin library and pytest plugin activate headless behaviour
automatically.

Usage::

    dolphin-run pytest tests/
    dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia

Architecture note: ``IUIAutomation.GetRootElement()`` sees only the current
thread's desktop.  Therefore the *entire* test process (pytest + dolphin) must
run on ``DolphinHidden`` — which is exactly what ``dolphin-run`` arranges.
``subprocess.STARTUPINFO.lpDesktop`` is not forwarded to Win32 ``CreateProcess``
by CPython; a ctypes struct is required instead.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import math
import os
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import Any

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ctypes' default restype is c_int (32-bit), which silently truncates
# pointer-sized handles on x64. Every Win32 function called below has
# to declare its argtypes / restype so that HANDLE / HWINSTA / HDESK
# values survive the transition to Python. Without these declarations
# a hidden-desktop handle with any bit above 32 set would be truncated
# and passed to CloseDesktop as a garbage value — sporadically leaking
# desktops on 64-bit CPython.
_wt = ctypes.wintypes

_user32.CreateDesktopW.argtypes = [
    _wt.LPCWSTR,
    _wt.LPCWSTR,
    ctypes.c_void_p,
    _wt.DWORD,
    _wt.DWORD,
    ctypes.c_void_p,
]
_user32.CreateDesktopW.restype = _wt.HANDLE

_user32.CloseDesktop.argtypes = [_wt.HANDLE]
_user32.CloseDesktop.restype = _wt.BOOL

_user32.SetThreadDesktop.argtypes = [_wt.HANDLE]
_user32.SetThreadDesktop.restype = _wt.BOOL

_user32.GetThreadDesktop.argtypes = [_wt.DWORD]
_user32.GetThreadDesktop.restype = _wt.HANDLE

_user32.GetProcessWindowStation.argtypes = []
_user32.GetProcessWindowStation.restype = _wt.HANDLE

_user32.GetUserObjectInformationW.argtypes = [
    _wt.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    _wt.DWORD,
    ctypes.POINTER(_wt.DWORD),
]
_user32.GetUserObjectInformationW.restype = _wt.BOOL

_user32.WaitForInputIdle.argtypes = [_wt.HANDLE, _wt.DWORD]
_user32.WaitForInputIdle.restype = _wt.DWORD

_kernel32.GetStdHandle.argtypes = [_wt.DWORD]
_kernel32.GetStdHandle.restype = _wt.HANDLE

_kernel32.CreateFileW.argtypes = [
    _wt.LPCWSTR,
    _wt.DWORD,
    _wt.DWORD,
    ctypes.c_void_p,
    _wt.DWORD,
    _wt.DWORD,
    _wt.HANDLE,
]
_kernel32.CreateFileW.restype = _wt.HANDLE

_kernel32.GetCurrentProcess.argtypes = []
_kernel32.GetCurrentProcess.restype = _wt.HANDLE
_kernel32.DuplicateHandle.argtypes = [
    _wt.HANDLE,
    _wt.HANDLE,
    _wt.HANDLE,
    ctypes.POINTER(_wt.HANDLE),
    _wt.DWORD,
    _wt.BOOL,
    _wt.DWORD,
]
_kernel32.DuplicateHandle.restype = _wt.BOOL
_kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p,
    _wt.DWORD,
    _wt.DWORD,
    ctypes.POINTER(ctypes.c_size_t),
]
_kernel32.InitializeProcThreadAttributeList.restype = _wt.BOOL
_kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p,
    _wt.DWORD,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_kernel32.UpdateProcThreadAttribute.restype = _wt.BOOL
_kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
_kernel32.DeleteProcThreadAttributeList.restype = None

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = _wt.DWORD

_kernel32.CreateProcessW.argtypes = [
    _wt.LPCWSTR,
    _wt.LPWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
    _wt.BOOL,
    _wt.DWORD,
    ctypes.c_void_p,
    _wt.LPCWSTR,
    ctypes.c_void_p,
    ctypes.c_void_p,
]
_kernel32.CreateProcessW.restype = _wt.BOOL

_kernel32.CloseHandle.argtypes = [_wt.HANDLE]
_kernel32.CloseHandle.restype = _wt.BOOL

_kernel32.TerminateProcess.argtypes = [_wt.HANDLE, _wt.UINT]
_kernel32.TerminateProcess.restype = _wt.BOOL

_kernel32.WaitForSingleObject.argtypes = [_wt.HANDLE, _wt.DWORD]
_kernel32.WaitForSingleObject.restype = _wt.DWORD

_kernel32.GetExitCodeProcess.argtypes = [_wt.HANDLE, ctypes.POINTER(_wt.DWORD)]
_kernel32.GetExitCodeProcess.restype = _wt.BOOL

_ConsoleHandler = ctypes.WINFUNCTYPE(_wt.BOOL, _wt.DWORD)
_kernel32.SetConsoleCtrlHandler.argtypes = [_ConsoleHandler, _wt.BOOL]
_kernel32.SetConsoleCtrlHandler.restype = _wt.BOOL

_kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, _wt.LPCWSTR]
_kernel32.CreateJobObjectW.restype = _wt.HANDLE
_kernel32.SetInformationJobObject.argtypes = [_wt.HANDLE, ctypes.c_int, ctypes.c_void_p, _wt.DWORD]
_kernel32.SetInformationJobObject.restype = _wt.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [_wt.HANDLE, _wt.HANDLE]
_kernel32.AssignProcessToJobObject.restype = _wt.BOOL
_kernel32.TerminateJobObject.argtypes = [_wt.HANDLE, _wt.UINT]
_kernel32.TerminateJobObject.restype = _wt.BOOL
_kernel32.QueryInformationJobObject.argtypes = [
    _wt.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    _wt.DWORD,
    ctypes.POINTER(_wt.DWORD),
]
_kernel32.QueryInformationJobObject.restype = _wt.BOOL
_kernel32.OpenProcess.argtypes = [_wt.DWORD, _wt.BOOL, _wt.DWORD]
_kernel32.OpenProcess.restype = _wt.HANDLE
_kernel32.IsProcessInJob.argtypes = [_wt.HANDLE, _wt.HANDLE, ctypes.POINTER(_wt.BOOL)]
_kernel32.IsProcessInJob.restype = _wt.BOOL
_kernel32.ResumeThread.argtypes = [_wt.HANDLE]
_kernel32.ResumeThread.restype = _wt.DWORD
_kernel32.CreateIoCompletionPort.argtypes = [_wt.HANDLE, _wt.HANDLE, ctypes.c_size_t, _wt.DWORD]
_kernel32.CreateIoCompletionPort.restype = _wt.HANDLE
_kernel32.GetQueuedCompletionStatus.argtypes = [
    _wt.HANDLE,
    ctypes.POINTER(_wt.DWORD),
    ctypes.POINTER(ctypes.c_size_t),
    ctypes.POINTER(ctypes.c_void_p),
    _wt.DWORD,
]
_kernel32.GetQueuedCompletionStatus.restype = _wt.BOOL

DESKTOP_NAME = "DolphinHidden"
_DESKTOP_ACCESS = 0x10000000  # GENERIC_ALL

_WAIT_OBJECT_0 = 0x00000000
_WAIT_TIMEOUT = 0x00000102
_WAIT_FAILED = 0xFFFFFFFF
_STARTF_USESTDHANDLES = 0x00000100
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_UOI_FLAGS = 1
_WSF_VISIBLE = 0x0001
_CREATE_SUSPENDED = 0x00000004
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_INTERRUPT_GRACE_SECONDS = 10.0
_PROCESS_TERMINATION_TIMEOUT_SECONDS = 10.0
_JOB_CLEANUP_TIMEOUT_SECONDS = 10.0


class _JobBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", _wt.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", _wt.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", _wt.DWORD),
        ("SchedulingClass", _wt.DWORD),
    ]


class _JobExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobBasicLimitInformation),
        ("IoInfo", ctypes.c_ulonglong * 6),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JobBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", _wt.DWORD),
        ("TotalProcesses", _wt.DWORD),
        ("ActiveProcesses", _wt.DWORD),
        ("TotalTerminatedProcesses", _wt.DWORD),
    ]


class _JobCompletionPort(ctypes.Structure):
    _fields_ = [("CompletionKey", ctypes.c_void_p), ("CompletionPort", _wt.HANDLE)]


class _JobNotifications:
    """Track live job members from advisory NEW_PROCESS notifications.

    Associate while the job is empty, before its suspended root is assigned.
    Notifications are advisory. Cleanup seals process admission and takes a
    complete snapshot independently, so lost historical notifications do not
    invalidate an otherwise successful command.
    """

    def __init__(self, job: int) -> None:
        self.job = job
        self.processes: dict[int, int] = {}
        self.port = _kernel32.CreateIoCompletionPort(_wt.HANDLE(-1), None, 0, 1)
        if not self.port:
            raise ctypes.WinError(ctypes.get_last_error())
        association = _JobCompletionPort(job, self.port)
        if not _kernel32.SetInformationJobObject(
            job, 7, ctypes.byref(association), ctypes.sizeof(association)
        ):
            error = ctypes.get_last_error()
            _kernel32.CloseHandle(self.port)
            raise ctypes.WinError(error)

    def drain(self) -> None:
        self.pending()
        # A producer cannot starve the command wait or its deadlines by flooding
        # the port. A subsequent poll drains the remaining packets.
        try:
            for _ in range(256):
                message = _wt.DWORD()
                key = ctypes.c_size_t()
                pid = ctypes.c_void_p()
                if not _kernel32.GetQueuedCompletionStatus(
                    self.port, ctypes.byref(message), ctypes.byref(key), ctypes.byref(pid), 0
                ):
                    # Both an empty queue and a monitoring failure fall back to
                    # the sealed, authoritative snapshot during final cleanup.
                    return
                if key.value != self.job or message.value != 6 or not pid.value:
                    continue  # unrelated, malformed or non-birth notification
                if pid.value in self.processes:
                    continue
                # Membership must be checked on the opened handle: the PID in
                # an advisory packet may already belong to a foreign process.
                handle = _kernel32.OpenProcess(0x101000, False, pid.value)
                if not handle:
                    continue
                member = _wt.BOOL()
                if (
                    not _kernel32.IsProcessInJob(handle, self.job, ctypes.byref(member))
                    or not member.value
                ):
                    _kernel32.CloseHandle(handle)
                    continue
                self.processes[pid.value] = handle
        finally:
            self.pending()

    def pending(self) -> bool:
        """Keep only live, verified members; release completed process identities.

        An open handle pins its PID until removal. Later packets for a reused
        PID must open a new handle and pass membership validation again. Errors
        in this advisory cache never substitute for authoritative cleanup errors.
        """
        for pid, handle in list(self.processes.items()):
            if _kernel32.WaitForSingleObject(handle, 0) != _WAIT_TIMEOUT:
                del self.processes[pid]
                _kernel32.CloseHandle(handle)
        return bool(self.processes)

    def close(self) -> None:
        for handle in self.processes.values():
            _kernel32.CloseHandle(handle)
        self.processes.clear()
        _kernel32.CloseHandle(self.port)


_job_notifications: dict[int, _JobNotifications] = {}


@contextmanager
def _job_process_handles(job: int) -> Iterator[list[int]]:
    """Retain process identities before termination removes them from the job."""
    handles: list[int] = []
    try:
        capacity = 16
        for _ in range(4):
            buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * capacity)
            complete = _kernel32.QueryInformationJobObject(
                job, 3, buffer, ctypes.sizeof(buffer), None
            )
            header = ctypes.cast(buffer, ctypes.POINTER(_wt.DWORD))
            assigned, listed = header[0], header[1]
            if complete and listed == assigned and listed <= capacity:
                break
            if not complete:
                error = ctypes.get_last_error()
                if error != 234:  # ERROR_MORE_DATA: the job grew during enumeration
                    raise ctypes.WinError(error)
            capacity = max(capacity * 2, assigned)
        else:
            raise OSError("command job process list remained incomplete during cleanup")
        count = ctypes.cast(buffer, ctypes.POINTER(_wt.DWORD))[1]
        pids = (ctypes.c_size_t * count).from_buffer(buffer, 8)
        for pid in pids:
            # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION (for IsProcessInJob)
            handle = _kernel32.OpenProcess(0x101000, False, pid)
            if not handle:
                error = ctypes.get_last_error()
                if error == 87:  # ERROR_INVALID_PARAMETER: already exited
                    continue
                raise ctypes.WinError(error)
            handles.append(handle)
            member = _wt.BOOL()
            if not _kernel32.IsProcessInJob(handle, job, ctypes.byref(member)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not member.value:
                # The PID was reused between enumeration and OpenProcess.
                handles.pop()
                _kernel32.CloseHandle(handle)
        yield handles
    finally:
        for handle in handles:
            _kernel32.CloseHandle(handle)


def _seal_job(job: int) -> None:
    """Reject new process associations before capturing the final process set.

    Existing members remain alive until explicitly terminated. A zero active
    process limit also applies to inherited associations in nested jobs. Keep
    the job's other limits, including KILL_ON_JOB_CLOSE, unchanged.
    """
    limits = _JobExtendedLimitInformation()
    if not _kernel32.QueryInformationJobObject(
        job, 9, ctypes.byref(limits), ctypes.sizeof(limits), None
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    limits.BasicLimitInformation.LimitFlags |= 0x00000008  # JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    limits.BasicLimitInformation.ActiveProcessLimit = 0
    if not _kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        raise ctypes.WinError(ctypes.get_last_error())


def _terminate_and_wait_for_job(job: int) -> None:
    """Drain the job before releasing its handle and the command's desktop.

    TerminateJobObject, like TerminateProcess, can return before processes exit.
    Retain process handles before terminating and require them to be signalled,
    as well as a zero active count: job accounting can reach zero before handles
    become signalled. A stuck driver must produce a cleanup error rather than
    an indefinite launcher hang.
    """
    # Sealing first removes the snapshot-to-termination birth race without
    # relying on delivery of NEW_PROCESS notifications.
    _seal_job(job)
    with _job_process_handles(job) as handles:
        notifications = _job_notifications.get(job)
        if notifications is not None:
            notifications.drain()
        if not _kernel32.TerminateJobObject(job, 130):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + _JOB_CLEANUP_TIMEOUT_SECONDS
        accounting = _JobBasicAccountingInformation()
        while True:
            if notifications is not None:
                notifications.drain()
            stopped = notifications is None or not notifications.pending()
            for handle in handles:
                result = _kernel32.WaitForSingleObject(handle, 0)
                if result not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
                    raise ctypes.WinError(ctypes.get_last_error())
                stopped &= result == _WAIT_OBJECT_0
            if not _kernel32.QueryInformationJobObject(
                job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if accounting.ActiveProcesses == 0 and stopped:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("command job still has pending processes after cleanup timeout")
            time.sleep(0.01)


@contextmanager
def _owned_job() -> Iterator[int]:
    """Own the entire command tree, including venv redirectors and their children."""
    job = _kernel32.CreateJobObjectW(None, None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        limits = _JobExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            notifications = _JobNotifications(job)
        except OSError:
            notifications = None  # final sealing/snapshot remains authoritative
        if notifications is not None:
            _job_notifications[job] = notifications
        try:
            try:
                yield job
            finally:
                _terminate_and_wait_for_job(job)
        finally:
            if notifications is not None:
                del _job_notifications[job]
                notifications.close()
    finally:
        _kernel32.CloseHandle(job)


_console_registration_lock = threading.Lock()
_console_subscribers: tuple[threading.Event, ...] = ()
_console_handler_registered = False


@_ConsoleHandler
def _console_control_handler(event: int) -> bool:
    if event not in (0, 1):  # CTRL_C_EVENT, CTRL_BREAK_EVENT
        return False
    # Read an immutable snapshot without taking the registration lock. Windows
    # may call us while another thread is inside SetConsoleCtrlHandler.
    subscribers = _console_subscribers
    for interrupted in subscribers:
        interrupted.set()
    return bool(subscribers)


@contextmanager
def _console_interrupts() -> Iterator[threading.Event]:
    """Let the command handle console interrupts while its launcher waits.

    CreateProcess inherits our console, process group and Ctrl+C ignore setting.
    Windows delivers enabled events to the command tree itself; generating another
    event here would deliver it twice. CREATE_NEW_PROCESS_GROUP disables Ctrl+C;
    callers must use Ctrl+Break for that case. Installing this handler does not
    change the inherited ignore setting.
    Use a native handler because SIGBREAK has a terminating default,
    and Python signal handlers cannot run during an infinite native wait.
    A single module-pinned callback broadcasts to all active callers. If removal
    fails, the callback remains alive and its registration is reused until a
    later removal succeeds. With no subscribers it returns FALSE.
    """
    global _console_handler_registered, _console_subscribers
    interrupted = threading.Event()
    with _console_registration_lock:
        previous = _console_subscribers
        _console_subscribers += (interrupted,)
        try:
            if not _console_handler_registered:
                if not _kernel32.SetConsoleCtrlHandler(_console_control_handler, True):
                    raise ctypes.WinError(ctypes.get_last_error())
                _console_handler_registered = True
        except BaseException:
            _console_subscribers = previous
            raise
    del previous
    try:
        yield interrupted
    finally:
        with _console_registration_lock:
            _console_subscribers = tuple(s for s in _console_subscribers if s is not interrupted)
            if not _console_subscribers:
                if not _kernel32.SetConsoleCtrlHandler(_console_control_handler, False):
                    raise ctypes.WinError(ctypes.get_last_error())
                _console_handler_registered = False


class _StartupInfoW(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.wintypes.DWORD),
        ("lpReserved", ctypes.wintypes.LPWSTR),
        ("lpDesktop", ctypes.wintypes.LPWSTR),
        ("lpTitle", ctypes.wintypes.LPWSTR),
        ("dwX", ctypes.wintypes.DWORD),
        ("dwY", ctypes.wintypes.DWORD),
        ("dwXSize", ctypes.wintypes.DWORD),
        ("dwYSize", ctypes.wintypes.DWORD),
        ("dwXCountChars", ctypes.wintypes.DWORD),
        ("dwYCountChars", ctypes.wintypes.DWORD),
        ("dwFillAttribute", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("wShowWindow", ctypes.wintypes.WORD),
        ("cbReserved2", ctypes.wintypes.WORD),
        ("lpReserved2", ctypes.c_char_p),
        ("hStdInput", ctypes.wintypes.HANDLE),
        ("hStdOutput", ctypes.wintypes.HANDLE),
        ("hStdError", ctypes.wintypes.HANDLE),
    ]


class _StartupInfoExW(ctypes.Structure):
    _anonymous_ = ("StartupInfo",)
    _fields_ = [("StartupInfo", _StartupInfoW), ("lpAttributeList", ctypes.c_void_p)]


@contextmanager
def _startup_stdio(desktop_name: str, inherit_stdio: bool) -> Iterator[_StartupInfoExW]:
    """Keep private stdio duplicates and the inheritance list alive through creation."""
    si = _StartupInfoExW()
    si.cb = ctypes.sizeof(_StartupInfoW)
    si.lpDesktop = desktop_name
    with ExitStack() as cleanup:
        handles = []
        if inherit_stdio:
            streams = [
                (field, _kernel32.GetStdHandle(_wt.DWORD(kind)))
                for field, kind in (("hStdInput", -10), ("hStdOutput", -11), ("hStdError", -12))
            ]
            missing = (None, 0, _wt.HANDLE(-1).value)
            # Preserve the no-stdio case; partial stdio needs three valid handles
            # once STARTF_USESTDHANDLES is enabled.
            if not any(handle not in missing for _, handle in streams):
                streams = []
            process = _kernel32.GetCurrentProcess()
            for field, handle in streams:
                if handle in missing:
                    access = 0x80000000 if field == "hStdInput" else 0x40000000
                    # GENERIC_READ/WRITE, FILE_SHARE_READ|WRITE, OPEN_EXISTING.
                    # Native handles avoid allocating or changing CRT stdio slots.
                    handle = _kernel32.CreateFileW("NUL", access, 3, None, 3, 0, None)
                    if handle in missing:
                        raise ctypes.WinError(ctypes.get_last_error())
                    cleanup.callback(_kernel32.CloseHandle, handle)
                duplicate = _wt.HANDLE()
                if not _kernel32.DuplicateHandle(
                    process, handle, process, ctypes.byref(duplicate), 0, True, 2
                ):  # DUPLICATE_SAME_ACCESS; do not change the original's inherit flag.
                    raise ctypes.WinError(ctypes.get_last_error())
                cleanup.callback(_kernel32.CloseHandle, duplicate.value)
                setattr(si, field, duplicate.value)
                handles.append(duplicate.value)
        if handles:
            si.dwFlags = _STARTF_USESTDHANDLES
            size = ctypes.c_size_t()
            _kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attributes = ctypes.create_string_buffer(size.value)
            if not _kernel32.InitializeProcThreadAttributeList(
                attributes, 1, 0, ctypes.byref(size)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            cleanup.callback(_kernel32.DeleteProcThreadAttributeList, attributes)
            handle_list = (_wt.HANDLE * len(handles))(*handles)
            if not _kernel32.UpdateProcThreadAttribute(
                attributes,
                0,
                _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                handle_list,
                ctypes.sizeof(handle_list),
                None,
                None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            si.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
            si.cb = ctypes.sizeof(_StartupInfoExW)
        yield si


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.wintypes.HANDLE),
        ("hThread", ctypes.wintypes.HANDLE),
        ("dwProcessId", ctypes.wintypes.DWORD),
        ("dwThreadId", ctypes.wintypes.DWORD),
    ]


class _UserObjectFlags(ctypes.Structure):
    _fields_ = [
        ("fInherit", ctypes.wintypes.BOOL),
        ("fReserved", ctypes.wintypes.BOOL),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]


# Desktop management


def create_hidden_desktop(name: str = DESKTOP_NAME) -> int:
    """Create a named hidden desktop and return its handle.

    ``CreateDesktopW`` on an already-existing name returns a new handle to the
    same object — the caller must still call :func:`close_desktop` on it.
    """
    h_desk = _user32.CreateDesktopW(name, None, None, 0, _DESKTOP_ACCESS, None)
    if not h_desk:
        raise OSError(f"CreateDesktopW({name!r}) failed: error {ctypes.get_last_error()}")
    return h_desk


def close_desktop(handle: int) -> None:
    """Close a desktop handle obtained from :func:`create_hidden_desktop`."""
    _user32.CloseDesktop(handle)


def close_process_handle(handle: int) -> None:
    """Close a process handle returned by :func:`launch_on_desktop` or
    :func:`launch_cmd_on_desktop`."""
    _kernel32.CloseHandle(handle)


def terminate_process_handle(handle: int, exit_code: int = 1) -> None:
    """Terminate a just-launched process through its original process handle."""
    if not _kernel32.TerminateProcess(handle, exit_code):
        raise OSError(f"TerminateProcess failed: error {ctypes.get_last_error()}")


def _wait_for_process_start(handle: int, timeout: float) -> None:
    """Wait for a GUI child to become input-idle without reopening its PID."""
    if timeout == 0:
        return
    result = _user32.WaitForInputIdle(handle, int(timeout * 1000))
    if result == _WAIT_TIMEOUT:
        try:
            terminate_process_handle(handle)
        finally:
            close_process_handle(handle)
        raise TimeoutError(f"process did not become ready within {timeout:g} seconds")
    # WAIT_FAILED is expected for console processes and applications that do
    # not create a GUI message queue. CreateProcessW already established that
    # the process exists, so those children are ready for the caller's next
    # operation even though input-idle cannot be observed.
    if result == _WAIT_FAILED:
        return


def switch_thread_to_desktop(handle: int) -> None:
    """Switch the calling thread to the desktop identified by *handle*.

    Must be called before the thread creates any windows — once a thread has
    windows it cannot switch desktops.  Makes ``IUIAutomation.GetRootElement()``
    see the hidden desktop on this thread.
    """
    if not _user32.SetThreadDesktop(handle):
        raise OSError(f"SetThreadDesktop failed: error {ctypes.get_last_error()}")


# Desktop / station introspection


def get_current_desktop_name() -> str:
    """Return the name of the calling thread's current desktop."""
    tid = _kernel32.GetCurrentThreadId()
    h_desk = _user32.GetThreadDesktop(tid)
    buf = ctypes.create_unicode_buffer(256)
    needed = ctypes.wintypes.DWORD()
    _user32.GetUserObjectInformationW(h_desk, 2, buf, 512, ctypes.byref(needed))
    return buf.value


def has_interactive_station() -> bool:
    """Return ``True`` if the process window station has a visible (interactive) display.

    Returns ``False`` in non-interactive sessions where no user desktop is
    attached (e.g. Windows Session 0, headless CI without an RDP virtual
    display).  On GitHub Actions and Azure DevOps Windows runners this returns
    ``True`` because those environments maintain an active RDP session.
    """
    h_win_sta = _user32.GetProcessWindowStation()
    if not h_win_sta:
        return False
    flags = _UserObjectFlags()
    needed = ctypes.wintypes.DWORD()
    ok = _user32.GetUserObjectInformationW(
        h_win_sta,
        _UOI_FLAGS,
        ctypes.byref(flags),
        ctypes.sizeof(flags),
        ctypes.byref(needed),
    )
    if not ok:
        return False
    return bool(flags.dwFlags & _WSF_VISIBLE)


# Process launch on a specific desktop


def launch_on_desktop(
    args: list[str],
    desktop_name: str = DESKTOP_NAME,
    *,
    inherit_stdio: bool = True,
    job_handle: int | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int]:
    """Launch *args* as a new process on *desktop_name*, forwarding stdio.

    Returns ``(pid, h_process)``.  The caller must close *h_process*.

    Used by :func:`run_hidden` (the ``dolphin-run`` entry point) to re-launch
    pytest or any other command on the hidden desktop while keeping stdout and
    stderr connected to the calling terminal.

    When *job_handle* is provided, assign the suspended child to that job before
    resuming it, so even immediately spawned descendants belong to the job.
    Environment overrides are private to the child; the parent is not modified.
    """
    pi = _ProcessInformation()
    cmd_str = _build_cmd_string(args)
    environment = _build_environment_block(env)
    creation_flags = _CREATE_SUSPENDED if job_handle is not None else 0
    if environment is not None:
        creation_flags |= _CREATE_UNICODE_ENVIRONMENT
    environment_ptr = ctypes.cast(environment, ctypes.c_void_p) if environment is not None else None

    with _startup_stdio(desktop_name, inherit_stdio) as si:
        inherit_handles = bool(si.lpAttributeList)
        if inherit_handles:
            creation_flags |= _EXTENDED_STARTUPINFO_PRESENT
        ok = _kernel32.CreateProcessW(
            None,
            cmd_str,
            None,  # process security attrs
            None,  # thread security attrs
            inherit_handles,  # TRUE only with an explicit HANDLE_LIST
            creation_flags,
            environment_ptr,
            None,  # lpCurrentDirectory=NULL → inherit parent cwd
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            raise OSError(f"CreateProcessW({cmd_str!r}) failed: error {ctypes.get_last_error()}")
    try:
        if job_handle is not None:
            # Assign before any code runs: a venv redirector may immediately
            # create another Python process, which must inherit job membership.
            if not _kernel32.AssignProcessToJobObject(job_handle, pi.hProcess):
                error = ctypes.get_last_error()
                raise ctypes.WinError(
                    error,
                    "AssignProcessToJobObject failed; an existing Windows Job Object "
                    "may prevent nesting (for example, CI agent UI restrictions). "
                    "The command was not resumed. " + ctypes.FormatError(error),
                )
            if _kernel32.ResumeThread(pi.hThread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        try:
            terminate_process_handle(pi.hProcess)
            waited = _kernel32.WaitForSingleObject(
                pi.hProcess, int(_PROCESS_TERMINATION_TIMEOUT_SECONDS * 1000)
            )
            if waited == _WAIT_TIMEOUT:
                raise TimeoutError("suspended command process did not exit after termination")
            if waited != _WAIT_OBJECT_0:
                raise OSError(
                    "WaitForSingleObject during failed launch cleanup returned "
                    f"0x{waited:08X}: error {ctypes.get_last_error()}"
                )
        finally:
            close_process_handle(pi.hProcess)
        raise
    finally:
        _kernel32.CloseHandle(pi.hThread)
    return pi.dwProcessId, pi.hProcess


def launch_cmd_on_desktop(
    cmd: str,
    desktop_name: str | None = DESKTOP_NAME,
    *,
    timeout: float = 10.0,
    wait_for_idle: bool = False,
    work_dir: str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[int, int]:
    """Launch a command string on *desktop_name* without inheriting stdio.

    ``CreateProcessW`` reports process creation synchronously.  When
    *wait_for_idle* is true, *timeout* bounds the subsequent input-idle
    readiness wait.  The default is false, matching pywinauto's former
    ``Application.start(..., wait_for_idle=False)`` behaviour. The returned
    process handle remains open; the caller must close it via
    :func:`close_process_handle`.

    When *env* is provided, it is merged with the parent's environment into a
    private Unicode environment block.  The parent ``os.environ`` is never
    modified.  Passing ``desktop_name=None`` launches on the caller's desktop.
    """
    if timeout < 0 or not math.isfinite(timeout):
        raise ValueError("timeout must be a finite non-negative number")
    si = _StartupInfoW()
    si.cb = ctypes.sizeof(_StartupInfoW)
    si.lpDesktop = desktop_name

    pi = _ProcessInformation()
    environment = _build_environment_block(env)
    creation_flags = _CREATE_UNICODE_ENVIRONMENT if environment is not None else 0
    environment_ptr = ctypes.cast(environment, ctypes.c_void_p) if environment is not None else None

    ok = _kernel32.CreateProcessW(
        None,
        cmd,
        None,
        None,
        False,  # bInheritHandles
        creation_flags,
        environment_ptr,
        work_dir,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        raise OSError(f"CreateProcessW({cmd!r}) failed: error {ctypes.get_last_error()}")
    _kernel32.CloseHandle(pi.hThread)
    if wait_for_idle:
        _wait_for_process_start(pi.hProcess, timeout)
    return pi.dwProcessId, pi.hProcess


def _build_environment_block(overrides: Mapping[str, str] | None) -> Any:
    """Build a private UTF-16 environment block for ``CreateProcessW``."""
    if overrides is None:
        return None
    environment = os.environ.copy()
    environment.update(overrides)
    block = "\0".join(f"{key}={value}" for key, value in sorted(environment.items()))
    return ctypes.create_unicode_buffer(f"{block}\0")


def _build_cmd_string(args: list[str]) -> str:
    """Join *args* into a Win32 command string the child will parse back correctly.

    ``CommandLineToArgvW`` — what the CRT and CPython use to split a command line —
    requires backslashes preceding a quote to be doubled, which shell-style ``\\"``
    escaping does not do: a trailing backslash in a quoted path would escape the
    closing quote and swallow the rest of the line.
    """
    import subprocess

    return subprocess.list2cmdline(args)


# dolphin-run entry point


def run_hidden(args: list[str]) -> int:
    """Create a hidden desktop and run *args* on it.  Returns the exit code.

    If the calling process is already on ``DolphinHidden`` (a nested
    ``dolphin-run`` call), no additional desktop handle is opened.
    ``DOLPHIN_HEADLESS=1`` is injected into the environment before
    launch so the dolphin library and pytest plugin activate headless behaviour
    without extra flags.
    """
    h_desk = None
    try:
        with _console_interrupts() as interrupted, _owned_job() as job:
            if get_current_desktop_name() != DESKTOP_NAME:
                h_desk = create_hidden_desktop()
            _pid, h_process = launch_on_desktop(
                args,
                DESKTOP_NAME,
                inherit_stdio=True,
                job_handle=job,
                env={"DOLPHIN_HEADLESS": "1"},
            )
            try:
                return _wait_for_command(h_process, job, interrupted)
            finally:
                close_process_handle(h_process)
    finally:
        if h_desk is not None:
            close_desktop(h_desk)


def _wait_for_command(h_process: int, job: int, interrupted: threading.Event) -> int:
    deadline = None
    termination_deadline = None
    while True:
        notifications = _job_notifications.get(job)
        if notifications is not None:
            notifications.drain()
        # An unchecked wait is worse than no wait: on WAIT_FAILED the process is
        # still running, GetExitCodeProcess reports STILL_ACTIVE (259), and
        # dolphin-run exits 259 — a fabricated result for CI to act on.
        waited = _kernel32.WaitForSingleObject(h_process, 100)
        if waited == _WAIT_TIMEOUT:
            if termination_deadline is not None:
                if time.monotonic() >= termination_deadline:
                    raise TimeoutError("command process did not exit after forced termination")
            elif interrupted.is_set():
                if deadline is None:
                    deadline = time.monotonic() + _INTERRUPT_GRACE_SECONDS
                elif time.monotonic() >= deadline:
                    _terminate_and_wait_for_job(job)
                    termination_deadline = time.monotonic() + _PROCESS_TERMINATION_TIMEOUT_SECONDS
            continue
        if waited != _WAIT_OBJECT_0:
            raise OSError(
                f"WaitForSingleObject on the child process returned 0x{waited:08X}: "
                f"error {ctypes.get_last_error()}"
            )
        exit_code = ctypes.wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(h_process, ctypes.byref(exit_code)):
            raise OSError(f"GetExitCodeProcess failed: error {ctypes.get_last_error()}")
        return exit_code.value


def main() -> None:
    """Entry point for the ``dolphin-run`` CLI command."""
    if len(sys.argv) < 2:
        print("Usage: dolphin-run <command> [args...]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Examples:", file=sys.stderr)
        print("  dolphin-run pytest tests/", file=sys.stderr)
        print("  dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] in ("-h", "--help"):
        print("Usage: dolphin-run <command> [args...]")
        print("")
        print("Examples:")
        print("  dolphin-run pytest tests/")
        print("  dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia")
        sys.exit(0)
    sys.exit(run_hidden(sys.argv[1:]))
