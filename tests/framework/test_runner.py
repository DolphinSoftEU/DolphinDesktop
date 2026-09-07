"""Tests for the Win32 seams exposed by :mod:`dolphin_desktop._runner`."""

from __future__ import annotations

import ctypes
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner


class _FakeLauncher:
    """Small ``kernel32`` double that fills the process-information struct."""

    def __init__(
        self,
        *,
        create_result: bool = True,
        pid: int = 1234,
        process: int = 5678,
        thread: int = 9012,
    ) -> None:
        self.create_result = create_result
        self.pid = pid
        self.process = process
        self.thread = thread
        self.std_handle_calls: list[int] = []
        self.create_process_args: tuple[object, ...] | None = None
        self.closed_handles: list[int] = []

    def GetStdHandle(self, kind):  # noqa: N802
        self.std_handle_calls.append(kind.value)
        return {4294967286: 10, 4294967285: 11, 4294967284: 12}[kind.value]

    def CreateProcessW(self, *args):  # noqa: N802
        self.create_process_args = args
        if self.create_result:
            process_info = args[-1]._obj
            process_info.hProcess = self.process
            process_info.hThread = self.thread
            process_info.dwProcessId = self.pid
        return self.create_result

    def CloseHandle(self, handle):  # noqa: N802
        self.closed_handles.append(handle)
        return True


def test_create_hidden_desktop_success_and_failure(monkeypatch) -> None:
    create = Mock(return_value=0x123456789)
    monkeypatch.setattr(_runner._user32, "CreateDesktopW", create)

    assert _runner.create_hidden_desktop("CustomDesk") == 0x123456789
    create.assert_called_once_with("CustomDesk", None, None, 0, _runner._DESKTOP_ACCESS, None)

    create.reset_mock()
    create.return_value = 0
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(OSError, match=r"CreateDesktopW\('CustomDesk'\) failed: error 5"):
        _runner.create_hidden_desktop("CustomDesk")


def test_close_desktop_and_process_handle_delegate_to_win32(monkeypatch) -> None:
    close_desktop = Mock()
    close_handle = Mock()
    monkeypatch.setattr(_runner._user32, "CloseDesktop", close_desktop)
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close_handle)

    assert _runner.close_desktop(101) is None
    assert _runner.close_process_handle(202) is None
    close_desktop.assert_called_once_with(101)
    close_handle.assert_called_once_with(202)


def test_switch_thread_to_desktop_success_and_failure(monkeypatch) -> None:
    switch = Mock(return_value=True)
    monkeypatch.setattr(_runner._user32, "SetThreadDesktop", switch)
    _runner.switch_thread_to_desktop(303)
    switch.assert_called_once_with(303)

    switch.reset_mock()
    switch.return_value = False
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 170)
    with pytest.raises(OSError, match="SetThreadDesktop failed: error 170"):
        _runner.switch_thread_to_desktop(303)


def test_get_current_desktop_name_uses_thread_and_user_object_information(monkeypatch) -> None:
    get_thread_id = Mock(return_value=404)
    get_thread_desktop = Mock(return_value=505)

    def get_information(handle, index, buffer, buffer_size, needed):
        assert handle == 505
        assert index == 2
        assert buffer_size == 512
        buffer.value = "CustomDesk"
        needed._obj.value = 11
        return True

    get_information = Mock(side_effect=get_information)
    monkeypatch.setattr(_runner._kernel32, "GetCurrentThreadId", get_thread_id)
    monkeypatch.setattr(_runner._user32, "GetThreadDesktop", get_thread_desktop)
    monkeypatch.setattr(_runner._user32, "GetUserObjectInformationW", get_information)

    assert _runner.get_current_desktop_name() == "CustomDesk"
    get_thread_id.assert_called_once_with()
    get_thread_desktop.assert_called_once_with(404)
    get_information.assert_called_once()


@pytest.mark.parametrize(
    ("window_station", "information_result", "flags", "expected"),
    [
        (0, True, 0, False),
        (606, False, 0, False),
        (606, True, 0, False),
        (606, True, _runner._WSF_VISIBLE, True),
    ],
)
def test_has_interactive_station_covers_missing_query_and_visibility_results(
    monkeypatch,
    window_station: int,
    information_result: bool,
    flags: int,
    expected: bool,
) -> None:
    get_station = Mock(return_value=window_station)

    def get_information(_handle, _index, output, _size, _needed):
        output._obj.dwFlags = flags
        return information_result

    get_information = Mock(side_effect=get_information)
    monkeypatch.setattr(_runner._user32, "GetProcessWindowStation", get_station)
    monkeypatch.setattr(_runner._user32, "GetUserObjectInformationW", get_information)

    assert _runner.has_interactive_station() is expected
    get_station.assert_called_once_with()
    if window_station:
        get_information.assert_called_once()
    else:
        get_information.assert_not_called()


def test_launch_on_desktop_forwards_stdio_and_process_structures(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=707, process=808, thread=909)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)

    assert _runner.launch_on_desktop(["pytest", "C:\\my tests\\"], "HiddenDesk") == (707, 808)
    assert kernel32.std_handle_calls == [4294967286, 4294967285, 4294967284]
    assert kernel32.closed_handles == [909]

    args = kernel32.create_process_args
    assert args is not None
    assert args[0] is None
    assert args[1] == _runner._build_cmd_string(["pytest", "C:\\my tests\\"])
    assert args[4] is True
    assert args[7] is None
    startup_info = args[8]._obj
    assert startup_info.cb == ctypes.sizeof(_runner._StartupInfoW)
    assert startup_info.lpDesktop == "HiddenDesk"
    assert startup_info.dwFlags == _runner._STARTF_USESTDHANDLES
    assert (startup_info.hStdInput, startup_info.hStdOutput, startup_info.hStdError) == (10, 11, 12)


def test_launch_on_desktop_can_leave_stdio_uninherited(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=1, process=2, thread=3)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)

    assert _runner.launch_on_desktop(["tool.exe"], inherit_stdio=False) == (1, 2)
    assert kernel32.std_handle_calls == []
    assert kernel32.closed_handles == [3]
    assert kernel32.create_process_args is not None
    assert kernel32.create_process_args[4] is True
    assert kernel32.create_process_args[1] == "tool.exe"
    assert kernel32.create_process_args[8]._obj.dwFlags == 0


def test_launch_on_desktop_reports_create_process_failure(monkeypatch) -> None:
    kernel32 = _FakeLauncher(create_result=False)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 740)

    with pytest.raises(OSError, match=r"CreateProcessW\('tool.exe'\) failed: error 740"):
        _runner.launch_on_desktop(["tool.exe"])
    assert kernel32.closed_handles == []


def test_launch_cmd_on_desktop_forwards_command_work_dir_and_no_stdio(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)

    assert _runner.launch_cmd_on_desktop("tool.exe --flag", "Desk", work_dir=r"C:\work") == (
        111,
        222,
    )
    assert kernel32.std_handle_calls == []
    assert kernel32.closed_handles == [333]
    assert kernel32.create_process_args is not None
    args = kernel32.create_process_args
    assert args[1] == "tool.exe --flag"
    assert args[4] is False
    assert args[7] == r"C:\work"
    startup_info = args[8]._obj
    assert startup_info.cb == ctypes.sizeof(_runner._StartupInfoW)
    assert startup_info.lpDesktop == "Desk"


def test_launch_cmd_on_desktop_reports_create_process_failure(monkeypatch) -> None:
    kernel32 = _FakeLauncher(create_result=False)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 87)

    with pytest.raises(OSError, match=r"CreateProcessW\('tool.exe'\) failed: error 87"):
        _runner.launch_cmd_on_desktop("tool.exe")
    assert kernel32.closed_handles == []


def test_run_hidden_executes_directly_when_already_on_hidden_desktop(monkeypatch) -> None:
    run = Mock(return_value=SimpleNamespace(returncode=13))
    monkeypatch.setattr(_runner, "get_current_desktop_name", lambda: _runner.DESKTOP_NAME)
    monkeypatch.setattr(subprocess, "run", run)

    assert _runner.run_hidden(["pytest", "-q"]) == 13
    run.assert_called_once_with(["pytest", "-q"])


def test_run_hidden_restores_environment_and_closes_desktop_on_launch_failure(monkeypatch) -> None:
    create = Mock(return_value=7070)
    launch = Mock(side_effect=RuntimeError("child could not start"))
    close = Mock()
    monkeypatch.setattr(_runner, "get_current_desktop_name", lambda: "Default")
    monkeypatch.setattr(_runner, "create_hidden_desktop", create)
    monkeypatch.setattr(_runner, "launch_on_desktop", launch)
    monkeypatch.setattr(_runner, "close_desktop", close)
    monkeypatch.setenv("DOLPHIN_HEADLESS", "caller-value")

    with pytest.raises(RuntimeError, match="child could not start"):
        _runner.run_hidden(["pytest"])

    launch.assert_called_once_with(["pytest"], _runner.DESKTOP_NAME, inherit_stdio=True)
    assert os.environ["DOLPHIN_HEADLESS"] == "caller-value"
    close.assert_called_once_with(7070)


def test_main_prints_usage_without_a_command(monkeypatch, capsys) -> None:
    monkeypatch.setattr(_runner.sys, "argv", ["dolphin-run"])

    with pytest.raises(SystemExit) as error:
        _runner.main()

    assert error.value.code == 1
    assert capsys.readouterr().err == (
        "Usage: dolphin-run <command> [args...]\n"
        "\n"
        "Examples:\n"
        "  dolphin-run pytest tests/\n"
        "  dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia\n"
    )


def test_main_prints_help_without_launching_a_hidden_desktop(monkeypatch, capsys) -> None:
    run_hidden = Mock()
    monkeypatch.setattr(_runner.sys, "argv", ["dolphin-run", "--help"])
    monkeypatch.setattr(_runner, "run_hidden", run_hidden)

    with pytest.raises(SystemExit) as error:
        _runner.main()

    assert error.value.code == 0
    assert capsys.readouterr().out == (
        "Usage: dolphin-run <command> [args...]\n"
        "\n"
        "Examples:\n"
        "  dolphin-run pytest tests/\n"
        "  dolphin-run pytest tests/ -k test_notepad --dolphin-backend=uia\n"
    )
    run_hidden.assert_not_called()


def test_main_passes_command_arguments_to_run_hidden_and_exits(monkeypatch) -> None:
    run_hidden = Mock(return_value=23)
    monkeypatch.setattr(_runner.sys, "argv", ["dolphin-run", "pytest", "tests/"])
    monkeypatch.setattr(_runner, "run_hidden", run_hidden)

    with pytest.raises(SystemExit) as error:
        _runner.main()

    assert error.value.code == 23
    run_hidden.assert_called_once_with(["pytest", "tests/"])


def test_runner_command_builder_round_trips_spaces_and_trailing_backslashes() -> None:
    from dolphin_desktop._runner import _build_cmd_string

    command = _build_cmd_string(["app.exe", r"C:\path with spaces\\", 'say "hello"'])
    assert command.startswith("app.exe ")
    assert "path with spaces" in command
    assert '\\"hello\\"' in command


def test_runner_quotes_windows_command_arguments() -> None:
    from dolphin_desktop._runner import _build_cmd_string

    assert _build_cmd_string(["python.exe", "script with spaces.py", "--name", "A B"]) == (
        'python.exe "script with spaces.py" --name "A B"'
    )
