"""Tests for :mod:`dolphin_desktop._netinfo`.

``_netinfo`` answers "which PID(s) own a loopback-listening TCP port" via the
Windows ``GetExtendedTcpTable`` API, so a launcher can refuse to trust a CDP
endpoint it did not spawn (KAN-475). These tests never touch the real IP
Helper API: ``ctypes.WinDLL`` is faked with a Python callable that mimics the
two-call sizing protocol and writes real ``MIB_TCPROW_OWNER_PID`` /
``MIB_TCP6ROW_OWNER_PID`` bytes into the caller's buffer, so the structure
parsing / byte-order logic in ``_query_table`` runs for real.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import socket
import struct
import sys

import pytest

from dolphin_desktop import _netinfo


def _swap_port(port: int) -> int:
    """Undo/apply the network-byte-order swap ``_query_table`` performs."""
    return ((port & 0xFF) << 8) | ((port >> 8) & 0xFF)


def _v4_row(port: int, pid: int, addr: str = "127.0.0.1") -> bytes:
    addr_dword = int.from_bytes(socket.inet_aton(addr), "little")
    # dwState, dwLocalAddr, dwLocalPort, dwRemoteAddr, dwRemotePort, dwOwningPid
    return struct.pack("<IIIIII", 0, addr_dword, _swap_port(port), 0, 0, pid)


def _v6_row(port: int, pid: int, addr: bytes = b"\x00" * 15 + b"\x01") -> bytes:
    assert len(addr) == 16
    # ucLocalAddr[16], dwLocalScopeId, dwLocalPort, ucRemoteAddr[16],
    # dwRemoteScopeId, dwRemotePort, dwState, dwOwningPid
    return struct.pack(
        "<16sII16sIIII",
        addr,
        0,
        _swap_port(port),
        b"\x00" * 16,
        0,
        0,
        0,
        pid,
    )


def _payload(rows: list[bytes]) -> bytes:
    return struct.pack("<I", len(rows)) + b"".join(rows)


class _FakeGetTable:
    """Stand-in for the bound ``GetExtendedTcpTable`` foreign function.

    ``_query_table`` assigns ``.argtypes``/``.restype`` on whatever
    ``iphlpapi.GetExtendedTcpTable`` returns, so this must be a plain object
    (not a bound method) that accepts arbitrary attribute assignment.
    """

    def __init__(self, first_size: int, rc: int, payload: bytes = b"") -> None:
        self.first_size = first_size
        self.rc = rc
        self.payload = payload
        self.calls: list[tuple] = []

    def __call__(self, buf, size_ref, order, family, table_class, reserved):
        self.calls.append((buf, order, family, table_class, reserved))
        size_ptr = ctypes.cast(size_ref, ctypes.POINTER(wt.DWORD))
        if buf is None:
            size_ptr.contents.value = self.first_size
            return 0
        size_ptr.contents.value = len(self.payload)
        if self.rc == 0 and self.payload:
            ctypes.memmove(buf, self.payload, len(self.payload))
        return self.rc


class _FakeDll:
    def __init__(self, get_table: _FakeGetTable) -> None:
        self.GetExtendedTcpTable = get_table


def _patch_windll(monkeypatch: pytest.MonkeyPatch, get_table: _FakeGetTable) -> None:
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=True: _FakeDll(get_table))


# --------------------------------------------------------------------------- #
# _loopback_addr                                                              #
# --------------------------------------------------------------------------- #


def test_loopback_addr_v4_accepts_127_and_rejects_routable() -> None:
    assert _netinfo._loopback_addr(_netinfo._AF_INET, socket.inet_aton("127.0.0.1")) == "127.0.0.1"
    assert _netinfo._loopback_addr(_netinfo._AF_INET, socket.inet_aton("10.0.0.5")) is None


def test_loopback_addr_v6_accepts_colon_colon_1() -> None:
    packed = socket.inet_pton(socket.AF_INET6, "::1")
    assert _netinfo._loopback_addr(_netinfo._AF_INET6, packed) == "::1"


def test_loopback_addr_v6_accepts_mapped_loopback_forms() -> None:
    mapped = socket.inet_pton(socket.AF_INET6, "::ffff:127.0.0.1")
    assert _netinfo._loopback_addr(_netinfo._AF_INET6, mapped) == "::ffff:127.0.0.1"


def test_loopback_addr_v6_rejects_a_routable_address() -> None:
    packed = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
    assert _netinfo._loopback_addr(_netinfo._AF_INET6, packed) is None


def test_loopback_addr_swallows_bad_input() -> None:
    # Too short to be a valid address: inet_ntop raises, and the function
    # must translate that into "not loopback" rather than propagate.
    assert _netinfo._loopback_addr(_netinfo._AF_INET, b"\x01\x02") is None
    assert _netinfo._loopback_addr(_netinfo._AF_INET6, b"\x01\x02") is None


# --------------------------------------------------------------------------- #
# _query_table                                                                #
# --------------------------------------------------------------------------- #


def test_query_table_returns_empty_when_sizing_call_reports_zero(monkeypatch) -> None:
    _patch_windll(monkeypatch, _FakeGetTable(first_size=0, rc=0))
    assert _netinfo._query_table(_netinfo._AF_INET) == []


def test_query_table_returns_empty_when_second_call_fails(monkeypatch) -> None:
    _patch_windll(monkeypatch, _FakeGetTable(first_size=64, rc=1))
    assert _netinfo._query_table(_netinfo._AF_INET) == []


def test_query_table_parses_v4_rows_and_filters_non_loopback(monkeypatch) -> None:
    rows = [
        _v4_row(port=8080, pid=111, addr="127.0.0.1"),
        _v4_row(port=9090, pid=222, addr="10.1.2.3"),
    ]
    _patch_windll(monkeypatch, _FakeGetTable(first_size=64, rc=0, payload=_payload(rows)))
    result = _netinfo._query_table(_netinfo._AF_INET)
    assert result == [(8080, 111)]


def test_query_table_parses_v6_rows_and_filters_non_loopback(monkeypatch) -> None:
    routable = socket.inet_pton(socket.AF_INET6, "2001:db8::1")
    rows = [
        _v6_row(port=9222, pid=333),
        _v6_row(port=9223, pid=444, addr=routable),
    ]
    _patch_windll(monkeypatch, _FakeGetTable(first_size=128, rc=0, payload=_payload(rows)))
    result = _netinfo._query_table(_netinfo._AF_INET6)
    assert result == [(9222, 333)]


# --------------------------------------------------------------------------- #
# loopback_listener_pids                                                      #
# --------------------------------------------------------------------------- #


def test_loopback_listener_pids_returns_empty_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(_netinfo.sys, "platform", "linux")
    assert _netinfo.loopback_listener_pids(9222) == set()


def test_loopback_listener_pids_aggregates_matching_ports_across_families(monkeypatch) -> None:
    monkeypatch.setattr(_netinfo.sys, "platform", "win32")

    def fake_query_table(family: int) -> list[tuple[int, int]]:
        if family == _netinfo._AF_INET:
            return [(9222, 100), (9223, 200)]
        return [(9222, 300), (9224, 400)]

    monkeypatch.setattr(_netinfo, "_query_table", fake_query_table)
    assert _netinfo.loopback_listener_pids(9222) == {100, 300}


def test_loopback_listener_pids_ignores_a_pid_of_zero(monkeypatch) -> None:
    monkeypatch.setattr(_netinfo.sys, "platform", "win32")
    monkeypatch.setattr(_netinfo, "_query_table", lambda family: [(9222, 0)])
    assert _netinfo.loopback_listener_pids(9222) == set()


def test_loopback_listener_pids_survives_a_lookup_failure(monkeypatch) -> None:
    """A raising family lookup must not blow up the caller (best-effort API)."""
    monkeypatch.setattr(_netinfo.sys, "platform", "win32")

    def boom(family: int) -> list[tuple[int, int]]:
        raise OSError("iphlpapi unavailable")

    monkeypatch.setattr(_netinfo, "_query_table", boom)
    assert _netinfo.loopback_listener_pids(9222) == set()


# --------------------------------------------------------------------------- #
# port_owned_by / describe_owners                                             #
# --------------------------------------------------------------------------- #


def test_describe_owners_reports_no_listener_when_lookup_is_empty(monkeypatch) -> None:
    monkeypatch.setattr(_netinfo, "loopback_listener_pids", lambda port: set())
    message = _netinfo.describe_owners(9222)
    assert "no loopback listener found" in message


def test_describe_owners_lists_sorted_pids_when_found(monkeypatch) -> None:
    monkeypatch.setattr(_netinfo, "loopback_listener_pids", lambda port: {300, 100, 200})
    message = _netinfo.describe_owners(9222)
    assert message == "port 9222 owned by PID(s) [100, 200, 300]"


def test_sys_platform_is_win32_on_this_runner() -> None:
    # Sanity check that the module's real platform gate matches this CI/dev
    # runner, so the mocked-platform tests above are exercising a genuine
    # branch flip rather than a no-op.
    assert sys.platform == "win32"
