"""Unit tests for the shared quit-and-confirm helper of the live Outlook suites.

Driven with a fake process table and a fake ``taskkill`` so they need no
Outlook and run in CI. The helper must return only once the pid is really gone:
an ineffective ``taskkill`` or a pid that survives the kill is an error.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tests.office import _outlook_process as proc

pytestmark = pytest.mark.unit

PID = 4242


class _Table:
    """A fake process snapshot; ``kill_works`` decides whether taskkill removes the pid."""

    def __init__(self, *, alive: bool = True, returncode: int = 0, kill_works: bool = True):
        self.alive = alive
        self.returncode = returncode
        self.kill_works = kill_works
        self.kills: list[int] = []

    def pids(self) -> list[int]:
        return [PID] if self.alive else []

    def taskkill(self, pid: int) -> subprocess.CompletedProcess[str]:
        self.kills.append(pid)
        if self.kill_works:
            self.alive = False
        stderr = "" if self.returncode == 0 else "ERROR: Access is denied."
        return subprocess.CompletedProcess(["taskkill"], self.returncode, "", stderr)


@pytest.fixture
def table(monkeypatch):
    t = _Table()
    monkeypatch.setattr(proc, "outlook_pids", t.pids)
    monkeypatch.setattr(proc, "_taskkill", t.taskkill)
    monkeypatch.setattr(proc, "_POLL_SECONDS", 0.001)
    return t


_FAST = {"grace": 0.01, "reap": 0.01}


def test_a_process_that_exits_on_its_own_is_not_killed(table):
    table.alive = False
    proc.confirm_process_gone(PID, **_FAST)
    assert table.kills == []


def test_a_successful_kill_returns_once_the_pid_is_gone(table):
    proc.confirm_process_gone(PID, **_FAST)
    assert table.kills == [PID]
    assert PID not in table.pids()


def test_an_ineffective_taskkill_is_an_error_with_its_diagnostics(table):
    table.returncode = 1
    table.kill_works = False
    with pytest.raises(RuntimeError, match="could not be force-killed") as info:
        proc.confirm_process_gone(PID, **_FAST)
    assert "exit code 1" in str(info.value)
    assert "Access is denied" in str(info.value)


def test_a_pid_that_survives_a_successful_taskkill_is_an_error(table):
    table.kill_works = False  # taskkill says 0, but the pid never disappears
    with pytest.raises(RuntimeError, match="still running"):
        proc.confirm_process_gone(PID, **_FAST)


def test_a_failing_taskkill_is_fine_when_the_process_already_exited(table):
    # "process not found": it exited between the last poll and the kill.
    table.returncode = 128
    proc.confirm_process_gone(PID, **_FAST)
    assert table.kills == [PID]


class _App:
    def __init__(self, *, pid: int | None = PID, quit_error: Exception | None = None):
        self.process_id = pid
        self._quit_error = quit_error
        self.quits: list[bool] = []

    def quit(self, *, save_changes: bool = False) -> None:
        self.quits.append(save_changes)
        if self._quit_error is not None:
            raise self._quit_error


def test_quit_passes_save_changes_and_confirms_the_exit(table):
    app = _App()
    proc.quit_and_confirm_gone(app, save_changes=True, **_FAST)
    assert app.quits == [True]
    assert PID not in table.pids()


def test_quit_that_asks_for_retry_cleanup_still_ends_in_a_confirmed_exit(table):
    app = _App(quit_error=RuntimeError("did not exit after Quit; retry cleanup"))
    proc.quit_and_confirm_gone(app, **_FAST)
    assert table.kills == [PID]


def test_quit_never_reports_success_while_the_process_still_runs(table):
    # Quit is ignored and taskkill is refused: the helper must raise, so a test
    # relying on it can never PASS with OUTLOOK.EXE still alive.
    table.returncode = 1
    table.kill_works = False
    app = _App(quit_error=RuntimeError("did not exit after Quit; retry cleanup"))
    with pytest.raises(RuntimeError, match=f"pid {PID}"):
        proc.quit_and_confirm_gone(app, **_FAST)
    assert PID in table.pids()


def test_an_unexpected_quit_error_propagates(table):
    app = _App(quit_error=RuntimeError("COM server crashed"))
    with pytest.raises(RuntimeError, match="COM server crashed"):
        proc.quit_and_confirm_gone(app, **_FAST)
    assert table.kills == []


def test_an_app_without_an_owned_process_only_quits(table):
    app = _App(pid=None)
    proc.quit_and_confirm_gone(app, **_FAST)
    assert app.quits == [False]
    assert table.kills == []


def test_both_live_suites_share_the_same_helper():
    from tests.office import test_outlook_live as live
    from tests.office import test_outlook_live_more as more

    assert live._quit_and_confirm_gone is proc.quit_and_confirm_gone
    assert more._quit_and_confirm_gone is proc.quit_and_confirm_gone
    assert not hasattr(live, "_confirm_process_gone")
    assert not hasattr(more, "_confirm_process_gone")


def test_taskkill_result_is_captured_not_ignored(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen.update(args=args, **kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(proc.subprocess, "run", fake_run)
    proc._taskkill(PID)
    assert seen["args"] == ["taskkill", "/F", "/PID", str(PID)]
    assert seen["capture_output"] is True


# ---------------------------------------------------------------------------
# LocalServer32 parsing
# ---------------------------------------------------------------------------

_OFFICE = r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # quoted path with spaces, with and without arguments
        (rf'"{_OFFICE}" /automation', _OFFICE),
        (rf'"{_OFFICE}" /automation /embedding', _OFFICE),
        (rf'"{_OFFICE}"', _OFFICE),
        (rf'  "{_OFFICE}"   /automation  ', _OFFICE),
        # bare path with spaces, with and without arguments
        (rf"{_OFFICE} /automation", _OFFICE),
        (_OFFICE, _OFFICE),
        # bare path without spaces
        (r"C:\Office\outlook.exe /automation", r"C:\Office\outlook.exe"),
        (
            r"C:\PROGRA~1\MICROS~1\Office16\OUTLOOK.EXE",
            r"C:\PROGRA~1\MICROS~1\Office16\OUTLOOK.EXE",
        ),
    ],
)
def test_local_server32_yields_the_bare_executable_path(value, expected):
    result = proc.parse_local_server32(value)
    assert result == expected
    assert '"' not in result


@pytest.mark.parametrize("value", ["", "   ", r'"C:\Office\OUTLOOK.EXE /automation', '"" /x'])
def test_malformed_local_server32_is_rejected(value):
    with pytest.raises(ValueError, match="LocalServer32"):
        proc.parse_local_server32(value)


def test_both_live_suites_share_the_local_server32_reader():
    from tests.office import test_outlook_live as live
    from tests.office import test_outlook_live_more as more

    assert live._outlook_exe is proc.outlook_exe
    assert more._outlook_exe is proc.outlook_exe
