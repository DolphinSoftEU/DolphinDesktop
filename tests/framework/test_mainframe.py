"""Tests for the non-UI paths in :mod:`dolphin_desktop._mainframe`.

The real mainframe backends talk to an emulator, a DLL, or a socket.  These
tests keep the suite headless by replacing those boundaries with tiny fakes
and exercise the protocol parsers and the public facade directly.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import dolphin_desktop._mainframe as mf
from dolphin_desktop._mainframe import AID, FieldInfo, TerminalField, TerminalScreen


def _field(mainframe, row, col, length, *, protected=False):
    return mainframe.FieldInfo(
        row,
        col,
        length,
        protected=protected,
        hidden=False,
        numeric=False,
        modified=False,
        attr_byte=0,
    )


class _Pipe:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.lines = list(lines or [])
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""


class _Process:
    def __init__(self, lines: list[bytes] | None = None) -> None:
        self.stdin = _Pipe()
        self.stdout = _Pipe(lines)
        self.stderr = _Pipe()
        self.wait_calls: list[float] = []
        self.killed = False
        self.poll_value: int | None = None

    def poll(self) -> int | None:
        return self.poll_value

    def wait(self, timeout: float) -> None:
        self.wait_calls.append(timeout)

    def kill(self) -> None:
        self.killed = True


class _Socket:
    def __init__(self, chunks: list[bytes | BaseException] | None = None) -> None:
        self.chunks = list(chunks or [])
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []
        self.shutdown_calls: list[int] = []
        self.closed = False

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def recv(self, size: int) -> bytes:
        del size
        if not self.chunks:
            raise TimeoutError()
        chunk = self.chunks.pop(0)
        if isinstance(chunk, BaseException):
            raise chunk
        return chunk

    def shutdown(self, how: int) -> None:
        self.shutdown_calls.append(how)

    def close(self) -> None:
        self.closed = True


def _status(*, rows: int = 24, cols: int = 80, cursor: tuple[int, int] = (0, 0)) -> str:
    return " ".join(
        [
            "U",
            "F",
            "U",
            "C",
            "I",
            "4",
            str(rows),
            str(cols),
            str(cursor[0]),
            str(cursor[1]),
            "0",
            "-",
        ]
    )


def _wtd(*orders: bytes, row: int = 1, col: int = 1) -> bytes:
    return b"\x00\x00" + bytes([mf._O_SBA, row, col]) + b"".join(orders)


def _sf(
    *,
    ffw: bytes = b"\x40\x00",
    fcws: bytes = b"",
    attr: int = 0x20,
    length: int = 4,
) -> bytes:
    return bytes([mf._O_SF]) + ffw + fcws + bytes([attr, length >> 8, length & 0xFF])


def _record(*payload: bytes) -> bytes:
    body = b"".join(payload)
    return bytes([0, len(body) + 10, 0x12, 0xA0, 0, 0, 0, 0, 0, 0]) + body


def _terminal_with_backend(backend: object) -> mf.MainframeTerminal:
    return mf.MainframeTerminal(backend)  # type: ignore[arg-type]


def test_aid_screen_field_and_terminal_field_value_objects() -> None:
    assert mf.AID.pf(1) == "PF1"
    assert mf.AID.pf(24) == "PF24"
    with pytest.raises(ValueError, match=r"1\.\.24"):
        mf.AID.pf(0)
    with pytest.raises(ValueError, match=r"1\.\.24"):
        mf.AID.pf(25)

    screen = mf.TerminalScreen(["AB", "C"], (2, 1))
    assert (screen.rows, screen.cols, screen.cursor) == (2, 2, (2, 1))
    assert screen.text() == "AB\nC "
    assert screen.line(1) == "AB"
    assert screen.text_at(2, 1, 10) == "C "
    assert screen.contains("AB") is True
    assert screen.contains("C", row=2) is True
    assert screen.find("C") == (2, 1)
    assert screen.find("missing") is None
    assert repr(screen) == "TerminalScreen(2x2, cursor=(2, 1))"
    with pytest.raises(ValueError, match="row must be"):
        screen.line(0)
    with pytest.raises(ValueError, match="row must be"):
        screen.text_at(3, 1, 1)
    with pytest.raises(ValueError, match="col must be"):
        screen.text_at(1, 0, 1)

    all_flags = mf.FieldInfo(
        2,
        3,
        4,
        protected=True,
        hidden=True,
        numeric=True,
        modified=True,
        attr_byte=0xFF,
    )
    plain = mf.FieldInfo(
        1,
        1,
        1,
        protected=False,
        hidden=False,
        numeric=False,
        modified=False,
        attr_byte=0,
    )
    assert all_flags.writable is False
    assert plain.writable is True
    assert (
        repr(all_flags) == "FieldInfo(row=2, col=3, length=4 [protected,hidden,numeric,modified])"
    )
    assert repr(plain) == "FieldInfo(row=1, col=1, length=1)"

    backend = Mock()
    backend.read_screen.return_value = (["abc   "], (1, 1))
    terminal = _terminal_with_backend(backend)
    field = mf.TerminalField(terminal, 1, 1, 6)
    assert field.position == (1, 1)
    assert field.length == 6
    assert field.read() == "abc"
    field.type_text("X")
    assert backend.move_cursor.call_args_list == [((1, 1),), ((1, 1),)]
    assert backend.send_string.call_args_list == [(("      ",),), (("X",),)]
    field.type_text("Y", clear=False)
    assert backend.move_cursor.call_args_list[-1] == ((1, 1),)
    assert backend.send_string.call_args_list[-1] == (("Y",),)
    assert repr(field) == "TerminalField(row=1, col=1, length=6)"


def test_abstract_backend_protocol_stubs_are_callable() -> None:
    # The concrete adapters are required to implement this complete surface;
    # invoke the abstract declarations once so coverage also records their
    # intentionally empty bodies.
    mf._TerminalBackend.connect(None, "", 23, session_type="3270")  # type: ignore[arg-type]
    mf._TerminalBackend.disconnect(None)
    mf._TerminalBackend.is_connected(None)
    mf._TerminalBackend.read_screen(None)
    mf._TerminalBackend.send_string(None, "")
    mf._TerminalBackend.send_aid(None, "Enter")
    mf._TerminalBackend.move_cursor(None, 1, 1)
    mf._TerminalBackend.wait_unlock(None, 0)
    mf._TerminalBackend.wait_output(None, 0)
    mf._TerminalBackend.is_keyboard_locked(None)
    mf._TerminalBackend.read_fields(None)


def test_find_s3270_and_spawn_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    which = Mock(side_effect=[None, "s3270-found"])
    monkeypatch.setattr(mf.shutil, "which", which)
    assert mf._find_s3270() == "s3270-found"
    assert which.call_count == 2

    monkeypatch.setattr(mf.shutil, "which", Mock(return_value=None))
    monkeypatch.setattr(mf.sys, "platform", "linux")
    assert mf._find_s3270() is None

    monkeypatch.setattr(mf.sys, "platform", "win32")
    is_file = Mock(side_effect=[False, False, True])
    monkeypatch.setattr(mf.os.path, "isfile", is_file)
    assert mf._find_s3270().endswith(r"\wc3270\ws3270.exe")
    monkeypatch.setattr(mf.os.path, "isfile", Mock(return_value=False))
    assert mf._find_s3270() is None

    monkeypatch.setattr(mf, "_find_s3270", lambda: None)
    with pytest.raises(mf.MainframeError, match="No ws3270/s3270"):
        mf._S3270Backend()

    popen = Mock(return_value=_Process())
    monkeypatch.setattr(mf.subprocess, "Popen", popen)
    monkeypatch.setattr(mf.sys, "platform", "linux")
    backend = mf._S3270Backend(
        binary="demo",
        model="3278-2",
        codepage="cp500",
        extra_args=["-trace"],
    )
    backend._spawn()
    popen.assert_called_once_with(
        ["demo", "-model", "3278-2", "-utf8", "-charset", "cp500", "-trace"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        creationflags=0,
    )
    backend._spawn()
    assert popen.call_count == 1

    popen.reset_mock()
    monkeypatch.setattr(mf.sys, "platform", "win32")
    win_backend = mf._S3270Backend(binary="demo")
    win_backend._spawn()
    assert popen.call_args.kwargs["creationflags"] == 0x08000000


def test_s3270_lifecycle_and_status_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._proc = None
    backend._connected = False
    backend.disconnect()
    assert backend.is_connected() is False

    proc = _Process()
    backend._proc = proc
    backend._connected = False
    calls: list[tuple[str, bool]] = []

    def exec_ok(command: str, *, raise_on_error: bool = True) -> tuple[list[str], str]:
        calls.append((command, raise_on_error))
        return [], ""

    backend._exec = exec_ok  # type: ignore[method-assign]
    backend.disconnect()
    assert calls == [("Quit", False)]
    assert proc.stdin.closed is True
    assert backend._proc is None

    proc = _Process()
    backend._proc = proc
    backend._connected = True
    backend._exec = exec_ok  # type: ignore[method-assign]
    backend.disconnect()
    assert calls[-2:] == [("Disconnect", False), ("Quit", False)]

    class BrokenProcess(_Process):
        def __init__(self) -> None:
            super().__init__()
            self.stdin = SimpleNamespace(close=Mock(side_effect=RuntimeError("close")))

        def wait(self, timeout: float) -> None:
            del timeout
            raise RuntimeError("wait")

        def kill(self) -> None:
            raise RuntimeError("kill")

    broken = BrokenProcess()
    backend._proc = broken
    backend._connected = True
    backend._exec = Mock(side_effect=RuntimeError("exec"))  # type: ignore[method-assign]
    backend.disconnect()
    assert backend._proc is None
    assert backend._connected is False

    proc = _Process()
    backend._proc = proc
    backend._connected = True
    proc.poll_value = None
    assert backend.is_connected() is True
    proc.poll_value = 1
    assert backend.is_connected() is False
    backend._proc = None
    assert backend.is_connected() is False

    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._proc = _Process()
    backend._connected = False
    backend._spawn = Mock()  # type: ignore[method-assign]
    backend._exec = Mock(return_value=([], ""))  # type: ignore[method-assign]
    backend.connect("host", 23, session_type="3270")
    backend._spawn.assert_called_once_with()
    assert backend._exec.call_args_list == [
        (("Connect(host:23)",), {}),
        (("Wait(15,InputField)",), {"raise_on_error": False}),
    ]
    assert backend._connected is True


def test_s3270_read_fields_keyboard_and_input(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._exec = Mock(return_value=([], _status(cursor=(4, 7))))  # type: ignore[method-assign]
    assert backend.read_screen() == ([], (5, 8))
    backend._exec.return_value = ([], "bad status")
    assert backend.read_screen() == ([], (1, 1))

    for status, expected in [
        ("L anything", True),
        ("E anything", True),
        ("U anything", False),
        ("", False),
    ]:
        backend._exec.return_value = ([], status)
        assert backend.is_keyboard_locked() is expected

    assert backend._sf_attr_byte("c0=4d,42=f4") == 0x4D
    assert backend._sf_attr_byte("c0=not-hex") is None
    assert backend._sf_attr_byte("4d") == 0x4D
    assert backend._sf_attr_byte("not-hex") is None
    assert backend._sf_attr_byte("42=f4") is None

    backend._exec.return_value = (["00 SF(c0=zz) SA(42=f4) 00"], "bad")
    assert backend.read_fields() == []
    backend._exec.return_value = (["SF(c0=20) 00", "SF(20) 00"], "bad")
    fields = backend.read_fields()
    assert len(fields) == 2
    assert fields[0].protected is True
    assert fields[1].attr_byte == 0x20

    backend._exec = Mock()  # type: ignore[method-assign]
    backend.send_string('a\\"b')
    backend._exec.assert_called_once_with('String("a\\\\\\"b")')
    backend.send_aid("Enter")
    backend.send_aid("CLEAR")
    backend.send_aid("pf3")
    backend.send_aid("PA2")
    assert [call.args[0] for call in backend._exec.call_args_list] == [
        'String("a\\\\\\"b")',
        "Enter",
        "Clear",
        "PF(3)",
        "PA(2)",
    ]
    with pytest.raises(mf.MainframeError, match="invalid PF"):
        backend.send_aid("PFx")
    with pytest.raises(mf.MainframeError, match="invalid PA"):
        backend.send_aid("PAx")
    with pytest.raises(mf.MainframeError, match="unknown AID"):
        backend.send_aid("F1")
    backend.move_cursor(3, 4)
    backend.wait_unlock(9.9)
    backend.wait_output(9.9)
    assert backend._exec.call_args_list[-3:] == [
        call("MoveCursor(2,3)"),
        call("Wait(9,Unlock)"),
        call("Wait(9,Output)"),
    ]

    backend._exec.side_effect = mf.MainframeError("NOT CONNECTED")
    backend.wait_output(1)
    backend._exec.side_effect = mf.MainframeError("other failure")
    with pytest.raises(mf.MainframeError, match="other failure"):
        backend.wait_output(1)


def test_s3270_exec_protocol_and_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._proc = None
    backend._trace = False
    with pytest.raises(mf.MainframeError, match="not running"):
        backend._exec("Ping")

    proc = _Process([b"data: one\n", b"status\n", b"ok\n"])
    backend._proc = proc
    assert backend._exec("Ping") == (["one"], "status")
    assert proc.stdin.writes == [b"Ping\n"]

    proc.stdout.lines = [b"data: one\n", b"error text\n", b"error\n"]
    with pytest.raises(mf.MainframeError, match="failed: one"):
        backend._exec("Bad")
    proc.stdout.lines = [b"error text\n", b"error\n"]
    assert backend._exec("Bad", raise_on_error=False) == ([], "error text")

    proc.stdout.lines = [b"\n"]
    with pytest.raises(mf.MainframeError, match="exited"):
        backend._exec("EOF")

    proc.stdout.lines = [b"data: one\n", b"data: two\n", b"ok\n"]
    proc.stdin.write = Mock(side_effect=BrokenPipeError("pipe"))  # type: ignore[method-assign]
    with pytest.raises(mf.MainframeError, match="pipe closed"):
        backend._exec("Pipe")

    proc = _Process([b"ok\n"])
    backend._proc = proc
    backend._trace = True
    backend._exec("Trace")
    backend._trace_response("Trace", "ok", ["1", "2", "3", "4", "5"], "status")
    backend._trace_response("Trace", "ok", [], "")
    proc.stdout.lines = [b"failure detail\n", b"error\n"]
    assert backend._exec("TraceBad", raise_on_error=False) == ([], "failure detail")


def test_s3270_wait_output_rethrows_non_disconnect_and_connect_guard() -> None:
    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._exec = Mock()  # type: ignore[method-assign]
    with pytest.raises(mf.MainframeError, match="TN3270 only"):
        backend.connect("host", 23, session_type="tn5250")
    backend._exec.side_effect = OSError("write")
    with pytest.raises(OSError):
        backend.wait_output(1)


def test_resolve_hllapi_dll_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fn = Mock()
    dll = SimpleNamespace(HLLAPI=fn)
    loader = Mock(side_effect=[OSError("missing"), dll])
    monkeypatch.setattr(mf.ctypes, "WinDLL", loader, raising=False)
    resolved_dll, resolved_fn = mf._resolve_hllapi_dll("explicit.dll")
    assert resolved_dll is dll
    assert resolved_fn is fn
    assert fn.restype is None
    assert len(fn.argtypes) == 4

    class NoFunction:
        pass

    monkeypatch.setattr(mf.ctypes, "WinDLL", Mock(return_value=NoFunction()), raising=False)
    with pytest.raises(mf.MainframeError, match="No HLLAPI-compatible DLL") as exc:
        mf._resolve_hllapi_dll(None)
    assert "PCSHLL32.DLL!hllapi" in str(exc.value)

    resolved = Mock(return_value=(dll, fn))
    monkeypatch.setattr(mf, "_resolve_hllapi_dll", resolved)
    injected_by_resolve = mf._HLLAPIBackend(dll_path="configured.dll")
    assert injected_by_resolve._dll is dll
    resolved.assert_called_once_with("configured.dll")


def _hllapi_backend(call: Mock | None = None) -> mf._HLLAPIBackend:
    backend = mf._HLLAPIBackend(_hllapi_fn=call or Mock())
    backend._rows = 2
    backend._cols = 4
    return backend


def test_hllapi_call_and_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, bytes, int, int]] = []

    def fn(func: object, buf: object, length: object, rc: object) -> None:
        f = func._obj.value  # type: ignore[attr-defined]
        ln = length._obj  # type: ignore[attr-defined]
        code = rc._obj  # type: ignore[attr-defined]
        calls.append((f, bytes(buf), ln.value, code.value))  # type: ignore[arg-type]
        code.value = 7
        ln.value = min(2, len(buf))  # type: ignore[arg-type]

    backend = mf._HLLAPIBackend(_hllapi_fn=fn, trace=True)
    rc, data, length = backend._call(mf.HllapiFn.WAIT, b"x" * 40, ps_pos=9)
    assert (rc, data, length) == (7, b"xx", 2)
    rc, data, length = backend._call(99)
    assert (rc, data, length) == (7, b"\x00", 1)
    assert calls[0][0] == int(mf.HllapiFn.WAIT)
    quiet_backend = mf._HLLAPIBackend(_hllapi_fn=lambda *_args: None)
    assert quiet_backend._call(1) == (0, b"", 0)

    backend = _hllapi_backend()
    backend._call = Mock(side_effect=[(0, b"", 0), (0, b"x" * 15, 15)])  # type: ignore[method-assign]
    backend.connect("ignored", 123, session_type="5250")
    assert backend._connected is True
    assert (backend._rows, backend._cols) == (2, 4)

    status = bytearray(18)
    status[11:13] = (27).to_bytes(2, "little")
    status[13:15] = (132).to_bytes(2, "little")
    backend._rows, backend._cols = 24, 80
    backend._call = Mock(side_effect=[(0, b"", 0), (0, bytes(status), 18)])  # type: ignore[method-assign]
    backend.connect("ignored", 123, session_type="3270")
    assert (backend._rows, backend._cols) == (27, 132)
    backend._call = Mock(side_effect=[(0, b"", 0), (1, b"short", 5)])  # type: ignore[method-assign]
    backend.connect("ignored", 123, session_type="3270")

    backend._call = Mock(side_effect=[(4, b"", 0)])  # type: ignore[method-assign]
    with pytest.raises(mf.MainframeError, match="ConnectPS"):
        backend.connect("ignored", 123, session_type="3270")

    backend._connected = False
    backend.disconnect()
    backend._connected = True
    backend._call = Mock(return_value=(0, b"", 0))  # type: ignore[method-assign]
    backend.disconnect()
    assert backend.is_connected() is False
    backend._connected = True
    assert backend.is_connected() is True


def test_hllapi_read_write_and_wait_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _hllapi_backend()
    backend._call = Mock(side_effect=[(0, b"ABCDWXYZ", 8), (0, b"", 6)])  # type: ignore[method-assign]
    lines, cursor = backend.read_screen()
    assert lines == ["ABCD", "WXYZ"]
    assert cursor == (2, 2)

    backend._call = Mock(side_effect=[(0, b"ABCDWXYZ", 8), (4, b"", 0)])  # type: ignore[method-assign]
    assert backend.read_screen()[1] == (1, 1)
    backend._call = Mock(return_value=(4, b"", 0))  # type: ignore[method-assign]
    with pytest.raises(mf.MainframeError, match="CopyPS"):
        backend.read_screen()

    backend._call = Mock(return_value=(0, b"", 0))  # type: ignore[method-assign]
    backend.send_string("a@b")
    assert backend._call.call_args.args[1] == b"a@@b"
    backend._call.return_value = (5, b"", 0)
    with pytest.raises(mf.MainframeError, match="SendKey"):
        backend.send_string("x" * 20)

    aid_inputs = ["Enter", "Clear", "PF1", "PF24", "PA1", "PA3"]
    backend._call = Mock(return_value=(0, b"", 0))  # type: ignore[method-assign]
    for aid in aid_inputs:
        backend.send_aid(aid)
    assert backend._call.call_count == len(aid_inputs)
    with pytest.raises(mf.MainframeError, match="invalid PF"):
        backend.send_aid("PFx")
    with pytest.raises(mf.MainframeError, match=r"PF1\.\.PF24"):
        backend.send_aid("PF25")
    with pytest.raises(mf.MainframeError, match="invalid PA"):
        backend.send_aid("PAx")
    with pytest.raises(mf.MainframeError, match="invalid PA"):
        backend.send_aid("PA4")
    with pytest.raises(mf.MainframeError, match="unknown AID"):
        backend.send_aid("F1")
    backend._call.return_value = (9, b"", 0)
    with pytest.raises(mf.MainframeError, match="SendKey"):
        backend.send_aid("Enter")

    backend._call.return_value = (0, b"", 0)
    backend.move_cursor(2, 3)
    assert backend._call.call_args.kwargs["ps_pos"] == 7
    backend._call.return_value = (9, b"", 0)
    with pytest.raises(mf.MainframeError, match="SetCursor"):
        backend.move_cursor(1, 1)
    assert backend.read_fields() == []

    backend._call = Mock(side_effect=[(0, b"", 0)])  # type: ignore[method-assign]
    backend.wait_unlock(1)
    assert backend._call.call_count == 1
    monkeypatch.setattr(mf.time, "monotonic", Mock(side_effect=[0, 0, 2]))
    monkeypatch.setattr(mf.time, "sleep", Mock())
    backend._call = Mock(return_value=(1, b"", 0))  # type: ignore[method-assign]
    with pytest.raises(mf.MainframeError, match="did not unlock"):
        backend.wait_unlock(1)
    monkeypatch.setattr(mf.time, "monotonic", Mock(return_value=2))
    backend._call.return_value = (0, b"", 0)
    backend.wait_output(1)
    backend._call.return_value = (1, b"", 0)
    assert backend.is_keyboard_locked() is True
    backend._call.return_value = (0, b"", 0)
    assert backend.is_keyboard_locked() is False


def test_tn5250_connect_disconnect_and_telnet_negotiation(monkeypatch: pytest.MonkeyPatch) -> None:
    incoming = b"".join(
        [
            bytes([mf._T_IAC, mf._T_DO, mf._TOPT_BINARY]),
            bytes([mf._T_IAC, mf._T_DO, 99]),
            bytes([mf._T_IAC, mf._T_DONT, mf._TOPT_EOR]),
            bytes([mf._T_IAC, mf._T_WILL, mf._TOPT_TERMTYPE]),
            bytes([mf._T_IAC, mf._T_WILL, 99]),
            bytes([mf._T_IAC, mf._T_WONT, mf._TOPT_NEWENV]),
            bytes([mf._T_IAC, 0xF1]),
            bytes([mf._T_IAC, mf._T_SB, mf._TOPT_TERMTYPE, 0x01, mf._T_IAC, mf._T_SE]),
            bytes([mf._T_IAC, mf._T_SB, mf._TOPT_NEWENV, 0x01, mf._T_IAC, mf._T_SE]),
            bytes([mf._T_IAC, mf._T_SB, mf._T_IAC, mf._T_SE]),
            b"\x00",
        ]
    )
    sock = _Socket([incoming])
    backend = mf._Tn5250Backend(trace=True, rows=2, cols=4)
    backend._sock = sock
    backend._negotiate()
    assert backend._rx_backlog == b"\x00"
    assert bytes([mf._T_IAC, mf._T_WILL, mf._TOPT_TERMTYPE]) in sock.sent
    assert (
        bytes(
            [
                mf._T_IAC,
                mf._T_SB,
                mf._TOPT_TERMTYPE,
                0,
                *mf._Tn5250Backend._MODEL.encode(),
                mf._T_IAC,
                mf._T_SE,
            ]
        )
        in sock.sent
    )
    assert bytes([mf._T_IAC, mf._T_SB, mf._TOPT_NEWENV, 0, mf._T_IAC, mf._T_SE]) in sock.sent

    # A command split over recv() calls is retained until its option arrives.
    split = _Socket([bytes([mf._T_IAC]), bytes([mf._T_DO, mf._TOPT_EOR, 0])])
    backend._sock = split
    backend._rx_backlog = b""
    backend._negotiate()
    assert backend._rx_backlog == b"\x00"

    # An incomplete sub-negotiation is also retained across recv() calls.
    split_sub = _Socket(
        [
            bytes([mf._T_IAC, mf._T_SB, mf._TOPT_TERMTYPE]),
            bytes([0x01, mf._T_IAC, mf._T_SE, 0]),
        ]
    )
    backend._sock = split_sub
    backend._rx_backlog = b""
    backend._negotiate()
    assert backend._rx_backlog == b"\x00"

    timeout_sock = _Socket([TimeoutError()])
    backend._sock = timeout_sock
    backend._rx_backlog = b""
    backend._negotiate()
    assert len(timeout_sock.sent) == 6

    command_only = _Socket([bytes([mf._T_IAC, 0xF1])])
    backend._sock = command_only
    backend._rx_backlog = b""
    backend._negotiate()
    assert backend._rx_backlog == b""

    huge_command_stream = _Socket([bytes([mf._T_IAC, 0xF1]) * 512] * 4)
    backend._sock = huge_command_stream
    backend._rx_backlog = b""
    backend._negotiate()
    assert backend._rx_backlog == b""

    empty_sock = _Socket([b""])
    backend._sock = empty_sock
    backend._rx_backlog = b""
    backend._negotiate()
    assert len(empty_sock.sent) == 6

    split_neg = _Socket([bytes([mf._T_IAC, mf._T_DO]), bytes([mf._TOPT_EOR, 0])])
    backend._sock = split_neg
    backend._rx_backlog = b""
    backend._negotiate()
    assert backend._rx_backlog == b"\x00"

    monkeypatch.setattr(backend._socket, "create_connection", Mock(return_value=sock))
    backend._negotiate = Mock()  # type: ignore[method-assign]
    backend._read_records = Mock(side_effect=backend._socket.timeout())  # type: ignore[method-assign]
    backend.connect("ibmi", 992, session_type="5250")
    assert backend._connected is True
    assert sock.timeouts[-1] == 5.0

    backend.disconnect()
    assert backend._sock is None
    assert backend._connected is False
    backend.disconnect()


def test_tn5250_negotiation_helpers_and_raw_send(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = mf._Tn5250Backend(trace=True)
    sock = _Socket()
    backend._sock = sock
    backend._on_negotiation(mf._T_DO, mf._TOPT_BINARY)
    backend._on_negotiation(mf._T_DO, 99)
    backend._on_negotiation(mf._T_DONT, mf._TOPT_BINARY)
    backend._on_negotiation(mf._T_WILL, mf._TOPT_EOR)
    backend._on_negotiation(mf._T_WILL, 99)
    backend._on_negotiation(mf._T_WONT, mf._TOPT_EOR)
    backend._on_negotiation(123, mf._TOPT_EOR)
    backend._on_subneg(b"")
    backend._on_subneg(bytes([mf._TOPT_TERMTYPE, 0]))
    backend._on_subneg(bytes([mf._TOPT_NEWENV, 0]))
    backend._on_subneg(bytes([mf._TOPT_TERMTYPE, 1]))
    backend._on_subneg(bytes([mf._TOPT_NEWENV, 1]))
    assert len(sock.sent) == 8
    backend._send_raw(b"hello")
    assert sock.sent[-1] == b"hello"
    backend._push_rx(b"new")
    backend._push_rx(b"old")
    assert backend._rx_backlog == b"oldnew"

    # Shutdown and close are deliberately best-effort.
    class BrokenSocket(_Socket):
        def shutdown(self, how: int) -> None:
            del how
            raise OSError("already down")

        def close(self) -> None:
            raise OSError("already closed")

    backend._sock = BrokenSocket()
    backend._connected = True
    backend.disconnect()
    assert backend.is_connected() is False
    backend._sock = None
    backend._connected = True
    assert backend.is_connected() is False


def test_tn5250_read_records_handles_escaping_negotiation_and_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = mf._Tn5250Backend()
    backend._rx_backlog = b""
    backend._sock = _Socket(
        [b"abc" + bytes([mf._T_IAC, mf._T_IAC, mf._T_IAC, mf._T_EOR]), TimeoutError()]
    )
    backend._process_record = Mock()  # type: ignore[method-assign]
    records = backend._read_records(1)
    assert records == [b"abc\xff"]
    backend._process_record.assert_called_once_with(b"abc\xff")
    assert backend._sock.timeouts[-1] == 0.2

    # An unfinished negotiation is kept until its third byte arrives.
    backend._sock = _Socket([bytes([mf._T_DO, mf._TOPT_EOR, mf._T_IAC, mf._T_EOR])])
    backend._rx_backlog = bytes([mf._T_IAC, mf._T_DO])
    backend._process_record = Mock()  # type: ignore[method-assign]
    backend._read_records(1)
    assert backend._process_record.called is True

    # Mid-stream negotiation and sub-negotiation are removed before framing.
    midstream = (
        bytes(
            [
                mf._T_IAC,
                mf._T_DO,
                mf._TOPT_BINARY,
                mf._T_IAC,
                mf._T_SB,
                mf._TOPT_TERMTYPE,
                1,
                mf._T_IAC,
                mf._T_SE,
            ]
        )
        + b"x"
        + bytes([mf._T_IAC, mf._T_EOR])
    )
    backend._sock = _Socket([midstream, TimeoutError()])
    backend._rx_backlog = b""
    backend._process_record = Mock()  # type: ignore[method-assign]
    assert backend._read_records(1) == [b"x"]

    incomplete_sb = _Socket(
        [
            bytes([mf._T_IAC, mf._T_SB, mf._TOPT_TERMTYPE]),
            bytes([1, mf._T_IAC, mf._T_SE, mf._T_IAC, mf._T_EOR]),
        ]
    )
    backend._sock = incomplete_sb
    backend._rx_backlog = b""
    backend._process_record = Mock()  # type: ignore[method-assign]
    assert backend._read_records(1) == [b""]

    unknown_telnet = _Socket([bytes([mf._T_IAC, 0xF1]) + b"x" + bytes([mf._T_IAC, mf._T_EOR])])
    backend._sock = unknown_telnet
    backend._rx_backlog = b""
    backend._process_record = Mock()  # type: ignore[method-assign]
    assert backend._read_records(1) == [b"\xff\xf1x"]

    # No record before the deadline and a peer closing the socket both retain
    # whatever bytes were already buffered.
    monkeypatch.setattr(mf.time, "monotonic", Mock(side_effect=[0, 2]))
    backend._sock = _Socket()
    backend._rx_backlog = b"partial"
    assert backend._read_records(1) == []
    assert backend._rx_backlog == b"partial"
    monkeypatch.setattr(mf.time, "monotonic", Mock(side_effect=[0, 0, 0]))
    backend._sock = _Socket([b""])
    backend._rx_backlog = b""
    assert backend._read_records(1) == []


def test_tn5250_record_dispatch_and_wtd_orders() -> None:
    backend = mf._Tn5250Backend(rows=2, cols=5, trace=True)
    backend._process_record(b"short")
    backend._process_record(_record(b"\x00\x00", bytes([0x04])))
    backend._process_record(_record(bytes([0x04, 0x40]), bytes([0x04, 0x20])))
    assert all(cell == " " for row in backend._screen for cell in row)

    backend._process_record(_record(bytes([0x04, 0x50])))
    assert backend._fields == []
    backend._process_record(_record(bytes([0x04, 0xF3, 0, 1, 9, 0x04, 0xF3])))
    # The second WSF is truncated and simply stops parsing.
    backend._process_record(_record(bytes([0x04, 0xF3])))

    backend._process_record(_record(bytes([0x04, 0x99]), b"x"))
    assert backend._screen == [[" "] * 5 for _ in range(2)]


def test_tn5250_wtd_parser_orders_bounds_attributes_and_truncation() -> None:
    backend = mf._Tn5250Backend(rows=2, cols=5)

    assert backend._parse_wtd(b"", 0) == 0
    assert backend._parse_wtd(b"\x00", 0) == 1
    assert backend._parse_wtd(b"\x00\x00\x04", 0) == 2
    assert backend._parse_wtd(b"\x00\x00\x01", 0) == 2

    # SBA, IC, repeat-to-address, inline attributes, control and EBCDIC data.
    orders = _wtd(
        bytes([mf._O_IC, 2, 3]),
        bytes([mf._O_SBA, 1, 1]),
        bytes([mf._O_RA, 1, 4, 0xC1]),
        bytes([0x20]),
        bytes([0x00]),
        "B".encode("cp037"),
    )
    backend._parse_wtd(orders, 0)
    assert backend._cursor == (2, 3)
    assert "A" in "".join(backend._screen[0])
    assert "B" in "".join(backend._screen[0])

    # Truncated forms of SBA, IC and RA stop safely.
    for order in (
        bytes([mf._O_SBA]),
        bytes([mf._O_SBA, 1]),
        bytes([mf._O_IC]),
        bytes([mf._O_IC, 1]),
        bytes([mf._O_RA, 1, 1]),
    ):
        assert backend._parse_wtd(b"\x00\x00" + order, 0) <= len(b"\x00\x00" + order)

    # Repeat with an out-of-screen start exercises the bounds guard.
    backend._parse_wtd(_wtd(bytes([mf._O_SBA, 0, 1]), bytes([mf._O_RA, 1, 3, 0xC1])), 0)

    # SOH skips its declared payload; a truncated SOH is harmless.
    backend._parse_wtd(b"\x00\x00\x01\x02XYB", 0)
    backend._parse_wtd(b"\x00\x00\x01", 0)

    # SF with no FFW consumes attribute + length but creates no field.
    backend._fields = []
    backend._parse_wtd(_wtd(_sf(ffw=b"")), 0)
    assert backend._fields == []

    # FCW pairs, wrapped attribute, and the ordinary FFW path.
    backend._fields = []
    backend._parse_wtd(_wtd(_sf(fcws=b"\x81\x02\x85\x06", length=3), row=1, col=5), 0)
    assert backend._fields[0].col == 1  # attribute at col 5 wrapped to row 2
    assert backend._fields[0].length == 3
    backend._fields = []
    backend._parse_wtd(_wtd(_sf(length=3), row=2, col=4), 0)
    assert backend._fields[0].length == 1  # clipped at the bottom-right corner

    # Missing display attribute, missing length, and a truncated FCW.
    backend._fields = []
    backend._parse_wtd(b"\x00\x00\x1d\x40\x00\x00\x04", 0)
    backend._parse_wtd(b"\x00\x00\x1d\x40\x00", 0)
    backend._parse_wtd(b"\x00\x00\x1d\x40\x00\x81", 0)
    backend._parse_wtd(b"\x00\x00\x1d", 0)
    backend._parse_wtd(b"\x00\x00\x1d\x40", 0)
    assert backend._fields

    # Attribute/data order at the end of a row exercises wrap and out-of-range
    # screen writes without raising.
    backend._parse_wtd(_wtd(bytes([0x20]), row=2, col=5), 0)
    backend._parse_wtd(_wtd(b"Z", row=3, col=5), 0)
    backend._parse_wtd(_wtd(bytes([0x20]), row=0, col=0), 0)
    backend._fields = []
    backend._parse_wtd(_wtd(_sf(), row=0, col=0), 0)


def test_tn5250_recompute_read_screen_fields_and_local_typing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = mf._Tn5250Backend(rows=2, cols=5)
    backend._fields = [
        mf.FieldInfo(
            1, 1, 1, protected=False, hidden=False, numeric=False, modified=False, attr_byte=0
        ),
        mf.FieldInfo(
            1, 4, 0, protected=False, hidden=False, numeric=False, modified=False, attr_byte=0
        ),
        mf.FieldInfo(
            2, 2, 0, protected=False, hidden=False, numeric=False, modified=False, attr_byte=0
        ),
        mf.FieldInfo(
            2, 4, 0, protected=False, hidden=False, numeric=False, modified=False, attr_byte=0
        ),
    ]
    backend._recompute_field_lengths()
    assert [f.length for f in backend._fields] == [1, 2, 1, 2]
    assert backend.read_fields() is not backend._fields

    backend._read_records = Mock(side_effect=OSError("not connected"))  # type: ignore[method-assign]
    lines, cursor = backend.read_screen()
    assert len(lines) == 2 and len(lines[0]) == 5
    assert cursor == (1, 1)

    backend._cursor = (1, 1)
    backend.send_string("ABCDEF")
    assert "ABC" == "".join(backend._screen[0][:3])
    assert backend._input_cursor == (2, 2)
    backend.move_cursor(2, 5)
    backend.send_string("XY")
    assert backend._input_cursor == (2, 2)
    assert backend._pending_writes[-1][0:2] == (2, 5)
    backend._input_cursor = (3, 1)
    backend.send_string("Z")
    assert backend._screen[1][0] == "Y"  # the out-of-range write did not overwrite it

    backend._read_records = Mock(return_value=[])  # type: ignore[method-assign]
    backend.wait_unlock(0.1)
    backend.wait_output(0.1)
    backend._read_records.side_effect = OSError("gone")
    backend.wait_unlock(0.1)
    backend.wait_output(0.1)
    assert backend.is_keyboard_locked() is False


def test_tn5250_send_aid_pf_unknown_and_pending_writes() -> None:
    backend = mf._Tn5250Backend(rows=2, cols=5)
    sock = _Socket()
    backend._sock = sock
    backend._cursor = (2, 3)
    backend._pending_writes = [(1, 2, b"A\xff")]
    backend.send_aid("PF1")
    wire = sock.sent[-1]
    assert wire[-2:] == bytes([mf._T_IAC, mf._T_EOR])
    assert wire[10:16] == bytes([2, 3, mf._A5_PF1, mf._O_SBA, 1, 2])
    assert wire[16:-2] == b"A\xff\xff"
    assert backend._pending_writes == []
    assert backend._input_cursor == (1, 1)

    backend._cursor = (1, 1)
    backend.send_aid("PF12")
    backend.send_aid("PF13")
    assert sock.sent[-2][12] == mf._A5_PF12
    assert sock.sent[-1][12] == mf._A5_PF13
    for aid in ("PFx", "PF25"):
        with pytest.raises(mf.MainframeError, match="invalid PF"):
            backend.send_aid(aid)
    with pytest.raises(mf.MainframeError, match="unknown AID"):
        backend.send_aid("ROLLUP")

    backend._pending_writes = [(0, 1, b"bad")]
    with pytest.raises(mf.MainframeError, match="pending write"):
        backend.send_aid("Enter")
    assert backend._pending_writes == []


def test_mainframe_terminal_delegates_and_context_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    import dolphin_desktop._backend as backend_module

    resolved = Mock()
    monkeypatch.setattr(backend_module, "resolve", resolved)
    resolved.return_value.supports.return_value = True
    assert mf.MainframeTerminal.backend() is resolved.return_value
    assert mf.MainframeTerminal.backend_supports("capability") is True
    mf.MainframeTerminal.require_capability("capability")
    resolved.return_value.supports.assert_called_once_with("capability")
    resolved.return_value.require_capability.assert_called_once_with("capability")

    backend = Mock()
    backend.read_screen.return_value = (["HELLO"], (1, 3))
    backend.read_fields.return_value = []
    backend.is_connected.return_value = True
    backend.is_keyboard_locked.return_value = False
    terminal = _terminal_with_backend(backend)
    terminal.connect("host", 992, session_type="3270", timeout=2.5)
    backend.connect.assert_called_once_with("host", 992, session_type="3270")
    backend.wait_output.assert_called_once_with(2.5)
    assert terminal.is_connected() is True
    assert terminal.screen().text() == "HELLO"
    assert terminal.text() == "HELLO"
    assert terminal.field(row=1, col=2).position == (1, 2)
    assert terminal.fields() == []
    terminal.type_text("x")
    terminal.press("Enter")
    terminal.move_cursor(1, 1)
    terminal.wait_ready(1)
    terminal.wait_change(1)
    assert terminal.is_keyboard_locked() is False
    assert repr(terminal) == "MainframeTerminal(backend=Mock)"
    terminal.disconnect()
    backend.disconnect.assert_called_once_with()

    backend.wait_output.side_effect = mf.MainframeError("disconnect during initial wait")
    terminal.connect(timeout=1)

    with terminal as entered:
        assert entered is terminal
    assert backend.disconnect.call_count == 2


def test_field_after_uses_attributes_then_falls_back_to_scan() -> None:
    writable = mf.FieldInfo(
        1,
        8,
        6,
        protected=False,
        hidden=False,
        numeric=False,
        modified=False,
        attr_byte=0,
    )
    protected = mf.FieldInfo(
        1,
        6,
        1,
        protected=True,
        hidden=False,
        numeric=False,
        modified=False,
        attr_byte=0x20,
    )
    backend = Mock()
    backend.read_screen.return_value = (["Name:  ______", "Other"], (1, 1))
    backend.read_fields.return_value = [protected, writable]
    terminal = _terminal_with_backend(backend)
    found = terminal.field_after("Name", row=1)
    assert found.position == (1, 8)
    assert found.length == 6
    forced = terminal.field_after("Name", length=3)
    assert forced.position == (1, 8)
    assert forced.length == 3

    backend.read_screen.return_value = (["Name:  ______", "Other"], (1, 1))
    backend.read_fields.return_value = [
        mf.FieldInfo(
            2,
            1,
            4,
            protected=False,
            hidden=False,
            numeric=False,
            modified=False,
            attr_byte=0,
        )
    ]
    wrapped = terminal.field_after("Name")
    assert wrapped.position == (2, 1)
    assert wrapped.length == 4
    fallback_forced = terminal.field_after("Name", length=9)
    assert fallback_forced.position == (1, 9)
    assert fallback_forced.length == 9

    backend.read_screen.return_value = (["User....____________"], (1, 1))
    backend.read_fields.return_value = []
    fallback = terminal.field_after("User")
    assert fallback.position == (1, 9)
    assert fallback.length == 20
    fallback_custom = terminal.field_after("User", length=5)
    assert fallback_custom.position == (1, 9)
    assert fallback_custom.length == 5

    backend.read_screen.return_value = (["Label", ""], (1, 1))
    with pytest.raises(mf.MainframeError, match="not found on screen"):
        terminal.field_after("missing")
    backend.read_screen.return_value = (["Label"], (1, 1))
    with pytest.raises(mf.MainframeError, match="not found on row 1"):
        terminal.field_after("missing", row=1)


def test_mainframe_terminal_wait_helpers_and_polling(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = Mock()
    backend.read_screen.side_effect = [(["READY"], (1, 1)), (["READY"], (1, 4))]
    terminal = _terminal_with_backend(backend)
    terminal.wait_for_text("READY", timeout=1, row=1)
    terminal.wait_for_cursor(1, 4, timeout=1)
    with pytest.raises(ValueError, match="always matches"):
        terminal.wait_for_text("")

    writable = mf.FieldInfo(
        3,
        4,
        2,
        protected=False,
        hidden=False,
        numeric=False,
        modified=False,
        attr_byte=0,
    )
    protected = mf.FieldInfo(
        3,
        4,
        2,
        protected=True,
        hidden=False,
        numeric=False,
        modified=False,
        attr_byte=0x20,
    )
    backend.read_fields.return_value = [protected, writable]
    assert terminal.wait_for_field(3, 4) is writable
    assert terminal.wait_for_field(3, 4, writable=False) is protected
    terminal.wait_for(lambda current: current is terminal, timeout=1, description="identity")

    monkeypatch.setattr(mf.time, "monotonic", Mock(side_effect=[0, 0, 2]))
    with pytest.raises(mf.MainframeError, match=r"field at \(9,9\)"):
        terminal.wait_for_field(9, 9, timeout=1, poll_interval=0)

    monkeypatch.setattr(mf.time, "sleep", Mock())
    monkeypatch.setattr(mf.time, "monotonic", Mock(side_effect=[0, 0, 0]))
    check = Mock(side_effect=[mf.MainframeError("temporary"), True])
    terminal._poll_until(check, timeout=1, poll_interval=0, description="retry")
    assert check.call_count == 2

    monkeypatch.setattr(mf.time, "monotonic", Mock(side_effect=[0, 0, 2]))
    with pytest.raises(mf.MainframeError, match="never did not become true"):
        terminal._poll_until(lambda: False, timeout=1, poll_interval=0, description="never")


def test_build_terminal_selects_all_backends_and_rejects_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    implementations = {
        "s3270": object(),
        "hllapi": object(),
        "tn5250": object(),
    }
    s3270 = Mock(return_value=implementations["s3270"])
    hllapi = Mock(return_value=implementations["hllapi"])
    tn5250 = Mock(return_value=implementations["tn5250"])
    monkeypatch.setattr(mf, "_S3270Backend", s3270)
    monkeypatch.setattr(mf, "_HLLAPIBackend", hllapi)
    monkeypatch.setattr(mf, "_Tn5250Backend", tn5250)

    args = {
        "ws3270_path": "ws3270",
        "model": "3279-4",
        "codepage": "cp500",
        "session_id": "A",
        "hllapi_dll_path": "hllapi.dll",
        "extra_args": ["-x"],
        "trace": True,
    }
    assert mf._build_terminal(backend="s3270", **args)._backend is implementations["s3270"]
    s3270.assert_called_once_with(
        binary="ws3270",
        model="3279-4",
        codepage="cp500",
        extra_args=["-x"],
        trace=True,
    )
    assert mf._build_terminal(backend="hllapi", **args)._backend is implementations["hllapi"]
    hllapi.assert_called_once_with(session_id="A", dll_path="hllapi.dll", trace=True)
    assert mf._build_terminal(backend="tn5250", **args)._backend is implementations["tn5250"]
    tn5250.assert_called_once_with(codepage="cp500", trace=True)

    args["codepage"] = None
    mf._build_terminal(backend="tn5250", **args)
    assert tn5250.call_args.kwargs["codepage"] == "cp037"
    with pytest.raises(mf.MainframeError, match="unknown mainframe backend"):
        mf._build_terminal(backend="other", **args)


def test_mainframe_terminal_delegates_connect_and_screen_reading() -> None:
    from dolphin_desktop._mainframe import MainframeTerminal

    backend = Mock()
    backend.read_screen.return_value = (["READY"], (1, 2))
    terminal = MainframeTerminal(backend)
    terminal.connect("host", 992, session_type="5250", timeout=3)
    screen = terminal.screen()
    backend.connect.assert_called_once_with("host", 992, session_type="5250")
    assert screen.text() == "READY" and screen.cursor == (1, 2)


def test_mainframe_screen_field_and_metadata_are_deterministic() -> None:
    screen = TerminalScreen(["READY", "A"], cursor=(2, 1))
    assert (screen.rows, screen.cols, screen.cursor) == (2, 5, (2, 1))
    assert screen.line(2) == "A    "
    assert screen.text_at(1, 2, 99) == "EADY"
    assert screen.contains("ADY") and screen.contains("A", row=2)
    assert screen.find("DY") == (1, 4)
    with pytest.raises(ValueError, match="col must be >= 1"):
        screen.text_at(1, 0, 1)

    terminal = Mock()
    terminal.screen.return_value = screen
    field = TerminalField(terminal, 1, 1, 5)
    assert field.read() == "READY"
    field.type_text("GO")
    terminal.assert_has_calls(
        [
            call.move_cursor(1, 1),
            call.type_text(" " * 5),
            call.move_cursor(1, 1),
            call.type_text("GO"),
        ]
    )
    assert AID.pf(24) == "PF24"
    with pytest.raises(ValueError):
        AID.pf(25)
    info = FieldInfo(
        1, 2, 3, protected=False, hidden=True, numeric=True, modified=False, attr_byte=0
    )
    assert info.writable is True
    assert "hidden,numeric" in repr(info)


def test_s3270_backend_parses_screen_status_and_field_attributes() -> None:
    import dolphin_desktop._mainframe as mainframe

    backend = mainframe._S3270Backend("s3270.exe")
    assert backend._sf_attr_byte("20") == 0x20
    assert backend._sf_attr_byte("c0=2d,42=f4") == 0x2D
    assert backend._sf_attr_byte("gg") is None
    backend._exec = Mock(
        side_effect=[
            (["HELLO"], "U F U C M 4 5 80 1 2 0 0"),
            ([], "L F U C M 4 5 80 0 0 0 0"),
            (
                ["SF(c0=20) A B SF(c0=1d) C D"],
                "U F U C M 4 1 5 0 0 0 0",
            ),
        ]
    )
    assert backend.read_screen() == (["HELLO"], (2, 3))
    assert backend.is_keyboard_locked() is True
    fields = backend.read_fields()
    assert [(f.row, f.col, f.length, f.protected) for f in fields] == [
        (1, 2, 2, True),
        (1, 5, 1, False),
    ]


def test_mainframe_screen_field_and_facade_wait_paths(monkeypatch) -> None:
    from dolphin_desktop._mainframe import (
        FieldInfo,
        MainframeError,
        MainframeTerminal,
        TerminalField,
        TerminalScreen,
    )

    screen = TerminalScreen([], cursor=(1, 1))
    assert screen.text() == "" and screen.cols == 0 and screen.find("x") is None
    with pytest.raises(ValueError, match=r"row must be 1\.\.0"):
        screen.line(1)
    info = FieldInfo(
        1,
        2,
        3,
        protected=True,
        hidden=True,
        numeric=True,
        modified=True,
        attr_byte=0,
    )
    assert repr(info) == ("FieldInfo(row=1, col=2, length=3 [protected,hidden,numeric,modified])")

    terminal = Mock()
    terminal.screen.return_value = TerminalScreen(["Name     "], cursor=(1, 1))
    field = TerminalField(terminal, 1, 1, 4)
    field.type_text("Joe", clear=False)
    terminal.assert_has_calls([call.move_cursor(1, 1), call.type_text("Joe")])

    backend = Mock()
    backend.wait_output.side_effect = MainframeError("not painted")
    backend.read_screen.return_value = (["READY"], (1, 1))
    facade = MainframeTerminal(backend)
    facade.connect("host", 23)
    assert facade.text() == "READY"
    facade.wait_for_text("READY", poll_interval=0)
    with pytest.raises(ValueError, match="always matches"):
        facade.wait_for_text("")
    facade.disconnect()
    backend.disconnect.assert_called_once_with()
    assert repr(facade) == "MainframeTerminal(backend=Mock)"


def test_mainframe_facade_covers_field_detection_waits_and_context_manager(monkeypatch) -> None:
    import dolphin_desktop._mainframe as mainframe

    backend = Mock()
    backend.read_screen.return_value = (["User:    ", "READY"], (1, 1))
    backend.read_fields.return_value = [_field(mainframe, 1, 7, 8)]
    terminal = mainframe.MainframeTerminal(backend)

    auto = terminal.field_after("User:")
    assert auto.position == (1, 7)
    assert auto.length == 8
    terminal.type_text("alice")
    terminal.press("Enter")
    terminal.move_cursor(1, 7)
    terminal.wait_ready(0.1)
    terminal.wait_change(0.1)
    assert terminal.is_keyboard_locked() == backend.is_keyboard_locked.return_value
    backend.send_string.assert_called_once_with("alice")
    backend.send_aid.assert_called_once_with("Enter")
    backend.move_cursor.assert_called_once_with(1, 7)

    assert terminal.wait_for_field(1, 7, timeout=0.1) is backend.read_fields.return_value[0]
    assert terminal.wait_for_field(1, 7, writable=False, timeout=0.1)
    terminal.wait_for_text("READY", timeout=0.1, poll_interval=0)
    terminal.wait_for_cursor(1, 1, timeout=0.1, poll_interval=0)
    terminal.wait_for(
        lambda term: term.text() == "User:    \nREADY    ", timeout=0.1, poll_interval=0
    )

    backend.read_fields.return_value = []
    fallback = terminal.field_after("User:")
    assert fallback.position == (1, 10)
    assert fallback.length == 20
    with pytest.raises(ValueError, match="always matches"):
        terminal.wait_for_text("", timeout=0.1)
    with pytest.raises(mainframe.MainframeError, match="not found"):
        terminal.field_after("Missing")

    backend.wait_output.side_effect = mainframe.MainframeError("temporary")
    terminal.connect("host", 23, session_type="3270", timeout=0.1)
    backend.connect.assert_called_once_with("host", 23, session_type="3270")
    terminal.disconnect()
    backend.disconnect.assert_called_once_with()
    with terminal:
        pass
    assert backend.disconnect.call_count == 2


def test_mainframe_factory_selects_supported_backends_and_finds_binary(monkeypatch) -> None:
    import dolphin_desktop._mainframe as mainframe

    s3270 = Mock(name="s3270")
    hllapi = Mock(name="hllapi")
    tn5250 = Mock(name="tn5250")
    monkeypatch.setattr(mainframe, "_S3270Backend", Mock(return_value=s3270))
    monkeypatch.setattr(mainframe, "_HLLAPIBackend", Mock(return_value=hllapi))
    monkeypatch.setattr(mainframe, "_Tn5250Backend", Mock(return_value=tn5250))

    def build(kind):
        return mainframe._build_terminal(
            backend=kind,
            ws3270_path="ws3270.exe",
            model="3279-2",
            codepage="cp273",
            session_id="B",
            hllapi_dll_path="hllapi.dll",
            extra_args=["-trace"],
            trace=True,
        )

    assert build("s3270")._backend is s3270
    assert build("hllapi")._backend is hllapi
    assert build("tn5250")._backend is tn5250
    with pytest.raises(mainframe.MainframeError, match="unknown mainframe backend"):
        build("invalid")

    import shutil

    monkeypatch.setattr(
        shutil,
        "which",
        lambda candidate: "C:\\tools\\s3270.exe" if candidate == "s3270" else None,
    )
    assert mainframe._find_s3270() == "C:\\tools\\s3270.exe"
