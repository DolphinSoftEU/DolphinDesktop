"""Advisory job notifications and final process-admission sealing."""

from __future__ import annotations

import ctypes
import threading
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner

pytestmark = pytest.mark.unit


def _tracker(monkeypatch, packets, *, open_error=0, member=True, membership_ok=True):
    pending = deque(packets)

    def dequeue(port, message, key, pid, timeout):
        assert (port, timeout) == (88, 0)
        packet = pending.popleft() if pending else None
        if packet is None or packet == "error":
            ctypes.set_last_error(258 if packet is None else 5)
            return False
        message._obj.value, key._obj.value, pid._obj.value = packet
        return True

    def open_process(access, inherit, pid):
        assert (access, inherit) == (0x101000, False)
        if open_error:
            ctypes.set_last_error(open_error)
            return 0
        return pid + 1000

    def in_job(handle, job, output):
        assert job == 99
        output._obj.value = member
        return membership_ok

    api = SimpleNamespace(
        CreateIoCompletionPort=Mock(return_value=88),
        SetInformationJobObject=Mock(return_value=True),
        GetQueuedCompletionStatus=dequeue,
        OpenProcess=Mock(side_effect=open_process),
        IsProcessInJob=Mock(side_effect=in_job),
        WaitForSingleObject=Mock(return_value=258),
        CloseHandle=Mock(),
        packets=pending,
    )
    monkeypatch.setattr(_runner, "_kernel32", api)
    tracker = _runner._JobNotifications(99)
    association = api.SetInformationJobObject.call_args.args
    assert association[:2] == (99, 7)
    assert association[2]._obj.CompletionKey == 99
    assert association[2]._obj.CompletionPort == 88
    return tracker, api


def test_notifications_retain_verified_live_processes_without_duplicate_handles(monkeypatch):
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
def test_invalid_completion_packets_do_not_abort_the_command(monkeypatch, packets):
    tracker, api = _tracker(monkeypatch, packets)
    try:
        tracker.drain()
        assert tracker.processes == {}
        api.OpenProcess.assert_not_called()
    finally:
        tracker.close()
    api.CloseHandle.assert_called_once_with(88)


@pytest.mark.parametrize("open_error", [5, 87])
def test_unopenable_notifications_fall_back_to_final_cleanup(monkeypatch, open_error):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42)], open_error=open_error)
    try:
        tracker.drain()
        assert tracker.processes == {}
        api.IsProcessInJob.assert_not_called()
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


def test_late_notification_is_still_synchronized_after_sealing(monkeypatch):
    monkeypatch.setattr(_runner, "_seal_job", Mock())
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

    tracker.pending = lambda: wait(1043, 0) == 258

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


def test_lost_historical_notification_does_not_reject_an_empty_job(monkeypatch):
    tracker = SimpleNamespace(processes={}, drain=Mock(), pending=Mock(return_value=False))
    monkeypatch.setitem(_runner._job_notifications, 99, tracker)
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([]))
    monkeypatch.setattr(_runner, "_seal_job", Mock())
    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", Mock(return_value=True))

    def accounting(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 0
        output._obj.TotalProcesses = 2
        return True

    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", accounting)
    monkeypatch.setattr(_runner, "_JOB_CLEANUP_TIMEOUT_SECONDS", 0)
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


@pytest.mark.parametrize("outcome", ["success", "query-error", "set-error"])
def test_sealing_rejects_new_members_and_preserves_existing_limits(monkeypatch, outcome):
    def query(job, kind, output, size, returned):
        assert (job, kind) == (99, 9)
        output._obj.BasicLimitInformation.LimitFlags = 0x2100
        output._obj.BasicLimitInformation.ActiveProcessLimit = 8
        output._obj.ProcessMemoryLimit = 123456
        return outcome != "query-error"

    configure = Mock(return_value=outcome != "set-error")
    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", query)
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", configure)
    if outcome == "success":
        _runner._seal_job(99)
        job, kind, output, _size = configure.call_args.args
        assert (job, kind) == (99, 9)
        assert output._obj.BasicLimitInformation.LimitFlags == 0x2108
        assert output._obj.BasicLimitInformation.ActiveProcessLimit == 0
        assert output._obj.ProcessMemoryLimit == 123456
    else:
        with pytest.raises(OSError):
            _runner._seal_job(99)
        assert configure.call_count == int(outcome == "set-error")


def test_admission_is_sealed_before_snapshot_even_when_all_notifications_are_missing(monkeypatch):
    events = []
    monkeypatch.setattr(_runner, "_seal_job", lambda job: events.append("seal"))

    def snapshot(job):
        assert events == ["seal"]
        events.append("snapshot")
        return nullcontext([])

    def accounting(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 0
        output._obj.TotalProcesses = 999
        return True

    monkeypatch.setitem(
        _runner._job_notifications,
        99,
        SimpleNamespace(processes={}, drain=Mock(), pending=Mock(return_value=False)),
    )
    monkeypatch.setattr(_runner, "_job_process_handles", snapshot)
    monkeypatch.setattr(_runner._kernel32, "TerminateJobObject", Mock(return_value=True))
    monkeypatch.setattr(_runner._kernel32, "QueryInformationJobObject", accounting)
    _runner._terminate_and_wait_for_job(99)


@pytest.mark.parametrize("membership_ok", [False, True])
def test_recycled_or_unverifiable_pid_is_never_retained_or_waited_on(monkeypatch, membership_ok):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42)], member=False, membership_ok=membership_ok)
    try:
        tracker.drain()
        assert tracker.processes == {}
        api.CloseHandle.assert_called_once_with(1042)
        api.WaitForSingleObject.assert_not_called()
    finally:
        tracker.close()
    assert [call.args[0] for call in api.CloseHandle.call_args_list] == [1042, 88]


@pytest.mark.parametrize("completion", [0, 0xFFFFFFFF])
def test_finished_or_failed_advisory_handles_are_released_before_job_ends(monkeypatch, completion):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42)])
    tracker.drain()
    assert tracker.processes == {42: 1042}
    api.WaitForSingleObject.return_value = completion
    assert not tracker.pending()
    assert tracker.processes == {}
    api.CloseHandle.assert_called_once_with(1042)
    # A second reaper pass must not close the handle again.
    assert not tracker.pending()
    tracker.close()
    assert [call.args[0] for call in api.CloseHandle.call_args_list] == [1042, 88]


@pytest.mark.parametrize("new_member", [False, True])
def test_pid_reuse_after_reaping_requires_fresh_membership_validation(monkeypatch, new_member):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42)])
    tracker.drain()
    api.WaitForSingleObject.return_value = 0
    tracker.pending()
    assert tracker.processes == {}

    def membership(handle, job, output):
        output._obj.value = new_member
        return True

    api.IsProcessInJob.side_effect = membership
    api.packets.append((6, 99, 42))
    api.WaitForSingleObject.return_value = 258
    api.WaitForSingleObject.reset_mock()
    tracker.drain()
    assert api.OpenProcess.call_count == 2
    assert api.IsProcessInJob.call_count == 2
    if new_member:
        assert tracker.processes == {42: 1042}
    else:
        assert tracker.processes == {}
        api.WaitForSingleObject.assert_not_called()
    tracker.close()


def test_many_finished_processes_do_not_accumulate_handles_or_pid_tombstones(monkeypatch):
    tracker, api = _tracker(monkeypatch, [])
    for pid in range(1, 1001):
        api.packets.append((6, 99, pid))
        api.WaitForSingleObject.return_value = 258
        tracker.drain()
        assert len(tracker.processes) == 1
        api.WaitForSingleObject.return_value = 0
        assert not tracker.pending()
        assert tracker.processes == {}
    assert api.CloseHandle.call_count == 1000
    tracker.close()
    assert api.CloseHandle.call_count == 1001


def test_monitoring_access_denied_does_not_replace_exit_code_or_cleanup_result(monkeypatch):
    tracker, api = _tracker(monkeypatch, [(6, 99, 42)], open_error=5)
    monkeypatch.setitem(_runner._job_notifications, 99, tracker)
    monkeypatch.setattr(_runner, "_seal_job", Mock())
    monkeypatch.setattr(_runner, "_job_process_handles", lambda job: nullcontext([]))
    api.WaitForSingleObject.return_value = 0  # the actual command has exited
    api.TerminateJobObject = Mock(return_value=True)

    def accounting(job, kind, output, size, returned):
        output._obj.ActiveProcesses = 0
        return True

    def exit_code(handle, output):
        output._obj.value = 23
        return True

    api.QueryInformationJobObject = accounting
    api.GetExitCodeProcess = exit_code
    try:
        result = _runner._wait_for_command(456, 99, threading.Event())
        _runner._terminate_and_wait_for_job(99)
        assert result == 23
        assert tracker.processes == {}
    finally:
        tracker.close()


def test_unavailable_completion_port_falls_back_to_primary_cleanup(monkeypatch):
    monkeypatch.delitem(_runner._job_notifications, 99, raising=False)
    monkeypatch.setattr(_runner._kernel32, "CreateJobObjectW", Mock(return_value=99))
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", Mock(return_value=True))
    monkeypatch.setattr(_runner, "_JobNotifications", Mock(side_effect=OSError("port unavailable")))
    cleanup = Mock()
    close = Mock()
    monkeypatch.setattr(_runner, "_terminate_and_wait_for_job", cleanup)
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close)
    with _runner._owned_job() as job:
        assert job == 99
        assert job not in _runner._job_notifications
        result = 23
    assert result == 23
    cleanup.assert_called_once_with(99)
    close.assert_called_once_with(99)
