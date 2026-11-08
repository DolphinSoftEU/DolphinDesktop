"""Job cleanup ordering, delayed native termination and interrupt failure paths."""

from __future__ import annotations

import threading
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("command_fails", [False, True])
def test_desktop_is_closed_only_after_delayed_job_termination(monkeypatch, command_fails):
    events = []
    remaining = iter([2, 1, 0])
    monkeypatch.setattr(_runner._kernel32, "CreateJobObjectW", Mock(return_value=99))
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", Mock(return_value=True))

    def terminate(job, code):
        assert (job, code) == (99, 130)
        events.append("terminate")
        return True

    def query(job, kind, output, size, returned):
        assert (job, kind) == (99, 1)
        output._obj.ActiveProcesses = next(remaining)
        events.append(f"active={output._obj.ActiveProcesses}")
        return True

    def close_job(job):
        assert job == 99
        assert events[-1] == "active=0"
        events.append("close-job")

    def close_desktop(desktop):
        assert desktop == 88
        assert events[-1] == "close-job"
        events.append("close-desktop")

    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", terminate)
    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", query)
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close_job)
    monkeypatch.setattr(_runner.time, "sleep", lambda delay: events.append("wait"))
    monkeypatch.setattr(_runner, "get_current_desktop_name", lambda: "Default")
    monkeypatch.setattr(_runner, "create_hidden_desktop", lambda: 88)
    monkeypatch.setattr(_runner, "close_desktop", close_desktop)
    monkeypatch.setattr(_runner, "launch_on_desktop", lambda *a, **kw: (123, 456))
    monkeypatch.setattr(_runner, "close_process_handle", Mock())
    wait = Mock(
        return_value=0, side_effect=RuntimeError("command failed") if command_fails else None
    )
    monkeypatch.setattr(_runner, "_wait_for_command", wait)

    if command_fails:
        with pytest.raises(RuntimeError, match="command failed"):
            _runner.run_hidden(["command"])
    else:
        assert _runner.run_hidden(["command"]) == 0
    assert events == [
        "terminate",
        "active=2",
        "wait",
        "active=1",
        "wait",
        "active=0",
        "close-job",
        "close-desktop",
    ]


@pytest.mark.parametrize("failure", ["terminate", "query", "timeout"])
def test_cleanup_errors_are_reported_and_job_handle_is_closed(monkeypatch, failure):
    monkeypatch.setattr(_runner._kernel32, "CreateJobObjectW", Mock(return_value=99))
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", Mock(return_value=True))
    monkeypatch.setattr(
        _runner._kernel32, "TerminateJobObject", Mock(return_value=failure != "terminate")
    )

    def query(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 1
        return failure != "query"

    close = Mock()
    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", query)
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close)
    monkeypatch.setattr(_runner, "_JOB_CLEANUP_TIMEOUT_SECONDS", 0)
    with pytest.raises(TimeoutError if failure == "timeout" else OSError):
        with _runner._owned_job():
            pass
    close.assert_called_once_with(99)


def test_job_creation_failure_does_not_close_an_invalid_handle(monkeypatch):
    monkeypatch.setattr(_runner._kernel32, "CreateJobObjectW", Mock(return_value=0))
    close = Mock()
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close)
    with pytest.raises(OSError), _runner._owned_job():
        pytest.fail("invalid job was yielded")
    close.assert_not_called()


def test_console_handler_registration_failure_is_reported(monkeypatch):
    register = Mock(return_value=False)
    monkeypatch.setattr(_runner._kernel32, "SetConsoleCtrlHandler", register)
    with pytest.raises(OSError), _runner._console_interrupts():
        pytest.fail("missing console handler was accepted")
    assert register.call_count == 1


@pytest.mark.parametrize("terminate_ok", [False, True])
def test_interrupt_grace_expires_before_forcing_termination(monkeypatch, terminate_ok):
    interrupted = threading.Event()
    waits = iter([_runner._WAIT_TIMEOUT] * 3 + [_runner._WAIT_OBJECT_0])
    calls = []

    def wait(handle, timeout):
        calls.append((handle, timeout))
        if len(calls) == 2:
            interrupted.set()
        return next(waits)

    def exit_code(handle, output):
        output._obj.value = 130
        return True

    ticks = iter([0.0, 11.0])
    monkeypatch.setattr(_runner.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(_runner._kernel32, "WaitForSingleObject", wait)
    monkeypatch.setattr(_runner._kernel32, "GetExitCodeProcess", exit_code)
    terminate = Mock(return_value=terminate_ok)
    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", terminate)
    if terminate_ok:
        assert _runner._wait_for_command(456, 99, interrupted) == 130
        assert len(calls) == 4
    else:
        with pytest.raises(OSError):
            _runner._wait_for_command(456, 99, interrupted)
        assert len(calls) == 3
    terminate.assert_called_once_with(99, 130)
