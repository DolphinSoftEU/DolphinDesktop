"""Shared console-handler subscriptions and callback lifetime on removal failure."""

from __future__ import annotations

import ctypes
import gc
import os
import threading
import weakref
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated_handler_state(monkeypatch):
    # Every registration in this module is simulated. Restore the real manager
    # even when a regression leaves a subscription or a failed removal behind.
    monkeypatch.setattr(_runner, "_console_registration_lock", threading.Lock())
    monkeypatch.setattr(_runner, "_console_subscribers", ())
    monkeypatch.setattr(_runner, "_console_handler_registered", False)


@pytest.mark.parametrize("signal", [0, 1])
def test_parallel_scopes_share_one_handler_and_receive_the_same_signal(monkeypatch, signal):
    register = Mock(return_value=True)
    monkeypatch.setattr(_runner._kernel32, "SetConsoleCtrlHandler", register)
    ready = threading.Barrier(3)
    release = [threading.Event(), threading.Event()]
    subscribers = {}
    errors = []

    def worker(index):
        try:
            with _runner._console_interrupts() as interrupted:
                subscribers[index] = interrupted
                ready.wait(timeout=5)
                assert release[index].wait(5)
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=worker, args=(index,), daemon=True) for index in range(2)]
    try:
        for worker_thread in workers:
            worker_thread.start()
        ready.wait(timeout=5)
        register.assert_called_once_with(_runner._console_control_handler, True)
        assert _runner._console_control_handler(signal)
        assert all(event.is_set() for event in subscribers.values())
        for event in subscribers.values():
            event.clear()
        release[0].set()
        workers[0].join(timeout=5)
        assert not workers[0].is_alive()
        assert register.call_count == 1
        assert _runner._console_control_handler(signal)
        assert not subscribers[0].is_set()
        assert subscribers[1].is_set()
    finally:
        for release_event in release:
            release_event.set()
        for worker_thread in workers:
            worker_thread.join(timeout=5)
    assert not errors
    assert all(not worker_thread.is_alive() for worker_thread in workers)
    assert [call.args[1] for call in register.call_args_list] == [True, False]
    assert not _runner._console_control_handler(signal)


def test_callback_does_not_wait_for_the_registration_lock(monkeypatch):
    interrupted = threading.Event()
    monkeypatch.setattr(_runner, "_console_subscribers", (interrupted,))
    worker = threading.Thread(target=lambda: _runner._console_control_handler(1), daemon=True)
    with _runner._console_registration_lock:
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert interrupted.is_set()


@pytest.mark.parametrize("body_fails", [False, True])
def test_failed_removal_keeps_callback_alive_and_retries_without_duplicate_registration(
    monkeypatch, body_fails
):
    outcomes = iter([True, False, True])
    native_calls = []

    def register(callback, enabled):
        # Model Windows retaining only a raw address, not a Python reference.
        native_calls.append((ctypes.cast(callback, ctypes.c_void_p).value, enabled))
        return next(outcomes)

    monkeypatch.setattr(_runner._kernel32, "SetConsoleCtrlHandler", register)
    monkeypatch.setattr(_runner.ctypes, "get_last_error", lambda: 5)
    reference = weakref.ref(_runner._console_control_handler)
    with pytest.raises(OSError) as failure:
        with _runner._console_interrupts() as first:
            if body_fails:
                raise RuntimeError("command failed")
    assert failure.value.winerror == 5
    if body_fails:
        assert isinstance(failure.value.__context__, RuntimeError)
    gc.collect()
    assert reference() is not None
    assert _runner._console_handler_registered
    assert _runner._console_subscribers == ()
    first.clear()
    assert not reference()(1)
    assert not first.is_set()
    with _runner._console_interrupts() as second:
        assert len(native_calls) == 2  # failed removal did not lead to another add
        assert reference()(1)
        assert second.is_set()
        assert not first.is_set()
    assert [enabled for _, enabled in native_calls] == [True, False, False]
    assert len({address for address, _ in native_calls}) == 1
    assert not _runner._console_handler_registered
    assert not reference()(1)


def test_registration_failure_rolls_back_the_subscription(monkeypatch):
    monkeypatch.setattr(_runner._kernel32, "SetConsoleCtrlHandler", Mock(return_value=False))
    with pytest.raises(OSError), _runner._console_interrupts():
        pytest.fail("registration failure was ignored")
    assert not _runner._console_handler_registered
    assert _runner._console_subscribers == ()
    assert not _runner._console_control_handler(1)


def test_launch_passes_a_private_headless_environment_without_parent_mutation(monkeypatch):
    monkeypatch.setenv("DOLPHIN_HEADLESS", "0")
    monkeypatch.setenv("RUNNER_TEST", "value")
    buffers = []
    build_environment = _runner._build_environment_block

    def retain_buffer(overrides):
        buffer = build_environment(overrides)
        buffers.append(buffer)
        return buffer

    def create_process(*args):
        info = args[-1]._obj
        info.dwProcessId, info.hProcess, info.hThread = 123, 456, 789
        return True

    api = SimpleNamespace(
        CreateProcessW=Mock(side_effect=create_process),
        AssignProcessToJobObject=Mock(return_value=True),
        ResumeThread=Mock(return_value=1),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(_runner, "_kernel32", api)
    monkeypatch.setattr(_runner, "_build_environment_block", retain_buffer)
    assert _runner.launch_on_desktop(
        ["python"], inherit_stdio=False, job_handle=99, env={"DOLPHIN_HEADLESS": "1"}
    ) == (123, 456)
    args = api.CreateProcessW.call_args.args
    assert args[5] == _runner._CREATE_SUSPENDED | _runner._CREATE_UNICODE_ENVIRONMENT
    assert args[6].value == ctypes.addressof(buffers[0])
    assert "DOLPHIN_HEADLESS=1\0" in "".join(buffers[0])
    assert "RUNNER_TEST=value\0" in "".join(buffers[0])
    assert os.environ["DOLPHIN_HEADLESS"] == "0"
