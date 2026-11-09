"""Tests for the Win32 seams exposed by :mod:`dolphin_desktop._runner`."""

from __future__ import annotations

import ctypes
import os
import threading
from contextlib import nullcontext
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
        self.inherited_handles: list[int] = []
        self.deleted_attributes = 0

    def GetCurrentProcess(self):  # noqa: N802
        return -1

    def DuplicateHandle(self, source, handle, target, output, access, inherit, options):  # noqa: N802
        output._obj.value = handle + 100
        return True

    def InitializeProcThreadAttributeList(self, attributes, count, flags, size):  # noqa: N802
        size._obj.value = 64
        return attributes is not None

    def UpdateProcThreadAttribute(self, attributes, flags, kind, handles, size, old, needed):  # noqa: N802
        assert kind == _runner._PROC_THREAD_ATTRIBUTE_HANDLE_LIST
        self.inherited_handles = list(handles)
        return True

    def DeleteProcThreadAttributeList(self, attributes):  # noqa: N802
        self.deleted_attributes += 1

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


def test_terminate_process_handle_delegates_and_reports_win32_failure(monkeypatch) -> None:
    terminate = Mock(side_effect=[True, False])
    monkeypatch.setattr(_runner._kernel32, "TerminateProcess", terminate)

    assert _runner.terminate_process_handle(303, exit_code=7) is None
    terminate.assert_called_once_with(303, 7)

    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(OSError, match="TerminateProcess failed: error 5"):
        _runner.terminate_process_handle(404)
    assert terminate.call_args_list[-1].args == (404, 1)


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
    assert kernel32.closed_handles == [112, 111, 110, 909]
    assert kernel32.inherited_handles == [110, 111, 112]
    assert kernel32.deleted_attributes == 1

    args = kernel32.create_process_args
    assert args is not None
    assert args[0] is None
    assert args[1] == _runner._build_cmd_string(["pytest", "C:\\my tests\\"])
    assert args[4] is True
    assert args[7] is None
    startup_info = args[8]._obj
    assert startup_info.cb == ctypes.sizeof(_runner._StartupInfoExW)
    assert startup_info.lpDesktop == "HiddenDesk"
    assert startup_info.dwFlags == _runner._STARTF_USESTDHANDLES
    assert (startup_info.hStdInput, startup_info.hStdOutput, startup_info.hStdError) == (
        110,
        111,
        112,
    )
    assert args[5] & _runner._EXTENDED_STARTUPINFO_PRESENT


def test_launch_on_desktop_can_leave_stdio_uninherited(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=1, process=2, thread=3)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)

    assert _runner.launch_on_desktop(["tool.exe"], inherit_stdio=False) == (1, 2)
    assert kernel32.std_handle_calls == []
    assert kernel32.closed_handles == [3]
    assert kernel32.create_process_args is not None
    assert kernel32.create_process_args[4] is False
    assert kernel32.create_process_args[1] == "tool.exe"
    assert kernel32.create_process_args[8]._obj.dwFlags == 0


def test_launch_on_desktop_reports_create_process_failure(monkeypatch) -> None:
    kernel32 = _FakeLauncher(create_result=False)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 740)

    with pytest.raises(OSError, match=r"CreateProcessW\('tool.exe'\) failed: error 740"):
        _runner.launch_on_desktop(["tool.exe"])
    assert kernel32.closed_handles == [112, 111, 110]
    assert kernel32.deleted_attributes == 1


@pytest.mark.parametrize("phase", ["duplicate", "initialize", "update"])
def test_stdio_setup_failure_releases_resources_without_starting_child(monkeypatch, phase):
    kernel32 = _FakeLauncher()
    if phase == "duplicate":
        duplicate = kernel32.DuplicateHandle
        kernel32.DuplicateHandle = lambda *args: duplicate(*args) if args[1] == 10 else False
    elif phase == "initialize":
        initialize = kernel32.InitializeProcThreadAttributeList
        kernel32.InitializeProcThreadAttributeList = lambda *args: (
            initialize(*args) if args[0] is None else False
        )
    else:
        kernel32.UpdateProcThreadAttribute = Mock(return_value=False)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 5)
    with pytest.raises(OSError):
        _runner.launch_on_desktop(["tool.exe"])
    assert kernel32.create_process_args is None
    assert kernel32.closed_handles == ([110] if phase == "duplicate" else [112, 111, 110])
    assert kernel32.deleted_attributes == (1 if phase == "update" else 0)


def test_missing_standard_handles_disable_handle_inheritance(monkeypatch):
    kernel32 = _FakeLauncher()
    kernel32.GetStdHandle = Mock(side_effect=[None, 0, ctypes.wintypes.HANDLE(-1).value])
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    _runner.launch_on_desktop(["tool.exe"])
    assert kernel32.create_process_args[4] is False
    assert kernel32.create_process_args[5] & _runner._EXTENDED_STARTUPINFO_PRESENT == 0
    assert kernel32.inherited_handles == []


def test_launch_cmd_on_desktop_forwards_command_work_dir_and_no_stdio(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    wait_for_idle = Mock()
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner._user32, "WaitForInputIdle", wait_for_idle)

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
    wait_for_idle.assert_not_called()


def test_launch_cmd_on_desktop_passes_private_environment_block(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setenv("DOLPHIN_PARENT_ENV", "parent-value")
    monkeypatch.delenv("DOLPHIN_CHILD_ENV", raising=False)

    assert _runner.launch_cmd_on_desktop(
        "tool.exe",
        None,
        env={"DOLPHIN_CHILD_ENV": "child-value"},
    ) == (111, 222)

    args = kernel32.create_process_args
    assert args is not None
    assert args[5] == _runner._CREATE_UNICODE_ENVIRONMENT
    assert isinstance(args[6], ctypes.c_void_p)
    assert args[6].value
    block = _runner._build_environment_block({"DOLPHIN_CHILD_ENV": "child-value"})
    text = "".join(block)
    assert "DOLPHIN_PARENT_ENV=parent-value\0" in text
    assert "DOLPHIN_CHILD_ENV=child-value\0" in text
    assert text.endswith("\0\0")
    assert os.environ.get("DOLPHIN_CHILD_ENV") is None


def test_environment_blocks_are_independent_under_concurrency(monkeypatch) -> None:
    monkeypatch.delenv("DOLPHIN_SHARED_ENV", raising=False)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}
    errors: list[BaseException] = []

    def build(value: str) -> None:
        try:
            barrier.wait(timeout=5)
            results[value] = "".join(
                _runner._build_environment_block({"DOLPHIN_SHARED_ENV": value})
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=build, args=(value,)) for value in ("A", "B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert all(f"DOLPHIN_SHARED_ENV={value}\0" in results[value] for value in ("A", "B"))
    assert os.environ.get("DOLPHIN_SHARED_ENV") is None


def test_launch_cmd_on_desktop_reports_create_process_failure(monkeypatch) -> None:
    kernel32 = _FakeLauncher(create_result=False)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 87)

    with pytest.raises(OSError, match=r"CreateProcessW\('tool.exe'\) failed: error 87"):
        _runner.launch_cmd_on_desktop("tool.exe")
    assert kernel32.closed_handles == []


def test_launch_cmd_on_desktop_waits_for_gui_readiness_with_timeout(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    wait_for_idle = Mock(return_value=0)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner._user32, "WaitForInputIdle", wait_for_idle)

    assert _runner.launch_cmd_on_desktop("tool.exe", timeout=2.5, wait_for_idle=True) == (
        111,
        222,
    )

    wait_for_idle.assert_called_once_with(222, 2500)


def test_launch_cmd_on_desktop_cleans_up_when_gui_readiness_times_out(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    terminate = Mock(return_value=True)
    kernel32.TerminateProcess = terminate  # type: ignore[attr-defined]
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(
        _runner._user32, "WaitForInputIdle", Mock(return_value=_runner._WAIT_TIMEOUT)
    )

    with pytest.raises(TimeoutError, match="did not become ready"):
        _runner.launch_cmd_on_desktop("tool.exe", timeout=2, wait_for_idle=True)

    terminate.assert_called_once_with(222, 1)
    assert kernel32.closed_handles == [333, 222]


def test_launch_cmd_on_desktop_skips_wait_when_timeout_is_zero(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    wait_for_idle = Mock()
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner._user32, "WaitForInputIdle", wait_for_idle)

    assert _runner.launch_cmd_on_desktop("tool.exe", timeout=0, wait_for_idle=True) == (
        111,
        222,
    )

    wait_for_idle.assert_not_called()


def test_launch_cmd_on_desktop_treats_wait_failed_as_ready(monkeypatch) -> None:
    kernel32 = _FakeLauncher(pid=111, process=222, thread=333)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(
        _runner._user32, "WaitForInputIdle", Mock(return_value=_runner._WAIT_FAILED)
    )

    # WAIT_FAILED is expected for console processes with no GUI message
    # queue; the process is still treated as ready rather than as an error.
    assert _runner.launch_cmd_on_desktop("tool.exe", timeout=2, wait_for_idle=True) == (
        111,
        222,
    )
    assert kernel32.closed_handles == [333]


def test_launch_cmd_on_desktop_rejects_negative_timeout_before_spawning(monkeypatch) -> None:
    kernel32 = _FakeLauncher()
    monkeypatch.setattr(_runner, "_kernel32", kernel32)

    with pytest.raises(ValueError, match="timeout must be a finite non-negative number"):
        _runner.launch_cmd_on_desktop("tool.exe", timeout=-1)

    assert kernel32.create_process_args is None


def test_run_hidden_executes_directly_when_already_on_hidden_desktop(monkeypatch) -> None:
    launch = Mock(return_value=(123, 456))
    create = Mock()
    monkeypatch.setattr(_runner, "get_current_desktop_name", lambda: _runner.DESKTOP_NAME)
    monkeypatch.setattr(_runner, "create_hidden_desktop", create)
    monkeypatch.setattr(_runner, "launch_on_desktop", launch)
    monkeypatch.setattr(_runner, "_console_interrupts", lambda: nullcontext(threading.Event()))
    monkeypatch.setattr(_runner, "_owned_job", lambda: nullcontext(99))
    monkeypatch.setattr(_runner, "_wait_for_command", lambda *args: 13)
    monkeypatch.setattr(_runner, "close_process_handle", Mock())

    assert _runner.run_hidden(["pytest", "-q"]) == 13
    launch.assert_called_once_with(
        ["pytest", "-q"],
        _runner.DESKTOP_NAME,
        inherit_stdio=True,
        job_handle=99,
        env={"DOLPHIN_HEADLESS": "1"},
    )
    create.assert_not_called()


@pytest.mark.parametrize("assign_ok,resume_result", [(True, 1), (False, 1), (True, 0xFFFFFFFF)])
def test_runner_assigns_suspended_child_before_resuming_and_cleans_up_failures(
    monkeypatch, assign_ok, resume_result
) -> None:
    kernel32 = _FakeLauncher()
    assign = Mock(return_value=assign_ok)
    resume = Mock(return_value=resume_result)
    terminate = Mock(return_value=True)
    wait = Mock(return_value=0)
    kernel32.AssignProcessToJobObject = assign
    kernel32.ResumeThread = resume
    kernel32.TerminateProcess = terminate
    kernel32.WaitForSingleObject = wait
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    if assign_ok and resume_result != 0xFFFFFFFF:
        assert _runner.launch_on_desktop(["python"], job_handle=99) == (1234, 5678)
        assert kernel32.closed_handles == [112, 111, 110, 9012]
        terminate.assert_not_called()
    else:
        with pytest.raises(OSError) as error:
            _runner.launch_on_desktop(["python"], job_handle=99)
        if not assign_ok:
            assert "AssignProcessToJobObject failed" in str(error.value)
            assert "existing Windows Job Object" in str(error.value)
        terminate.assert_called_once_with(5678, 1)
        wait.assert_called_once_with(5678, int(_runner._PROCESS_TERMINATION_TIMEOUT_SECONDS * 1000))
        assert kernel32.closed_handles == [112, 111, 110, 5678, 9012]
    assert kernel32.create_process_args[5] == (
        _runner._CREATE_SUSPENDED | _runner._EXTENDED_STARTUPINFO_PRESENT
    )
    assign.assert_called_once_with(99, 5678)
    if assign_ok:
        resume.assert_called_once_with(9012)
    else:
        resume.assert_not_called()


@pytest.mark.parametrize("phase", ["assign", "resume"])
@pytest.mark.parametrize(
    "outcome", ["terminated", "timeout", "wait-failed", "unexpected", "terminate-error"]
)
def test_failed_launch_cleanup_is_bounded_and_closes_handles(monkeypatch, phase, outcome):
    kernel32 = _FakeLauncher()
    kernel32.AssignProcessToJobObject = Mock(return_value=phase != "assign")
    kernel32.ResumeThread = Mock(return_value=0xFFFFFFFF)
    kernel32.TerminateProcess = Mock(return_value=outcome != "terminate-error")
    result = {
        "terminated": _runner._WAIT_OBJECT_0,
        "timeout": _runner._WAIT_TIMEOUT,
        "wait-failed": _runner._WAIT_FAILED,
        "unexpected": 0x80,
        "terminate-error": _runner._WAIT_OBJECT_0,
    }[outcome]
    kernel32.WaitForSingleObject = Mock(return_value=result)
    monkeypatch.setattr(_runner, "_kernel32", kernel32)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 5)
    monkeypatch.setattr(_runner, "_PROCESS_TERMINATION_TIMEOUT_SECONDS", 0.025)

    with pytest.raises(TimeoutError if outcome == "timeout" else OSError) as error:
        _runner.launch_on_desktop(["python"], job_handle=99)
    assert kernel32.closed_handles == [112, 111, 110, 5678, 9012]
    kernel32.TerminateProcess.assert_called_once_with(5678, 1)
    if outcome == "terminate-error":
        kernel32.WaitForSingleObject.assert_not_called()
    else:
        kernel32.WaitForSingleObject.assert_called_once_with(5678, 25)
    if outcome == "terminated":
        assert error.value.winerror == 5
    else:
        # Cleanup diagnostics retain the original assignment/resume error.
        assert isinstance(error.value.__context__, OSError)
        assert error.value.__context__.winerror == 5
        if outcome in ("wait-failed", "unexpected"):
            assert f"0x{result:08X}" in str(error.value)


@pytest.mark.parametrize("configure_ok", [True, False])
def test_owned_job_closes_its_handle_on_configuration_or_command_failure(
    monkeypatch, configure_ok
) -> None:
    create = Mock(return_value=99)
    configure = Mock(return_value=configure_ok)
    close = Mock()
    monkeypatch.setattr(_runner._kernel32, "CreateJobObjectW", create)
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", configure)
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close)
    monkeypatch.setattr(_runner, "_JobNotifications", Mock())
    cleanup = Mock()
    monkeypatch.setattr(_runner, "_terminate_and_wait_for_job", cleanup)
    with pytest.raises((OSError, RuntimeError)):
        with _runner._owned_job() as job:
            assert job == 99
            raise RuntimeError("command failed")
    close.assert_called_once_with(99)
    assert cleanup.call_count == int(configure_ok)


def test_console_handler_consumes_only_interrupts_and_is_unregistered(monkeypatch) -> None:
    handlers = []

    def register(callback, enabled):
        handlers.append((callback, enabled))
        return True

    monkeypatch.setattr(_runner._kernel32, "SetConsoleCtrlHandler", register)
    with pytest.raises(RuntimeError), _runner._console_interrupts() as interrupted:
        callback = handlers[0][0]
        assert not callback(2)  # CTRL_CLOSE_EVENT retains its existing semantics
        assert not interrupted.is_set()
        assert callback(1)
        assert interrupted.is_set()
        interrupted.clear()
        assert callback(0)
        assert interrupted.is_set()
        raise RuntimeError("command failed")
    assert handlers == [(callback, True), (callback, False)]


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

    assert launch.call_args.args == (["pytest"], _runner.DESKTOP_NAME)
    assert launch.call_args.kwargs["inherit_stdio"] is True
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
