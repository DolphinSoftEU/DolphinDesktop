"""Job cleanup ordering, delayed native termination and interrupt failure paths."""

from __future__ import annotations

import ctypes
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_job_notifications(monkeypatch):
    tracker = SimpleNamespace(
        processes={}, drain=Mock(), close=Mock(), pending=Mock(return_value=False)
    )
    monkeypatch.setattr(_runner, "_JobNotifications", lambda job: tracker)
    return tracker


@pytest.mark.parametrize("command_fails", [False, True])
def test_desktop_is_closed_only_after_delayed_job_termination(
    monkeypatch, command_fails, fake_job_notifications
):
    monkeypatch.setattr(_runner, "_seal_job", Mock())
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([]))
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
def test_cleanup_errors_are_reported_and_job_handle_is_closed(
    monkeypatch, failure, fake_job_notifications
):
    monkeypatch.setattr(_runner, "_seal_job", Mock())
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([]))
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

    ticks = iter([0.0, 11.0, 11.0])
    monkeypatch.setattr(_runner.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(_runner._kernel32, "WaitForSingleObject", wait)
    monkeypatch.setattr(_runner._kernel32, "GetExitCodeProcess", exit_code)
    terminate = Mock(side_effect=None if terminate_ok else OSError("termination failed"))
    monkeypatch.setattr(_runner, "_terminate_and_wait_for_job", terminate)
    if terminate_ok:
        assert _runner._wait_for_command(456, 99, interrupted) == 130
        assert len(calls) == 4
    else:
        with pytest.raises(OSError):
            _runner._wait_for_command(456, 99, interrupted)
        assert len(calls) == 3
    terminate.assert_called_once_with(99)


@pytest.mark.parametrize("eventually_exits", [False, True])
def test_forced_termination_has_a_separate_deadline(monkeypatch, eventually_exits):
    interrupted = threading.Event()
    interrupted.set()
    # Grace ends at t=10; termination is requested once at t=11. The command
    # remains unsignalled through t=20.9, then either exits or times out at t=21.
    ticks = iter([0.0, 11.0, 11.0, 12.0, 20.9, 21.0])
    monkeypatch.setattr(_runner.time, "monotonic", lambda: next(ticks))
    waits = iter([_runner._WAIT_TIMEOUT] * 4 + [_runner._WAIT_OBJECT_0])
    wait = Mock(
        side_effect=(lambda *args: next(waits)) if eventually_exits else None,
        return_value=_runner._WAIT_TIMEOUT,
    )
    monkeypatch.setattr(_runner._kernel32, "WaitForSingleObject", wait)
    terminate = Mock()
    monkeypatch.setattr(_runner, "_terminate_and_wait_for_job", terminate)

    def exit_code(handle, output):
        output._obj.value = 130
        return True

    get_exit = Mock(side_effect=exit_code)
    monkeypatch.setattr(_runner._kernel32, "GetExitCodeProcess", get_exit)
    if eventually_exits:
        assert _runner._wait_for_command(456, 99, interrupted) == 130
        get_exit.assert_called_once()
    else:
        with pytest.raises(TimeoutError, match="did not exit after forced termination"):
            _runner._wait_for_command(456, 99, interrupted)
        get_exit.assert_not_called()
    assert wait.call_count == 5
    terminate.assert_called_once_with(99)


@pytest.mark.parametrize("outcome", ["success", "timeout", "wait-error"])
def test_zero_job_count_does_not_replace_process_handle_synchronization(monkeypatch, outcome):
    monkeypatch.setattr(_runner, "_seal_job", Mock())
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([456]))
    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", Mock(return_value=True))
    states = iter([_runner._WAIT_TIMEOUT, _runner._WAIT_OBJECT_0])
    wait = Mock(
        side_effect=(lambda *args: next(states)) if outcome == "success" else None,
        return_value=_runner._WAIT_FAILED if outcome == "wait-error" else _runner._WAIT_TIMEOUT,
    )
    monkeypatch.setattr(_runner._kernel32, "WaitForSingleObject", wait)

    def query(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 0
        return True

    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", query)
    monkeypatch.setattr(_runner.time, "sleep", Mock())
    monkeypatch.setattr(_runner, "_JOB_CLEANUP_TIMEOUT_SECONDS", 0 if outcome == "timeout" else 10)
    if outcome == "success":
        _runner._terminate_and_wait_for_job(99)
        assert wait.call_count == 2
    else:
        with pytest.raises(TimeoutError if outcome == "timeout" else OSError):
            _runner._terminate_and_wait_for_job(99)


@pytest.mark.parametrize(
    "outcome",
    [
        "success",
        "retry",
        "partial-success",
        "partial-growing",
        "growing",
        "query-error",
        "exited",
        "denied",
        "membership-error",
        "reused",
    ],
)
def test_job_process_handles_pin_members_and_release_handles_on_errors(monkeypatch, outcome):
    queries = []

    def query(job, kind, output, size, returned):
        assert (job, kind) == (99, 3)
        queries.append(size)
        header = ctypes.cast(output, ctypes.POINTER(ctypes.wintypes.DWORD))
        header[0], header[1] = 1, 1
        (ctypes.c_size_t * 1).from_buffer(output, 8)[0] = 42
        if outcome == "growing" or (outcome == "retry" and len(queries) == 1):
            ctypes.set_last_error(234)
            return False
        if outcome == "partial-growing" or (outcome == "partial-success" and len(queries) == 1):
            header[0] = 20
            return True
        if outcome == "query-error":
            ctypes.set_last_error(5)
            return False
        return True

    def open_process(access, inherit, pid):
        assert (access, inherit, pid) == (0x101000, False, 42)
        if outcome in ("exited", "denied"):
            ctypes.set_last_error(87 if outcome == "exited" else 5)
            return 0
        return 456

    def member(handle, job, output):
        output._obj.value = outcome != "reused"
        return outcome != "membership-error"

    close = Mock()
    monkeypatch.setattr(
        _runner,
        "_kernel32",
        SimpleNamespace(
            QueryInformationJobObject=query,
            OpenProcess=open_process,
            IsProcessInJob=member,
            CloseHandle=close,
        ),
    )
    errors = {"growing", "partial-growing", "query-error", "denied", "membership-error"}
    if outcome in errors:
        with pytest.raises(OSError), _runner._job_process_handles(99):
            pytest.fail("invalid process snapshot was accepted")
    else:
        with _runner._job_process_handles(99) as handles:
            assert handles == ([] if outcome in ("exited", "reused") else [456])
    assert close.call_count == int(
        outcome in {"success", "retry", "partial-success", "membership-error", "reused"}
    )
    if outcome in {"retry", "partial-success"}:
        assert len(queries) == 2 and queries[1] > queries[0]
    elif outcome in {"growing", "partial-growing"}:
        assert len(queries) == 4
