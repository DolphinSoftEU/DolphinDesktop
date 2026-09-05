"""Headless tests for the Qt agent RPC framing and the injection guards.

Nothing here injects into a real process: the pipe I/O primitives are
substituted so the response framing, id validation and architecture checks
can be exercised on their own.
"""


from __future__ import annotations

import ctypes
import gc
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from dolphin_desktop import _qt_inject
from dolphin_desktop._exceptions import DolphinError
from dolphin_desktop._qt_inject import (
    IMAGE_FILE_MACHINE_AMD64,
    IMAGE_FILE_MACHINE_I386,
    QtAgentClient,
    QtAgentInjectError,
    QtAgentRpcError,
    QtAgentTimeoutError,
)


class _FakeClient(QtAgentClient):
    """QtAgentClient with the two pipe primitives replaced by canned data."""

    def __init__(self, chunks, *, rpc_timeout: float = 30.0) -> None:
        super().__init__(4242, 0, rpc_timeout=rpc_timeout)
        self.written: list[bytes] = []
        self._chunks = list(chunks)

    def _write_line(self, line: bytes) -> None:
        self.written.append(line)

    def _read_chunk(self, deadline: float, op: str) -> bytes:
        if not self._chunks:
            raise QtAgentRpcError(f"no canned data left for op={op!r}")
        return self._chunks.pop(0)


class TestResponseFraming:
    def test_matching_id_returns_result(self):
        client = _FakeClient([b'{"id": 1, "ok": true, "result": "pong"}\n'])
        assert client.ping() == "pong"

    def test_response_split_across_chunks(self):
        client = _FakeClient([b'{"id": 1, "ok": tr', b'ue, "result": [1, 2]}', b"\n"])
        assert client.tree() == [1, 2]

    def test_two_responses_in_one_chunk_are_not_mixed(self):
        client = _FakeClient(
            [b'{"id": 1, "ok": true, "result": "a"}\n{"id": 2, "ok": true, "result": "b"}\n']
        )
        assert client.ping() == "a"
        # Second reply comes out of the buffered remainder, not a new read.
        assert client.ping() == "b"

    def test_agent_error_does_not_poison_the_connection(self):
        client = _FakeClient(
            [
                b'{"id": 1, "ok": false, "error": "no such handle"}\n',
                b'{"id": 2, "ok": true, "result": "pong"}\n',
            ]
        )
        with pytest.raises(QtAgentRpcError, match="no such handle"):
            client.ping()
        assert client.ping() == "pong"


class TestIdValidation:
    def test_mismatched_id_raises(self):
        client = _FakeClient([b'{"id": 7, "ok": true, "result": "other widget"}\n'])
        with pytest.raises(QtAgentRpcError, match="does not match request id 1"):
            client.ping()

    def test_missing_id_raises(self):
        client = _FakeClient([b'{"ok": true, "result": "x"}\n'])
        with pytest.raises(QtAgentRpcError, match="does not match request id"):
            client.ping()

    def test_desync_is_terminal(self):
        client = _FakeClient(
            [
                b'{"id": 99, "ok": true, "result": "stale"}\n',
                b'{"id": 2, "ok": true, "result": "pong"}\n',
            ]
        )
        with pytest.raises(QtAgentRpcError):
            client.ping()
        with pytest.raises(QtAgentRpcError, match="no longer usable"):
            client.ping()

    def test_request_carries_the_id_that_is_validated(self):
        client = _FakeClient([b'{"id": 1, "ok": true, "result": "pong"}\n'])
        client.ping()
        assert b'"id": 1' in client.written[0]
        assert client.written[0].endswith(b"\n")


class TestMalformedResponses:
    def test_invalid_json_raises_and_is_terminal(self):
        client = _FakeClient([b"not json at all\n", b'{"id": 2, "ok": true}\n'])
        with pytest.raises(QtAgentRpcError, match="malformed JSON reply"):
            client.ping()
        with pytest.raises(QtAgentRpcError, match="no longer usable"):
            client.ping()

    def test_non_object_json_is_rejected(self):
        client = _FakeClient([b"[1, 2, 3]\n"])
        with pytest.raises(QtAgentRpcError, match="does not match request id"):
            client.ping()

    def test_undecodable_bytes_raise(self):
        client = _FakeClient([b"\xff\xfe\n"])
        with pytest.raises(QtAgentRpcError, match="malformed JSON reply"):
            client.ping()


class _EndlessClient(_FakeClient):
    def _read_chunk(self, deadline: float, op: str) -> bytes:
        return b"x" * 16


class TestResponseSizeCap:
    def test_response_without_newline_is_capped(self, monkeypatch):
        monkeypatch.setattr(_qt_inject, "MAX_RESPONSE_BYTES", 64)
        client = _EndlessClient([])
        with pytest.raises(QtAgentRpcError, match="exceeded 64 bytes"):
            client.ping()

    def test_cap_is_terminal(self, monkeypatch):
        monkeypatch.setattr(_qt_inject, "MAX_RESPONSE_BYTES", 64)
        client = _EndlessClient([])
        with pytest.raises(QtAgentRpcError):
            client.ping()
        with pytest.raises(QtAgentRpcError, match="no longer usable"):
            client.ping()


class _PipeSim:
    """Scripted stand-in for the agent's end of the pipe.

    Feeds the *real* ``_read_chunk`` loop: anything not fed yet peeks as "no
    data available", which is exactly what drives the deadline path.
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.chunk_limit = 1 << 20
        self.on_peek = None
        self.peeks = 0
        self.bytes_read = 0

    def feed(self, data: bytes) -> None:
        self.buffer.extend(data)

    # kernel32 stand-ins — signatures match the call sites in _qt_inject.

    def peek(self, _handle, _buf, _size, _read, avail, _left):
        self.peeks += 1
        if self.on_peek is not None:
            self.on_peek(self)
        avail._obj.value = min(len(self.buffer), self.chunk_limit)
        return 1

    def read(self, _handle, buf, n, read, _overlapped):
        take = bytes(self.buffer[: min(n, self.chunk_limit)])
        del self.buffer[: len(take)]
        ctypes.memmove(buf, take, len(take))
        read._obj.value = len(take)
        self.bytes_read += len(take)
        return 1

    def close(self, _handle):
        return 1


class _WireClient(QtAgentClient):
    """Only the write half is stubbed — reads run the production loop."""

    def __init__(self, *, rpc_timeout: float = 30.0) -> None:
        super().__init__(4242, 5, rpc_timeout=rpc_timeout)
        self.written: list[dict] = []

    def _write_line(self, line: bytes) -> None:
        self.written.append(json.loads(line))


@pytest.fixture
def pipe_sim(monkeypatch):
    sim = _PipeSim()
    monkeypatch.setattr(_qt_inject, "_PeekNamedPipe", sim.peek)
    monkeypatch.setattr(_qt_inject, "_ReadFile", sim.read)
    monkeypatch.setattr(_qt_inject, "_CloseHandle", sim.close)
    return sim


def _reply(req_id: int, result: str = "pong") -> bytes:
    return json.dumps({"id": req_id, "ok": True, "result": result}).encode() + b"\n"


class TestReadLoop:
    def test_reply_is_reassembled_across_real_reads(self, pipe_sim):
        pipe_sim.chunk_limit = 8
        client = _WireClient()
        pipe_sim.feed(_reply(1))
        assert client.ping() == "pong"
        assert pipe_sim.bytes_read == len(_reply(1))

    def test_read_gives_up_at_the_deadline(self, pipe_sim):
        client = _WireClient(rpc_timeout=0.05)
        started = time.monotonic()
        with pytest.raises(QtAgentTimeoutError, match="timed out after"):
            client.ping()
        assert time.monotonic() - started >= 0.05
        assert pipe_sim.peeks > 1

    def test_deadline_is_per_request_not_per_chunk(self, pipe_sim):
        """A trickle of partial data must not push the deadline out forever."""

        def trickle(sim):
            if sim.peeks % 10 == 0:
                sim.feed(b"x")

        pipe_sim.on_peek = trickle
        client = _WireClient(rpc_timeout=0.1)
        with pytest.raises(QtAgentTimeoutError):
            client.ping()
        assert pipe_sim.bytes_read >= 1


class TestTimeoutIsRecoverable:
    def test_timeout_mid_message_keeps_the_stream_intact(self, pipe_sim):
        """A half-arrived reply must survive the timeout that interrupted it.

        Dropping the consumed prefix leaves the tail to parse as a malformed
        line on the next call, which poisons the connection permanently — the
        exact outcome ``QtAgentTimeoutError`` exists to avoid.
        """
        whole = _reply(1)
        head, tail = whole[:12], whole[12:]

        pipe_sim.feed(head)
        client = _WireClient(rpc_timeout=0.05)
        with pytest.raises(QtAgentTimeoutError):
            client.ping()
        assert client.is_broken is False

        pipe_sim.feed(tail)
        pipe_sim.feed(_reply(2))
        assert client.ping() == "pong"
        assert client.is_broken is False

    def test_timeout_does_not_disable_the_agent(self, pipe_sim):
        client = _WireClient(rpc_timeout=0.05)
        with pytest.raises(QtAgentTimeoutError):
            client.ping()
        assert client.is_broken is False
        assert client.broken_reason is None

    def test_late_reply_is_discarded_and_the_next_call_succeeds(self, pipe_sim):
        client = _WireClient(rpc_timeout=0.05)
        with pytest.raises(QtAgentTimeoutError):
            client.ping()
        # The blocked event loop wakes up: the abandoned reply lands first.
        pipe_sim.feed(_reply(1, "stale") + _reply(2, "pong"))
        assert client.ping() == "pong"
        assert client.is_broken is False

    def test_timeout_is_an_rpc_error_for_existing_handlers(self, pipe_sim):
        client = _WireClient(rpc_timeout=0.05)
        with pytest.raises(QtAgentRpcError):
            client.ping()

    def test_a_reply_id_that_was_never_sent_is_still_terminal(self, pipe_sim):
        client = _WireClient(rpc_timeout=0.05)
        with pytest.raises(QtAgentTimeoutError):
            client.ping()
        pipe_sim.feed(_reply(99))
        with pytest.raises(QtAgentRpcError, match="out of sync"):
            client.ping()
        assert client.is_broken is True


class TestBrokenTransportRecovery:
    def test_fail_closes_the_pipe_handle(self, monkeypatch):
        closed: list[int] = []
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: closed.append(h) or 1)
        client = QtAgentClient(4242, 77)
        client._fail("boom", "boom")
        assert closed == [77]
        assert client._pipe == 0

    def test_dropped_client_releases_the_handle(self, monkeypatch):
        closed: list[int] = []
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: closed.append(h) or 1)
        QtAgentClient(4242, 78)
        gc.collect()
        assert closed == [78]

    def test_reattach_reopens_the_pipe_and_clears_broken(self, monkeypatch, pipe_sim):
        monkeypatch.setattr(_qt_inject, "_open_pipe", lambda name, pid, timeout: 9)
        monkeypatch.setattr(_qt_inject, "_OpenProcess", lambda a, b, c: 555)
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: 1)
        client = _WireClient()
        client._fail("desync", "desync")
        assert client.is_broken is True
        client.reattach()
        assert client.is_broken is False
        assert client._pipe == 9
        pipe_sim.feed(_reply(1))
        assert client.ping() == "pong"

    def test_reattach_reports_an_unreachable_agent(self, monkeypatch):
        def _boom(name, pid, timeout):
            raise QtAgentInjectError(f"timed out connecting to {name}")

        monkeypatch.setattr(_qt_inject, "_open_pipe", _boom)
        monkeypatch.setattr(_qt_inject, "_OpenProcess", lambda a, b, c: 555)
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: 1)
        client = QtAgentClient(4242, 5)
        with pytest.raises(QtAgentInjectError, match="timed out connecting"):
            client.reattach()

    def test_reattach_pins_the_pid_so_a_recycled_process_is_refused(self, monkeypatch):
        """Without an open handle Windows may hand this pid to another process.

        That process may itself be dolphin-injected, so its pipe passes
        ``_verify_pipe_server`` and the stale client would silently drive a
        different application.
        """
        opened: list[int] = []
        monkeypatch.setattr(_qt_inject, "_OpenProcess", lambda a, b, pid: opened.append(pid) or 0)
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: 1)
        monkeypatch.setattr(
            _qt_inject,
            "_open_pipe",
            lambda *a: pytest.fail("must not touch the pipe once the pid is gone"),
        )
        client = QtAgentClient(4242, 5)
        with pytest.raises(QtAgentInjectError, match="most likely exited"):
            client.reattach()
        assert opened == [4242]

    def test_broken_client_names_the_recovery_path(self):
        client = _FakeClient([b'{"id": 99, "ok": true, "result": "stale"}\n'])
        with pytest.raises(QtAgentRpcError):
            client.ping()
        with pytest.raises(QtAgentRpcError, match="reattach"):
            client.ping()


class _AlwaysTimingOutClient(_FakeClient):
    def _read_chunk(self, deadline: float, op: str) -> bytes:
        raise QtAgentTimeoutError("the agent's event loop is wedged")


class TestAbandonedIdsAreBounded:
    """A wedged agent produces one abandoned id per call, forever."""

    def _wedge(self, monkeypatch, calls: int) -> QtAgentClient:
        monkeypatch.setattr(_qt_inject, "MAX_ABANDONED_IDS", 8)
        client = _AlwaysTimingOutClient([])
        for _ in range(calls):
            with pytest.raises(QtAgentTimeoutError):
                client.ping()
        return client

    def test_the_set_stops_growing_at_the_cap(self, monkeypatch):
        client = self._wedge(monkeypatch, 50)
        assert len(client._abandoned) == 8

    def test_the_ids_kept_are_the_most_recent_ones(self, monkeypatch):
        client = self._wedge(monkeypatch, 50)
        assert client._abandoned == set(range(43, 51))


class _BlockingWriteSim:
    """A pipe whose server stopped draining: WriteFile never returns on its own."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.aborted = threading.Event()
        self.cancelled: list[int] = []

    def write(self, _handle, _buf, _n, written, _overlapped):
        self.entered.set()
        self.aborted.wait(10)
        written._obj.value = 0
        return 0

    def cancel(self, handle, _overlapped):
        self.cancelled.append(handle)
        self.aborted.set()
        return 1


@pytest.fixture
def blocked_write(monkeypatch):
    sim = _BlockingWriteSim()
    monkeypatch.setattr(_qt_inject, "_WriteFile", sim.write)
    monkeypatch.setattr(_qt_inject, "_CancelIoEx", sim.cancel)
    monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: 1)
    return sim


class TestRecoveryPreemptsABlockedWrite:
    """``reattach``/``close`` are the advertised recovery from a wedged agent.

    A request larger than the pipe's input buffer parks inside ``_send`` until
    the agent drains it, so recovery that needs the request lock — or that
    leaves the handle's I/O running — can never arrive.
    """

    def _wedged_send(self, client, sim):
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                client.ping()
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        assert sim.entered.wait(5), "the write never blocked"
        return thread, errors

    def test_close_does_not_queue_behind_the_blocked_write(self, blocked_write):
        client = QtAgentClient(4242, 71)
        thread, errors = self._wedged_send(client, blocked_write)
        started = time.monotonic()
        client.close()
        elapsed = time.monotonic() - started
        thread.join(timeout=5)
        assert elapsed < 2.0
        assert blocked_write.cancelled == [71]
        assert client._pipe == 0
        assert errors and isinstance(errors[0], QtAgentRpcError)

    def test_reattach_does_not_queue_behind_the_blocked_write(self, blocked_write, monkeypatch):
        monkeypatch.setattr(_qt_inject, "_OpenProcess", lambda a, b, c: 555)
        monkeypatch.setattr(_qt_inject, "_open_pipe", lambda name, pid, timeout: 9)
        client = QtAgentClient(4242, 71)
        thread, errors = self._wedged_send(client, blocked_write)
        started = time.monotonic()
        client.reattach()
        elapsed = time.monotonic() - started
        thread.join(timeout=5)
        assert elapsed < 2.0
        assert blocked_write.cancelled == [71]
        assert client._pipe == 9
        assert client.is_broken is False
        assert errors and isinstance(errors[0], QtAgentRpcError)


class _ConcurrencyProbe(QtAgentClient):
    """Records any two ``_send`` bodies that are ever in flight together."""

    def __init__(self) -> None:
        super().__init__(4242, 0, rpc_timeout=5.0)
        self.overlaps = 0
        self.ids: list[int] = []
        self._inflight = 0
        self._probe_lock = threading.Lock()
        self._reply_for = 0

    def _write_line(self, line: bytes) -> None:
        req = json.loads(line)
        with self._probe_lock:
            self._inflight += 1
            if self._inflight > 1:
                self.overlaps += 1
            self.ids.append(req["id"])
        self._reply_for = req["id"]
        time.sleep(0.002)

    def _read_chunk(self, deadline: float, op: str) -> bytes:
        time.sleep(0.002)
        reply = _reply(self._reply_for)
        with self._probe_lock:
            self._inflight -= 1
        return reply


class TestSharedClientLocking:
    def test_send_serialises_across_threads(self):
        client = _ConcurrencyProbe()
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                for _ in range(10):
                    client.ping()
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not errors, errors
        assert client.overlaps == 0
        assert client.ids == sorted(client.ids)
        assert len(set(client.ids)) == 60
        assert client.is_broken is False


class TestExceptionTaxonomy:
    def test_agent_errors_are_dolphin_errors(self):
        assert issubclass(QtAgentInjectError, DolphinError)
        assert issubclass(QtAgentRpcError, DolphinError)

    def test_agent_errors_stay_runtime_errors(self):
        assert issubclass(QtAgentInjectError, RuntimeError)
        assert issubclass(QtAgentRpcError, RuntimeError)

    def test_timeout_is_a_narrower_rpc_error(self):
        assert issubclass(QtAgentTimeoutError, QtAgentRpcError)

    def test_hint_kwarg_is_accepted(self):
        exc = QtAgentInjectError("nope", hint="try again")
        assert "try again" in str(exc)


def _write_fake_pe(path, machine: int) -> None:
    """Write the smallest PE prefix ``_pe_machine`` needs."""
    data = bytearray(b"\x00" * 0x46)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x40).to_bytes(4, "little")
    data[0x40:0x44] = b"PE\x00\x00"
    data[0x44:0x46] = machine.to_bytes(2, "little")
    path.write_bytes(bytes(data))


class TestArchitectureGuard:
    def test_shipped_agent_dlls_are_amd64(self):
        from dolphin_desktop._qt_agent import QT5_AGENT_DLL, QT6_AGENT_DLL

        for dll in (QT5_AGENT_DLL, QT6_AGENT_DLL):
            if not dll.is_file():
                pytest.skip(f"{dll.name} not built in this tree")
            assert _qt_inject._pe_machine(dll) == IMAGE_FILE_MACHINE_AMD64

    def test_pe_machine_reads_the_coff_header(self, tmp_path):
        dll = tmp_path / "fake_x86.dll"
        _write_fake_pe(dll, IMAGE_FILE_MACHINE_I386)
        assert _qt_inject._pe_machine(dll) == IMAGE_FILE_MACHINE_I386

    def test_non_pe_file_is_rejected(self, tmp_path):
        dll = tmp_path / "not_a_dll.txt"
        dll.write_bytes(b"hello")
        with pytest.raises(QtAgentInjectError, match="not a PE file"):
            _qt_inject._pe_machine(dll)

    def test_mismatched_dll_arch_refuses_injection(self, tmp_path):
        dll = tmp_path / "dolphin_qt6_agent.dll"
        _write_fake_pe(dll, IMAGE_FILE_MACHINE_I386)
        hproc = _qt_inject._GetCurrentProcess()
        with pytest.raises(QtAgentInjectError, match="architecture mismatch"):
            _qt_inject._require_matching_arch(hproc, 4242, dll)

    def test_matching_arch_is_allowed(self, tmp_path):
        host = _qt_inject._process_machine(_qt_inject._GetCurrentProcess())
        dll = tmp_path / "dolphin_qt6_agent.dll"
        _write_fake_pe(dll, host)
        # The guard clears the way by returning: target, DLL and host all match.
        assert _qt_inject._require_matching_arch(_qt_inject._GetCurrentProcess(), 4242, dll) is None


def _pipe_server_pid(server_pid: int, *, ok: int = 1):
    def _impl(_handle, out):
        out._obj.value = server_pid
        return ok

    return _impl


class TestPipeServerVerification:
    """SECURITY.md advertises this check: the pipe name is guessable, the
    server pid is not forgeable."""

    def test_matching_server_pid_is_accepted(self, monkeypatch):
        monkeypatch.setattr(_qt_inject, "_GetNamedPipeServerProcessId", _pipe_server_pid(4242))
        # The pipe is served by the injected pid, so the check returns and the
        # caller keeps the handle it opened.
        assert _qt_inject._verify_pipe_server(5, r"\\.\pipe\dolphin_qt_4242", 4242) is None

    def test_squatted_pipe_is_refused(self, monkeypatch):
        monkeypatch.setattr(_qt_inject, "_GetNamedPipeServerProcessId", _pipe_server_pid(1337))
        with pytest.raises(QtAgentInjectError, match="served by pid 1337"):
            _qt_inject._verify_pipe_server(5, r"\\.\pipe\dolphin_qt_4242", 4242)

    def test_unidentifiable_server_is_refused(self, monkeypatch):
        monkeypatch.setattr(_qt_inject, "_GetNamedPipeServerProcessId", _pipe_server_pid(0, ok=0))
        with pytest.raises(QtAgentInjectError, match="refusing to trust"):
            _qt_inject._verify_pipe_server(5, r"\\.\pipe\dolphin_qt_4242", 4242)

    def test_the_pipe_is_opened_at_identification_level(self, monkeypatch):
        """The pid check refuses a squatted pipe, but only after CreateFileW —
        by which point an impersonation-level connection has already handed the
        squatter a token it can call ImpersonateNamedPipeClient() with."""
        seen: list[tuple] = []
        monkeypatch.setattr(_qt_inject, "_CreateFileW", lambda *a: seen.append(a) or 61)
        monkeypatch.setattr(_qt_inject, "_GetNamedPipeServerProcessId", _pipe_server_pid(4242))
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: 1)
        assert _qt_inject._open_pipe(r"\\.\pipe\dolphin_qt_4242", 4242) == 61
        flags = seen[0][5]
        assert flags & _qt_inject.SECURITY_SQOS_PRESENT
        assert flags & 0x00030000 == _qt_inject.SECURITY_IDENTIFICATION

    def test_open_pipe_closes_a_rejected_handle(self, monkeypatch):
        closed: list[int] = []
        monkeypatch.setattr(_qt_inject, "_CreateFileW", lambda *a: 61)
        monkeypatch.setattr(_qt_inject, "_GetNamedPipeServerProcessId", _pipe_server_pid(1337))
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: closed.append(h) or 1)
        with pytest.raises(QtAgentInjectError, match="served by pid 1337"):
            _qt_inject._open_pipe(r"\\.\pipe\dolphin_qt_4242", 4242, timeout_s=0.1)
        assert closed == [61]


class _Win32Sim:
    """kernel32 stand-ins for the two remote-thread call sites."""

    def __init__(self, *, wait_rc: int = 0, thread: int = 7, exit_code: int = 1) -> None:
        self.wait_rc = wait_rc
        self.thread = thread
        self.exit_code = exit_code
        self.write_ok = True
        self.freed: list[tuple[int, int]] = []

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr(_qt_inject, "_OpenProcess", lambda *a: 1)
        monkeypatch.setattr(_qt_inject, "_VirtualAllocEx", lambda *a: 0x1000)
        monkeypatch.setattr(_qt_inject, "_GetModuleHandleW", lambda name: 1)
        monkeypatch.setattr(_qt_inject, "_GetProcAddress", lambda h, n: 2)
        monkeypatch.setattr(_qt_inject, "_CloseHandle", lambda h: 1)
        monkeypatch.setattr(_qt_inject, "_WriteProcessMemory", self.write)
        monkeypatch.setattr(_qt_inject, "_CreateRemoteThread", self.create_thread)
        monkeypatch.setattr(_qt_inject, "_WaitForSingleObject", lambda h, ms: self.wait_rc)
        monkeypatch.setattr(_qt_inject, "_GetExitCodeThread", self.get_exit_code)
        monkeypatch.setattr(_qt_inject, "_VirtualFreeEx", self.free)

    def write(self, _hproc, _addr, _buf, size, written):
        written._obj.value = size if self.write_ok else 0
        return 1 if self.write_ok else 0

    def create_thread(self, _hproc, _sec, _stack, _start, _param, _flags, tid):
        return self.thread

    def get_exit_code(self, _hthread, out):
        out._obj.value = self.exit_code
        return 1

    def free(self, _hproc, addr, size, _flags):
        self.freed.append((addr, size))
        return 1


class TestInjectionAllocationLifetime:
    """The remote thread reads the allocation this code owns — freeing it while
    that thread still runs faults the AUT."""

    def _inject(self, monkeypatch, sim, tmp_path):
        monkeypatch.setattr(_qt_inject, "_require_matching_arch", lambda *a: None)
        sim.install(monkeypatch)
        _qt_inject._inject_dll(4242, tmp_path / "dolphin_qt6_agent.dll")

    def test_allocation_is_freed_once_the_thread_has_exited(self, monkeypatch, tmp_path):
        sim = _Win32Sim()
        self._inject(monkeypatch, sim, tmp_path)
        assert sim.freed == [(0x1000, 0)]

    def test_allocation_is_kept_when_the_thread_times_out(self, monkeypatch, tmp_path):
        sim = _Win32Sim(wait_rc=258)
        monkeypatch.setattr(_qt_inject, "_require_matching_arch", lambda *a: None)
        sim.install(monkeypatch)
        with pytest.raises(QtAgentInjectError, match="timed out"):
            _qt_inject._inject_dll(4242, tmp_path / "dolphin_qt6_agent.dll")
        assert sim.freed == []

    def test_allocation_is_freed_when_no_thread_was_created(self, monkeypatch, tmp_path):
        sim = _Win32Sim(thread=0)
        monkeypatch.setattr(_qt_inject, "_require_matching_arch", lambda *a: None)
        sim.install(monkeypatch)
        with pytest.raises(QtAgentInjectError, match="CreateRemoteThread failed"):
            _qt_inject._inject_dll(4242, tmp_path / "dolphin_qt6_agent.dll")
        assert sim.freed == [(0x1000, 0)]

    def test_loadlibrary_returning_null_is_reported(self, monkeypatch, tmp_path):
        sim = _Win32Sim(exit_code=0)
        monkeypatch.setattr(_qt_inject, "_require_matching_arch", lambda *a: None)
        sim.install(monkeypatch)
        with pytest.raises(QtAgentInjectError, match="LoadLibraryW returned NULL"):
            _qt_inject._inject_dll(4242, tmp_path / "dolphin_qt6_agent.dll")


def _install_pywin32(monkeypatch, dll_path, *, open_process=None):
    win32api = types.SimpleNamespace(
        OpenProcess=open_process or (lambda *a: 11),
        CloseHandle=lambda h: None,
    )
    win32process = types.SimpleNamespace(
        EnumProcessModulesEx=lambda h, f: [0x4000],
        EnumProcessModules=lambda h: [0x4000],
        GetModuleFileNameEx=lambda h, m: str(dll_path),
    )
    monkeypatch.setitem(sys.modules, "win32api", win32api)
    monkeypatch.setitem(sys.modules, "win32process", win32process)


class TestStartAgentAllocationLifetime:
    def _install_pywin32(self, monkeypatch, dll_path, *, open_process=None):
        _install_pywin32(monkeypatch, dll_path, open_process=open_process)

    def _start(self, monkeypatch, sim, dll_path):
        monkeypatch.setattr(_qt_inject, "_resolve_export_rva", lambda p, n: 0x100)
        self._install_pywin32(monkeypatch, dll_path)
        sim.install(monkeypatch)
        _qt_inject._start_agent(4242, dll_path, r"\\.\pipe\dolphin_qt_4242")

    def test_pipe_name_buffer_outlives_a_successful_start(self, monkeypatch, tmp_path):
        """Deliberate: nothing proves the agent copied the string rather than
        retaining the pointer, and its pipe server outlives our connection."""
        sim = _Win32Sim(exit_code=0)
        self._start(monkeypatch, sim, tmp_path / "dolphin_qt6_agent.dll")
        assert sim.freed == []

    def test_pipe_name_buffer_is_kept_when_the_thread_times_out(self, monkeypatch, tmp_path):
        sim = _Win32Sim(wait_rc=258, exit_code=0)
        monkeypatch.setattr(_qt_inject, "_resolve_export_rva", lambda p, n: 0x100)
        dll = tmp_path / "dolphin_qt6_agent.dll"
        self._install_pywin32(monkeypatch, dll)
        sim.install(monkeypatch)
        with pytest.raises(QtAgentInjectError, match="timed out"):
            _qt_inject._start_agent(4242, dll, r"\\.\pipe\dolphin_qt_4242")
        assert sim.freed == []

    def test_pipe_name_buffer_is_freed_when_no_thread_was_created(self, monkeypatch, tmp_path):
        sim = _Win32Sim(thread=0, exit_code=0)
        monkeypatch.setattr(_qt_inject, "_resolve_export_rva", lambda p, n: 0x100)
        dll = tmp_path / "dolphin_qt6_agent.dll"
        self._install_pywin32(monkeypatch, dll)
        sim.install(monkeypatch)
        with pytest.raises(QtAgentInjectError, match="CreateRemoteThread\\(start\\) failed"):
            _qt_inject._start_agent(4242, dll, r"\\.\pipe\dolphin_qt_4242")
        assert sim.freed == [(0x1000, 0)]

    def test_open_process_failure_is_wrapped(self, monkeypatch, tmp_path):
        class _FakePywintypesError(Exception):
            pass

        def _boom(*_a):
            raise _FakePywintypesError("(5, 'OpenProcess', 'Access is denied.')")

        dll = tmp_path / "dolphin_qt6_agent.dll"
        monkeypatch.setattr(_qt_inject, "_resolve_export_rva", lambda p, n: 0x100)
        self._install_pywin32(monkeypatch, dll, open_process=_boom)
        with pytest.raises(QtAgentInjectError, match="Access is denied"):
            _qt_inject._start_agent(4242, dll, r"\\.\pipe\dolphin_qt_4242")

    def test_missing_module_base_is_reported(self, monkeypatch, tmp_path):
        dll = tmp_path / "dolphin_qt6_agent.dll"
        monkeypatch.setattr(_qt_inject, "_resolve_export_rva", lambda p, n: 0x100)
        self._install_pywin32(monkeypatch, tmp_path / "something_else.dll")
        with pytest.raises(QtAgentInjectError, match="not visible in target"):
            _qt_inject._start_agent(4242, dll, r"\\.\pipe\dolphin_qt_4242")


class TestStartAgentExitCode:
    """``dolphin_qt_agent_start`` reports success as 0, so an unread exit code
    reads as a started agent — unlike the LoadLibraryW site, where it fails safe."""

    def test_an_unreadable_exit_code_is_not_taken_for_success(self, monkeypatch, tmp_path):
        dll = tmp_path / "dolphin_qt6_agent.dll"
        monkeypatch.setattr(_qt_inject, "_resolve_export_rva", lambda p, n: 0x100)
        _install_pywin32(monkeypatch, dll)
        _Win32Sim(exit_code=0).install(monkeypatch)
        monkeypatch.setattr(_qt_inject, "_GetExitCodeThread", lambda h, out: 0)
        with pytest.raises(QtAgentInjectError, match="GetExitCodeThread failed"):
            _qt_inject._start_agent(4242, dll, r"\\.\pipe\dolphin_qt_4242")


def _pe_with_exports(names: list[bytes], *, terminate: bool = True) -> bytes:
    """Smallest PE whose export table ``_resolve_export_rva`` can walk.

    One section mapped at RVA 0 with raw offset 0, so RVA == file offset.
    """
    export_dir, funcs, name_ptrs, ordinals, strings = 0x200, 0x300, 0x320, 0x340, 0x360
    size = 0x400 + sum(len(n) + 1 for n in names)
    data = bytearray(size)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = (0x40).to_bytes(4, "little")
    data[0x40:0x44] = b"PE\x00\x00"
    coff = 0x44
    data[coff : coff + 2] = IMAGE_FILE_MACHINE_AMD64.to_bytes(2, "little")
    data[coff + 2 : coff + 4] = (1).to_bytes(2, "little")
    opt_size = 0xF0
    data[coff + 16 : coff + 18] = opt_size.to_bytes(2, "little")
    opt = coff + 20
    data[opt : opt + 2] = b"\x0b\x02"  # PE32+
    data[opt + 112 : opt + 116] = export_dir.to_bytes(4, "little")

    sec = opt + opt_size
    data[sec : sec + 8] = b".text\x00\x00\x00"
    data[sec + 8 : sec + 12] = size.to_bytes(4, "little")  # VirtualSize
    data[sec + 12 : sec + 16] = (0).to_bytes(4, "little")  # VirtualAddress
    data[sec + 16 : sec + 20] = size.to_bytes(4, "little")  # SizeOfRawData
    data[sec + 20 : sec + 24] = (0).to_bytes(4, "little")  # PointerToRawData

    # NumberOfFunctions and NumberOfNames both equal len(names) here: every
    # export in this fixture is named. They are separate fields because a real
    # DLL can export by ordinal only, and the parser bounds-checks each name's
    # ordinal against NumberOfFunctions.
    data[export_dir + 20 : export_dir + 24] = len(names).to_bytes(4, "little")
    data[export_dir + 24 : export_dir + 28] = len(names).to_bytes(4, "little")
    data[export_dir + 28 : export_dir + 32] = funcs.to_bytes(4, "little")
    data[export_dir + 32 : export_dir + 36] = name_ptrs.to_bytes(4, "little")
    data[export_dir + 36 : export_dir + 40] = ordinals.to_bytes(4, "little")

    off = strings
    for i, name in enumerate(names):
        data[funcs + i * 4 : funcs + i * 4 + 4] = (0x1000 + i).to_bytes(4, "little")
        data[name_ptrs + i * 4 : name_ptrs + i * 4 + 4] = off.to_bytes(4, "little")
        data[ordinals + i * 2 : ordinals + i * 2 + 2] = i.to_bytes(2, "little")
        data[off : off + len(name)] = name
        off += len(name) + 1
    if not terminate:
        del data[off - 1 :]
    return bytes(data)


class TestExportTableParsing:
    def test_a_named_export_resolves_to_its_rva(self, tmp_path):
        dll = tmp_path / "dolphin_qt6_agent.dll"
        dll.write_bytes(_pe_with_exports([b"other", b"dolphin_qt_agent_start"]))
        assert _qt_inject._resolve_export_rva(dll, b"dolphin_qt_agent_start") == 0x1001

    def test_a_missing_export_is_reported(self, tmp_path):
        dll = tmp_path / "dolphin_qt6_agent.dll"
        dll.write_bytes(_pe_with_exports([b"other"]))
        with pytest.raises(QtAgentInjectError, match="not found"):
            _qt_inject._resolve_export_rva(dll, b"dolphin_qt_agent_start")

    def test_a_truncated_name_table_stays_a_dolphin_error(self, tmp_path):
        """A short read must not surface as a bare ValueError from bytes.index."""
        dll = tmp_path / "dolphin_qt6_agent.dll"
        dll.write_bytes(_pe_with_exports([b"other"], terminate=False))
        with pytest.raises(QtAgentInjectError, match="unterminated export name"):
            _qt_inject._resolve_export_rva(dll, b"dolphin_qt_agent_start")


def test_qt_pe_header_parser_accepts_valid_and_rejects_invalid_files(tmp_path: Path) -> None:
    from dolphin_desktop._qt_inject import IMAGE_FILE_MACHINE_AMD64, QtAgentInjectError, _pe_machine

    valid = tmp_path / "agent.dll"
    dos = bytearray(0x40)
    dos[0:2] = b"MZ"
    dos[0x3C:0x40] = (0x40).to_bytes(4, "little")
    valid.write_bytes(bytes(dos) + b"PE\0\0" + IMAGE_FILE_MACHINE_AMD64.to_bytes(2, "little"))
    assert _pe_machine(valid) == IMAGE_FILE_MACHINE_AMD64
    invalid = tmp_path / "invalid.dll"
    invalid.write_bytes(b"not-a-pe")
    with pytest.raises(QtAgentInjectError, match="not a PE file"):
        _pe_machine(invalid)


def test_qt_inject_machine_names_cover_known_and_unknown_values() -> None:
    from dolphin_desktop._qt_inject import _machine_name

    assert _machine_name(0x8664) == "x64 (64-bit)"
    assert _machine_name(123) == "IMAGE_FILE_MACHINE_0x7b"
