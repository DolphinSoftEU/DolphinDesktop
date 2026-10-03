"""Map a listening TCP port to the process that owns it (Windows).

``_launch_with_cdp_flag`` treats an ``HTTP 200`` on ``127.0.0.1:<port>`` as
"the app I launched is ready". That is a forgeable signal: any local process
can pre-bind the well-known debug port and answer with 200, and Chromium's
CDP endpoint has no authentication, so connecting to the wrong server hands
DOM, cookies and screenshot bytes to a stranger (CWE-346). Binding the port
to the launched process's PID — or a verified descendant of it — before
connecting closes that gap.

Everything here is best-effort and Windows-only: a lookup failure returns an
empty result, and the caller decides how strict to be. It uses the IP Helper
API ``GetExtendedTcpTable`` with the ``*_OWNER_PID_LISTENER`` table so no
extra dependency is needed.
"""

from __future__ import annotations

import socket
import sys

from ._logging import get_logger

_LOG = get_logger("netinfo")

# GetExtendedTcpTable table classes.
_TCP_TABLE_OWNER_PID_LISTENER = 3

# AF_INET / AF_INET6 as the API expects them.
_AF_INET = 2
_AF_INET6 = 23

_ERROR_INSUFFICIENT_BUFFER = 122


def _loopback_addr(family: int, raw: bytes) -> str | None:
    """Return the dotted/hex address if *raw* is a loopback bind, else None.

    Only loopback binds matter here: a CDP endpoint dolphin drives must be on
    127.0.0.1 / ::1 (Chromium binds its debugger there), and a port bound on
    a routable address is not the endpoint we are looking for.
    """
    try:
        if family == _AF_INET:
            addr = socket.inet_ntop(socket.AF_INET, raw[:4])
            return addr if addr.startswith("127.") else None
        packed = raw[:16]
        addr = socket.inet_ntop(socket.AF_INET6, packed)
        # ::1, and ::ffff:127.0.0.0/8 mapped form.
        if addr in ("::1",) or packed == b"\x00" * 15 + b"\x01":
            return addr
        if addr.startswith("::ffff:127.") or addr.startswith("::ffff:7f"):
            return addr
        return None
    except (OSError, ValueError):
        return None


def _query_table(family: int) -> list[tuple[int, int]]:
    """Return ``[(port, pid), ...]`` for every loopback LISTEN socket of *family*."""
    import ctypes
    import ctypes.wintypes as wt

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    get_table = iphlpapi.GetExtendedTcpTable
    get_table.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wt.DWORD),
        wt.BOOL,
        wt.ULONG,
        ctypes.c_int,
        wt.ULONG,
    ]
    get_table.restype = wt.DWORD

    size = wt.DWORD(0)
    # First call sizes the buffer.
    get_table(None, ctypes.byref(size), False, family, _TCP_TABLE_OWNER_PID_LISTENER, 0)
    if size.value == 0:
        return []
    buf = ctypes.create_string_buffer(size.value)
    rc = get_table(buf, ctypes.byref(size), False, family, _TCP_TABLE_OWNER_PID_LISTENER, 0)
    if rc != 0:
        return []

    is_v6 = family == _AF_INET6
    # MIB_TCPROW_OWNER_PID / MIB_TCP6ROW_OWNER_PID layouts.
    if is_v6:

        class _Row(ctypes.Structure):
            _fields_ = [
                ("ucLocalAddr", ctypes.c_ubyte * 16),
                ("dwLocalScopeId", wt.DWORD),
                ("dwLocalPort", wt.DWORD),
                ("ucRemoteAddr", ctypes.c_ubyte * 16),
                ("dwRemoteScopeId", wt.DWORD),
                ("dwRemotePort", wt.DWORD),
                ("dwState", wt.DWORD),
                ("dwOwningPid", wt.DWORD),
            ]
    else:

        class _Row(ctypes.Structure):  # type: ignore[no-redef]
            _fields_ = [
                ("dwState", wt.DWORD),
                ("dwLocalAddr", wt.DWORD),
                ("dwLocalPort", wt.DWORD),
                ("dwRemoteAddr", wt.DWORD),
                ("dwRemotePort", wt.DWORD),
                ("dwOwningPid", wt.DWORD),
            ]

    num = ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0]
    rows_addr = ctypes.addressof(buf) + ctypes.sizeof(wt.DWORD)
    rows = (_Row * num).from_address(rows_addr)
    out: list[tuple[int, int]] = []
    for row in rows:
        # Local port is stored in network byte order in the low 16 bits.
        raw_port = int(row.dwLocalPort)
        port = ((raw_port & 0xFF) << 8) | ((raw_port >> 8) & 0xFF)
        if is_v6:
            addr_bytes = bytes(row.ucLocalAddr)
        else:
            addr_bytes = int(row.dwLocalAddr).to_bytes(4, "little")
        if _loopback_addr(family, addr_bytes) is None:
            continue
        out.append((port, int(row.dwOwningPid)))
    return out


def loopback_listener_pids(port: int) -> set[int]:
    """Return the PIDs listening on ``127.0.0.1:port`` / ``[::1]:port``.

    Empty when nothing loopback-listens on *port*, when the lookup fails, or
    off Windows. A non-empty result means "these processes own the port"; the
    caller treats an empty result as "cannot confirm", never as "safe".
    """
    if sys.platform != "win32":
        return set()
    pids: set[int] = set()
    for family in (_AF_INET, _AF_INET6):
        try:
            for found_port, pid in _query_table(family):
                if found_port == port and pid:
                    pids.add(pid)
        except Exception as exc:  # pragma: no cover - defensive
            _LOG.debug("loopback listener lookup (family=%d) failed: %s", family, exc)
    return pids


def port_owned_by(port: int, allowed_pids: set[int]) -> tuple[bool, set[int]]:
    """Return ``(is_owned, owners)`` for the loopback listeners on *port*.

    ``is_owned`` is True only when every loopback listener on the port is in
    *allowed_pids* and at least one listener was found. An empty owner set
    (lookup unavailable) yields ``(False, set())`` so the caller can decide
    whether to proceed on the weaker HTTP check alone.
    """
    owners = loopback_listener_pids(port)
    if not owners:
        return (False, owners)
    return (owners <= allowed_pids, owners)


def describe_owners(port: int) -> str:
    """Human-readable owner list for an error message."""
    owners = loopback_listener_pids(port)
    if not owners:
        return f"port {port}: no loopback listener found (or lookup unavailable)"
    return f"port {port} owned by PID(s) {sorted(owners)}"
