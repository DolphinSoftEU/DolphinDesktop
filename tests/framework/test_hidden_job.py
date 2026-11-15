"""Owner lifetime and atomic job assignment for hidden Desktop launches."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from dolphin_desktop import _runner
from tests.framework.test_runner import _FakeLauncher


def test_concurrent_hidden_launches_share_one_owner_only_job(monkeypatch):
    create = Mock(return_value=123)
    register = Mock()
    monkeypatch.setattr(_runner, "_hidden_job", None)
    monkeypatch.setattr(_runner, "_create_kill_on_close_job", create)
    monkeypatch.setattr(_runner.atexit, "register", register)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _: _runner._hidden_launch_job(), range(32))) == [123] * 32
    create.assert_called_once_with()
    register.assert_called_once_with(_runner._kernel32.CloseHandle, 123)


def test_job_configuration_failure_closes_handle_and_can_retry(monkeypatch):
    create = Mock(return_value=123)
    configure = Mock(return_value=False)
    close = Mock()
    register = Mock()
    monkeypatch.setattr(_runner, "_hidden_job", None)
    monkeypatch.setattr(_runner._kernel32, "CreateJobObjectW", create)
    monkeypatch.setattr(_runner._kernel32, "SetInformationJobObject", configure)
    monkeypatch.setattr(_runner._kernel32, "CloseHandle", close)
    monkeypatch.setattr(_runner.atexit, "register", register)
    with pytest.raises(OSError):
        _runner._hidden_launch_job()
    assert _runner._hidden_job is None
    close.assert_called_once_with(123)
    register.assert_not_called()
    configure.return_value = True
    assert _runner._hidden_launch_job() == 123
    assert configure.call_args.args[2]._obj.BasicLimitInformation.LimitFlags == 0x2000
    create.assert_called_with(None, None)  # unnamed, no inheritable security attributes


@pytest.mark.parametrize("failure", [None, "initialize", "attribute", "create"])
def test_hidden_job_is_applied_at_creation_without_handle_inheritance(monkeypatch, failure):
    kernel = _FakeLauncher(create_result=failure != "create")
    update = Mock(return_value=failure != "attribute")
    kernel.UpdateProcThreadAttribute = update
    if failure == "initialize":
        kernel.InitializeProcThreadAttributeList = lambda *args: False
    monkeypatch.setattr(_runner, "_kernel32", kernel)
    if failure:
        with pytest.raises(OSError):
            _runner.launch_cmd_on_desktop("tool.exe", job_handle=123)
    else:
        assert _runner.launch_cmd_on_desktop("tool.exe", job_handle=123) == (1234, 5678)
        args = kernel.create_process_args
        assert args[4] is False
        assert args[5] & _runner._EXTENDED_STARTUPINFO_PRESENT
        assert args[8]._obj.cb == _runner.ctypes.sizeof(_runner._StartupInfoExW)
        assert kernel.closed_handles == [9012]
    if failure in ("initialize", "attribute"):
        assert kernel.create_process_args is None
    assert kernel.deleted_attributes == (0 if failure == "initialize" else 1)
    if failure != "initialize":
        assert update.call_args.args[2] == _runner._PROC_THREAD_ATTRIBUTE_JOB_LIST
        assert list(update.call_args.args[3]) == [123]
