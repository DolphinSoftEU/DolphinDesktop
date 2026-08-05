"""Tests for dolphin-run's command-line quoting and child exit-code handling."""

from __future__ import annotations

import ctypes

import pytest

from dolphin_desktop import _runner

# CreateProcessW takes a single string that the child splits with CommandLineToArgvW


def _round_trip(args: list[str]) -> list[str]:
    """Split the built command line the way a CRT / CPython child would."""
    cmd = _runner._build_cmd_string(args)
    argv = ctypes.windll.shell32.CommandLineToArgvW
    argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    count = ctypes.c_int(0)
    ptr = argv(cmd, ctypes.byref(count))
    try:
        return [ptr[i] for i in range(count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(ptr)


class TestBuildCmdString:
    @pytest.mark.parametrize(
        "args",
        [
            ["pytest", "tests/"],
            ["pytest", "C:\\my tests\\"],
            ["pytest", "-k", "test one or test two"],
            ["pytest", '--x=a"b'],
            ["pytest", "C:\\dir with space\\", "-q"],
            ["pytest", ""],
            ["py", "back\\\\slashes\\\\"],
        ],
    )
    def test_child_sees_exactly_the_arguments_given(self, args):
        """A trailing backslash before the closing quote swallows the rest of the line."""
        assert _round_trip(args) == args

    def test_plain_tokens_are_not_gratuitously_quoted(self):
        assert _runner._build_cmd_string(["pytest", "tests/"]) == "pytest tests/"


# An unchecked wait turns a failed wait into a fabricated exit code


class _FakeKernel32:
    def __init__(self, wait_result: int, exit_code: int = 0, get_exit_ok: bool = True) -> None:
        self.wait_result = wait_result
        self.exit_code = exit_code
        self.get_exit_ok = get_exit_ok
        self.closed: list[int] = []

    def WaitForSingleObject(self, handle, timeout):  # noqa: N802
        return self.wait_result

    def GetExitCodeProcess(self, handle, out):  # noqa: N802
        out._obj.value = self.exit_code
        return self.get_exit_ok

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        return True


@pytest.fixture
def hidden_desktop(monkeypatch):
    monkeypatch.setattr(_runner, "get_current_desktop_name", lambda: "Default")
    monkeypatch.setattr(_runner, "create_hidden_desktop", lambda *a, **k: 0x1234)
    monkeypatch.setattr(_runner, "close_desktop", lambda handle: None)
    monkeypatch.setattr(_runner, "launch_on_desktop", lambda *a, **k: (4242, 0xABCD))


class TestRunHiddenExitCode:
    def test_successful_wait_returns_the_child_exit_code(self, hidden_desktop, monkeypatch):
        fake = _FakeKernel32(_runner._WAIT_OBJECT_0, exit_code=3)
        monkeypatch.setattr(_runner, "_kernel32", fake)
        assert _runner.run_hidden(["pytest"]) == 3

    def test_failed_wait_does_not_report_a_fabricated_exit_code(self, hidden_desktop, monkeypatch):
        """WAIT_FAILED leaves the child running; GetExitCodeProcess then yields 259."""
        fake = _FakeKernel32(0xFFFFFFFF, exit_code=259)
        monkeypatch.setattr(_runner, "_kernel32", fake)
        with pytest.raises(OSError, match="WaitForSingleObject"):
            _runner.run_hidden(["pytest"])

    def test_failed_get_exit_code_is_reported(self, hidden_desktop, monkeypatch):
        fake = _FakeKernel32(_runner._WAIT_OBJECT_0, get_exit_ok=False)
        monkeypatch.setattr(_runner, "_kernel32", fake)
        with pytest.raises(OSError, match="GetExitCodeProcess"):
            _runner.run_hidden(["pytest"])

    def test_process_handle_is_closed_even_on_a_failed_wait(self, hidden_desktop, monkeypatch):
        fake = _FakeKernel32(0xFFFFFFFF)
        monkeypatch.setattr(_runner, "_kernel32", fake)
        with pytest.raises(OSError):
            _runner.run_hidden(["pytest"])
        assert fake.closed == [0xABCD]

    def test_headless_env_var_is_not_left_behind(self, hidden_desktop, monkeypatch):
        import os

        monkeypatch.setattr(_runner, "_kernel32", _FakeKernel32(_runner._WAIT_OBJECT_0))
        _runner.run_hidden(["pytest"])
        assert "DOLPHIN_HEADLESS" not in os.environ

    def test_a_caller_set_headless_var_is_restored(self, hidden_desktop, monkeypatch):
        """run_hidden borrows the variable for the child; it does not own it."""
        import os

        monkeypatch.setenv("DOLPHIN_HEADLESS", "0")
        monkeypatch.setattr(_runner, "_kernel32", _FakeKernel32(_runner._WAIT_OBJECT_0))
        _runner.run_hidden(["pytest"])
        assert os.environ["DOLPHIN_HEADLESS"] == "0"
