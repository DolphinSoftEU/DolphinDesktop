"""Job birth notifications close the snapshot-to-termination race."""

from __future__ import annotations

import ctypes
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner

pytestmark = pytest.mark.unit


def _tracker(monkeypatch, packets, *, open_error=0):
    pending = iter(packets)

    def dequeue(port, message, key, pid, timeout):
        assert (port, timeout) == (88, 0)
        packet = next(pending, None)
        if packet is None or packet == "error":
            ctypes.set_last_error(258 if packet is None else 5)
            return False
        message._obj.value, key._obj.value, pid._obj.value = packet
        return True

    def open_process(access, inherit, pid):
        assert (access, inherit) == (0x100000, False)
        if open_error:
            ctypes.set_last_error(open_error)
            return 0
        return pid + 1000

    api = SimpleNamespace(
        CreateIoCompletionPort=Mock(return_value=88),
        SetInformationJobObject=Mock(return_value=True),
        GetQueuedCompletionStatus=dequeue,
        OpenProcess=Mock(side_effect=open_process),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(_runner, "_kernel32", api)
    tracker = _runner._JobNotifications(99)
    association = api.SetInformationJobObject.call_args.args
    assert association[:2] == (99, 7)
    assert association[2]._obj.CompletionKey == 99
    assert association[2]._obj.CompletionPort == 88
    return tracker, api


def test_notifications_retain_each_process_until_cleanup_even_after_exit(monkeypatch):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42), (6, 99, 42), (7, 99, 42), (6, 99, 43)])
    try:
        tracker.drain()
        assert tracker.processes == {42: 1042, 43: 1043}
        assert api.OpenProcess.call_count == 2
        api.CloseHandle.assert_not_called()
    finally:
        tracker.close()
    assert [call.args[0] for call in api.CloseHandle.call_args_list] == [1042, 1043, 88]


@pytest.mark.parametrize("packets", [["error"], [(6, 1, 42)], [(6, 99, 0)]])
def test_invalid_completion_packets_are_errors(monkeypatch, packets):
    tracker, api = _tracker(monkeypatch, packets)
    try:
        with pytest.raises(OSError):
            tracker.drain()
    finally:
        tracker.close()
    api.CloseHandle.assert_called_once_with(88)


@pytest.mark.parametrize("open_error", [5, 87])
def test_unopenable_births_are_checked_instead_of_silently_lost(monkeypatch, open_error):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42)], open_error=open_error)
    try:
        if open_error == 87:
            tracker.drain()
            assert tracker.processes == {42: None}
        else:
            with pytest.raises(OSError):
                tracker.drain()
            assert tracker.processes == {}
    finally:
        tracker.close()
    api.CloseHandle.assert_called_once_with(88)


def test_notification_flood_cannot_starve_deadlines(monkeypatch):
    tracker, api = _tracker(monkeypatch, [(6, 99, pid) for pid in range(1, 301)])
    try:
        tracker.drain()
        assert len(tracker.processes) == 256
        tracker.drain()
        assert len(tracker.processes) == 300
    finally:
        tracker.close()
    assert api.CloseHandle.call_count == 301


@pytest.mark.parametrize("port_ok", [True, False])
def test_completion_port_setup_failures_release_owned_handles(monkeypatch, port_ok):
    close = Mock()
    monkeypatch.setattr(
        _runner._kernel32, "CreateIoCompletionPort", Mock(return_value=88 if port_ok else 0)
    )
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", Mock(return_value=False))
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close)
    with pytest.raises(OSError):
        _runner._JobNotifications(99)
    assert close.call_count == int(port_ok)


def test_birth_after_snapshot_and_before_termination_is_still_synchronized(monkeypatch):
    tracker = SimpleNamespace(processes={42: 1042})
    terminated = False
    events = []

    def drain():
        if terminated:
            tracker.processes[43] = 1043

    tracker.drain = drain
    monkeypatch.setitem(_runner._job_notifications, 99, tracker)
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([1042]))

    def terminate(job, code):
        nonlocal terminated
        assert 43 not in tracker.processes
        terminated = True
        return True

    late_states = iter([_runner._WAIT_TIMEOUT, _runner._WAIT_OBJECT_0])

    def wait(handle, timeout):
        state = next(late_states) if handle == 1043 else _runner._WAIT_OBJECT_0
        events.append((handle, state))
        return state

    def accounting(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 0
        output._obj.TotalProcesses = 2
        return True

    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", terminate)
    monkeypatch.setattr(_runner._kernel32, "WaitForSingleObject", wait)
    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", accounting)
    monkeypatch.setattr(_runner.time, "sleep", Mock())
    _runner._terminate_and_wait_for_job(99)
    assert [state for handle, state in events if handle == 1043] == [258, 0]


def test_lost_notification_cannot_produce_success_with_zero_active_processes(monkeypatch):
    tracker = SimpleNamespace(processes={42: None}, drain=Mock())
    monkeypatch.setitem(_runner._job_notifications, 99, tracker)
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([]))
    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", Mock(return_value=True))

    def accounting(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 0
        output._obj.TotalProcesses = 2
        return True

    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", accounting)
    monkeypatch.setattr(_runner, "_JOB_CLEANUP_TIMEOUT_SECONDS", 0)
    with pytest.raises(TimeoutError, match="unconfirmed births"):
        _runner._terminate_and_wait_for_job(99)


def test_normal_command_wait_services_queued_birth_notifications(monkeypatch):
    tracker = SimpleNamespace(drain=Mock())
    monkeypatch.setitem(_runner._job_notifications, 99, tracker)
    monkeypatch.setattr(_runner._kernel32, "WaitForSingleObject", Mock(side_effect=[258, 0]))

    def exit_code(handle, output):
        output._obj.value = 23
        return True

    monkeypatch.setattr(_runner._kernel32, "GetExitCodeProcess", exit_code)
    assert _runner._wait_for_command(456, 99, threading.Event()) == 23
    assert tracker.drain.call_count == 2
