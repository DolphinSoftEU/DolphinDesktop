"""Cross-process DLL injection + named-pipe IPC for the Qt agent.

This module loads the Qt 5/6 agent DLL into a target process using
``CreateRemoteThread`` + ``LoadLibraryW``, then talks to it over a Windows
named pipe (``\\\\.\\pipe\\dolphin_qt_<pid>``).

Only x64 agent DLLs ship, and the remote thread is started at an address
resolved in *our* address space, so injection is refused unless the target
process, the agent DLL and the host Python all share one architecture.

The agent exposes ``QObject``-tree introspection, QML scene graph walking,
``QGraphicsView`` items, ``Q_PROPERTY`` get/set, and ``QMetaObject``
method invocation — closing the UIA gaps for QML / custom
canvases / Qt Charts / Qt 3D.

Usage::

    inj = QtAgentClient.attach(app.process_id, qt_version=app.qt_version())
    inj.ping()
    for top in inj.tree():
        print(top["class"], top["objectName"])
    btn = inj.find(objectName="loginButton")[0]
    inj.invoke(btn["handle"], "animateClick")

One :class:`QtAgentClient` may be shared across threads: every request
holds an internal lock for the whole write → read round-trip, so concurrent
callers serialise instead of interleaving on the pipe.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import threading
import time
from pathlib import Path
from typing import Any

from ._exceptions import DolphinError
from ._qt_agent import agent_dll_for

# Win32 constants and prototypes

PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
INFINITE = 0xFFFFFFFF

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
ERROR_PIPE_BUSY = 231
SECURITY_SQOS_PRESENT = 0x00100000
SECURITY_IDENTIFICATION = 0x00010000

IMAGE_FILE_MACHINE_UNKNOWN = 0x0000
IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_ARM64 = 0xAA64
IMAGE_FILE_MACHINE_AMD64 = 0x8664

_MACHINE_NAMES = {
    IMAGE_FILE_MACHINE_I386: "x86 (32-bit)",
    IMAGE_FILE_MACHINE_AMD64: "x64 (64-bit)",
    IMAGE_FILE_MACHINE_ARM64: "arm64",
}

#: Seconds a single RPC round-trip may take before the call is abandoned.
DEFAULT_RPC_TIMEOUT = 30.0
#: Largest single agent response accepted before the stream is declared corrupt.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
#: How many timed-out request ids stay recoverable; a permanently wedged agent
#: would otherwise add one per call for the lifetime of the client.
MAX_ABANDONED_IDS = 256
_READ_CHUNK = 64 * 1024
_READ_POLL_S = 0.005

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_OpenProcess = _kernel32.OpenProcess
_OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
_OpenProcess.restype = wt.HANDLE

_VirtualAllocEx = _kernel32.VirtualAllocEx
_VirtualAllocEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD, wt.DWORD]
_VirtualAllocEx.restype = ctypes.c_void_p

_VirtualFreeEx = _kernel32.VirtualFreeEx
_VirtualFreeEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wt.DWORD]
_VirtualFreeEx.restype = wt.BOOL

_WriteProcessMemory = _kernel32.WriteProcessMemory
_WriteProcessMemory.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
_WriteProcessMemory.restype = wt.BOOL

_CreateRemoteThread = _kernel32.CreateRemoteThread
_CreateRemoteThread.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
]
_CreateRemoteThread.restype = wt.HANDLE

_GetModuleHandleW = _kernel32.GetModuleHandleW
_GetModuleHandleW.argtypes = [wt.LPCWSTR]
_GetModuleHandleW.restype = wt.HMODULE

_GetProcAddress = _kernel32.GetProcAddress
_GetProcAddress.argtypes = [wt.HMODULE, ctypes.c_char_p]
_GetProcAddress.restype = ctypes.c_void_p

_WaitForSingleObject = _kernel32.WaitForSingleObject
_WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
_WaitForSingleObject.restype = wt.DWORD

_GetExitCodeThread = _kernel32.GetExitCodeThread
_GetExitCodeThread.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
_GetExitCodeThread.restype = wt.BOOL

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = [wt.HANDLE]
_CloseHandle.restype = wt.BOOL

_CancelIoEx = _kernel32.CancelIoEx
_CancelIoEx.argtypes = [wt.HANDLE, ctypes.c_void_p]
_CancelIoEx.restype = wt.BOOL

_CreateFileW = _kernel32.CreateFileW
_CreateFileW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    wt.HANDLE,
]
_CreateFileW.restype = wt.HANDLE

_WaitNamedPipeW = _kernel32.WaitNamedPipeW
_WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
_WaitNamedPipeW.restype = wt.BOOL

_ReadFile = _kernel32.ReadFile
_ReadFile.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
_ReadFile.restype = wt.BOOL

_PeekNamedPipe = _kernel32.PeekNamedPipe
_PeekNamedPipe.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(wt.DWORD),
    ctypes.POINTER(wt.DWORD),
]
_PeekNamedPipe.restype = wt.BOOL

_GetNamedPipeServerProcessId = _kernel32.GetNamedPipeServerProcessId
_GetNamedPipeServerProcessId.argtypes = [wt.HANDLE, ctypes.POINTER(wt.ULONG)]
_GetNamedPipeServerProcessId.restype = wt.BOOL

_GetCurrentProcess = _kernel32.GetCurrentProcess
_GetCurrentProcess.argtypes = []
_GetCurrentProcess.restype = wt.HANDLE

_IsWow64Process = _kernel32.IsWow64Process
_IsWow64Process.argtypes = [wt.HANDLE, ctypes.POINTER(wt.BOOL)]
_IsWow64Process.restype = wt.BOOL

# Windows 10 1511+; absent on older builds, where _IsWow64Process is the fallback.
_IsWow64Process2 = getattr(_kernel32, "IsWow64Process2", None)
if _IsWow64Process2 is not None:
    _IsWow64Process2.argtypes = [
        wt.HANDLE,
        ctypes.POINTER(wt.USHORT),
        ctypes.POINTER(wt.USHORT),
    ]
    _IsWow64Process2.restype = wt.BOOL

_WriteFile = _kernel32.WriteFile
_WriteFile.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
_WriteFile.restype = wt.BOOL


# Public exceptions


class QtAgentInjectError(DolphinError, RuntimeError):
    """Raised when DLL injection or the agent's start function fails."""


class QtAgentRpcError(DolphinError, RuntimeError):
    """Raised when the agent returns ``ok=False`` for a request."""


class QtAgentTimeoutError(QtAgentRpcError):
    """Raised when a single request outlives ``rpc_timeout``.

    Recoverable, unlike its base: the connection stays usable and the late
    reply to the abandoned request is discarded when it arrives. A Qt event
    loop parked behind a native modal dialog produces this and nothing else.
    """


# Architecture matching


def _machine_name(machine: int) -> str:
    return _MACHINE_NAMES.get(machine, f"IMAGE_FILE_MACHINE_0x{machine:x}")


def _native_machine() -> int:
    """OS architecture, used only on Windows builds without ``IsWow64Process2``.

    Those builds predate arm64 Windows, so a WOW64 host implies an x64 OS.
    """
    wow64 = wt.BOOL(0)
    if _IsWow64Process(_GetCurrentProcess(), ctypes.byref(wow64)) and wow64.value:
        return IMAGE_FILE_MACHINE_AMD64
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        return IMAGE_FILE_MACHINE_AMD64
    return IMAGE_FILE_MACHINE_I386


def _process_machine(hproc: int) -> int:
    """Return the ``IMAGE_FILE_MACHINE_*`` the code in *hproc* executes as."""
    if _IsWow64Process2 is not None:
        process_machine = wt.USHORT(0)
        native_machine = wt.USHORT(0)
        ok = _IsWow64Process2(hproc, ctypes.byref(process_machine), ctypes.byref(native_machine))
        if ok:
            if process_machine.value != IMAGE_FILE_MACHINE_UNKNOWN:
                return process_machine.value
            return native_machine.value
    wow64 = wt.BOOL(0)
    if not _IsWow64Process(hproc, ctypes.byref(wow64)):
        err = ctypes.get_last_error()
        raise QtAgentInjectError(f"IsWow64Process failed: WinError {err}")
    if wow64.value:
        return IMAGE_FILE_MACHINE_I386
    return _native_machine()


def _pe_machine(dll_path: Path) -> int:
    """Return the ``IMAGE_FILE_MACHINE_*`` from a PE file's COFF header."""
    with open(dll_path, "rb") as fh:
        dos = fh.read(0x40)
        if dos[:2] != b"MZ":
            raise QtAgentInjectError(f"not a PE file: {dll_path}")
        fh.seek(int.from_bytes(dos[0x3C:0x40], "little"))
        pe = fh.read(6)
    if pe[:4] != b"PE\x00\x00":
        raise QtAgentInjectError(f"missing PE signature in {dll_path}")
    return int.from_bytes(pe[4:6], "little")


def _require_matching_arch(hproc: int, pid: int, dll_path: Path) -> None:
    """Refuse injection unless target, agent DLL and host Python share an arch.

    ``_inject_dll`` resolves ``LoadLibraryW`` from *our own* kernel32 and hands
    that address to ``CreateRemoteThread`` in the target: the address is only
    meaningful in a process of the same architecture. Starting a remote thread
    at a foreign address terminates the target process.
    """
    target = _process_machine(hproc)
    dll = _pe_machine(dll_path)
    host = _process_machine(_GetCurrentProcess())
    if target == dll == host:
        return
    raise QtAgentInjectError(
        f"refusing to inject into pid={pid}: architecture mismatch — target "
        f"process is {_machine_name(target)}, agent DLL {dll_path.name} is "
        f"{_machine_name(dll)}, host Python is {_machine_name(host)}. "
        "dolphin ships x64 agent DLLs only, and cross-architecture injection "
        "would crash the target process. Use the UIA backend (Desktop / Window "
        f"/ Locator) for {_machine_name(target)} Qt apps — it needs no agent — "
        "or run the app and the test process on the same architecture."
    )


# Injection


def _inject_dll(pid: int, dll_path: Path) -> None:
    """Inject *dll_path* into process *pid* via LoadLibraryW.

    Raises QtAgentInjectError on any failure, including an architecture
    mismatch between the target, the agent DLL and this Python process.
    """
    rights = (
        PROCESS_CREATE_THREAD
        | PROCESS_QUERY_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_WRITE
        | PROCESS_VM_READ
    )
    hproc = _OpenProcess(rights, False, pid)
    if not hproc:
        err = ctypes.get_last_error()
        raise QtAgentInjectError(f"OpenProcess({pid}) failed: WinError {err}")

    try:
        _require_matching_arch(hproc, pid, dll_path)

        wide = str(dll_path).encode("utf-16le") + b"\x00\x00"
        size = len(wide)

        addr = _VirtualAllocEx(hproc, None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not addr:
            err = ctypes.get_last_error()
            raise QtAgentInjectError(f"VirtualAllocEx failed: WinError {err}")

        remote_thread_live = False
        try:
            written = ctypes.c_size_t(0)
            ok = _WriteProcessMemory(hproc, addr, wide, size, ctypes.byref(written))
            if not ok or written.value != size:
                err = ctypes.get_last_error()
                raise QtAgentInjectError(f"WriteProcessMemory failed: WinError {err}")

            hk = _GetModuleHandleW("kernel32.dll")
            load_lib = _GetProcAddress(hk, b"LoadLibraryW")
            if not load_lib:
                err = ctypes.get_last_error()
                raise QtAgentInjectError(f"GetProcAddress(LoadLibraryW) failed: WinError {err}")

            tid = wt.DWORD(0)
            hthread = _CreateRemoteThread(hproc, None, 0, load_lib, addr, 0, ctypes.byref(tid))
            if not hthread:
                err = ctypes.get_last_error()
                raise QtAgentInjectError(f"CreateRemoteThread failed: WinError {err}")

            remote_thread_live = True
            try:
                rc = _WaitForSingleObject(hthread, 30_000)
                if rc != 0:
                    raise QtAgentInjectError(f"LoadLibraryW thread timed out (rc={rc})")
                remote_thread_live = False
                # A thread exit code is a DWORD, so this is LoadLibraryW's
                # HMODULE with the top 32 bits cut off. A remote base whose low
                # half is all zeros therefore reads as NULL and a successful
                # load is reported as a failure — rare (64K-granular ASLR) and
                # in the safe direction, which is why it is left alone. Do not
                # "fix" it by treating 0 as success: that turns every genuine
                # load failure into a hang at the first RPC.
                exit_code = wt.DWORD(0)
                _GetExitCodeThread(hthread, ctypes.byref(exit_code))
                if exit_code.value == 0:
                    raise QtAgentInjectError(
                        "LoadLibraryW returned NULL — Qt agent DLL failed to load "
                        "(check Qt runtime DLLs are present in the target process)"
                    )
            finally:
                _CloseHandle(hthread)
        finally:
            # addr is the argument the remote thread is reading. Freeing it
            # while that thread still runs faults the target, so on timeout the
            # allocation is leaked on purpose — a page in the AUT beats killing it.
            if not remote_thread_live:
                _VirtualFreeEx(hproc, addr, 0, MEM_RELEASE)
    finally:
        _CloseHandle(hproc)


# Named-pipe client


def _verify_pipe_server(handle: int, pipe_name: str, expected_pid: int) -> None:
    """Refuse a pipe that is not served by *expected_pid*.

    The agent pipe name is derived from the target pid, so any local process
    running as the same user can pre-create it and answer our requests. The
    server pid is the one property a squatter cannot forge.
    """
    server_pid = wt.ULONG(0)
    if not _GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid)):
        err = ctypes.get_last_error()
        raise QtAgentInjectError(
            f"cannot identify the server process of {pipe_name} (WinError {err}) — "
            "refusing to trust an unverified agent pipe"
        )
    if server_pid.value != expected_pid:
        raise QtAgentInjectError(
            f"{pipe_name} is served by pid {server_pid.value}, not the injected "
            f"target pid {expected_pid} — another local process holds the agent "
            "pipe name and could forge agent responses; refusing to connect"
        )


def _open_pipe(pipe_name: str, expected_pid: int, timeout_s: float = 10.0) -> int:
    """Connect to a named pipe, waiting up to *timeout_s* for it to appear.

    The connected pipe is only returned once its server has been confirmed to
    be *expected_pid*.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        # Without SECURITY_SQOS_PRESENT Windows connects named pipes at
        # SecurityImpersonation, so a squatter holding this name can call
        # ImpersonateNamedPipeClient() and act as us the instant CreateFileW
        # returns — before _verify_pipe_server gets a chance to refuse it.
        handle = _CreateFileW(
            pipe_name,
            GENERIC_READ | GENERIC_WRITE,
            0,
            None,
            OPEN_EXISTING,
            SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION,
            None,
        )
        if handle and handle != ctypes.c_void_p(INVALID_HANDLE_VALUE).value:
            try:
                _verify_pipe_server(handle, pipe_name, expected_pid)
            except QtAgentInjectError:
                _CloseHandle(handle)
                raise
            return handle
        err = ctypes.get_last_error()
        if err == ERROR_PIPE_BUSY:
            _WaitNamedPipeW(pipe_name, 1000)
        if time.monotonic() > deadline:
            raise QtAgentInjectError(f"timed out connecting to {pipe_name} (WinError {err})")
        time.sleep(0.05)


class QtAgentClient:
    """High-level RPC client for an injected Qt agent."""

    def __init__(
        self,
        pid: int,
        pipe_handle: int,
        *,
        rpc_timeout: float = DEFAULT_RPC_TIMEOUT,
        qt_version: str | None = None,
    ) -> None:
        self.pid = pid
        self.rpc_timeout = rpc_timeout
        self._pipe = pipe_handle
        self._qt_version = qt_version
        self._req_id = 0
        self._pending = b""
        self._broken: str | None = None
        self._abandoned: set[int] = set()
        self._lock = threading.RLock()
        # The handle is guarded separately: recovery has to be able to drop a
        # pipe that a _send() is parked on, which it cannot do behind _lock.
        self._pipe_lock = threading.Lock()

    # ----- lifecycle --------------------------------------------------

    @classmethod
    def attach(
        cls,
        pid: int,
        qt_version: str,
        *,
        timeout: float = 15.0,
        rpc_timeout: float = DEFAULT_RPC_TIMEOUT,
    ) -> QtAgentClient:
        """Inject the matching DLL into ``pid`` and return a connected client.

        Args:
            pid: Target process id.
            qt_version: ``"5"`` or ``"6"`` — picks the matching agent DLL.
            timeout: Seconds allowed for the agent's pipe to appear and accept
                a connection. It does not bound the whole call: the two remote
                threads before it carry their own fixed caps (30 s for
                ``LoadLibraryW``, 15 s for ``dolphin_qt_agent_start``), so the
                worst case is those plus *timeout*.
            rpc_timeout: Seconds any single request may wait for its reply.

        Raises:
            QtAgentInjectError: if the target's architecture does not match the
                agent DLL and this Python process, if injection fails, or if
                the agent pipe turns out to be served by a different process.
        """
        dll = agent_dll_for(qt_version)

        # Windows does not recycle a pid while a handle to it is open, so this
        # handle pins the identity of the target across inject → start → connect.
        hpin = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hpin:
            err = ctypes.get_last_error()
            raise QtAgentInjectError(f"OpenProcess({pid}) failed: WinError {err}")
        try:
            _inject_dll(pid, dll)

            # The injected DLL's DllMain doesn't call dolphin_qt_agent_start —
            # we need to invoke it explicitly via a second CreateRemoteThread
            # pointed at the exported entry point.
            pipe_name = f"\\\\.\\pipe\\dolphin_qt_{pid}"
            _start_agent(pid, dll, pipe_name)

            handle = _open_pipe(pipe_name, pid, timeout)
        finally:
            _CloseHandle(hpin)
        return cls(pid, handle, rpc_timeout=rpc_timeout, qt_version=qt_version)

    def reattach(self, *, timeout: float = 15.0) -> QtAgentClient:
        """Reopen the pipe to the agent already loaded in the target process.

        This is the recovery path for a terminally broken transport
        (:attr:`is_broken`) — the DLL is never injected a second time, only the
        connection is rebuilt, so the request stream restarts in a known state
        and requests abandoned by an earlier timeout are forgotten with the old
        pipe. If the agent's pipe server is gone and the client knows which Qt
        version it attached with, ``dolphin_qt_agent_start`` is called again in
        the target (idempotent — it reports success when already running).

        Safe to call from a watchdog thread while other threads are blocked in
        a request: the old pipe is dropped before the request lock is taken, so
        those requests fail immediately instead of holding recovery off for
        ``rpc_timeout`` each.

        Raises:
            QtAgentInjectError: if the target process is gone, if the agent
                pipe cannot be reached, or if it is served by a process other
                than the attached target.
        """
        self._close_pipe()
        with self._lock:
            # Same pin as attach(): without a handle open, Windows may have
            # recycled this pid onto a different process — which, if that one
            # was also injected, serves a genuine dolphin_qt_<pid> pipe, so
            # _verify_pipe_server would happily accept the wrong application.
            hpin = _OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, self.pid)
            if not hpin:
                err = ctypes.get_last_error()
                raise QtAgentInjectError(
                    f"cannot reattach to pid={self.pid}: OpenProcess failed "
                    f"(WinError {err}) — the process has most likely exited"
                )
            try:
                pipe_name = f"\\\\.\\pipe\\dolphin_qt_{self.pid}"
                try:
                    handle = _open_pipe(pipe_name, self.pid, timeout)
                except QtAgentInjectError:
                    if self._qt_version is None:
                        raise
                    _start_agent(self.pid, agent_dll_for(self._qt_version), pipe_name)
                    handle = _open_pipe(pipe_name, self.pid, timeout)
            finally:
                _CloseHandle(hpin)
            with self._pipe_lock:
                self._pipe = handle
            self._pending = b""
            self._abandoned.clear()
            self._broken = None
        return self

    @property
    def is_broken(self) -> bool:
        """True once the transport failed terminally; :meth:`reattach` clears it."""
        return self._broken is not None

    @property
    def broken_reason(self) -> str | None:
        """Why the transport is unusable, or ``None`` while it is healthy."""
        return self._broken

    def _close_pipe(self) -> None:
        """Drop the pipe handle, aborting whatever I/O is parked on it.

        Callable from any thread without holding :attr:`_lock`. ``WriteFile``
        on a blocking-mode pipe does not return until the agent drains its
        input buffer, so a request larger than that buffer parks inside
        ``_send``; ``CancelIoEx`` is what lets :meth:`close` / :meth:`reattach`
        preempt it instead of queueing behind it forever.
        """
        with self._pipe_lock:
            pipe, self._pipe = self._pipe, 0
        if pipe:
            _CancelIoEx(pipe, None)
            _CloseHandle(pipe)

    def close(self) -> None:
        """Close the pipe. The agent DLL stays loaded in the target process.

        There is no detach. The DLL does export ``dolphin_qt_agent_stop``
        alongside ``dolphin_qt_agent_start``, so a remote stop + ``FreeLibrary``
        is mechanically possible — but nothing in this repo pins down when the
        agent's own threads have finished with the module, and unloading it
        early faults the AUT. :meth:`reattach` rebuilds the connection instead.
        """
        self._close_pipe()

    def __del__(self) -> None:
        # A client poisoned by _fail() is unreachable through Application's
        # cache; without this the pipe handle would outlive it.
        try:
            self._close_pipe()
        except Exception:
            pass

    def __enter__(self) -> QtAgentClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ----- IPC primitive ---------------------------------------------

    def _fail(self, reason: str, message: str) -> QtAgentRpcError:
        """Mark the connection unusable, drop the pipe, and build the error.

        Terminal for *this* connection: bytes are left in the pipe that cannot
        be attributed to a request, so the next reply would answer an earlier
        one. Only :meth:`reattach` clears it. A timeout is not terminal — the
        abandoned request id is remembered instead, see :meth:`_send`.
        """
        self._broken = reason
        self._pending = b""
        try:
            self._close_pipe()
        except Exception:
            pass
        return QtAgentRpcError(message)

    def _pipe_or_fail(self, op: str) -> int:
        """Snapshot the handle under :attr:`_pipe_lock`, or fail the request.

        ``_close_pipe`` runs from watchdog threads, so the handle must not be
        read straight off the attribute at a syscall site: a close landing
        between the load and the call would issue it on a value Windows is
        free to have already reissued to an unrelated kernel object.
        """
        with self._pipe_lock:
            pipe = self._pipe
        if not pipe:
            raise self._fail(
                "pipe closed under an in-flight request",
                f"pipe {op} failed: the connection was closed",
            )
        return pipe

    def _write_line(self, line: bytes) -> None:
        pipe = self._pipe_or_fail("write")
        written = wt.DWORD(0)
        ok = _WriteFile(pipe, line, len(line), ctypes.byref(written), None)
        if not ok or written.value != len(line):
            err = ctypes.get_last_error()
            raise self._fail(
                f"pipe write failed (WinError {err})",
                f"pipe write failed: WinError {err}",
            )

    def _read_chunk(self, deadline: float, op: str) -> bytes:
        """Return the next available bytes, waiting no longer than *deadline*.

        The handle is opened in blocking mode, so a bare ``ReadFile`` never
        returns when the target's event loop is wedged behind a native modal
        dialog or the agent deadlocks. Peeking first bounds that wait.
        """
        avail = wt.DWORD(0)
        read = wt.DWORD(0)
        chunk = ctypes.create_string_buffer(_READ_CHUNK)
        while True:
            pipe = self._pipe_or_fail("read")
            if not _PeekNamedPipe(pipe, None, 0, None, ctypes.byref(avail), None):
                err = ctypes.get_last_error()
                raise self._fail(
                    f"pipe peek failed (WinError {err})",
                    f"pipe read failed: WinError {err}",
                )
            if avail.value:
                n = min(avail.value, _READ_CHUNK)
                ok = _ReadFile(pipe, chunk, n, ctypes.byref(read), None)
                if not ok or read.value == 0:
                    err = ctypes.get_last_error()
                    raise self._fail(
                        f"pipe read failed (WinError {err})",
                        f"pipe read failed: WinError {err}",
                    )
                return chunk.raw[: read.value]
            if time.monotonic() >= deadline:
                raise QtAgentTimeoutError(
                    f"timed out after {self.rpc_timeout}s waiting for the agent's "
                    f"reply to op={op!r} (pid={self.pid}) — the target's Qt event "
                    "loop is most likely blocked (native modal dialog) or the "
                    "agent deadlocked. The connection stays usable: retry once "
                    "the loop is running again"
                )
            time.sleep(_READ_POLL_S)

    def _read_line(self, deadline: float, op: str) -> bytes:
        buf = bytearray()
        while True:
            nl = self._pending.find(b"\n")
            if nl >= 0:
                buf.extend(self._pending[:nl])
                self._pending = self._pending[nl + 1 :]
                return bytes(buf)
            buf.extend(self._pending)
            self._pending = b""
            if len(buf) > MAX_RESPONSE_BYTES:
                raise self._fail(
                    f"response to op={op!r} exceeded {MAX_RESPONSE_BYTES} bytes",
                    f"agent response to op={op!r} exceeded {MAX_RESPONSE_BYTES} "
                    "bytes without a line terminator — refusing to buffer more",
                )
            try:
                self._pending = self._read_chunk(deadline, op)
            except QtAgentTimeoutError:
                # A timeout must leave the stream byte-for-byte as it found it.
                # Dropping the prefix already consumed here would make the tail
                # that arrives later parse as a malformed line, poisoning the
                # very connection this exception exists to keep usable.
                self._pending = bytes(buf) + self._pending
                raise

    def _send(self, op: str, **kwargs: Any) -> Any:
        # The lock spans write → read: two unserialised callers would each read
        # the other's reply off the shared pipe.
        with self._lock:
            if self._broken is not None:
                raise QtAgentRpcError(
                    f"Qt agent connection is no longer usable: {self._broken}. "
                    "Call reattach() to rebuild the connection to the same "
                    "process, or attach a fresh agent."
                )
            self._req_id += 1
            req_id = self._req_id
            req = {"id": req_id, "op": op, **kwargs}
            self._write_line((json.dumps(req) + "\n").encode("utf-8"))

            deadline = time.monotonic() + self.rpc_timeout
            while True:
                try:
                    raw = self._read_line(deadline, op)
                except QtAgentTimeoutError:
                    # The request is already on the wire, so its reply may still
                    # turn up. Remembering the id is what keeps the timeout
                    # recoverable: the late reply is discarded below instead of
                    # being mistaken for a later request's answer.
                    self._abandoned.add(req_id)
                    # Ids only ever increase, so the smallest is the oldest:
                    # past the window the reply can no longer be attributed and
                    # is treated as the desync it is.
                    while len(self._abandoned) > MAX_ABANDONED_IDS:
                        self._abandoned.discard(min(self._abandoned))
                    raise
                try:
                    resp = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, ValueError) as exc:
                    raise self._fail(
                        f"malformed reply to op={op!r} ({exc})",
                        f"agent sent a malformed JSON reply to op={op!r}: {exc}",
                    ) from exc
                got_id = resp.get("id") if isinstance(resp, dict) else None
                if got_id == req_id:
                    break
                if isinstance(got_id, int) and got_id in self._abandoned:
                    self._abandoned.discard(got_id)
                    continue
                raise self._fail(
                    f"reply id {got_id!r} did not match request id {req_id}",
                    f"agent reply id {got_id!r} does not match request id {req_id} for "
                    f"op={op!r} — the pipe stream is out of sync, so every later reply "
                    "would carry another request's payload. Call reattach() to "
                    "rebuild the connection.",
                )
            if not resp.get("ok"):
                raise QtAgentRpcError(resp.get("error", "agent returned ok=False"))
            return resp.get("result")

    # ----- high-level API --------------------------------------------

    def ping(self) -> str:
        return self._send("ping")

    def tree(self) -> list[dict[str, Any]]:
        """Return the QObject tree of every visible top-level QWidget / QQuickWindow."""
        return self._send("tree")

    def find(self, **filter_kwargs: Any) -> list[dict[str, Any]]:
        """Flat list of QObjects matching the filter.

        Supported filter keys: ``objectName``, ``className``, ``text``,
        ``regex`` (regex applied to objectName).
        """
        return self._send("find", filter=filter_kwargs)

    def describe(self, handle: str) -> dict[str, Any]:
        return self._send("describe", target=handle)

    def members(self, handle: str) -> dict[str, Any]:
        return self._send("members", target=handle)

    def invoke(self, handle: str, method: str, *args: Any) -> dict[str, Any]:
        return self._send("invoke", target=handle, method=method, args=list(args))

    def get_property(self, handle: str, prop: str) -> Any:
        return self._send("get_property", target=handle, property=prop)

    def set_property(self, handle: str, prop: str, value: Any) -> dict[str, Any]:
        return self._send("set_property", target=handle, property=prop, value=value)

    # ----- QML -------------------------------------------------------

    def qml_root(self) -> list[dict[str, Any]]:
        return self._send("qml_root")

    def qml_find(self, object_name: str) -> list[dict[str, Any]]:
        return self._send("qml_find", objectName=object_name)

    def qml_item_at(self, window_handle: str, x: float, y: float) -> dict[str, Any]:
        return self._send("qml_item_at", window=window_handle, x=x, y=y)

    def qml_click(self, item_handle: str) -> dict[str, Any]:
        return self._send("qml_click", target=item_handle)

    # ----- QGraphicsView ---------------------------------------------

    def graphics_items(self, view_handle: str) -> list[dict[str, Any]]:
        return self._send("graphics_items", view=view_handle)

    def graphics_item_at(self, view_handle: str, x: float, y: float) -> dict[str, Any]:
        return self._send("graphics_item_at", view=view_handle, x=x, y=y)


def _resolve_export_rva(dll_path: Path, export_name: bytes) -> int:
    """Parse a Windows PE file and return the RVA of an exported symbol.

    We avoid actually loading the DLL into our own process because the agent
    links against Qt — loading it locally would require Qt DLLs to be on the
    search path, which is brittle and pollutes our address space. Reading the
    PE export table from the file gives us the RVA directly.
    """
    with open(dll_path, "rb") as fh:
        data = fh.read()

    # DOS header: e_lfanew at offset 0x3C points to PE header.
    if data[:2] != b"MZ":
        raise QtAgentInjectError(f"not a PE file: {dll_path}")
    pe_off = int.from_bytes(data[0x3C:0x40], "little")
    if data[pe_off : pe_off + 4] != b"PE\x00\x00":
        raise QtAgentInjectError(f"missing PE signature in {dll_path}")

    coff_off = pe_off + 4
    machine = int.from_bytes(data[coff_off : coff_off + 2], "little")
    num_sections = int.from_bytes(data[coff_off + 2 : coff_off + 4], "little")
    opt_size = int.from_bytes(data[coff_off + 16 : coff_off + 18], "little")
    opt_off = coff_off + 20

    is_pe32_plus = data[opt_off : opt_off + 2] == b"\x0b\x02"  # PE32+
    # Data directories start at:
    #   PE32   : opt_off + 96
    #   PE32+  : opt_off + 112
    dd_off = opt_off + (112 if is_pe32_plus else 96)
    # Export Directory is data directory index 0.
    export_rva = int.from_bytes(data[dd_off : dd_off + 4], "little")
    if export_rva == 0:
        raise QtAgentInjectError(f"no export directory in {dll_path}")

    sections_off = opt_off + opt_size

    # Build (virtual_addr, raw_offset, raw_size) list to translate RVA → file offset.
    sections: list[tuple[int, int, int, int]] = []
    for i in range(num_sections):
        s = sections_off + i * 40
        virtual_size = int.from_bytes(data[s + 8 : s + 12], "little")
        virtual_addr = int.from_bytes(data[s + 12 : s + 16], "little")
        raw_size = int.from_bytes(data[s + 16 : s + 20], "little")
        raw_off = int.from_bytes(data[s + 20 : s + 24], "little")
        sections.append((virtual_addr, virtual_size, raw_off, raw_size))

    def rva_to_off(rva: int, need: int = 1) -> int:
        """Translate *rva*, refusing anything not backed by *need* raw bytes.

        A section's virtual size may exceed its raw size (BSS-style tail) and
        the file may be shorter than the headers claim; slicing past either
        yields a short ``bytes`` that ``int.from_bytes`` happily turns into a
        wrong number, so the truncation has to be an error here.
        """
        for va, vs, ro, rs in sections:
            if va <= rva < va + max(vs, rs):
                delta = rva - va
                off = ro + delta
                if delta + need > rs or off + need > len(data):
                    raise QtAgentInjectError(
                        f"RVA 0x{rva:x} (+{need} bytes) has no raw data behind it in "
                        f"{dll_path} — the file is truncated or not the agent DLL"
                    )
                return off
        raise QtAgentInjectError(f"RVA 0x{rva:x} not mapped")

    export_off = rva_to_off(export_rva, 40)  # sizeof(IMAGE_EXPORT_DIRECTORY)
    # IMAGE_EXPORT_DIRECTORY layout:
    #   uint32 Characteristics, TimeDateStamp; uint16 MajorVersion, MinorVersion;
    #   uint32 Name; uint32 Base; uint32 NumberOfFunctions; uint32 NumberOfNames;
    #   uint32 AddressOfFunctions; uint32 AddressOfNames; uint32 AddressOfNameOrdinals;
    num_funcs = int.from_bytes(data[export_off + 20 : export_off + 24], "little")
    num_names = int.from_bytes(data[export_off + 24 : export_off + 28], "little")
    addr_funcs = int.from_bytes(data[export_off + 28 : export_off + 32], "little")
    addr_names = int.from_bytes(data[export_off + 32 : export_off + 36], "little")
    addr_ords = int.from_bytes(data[export_off + 36 : export_off + 40], "little")

    funcs_off = rva_to_off(addr_funcs, num_funcs * 4)
    names_off = rva_to_off(addr_names, num_names * 4)
    ords_off = rva_to_off(addr_ords, num_names * 2)

    for i in range(num_names):
        name_rva = int.from_bytes(data[names_off + i * 4 : names_off + i * 4 + 4], "little")
        name_off = rva_to_off(name_rva)
        # Read NUL-terminated name.
        end = data.find(b"\x00", name_off)
        if end < 0:
            raise QtAgentInjectError(
                f"unterminated export name at offset 0x{name_off:x} in "
                f"{dll_path} — the file is truncated or not the agent DLL"
            )
        sym = data[name_off:end]
        if sym == export_name:
            ordinal = int.from_bytes(data[ords_off + i * 2 : ords_off + i * 2 + 2], "little")
            if ordinal >= num_funcs:
                raise QtAgentInjectError(
                    f"export {export_name!r} has ordinal {ordinal}, past the "
                    f"{num_funcs}-entry function table of {dll_path}"
                )
            func_rva = int.from_bytes(
                data[funcs_off + ordinal * 4 : funcs_off + ordinal * 4 + 4], "little"
            )
            return func_rva

    raise QtAgentInjectError(
        f"export {export_name!r} not found in {dll_path} (machine=0x{machine:x}, names={num_names})"
    )


def _start_agent(pid: int, dll_path: Path, pipe_name: str) -> None:
    """Look up ``dolphin_qt_agent_start`` in the injected DLL and call it remotely.

    Resolves the export RVA by parsing the DLL's PE header from disk (no
    local load — the DLL links against Qt and would need Qt DLLs in our
    search path). The remote base is found via ``EnumProcessModulesEx`` in
    the target process, then ``base + rva`` gives the function address.
    """
    rva = _resolve_export_rva(dll_path, b"dolphin_qt_agent_start")

    # Find the DLL's base in the target process by scanning its modules.
    try:
        import win32api
        import win32process
    except ImportError as exc:
        raise QtAgentInjectError("pywin32 missing — required to resolve module bases") from exc

    rights = PROCESS_QUERY_INFORMATION | PROCESS_VM_READ
    try:
        hproc = win32api.OpenProcess(rights, False, pid)
    except Exception as exc:
        # pywintypes.error is not an ImportError and not a dolphin exception.
        raise QtAgentInjectError(
            f"OpenProcess({pid}) failed while resolving the agent module base: {exc}"
        ) from exc
    try:
        target_dll_name = dll_path.name.lower()
        try:
            mods = win32process.EnumProcessModulesEx(hproc, 0x03)
        except Exception:
            mods = win32process.EnumProcessModules(hproc)
        base_remote = None
        for hmod in mods:
            try:
                name = win32process.GetModuleFileNameEx(hproc, hmod).lower()
            except Exception:
                continue
            if name.endswith(target_dll_name):
                base_remote = int(hmod)
                break
        if base_remote is None:
            raise QtAgentInjectError(f"injected DLL {target_dll_name!r} not visible in target")
    finally:
        win32api.CloseHandle(hproc)

    remote_proc = base_remote + rva

    # Allocate pipe name buffer in target.
    rights2 = (
        PROCESS_CREATE_THREAD
        | PROCESS_QUERY_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_WRITE
        | PROCESS_VM_READ
    )
    hproc2 = _OpenProcess(rights2, False, pid)
    if not hproc2:
        err = ctypes.get_last_error()
        raise QtAgentInjectError(f"OpenProcess(start) failed: WinError {err}")
    try:
        name_bytes = pipe_name.encode("utf-8") + b"\x00"
        addr = _VirtualAllocEx(
            hproc2, None, len(name_bytes), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
        )
        if not addr:
            err = ctypes.get_last_error()
            raise QtAgentInjectError(f"VirtualAllocEx(start) failed: WinError {err}")
        pipe_name_in_use = False
        try:
            written = ctypes.c_size_t(0)
            ok = _WriteProcessMemory(
                hproc2, addr, name_bytes, len(name_bytes), ctypes.byref(written)
            )
            if not ok or written.value != len(name_bytes):
                err = ctypes.get_last_error()
                raise QtAgentInjectError(f"WriteProcessMemory(pipe name) failed: WinError {err}")

            tid = wt.DWORD(0)
            hthread = _CreateRemoteThread(hproc2, None, 0, remote_proc, addr, 0, ctypes.byref(tid))
            if not hthread:
                err = ctypes.get_last_error()
                raise QtAgentInjectError(f"CreateRemoteThread(start) failed: WinError {err}")
            pipe_name_in_use = True
            try:
                rc = _WaitForSingleObject(hthread, 15_000)
                if rc != 0:
                    raise QtAgentInjectError(f"agent start thread timed out (rc={rc})")
                exit_code = wt.DWORD(0)
                if not _GetExitCodeThread(hthread, ctypes.byref(exit_code)):
                    err = ctypes.get_last_error()
                    # Unlike the LoadLibraryW site, 0 means success here, so an
                    # unchecked failure would read as a started agent.
                    raise QtAgentInjectError(
                        "cannot tell whether dolphin_qt_agent_start succeeded: "
                        f"GetExitCodeThread failed (WinError {err})"
                    )
                # dolphin_qt_agent_start returns 0 on success, non-zero otherwise.
                # Note: 0 == ALREADY-running is also success in our model.
                if exit_code.value not in (0,):
                    raise QtAgentInjectError(
                        f"dolphin_qt_agent_start returned {exit_code.value} "
                        "(qApp likely not constructed yet — wait for window then retry)"
                    )
            finally:
                _CloseHandle(hthread)
        finally:
            # addr is the ``const char* pipe_name`` argument. Once the remote
            # thread has been created the buffer is leaked on purpose: on
            # timeout the thread is still reading it, and even after it returns
            # we cannot prove dolphin_qt_agent_start copied the string rather
            # than retaining the pointer — the agent's pipe server outlives a
            # client disconnect (QtAgentClient.reattach relies on that), so it
            # may well re-read the name to re-create the pipe. One page in the
            # AUT beats a fault.
            if not pipe_name_in_use:
                _VirtualFreeEx(hproc2, addr, 0, MEM_RELEASE)
    finally:
        _CloseHandle(hproc2)
