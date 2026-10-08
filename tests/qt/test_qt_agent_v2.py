"""Qt 5 and Qt 6 authenticated agent integration smoke tests."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import sys
import threading
import time
from importlib.util import find_spec

import pytest

from dolphin_desktop import Desktop, QtAgentRpcError, sleep
from dolphin_desktop._qt_inject import QtAgentClient
from tests.qt._qt_helpers import (
    QT5_SCRIPT,
    QT5_WINDOW_TITLE,
    QT6_SCRIPT,
    QT6_WINDOW_TITLE,
    launch_demo,
)

pytestmark = pytest.mark.qt_agent

_TOKEN_QUERY = 0x0008
_TOKEN_DUPLICATE = 0x0002
_TOKEN_IMPERSONATE = 0x0004
_TOKEN_GROUPS = 2
_SE_GROUP_LOGON_ID = 0xC0000000
_SECURITY_IMPERSONATION = 2
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_ERROR_ACCESS_DENIED = 5
_ERROR_FILE_NOT_FOUND = 2
_ERROR_PIPE_BUSY = 231
_ERROR_BROKEN_PIPE = 109
_ERROR_NO_DATA = 232
_ERROR_PIPE_NOT_CONNECTED = 233


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", ctypes.c_void_p), ("attributes", wt.DWORD)]


class _TokenGroupsHeader(ctypes.Structure):
    _fields_ = [("group_count", wt.DWORD), ("groups", _SidAndAttributes * 1)]


def _assert_logon_sid_is_required(pipe_name: str) -> None:
    """Try the pipe with this process's logon SID disabled in its thread token."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wt.HANDLE
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL
    kernel32.CreateFileW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
        wt.HANDLE,
    ]
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
    kernel32.WaitNamedPipeW.restype = wt.BOOL
    advapi32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.OpenProcessToken.restype = wt.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.POINTER(wt.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wt.BOOL
    advapi32.CreateRestrictedToken.argtypes = [
        wt.HANDLE,
        wt.DWORD,
        wt.DWORD,
        ctypes.POINTER(_SidAndAttributes),
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(wt.HANDLE),
    ]
    advapi32.CreateRestrictedToken.restype = wt.BOOL
    advapi32.DuplicateToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    advapi32.DuplicateToken.restype = wt.BOOL
    advapi32.SetThreadToken.argtypes = [ctypes.c_void_p, wt.HANDLE]
    advapi32.SetThreadToken.restype = wt.BOOL
    advapi32.RevertToSelf.restype = wt.BOOL

    process_token = wt.HANDLE()
    restricted_token = wt.HANDLE()
    impersonation_token = wt.HANDLE()
    impersonating = False
    try:
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            _TOKEN_QUERY | _TOKEN_DUPLICATE,
            ctypes.byref(process_token),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        required = wt.DWORD()
        advapi32.GetTokenInformation(process_token, _TOKEN_GROUPS, None, 0, ctypes.byref(required))
        if not required.value:
            raise ctypes.WinError(ctypes.get_last_error())
        storage = (ctypes.c_byte * required.value)()
        if not advapi32.GetTokenInformation(
            process_token,
            _TOKEN_GROUPS,
            ctypes.byref(storage),
            required,
            ctypes.byref(required),
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        group_count = ctypes.cast(storage, ctypes.POINTER(wt.DWORD)).contents.value
        groups = (_SidAndAttributes * group_count).from_address(
            ctypes.addressof(storage) + _TokenGroupsHeader.groups.offset
        )
        logon_sid = next(
            (
                group.sid
                for group in groups
                if group.attributes & _SE_GROUP_LOGON_ID == _SE_GROUP_LOGON_ID
            ),
            None,
        )
        assert logon_sid is not None, "current process token has no logon SID"
        disabled_sid = _SidAndAttributes(logon_sid, 0)
        if not advapi32.CreateRestrictedToken(
            process_token,
            0,
            1,
            ctypes.byref(disabled_sid),
            0,
            None,
            0,
            None,
            ctypes.byref(restricted_token),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi32.DuplicateToken(
            restricted_token, _SECURITY_IMPERSONATION, ctypes.byref(impersonation_token)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if not advapi32.SetThreadToken(None, impersonation_token):
            raise ctypes.WinError(ctypes.get_last_error())
        impersonating = True

        invalid_handle = ctypes.c_void_p(-1).value
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            handle = kernel32.CreateFileW(
                pipe_name,
                _GENERIC_READ | _GENERIC_WRITE,
                0,
                None,
                _OPEN_EXISTING,
                _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
                None,
            )
            if handle not in (None, invalid_handle):
                kernel32.CloseHandle(handle)
                raise AssertionError("pipe allowed a client without its logon SID")
            error = ctypes.get_last_error()
            if error == _ERROR_ACCESS_DENIED:
                return
            if error not in (_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY):
                raise ctypes.WinError(error)
            kernel32.WaitNamedPipeW(pipe_name, 100)
        raise AssertionError("pipe never became available for the logon SID probe")
    finally:
        if impersonating and not advapi32.RevertToSelf():
            raise ctypes.WinError(ctypes.get_last_error())
        for token in (impersonation_token, restricted_token, process_token):
            if token:
                kernel32.CloseHandle(token)


def _assert_remote_named_pipe_client_is_rejected(pipe_name: str) -> None:
    """Open through the localhost SMB namespace and require remote rejection."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wt.LPCWSTR,
        wt.DWORD,
        wt.DWORD,
        ctypes.c_void_p,
        wt.DWORD,
        wt.DWORD,
        wt.HANDLE,
    ]
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.WaitNamedPipeW.argtypes = [wt.LPCWSTR, wt.DWORD]
    kernel32.WaitNamedPipeW.restype = wt.BOOL
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    kernel32.CloseHandle.restype = wt.BOOL

    remote_name = "\\\\localhost\\pipe\\" + pipe_name.rsplit("\\", 1)[-1]
    invalid_handle = ctypes.c_void_p(-1).value
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        handle = kernel32.CreateFileW(
            remote_name,
            _GENERIC_READ | _GENERIC_WRITE,
            0,
            None,
            _OPEN_EXISTING,
            _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
            None,
        )
        if handle not in (None, invalid_handle):
            kernel32.CloseHandle(handle)
            raise AssertionError("remote named-pipe client connected to the agent")
        error = ctypes.get_last_error()
        if error == _ERROR_ACCESS_DENIED:
            return
        if error not in (_ERROR_FILE_NOT_FOUND, _ERROR_PIPE_BUSY):
            raise ctypes.WinError(error)
        kernel32.WaitNamedPipeW(remote_name, 100)
    raise AssertionError("remote named-pipe probe did not reach the agent pipe")


def _assert_foreign_process_is_rejected(pipe_name: str, auth_token: str) -> None:
    """Connect from another same-logon process and require the server to drop it."""
    probe = r"""
import ctypes
import sys
import time

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
]
kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
kernel32.WaitNamedPipeW.restype = ctypes.c_int
kernel32.WriteFile.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
]
kernel32.WriteFile.restype = ctypes.c_int
kernel32.PeekNamedPipe.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
]
kernel32.PeekNamedPipe.restype = ctypes.c_int
kernel32.ReadFile.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
]
kernel32.ReadFile.restype = ctypes.c_int
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CloseHandle.restype = ctypes.c_int

pipe_name, secret = sys.argv[1:]
invalid = ctypes.c_void_p(-1).value
if not kernel32.WaitNamedPipeW(pipe_name, 5000):
    raise SystemExit(f"WaitNamedPipeW failed: {ctypes.get_last_error()}")
pipe = kernel32.CreateFileW(
    pipe_name, 0x80000000 | 0x40000000, 0, None, 3, 0x00100000 | 0x00010000, None
)
if pipe is None or pipe == invalid:
    raise SystemExit(f"CreateFileW failed: {ctypes.get_last_error()}")
try:
    request = (
        '{"id":1,"op":"ping","auth":"' + secret + '"}\n'
    ).encode("utf-8")
    written = ctypes.c_uint32()
    if not kernel32.WriteFile(pipe, request, len(request), ctypes.byref(written), None):
        error = ctypes.get_last_error()
        if error in (109, 232, 233):
            raise SystemExit(0)
        raise SystemExit(f"WriteFile failed unexpectedly: {error}")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        available = ctypes.c_uint32()
        if not kernel32.PeekNamedPipe(pipe, None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error in (109, 232, 233):
                raise SystemExit(0)
            raise SystemExit(f"PeekNamedPipe failed unexpectedly: {error}")
        if available.value:
            buffer = ctypes.create_string_buffer(available.value)
            read = ctypes.c_uint32()
            kernel32.ReadFile(pipe, buffer, available.value, ctypes.byref(read), None)
            response = buffer.raw[:read.value].decode()
            raise SystemExit(
                "foreign process received an agent response: " + response
            )
        time.sleep(0.02)
    raise SystemExit("server kept the foreign process connection open")
finally:
    kernel32.CloseHandle(pipe)
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, pipe_name, auth_token],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == 0, (
        "agent accepted an unexpected client process or failed to close the connection: "
        f"exit={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}"
    )


@pytest.mark.parametrize(
    ("binding", "version", "script", "title"),
    [
        ("PyQt5", "5", QT5_SCRIPT, QT5_WINDOW_TITLE),
        ("PySide6", "6", QT6_SCRIPT, QT6_WINDOW_TITLE),
    ],
    ids=["qt5", "qt6"],
)
def test_authenticated_agent_attach_auth_and_stop_restart(binding, version, script, title):
    """Exercise the shipped v2 DLL against a live Qt 5 or Qt 6 process."""
    assert find_spec(binding) is not None, f"required Qt test binding {binding} is not installed"

    app, _window = launch_demo(Desktop(), script, title)
    try:
        sleep(0.5)
        assert app.qt_version() == version

        agent = app.qt_agent
        assert agent.ping() == "pong"
        assert agent.find(className="QPushButton")

        # The server must reject a wrong secret without poisoning the pipe.
        session_secret = agent._auth_token
        assert session_secret
        agent._auth_token = "wrong-session-secret"
        with pytest.raises(QtAgentRpcError, match="unauthorized"):
            agent.ping()
        agent._auth_token = session_secret
        assert agent.ping() == "pong"

        # Two Application/client objects reuse the same authenticated server.
        second = QtAgentClient.attach(app.process_id, version)
        assert second._pipe_name == agent._pipe_name
        assert second.ping() == "pong"

        # Closing one owner must not stop the server used by the other.
        agent.close()
        assert second.ping() == "pong"

        # The last explicit close stops the server; reattach starts it again.
        second.close()
        assert agent.reattach().ping() == "pong"
    finally:
        try:
            if "second" in locals():
                second.close()
        except Exception:
            pass
        try:
            agent.close()
        except Exception:
            pass
        try:
            app.kill()
        except Exception:
            pass


@pytest.mark.parametrize(
    ("binding", "version"),
    [("PyQt5", "5"), ("PySide6", "6")],
    ids=["qt5", "qt6"],
)
def test_native_object_lifetime_overloads_and_bounded_stop(binding, version, tmp_path):
    """Exercise QObject invalidation, overload selection, and stop while GUI is blocked."""
    assert find_spec(binding) is not None, f"required Qt test binding {binding} is not installed"

    if binding == "PyQt5":
        slot = "Slot"
        qt_imports = (
            "from PyQt5.QtCore import QObject, pyqtSlot as Slot\n"
            "from PyQt5.QtWidgets import (\n"
            "    QApplication, QPushButton, QVBoxLayout, QWidget,\n"
            ")"
        )
    else:
        slot = "Slot"
        qt_imports = (
            "from PySide6.QtCore import QObject, Slot\n"
            "from PySide6.QtWidgets import (\n"
            "    QApplication, QPushButton, QVBoxLayout, QWidget,\n"
            ")"
        )

    title = f"Dolphin Agent Lifetime Qt{version}"
    started_file = tmp_path / f"blocked-{version}.txt"
    script = tmp_path / f"agent-lifetime-qt{version}.py"
    script.write_text(
        f"""import sys
import time
from pathlib import Path
{qt_imports}

BLOCK_MARKER = {str(started_file)!r}
app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle({title!r})
layout = QVBoxLayout(window)

class Controller(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.window = parent
        self.widget = None
        self.replace()

    @{slot}()
    def replace(self):
        if self.widget is not None:
            self.widget.deleteLater()
        self.widget = QPushButton("live", self.window)
        self.widget.setObjectName("replaceable")
        layout.addWidget(self.widget)

    @{slot}(int)
    @{slot}(str)
    def choose(self, value):
        pass

    @{slot}()
    def block_gui(self):
        Path(BLOCK_MARKER).write_text("started", encoding="utf-8")
        time.sleep(5)

controller = Controller(window)
controller.setObjectName("agent_controller")
window.controller = controller
window.show()
app.exec()
""",
        encoding="utf-8",
    )

    app, _window = launch_demo(Desktop(), script, title)
    try:
        agent = app.qt_agent
        controller = agent.find(objectName="agent_controller")[0]["handle"]
        original = agent.find(objectName="replaceable")[0]["handle"]

        assert agent.invoke(controller, "replace")["ok"] is True
        sleep(0.3)
        recreated = agent.find(objectName="replaceable")[0]["handle"]
        assert recreated != original
        assert agent.describe(original)["ok"] is False
        assert agent.describe(recreated)["ok"] is True

        ambiguous = agent.invoke(controller, "choose", 7)
        assert ambiguous["ok"] is False
        assert "ambiguous" in ambiguous["error"]
        assert agent.invoke(controller, "choose(int)", 7)["ok"] is True

        errors = []

        def invoke_blocking_slot():
            try:
                agent.invoke(controller, "block_gui")
            except Exception as exc:
                errors.append(exc)

        rpc_thread = threading.Thread(target=invoke_blocking_slot)
        rpc_thread.start()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not started_file.exists():
            sleep(0.05)
        assert started_file.exists(), "GUI slot did not start"

        started = time.monotonic()
        agent.close()
        elapsed = time.monotonic() - started
        assert elapsed < 3.0
        rpc_thread.join(timeout=2)
        assert not rpc_thread.is_alive()
        assert errors
    finally:
        try:
            app.kill()
        except Exception:
            pass


@pytest.mark.parametrize(
    ("binding", "version", "script", "title"),
    [
        ("PyQt5", "5", QT5_SCRIPT, QT5_WINDOW_TITLE),
        ("PySide6", "6", QT6_SCRIPT, QT6_WINDOW_TITLE),
    ],
    ids=["qt5", "qt6"],
)
def test_agent_pipe_security_boundaries(binding, version, script, title):
    """Verify logon SID ACLs, client PID checks, and the remote-client pipe flag."""
    assert find_spec(binding) is not None, f"required Qt test binding {binding} is not installed"

    app, _window = launch_demo(Desktop(), script, title)
    try:
        agent = app.qt_agent
        assert agent.ping() == "pong"
        pipe_name = agent._pipe_name
        auth_token = agent._auth_token
        assert pipe_name and auth_token

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetNamedPipeInfo.argtypes = [
            wt.HANDLE,
            ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(wt.DWORD),
        ]
        kernel32.GetNamedPipeInfo.restype = wt.BOOL
        pipe_flags = wt.DWORD()
        if not kernel32.GetNamedPipeInfo(agent._pipe, ctypes.byref(pipe_flags), None, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        assert pipe_flags.value & _PIPE_REJECT_REMOTE_CLIENTS, (
            "agent pipe is not configured to reject remote named-pipe clients"
        )

        # Free the authorized instance without stopping the server so probes
        # can test the remote flag and logon SID ACL at the pipe boundary.
        agent._close_pipe()
        _assert_remote_named_pipe_client_is_rejected(pipe_name)
        _assert_logon_sid_is_required(pipe_name)

        # A child shares the allowed logon SID, but its process ID differs from
        # the one authorized at agent startup. The server must disconnect it.
        _assert_foreign_process_is_rejected(pipe_name, auth_token)
        assert agent.reattach().ping() == "pong"
    finally:
        try:
            agent.close()
        except Exception:
            pass
        try:
            app.kill()
        except Exception:
            pass
